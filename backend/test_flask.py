import json
import time
import logging
import threading
import requests
import urllib3
import pandas as pd
import os
import glob
import re
import random
import smtplib
import urllib.request
import urllib.error
import urllib.parse
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import defaultdict
from io import BytesIO
from datetime import datetime, timedelta

from flask import Flask, jsonify, request, Response, stream_with_context, send_file, g
from flask_cors import CORS
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from auth import LDAPAuth, create_jwt, decode_jwt, jwt_required

# ======================================================
# Flask App
# ======================================================
app = Flask(__name__)
CORS(app, supports_credentials=True)

# ======================================================
# Disable SSL warnings
# ======================================================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ======================================================
# Logging
# ======================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# ======================================================
# Constants
# ======================================================
JITA_BASE = "https://jita.eng.nutanix.com/api/v2"
TRIAGE_GENIE_BASE = "http://triage-genie.eng.nutanix.com/api"
# Login URL for Triage Genie (session auth); override with TRIAGE_GENIE_LOGIN_URL if needed
LOGIN_URL = os.getenv("TRIAGE_GENIE_LOGIN_URL", "http://triage-genie.eng.nutanix.com/login")
PHX_BASE = "https://jita-phx1-webserver-2.eng.nutanix.com/api/v2"
TCMS_BASE = "https://tcms.eng.nutanix.com/api-readonly/v1"
TCMS_SUMMARY_BASE = "https://tcms.eng.nutanix.com/api/v1"
TCMS_WRITE_BASE = "https://tcms.eng.nutanix.com/api/v1"
TCMS_TESTDB_BASE = "https://quality-pipeline.eng.nutanix.com/testdb/api/v1"

# TCMS auth (base64-encoded defaults; override with env vars in production)
TCMS_USER = os.getenv("TCMS_USER", "agave_bot")
TCMS_PASSWORD = os.getenv("TCMS_PASSWORD", "admin")

TESTCASE_MGMT_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)

# Tag-to-team configuration for TCMS QI lookups.
# Each key is a tag pattern; value holds the TCMS team name and fallback branch.
# "default" is used when no specific tag match is found.
TEAM_CONFIG = {
    "cdp_master_full_reg": {"team": "CDP", "default_branch": "master"},
    "default":             {"team": "CDP", "default_branch": "master"},
}

# Maps full branch names (as shown in the Run Summary table) to the short
# milestone names expected by the TCMS API.  "master" stays as-is.
BRANCH_SHORT_NAME_MAP = {
    "master": "master",
    "ganges-7.6-stable": "7.6",
    "ganges-7.5-stable": "7.5",
    "ganges-7.5.1-stable": "7.5.1",
}

# AI Endpoint for failure summary
AI_BASE = "https://hkn12.ai.nutanix.com/enterpriseai/v1"
AI_API_KEY = "ddb2b793-1004-49a1-b005-4ddf4c2ade8c"

# SSL context for AI endpoint (skip TLS verify)
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

MAX_FAILED_TESTS = 50
MAX_WORKERS = 5

HEADERS = {
    "Authorization": "Bearer TOKEN",  # move to env later
    "Content-Type": "application/json",
    "Accept": "application/json"
}

# In-memory storage for manual tasks
# Structure: {tag: {branch: [task_ids]}}
manual_tasks_store = {}

# Run Plan storage file
RUN_PLAN_STORAGE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "run_plans.json")

def load_run_plans():
    """Load run plans from JSON file"""
    try:
        if os.path.exists(RUN_PLAN_STORAGE):
            with open(RUN_PLAN_STORAGE, 'r') as f:
                return json.load(f)
        return {"run_plans": [], "history": []}
    except Exception as e:
        logger.error(f"Error loading run plans: {e}")
        return {"run_plans": [], "history": []}

def save_run_plans(data):
    """Save run plans to JSON file"""
    try:
        with open(RUN_PLAN_STORAGE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving run plans: {e}")
        raise

# Triage Genie jobs storage file
TRIAGE_GENIE_STORAGE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "triage_genie_jobs.json")

def load_triage_genie_jobs():
    """Load Triage Genie jobs from JSON file"""
    try:
        if os.path.exists(TRIAGE_GENIE_STORAGE):
            with open(TRIAGE_GENIE_STORAGE, 'r') as f:
                return json.load(f)
        return {"jobs": []}
    except Exception as e:
        logger.error(f"Error loading triage genie jobs: {e}")
        return {"jobs": []}

def save_triage_genie_jobs(data):
    """Save Triage Genie jobs to JSON file"""
    try:
        with open(TRIAGE_GENIE_STORAGE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving triage genie jobs: {e}")
        raise

# Regression Dashboard Configuration storage file
REGRESSION_CONFIG_STORAGE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "regression_config.json")

def load_regression_config():
    """Load regression dashboard configuration from JSON file. Migrates legacy schema."""
    try:
        if os.path.exists(REGRESSION_CONFIG_STORAGE):
            with open(REGRESSION_CONFIG_STORAGE, 'r') as f:
                config = json.load(f)
            # Migration: add default_tag, added_tags if missing
            added = config.get("added_tags", [])
            if not isinstance(added, list):
                added = []
            if "default_tag" not in config:
                existing_tag = config.get("tag", "").strip()
                if existing_tag:
                    config["default_tag"] = existing_tag
                    if existing_tag not in added:
                        added = list(added) + [existing_tag]
                else:
                    config["default_tag"] = None
            config["added_tags"] = added
            return config
        return {
            "input_mode": "tag",
            "tag": "cdp_master_full_reg",
            "default_tag": "cdp_master_full_reg",
            "added_tags": ["cdp_master_full_reg"],
            "task_ids": []
        }
    except Exception as e:
        logger.error(f"Error loading regression config: {e}")
        return {
            "input_mode": "tag",
            "tag": "cdp_master_full_reg",
            "default_tag": "cdp_master_full_reg",
            "added_tags": ["cdp_master_full_reg"],
            "task_ids": []
        }

def save_regression_config(data):
    """Save regression dashboard configuration to JSON file"""
    try:
        with open(REGRESSION_CONFIG_STORAGE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving regression config: {e}")
        raise

# Triage Accuracy Analyzer data storage
TRIAGE_ACCURACY_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
TRIAGE_ACCURACY_TASKIDS_FILE = "triage_accuracy_data_taskids.json"

def _sanitize_tag_for_filename(tag):
    """Replace unsafe chars for filenames; collapse multiple underscores."""
    if not tag or not isinstance(tag, str):
        return "unknown"
    s = tag.strip()
    for c in r'|/:*?"<>\\':
        s = s.replace(c, "_")
    s = re.sub(r"_+", "_", s).strip("_")
    return s if s else "unknown"

def _triage_accuracy_path(tag=None):
    """Get path for triage accuracy JSON. tag=None means task_ids mode."""
    os.makedirs(TRIAGE_ACCURACY_DATA_DIR, exist_ok=True)
    if tag:
        sanitized = _sanitize_tag_for_filename(tag)
        return os.path.join(TRIAGE_ACCURACY_DATA_DIR, f"triage_accuracy_data_{sanitized}.json")
    return os.path.join(TRIAGE_ACCURACY_DATA_DIR, TRIAGE_ACCURACY_TASKIDS_FILE)

def load_triage_accuracy_data(tag=None):
    """Load triage accuracy data from JSON file. tag=None for task_ids mode."""
    try:
        path = _triage_accuracy_path(tag)
        if os.path.exists(path):
            with open(path, 'r') as f:
                return json.load(f)
        # Migration: copy legacy triage_accuracy_data.json to per-tag file if tag matches
        if tag:
            legacy_path = os.path.join(TRIAGE_ACCURACY_DATA_DIR, "triage_accuracy_data.json")
            if os.path.exists(legacy_path):
                with open(legacy_path, 'r') as f:
                    data = json.load(f)
                cached_tag = (data.get("tag") or "").strip()
                if cached_tag == tag:
                    save_triage_accuracy_data(data, tag)
                    return data
        return None
    except Exception as e:
        logger.error(f"Error loading triage accuracy data: {e}")
        return None

def save_triage_accuracy_data(data, tag=None):
    """Save triage accuracy data to JSON file. tag=None for task_ids mode."""
    try:
        path = _triage_accuracy_path(tag)
        os.makedirs(TRIAGE_ACCURACY_DATA_DIR, exist_ok=True)
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving triage accuracy data: {e}")
        raise

def invalidate_triage_accuracy_cache(tag=None):
    """Delete triage accuracy cache. tag=None invalidates only task_ids file; pass tag for per-tag file."""
    try:
        path = _triage_accuracy_path(tag)
        if os.path.exists(path):
            os.remove(path)
            logger.info(f"Invalidated triage accuracy cache: {path}")
    except Exception as e:
        logger.warning(f"Could not invalidate triage accuracy cache: {e}")

# Load regression owners mapping from CSV
CSV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "regression_owners.csv")
owner_mapping = {}

def load_owner_mapping():
    """Load test prefix to owner mapping from CSV"""
    global owner_mapping
    try:
        if os.path.exists(CSV_PATH):
            df = pd.read_csv(CSV_PATH, header=0)
            # CSV format: "Test Area,Regression Owner"
            for _, row in df.iterrows():
                test_prefix = str(row.iloc[0]).strip()
                owner = str(row.iloc[1]).strip() if len(row) > 1 else "Unknown"
                if test_prefix and owner and test_prefix != "Test Area":  # Skip header row
                    owner_mapping[test_prefix] = owner
            logger.info(f"Loaded {len(owner_mapping)} owner mappings from CSV")
        else:
            logger.warning(f"CSV file not found at {CSV_PATH}")
    except Exception as e:
        logger.error(f"Error loading owner mapping: {e}")

# Load owner mapping on startup
load_owner_mapping()

# ======================================================
# Session (reused)
# ======================================================
session = requests.Session()
session.headers.update(HEADERS)
session.verify = False

# ======================================================
# Helpers
# ======================================================
def should_process_task(status):
    """Process only non-successful runs"""
    return status not in ("Succeeded", "Pending")


def fetch_test_result(testcase_id):
    resp = session.get(
        f"{PHX_BASE}/agave_test_results/{testcase_id}",
        timeout=30
    )
    resp.raise_for_status()
    return resp.json().get("data", {})


def process_failed_tests(task_id, agave_task):
    failed_tests = []

    agave_results = agave_task.get("AgaveTestResults", [])
    if not agave_results:
        return failed_tests

    failed_ids = [
        tr["$oid"]
        for tr in agave_results
        if tr.get("status") in ("Failed", "Warning")
    ][:MAX_FAILED_TESTS]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(fetch_test_result, tid)
            for tid in failed_ids
        ]

        for future in as_completed(futures):
            try:
                failure = future.result()
                failed_tests.append({
                    "testcase_id": failure.get("_id", {}).get("$oid"),
                    "test_name": failure.get("test", {}).get("name"),
                    "status": failure.get("status"),
                    "jira_tickets": failure.get("jira_tickets", []),
                    "exception_summary": failure.get("exception_summary"),
                    "log_url": failure.get("test_log_url")
                })
            except Exception as e:
                logger.error(f"[ERROR] Failed testcase fetch: {e}")

    return failed_tests


# ======================================================
# API-1: Fetch Regression Tasks
# ======================================================
def fetch_regression_tasks(tag=None, task_ids=None):
    """
    Fetch regression tasks either by tag or by task IDs
    
    Args:
        tag: Tag name to filter tasks
        task_ids: List of task IDs to fetch
    
    Returns:
        List of task data
    """
    if task_ids:
        # Fetch tasks by task IDs
        raw_query = {
            "_id": {
                "$in": [{"$oid": tid} for tid in task_ids]
            }
        }
    elif tag:
        # Fetch tasks by tag (original behavior)
        raw_query = {
            "$or": [
                {"created_by": {
                    "$in": ["shilpa.sattigeri", "sudharshan.musali"]
                    }
                },
                {"user_groups": {"$in": ["cdp_reg_jarvis"]}}
            ],
            "tester_tags": {"$in": [tag]},
            "system_under_test.component": "main"
        }
    else:
        raise ValueError("Either tag or task_ids must be provided")

    params = {
        "limit": 2000,
        "start": 0,
        "sort": "-_id",
        "only": (
            "label,branch,status,created_by,test_result_count,"
            "created_at,end_time"
        ),
        "raw_query": json.dumps(raw_query)
    }

    try:
        resp = session.get(
            f"{JITA_BASE}/tasks",
            params=params,
            timeout=30
        )
        resp.raise_for_status()
        return resp.json().get("data", [])
    except requests.exceptions.ConnectionError as e:
        logger.error(f"Connection error fetching regression tasks: {e}")
        raise ConnectionError(f"Failed to connect to JITA API. Please check your network connection and ensure 'jita.eng.nutanix.com' is accessible.")
    except requests.exceptions.Timeout as e:
        logger.error(f"Timeout error fetching regression tasks: {e}")
        raise TimeoutError(f"Request to JITA API timed out. Please try again.")
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error fetching regression tasks: {e}")
        raise Exception(f"Error fetching regression tasks: {str(e)}")


# ======================================================
# API-2: Fetch Agave Task
# ======================================================
def fetch_agave_task(task_id):
    resp = session.get(
        f"{JITA_BASE}/agave_tasks/{task_id}",
        timeout=30
    )
    resp.raise_for_status()
    return resp.json().get("data", {})

# ======================================================
# Flask Endpoint
# ======================================================
def fetch_test_results_batch_with_pagination(task_ids, limit=2000, timeout=120):
    """
    Fetch test results for multiple tasks in batch.
    Always uses merge=True to get merged results across all tasks.
    Fetches all results in a single request with limit=2000.
    
    Args:
        task_ids: List of task IDs to fetch results for
        limit: Number of results to fetch (default: 2000)
        timeout: Request timeout in seconds (default: 120)
    
    Returns:
        List of merged test results
    
    Raises:
        requests.exceptions.Timeout: If request times out
        requests.exceptions.RequestException: For other request errors
    """
    if not task_ids:
        return []
    
    # Increase timeout for large task sets (more than 50 tasks)
    if len(task_ids) > 50:
        timeout = max(timeout, 180)  # At least 3 minutes for large sets
    
    logger.info(f"Fetching test results for {len(task_ids)} tasks (timeout: {timeout}s, limit: {limit})")
    
    # Correct payload structure for merged test results
    # Verified format: raw_query at top level with agave_task_id query, merge at top level
    payload = {
        "raw_query": {
            "agave_task_id": {
                "$in": [{"$oid": tid} for tid in task_ids]
            }
        },
        "only": (
            "_id,test,status,agave_task_id,jira_tickets,triaged_by,exception_summary,"
            "test_log_url,comments"
        ),
        "start": 0,
        "limit": limit,
        "sort": "agave_task_id,status",
        "merge": True  # Must be at top level to get merged results
    }
    
    # Log payload for verification
    #logger.info(f"[agave_test_results API] Payload: {json.dumps(payload, indent=2)}")
    #logger.info(f"[agave_test_results API] merge parameter: {payload.get('merge')}")
    logger.info(f"[agave_test_results API] Number of task_ids in query: {len(task_ids)}")
    
    try:
        resp = session.post(
            f"{JITA_BASE}/reports/agave_test_results",
            json=payload,
            timeout=timeout
        )
        
        resp.raise_for_status()
        response_data = resp.json()
        results = response_data.get("data", [])
        total = response_data.get("total", 0)
        
        # Log response info
        logger.info(f"[agave_test_results API] Response - Total: {total}, Returned: {len(results)}, Merge enabled: {payload.get('merge')}")
        if results:
            # Log sample test names to verify merge (first 3 unique test names)
            sample_tests = []
            seen_sample = set()
            for r in results:
                test_name = r.get("test", {}).get("name", "")
                if test_name and test_name not in seen_sample and len(sample_tests) < 3:
                    sample_tests.append(test_name)
                    seen_sample.add(test_name)
            logger.info(f"[agave_test_results API] Sample test names (first 3 unique): {sample_tests}")
        
        logger.info(f"Fetched {len(results)} merged test results from {len(task_ids)} tasks")
        return results
        
    except requests.exceptions.Timeout as e:
        logger.error(f"Timeout fetching test results: {e}")
        raise requests.exceptions.Timeout(
            f"Request timed out after {timeout}s while fetching test results. "
            f"This may be due to a large number of tasks ({len(task_ids)}). "
            f"Try reducing the number of tasks or increasing the timeout."
        ) from e
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching test results: {e}")
        raise

# ======================================================
# Auth Routes (no @jwt_required)
# ======================================================
@app.route("/mcp/regression/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    ldap_auth = LDAPAuth()
    user_info = ldap_auth.authenticate(username, password)
    if user_info is None:
        return jsonify({"error": "Invalid username or password"}), 401

    token = create_jwt(
        user_info["username"],
        user_info.get("displayName", ""),
        user_info.get("email", ""),
    )
    return jsonify({"token": token, "user": user_info})


@app.route("/mcp/regression/auth/me", methods=["GET"])
@jwt_required
def auth_me():
    return jsonify({"user": g.current_user})


@app.route("/mcp/regression/auth/logout", methods=["POST"])
def auth_logout():
    return jsonify({"success": True})


# ======================================================
# Protected Routes
# ======================================================
@app.route("/mcp/regression/home", methods=["GET"])
@jwt_required
def regression_home():
    start = time.time()

    tag = request.args.get("tag")
    task_ids_param = request.args.get("task_ids")  # Comma-separated task IDs
    
    # Parse task_ids if provided
    task_ids = None
    if task_ids_param:
        task_ids = [tid.strip() for tid in task_ids_param.split(",") if tid.strip()]
    
    if not tag and not task_ids:
        return jsonify({"error": "Either tag or task_ids is required"}), 400

    if tag:
        logger.info(f"[START] Regression Home | tag={tag}")
    else:
        logger.info(f"[START] Regression Home | task_ids={len(task_ids)} tasks")
        logger.info(f"[DEBUG] Requested task IDs: {task_ids[:5]}..." if len(task_ids) > 5 else f"[DEBUG] Requested task IDs: {task_ids}")

    # Store original requested task IDs for comparison
    requested_task_ids = task_ids.copy() if task_ids else None

    try:
        tasks = fetch_regression_tasks(tag=tag, task_ids=task_ids)
    except TimeoutError as e:
        logger.error(f"Regression home: JITA task list timeout: {e}")
        return jsonify({
            "error": str(e),
            "type": "jita_timeout",
            "tag": tag,
            "generated_at": datetime.utcnow().isoformat(),
            "total_runs": 0,
            "runs": [],
            "branch_start_dates": {},
            "oldest_start_date": None,
        }), 504
    except ConnectionError as e:
        logger.error(f"Regression home: JITA connection error: {e}")
        return jsonify({
            "error": str(e),
            "type": "jita_connection_error",
            "tag": tag,
            "generated_at": datetime.utcnow().isoformat(),
            "total_runs": 0,
            "runs": [],
            "branch_start_dates": {},
            "oldest_start_date": None,
        }), 503
    
    # Log which task IDs were found vs requested
    if requested_task_ids and not tag:
        found_task_ids = [task["_id"]["$oid"] for task in tasks]
        missing_task_ids = set(requested_task_ids) - set(found_task_ids)
        logger.info(f"[DEBUG] Found {len(found_task_ids)}/{len(requested_task_ids)} tasks")
        if missing_task_ids:
            logger.warning(f"[DEBUG] Missing task IDs ({len(missing_task_ids)}): {list(missing_task_ids)[:5]}..." if len(missing_task_ids) > 5 else f"[DEBUG] Missing task IDs: {list(missing_task_ids)}")
        
        # Log branch distribution from raw JITA data
        if tasks:
            branch_distribution = {}
            for task in tasks:
                raw_branch = task.get("branch", "None")
                branch_distribution[raw_branch] = branch_distribution.get(raw_branch, 0) + 1
            logger.info(f"[DEBUG] Raw branch distribution from JITA: {branch_distribution}")
    
    # Collect all task IDs from found tasks
    task_ids = [task["_id"]["$oid"] for task in tasks]
    
    # Fetch test results using agave_test_results API for accurate counts
    logger.info(f"Fetching test results for {len(task_ids)} tasks using agave_test_results API")
    test_results = []
    if task_ids:
        try:
            test_results = fetch_test_results_batch_with_pagination(task_ids)
            logger.info(f"Fetched {len(test_results)} test results")
        except Exception as e:
            logger.warning(f"Failed to fetch test results from agave_test_results API: {e}. Falling back to test_result_count.")
            test_results = []
    
    # Group test results by task_id
    test_results_by_task = defaultdict(list)
    for test_result in test_results:
        agave_task_id = test_result.get("agave_task_id")
        if agave_task_id:
            # Handle both string and $oid format
            if isinstance(agave_task_id, dict) and "$oid" in agave_task_id:
                task_id = agave_task_id["$oid"]
            else:
                task_id = str(agave_task_id)
            test_results_by_task[task_id].append(test_result)
    
    runs = []
    
    # Track created_at times for finding oldest start date
    created_at_times = []

    for task in tasks:
        task_id = task["_id"]["$oid"]
        status = task.get("status")
        
        # Collect created_at time for oldest date calculation
        created_at = task.get("created_at")
        if created_at:
            created_at_times.append(created_at)
        
        # Count test statuses from agave_test_results if available
        test_counts = {
            "total": 0,
            "Succeeded": 0,
            "Failed": 0,
            "Pending": 0,
            "Warning": 0,
            "Running": 0,
            "Skipped": 0,
            "Killed": 0
        }
        
        if task_id in test_results_by_task:
            # Count statuses from actual test results
            for test_result in test_results_by_task[task_id]:
                test_status = test_result.get("status", "")
                test_counts["total"] += 1
                
                # Normalize status names (handle case-insensitive matching)
                status_lower = test_status.lower() if test_status else ""
                
                if status_lower == "succeeded" or status_lower == "success":
                    test_counts["Succeeded"] += 1
                elif status_lower == "failed" or status_lower == "failure":
                    test_counts["Failed"] += 1
                elif status_lower == "pending" or status_lower == "waiting":
                    test_counts["Pending"] += 1
                elif status_lower == "warning" or status_lower == "warn":
                    test_counts["Warning"] += 1
                elif status_lower == "running" or status_lower == "executing" or status_lower == "in_progress":
                    test_counts["Running"] += 1
                elif status_lower == "skipped" or status_lower == "skip":
                    test_counts["Skipped"] += 1
                elif status_lower == "killed" or status_lower == "terminated" or status_lower == "cancelled":
                    test_counts["Killed"] += 1
                else:
                    # For unknown statuses, try to infer from common patterns
                    # But don't default to pending - log it for debugging
                    logger.debug(f"Unknown test status: {test_status} for task {task_id}")
                    # Map unknown statuses based on common patterns
                    if any(x in status_lower for x in ["pending", "waiting", "queued"]):
                        test_counts["Pending"] += 1
                    elif any(x in status_lower for x in ["running", "executing", "in_progress", "active"]):
                        test_counts["Running"] += 1
                    elif any(x in status_lower for x in ["skipped", "skip"]):
                        test_counts["Skipped"] += 1
                    elif any(x in status_lower for x in ["killed", "terminated", "cancelled", "aborted"]):
                        test_counts["Killed"] += 1
                    else:
                        # Default to pending only if truly unknown
                        test_counts["Pending"] += 1
        else:
            # Fallback to test_result_count if agave_test_results not available
            tc = task.get("test_result_count", {})
            test_counts = {
                "total": tc.get("Total", 0),
                "Succeeded": tc.get("Succeeded", 0),
                "Failed": tc.get("Failed", 0),
                "Pending": tc.get("Pending", 0),
                "Warning": tc.get("Warning", 0),
                "Running": tc.get("Running", 0),
                "Skipped": tc.get("Skipped", 0),
                "Killed": tc.get("Killed", 0)
            }

        # Get branch and normalize it
        original_branch = task.get("branch")
        label = task.get("label", "")
        branch = None
        
        if original_branch:
            # Branch exists, normalize it
            branch = original_branch.strip()
            # Normalize master branch variations (case-insensitive)
            branch_lower = branch.lower()
            if branch_lower in ["master", "main"]:
                branch = "master"
            elif branch_lower in ["ganges-7.5-stable", "ganges_7.5_stable"]:
                branch = "ganges-7.5-stable"
        else:
            # Branch is missing (None, empty string, etc.)
            # Try to infer from label
            label_lower = label.lower()
            
            # Check for master branch indicators in label
            master_keywords = ["master", "main", "cdp_master", "master_full", "master_reg"]
            if any(keyword in label_lower for keyword in master_keywords):
                branch = "master"
                logger.info(f"[BRANCH_INFER] Task {task_id}: No branch field, inferred 'master' from label: '{label}'")
            # Check for other known branch patterns
            elif "ganges" in label_lower or "7.5" in label_lower:
                branch = "ganges-7.5-stable"
                logger.info(f"[BRANCH_INFER] Task {task_id}: No branch field, inferred 'ganges-7.5-stable' from label: '{label}'")
            else:
                branch = "unknown"
                logger.warning(f"[BRANCH_MISSING] Task {task_id}: No branch field and cannot infer from label: '{label}'")
        
        # Log master branch detection for debugging
        if branch == "master":
            logger.info(f"[MASTER_BRANCH] Task {task_id}: branch='{original_branch}' -> normalized to 'master', label='{label}'")
        
        # Get created_at time for this task
        created_at = task.get("created_at")
        
        run = {
            "task_id": task_id,
            "label": label,
            "branch": branch,
            "status": status,
            "created_by": task.get("created_by"),
            "created_at": created_at,  # Include created_at in run object
            "test_counts": test_counts,
            "failed_tests": []
        }

        if not should_process_task(status):
            agave_task = fetch_agave_task(task_id)
            run["failed_tests"] = process_failed_tests(task_id, agave_task)

        runs.append(run)

    # Calculate oldest start date per branch from all runs
    branch_start_dates = {}  # {branch: oldest_datetime}
    
    for run in runs:
        branch = run.get("branch")
        created_at = run.get("created_at")
        
        if branch and created_at:
            try:
                # Parse created_at time
                if isinstance(created_at, str):
                    # Handle ISO format
                    if 'T' in created_at:
                        dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    else:
                        # Try other common formats
                        try:
                            dt = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
                        except ValueError:
                            dt = datetime.strptime(created_at, "%Y-%m-%d")
                elif isinstance(created_at, dict) and "$date" in created_at:
                    # MongoDB date format
                    dt = datetime.fromtimestamp(created_at["$date"] / 1000)
                else:
                    continue
                
                # Track oldest date per branch
                if branch not in branch_start_dates or dt < branch_start_dates[branch]:
                    branch_start_dates[branch] = dt
            except (ValueError, TypeError) as e:
                logger.debug(f"Could not parse created_at for branch {branch}: {created_at}, error: {e}")
                continue
    
    # Format branch start dates as readable strings
    branch_start_dates_formatted = {}
    for branch, dt in branch_start_dates.items():
        branch_start_dates_formatted[branch] = dt.strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"[DEBUG] Oldest start date for branch '{branch}': {branch_start_dates_formatted[branch]}")
    
    # Calculate overall oldest start date
    oldest_start_date = None
    if branch_start_dates:
        oldest_start_date = min(branch_start_dates.values())
        oldest_start_date_str = oldest_start_date.strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"[DEBUG] Overall oldest start date: {oldest_start_date_str}")

    # Log final branch distribution after normalization
    if runs:
        final_branch_dist = {}
        for run in runs:
            br = run.get("branch", "None")
            final_branch_dist[br] = final_branch_dist.get(br, 0) + 1
        logger.info(f"[DEBUG] Final branch distribution after normalization: {final_branch_dist}")
        if "master" in final_branch_dist:
            logger.info(f"[DEBUG] Master branch tasks found: {final_branch_dist['master']} tasks")
        else:
            logger.warning(f"[DEBUG] No 'master' branch found in final distribution! Available branches: {list(final_branch_dist.keys())}")

    logger.info(
        f"[END] runs={len(runs)} | time={time.time() - start:.2f}s"
    )
    
    # Include metadata about missing task IDs if using task_ids mode
    response_data = {
        "tag": tag,
        "generated_at": datetime.utcnow().isoformat(),
        "total_runs": len(runs),
        "runs": runs,
        "branch_start_dates": branch_start_dates_formatted,  # Oldest start date per branch
        "oldest_start_date": oldest_start_date.strftime("%Y-%m-%d %H:%M:%S") if oldest_start_date else None
    }
    
    if requested_task_ids and not tag:
        found_task_ids = [task["_id"]["$oid"] for task in tasks]
        missing_task_ids = list(set(requested_task_ids) - set(found_task_ids))
        if missing_task_ids:
            response_data["missing_task_ids"] = missing_task_ids
            response_data["requested_count"] = len(requested_task_ids)
            response_data["found_count"] = len(found_task_ids)
            logger.warning(f"Some task IDs were not found: {len(missing_task_ids)} missing out of {len(requested_task_ids)} requested")

    return jsonify(response_data)


# ---------------------------------------------------
# Manual Tasks Endpoints
# ---------------------------------------------------
@app.route("/mcp/regression/manual-tasks", methods=["GET"])
@jwt_required
def get_manual_tasks():
    tag = request.args.get("tag")
    branch = request.args.get("branch")

    if not tag:
        return jsonify({"error": "tag is required"}), 400

    if not branch:
        return jsonify({"error": "branch is required"}), 400

    # Get manual tasks for the given tag and branch
    tag_store = manual_tasks_store.get(tag, {})
    task_ids = tag_store.get(branch, [])

    return jsonify({
        "tag": tag,
        "branch": branch,
        "manual_tasks": task_ids
    })


@app.route("/mcp/regression/manual-tasks", methods=["POST"])
@jwt_required
def add_manual_tasks():
    data = request.get_json()
    tag = data.get("tag")
    branch = data.get("branch")
    task_ids = data.get("task_ids", [])

    if not tag:
        return jsonify({"error": "tag is required"}), 400

    if not branch:
        return jsonify({"error": "branch is required"}), 400

    if not task_ids:
        return jsonify({"error": "task_ids is required"}), 400

    # Initialize storage if needed
    if tag not in manual_tasks_store:
        manual_tasks_store[tag] = {}

    if branch not in manual_tasks_store[tag]:
        manual_tasks_store[tag][branch] = []

    # Add new task IDs (avoid duplicates)
    existing = set(manual_tasks_store[tag][branch])
    for task_id in task_ids:
        if task_id not in existing:
            manual_tasks_store[tag][branch].append(task_id)

    return jsonify({
        "tag": tag,
        "branch": branch,
        "manual_tasks": manual_tasks_store[tag][branch]
    })


@app.route("/mcp/regression/manual-tasks", methods=["DELETE"])
@jwt_required
def remove_manual_task():
    tag = request.args.get("tag")
    branch = request.args.get("branch")
    task_id = request.args.get("task_id")

    if not tag:
        return jsonify({"error": "tag is required"}), 400

    if not branch:
        return jsonify({"error": "branch is required"}), 400

    if not task_id:
        return jsonify({"error": "task_id is required"}), 400

    # Remove task ID if it exists
    if tag in manual_tasks_store and branch in manual_tasks_store[tag]:
        if task_id in manual_tasks_store[tag][branch]:
            manual_tasks_store[tag][branch].remove(task_id)

    return jsonify({
        "tag": tag,
        "branch": branch,
        "manual_tasks": manual_tasks_store.get(tag, {}).get(branch, [])
    })


# ---------------------------------------------------
# Configuration Endpoints
# ---------------------------------------------------
@app.route("/mcp/regression/config", methods=["GET"])
@jwt_required
def get_regression_config():
    """Get regression dashboard configuration from JSON file"""
    try:
        config = load_regression_config()
        return jsonify(config)
    except Exception as e:
        logger.error(f"Error getting regression config: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/mcp/regression/config", methods=["POST"])
@jwt_required
def save_regression_config_endpoint():
    """Save regression dashboard configuration to JSON file"""
    try:
        data = request.get_json()
        
        # Validate required fields
        input_mode = data.get("input_mode")
        if input_mode not in ["tag", "task_ids"]:
            return jsonify({"error": "input_mode must be 'tag' or 'task_ids'"}), 400
        
        default_tag = data.get("default_tag")
        added_tags = data.get("added_tags")
        if added_tags is None:
            added_tags = []
        if not isinstance(added_tags, list):
            added_tags = []
        
        config = {
            "input_mode": input_mode,
            "default_tag": default_tag if default_tag else None,
            "added_tags": [str(t).strip() for t in added_tags if t and str(t).strip()],
            "tag": (default_tag or "").strip() if input_mode == "tag" else "",
            "task_ids": data.get("task_ids", []) if input_mode == "task_ids" else []
        }
        
        # Validate based on input mode
        if input_mode == "tag":
            if config["default_tag"] and config["default_tag"] not in config["added_tags"]:
                return jsonify({"error": "default_tag must be in added_tags or null"}), 400
            config["task_ids"] = []
        elif input_mode == "task_ids":
            if not config["task_ids"] or len(config["task_ids"]) == 0:
                return jsonify({"error": "task_ids is required when input_mode is 'task_ids'"}), 400
            config["tag"] = ""
            config["default_tag"] = None
        
        save_regression_config(config)
        if input_mode == "task_ids":
            invalidate_triage_accuracy_cache(None)
        logger.info(f"Saved regression config: input_mode={input_mode}, default_tag={config.get('default_tag')}, added_tags={len(config.get('added_tags', []))}")
        return jsonify(config)
    except Exception as e:
        logger.error(f"Error saving regression config: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/mcp/regression/config/tags", methods=["POST"])
@jwt_required
def add_config_tag():
    """Add a tag to added_tags. Validation is lenient - tag is added even if JITA returns empty."""
    try:
        data = request.get_json() or {}
        tag = (data.get("tag") or "").strip()
        if not tag:
            return jsonify({"error": "tag is required"}), 400
        
        config = load_regression_config()
        added = list(config.get("added_tags", []))
        if tag in added:
            return jsonify({"added_tags": added, "message": "Tag already in list"}), 200
        
        # Optional validation: if JITA returns tasks, tag is validated; otherwise still add (lenient)
        validated = False
        try:
            tasks = fetch_regression_tasks(tag=tag)
            validated = bool(tasks)
        except Exception as e:
            logger.warning(f"Tag validation skipped for '{tag}': {e}")
        
        added.append(tag)
        config["added_tags"] = added
        save_regression_config(config)
        logger.info(f"Added tag to config: {tag} (validated={validated})")
        return jsonify({"added_tags": added, "tag": tag, "validated": validated})
    except Exception as e:
        logger.error(f"Error adding tag: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/mcp/regression/config/tags", methods=["DELETE"])
@jwt_required
def delete_config_tag():
    """Remove tag from added_tags and delete per-tag triage accuracy JSON."""
    try:
        tag = request.args.get("tag", "").strip()
        if not tag:
            return jsonify({"error": "tag query param is required"}), 400
        
        config = load_regression_config()
        added = list(config.get("added_tags", []))
        if tag not in added:
            return jsonify({"error": f"Tag '{tag}' not in added_tags"}), 404
        
        added = [t for t in added if t != tag]
        config["added_tags"] = added
        if config.get("default_tag") == tag:
            config["default_tag"] = None
            config["tag"] = ""
        save_regression_config(config)
        
        invalidate_triage_accuracy_cache(tag)
        logger.info(f"Deleted tag from config and triage JSON: {tag}")
        return jsonify({"added_tags": added})
    except Exception as e:
        logger.error(f"Error deleting tag: {e}")
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------
# Fetch Branches from Tag Endpoint
# ---------------------------------------------------
@app.route("/mcp/regression/branches", methods=["GET"])
@jwt_required
def get_branches_from_tag():
    tag = request.args.get("tag")
    
    if not tag:
        return jsonify({"error": "tag is required"}), 400
    
    try:
        # Fetch tasks using the same API as regression_home
        tasks = fetch_regression_tasks(tag)
        
        # Extract unique branches
        branches = set()
        for task in tasks:
            branch = task.get("branch")
            if branch:
                branches.add(branch)
        
        return jsonify({
            "tag": tag,
            "branches": sorted(list(branches))
        })
    except Exception as e:
        logger.error(f"Error fetching branches: {e}")
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------
# Helper: Resolve Owner from Test Name
# ---------------------------------------------------
def resolve_owner(test_name):
    """Resolve owner from test name using prefix mapping"""
    for prefix, owner in owner_mapping.items():
        if test_name.startswith(prefix):
            return owner
    return "Unknown"


# ---------------------------------------------------
# Helper: Fetch Test Results (POST API – batch)
# NOTE: This function is deprecated. Use fetch_test_results_batch_with_pagination() instead.
# Kept for backward compatibility but redirects to pagination version.
# ---------------------------------------------------
def fetch_test_results_batch(task_ids):
    """Fetch test results for multiple tasks in batch (deprecated - uses pagination version)"""
    return fetch_test_results_batch_with_pagination(task_ids)


# ---------------------------------------------------
# Helper function to calculate QI impact for bulk issues
# ---------------------------------------------------
def calculate_bulk_issues_qi_impact(bulk_issues, test_data, tag=None):
    """
    Calculate QI impact for bulk issues using TCMS API.
    
    Args:
        bulk_issues: Dictionary mapping ticket -> list of testcase names
        test_data: List of test result data from API
        tag: Optional tag to extract milestone from
    
    Returns:
        Dictionary with:
        - bulk_issues_with_qi: Dict mapping ticket -> QI impact data
        - test_qi_map: Dict mapping testcase_name -> QI value
    """
    if not bulk_issues:
        return {"bulk_issues_with_qi": {}, "test_qi_map": {}}
    
    # Extract milestone from tag or use default
    milestone = "7.5.1"  # Default milestone
    if tag:
        # Try to extract milestone from tag (e.g., "cdp_master_full_reg" -> "master", "7.5.1" -> "7.5.1")
        milestone_match = re.search(r'(\d+\.\d+(?:\.\d+)?)', tag)
        if milestone_match:
            milestone = milestone_match.group(1)
        elif "master" in tag.lower():
            milestone = "master"
    
    # Collect all unique testcases from bulk issues for TCMS API calls
    all_bulk_testcases = set()
    for test_names in bulk_issues.values():
        all_bulk_testcases.update(test_names)
    
    # Fetch QI values from TCMS API for all testcases in bulk issues
    logger.info(f"Fetching QI from TCMS API for {len(all_bulk_testcases)} testcases in bulk issues (milestone: {milestone})")
    test_qi_map = {}
    
    # Use ThreadPoolExecutor to fetch QI values in parallel
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_testcase = {
            executor.submit(fetch_qi_from_tcms, testcase_name, milestone): testcase_name
            for testcase_name in all_bulk_testcases
        }
        
        for future in as_completed(future_to_testcase):
            testcase_name = future_to_testcase[future]
            try:
                qi_value = future.result()
                if qi_value is not None:
                    test_qi_map[testcase_name] = qi_value
                else:
                    # Fallback: use status-based QI if TCMS API doesn't return data
                    # Find the test in test_data to get status
                    status_qi = 0
                    for test in test_data:
                        if test.get("test", {}).get("name") == testcase_name:
                            status = test.get("status", "")
                            if status == "Succeeded":
                                status_qi = 100
                            elif status == "Warning":
                                status_qi = 50
                            else:
                                status_qi = 0
                            break
                    test_qi_map[testcase_name] = status_qi
            except Exception as e:
                logger.warning(f"Error fetching QI for {testcase_name}: {e}")
                # Fallback to 0 if error
                test_qi_map[testcase_name] = 0
    
    logger.info(f"Fetched QI values for {len(test_qi_map)} testcases from TCMS API")
    
    # Calculate QI impact for each bulk issue using generate_qi_impact logic
    bulk_issues_with_qi = {}
    # Use total unique test cases from all test data (not just failed)
    all_unique_tests = set()
    for test in test_data:
        test_name = test.get("test", {}).get("name", "")
        if test_name:
            all_unique_tests.add(test_name)
    total_test_cases = len(all_unique_tests) if all_unique_tests else 1  # Avoid division by zero
    
    for ticket, test_names in bulk_issues.items():
        # Get QI values for all testcases affected by this ticket
        qi_values = []
        testcase_qi_details = []  # Store individual testcase QI details
        for test_name in test_names:
            qi_value = test_qi_map.get(test_name, 0)
            qi_values.append(qi_value)
            testcase_qi_details.append({
                "testcase": test_name,
                "qi": qi_value
            })
        
        if qi_values:
            # Calculate average QI (matching generate_qi_impact logic)
            average_qi = sum(qi_values) / len(qi_values)
            nr_test_cases = len(test_names)
            
            # Calculate QI impact: (average_qi - 100) * nr_test_cases
            qi_impact = (average_qi - 100) * nr_test_cases
            
            # Calculate overall QI impact: 100 * (qi_impact / (100 * total_test_cases))
            if total_test_cases > 0:
                overall_qi_impact = 100 * (qi_impact / (100 * total_test_cases))
            else:
                overall_qi_impact = 0
            
            bulk_issues_with_qi[ticket] = {
                "testcases": test_names,
                "testcase_count": nr_test_cases,
                "average_qi": round(average_qi, 2),
                "qi_impact": round(qi_impact, 2),
                "overall_qi_impact": round(overall_qi_impact, 2),
                "testcase_qi_details": testcase_qi_details  # Include individual testcase QI details
            }
        else:
            # Fallback if no QI data
            bulk_issues_with_qi[ticket] = {
                "testcases": test_names,
                "testcase_count": len(test_names),
                "average_qi": 0,
                "qi_impact": 0,
                "overall_qi_impact": 0,
                "testcase_qi_details": []
            }
    
    return {
        "bulk_issues_with_qi": bulk_issues_with_qi,
        "test_qi_map": test_qi_map
    }


# ---------------------------------------------------
# Helper function to fetch QI from TCMS API
# ---------------------------------------------------
def fetch_qi_from_tcms(testcase_name, milestone="7.5.1"):
    """
    Fetch QI (operation_success_percentage) from TCMS API for a given testcase.
    
    Args:
        testcase_name: Name of the testcase (e.g., "cdp.counter.fio.test_fio_counters.CountersFIOTest.test_fio_end_to_end")
        milestone: Target milestone (default: "7.5.1")
    
    Returns:
        float: QI value (operation_success_percentage) or None if not found/error
    """
    try:
        # Construct payload based on the provided example
        # Use more flexible matching - try exact name match first, then regex
        payload = [{
            "$match": {
                "$and": [
                    {"target_milestone": milestone},
                    {"last_result": {"$elemMatch": {"pass_name": "overall"}}},
                    {"deleted": False},
                    {"test_case.metadata.tags": {"$nin": ["SYSTEST_LONGEVITY", "LIMITED_RUNS"]}},
                    {
                        "$or": [
                            {"test_case.name": testcase_name},  # Exact match first
                            {"test_case.name": {"$regex": testcase_name, "$options": "i"}}  # Case-insensitive regex
                        ]
                    },
                    {"test_case.deprecated": False}
                ]
            }
        }, {"$sort": {"name": 1}}, {"$skip": 0}, {"$limit": 50}]
        
        # Make POST request to TCMS API
        response = requests.post(
            f"{TCMS_BASE}/milestone_all_test_cases/aggregate",
            json=payload,
            headers={"Content-Type": "application/json"},
            verify=False,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get("success") and data.get("data") and len(data["data"]) > 0:
                # Extract operation_success_percentage from published section
                testcase_data = data["data"][0]
                last_result = testcase_data.get("last_result", [])
                if last_result and len(last_result) > 0:
                    published = last_result[0].get("published", {})
                    if published:
                        operation_success_percentage = published.get("operation_success_percentage")
                        if operation_success_percentage is not None:
                            return float(operation_success_percentage)
            
            logger.warning(f"TCMS API: No QI data found for testcase: {testcase_name}")
            return None
        else:
            logger.warning(f"TCMS API error for {testcase_name}: HTTP {response.status_code}")
            return None
            
    except requests.exceptions.Timeout:
        logger.warning(f"TCMS API timeout for testcase: {testcase_name}")
        return None
    except Exception as e:
        logger.warning(f"Error fetching QI from TCMS for {testcase_name}: {e}")
        return None


# ---------------------------------------------------
# TCMS Tags Fetch Endpoint
# ---------------------------------------------------
def _tcms_aggregate_headers():
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    token = (
        (os.getenv("TCMS_API_TOKEN") or os.getenv("TCMS_TOKEN") or os.getenv("TCMS_BEARER") or "")
        .strip()
    )
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _tcms_aggregate_post(payload, timeout=60):
    return requests.post(
        f"{TCMS_BASE}/milestone_all_test_cases/aggregate",
        json=payload,
        headers=_tcms_aggregate_headers(),
        verify=False,
        timeout=timeout,
    )


def _tcms_response_rows(data):
    """Normalize aggregate JSON body to a list of row dicts."""
    if not data or not isinstance(data, dict):
        return None
    rows = data.get("data")
    if rows is None:
        rows = data.get("result") or data.get("rows") or data.get("items")
    if not isinstance(rows, list):
        return None
    return rows


def _parse_grouped_tag_ids(rows):
    tags = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        tid = item.get("_id")
        if tid is None or isinstance(tid, (dict, list)):
            continue
        s = str(tid).strip()
        if s:
            tags.append(s)
    return tags


def _tags_from_projected_docs(rows, *paths):
    seen = set()
    out = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        for p in paths:
            t = item
            for part in p.split("."):
                if not isinstance(t, dict):
                    t = None
                    break
                t = t.get(part)
            if isinstance(t, list):
                for x in t:
                    if x and str(x).strip():
                        s = str(x).strip()
                        if s and s not in seen:
                            seen.add(s)
                            out.append(s)
            elif t and str(t).strip():
                s = str(t).strip()
                if s and s not in seen:
                    seen.add(s)
                    out.append(s)
    return sorted(out, key=str.lower)


def _fetch_tcms_distinct_tags_group(milestone):
    """$group distinct tag values (preferred, smallest payload)."""
    payload = [
        {
            "$match": {
                "$and": [
                    {"target_milestone": milestone},
                    {"deleted": False},
                    {"test_case.deprecated": False},
                    {"test_case.metadata.tags": {"$exists": True, "$ne": []}},
                ]
            }
        },
        {"$unwind": "$test_case.metadata.tags"},
        {"$group": {"_id": "$test_case.metadata.tags"}},
        {"$sort": {"_id": 1}},
        {"$limit": 2000},
    ]
    r = _tcms_aggregate_post(payload)
    if r.status_code != 200:
        return None, r
    j = r.json()
    rows = _tcms_response_rows(j)
    if not rows and j and j.get("data") is None and isinstance(j, dict) and "success" in j:
        logger.warning(f"TCMS tags group: unexpected body keys: {list(j.keys())} snippet={str(j)[:400]}")
    if not rows:
        return [], r
    return _parse_grouped_tag_ids(rows), r


def _fetch_tcms_tags_project_scan(milestone, limit=5000):
    """
    Fallback: return many documents and build distinct tags in Python.
    Handles different shapes / empty $group responses.
    """
    payload = [
        {
            "$match": {
                "target_milestone": milestone,
                "deleted": False,
            }
        },
        {
            "$project": {
                "m_tags": "$test_case.metadata.tags",
                "alt_tags": "$test_case.tags",
            }
        },
        {"$limit": int(limit)},
    ]
    r = _tcms_aggregate_post(payload, timeout=90)
    if r.status_code != 200:
        return None, r
    j = r.json()
    rows = _tcms_response_rows(j)
    if not rows:
        return [], r
    tags = _tags_from_projected_docs(rows, "m_tags", "alt_tags")
    return tags, r


@app.route("/mcp/regression/tcms/tags", methods=["GET"])
def fetch_tcms_tags():
    """
    Fetch available tags from TCMS API for a given milestone.
    Query params: milestone (default: env TCMS_MILESTONE or 7.5.1)
    On empty result, tries TCMS_MILESTONE_FALLBACKS (comma env) and project-scan fallback.
    Set TCMS_API_TOKEN if your TCMS read requires bearer auth.
    """
    try:
        default_ms = (os.getenv("TCMS_MILESTONE") or "7.5.1").strip()
        milestone = (request.args.get("milestone") or default_ms).strip()
        raw_fb = (os.getenv("TCMS_MILESTONE_FALLBACKS") or "7.5,7.5.1,7.3,7.3.1").strip()
        fallbacks = [m.strip() for m in raw_fb.split(",") if m.strip()]
        try_order = [milestone] + [m for m in fallbacks if m != milestone]

        last_resp = None
        used_ms = milestone
        for ms in try_order:
            used_ms = ms
            tags, last_resp = _fetch_tcms_distinct_tags_group(ms)
            if tags is None and last_resp is not None:
                try:
                    logger.error(
                        f"TCMS tags group failed milestone={ms} status={last_resp.status_code} "
                        f"body={last_resp.text[:500]}"
                    )
                except Exception:
                    pass
                continue
            if tags:
                logger.info(f"TCMS tags (group) milestone={ms} count={len(tags)}")
                return jsonify({"success": True, "tags": tags, "milestone": ms, "source": "group"})

        # project-scan for first milestone in try_order
        for ms in try_order:
            tags, last_resp = _fetch_tcms_tags_project_scan(ms)
            if tags is None and last_resp is not None and last_resp.status_code != 200:
                try:
                    logger.error(
                        f"TCMS tags scan failed milestone={ms} status={last_resp.status_code} "
                        f"body={last_resp.text[:500]}"
                    )
                except Exception:
                    pass
                continue
            if tags:
                logger.info(f"TCMS tags (scan) milestone={ms} count={len(tags)}")
                return jsonify(
                    {
                        "success": True,
                        "tags": tags,
                        "milestone": ms,
                        "source": "scan",
                    }
                )

        if last_resp is not None and last_resp.status_code != 200:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": f"TCMS API HTTP {last_resp.status_code}",
                        "tags": [],
                    }
                ),
                502,
            )

        logger.warning(f"TCMS API returned no tags (tried: {', '.join(try_order[:6])})")
        return jsonify(
            {
                "success": True,
                "tags": [],
                "milestone": used_ms,
                "source": "empty",
                "hint": "Set TCMS_MILESTONE and TCMS_API_TOKEN; check network to tcms.eng.nutanix.com",
            }
        )

    except requests.exceptions.Timeout:
        logger.error("TCMS API timeout (tags)")
        return jsonify({"error": "TCMS API timeout", "success": False, "tags": []}), 504
    except Exception as e:
        logger.error(f"Error fetching TCMS tags: {e}", exc_info=True)
        return jsonify({"error": str(e), "success": False, "tags": []}), 500


@app.route("/mcp/regression/tcms/testcases", methods=["POST"])
def fetch_tcms_testcases_by_tags():
    """
    Fetch testcases from TCMS API filtered by tags.
    Body: {"tags": ["tag1", "tag2"], "milestone": "7.5.1"}
    Returns: list of testcase names
    """
    try:
        req_data = request.json or {}
        tags = req_data.get("tags", [])
        milestone = req_data.get("milestone", "7.5.1")
        
        if not tags or not isinstance(tags, list):
            return jsonify({"error": "tags array is required"}), 400
        
        # Build match query for tags (test must have ALL specified tags)
        payload = [{
            "$match": {
                "$and": [
                    {"target_milestone": milestone},
                    {"deleted": False},
                    {"test_case.deprecated": False},
                    {"test_case.metadata.tags": {"$all": tags}}
                ]
            }
        }, {
            "$project": {
                "name": "$test_case.name",
                "tags": "$test_case.metadata.tags",
                "description": "$test_case.description"
            }
        }, {
            "$sort": {"name": 1}
        }, {
            "$limit": 500
        }]
        
        response = requests.post(
            f"{TCMS_BASE}/milestone_all_test_cases/aggregate",
            json=payload,
            headers={"Content-Type": "application/json"},
            verify=False,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get("success") and data.get("data"):
                testcases = data["data"]
                logger.info(f"Fetched {len(testcases)} TCMS testcases for tags {tags}")
                return jsonify({
                    "success": True,
                    "testcases": testcases,
                    "count": len(testcases),
                    "milestone": milestone,
                    "tags": tags
                })
            else:
                return jsonify({
                    "success": True,
                    "testcases": [],
                    "count": 0,
                    "milestone": milestone,
                    "tags": tags
                })
        else:
            logger.error(f"TCMS API error: HTTP {response.status_code}")
            return jsonify({"error": f"TCMS API error: {response.status_code}"}), 502
            
    except requests.exceptions.Timeout:
        logger.error("TCMS API timeout")
        return jsonify({"error": "TCMS API timeout"}), 504
    except Exception as e:
        logger.error(f"Error fetching TCMS testcases: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------
# Triage Count Endpoint
# ---------------------------------------------------
@app.route("/mcp/regression/triage-count", methods=["GET"])
@jwt_required
def get_triage_count():
    start = time.time()
    tag = request.args.get("tag")
    task_ids_param = request.args.get("task_ids")  # Comma-separated task IDs
    
    # Parse task_ids if provided
    task_ids = None
    if task_ids_param:
        task_ids = [tid.strip() for tid in task_ids_param.split(",") if tid.strip()]
    
    if not tag and not task_ids:
        return jsonify({"error": "Either tag or task_ids is required"}), 400
    
    if tag:
        logger.info(f"[START] Triage Count | tag={tag}")
    else:
        logger.info(f"[START] Triage Count | task_ids={len(task_ids)} tasks")
    
    try:
        # Reload owner mapping in case it was updated
        load_owner_mapping()

        try:
            tasks = fetch_regression_tasks(tag=tag, task_ids=task_ids)
        except TimeoutError as e:
            logger.error(f"Triage count: JITA task list timeout: {e}")
            return jsonify({
                "error": str(e),
                "type": "jita_timeout",
                "tag": tag,
                "generated_at": datetime.utcnow().isoformat(),
                "triage_summary": {},
                "owner_ticket_map": {},
                "bulk_issues": {},
                "bulk_issues_with_qi": {},
                "pending_tests": 0,
                "total_tests_processed": 0,
            }), 504
        except ConnectionError as e:
            logger.error(f"Triage count: JITA connection error: {e}")
            return jsonify({
                "error": str(e),
                "type": "jita_connection_error",
                "tag": tag,
                "generated_at": datetime.utcnow().isoformat(),
                "triage_summary": {},
                "owner_ticket_map": {},
                "bulk_issues": {},
                "bulk_issues_with_qi": {},
                "pending_tests": 0,
                "total_tests_processed": 0,
            }), 503

        logger.info(f"Tasks count: {len(tasks)}")
        
        if not tasks:
            return jsonify({
                "tag": tag,
                "generated_at": datetime.utcnow().isoformat(),
                "triage_summary": {},
                "owner_ticket_map": {},
                "bulk_issues": {},
                "pending_tests": 0,
                "message": "No tasks found for this tag"
            })
        
        # Collect all task IDs
        task_ids = []
        for task in tasks:
            task_id = task["_id"]["$oid"]
            task_ids.append(task_id)
        
        # Validate tasks exist and fetch test results in batch
        logger.info(f"Fetching test results for {len(task_ids)} tasks")
        try:
            # Use longer timeout for triage count as it may process many tasks
            test_data = fetch_test_results_batch_with_pagination(task_ids, timeout=180)
            logger.info(f"Fetched {len(test_data)} merged test results")
        except requests.exceptions.Timeout as e:
            logger.error(f"Timeout error in triage count: {e}")
            return jsonify({
                "error": f"Request timed out while fetching test results. This may be due to a large number of tasks ({len(task_ids)}). Please try with fewer tasks or contact support.",
                "type": "timeout_error",
                "task_count": len(task_ids)
            }), 504
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error in triage count: {e}")
            return jsonify({
                "error": f"Failed to fetch test results: {str(e)}",
                "type": "request_error"
            }), 500
        
        # Initialize data structures
        summary = defaultdict(lambda: {
            "Total Failed": 0,
            "Triaged": 0,
            "UnTriaged": 0,
            "Bulk Issues": 0
        })
        ticket_case_map = defaultdict(set)  # ticket → set of unique testcases
        owner_ticket_map = defaultdict(lambda: defaultdict(int))  # owner → ticket → count
        inprogress_tests = 0  # Count pending/running from merged test results
        
        # Deduplicate by test name to ensure each unique test is counted only once
        # This handles cases where merge might not fully deduplicate or same test appears with different statuses
        seen_tests = set()  # Track unique test names we've processed
        seen_pending_running = set()  # Track unique test names for pending/running
        
        # Process test data - matching the working script logic
        for test in test_data:
            status = test.get("status")
            test_name = test.get("test", {}).get("name", "")
            
            # Skip if test name is empty
            if not test_name:
                continue
            
            # Skip succeeded tests
            if status == "Succeeded":
                continue
            
            # Count pending/running tests as in-progress (from merged results)
            # Deduplicate by test name to avoid double counting
            if status == "Pending":
                if test_name not in seen_pending_running:
                    inprogress_tests += 1
                    seen_pending_running.add(test_name)
                continue
            if status == "Running":
                if test_name not in seen_pending_running:
                    inprogress_tests += 1
                    seen_pending_running.add(test_name)
                continue
            
            # Process failed tests only (excluding Succeeded, Pending, Running)
            # Deduplicate by test name to ensure each unique test is counted only once
            if test_name in seen_tests:
                continue  # Skip if we've already processed this test
            
            seen_tests.add(test_name)
            tickets = test.get("jira_tickets", [])
            owner = resolve_owner(test_name)
            
            # Summary stats
            summary[owner]["Total Failed"] += 1
            if tickets:
                summary[owner]["Triaged"] += 1
            else:
                summary[owner]["UnTriaged"] += 1
            
            # Update ticket-to-test map (using set to avoid duplicates)
            for ticket in tickets:
                ticket_case_map[ticket].add(test_name)
                owner_ticket_map[owner][ticket] += 1
        
        # Identify bulk issues (tickets with >5 testcases)
        # Convert sets to lists for JSON serialization
        bulk_issues = {ticket: list(tests) for ticket, tests in ticket_case_map.items() if len(tests) > 5}
        
        # Calculate QI impact for bulk issues only if requested (to speed up triage count)
        include_bulk_qi = request.args.get("include_bulk_qi", "false").lower() == "true"
        bulk_issues_with_qi = {}
        if include_bulk_qi and bulk_issues:
            logger.info("Calculating QI impact for bulk issues (this may take longer)...")
            qi_calculation_result = calculate_bulk_issues_qi_impact(bulk_issues, test_data, tag)
            bulk_issues_with_qi = qi_calculation_result["bulk_issues_with_qi"]
        else:
            logger.info("Skipping bulk issues QI calculation for faster triage count response")
        
        # Update bulk issues count per owner
        for owner in summary:
            owner_tickets = owner_ticket_map[owner]
            bulk_ticket_count = sum(1 for ticket in owner_tickets if ticket in bulk_issues)
            summary[owner]["Bulk Issues"] = bulk_ticket_count
        
        # Convert defaultdict to regular dict for JSON serialization
        triage_summary = {k: dict(v) for k, v in summary.items()}
        owner_ticket_dict = {k: dict(v) for k, v in owner_ticket_map.items()}
        bulk_issues_dict = {k: v for k, v in bulk_issues.items()}
        
        logger.info(f"[END] Triage Count | time={time.time() - start:.2f}s")
        logger.info(f"Triage Summary: {triage_summary}")
        logger.info(f"Owner Ticket Map: {owner_ticket_dict}")
        logger.info(f"Bulk Issues: {bulk_issues_dict}")
        logger.info(f"Bulk Issues with QI: {bulk_issues_with_qi}")
        logger.info(f"Pending Tests: {inprogress_tests}")
        logger.info(f"Total Tests Processed: {len(test_data)}")
        
        return jsonify({
            "tag": tag or None,
            "task_ids": task_ids if task_ids else None,
            "generated_at": datetime.utcnow().isoformat(),
            "triage_summary": triage_summary,
            "owner_ticket_map": owner_ticket_dict,
            "bulk_issues": bulk_issues_dict,
            "bulk_issues_with_qi": bulk_issues_with_qi,
            "pending_tests": inprogress_tests,
            "total_tests_processed": len(test_data)
        })
    except Exception as e:
        logger.error(f"Error in triage count: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------
# Triage Accuracy Analyzer Endpoint
# ---------------------------------------------------
def _config_matches_cached(cached, tag, task_ids):
    """Check if cached data matches current tag/task_ids config."""
    if not cached:
        return False
    cached_tag = (cached.get("tag") or "").strip()
    cached_task_ids = cached.get("task_ids") or []
    req_tag = (tag or "").strip()
    req_task_ids = sorted([str(t).strip() for t in (task_ids or []) if t and str(t).strip()])
    cached_task_ids_sorted = sorted([str(t).strip() for t in cached_task_ids if t])
    # Tag mode: match by tag only
    if req_tag:
        return req_tag == cached_tag
    # Task IDs mode: match by task_ids
    return req_task_ids == cached_task_ids_sorted


@app.route("/mcp/regression/triage-accuracy", methods=["GET"])
@app.route("/api/mcp/regression/triage-accuracy", methods=["GET"])
@jwt_required
def get_triage_accuracy():
    """Triage Accuracy Analyzer: fetch Failed+Warning testcases, compare Jira vs Triage Genie, store in JSON."""
    start = time.time()
    tag = request.args.get("tag")
    if tag is not None:
        tag = (tag or "").strip() or None
    task_ids_param = request.args.get("task_ids")
    task_ids = None
    if task_ids_param:
        task_ids = [tid.strip() for tid in task_ids_param.split(",") if tid.strip()]

    # Fall back to config if params missing
    if not tag and not task_ids:
        config = load_regression_config()
        if config.get("input_mode") == "tag":
            tag = config.get("default_tag") or config.get("tag", "") or ""
        if not tag and config.get("input_mode") == "task_ids" and config.get("task_ids"):
            task_ids = config.get("task_ids", [])

    if not tag and not task_ids:
        return jsonify({"error": "Either tag or task_ids is required"}), 400

    if tag:
        logger.info(f"[START] Triage Accuracy | tag={tag}")
    else:
        logger.info(f"[START] Triage Accuracy | task_ids={len(task_ids)} tasks")

    try:
        load_owner_mapping()
        cache_tag = tag if tag else None
        reload = request.args.get("reload", "false").lower() == "true"
        if reload:
            invalidate_triage_accuracy_cache(cache_tag)
            cached = None
            logger.info("[Triage Accuracy] Cache invalidated, fetching fresh data")
        else:
            cached = load_triage_accuracy_data(cache_tag)
        if cached and _config_matches_cached(cached, tag, task_ids):
            logger.info("[Triage Accuracy] Using cached data")
            return jsonify(cached)

        tasks = fetch_regression_tasks(tag=tag, task_ids=task_ids)
        if not tasks:
            result = {
                "generated_time": datetime.utcnow().isoformat(),
                "tag": tag or None,
                "task_ids": list(task_ids) if task_ids else [],
                "testcases": [],
                "triage_summary": {
                    "total_failed_warning_count": 0,
                    "triaged_count": 0,
                    "triage_genie_count": 0,
                    "total_triage_genie_count": 0,
                    "triage_completed_percent": 0,
                    "triage_genie_percent": 0,
                    "total_triage_genie_percent": 0,
                    "matched_count": 0,
                    "unmatched_count": 0,
                    "matched_percent": 0,
                    "unmatched_percent": 0,
                },
            }
            save_triage_accuracy_data(result, cache_tag)
            return jsonify(result)

        collected_task_ids = [t["_id"]["$oid"] for t in tasks]
        logger.info(f"Fetching test results for {len(collected_task_ids)} tasks")
        test_data = fetch_test_results_batch_with_pagination(collected_task_ids, timeout=180)

        # Filter Failed or Warning; deduplicate by test name
        failed_warning = [
            tr for tr in test_data
            if tr.get("status", "").lower() in ("failed", "failure", "warning", "warn")
        ]
        seen_tests = set()
        unique_results = []
        for tr in failed_warning:
            test_name = (tr.get("test") or {}).get("name", "") if isinstance(tr.get("test"), dict) else ""
            if not test_name or test_name in seen_tests:
                continue
            seen_tests.add(test_name)
            unique_results.append(tr)

        triage_genie_session = create_triage_genie_session()

        def process_one(tr):
            try:
                test_field = tr.get("test", {})
                testcase_name = (test_field.get("name", "") if isinstance(test_field, dict) else
                                str(test_field) if test_field else "")
                status = tr.get("status", "Failed")
                jira_tickets = tr.get("jira_tickets", [])
                jira_ticket = (jira_tickets[0] if jira_tickets else "") or ""
                if isinstance(jira_ticket, dict):
                    jira_ticket = jira_ticket.get("$oid", "") or str(jira_ticket)
                jira_ticket = str(jira_ticket).strip() if jira_ticket else ""

                testcase_id = None
                if isinstance(tr.get("_id"), dict) and "$oid" in tr.get("_id", {}):
                    testcase_id = tr["_id"]["$oid"]
                else:
                    testcase_id = str(tr.get("_id", "")) if tr.get("_id") else ""

                triage_genie_ticket = ""
                if testcase_id and triage_genie_session:
                    try:
                        tg = fetch_triage_genie_ticket_id(testcase_id, triage_session=triage_genie_session)
                        if tg:
                            triage_genie_ticket = str(tg).strip()
                    except Exception as tg_err:
                        logger.debug(f"Triage Genie lookup failed for {testcase_id}: {tg_err}")

                if jira_ticket and triage_genie_ticket:
                    match_status = "Matched" if jira_ticket.upper() == triage_genie_ticket.upper() else "Unmatched"
                else:
                    match_status = "N/A" if not jira_ticket and not triage_genie_ticket else ("" if not (jira_ticket and triage_genie_ticket) else "N/A")

                regression_owner = resolve_owner(testcase_name) if testcase_name else "Unknown"
                return {
                    "testcase_name": testcase_name,
                    "regression_owner": regression_owner,
                    "status": status,
                    "triage_genie_ticket": triage_genie_ticket,
                    "jira_ticket": jira_ticket,
                    "match_status": match_status,
                }
            except Exception as e:
                logger.warning(f"Error processing test result for triage accuracy: {e}")
                return None

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            raw = list(executor.map(process_one, unique_results))
            testcases = [tc for tc in raw if tc is not None]

        total = len(testcases)
        triaged_count = sum(1 for tc in testcases if tc.get("jira_ticket"))
        # Triage Genie % = among triaged (JITA tagged), how many have Triage Genie ticket
        triage_genie_count = sum(1 for tc in testcases if tc.get("jira_ticket") and tc.get("triage_genie_ticket"))
        # Total Triage Genie Tagged = among ALL failed/warning, how many have Triage Genie ticket
        total_triage_genie_count = sum(1 for tc in testcases if tc.get("triage_genie_ticket"))
        matched_count = sum(1 for tc in testcases if tc.get("match_status") == "Matched")
        unmatched_count = sum(1 for tc in testcases if tc.get("match_status") == "Unmatched")

        triage_completed_percent = round(100 * triaged_count / total, 1) if total else 0
        triage_genie_percent = round(100 * triage_genie_count / triaged_count, 1) if triaged_count else 0
        total_triage_genie_percent = round(100 * total_triage_genie_count / total, 1) if total else 0
        denom = matched_count + unmatched_count
        matched_percent = round(100 * matched_count / denom, 1) if denom else 0
        unmatched_percent = round(100 * unmatched_count / denom, 1) if denom else 0

        result = {
            "generated_time": datetime.utcnow().isoformat(),
            "tag": tag if tag else None,
            "task_ids": collected_task_ids,
            "testcases": testcases,
            "triage_summary": {
                "total_failed_warning_count": total,
                "triaged_count": triaged_count,
                "triage_genie_count": triage_genie_count,
                "total_triage_genie_count": total_triage_genie_count,
                "triage_completed_percent": triage_completed_percent,
                "triage_genie_percent": triage_genie_percent,
                "total_triage_genie_percent": total_triage_genie_percent,
                "matched_count": matched_count,
                "unmatched_count": unmatched_count,
                "matched_percent": matched_percent,
                "unmatched_percent": unmatched_percent,
            },
        }
        save_triage_accuracy_data(result, cache_tag)
        logger.info(f"[END] Triage Accuracy | testcases={len(testcases)} | time={time.time() - start:.2f}s")
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in triage accuracy: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/mcp/regression/triage-accuracy/export-excel", methods=["GET"])
@app.route("/api/mcp/regression/triage-accuracy/export-excel", methods=["GET"])
@jwt_required
def export_triage_accuracy_excel():
    """Export triage accuracy data as Excel file."""
    try:
        tag = request.args.get("tag")
        if not tag:
            config = load_regression_config()
            if config.get("input_mode") == "tag":
                tag = config.get("default_tag") or config.get("tag") or ""
        cache_tag = tag if tag else None
        data = load_triage_accuracy_data(cache_tag)
        if not data or not isinstance(data, dict):
            return jsonify({"error": "No triage accuracy data available. Load Triage Accuracy Analyzer first."}), 404

        testcases = data.get("testcases", [])
        triage_summary = data.get("triage_summary", {})

        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            # Sheet 1: Triage Analysis
            df_analysis = pd.DataFrame(testcases, columns=[
                "testcase_name", "regression_owner", "status",
                "triage_genie_ticket", "jira_ticket", "match_status"
            ])
            df_analysis.columns = [
                "Testcase Name", "Regression Owner", "Status",
                "Triage Genie Ticket", "Jira Ticket", "Matched/Unmatched"
            ]
            df_analysis.to_excel(writer, sheet_name="Triage Analysis", index=False)

            # Sheet 2: Triage Summary (Metric, Count, Percentage)
            tg_count = triage_summary.get("triage_genie_count", 0)
            matched = triage_summary.get("matched_count", 0)
            unmatched = triage_summary.get("unmatched_count", 0)
            summary_rows = [
                ("Triage Genie Ticket %(based on completed triaged)", tg_count, triage_summary.get("triage_genie_percent", 0)),
                ("Matched %", matched, triage_summary.get("matched_percent", 0)),
                ("Unmatched %", unmatched, triage_summary.get("unmatched_percent", 0)),
            ]
            df_summary = pd.DataFrame(summary_rows, columns=["Metric", "Count", "Percentage"])
            df_summary.to_excel(writer, sheet_name="Triage Summary", index=False)

        output.seek(0)
        return send_file(
            output,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name="triage_accuracy_report.xlsx",
        )
    except Exception as e:
        logger.error(f"Error exporting triage accuracy Excel: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------
# QI Summary Report Endpoint
# ---------------------------------------------------
@app.route("/mcp/regression/qi-summary", methods=["GET"])
@jwt_required
def get_qi_summary():
    start = time.time()
    tag = request.args.get("tag")
    task_ids_param = request.args.get("task_ids")  # Comma-separated task IDs
    
    # Parse task_ids if provided
    task_ids = None
    if task_ids_param:
        task_ids = [tid.strip() for tid in task_ids_param.split(",") if tid.strip()]
    
    if not tag and not task_ids:
        return jsonify({"error": "Either tag or task_ids is required"}), 400
    
    if tag:
        logger.info(f"[START] QI Summary | tag={tag}")
    else:
        logger.info(f"[START] QI Summary | task_ids={len(task_ids)} tasks")
    
    try:
        # Fetch tasks for the tag or task IDs
        tasks = fetch_regression_tasks(tag=tag, task_ids=task_ids)
        
        # Collect all task IDs from fetched tasks
        collected_task_ids = [task["_id"]["$oid"] for task in tasks]
        
        # Fetch test results using agave_test_results API for accurate counts
        logger.info(f"Fetching test results for {len(collected_task_ids)} tasks using agave_test_results API")
        test_results = []
        if collected_task_ids:
            try:
                test_results = fetch_test_results_batch_with_pagination(collected_task_ids)
                logger.info(f"Fetched {len(test_results)} test results")
            except Exception as e:
                logger.warning(f"Failed to fetch test results from agave_test_results API: {e}. Falling back to test_result_count.")
                test_results = []
        
        # Group test results by task_id and branch
        test_results_by_task = defaultdict(list)
        task_branch_map = {}
        for task in tasks:
            task_id = task["_id"]["$oid"]
            branch = task.get("branch", "unknown")
            task_branch_map[task_id] = branch
        
        for test_result in test_results:
            agave_task_id = test_result.get("agave_task_id")
            if agave_task_id:
                # Handle both string and $oid format
                if isinstance(agave_task_id, dict) and "$oid" in agave_task_id:
                    task_id = agave_task_id["$oid"]
                else:
                    task_id = str(agave_task_id)
                if task_id in task_branch_map:
                    test_results_by_task[task_id].append(test_result)
        
        # Generate QI Summary Report
        summary = {
            "tag": tag or None,
            "task_ids": collected_task_ids if collected_task_ids else None,
            "generated_at": datetime.utcnow().isoformat(),
            "total_tasks": len(tasks),
            "status_summary": {
                "testing": 0,
                "completed": 0,
                "pending": 0,
                "failed": 0
            },
            "test_summary": {
                "total": 0,
                "succeeded": 0,
                "failed": 0,
                "pending": 0,
                "warning": 0,
                "running": 0,
                "skipped": 0,
                "killed": 0
            },
            "branch_summary": {}
        }
        
        for task in tasks:
            task_id = task["_id"]["$oid"]
            status = task.get("status", "").lower()
            branch = task.get("branch", "unknown")
            
            # Count test statuses from agave_test_results if available
            task_test_counts = {
                "total": 0,
                "Succeeded": 0,
                "Failed": 0,
                "Pending": 0,
                "Warning": 0,
                "Running": 0,
                "Skipped": 0,
                "Killed": 0
            }
            
            if task_id in test_results_by_task:
                # Count statuses from actual test results
                for test_result in test_results_by_task[task_id]:
                    test_status = test_result.get("status", "")
                    task_test_counts["total"] += 1
                    
                    # Normalize status names (handle case-insensitive matching)
                    status_lower = test_status.lower() if test_status else ""
                    
                    if status_lower == "succeeded" or status_lower == "success":
                        task_test_counts["Succeeded"] += 1
                    elif status_lower == "failed" or status_lower == "failure":
                        task_test_counts["Failed"] += 1
                    elif status_lower == "pending" or status_lower == "waiting":
                        task_test_counts["Pending"] += 1
                    elif status_lower == "warning" or status_lower == "warn":
                        task_test_counts["Warning"] += 1
                    elif status_lower == "running" or status_lower == "executing" or status_lower == "in_progress":
                        task_test_counts["Running"] += 1
                    elif status_lower == "skipped" or status_lower == "skip":
                        task_test_counts["Skipped"] += 1
                    elif status_lower == "killed" or status_lower == "terminated" or status_lower == "cancelled":
                        task_test_counts["Killed"] += 1
                    else:
                        # For unknown statuses, try to infer from common patterns
                        if any(x in status_lower for x in ["pending", "waiting", "queued"]):
                            task_test_counts["Pending"] += 1
                        elif any(x in status_lower for x in ["running", "executing", "in_progress", "active"]):
                            task_test_counts["Running"] += 1
                        elif any(x in status_lower for x in ["skipped", "skip"]):
                            task_test_counts["Skipped"] += 1
                        elif any(x in status_lower for x in ["killed", "terminated", "cancelled", "aborted"]):
                            task_test_counts["Killed"] += 1
                        else:
                            # Default to pending only if truly unknown
                            task_test_counts["Pending"] += 1
            else:
                # Fallback to test_result_count if agave_test_results not available
                tc = task.get("test_result_count", {})
                task_test_counts = {
                    "total": tc.get("Total", 0),
                    "Succeeded": tc.get("Succeeded", 0),
                    "Failed": tc.get("Failed", 0),
                    "Pending": tc.get("Pending", 0),
                    "Warning": tc.get("Warning", 0),
                    "Running": tc.get("Running", 0),
                    "Skipped": tc.get("Skipped", 0),
                    "Killed": tc.get("Killed", 0)
                }
            
            # Status summary
            if status == "testing":
                summary["status_summary"]["testing"] += 1
            elif status == "pending":
                summary["status_summary"]["pending"] += 1
            elif task_test_counts["Failed"] > 0:
                summary["status_summary"]["failed"] += 1
            else:
                summary["status_summary"]["completed"] += 1
            
            # Test summary (aggregate across all tasks)
            summary["test_summary"]["total"] += task_test_counts["total"]
            summary["test_summary"]["succeeded"] += task_test_counts["Succeeded"]
            summary["test_summary"]["failed"] += task_test_counts["Failed"]
            summary["test_summary"]["pending"] += task_test_counts["Pending"]
            summary["test_summary"]["warning"] += task_test_counts["Warning"]
            summary["test_summary"]["running"] += task_test_counts["Running"]
            summary["test_summary"]["skipped"] += task_test_counts["Skipped"]
            summary["test_summary"]["killed"] += task_test_counts["Killed"]
            
            # Branch summary
            if branch not in summary["branch_summary"]:
                summary["branch_summary"][branch] = {
                    "total_tasks": 0,
                    "total_tests": 0,
                    "failed_tests": 0
                }
            
            summary["branch_summary"][branch]["total_tasks"] += 1
            summary["branch_summary"][branch]["total_tests"] += task_test_counts["total"]
            summary["branch_summary"][branch]["failed_tests"] += task_test_counts["Failed"]
        
        logger.info(f"[END] QI Summary | time={time.time() - start:.2f}s")
        logger.info(f"Test Summary: {summary['test_summary']}")
        
        return jsonify(summary)
    except Exception as e:
        logger.error(f"Error in QI summary: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------
# TCMS Overall QI Endpoint
# ---------------------------------------------------
def _resolve_tcms_milestone(branch_name):
    """
    Convert a full branch name (as shown in the Run Summary table) to the
    short milestone name the TCMS API expects.

    Lookup order:
      1. Explicit entry in BRANCH_SHORT_NAME_MAP
      2. Regex extraction of a version pattern like X.Y or X.Y.Z
      3. Fall back to the original branch_name unchanged
    """
    if branch_name in BRANCH_SHORT_NAME_MAP:
        return BRANCH_SHORT_NAME_MAP[branch_name]

    version_match = re.search(r'(\d+\.\d+(?:\.\d+)?)', branch_name)
    if version_match:
        return version_match.group(1)

    return branch_name


@app.route("/mcp/regression/tcms-overall-qi", methods=["GET"])
@jwt_required
def get_tcms_overall_qi():
    """
    Fetch the aggregate QI (average_total_op_success_percentage) from the
    TCMS Summary API for a given team, branch, and time filter.

    Branch handling:
      * **master** — uses team-specific filters (additional_data.team,
        team test_sets regex, release_name exclusion, tag exclusions) and
        ``feat_type=regression``.
      * **release branches** (e.g. ganges-7.6-stable → milestone "7.6") —
        uses simpler filters (test_sets regex + deprecated flag only) and
        ``feat_type=all``.
    """
    start = time.time()
    team_name = request.args.get("team_name")
    branch_name = request.args.get("branch_name")
    time_filter = request.args.get("time_filter", "all")

    if not team_name or not branch_name:
        return jsonify({"error": "team_name and branch_name are required"}), 400

    milestone = _resolve_tcms_milestone(branch_name)
    is_master = branch_name.lower() in ("master", "main")

    logger.info(
        f"[START] TCMS Overall QI | team={team_name} branch={branch_name} "
        f"milestone={milestone} is_master={is_master} time_filter={time_filter}"
    )

    try:
        if is_master:
            # Master: team-specific filters, feat_type=regression
            filters = json.dumps({
                "$and": [
                    {"test_case.test_sets": {"$regex": f"test_sets/milestones/{milestone}/", "$options": "i"}},
                    {"release_name": {"$ne": milestone}},
                    {"test_case.metadata.tags": {"$nin": ["SYSTEST_LONGEVITY", "LIMITED_RUNS"]}},
                    {"additional_data.team": f"{milestone}/{team_name}"},
                    {"test_case.test_sets": {"$regex": f"test_sets/milestones/{milestone}/{team_name}/", "$options": "i"}},
                    {"test_case.deprecated": False},
                ]
            })
            feat_type = "regression"
        else:
            # Release branch: simple filters, feat_type=all
            filters = json.dumps({
                "$and": [
                    {"test_case.test_sets": {"$regex": f"test_sets/milestones/{milestone}/", "$options": "i"}},
                    {"test_case.deprecated": False},
                ]
            })
            feat_type = "all"

        params = {
            "aggregation_field": "target_package_type",
            "time_filter": time_filter,
            "target_milestone": milestone,
            "feat_type": feat_type,
            "filters": filters,
        }

        url = f"{TCMS_SUMMARY_BASE}/milestone_all_test_cases/aggregate/metrics"
        response = requests.get(
            url,
            params=params,
            headers={"Content-Type": "application/json"},
            verify=False,
            timeout=30,
        )

        if response.status_code != 200:
            logger.error(f"TCMS Summary API returned {response.status_code}: {response.text[:500]}")
            return jsonify({"error": f"TCMS API error: {response.status_code}"}), 502

        data = response.json()
        if not data.get("success") or not data.get("data"):
            logger.warning("TCMS Summary API returned no data")
            return jsonify({
                "qi_value": None,
                "message": "No data returned from TCMS",
                "team_name": team_name,
                "branch_name": branch_name,
                "milestone": milestone,
                "time_filter": time_filter,
            })

        overall = data["data"][0]
        qi_value = overall.get("average_total_op_success_percentage")

        logger.info(
            f"[END] TCMS Overall QI | qi={qi_value} | time={time.time() - start:.2f}s"
        )

        return jsonify({
            "qi_value": qi_value,
            "team_name": team_name,
            "branch_name": branch_name,
            "milestone": milestone,
            "time_filter": time_filter,
            "total_tests": overall.get("total"),
            "run": overall.get("run"),
            "passed": overall.get("passed"),
            "failed": overall.get("failed"),
            "run_percentage": overall.get("run_percentage"),
            "overall_effectiveness": overall.get("overall_effectiveness"),
            "overall_stability": overall.get("overall_stability"),
        })

    except requests.exceptions.Timeout:
        logger.error("TCMS Summary API request timed out")
        return jsonify({"error": "TCMS API request timed out"}), 504
    except Exception as e:
        logger.error(f"Error fetching TCMS Overall QI: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------
# Team Config Endpoint
# ---------------------------------------------------
@app.route("/mcp/regression/team-config", methods=["GET"])
@jwt_required
def get_team_config():
    """Return the tag-to-team configuration and branch short-name mapping."""
    return jsonify({
        "team_config": TEAM_CONFIG,
        "branch_short_names": BRANCH_SHORT_NAME_MAP,
    })


# ======================================================
# Run Report - QI Analysis Endpoint
# ======================================================
def generate_qi_analysis(run_folder):
    """Generate QI analysis Excel file from CSV files in run folder"""
    try:
        # File paths
        tcms_csv_path = os.path.join(run_folder, 'tcms.csv')
        regression_owners_path = os.path.join(run_folder, 'regression_owners.csv')
        jita_csv_path = os.path.join(run_folder, 'jita.csv')
        tcms_bugs_csv_path = os.path.join(run_folder, 'tcms_bugs.csv')
        
        # Check if open-bugs.csv exists (alternative name)
        if not os.path.exists(tcms_bugs_csv_path):
            tcms_bugs_csv_path = os.path.join(run_folder, 'open-bugs.csv')
        
        # Validate required files exist
        required_files = {
            'tcms.csv': tcms_csv_path,
            'regression_owners.csv': regression_owners_path,
            'jita.csv': jita_csv_path,
            'tcms_bugs.csv or open-bugs.csv': tcms_bugs_csv_path
        }
        
        missing_files = [name for name, path in required_files.items() if not os.path.exists(path)]
        if missing_files:
            raise FileNotFoundError(f"Missing required files: {', '.join(missing_files)}")
        
        # Read CSV files
        logger.info("Reading CSV files...")
        df = pd.read_csv(tcms_csv_path)
        df_jita = pd.read_csv(jita_csv_path)
        df_o = pd.read_csv(regression_owners_path)
        tcms_bug_list = pd.read_csv(tcms_bugs_csv_path)
        
        # Process JITA data (remove Pending, handle duplicates)
        df_jita = df_jita[~df_jita.status.isin(['Pending'])]
        temp_df = df_jita[df_jita['start_time'] != "-"]
        if len(temp_df) > 0:
            temp_df.loc[:, 'start_time'] = pd.to_datetime(temp_df['start_time'], dayfirst=True)
            min_time = temp_df['start_time'].min()
            df_jita.loc[df_jita['start_time'] == '-', "start_time"] = min_time
            df_jita.loc[:, 'start_time'] = pd.to_datetime(df_jita['start_time'], dayfirst=True)
            df_jita = df_jita.sort_values(['start_time'], ascending=False).drop_duplicates(['name'])
        
        # Process TCMS data
        df['Test_Set'] = 'None'
        ll = []
        
        for index, row in df.iterrows():
            # Extract test set name
            if isinstance(row.get('Test Sets'), str):
                for testset in row['Test Sets'].split(','):
                    if re.search('test_sets/milestones/.*/cdp/.*/Regression_team_owned_lst', testset):
                        df.loc[index, 'Test_Set'] = testset.split('/')[-1]
                        break
            
            # Extract last passed ops
            if isinstance(row.get('Last Passed Ops'), str):
                parts = row['Last Passed Ops'].split('/')
                if len(parts) >= 2:
                    passed_ops = parts[0]
                    total_ops = parts[1].split('(')[0]
                    df.loc[index, 'last_passed_ops'] = passed_ops
                    df.loc[index, 'last_passed_total_ops'] = total_ops
            
            # Extract last run ops
            col_name = 'Last Run Ops'
            if isinstance(row.get(col_name), str):
                parts = row[col_name].split('/')
                if len(parts) >= 2:
                    passed_ops = parts[0]
                    total_ops = parts[1].split('(')[0]
                    df.loc[index, 'last_run_ops'] = int(passed_ops) if passed_ops.isdigit() else 0
                    df.loc[index, 'last_run_total_ops'] = int(total_ops) if total_ops.isdigit() else 0
                    if df.loc[index, 'last_run_total_ops'] > 0:
                        df.loc[index, 'last_run_qi'] = 100 * (df.loc[index, 'last_run_ops'] / df.loc[index, 'last_run_total_ops'])
                    else:
                        df.loc[index, 'last_run_qi'] = 0
                else:
                    df.loc[index, 'last_run_ops'] = 0
                    df.loc[index, 'last_run_total_ops'] = 0
                    df.loc[index, 'last_run_qi'] = 0
            else:
                df.loc[index, 'last_run_ops'] = 0
                df.loc[index, 'last_run_total_ops'] = 0
                df.loc[index, 'last_run_qi'] = 0
            
            # Extract test sets
            if isinstance(row.get('Test Sets'), str):
                test_sets = row['Test Sets'].split(',')
                for test_set in test_sets:
                    if re.search('test_sets/milestones/.*/cdp/.*/Regression_team_owned_lst/', test_set):
                        df.loc[index, 'Test_Set'] = test_set.split('/')[-1]
                        break
                    else:
                        df.loc[index, 'Test_Set'] = test_set.split('/')[-1]
            
            # Process open bugs
            if isinstance(row.get('Open Bugs'), str):
                bug_list = row['Open Bugs'].split(',')
                bugtype = tcms_bug_list[tcms_bug_list.Ticket.isin(bug_list)].groupby(['Type'])['Ticket'].count().to_dict()
                for key in bugtype.keys():
                    df.loc[index, f'{key}_bug_count'] = bugtype.get(key, 0)
                
                # Create bug-testcase mapping
                for bug in bug_list:
                    m = {
                        'bug_id': bug,
                        'test_case': row['Name'],
                        'last_run_qi': df.loc[index, 'last_run_qi'],
                        'last_run_ops': df.loc[index, 'last_run_ops'],
                        'last_run_total_ops': df.loc[index, 'last_run_total_ops'],
                        'Last Run Status': row.get('Last Run Status', '')
                    }
                    ll.append(m)
        
        df['last_run_ops'] = df['last_run_ops'].astype(int)
        df['last_run_total_ops'] = df['last_run_total_ops'].astype(int)
        
        df_bugs = pd.DataFrame(ll)
        
        # Extract test area
        testcasenames_split = df['Name'].str.split(pat='.', expand=True)
        t = testcasenames_split[[0, 1, 2, 3]].agg('.'.join, axis=1)
        df.insert(loc=1, column='Test Area', value=t)
        
        # Join with regression owners
        df = df.join(df_o.set_index('Test Area'), on='Test Area', how='left')
        
        nr_test_cases = df['Name'].count()
        
        def generate_qi_impact(df_data, colname, nr_test_cases):
            s1 = df_data.groupby([colname])['last_run_qi'].agg("mean").sort_values()
            s2 = df_data.groupby([colname])[colname].count()
            if colname == 'bug_id':
                s3 = df_bugs.groupby([colname, 'Last Run Status'])[[colname]].count().unstack()
                df_testarea = pd.concat([s1, s2, s3], axis=1)
            else:
                df_testarea = pd.concat([s1, s2], axis=1)
            cols = ['average_qi', 'nr_test_cases']
            cols.extend(df_testarea.columns[2:])
            df_testarea.columns = cols
            df_testarea['qi_impact'] = (df_testarea['average_qi'] - 100) * df_testarea['nr_test_cases']
            df_testarea['overall_qi_impact'] = 100 * (df_testarea['qi_impact'] / (100 * nr_test_cases))
            return df_testarea.sort_values(['overall_qi_impact'])
        
        df_testareas = generate_qi_impact(df, 'Test Area', nr_test_cases)
        df_bugid = generate_qi_impact(df_bugs, 'bug_id', nr_test_cases)
        
        # Process dates
        baseline_date = datetime.now() - timedelta(days=30)
        try:
            df.loc[:, 'Last Run Date'] = pd.to_datetime(df['Last Run Date'], format="%Y-%m-%d")
        except:
            df.loc[:, 'Last Run Date'] = pd.to_datetime(df['Last Run Date'])
        df = df.sort_values(['Last Run Date']).drop_duplicates(['Name'])
        
        df_tcms = df.copy()
        df_jita_tcms = df_jita.join(df.set_index('Name'), on='name', how='left')
        
        # Merge bugs with TCMS bug list
        tcms_bug_list['Name'] = tcms_bug_list['Ticket']
        del tcms_bug_list['Ticket']
        tcms_bug_qi = tcms_bug_list.join(df_bugid, on='Name', how='left')
        tcms_bug_qi['tcms_test_cases'] = tcms_bug_qi['nr_test_cases']
        del tcms_bug_qi['nr_test_cases']
        tcms_bug_qi = tcms_bug_qi.set_index(['Name'])
        
        # Generate output list for summary
        output_list = []
        output_list.append(f"Analysis ran on: {datetime.now()}")
        output_list.append(f"Total number of test cases: {df['Name'].count()}")
        output_list.append(f"Total number of test that passed in last run: {df[df['Last Run Status']=='succeeded']['Name'].count()}")
        output_list.append(f"Total number of test that failed in last run: {df[df['Last Run Status']=='failed']['Name'].count()}")
        output_list.append(f"Total number of test that warned in last run: {df[df['Last Run Status']=='warning']['Name'].count()}")
        output_list.append(f"Total number of test cases with bugs: {df[~df['Open Bugs'].isna()]['Name'].count()}")
        output_list.append(f"Last Run QI: {df['last_run_qi'].mean():.2f}")
        output_list.append(f"Total possible QI: {100*df['Name'].count():.0f}")
        output_list.append(f"Total QI of all test cases: {df[['last_run_qi']].sum().iloc[0]:.0f}")
        output_list.append(f"Total QI impacted due to bugs (overestimated): {df_bugid['qi_impact'].sum():.0f} in Percentage: {df_bugid['qi_impact'].sum()/(df['Name'].count()):.2f}%")
        output_list.append(f"Total number of bugs identified by the runs so far: {len(df_bugs['bug_id'].unique())}")
        output_list.append(f"Tests that never passed: {df[df['Last Passed Date'].isna()].count().iloc[0]}")
        output_list.append(f"Tests that never passed and have last_run_qi<50: {df[((df['Last Passed Date'].isna())&(df['last_run_qi']<50))].count().iloc[0]}")
        t = (100-(df[((df['Last Passed Date'].isna())&(df['last_run_qi']<50))]['last_run_qi'].sum()))*df[((df['Last Passed Date'].isna())&(df['last_run_qi']<50))].count().iloc[0]
        output_list.append(f"QI impact of tests that never passed and have last_run_qi<50: {t:.2f} in Percentage: {(t/df['Name'].count()):.2f}%")
        output_list.append(f"Failed tests with no open Bugs: {df_tcms[(df_tcms['Last Run Status'].isin(['failed','warning'])) & (df_tcms['Open Bugs'].isna())]['Name'].count()}")
        output_list.append(f"Failed tests that are not triaged (TCMS): {df[(df['Last Run Status'] != 'succeeded') & (df.get('Is Last Run Triaged', pd.Series([False]*len(df))) != True)]['Name'].count()}")
        
        # Generate Excel file
        filename = 'analysis_' + datetime.now().isoformat().replace(':', '_').replace('-', '_')
        xlsfilepath = os.path.join(run_folder, filename + '.xlsx')
        
        # Use openpyxl engine for writing
        with pd.ExcelWriter(xlsfilepath, engine='openpyxl') as writer:
            # Summary sheet
            pd.DataFrame(output_list).to_excel(writer, sheet_name='summary', startrow=0, startcol=0, index=False, header=False)
            
            # Bug QI Summary sheet
            tcms_bug_qi.groupby(['Type', 'Priority'])[['overall_qi_impact']].agg(['count', 'sum']).to_excel(
                writer, sheet_name='bug_qi_summary', startrow=0, startcol=0, index=True
            )
            
            # Bugs QI Analysis sheet
            tcms_bug_qi.sort_values(['overall_qi_impact']).to_excel(
                writer, sheet_name='bugs_qi_analysis', startrow=0, startcol=0, index=True
            )
        
        logger.info(f"Generated QI analysis file: {xlsfilepath}")
        return xlsfilepath
        
    except Exception as e:
        logger.error(f"Error generating QI analysis: {e}", exc_info=True)
        raise

@app.route("/mcp/regression/run-report/list-analysis-files", methods=["POST"])
@jwt_required
def list_analysis_files():
    """List all analysis_*.xlsx files in a given directory"""
    try:
        req_data = request.json
        folder_path = req_data.get("folder_path")
        
        if not folder_path:
            return jsonify({"error": "folder_path is required"}), 400
        
        if not os.path.isdir(folder_path):
            return jsonify({"error": f"Folder path does not exist: {folder_path}"}), 400
        
        # Find all files matching analysis_*.xlsx pattern
        pattern = os.path.join(folder_path, "analysis_*.xlsx")
        matching_files = glob.glob(pattern)
        
        # Sort by modification time (newest first)
        matching_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        
        # Return just the filenames
        file_list = [os.path.basename(f) for f in matching_files]
        
        return jsonify({
            "success": True,
            "files": file_list,
            "folder_path": folder_path
        })
    except Exception as e:
        logger.error(f"Error listing analysis files: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

def read_existing_analysis_file(analysis_file_path):
    """Read and extract data from an existing analysis Excel file"""
    try:
        if not os.path.exists(analysis_file_path):
            raise FileNotFoundError(f"Analysis file not found: {analysis_file_path}")
        
        logger.info(f"Reading QI analysis from existing file: {analysis_file_path}")
        
        # Read Excel file sheets (same logic as in qi_analysis_from_folder)
        excel_data = {}
        
        # 1. Read summary sheet
        try:
            df_summary = pd.read_excel(analysis_file_path, sheet_name="summary", header=None)
            summary_text = "\n".join(df_summary[0].astype(str).tolist()) if len(df_summary.columns) > 0 else ""
            excel_data["summary"] = summary_text
        except Exception as e:
            logger.warning(f"Could not read summary sheet: {e}")
            excel_data["summary"] = ""
        
        # 2. Read bug_qi_summary sheet
        try:
            df_bug_qi_summary = pd.read_excel(analysis_file_path, sheet_name="bug_qi_summary", index_col=[0, 1], header=[0, 1])
            # Handle multi-level columns (from groupby with agg)
            bug_qi_summary_data = []
            type_totals = {}  # Track total impacting QI by type (test, product, framework, other)
            
            for (bug_type, priority), row in df_bug_qi_summary.iterrows():
                # Extract count and sum from multi-level columns
                count_val = 0
                sum_val = 0.0
                
                # Try to find count and sum columns
                for col in df_bug_qi_summary.columns:
                    if isinstance(col, tuple):
                        if 'overall_qi_impact' in str(col[0]).lower() or 'overall_qi_impact' in str(col):
                            if 'count' in str(col[1]).lower() or 'count' in str(col):
                                count_val = int(row[col]) if pd.notna(row[col]) else 0
                            elif 'sum' in str(col[1]).lower() or 'sum' in str(col):
                                sum_val = float(row[col]) if pd.notna(row[col]) else 0.0
                
                # Fallback: if columns are flattened
                if count_val == 0 and sum_val == 0.0:
                    for col in df_bug_qi_summary.columns:
                        col_str = str(col)
                        if 'count' in col_str.lower():
                            count_val = int(row[col]) if pd.notna(row[col]) else 0
                        elif 'sum' in col_str.lower():
                            sum_val = float(row[col]) if pd.notna(row[col]) else 0.0
                
                # Accumulate total by type
                type_str = str(bug_type).lower()
                if type_str not in type_totals:
                    type_totals[type_str] = 0.0
                type_totals[type_str] += sum_val
                
                bug_qi_summary_data.append({
                    "type": str(bug_type),
                    "priority": str(priority),
                    "testcase_count": count_val,
                    "impacting_qi": sum_val
                })
            
            # Calculate totals for test, product, framework, other
            type_summary = {
                "test": 0.0,
                "product": 0.0,
                "framework": 0.0,
                "other": 0.0
            }
            
            for type_key, total_val in type_totals.items():
                type_lower = type_key.lower()
                if 'test' in type_lower:
                    type_summary["test"] += total_val
                elif 'product' in type_lower:
                    type_summary["product"] += total_val
                elif 'framework' in type_lower:
                    type_summary["framework"] += total_val
                else:
                    type_summary["other"] += total_val
            
            excel_data["bug_qi_summary"] = bug_qi_summary_data
            excel_data["type_summary"] = type_summary
        except Exception as e:
            logger.warning(f"Could not read bug_qi_summary sheet: {e}")
            excel_data["bug_qi_summary"] = []
        
        # 3. Read bugs_qi_analysis sheet (Top QI Impacting bugs)
        try:
            df_bugs_qi_analysis = pd.read_excel(analysis_file_path, sheet_name="bugs_qi_analysis", index_col=0)
            # Sort by overall_qi_impact in ascending order (most negative/impactful first) and get top 30
            if "overall_qi_impact" in df_bugs_qi_analysis.columns:
                df_bugs_qi_analysis = df_bugs_qi_analysis.sort_values("overall_qi_impact", ascending=True).head(30)
            
            # Extract required columns
            top_bugs = []
            for bug_name, row in df_bugs_qi_analysis.iterrows():
                bug_data = {
                    "name": str(bug_name),
                    "type": str(row.get("Type", "")) if "Type" in row else "",
                    "priority": str(row.get("Priority", "")) if "Priority" in row else "",
                    "summary": str(row.get("Summary", "")) if "Summary" in row else "",
                    "assignee": str(row.get("Assignee", "")) if "Assignee" in row else "",
                    "impacted_tcs_latest_run": int(row.get("Impacted TCs (Latest Run)", 0)) if "Impacted TCs (Latest Run)" in row else 0,
                    "deferred": str(row.get("Deferred", "")) if "Deferred" in row else "",
                    "average_qi": float(row.get("average_qi", 0)) if "average_qi" in row else 0.0,
                    "overall_qi_impact": float(row.get("overall_qi_impact", 0)) if "overall_qi_impact" in row else 0.0
                }
                top_bugs.append(bug_data)
            
            excel_data["top_qi_impacting_bugs"] = top_bugs
        except Exception as e:
            logger.warning(f"Could not read bugs_qi_analysis sheet: {e}")
            excel_data["top_qi_impacting_bugs"] = []
        
        return excel_data
    except Exception as e:
        logger.error(f"Error reading existing analysis file: {e}", exc_info=True)
        raise

@app.route("/mcp/regression/run-report/qi-analysis", methods=["POST"])
@jwt_required
def qi_analysis_from_folder():
    """Generate QI analysis Excel file and extract data from it, or read from existing file"""
    try:
        req_data = request.json
        run_folder = req_data.get("run_folder")
        analysis_file_name = req_data.get("analysis_file")  # Optional: if provided, use existing file
        
        if not run_folder:
            return jsonify({"error": "run_folder path is required"}), 400
        
        if not os.path.isdir(run_folder):
            return jsonify({"error": f"Folder path does not exist: {run_folder}"}), 400
        
        # If analysis_file is provided, read from existing file
        if analysis_file_name:
            analysis_file_path = os.path.join(run_folder, analysis_file_name)
            if not os.path.exists(analysis_file_path):
                return jsonify({"error": f"Analysis file not found: {analysis_file_name}"}), 400
            
            excel_data = read_existing_analysis_file(analysis_file_path)
            
            return jsonify({
                "success": True,
                "analysis_file": analysis_file_name,
                "run_folder": run_folder,
                "data": excel_data
            })
        
        # Otherwise, generate the analysis Excel file
        logger.info(f"Generating QI analysis for folder: {run_folder}")
        analysis_file = generate_qi_analysis(run_folder)
        
        # Read the generated file using the shared function
        excel_data = read_existing_analysis_file(analysis_file)
        
        return jsonify({
            "success": True,
            "analysis_file": os.path.basename(analysis_file),
            "run_folder": run_folder,
            "data": excel_data
        })
        
    except Exception as e:
        logger.error(f"Error reading QI analysis: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

# ======================================================
# Run Report - Email Endpoints
# ======================================================
@app.route("/mcp/regression/run-report/preview-email", methods=["POST"])
@jwt_required
def preview_qi_bug_email():
    """Generate email preview for Top QI Impacting Bugs"""
    try:
        req_data = request.json
        bugs = req_data.get("bugs", [])
        branch_name = req_data.get("branch_name", "Unknown Branch")
        run_folder = req_data.get("run_folder", "")
        
        if not bugs or len(bugs) == 0:
            return jsonify({"error": "Bug data is required"}), 400
        
        # Collect unique assignees
        assignees = set()
        for bug in bugs:
            assignee = bug.get("assignee", "")
            if assignee:
                assignees.add(assignee)
        
        if not assignees:
            return jsonify({"error": "No assignees found in bug data"}), 400
        
        # Convert assignees to email format
        recipient_emails = []
        for assignee in assignees:
            if "@" in assignee:
                recipient_emails.append(assignee)
            else:
                recipient_emails.append(f"{assignee}@nutanix.com")
        
        # Create email subject
        subject = f"Top QI Impacting Bugs on {branch_name}"
        
        # Create email body with HTML table for all bugs
        bugs_table_rows = ""
        for idx, bug in enumerate(bugs, 1):
            bugs_table_rows += f"""
                <tr>
                    <td>{idx}</td>
                    <td>{bug.get('name', 'N/A')}</td>
                    <td>{bug.get('type', 'N/A')}</td>
                    <td>{bug.get('priority', 'N/A')}</td>
                    <td>{bug.get('summary', 'N/A')}</td>
                    <td>{bug.get('assignee', 'N/A')}</td>
                    <td>{bug.get('impacted_tcs_latest_run', 0)}</td>
                    <td>{bug.get('deferred', 'N/A')}</td>
                    <td>{bug.get('average_qi', 0):.2f}</td>
                    <td>{bug.get('overall_qi_impact', 0):.2f}</td>
                </tr>
            """
        
        html_body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
                .summary {{ margin: 20px 0; padding: 15px; background-color: #f8f9fa; border-left: 4px solid #3498db; }}
                table {{ border-collapse: collapse; width: 100%; margin: 20px 0; font-size: 12px; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background-color: #3498db; color: white; font-weight: bold; }}
                tr:nth-child(even) {{ background-color: #f2f2f2; }}
                tr:hover {{ background-color: #e8f4f8; }}
                .note {{ margin-top: 20px; padding: 15px; background-color: #fff3cd; border-left: 4px solid #ffc107; }}
            </style>
        </head>
        <body>
            <div class="summary">
                <h2>Top QI Impacting Bugs Notification</h2>
                <p>The following bugs have been identified as top QI impacting bugs on branch: <strong>{branch_name}</strong></p>
                <p><strong>Total Bugs:</strong> {len(bugs)}</p>
                <p><strong>Recipients:</strong> {', '.join(recipient_emails)}</p>
            </div>
            
            <table>
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Bug Name</th>
                        <th>Type</th>
                        <th>Priority</th>
                        <th>Summary</th>
                        <th>Assignee</th>
                        <th>Impacted TCs</th>
                        <th>Deferred</th>
                        <th>Average QI</th>
                        <th>Overall QI Impact</th>
                    </tr>
                </thead>
                <tbody>
                    {bugs_table_rows}
                </tbody>
            </table>
            
            <div class="note">
                <p><strong>Note:</strong> These bugs impact more than 4 test cases and have significant overall QI impact. Please review and take appropriate action.</p>
                <p><strong>Run Folder:</strong> {run_folder}</p>
            </div>
        </body>
        </html>
        """
        
        # Plain text version
        text_body = f"""
Top QI Impacting Bugs Notification

The following bugs have been identified as top QI impacting bugs on branch: {branch_name}

Total Bugs: {len(bugs)}
Recipients: {', '.join(recipient_emails)}

Bug Details:
"""
        for idx, bug in enumerate(bugs, 1):
            text_body += f"""
{idx}. {bug.get('name', 'N/A')}
   - Type: {bug.get('type', 'N/A')}
   - Priority: {bug.get('priority', 'N/A')}
   - Summary: {bug.get('summary', 'N/A')}
   - Assignee: {bug.get('assignee', 'N/A')}
   - Impacted Test Cases: {bug.get('impacted_tcs_latest_run', 0)}
   - Deferred: {bug.get('deferred', 'N/A')}
   - Average QI: {bug.get('average_qi', 0):.2f}
   - Overall QI Impact: {bug.get('overall_qi_impact', 0):.2f}
"""
        
        text_body += f"""

Note: These bugs impact more than 4 test cases and have significant overall QI impact. Please review and take appropriate action.

Run Folder: {run_folder}
        """
        
        return jsonify({
            "success": True,
            "subject": subject,
            "html_body": html_body,
            "text_body": text_body,
            "recipients": recipient_emails,
            "bugs": bugs,
            "branch_name": branch_name,
            "run_folder": run_folder
        })
        
    except Exception as e:
        logger.error(f"Error in preview email: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route("/mcp/regression/run-report/send-email", methods=["POST"])
@jwt_required
def send_qi_bug_email():
    """Send email for Top QI Impacting Bugs"""
    try:
        req_data = request.json
        bugs = req_data.get("bugs", [])
        branch_name = req_data.get("branch_name", "Unknown Branch")
        run_folder = req_data.get("run_folder", "")
        recipients = req_data.get("recipients", [])
        
        if not bugs or len(bugs) == 0:
            return jsonify({"error": "Bug data is required"}), 400
        
        if not recipients or len(recipients) == 0:
            return jsonify({"error": "Recipients are required"}), 400
        
        # Create email subject
        subject = f"Top QI Impacting Bugs on {branch_name}"
        
        # Create email body with HTML table for all bugs
        bugs_table_rows = ""
        for idx, bug in enumerate(bugs, 1):
            bugs_table_rows += f"""
                <tr>
                    <td>{idx}</td>
                    <td>{bug.get('name', 'N/A')}</td>
                    <td>{bug.get('type', 'N/A')}</td>
                    <td>{bug.get('priority', 'N/A')}</td>
                    <td>{bug.get('summary', 'N/A')}</td>
                    <td>{bug.get('assignee', 'N/A')}</td>
                    <td>{bug.get('impacted_tcs_latest_run', 0)}</td>
                    <td>{bug.get('deferred', 'N/A')}</td>
                    <td>{bug.get('average_qi', 0):.2f}</td>
                    <td>{bug.get('overall_qi_impact', 0):.2f}</td>
                </tr>
            """
        
        html_body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
                .summary {{ margin: 20px 0; padding: 15px; background-color: #f8f9fa; border-left: 4px solid #3498db; }}
                table {{ border-collapse: collapse; width: 100%; margin: 20px 0; font-size: 12px; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background-color: #3498db; color: white; font-weight: bold; }}
                tr:nth-child(even) {{ background-color: #f2f2f2; }}
                tr:hover {{ background-color: #e8f4f8; }}
                .note {{ margin-top: 20px; padding: 15px; background-color: #fff3cd; border-left: 4px solid #ffc107; }}
            </style>
        </head>
        <body>
            <div class="summary">
                <h2>Top QI Impacting Bugs Notification</h2>
                <p>The following bugs have been identified as top QI impacting bugs on branch: <strong>{branch_name}</strong></p>
                <p><strong>Total Bugs:</strong> {len(bugs)}</p>
            </div>
            
            <table>
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Bug Name</th>
                        <th>Type</th>
                        <th>Priority</th>
                        <th>Summary</th>
                        <th>Assignee</th>
                        <th>Impacted TCs</th>
                        <th>Deferred</th>
                        <th>Average QI</th>
                        <th>Overall QI Impact</th>
                    </tr>
                </thead>
                <tbody>
                    {bugs_table_rows}
                </tbody>
            </table>
            
            <div class="note">
                <p><strong>Note:</strong> These bugs impact more than 4 test cases and have significant overall QI impact. Please review and take appropriate action.</p>
                <p><strong>Run Folder:</strong> {run_folder}</p>
            </div>
        </body>
        </html>
        """
        
        # Plain text version
        text_body = f"""
Top QI Impacting Bugs Notification

The following bugs have been identified as top QI impacting bugs on branch: {branch_name}

Total Bugs: {len(bugs)}

Bug Details:
"""
        for idx, bug in enumerate(bugs, 1):
            text_body += f"""
{idx}. {bug.get('name', 'N/A')}
   - Type: {bug.get('type', 'N/A')}
   - Priority: {bug.get('priority', 'N/A')}
   - Summary: {bug.get('summary', 'N/A')}
   - Assignee: {bug.get('assignee', 'N/A')}
   - Impacted Test Cases: {bug.get('impacted_tcs_latest_run', 0)}
   - Deferred: {bug.get('deferred', 'N/A')}
   - Average QI: {bug.get('average_qi', 0):.2f}
   - Overall QI Impact: {bug.get('overall_qi_impact', 0):.2f}
"""
        
        text_body += f"""

Note: These bugs impact more than 4 test cases and have significant overall QI impact. Please review and take appropriate action.

Run Folder: {run_folder}
        """
        
        # Create message
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = os.getenv('SMTP_FROM_EMAIL', 'regression-dashboard@nutanix.com')
        msg['To'] = ', '.join(recipients)
        
        # Add both plain text and HTML versions
        part1 = MIMEText(text_body, 'plain')
        part2 = MIMEText(html_body, 'html')
        
        msg.attach(part1)
        msg.attach(part2)
        
        # Send email using SMTP
        smtp_server = os.getenv('SMTP_SERVER', 'smtp.nutanix.com')
        smtp_port = int(os.getenv('SMTP_PORT', '587'))
        smtp_user = os.getenv('SMTP_USER', '')
        smtp_password = os.getenv('SMTP_PASSWORD', '')
        
        try:
            # For now, we'll just log the email instead of actually sending it
            # Uncomment the SMTP code below when SMTP credentials are configured
            logger.info(f"Email prepared for {', '.join(recipients)}:")
            logger.info(f"Subject: {subject}")
            logger.info(f"Number of bugs: {len(bugs)}")
            logger.info(f"Body length: {len(text_body)} chars")
            
            # Uncomment below to actually send email:
            # with smtplib.SMTP(smtp_server, smtp_port) as server:
            #     if smtp_user and smtp_password:
            #         server.starttls()
            #         server.login(smtp_user, smtp_password)
            #     server.send_message(msg)
            
            return jsonify({
                "success": True,
                "message": f"Email prepared for {len(recipients)} recipient(s)",
                "recipients": recipients,
                "note": "Email sending is currently logged. Configure SMTP settings to enable actual email sending."
            })
        except Exception as e:
            logger.error(f"Error sending email: {e}", exc_info=True)
            return jsonify({"error": f"Failed to send email: {str(e)}"}), 500
        
    except Exception as e:
        logger.error(f"Error in send email: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

# ======================================================
# Run Plan Endpoints
# ======================================================

# JITA Authentication
from base64 import b64decode

def safe_b64decode(s):
    """Safely decode base64 string, adding padding if needed"""
    # Add padding if needed (base64 strings must be multiple of 4)
    missing_padding = len(s) % 4
    if missing_padding:
        s += '=' * (4 - missing_padding)
    return b64decode(s).decode("utf-8")

# Service account credentials for batch operations (matching reference script)
JITA_SVC_USERNAME = safe_b64decode("c3ZjLmNkcC5yZWdyZXNzaW9u")
JITA_SVC_PASSWORD = safe_b64decode("Knh0WTFtNiYlVko0akZXZzJlZHY=")
JITA_SVC_AUTH = (JITA_SVC_USERNAME, JITA_SVC_PASSWORD)

# User credentials for triggering (matching reference script)
JITA_USERNAME = safe_b64decode("c3VkaGFyc2hhbi5tdXNhbGk=")
JITA_PASSWORD = safe_b64decode("V29ya291dEAy")
JITA_AUTH = (JITA_USERNAME, JITA_PASSWORD)

# Helper function to update tester_tags for job profiles
def update_job_profiles_tester_tags(job_profile_ids, tag_name, action="add"):
    """
    Update tester_tags for multiple job profiles
    action: "add" to append tag, "remove" to remove tag
    """
    updated_count = 0
    failed_updates = []
    
    def update_single_job_tags(job_id):
        try:
            # Fetch existing profile
            get_url = f"{JITA_BASE}/job_profiles/{job_id}"
            get_resp = requests.get(get_url, headers={"Content-Type": "application/json"}, auth=JITA_SVC_AUTH, verify=False, timeout=30)
            
            if get_resp.status_code != 200:
                return {"job_id": job_id, "success": False, "error": f"Failed to fetch: {get_resp.status_code}"}
            
            existing_profile = get_resp.json().get("data", {})
            if not existing_profile:
                return {"job_id": job_id, "success": False, "error": "Empty profile data"}
            
            updated_profile = existing_profile.copy()
            tester_tags = existing_profile.get("tester_tags", [])
            
            if not isinstance(tester_tags, list):
                tester_tags = []
            
            if action == "add":
                # Add tag if not already present
                if tag_name not in tester_tags:
                    tester_tags.append(tag_name)
                    updated_profile["tester_tags"] = tester_tags
                else:
                    return {"job_id": job_id, "success": True, "message": "Tag already exists"}
            elif action == "remove":
                # Remove tag if present
                if tag_name in tester_tags:
                    tester_tags.remove(tag_name)
                    updated_profile["tester_tags"] = tester_tags
                else:
                    return {"job_id": job_id, "success": True, "message": "Tag not found"}
            
            # Ensure JSON serializable
            serializable_payload = {}
            for k, v in updated_profile.items():
                if isinstance(v, (set, tuple)):
                    serializable_payload[k] = list(v)
                elif v is Ellipsis:
                    serializable_payload[k] = None
                else:
                    serializable_payload[k] = v
            
            # PUT update
            put_resp = requests.put(
                f"{JITA_BASE}/job_profiles/{job_id}",
                headers={"Content-Type": "application/json"},
                json=serializable_payload,
                auth=JITA_SVC_AUTH,
                verify=False,
                timeout=30
            )
            
            if put_resp.status_code == 200:
                resp_json = put_resp.json()
                if resp_json.get("success", True):
                    return {"job_id": job_id, "success": True}
                else:
                    return {"job_id": job_id, "success": False, "error": resp_json.get("message", "Update failed")}
            else:
                error_msg = put_resp.text[:200] if put_resp.text else f"HTTP {put_resp.status_code}"
                return {"job_id": job_id, "success": False, "error": error_msg}
        except Exception as e:
            logger.error(f"Exception updating tester_tags for {job_id}: {e}", exc_info=True)
            return {"job_id": job_id, "success": False, "error": str(e)}
    
    # Parallel execution
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(update_single_job_tags, jp_id) for jp_id in job_profile_ids]
        for future in as_completed(futures):
            result = future.result()
            if result["success"]:
                updated_count += 1
            else:
                failed_updates.append(result)
    
    return updated_count, failed_updates

@app.route("/mcp/regression/run-plan", methods=["GET"])
@jwt_required
def list_run_plans():
    """List all run plans"""
    try:
        data = load_run_plans()
        return jsonify({"run_plans": data.get("run_plans", [])})
    except Exception as e:
        logger.error(f"Error listing run plans: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/mcp/regression/run-plan", methods=["POST"])
@jwt_required
def create_run_plan():
    """Create a new run plan"""
    try:
        req_data = request.json
        data = load_run_plans()
        
        # Validate tag name uniqueness
        tag_name = req_data.get("tag_name")
        for rp in data.get("run_plans", []):
            if rp.get("tag_name") == tag_name:
                return jsonify({"error": f"Tag name '{tag_name}' already exists"}), 400
        
        # Validate and filter job profiles
        job_profiles = req_data.get("job_profiles", [])
        # Filter out empty strings and invalid IDs
        job_profiles = [jp_id for jp_id in job_profiles if jp_id and isinstance(jp_id, str) and jp_id.strip()]
        
        if not job_profiles:
            return jsonify({"error": "At least one valid job profile is required"}), 400
        
        # Generate tag_name automatically if not provided
        tag_name = req_data.get("tag_name")
        if not tag_name:
            # Extract branch from name (e.g., CDP_Regression_Upgrade_master -> master)
            name_parts = req_data.get("name", "").split("_")
            branch = name_parts[-1] if name_parts else "master"
            timestamp = int(time.time() * 1000)
            tag_name = f"{branch}_{timestamp}"
        
        # Create new run plan
        new_id = str(int(time.time() * 1000))
        new_run_plan = {
            "id": new_id,
            "name": req_data.get("name"),
            "job_profiles": job_profiles,
            "tag_name": tag_name,
            "schedule_date": req_data.get("schedule_date"),
            "created_at": datetime.now().isoformat(),
            "last_triggered": None
        }
        
        data["run_plans"].append(new_run_plan)
        save_run_plans(data)
        
        # Append tag_name to tester_tags for all job profiles
        if tag_name and job_profiles:
            logger.info(f"Updating tester_tags for {len(job_profiles)} job profile(s) with tag: {tag_name}")
            updated_count, failed = update_job_profiles_tester_tags(job_profiles, tag_name, action="add")
            logger.info(f"Updated tester_tags: {updated_count} succeeded, {len(failed)} failed")
        
        return jsonify({"success": True, "run_plan": new_run_plan}), 201
    except Exception as e:
        logger.error(f"Error creating run plan: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/mcp/regression/run-plan/<run_plan_id>", methods=["PUT"])
@jwt_required
def update_run_plan(run_plan_id):
    """Update an existing run plan"""
    try:
        req_data = request.json
        data = load_run_plans()
        
        # Find and update run plan
        for i, rp in enumerate(data.get("run_plans", [])):
            if rp.get("id") == run_plan_id:
                # Check if already triggered (restrict edits)
                if rp.get("last_triggered"):
                    # Only allow editing schedule_date, name, and tag_name
                    if "schedule_date" in req_data:
                        rp["schedule_date"] = req_data["schedule_date"]
                    if "name" in req_data:
                        rp["name"] = req_data["name"]
                    if "tag_name" in req_data:
                        # Validate uniqueness
                        tag_name = req_data["tag_name"]
                        for other_rp in data.get("run_plans", []):
                            if other_rp.get("id") != run_plan_id and other_rp.get("tag_name") == tag_name:
                                return jsonify({"error": f"Tag name '{tag_name}' already exists"}), 400
                        rp["tag_name"] = tag_name
                else:
                    # Full edit allowed before first trigger
                    rp["name"] = req_data.get("name", rp.get("name"))
                    
                    # Validate and filter job profiles if provided
                    if "job_profiles" in req_data:
                        new_job_profiles = req_data.get("job_profiles", [])
                        new_job_profiles = [jp_id for jp_id in new_job_profiles if jp_id and isinstance(jp_id, str) and jp_id.strip()]
                        if not new_job_profiles:
                            return jsonify({"error": "At least one valid job profile is required"}), 400
                        rp["job_profiles"] = new_job_profiles
                    
                    if "schedule_date" in req_data:
                        rp["schedule_date"] = req_data.get("schedule_date")
                
                save_run_plans(data)
                
                # Update tester_tags if job_profiles were updated
                # Tag name remains unchanged (auto-generated on create)
                tag_name = rp.get("tag_name")
                if "job_profiles" in req_data and tag_name:
                    job_profiles = rp.get("job_profiles", [])
                    job_profiles = [jp_id for jp_id in job_profiles if jp_id and isinstance(jp_id, str) and jp_id.strip()]
                    
                    if job_profiles:
                        logger.info(f"Ensuring tag '{tag_name}' exists in tester_tags for {len(job_profiles)} job profile(s)")
                        updated_count, failed = update_job_profiles_tester_tags(job_profiles, tag_name, action="add")
                        logger.info(f"Updated tester_tags: {updated_count} succeeded, {len(failed)} failed")
                
                return jsonify({"success": True, "run_plan": rp})
        
        return jsonify({"error": "Run plan not found"}), 404
    except Exception as e:
        logger.error(f"Error updating run plan: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/mcp/regression/run-plan/search-job-profiles", methods=["POST"])
@jwt_required
def search_job_profiles():
    """Search job profiles by ID or pattern"""
    try:
        req_data = request.json
        search_type = req_data.get("search_type")  # 'id' or 'pattern'
        search_value = req_data.get("search_value")
        
        if not search_value:
            return jsonify({"error": "Search value is required"}), 400
        
        # Build raw_query based on search type
        if search_type == "id":
            # Comma-separated IDs
            ids = [id.strip() for id in search_value.split(",")]
            raw_query = {
                "_id": {"$in": [{"$oid": id} for id in ids]}
            }
        else:  # pattern
            raw_query = {
                "name": {
                    "$regex": f"^{search_value}",
                    "$options": "i"
                }
            }
        
        # Call JITA API
        # Note: JITA API expects raw_query as a URL-encoded JSON string in query params
        from urllib.parse import quote
        raw_query_str = quote(json.dumps(raw_query))
        params = {
            "raw_query": raw_query_str,
            "limit": 100
        }
        
        response = requests.get(
            f"{JITA_BASE}/job_profiles",
            params=params,
            auth=JITA_SVC_AUTH,
            verify=False,
            timeout=30
        )
        
        if response.status_code != 200:
            return jsonify({"error": f"JITA API error: {response.status_code}"}), 500
        
        result = response.json()
        job_profiles = result.get("data", [])
        
        return jsonify({"success": True, "job_profiles": job_profiles})
    except Exception as e:
        logger.error(f"Error searching job profiles: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/mcp/regression/run-plan/<run_plan_id>/trigger", methods=["POST"])
@jwt_required
def trigger_run_plan(run_plan_id):
    """Trigger a run plan now"""
    try:
        logger.info(f"[START] Trigger Run Plan | run_plan_id={run_plan_id}")
        data = load_run_plans()
        
        # Find run plan
        run_plan = None
        for rp in data.get("run_plans", []):
            if rp.get("id") == run_plan_id:
                run_plan = rp
                break
        
        if not run_plan:
            logger.error(f"Run plan not found: {run_plan_id}")
            return jsonify({"error": "Run plan not found"}), 404
        
        logger.info(f"Found run plan: {run_plan.get('name')} (ID: {run_plan_id})")
        
        job_profile_ids = run_plan.get("job_profiles", [])
        logger.info(f"Original job_profiles from run plan: {job_profile_ids}")
        
        # Filter out empty strings and invalid IDs
        original_count = len(job_profile_ids)
        job_profile_ids = [jp_id for jp_id in job_profile_ids if jp_id and isinstance(jp_id, str) and jp_id.strip()]
        filtered_count = len(job_profile_ids)
        
        logger.info(f"Filtered job profiles: {original_count} -> {filtered_count} valid IDs")
        
        if not job_profile_ids:
            error_msg = f"Run plan '{run_plan.get('name')}' has no valid job profiles. Original list: {run_plan.get('job_profiles')}. Please add at least one valid job profile to the run plan."
            logger.error(error_msg)
            return jsonify({
                "error": error_msg,
                "run_plan_name": run_plan.get("name"),
                "original_job_profiles": run_plan.get("job_profiles"),
                "filtered_count": filtered_count
            }), 400
        
        logger.info(f"Triggering {filtered_count} job profile(s): {job_profile_ids}")
        
        # Trigger job profiles in parallel
        task_ids = []
        failed_jobs = []
        
        def trigger_single_job(job_id):
            try:
                if not job_id or not isinstance(job_id, str) or not job_id.strip():
                    return {"job_id": job_id, "success": False, "error": "Invalid job profile ID"}
                
                url = f"{JITA_BASE}/job_profiles/{job_id}/trigger"
                payload = {}
                headers = {"Content-Type": "application/json"}
                
                # Update NOS commit if provided (use service account for updates)
                if run_plan.get("nos_commit"):
                    # Fetch existing profile first
                    get_url = f"{JITA_BASE}/job_profiles/{job_id}"
                    get_resp = requests.get(get_url, headers=headers, auth=JITA_SVC_AUTH, verify=False, timeout=30)
                    if get_resp.status_code == 200:
                        existing_profile = get_resp.json().get("data", {})
                        if isinstance(existing_profile, dict):
                            build_selection = existing_profile.get("build_selection", {})
                            build_selection["commit_id"] = run_plan.get("nos_commit")
                            build_selection["by_commit_id"] = True
                            
                            # Update profile
                            update_payload = existing_profile.copy()
                            update_payload["build_selection"] = build_selection
                            
                            # Ensure JSON serializable
                            serializable_payload = {}
                            for k, v in update_payload.items():
                                if isinstance(v, (set, tuple)):
                                    serializable_payload[k] = list(v)
                                elif v is Ellipsis:
                                    serializable_payload[k] = None
                                else:
                                    serializable_payload[k] = v
                            
                            update_resp = requests.put(
                                f"{JITA_BASE}/job_profiles/{job_id}",
                                headers=headers,
                                json=serializable_payload,
                                auth=JITA_SVC_AUTH,
                                verify=False,
                                timeout=30
                            )
                            if update_resp.status_code != 200:
                                logger.warning(f"Failed to update commit for {job_id}: {update_resp.text[:200]}")
                
                # Trigger using user credentials (matching reference script)
                logger.info(f"Triggering Job Profile ID: {job_id}")
                resp = requests.post(
                    url,
                    headers=headers,
                    auth=JITA_AUTH,
                    json=payload,
                    verify=False,
                    timeout=60
                )
                
                if resp.status_code == 200:
                    try:
                        res_data = resp.json()
                        if res_data.get("success") and "task_ids" in res_data:
                            # Extract task IDs (matching reference script pattern)
                            ids = [item["$oid"] if isinstance(item, dict) and "$oid" in item else item for item in res_data["task_ids"]]
                            logger.info(f"✅ Triggered: {job_id} → Task ID(s): {ids}")
                            return {
                                "job_id": job_id,
                                "task_ids": ids,
                                "success": True
                            }
                        else:
                            error_msg = res_data.get("message", "Trigger failed") if isinstance(res_data, dict) else str(res_data)
                            logger.error(f"❌ Trigger failed for {job_id}: {error_msg}")
                            return {"job_id": job_id, "success": False, "error": error_msg}
                    except Exception as e:
                        logger.error(f"❌ Response parse error for {job_id}: {e}")
                        return {"job_id": job_id, "success": False, "error": f"Response parse error: {str(e)}"}
                else:
                    error_msg = resp.text[:200] if resp.text else f"HTTP {resp.status_code}"
                    logger.error(f"❌ HTTP {resp.status_code} → Failed to trigger job profile {job_id}: {error_msg}")
                    return {"job_id": job_id, "success": False, "error": f"HTTP {resp.status_code}: {error_msg}"}
            except Exception as e:
                logger.error(f"Exception in trigger_single_job for {job_id}: {e}", exc_info=True)
                return {"job_id": job_id, "success": False, "error": str(e)}
        
        # Parallel execution
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(trigger_single_job, jp_id) for jp_id in job_profile_ids]
            for future in as_completed(futures):
                result = future.result()
                if result["success"]:
                    task_ids.extend(result["task_ids"])
                else:
                    failed_jobs.append(result)
        
        # Update run plan
        for rp in data.get("run_plans", []):
            if rp.get("id") == run_plan_id:
                rp["last_triggered"] = datetime.now().isoformat()
                break
        
        # Save history
        history_entry = {
            "id": str(int(time.time() * 1000)),
            "run_plan_id": run_plan_id,
            "triggered_at": datetime.now().isoformat(),
            "task_ids": task_ids,
            "failed_jobs": failed_jobs,
            "status": "success" if not failed_jobs else "partial"
        }
        
        if "history" not in data:
            data["history"] = []
        data["history"].append(history_entry)
        
        save_run_plans(data)
        
        logger.info(f"[END] Trigger Run Plan | run_plan_id={run_plan_id} | task_ids={len(task_ids)} | failed={len(failed_jobs)}")
        
        return jsonify({
            "success": True,
            "task_ids": task_ids,
            "failed_jobs": failed_jobs,
            "total_triggered": len(task_ids),
            "total_failed": len(failed_jobs)
        })
    except Exception as e:
        logger.error(f"Error triggering run plan {run_plan_id}: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route("/mcp/regression/run-plan/<run_plan_id>/batch-update", methods=["POST"])
@jwt_required
def batch_update_job_profiles(run_plan_id):
    """Batch update job profiles in a run plan"""
    try:
        req_data = request.json
        data = load_run_plans()
        
        # Find run plan
        run_plan = None
        for rp in data.get("run_plans", []):
            if rp.get("id") == run_plan_id:
                run_plan = rp
                break
        
        if not run_plan:
            return jsonify({"error": "Run plan not found"}), 404
        
        job_profile_ids = run_plan.get("job_profiles", [])
        # Filter out empty strings and invalid IDs
        job_profile_ids = [jp_id for jp_id in job_profile_ids if jp_id and isinstance(jp_id, str) and jp_id.strip()]
        
        if not job_profile_ids:
            return jsonify({"error": "No valid job profiles in run plan"}), 400
        
        # Handle new components array structure or legacy single component structure
        components = req_data.get("components", [])
        if not components:
            # Legacy support: single component update
            component = req_data.get("component")
            if component:
                components = [{
                    "component": component,
                    "branch": req_data.get("branch", ""),
                    "update_type": req_data.get("update_type", ""),
                    "build_type": req_data.get("build_type", ""),
                    "tag": req_data.get("tag", ""),
                    "commit_id": req_data.get("commit_id", ""),
                    "gbn": req_data.get("gbn", "")
                }]
        
        updated_count = 0
        failed_updates = []
        
        def update_single_job(job_id):
            try:
                # Fetch existing profile (use service account for fetching)
                get_url = f"{JITA_BASE}/job_profiles/{job_id}"
                get_resp = requests.get(get_url, headers={"Content-Type": "application/json"}, auth=JITA_SVC_AUTH, verify=False, timeout=30)
                
                if get_resp.status_code != 200:
                    return {"job_id": job_id, "success": False, "error": f"Failed to fetch: {get_resp.status_code}"}
                
                existing_profile = get_resp.json().get("data", {})
                if not existing_profile:
                    return {"job_id": job_id, "success": False, "error": "Empty profile data"}
                
                # Start with full existing profile (like reference script)
                updated_profile = existing_profile.copy()
                
                # Process each component update
                for comp_data in components:
                    component = comp_data.get("component")
                    branch = comp_data.get("branch", "")
                    update_type = comp_data.get("update_type", "")
                    build_type = comp_data.get("build_type", "")
                    tag = comp_data.get("tag", "")
                    commit_id = comp_data.get("commit_id", "")
                    gbn = comp_data.get("gbn", "")
                    
                    if component == "NOS_CLUSTER":
                        # Update git.branch (always update if branch is provided, or preserve existing)
                        git = existing_profile.get("git", {})
                        if not git:
                            git = {"repo": "main"}
                        if branch:
                            git["branch"] = branch
                        git["repo"] = "main"  # Always ensure repo is "main"
                        updated_profile["git"] = git
                        
                        # Update build_selection (always update if update_type or build_type is provided)
                        if update_type or build_type:
                            build_selection = existing_profile.get("build_selection", {})
                            if not build_selection:
                                build_selection = {}
                            
                            # Always set build_type if provided
                            if build_type:
                                build_selection["build_type"] = build_type
                            
                            if update_type == "tag":
                                # By tag - always set these flags (reference shows they're always set)
                                build_selection["commit_must_be_newer"] = False
                                build_selection["by_latest_smoked"] = True
                                # Remove commit-related fields if they exist
                                build_selection.pop("by_commit_id", None)
                                build_selection.pop("commit_id", None)
                                build_selection.pop("gbn", None)
                            elif update_type == "commit":
                                # By commit - always set by_commit_id flag
                                build_selection["by_commit_id"] = True
                                # Remove tag-related fields if they exist
                                build_selection.pop("commit_must_be_newer", None)
                                build_selection.pop("by_latest_smoked", None)
                                
                                # Set commit_id and gbn if provided
                                if commit_id:
                                    build_selection["commit_id"] = commit_id
                                if gbn:
                                    # GBN should be an integer
                                    try:
                                        build_selection["gbn"] = int(gbn) if isinstance(gbn, str) else gbn
                                    except (ValueError, TypeError):
                                        build_selection["gbn"] = gbn
                            
                            updated_profile["build_selection"] = build_selection
                    
                    elif component == "PRISM_CENTRAL":
                        # Update PRISM_CENTRAL (following reference script pattern)
                        # Initialize resource_manager_json structure
                        resource_manager_json = existing_profile.get("resource_manager_json", {})
                        if not resource_manager_json:
                            resource_manager_json = {}
                        
                        PRISM_CENTRAL = resource_manager_json.get("PRISM_CENTRAL", {})
                        if not PRISM_CENTRAL:
                            PRISM_CENTRAL = {}
                        
                        PC_BUILD = PRISM_CENTRAL.get("build", {})
                        if not PC_BUILD:
                            PC_BUILD = {}
                        
                        # Update branch and component (if branch is provided)
                        if branch:
                            PC_BUILD["branch"] = branch
                        PC_BUILD["component"] = "main"  # Always set to "main"
                        
                        # Update build selection based on update_type
                        if update_type == "tag":
                            if tag:
                                PC_BUILD["build_selection_option"] = tag
                        elif update_type == "commit":
                            # For PRISM_CENTRAL, by commit uses build_selection_option for commit_id
                            if commit_id:
                                PC_BUILD["build_selection_option"] = commit_id
                            if gbn:
                                # GBN should be an integer
                                try:
                                    PC_BUILD["gbn"] = int(gbn) if isinstance(gbn, str) else gbn
                                except (ValueError, TypeError):
                                    PC_BUILD["gbn"] = gbn
                        
                        # Update build_selection_build_type if build_type is provided
                        if build_type:
                            PC_BUILD["build_selection_build_type"] = build_type
                        
                        # Always update PRISM_CENTRAL structure if component is selected
                        # This ensures the structure is properly initialized even if fields are optional
                        PRISM_CENTRAL["build"] = PC_BUILD
                        resource_manager_json["PRISM_CENTRAL"] = PRISM_CENTRAL
                        updated_profile["resource_manager_json"] = resource_manager_json
                
                # Update test framework branch if provided
                if req_data.get("nutest_branch"):
                    updated_profile["nutest-py3-tests_branch"] = req_data.get("nutest_branch")
                
                # Update test framework metadata (patch URLs and branch)
                if req_data.get("nutest_branch") or req_data.get("patch_url") or req_data.get("framework_patch_url"):
                    test_framework_metadata = existing_profile.get("test_framework_metadata", {})
                    if not test_framework_metadata:
                        test_framework_metadata = {"framework": {}, "test": {}}
                    
                    # Get existing metadata or create new (preserve existing values)
                    test_metadata = test_framework_metadata.get("test", {})
                    if not test_metadata:
                        test_metadata = {}
                    else:
                        # Preserve existing test metadata (branch, commit, etc.)
                        test_metadata = test_metadata.copy()
                    
                    framework_metadata = test_framework_metadata.get("framework", {})
                    if not framework_metadata:
                        framework_metadata = {}
                    else:
                        # Preserve existing framework metadata (branch, commit, etc.)
                        framework_metadata = framework_metadata.copy()
                    
                    # Update branch in both test and framework if nutest_branch is provided
                    if req_data.get("nutest_branch"):
                        test_metadata["branch"] = req_data.get("nutest_branch")
                        framework_metadata["branch"] = req_data.get("nutest_branch")
                    else:
                        # Preserve existing branch if not updating
                        if "branch" not in framework_metadata:
                            # Get from existing if available
                            existing_framework = test_framework_metadata.get("framework", {})
                            if existing_framework and "branch" in existing_framework:
                                framework_metadata["branch"] = existing_framework["branch"]
                    
                    # Update test patch URL if provided
                    if req_data.get("patch_url"):
                        test_metadata["patch_url"] = req_data.get("patch_url")
                    
                    # Update framework patch URL if provided
                    framework_patch_url = req_data.get("framework_patch_url")
                    if framework_patch_url:
                        framework_metadata["patch_url"] = framework_patch_url
                        logger.info(f"Updating framework patch_url to: {framework_patch_url}")
                    
                    # Ensure commit is preserved (set to null if not present, or preserve existing)
                    if "commit" not in framework_metadata:
                        existing_framework = test_framework_metadata.get("framework", {})
                        if existing_framework and "commit" in existing_framework:
                            framework_metadata["commit"] = existing_framework["commit"]
                        else:
                            framework_metadata["commit"] = None
                    if "commit" not in test_metadata:
                        existing_test = test_framework_metadata.get("test", {})
                        if existing_test and "commit" in existing_test:
                            test_metadata["commit"] = existing_test["commit"]
                        else:
                            test_metadata["commit"] = None
                    
                    test_framework_metadata["test"] = test_metadata
                    test_framework_metadata["framework"] = framework_metadata
                    updated_profile["test_framework_metadata"] = test_framework_metadata
                    logger.info(f"Updated test_framework_metadata: framework.patch_url={framework_metadata.get('patch_url')}, framework.branch={framework_metadata.get('branch')}")
                
                # Update tester_tags if provided (optional batch update)
                if req_data.get("tester_tags_action"):  # "add" or "remove"
                    tester_tags_action = req_data.get("tester_tags_action")
                    tester_tag_value = req_data.get("tester_tag_value", "")
                    
                    if tester_tag_value:
                        tester_tags = existing_profile.get("tester_tags", [])
                        if not isinstance(tester_tags, list):
                            tester_tags = []
                        
                        if tester_tags_action == "add":
                            # Add tag if not already present
                            if tester_tag_value not in tester_tags:
                                tester_tags.append(tester_tag_value)
                                updated_profile["tester_tags"] = tester_tags
                        elif tester_tags_action == "remove":
                            # Remove tag if present
                            if tester_tag_value in tester_tags:
                                tester_tags.remove(tester_tag_value)
                                updated_profile["tester_tags"] = tester_tags
                
                # Ensure JSON serializable (following reference script pattern)
                serializable_payload = {}
                for k, v in updated_profile.items():
                    if isinstance(v, (set, tuple)):
                        serializable_payload[k] = list(v)
                    elif v is Ellipsis:
                        serializable_payload[k] = None
                    else:
                        serializable_payload[k] = v
                
                # PUT update (use service account for batch updates)
                put_resp = requests.put(
                    f"{JITA_BASE}/job_profiles/{job_id}",
                    headers={"Content-Type": "application/json"},
                    json=serializable_payload,
                    auth=JITA_SVC_AUTH,
                    verify=False,
                    timeout=30
                )
                
                if put_resp.status_code == 200:
                    resp_json = put_resp.json()
                    if resp_json.get("success", True):
                        logger.info(f"Successfully updated job profile {job_id}")
                        return {"job_id": job_id, "success": True}
                    else:
                        error_msg = resp_json.get("message", "Update failed")
                        logger.error(f"Failed to update job profile {job_id}: {error_msg}")
                        return {"job_id": job_id, "success": False, "error": error_msg}
                else:
                    error_msg = put_resp.text[:500] if put_resp.text else f"HTTP {put_resp.status_code}"
                    logger.error(f"Failed to update job profile {job_id}: {error_msg}")
                    return {"job_id": job_id, "success": False, "error": error_msg}
            except Exception as e:
                logger.error(f"Exception updating job profile {job_id}: {e}", exc_info=True)
                return {"job_id": job_id, "success": False, "error": str(e)}
        
        # Parallel execution
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(update_single_job, jp_id) for jp_id in job_profile_ids]
            for future in as_completed(futures):
                result = future.result()
                if result["success"]:
                    updated_count += 1
                else:
                    failed_updates.append(result)
        
        return jsonify({
            "success": True,
            "updated_count": updated_count,
            "failed_updates": failed_updates
        })
    except Exception as e:
        logger.error(f"Error batch updating job profiles: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/mcp/regression/run-plan/<run_plan_id>/history", methods=["GET"])
@jwt_required
def get_run_plan_history(run_plan_id):
    """Get history for a run plan"""
    try:
        data = load_run_plans()
        history = [
            entry for entry in data.get("history", [])
            if entry.get("run_plan_id") == run_plan_id
        ]
        # Sort by triggered_at descending
        history.sort(key=lambda x: x.get("triggered_at", ""), reverse=True)
        return jsonify({"history": history})
    except Exception as e:
        logger.error(f"Error fetching history: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/mcp/regression/run-plan/history/<history_id>/retry", methods=["POST"])
@jwt_required
def retry_history_entry(history_id):
    """Retry a history entry trigger"""
    try:
        data = load_run_plans()
        
        # Find history entry
        history_entry = None
        for entry in data.get("history", []):
            if entry.get("id") == history_id:
                history_entry = entry
                break
        
        if not history_entry:
            return jsonify({"error": "History entry not found"}), 404
        
        # Trigger the run plan again
        run_plan_id = history_entry.get("run_plan_id")
        return trigger_run_plan(run_plan_id)
    except Exception as e:
        logger.error(f"Error retrying history entry: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/mcp/regression/run-plan/history/<history_id>", methods=["DELETE"])
@jwt_required
def delete_history_entry(history_id):
    """Delete a history entry"""
    try:
        data = load_run_plans()
        
        # Remove history entry
        data["history"] = [
            entry for entry in data.get("history", [])
            if entry.get("id") != history_id
        ]
        
        save_run_plans(data)
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error deleting history entry: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/mcp/regression/run-plan/<run_plan_id>/clone", methods=["POST"])
@jwt_required
def clone_run_plan(run_plan_id):
    """Clone a run plan with a new unique tag_name"""
    try:
        data = load_run_plans()
        
        # Find run plan to clone
        source_run_plan = None
        for rp in data.get("run_plans", []):
            if rp.get("id") == run_plan_id:
                source_run_plan = rp
                break
        
        if not source_run_plan:
            return jsonify({"error": "Run plan not found"}), 404
        
        # Generate new unique tag_name
        # Extract branch from name (e.g., CDP_Regression_Upgrade_master -> master)
        name_parts = source_run_plan.get("name", "").split("_")
        branch = name_parts[-1] if name_parts else "master"
        timestamp = int(time.time() * 1000)
        new_tag_name = f"{branch}_{timestamp}"
        
        # Check for uniqueness (should be unique due to timestamp, but double-check)
        for rp in data.get("run_plans", []):
            if rp.get("tag_name") == new_tag_name:
                # If somehow duplicate, add random suffix
                new_tag_name = f"{branch}_{timestamp}_{random.randint(1000, 9999)}"
                break
        
        # Create cloned run plan
        new_id = str(int(time.time() * 1000))
        original_name = source_run_plan.get("name", "")
        cloned_name = f"{original_name}_clone" if original_name else "cloned_run_plan"
        
        cloned_run_plan = {
            "id": new_id,
            "name": cloned_name,
            "job_profiles": source_run_plan.get("job_profiles", []).copy(),
            "tag_name": new_tag_name,
            "schedule_date": source_run_plan.get("schedule_date"),
            "created_at": datetime.now().isoformat(),
            "last_triggered": None  # Reset trigger status
        }
        
        data["run_plans"].append(cloned_run_plan)
        save_run_plans(data)
        
        # Append new tag_name to tester_tags for all job profiles
        job_profiles = cloned_run_plan.get("job_profiles", [])
        job_profiles = [jp_id for jp_id in job_profiles if jp_id and isinstance(jp_id, str) and jp_id.strip()]
        
        if new_tag_name and job_profiles:
            logger.info(f"Updating tester_tags for {len(job_profiles)} job profile(s) with new tag: {new_tag_name}")
            updated_count, failed = update_job_profiles_tester_tags(job_profiles, new_tag_name, action="add")
            logger.info(f"Updated tester_tags: {updated_count} succeeded, {len(failed)} failed")
        
        return jsonify({
            "success": True,
            "run_plan": cloned_run_plan,
            "message": f"Run plan cloned successfully with new tag: {new_tag_name}"
        })
        
    except Exception as e:
        logger.error(f"Error cloning run plan: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route("/mcp/regression/run-plan/<run_plan_id>", methods=["DELETE"])
@jwt_required
def delete_run_plan(run_plan_id):
    """Delete a run plan and all its associated history entries"""
    try:
        data = load_run_plans()
        
        # Find run plan
        run_plan = None
        for rp in data.get("run_plans", []):
            if rp.get("id") == run_plan_id:
                run_plan = rp
                break
        
        if not run_plan:
            return jsonify({"error": "Run plan not found"}), 404
        
        # Remove run plan from list
        data["run_plans"] = [
            rp for rp in data.get("run_plans", [])
            if rp.get("id") != run_plan_id
        ]
        
        # Remove all history entries associated with this run plan
        data["history"] = [
            entry for entry in data.get("history", [])
            if entry.get("run_plan_id") != run_plan_id
        ]
        
        save_run_plans(data)
        logger.info(f"Deleted run plan: {run_plan.get('name')} (ID: {run_plan_id})")
        
        return jsonify({"success": True, "message": f"Run plan '{run_plan.get('name')}' deleted successfully"})
    except Exception as e:
        logger.error(f"Error deleting run plan: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route("/mcp/regression/run-plan/<run_plan_id>/delete-tag", methods=["POST"])
@jwt_required
def delete_tag_from_job_profiles(run_plan_id):
    """Delete a tag from tester_tags of all job profiles in a run plan"""
    try:
        req_data = request.json
        tag_name = req_data.get("tag_name")
        
        if not tag_name:
            return jsonify({"error": "Tag name is required"}), 400
        
        data = load_run_plans()
        
        # Find run plan
        run_plan = None
        for rp in data.get("run_plans", []):
            if rp.get("id") == run_plan_id:
                run_plan = rp
                break
        
        if not run_plan:
            return jsonify({"error": "Run plan not found"}), 404
        
        job_profile_ids = run_plan.get("job_profiles", [])
        # Filter out empty strings and invalid IDs
        job_profile_ids = [jp_id for jp_id in job_profile_ids if jp_id and isinstance(jp_id, str) and jp_id.strip()]
        
        if not job_profile_ids:
            return jsonify({"error": "No valid job profiles in run plan"}), 400
        
        logger.info(f"Removing tag '{tag_name}' from tester_tags for {len(job_profile_ids)} job profile(s)")
        updated_count, failed = update_job_profiles_tester_tags(job_profile_ids, tag_name, action="remove")
        
        return jsonify({
            "success": True,
            "updated_count": updated_count,
            "failed_updates": failed,
            "tag_name": tag_name
        })
    except Exception as e:
        logger.error(f"Error deleting tag: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route("/mcp/regression/run-plan/tags", methods=["GET"])
@jwt_required
def get_available_tags():
    """Get list of available tags from JITA"""
    try:
        # Fetch recent tasks to get unique tags
        params = {
            "limit": 1000,
            "only": "tester_tags"
        }
        
        response = session.get(
            f"{JITA_BASE}/tasks",
            params=params,
            auth=JITA_AUTH,
            timeout=30
        )
        
        if response.status_code != 200:
            return jsonify({"error": f"JITA API error: {response.status_code}"}), 500
        
        result = response.json()
        tasks = result.get("data", [])
        
        # Extract unique tags
        tags_set = set()
        for task in tasks:
            task_tags = task.get("tester_tags", [])
            tags_set.update(task_tags)
        
        tags_list = sorted(list(tags_set))
        return jsonify({"tags": tags_list})
    except Exception as e:
        logger.error(f"Error fetching tags: {e}")
        return jsonify({"error": str(e)}), 500

# ======================================================
# Triage Genie Endpoints
# ======================================================
@app.route("/mcp/regression/triage-genie/jobs", methods=["GET"])
@jwt_required
def list_triage_genie_jobs():
    """List all Triage Genie jobs - primarily from JSON file"""
    try:
        # Load stored jobs from JSON file (primary source)
        stored_data = load_triage_genie_jobs()
        stored_jobs = stored_data.get("jobs", [])
        
        # Optionally fetch from API to update status for existing jobs
        # But prioritize stored jobs
        try:
            page = request.args.get("page", "1")
            per_page = request.args.get("per_page", "10")
            run_status = request.args.get("run_status", "")
            show_all = request.args.get("show_all", "true")
            name_search = request.args.get("name_search", "")
            
            timestamp = int(time.time() * 1000)
            url = f"{TRIAGE_GENIE_BASE}/jobs?page={page}&per_page={per_page}&run_status={run_status}&show_all={show_all}&name_search={name_search}&_={timestamp}"
            
            triage_token = os.getenv("TRIAGE_GENIE_TOKEN", "TOKEN")
            headers = {
                "Authorization": f"Bearer {triage_token}",
                "Content-Type": "application/json"
            }
            
            response = requests.get(url, headers=headers, verify=False, timeout=30)
            
            if response.status_code == 200:
                api_data = response.json()
                api_jobs = api_data.get("data", [])
                # Create a map of API jobs by ID for quick lookup
                api_jobs_map = {job.get("id"): job for job in api_jobs if job.get("id")}
                
                # Update stored jobs with latest status from API if available
                for stored_job in stored_jobs:
                    job_id = stored_job.get("id")
                    if job_id in api_jobs_map:
                        api_job = api_jobs_map[job_id]
                        # Update status fields but keep our stored data (name, jita_task_ids, etc.)
                        stored_job["run_status"] = api_job.get("run_status")
                        stored_job["triage_status"] = api_job.get("triage_status")
                        stored_job["last_check_time"] = api_job.get("last_check_time")
                        stored_job["last_check_status"] = api_job.get("last_check_status")
                        stored_job["last_check_triage_status"] = api_job.get("last_check_triage_status")
                        stored_job["last_check_review_status"] = api_job.get("last_check_review_status")
        except Exception as api_error:
            logger.warning(f"Failed to fetch from Triage Genie API: {api_error}, using stored jobs only")
        
        # Sort by ID descending (newest first)
        stored_jobs.sort(key=lambda x: x.get("id", 0), reverse=True)
        
        return jsonify({
            "success": True,
            "jobs": stored_jobs,
            "total": len(stored_jobs)
        })
            
    except Exception as e:
        logger.error(f"Error listing Triage Genie jobs: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route("/mcp/regression/triage-genie/jobs", methods=["POST"])
@jwt_required
def create_triage_genie_job():
    """Create a new Triage Genie job"""
    try:
        req_data = request.json
        name = req_data.get("name")
        jita_task_ids = req_data.get("jita_task_ids")  # Comma-separated string
        skip_review = req_data.get("skip_review", False)
        created_by = req_data.get("created_by", "")
        
        if not name:
            return jsonify({"error": "Name is required"}), 400
        
        if not jita_task_ids:
            return jsonify({"error": "JITA task IDs are required"}), 400
        
        # Build payload
        payload = {
            "name": name,
            "jita_task_ids": jita_task_ids,
            "skip_review": skip_review,
            "created_by": created_by
        }
        
        # Get authorization token from environment or use default "TOKEN"
        # The API accepts "Bearer TOKEN" as a valid authentication header
        triage_token = os.getenv("TRIAGE_GENIE_TOKEN", "TOKEN")
        headers = {
            "Authorization": f"Bearer {triage_token}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(
            f"{TRIAGE_GENIE_BASE}/jobs",
            headers=headers,
            json=payload,
            verify=False,
            timeout=30
        )
        
        if response.status_code in [200, 201]:
            data = response.json()
            # Handle different response structures
            if isinstance(data, dict):
                # Check if job is nested in response
                job_data = data.get("data") or data.get("job") or data
            else:
                job_data = {}
            
            # Always set name from request (ensure it's always present in stored data)
            # The name from the form should always be saved, even if API returns a different one
            job_data["name"] = name
            
            # Always set created_by from request
            if created_by:
                job_data["created_by"] = created_by
            
            # Always set jita_task_ids from request
            job_data["jita_task_ids"] = jita_task_ids
            
            # Convert jita_task_ids string to list and ensure jita_task_id_list is always set
            # Use API response if available, otherwise create from request
            if "jita_task_id_list" in job_data and job_data.get("jita_task_id_list"):
                # API already provided the list, use it
                pass
            else:
                # Create list from request jita_task_ids
                if isinstance(jita_task_ids, str):
                    job_data["jita_task_id_list"] = [tid.strip() for tid in jita_task_ids.split(",") if tid.strip()]
                elif isinstance(jita_task_ids, list):
                    job_data["jita_task_id_list"] = jita_task_ids
                else:
                    job_data["jita_task_id_list"] = []
            
            # Add created_at timestamp if not present
            if "create_time" not in job_data:
                job_data["create_time"] = datetime.now().isoformat()
            
            # Ensure skip_review is set
            job_data["skip_review"] = skip_review
            
            # Store job in JSON file
            stored_data = load_triage_genie_jobs()
            stored_jobs = stored_data.get("jobs", [])
            
            # Check if job already exists (by ID or name)
            job_id = job_data.get("id")
            job_name = job_data.get("name")
            
            # Remove existing job with same ID or name
            stored_jobs = [j for j in stored_jobs if j.get("id") != job_id and j.get("name") != job_name]
            
            # Add new job
            stored_jobs.append(job_data)
            stored_data["jobs"] = stored_jobs
            save_triage_genie_jobs(stored_data)
            
            logger.info(f"Triage Genie job created and stored: ID={job_id}, Name={job_name}")
            
            return jsonify({
                "success": True,
                "job": job_data
            })
        else:
            logger.error(f"Triage Genie API error: {response.status_code} - {response.text}")
            return jsonify({"error": f"Failed to create job: {response.status_code} - {response.text}"}), response.status_code
            
    except Exception as e:
        logger.error(f"Error creating Triage Genie job: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

# ======================================================
# Failed Testcase Analysis Endpoint
# ======================================================

# Constants for Jira and Glean APIs
JIRA_BASE = "https://jira.nutanix.com/rest/api/2"
GLEAN_BASE = "https://nutanix-be.glean.com/api/v1"

def get_jira_headers():
    """Get Jira API headers with authentication"""
    jira_token = os.getenv("JIRA_TOKEN", "")
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {jira_token}" if jira_token else ""
    }

def get_glean_headers():
    """Get Glean API headers with authentication"""
    glean_token = os.getenv("GLEAN_TOKEN", "")
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {glean_token}" if glean_token else ""
    }

def fetch_jira_ticket(ticket_id):
    """Fetch Jira ticket details"""
    try:
        if not os.getenv("JIRA_TOKEN"):
            logger.warning("JIRA_TOKEN not set, skipping Jira API call")
            return None
        
        headers = get_jira_headers()
        resp = session.get(
            f"{JIRA_BASE}/issue/{ticket_id}",
            headers=headers,
            timeout=30
        )
        if resp.status_code == 200:
            return resp.json()
        else:
            logger.warning(f"Failed to fetch Jira ticket {ticket_id}: {resp.status_code}")
            return None
    except Exception as e:
        logger.warning(f"Error fetching Jira ticket {ticket_id}: {e}")
        return None

def search_glean(query_text):
    """Search Glean for similar issues"""
    try:
        if not os.getenv("GLEAN_TOKEN"):
            logger.warning("GLEAN_TOKEN not set, skipping Glean API call")
            return None
        
        headers = get_glean_headers()
        payload = {
            "query": query_text,
            "max_results": 5
        }
        resp = session.post(
            f"{GLEAN_BASE}/search",
            headers=headers,
            json=payload,
            timeout=30
        )
        if resp.status_code == 200:
            return resp.json()
        else:
            logger.warning(f"Failed to search Glean: {resp.status_code}")
            return None
    except Exception as e:
        logger.warning(f"Error searching Glean: {e}")
        return None

def determine_failure_stage(test_result):
    """Determine failure stage from test result"""
    # Try to extract from exception_summary or exception
    exception_summary = test_result.get("exception_summary", "").lower()
    exception = test_result.get("exception", "").lower()
    combined = f"{exception_summary} {exception}"
    
    if any(keyword in combined for keyword in ["setup", "set up", "before", "precondition"]):
        return "Test Setup"
    elif any(keyword in combined for keyword in ["teardown", "tear down", "after", "cleanup"]):
        return "Teardown"
    elif any(keyword in combined for keyword in ["infra", "infrastructure", "connection", "timeout", "network"]):
        return "Infra"
    else:
        return "Test Body"

# Intermittent (mark for rerun) patterns: loaded from JSON; if exception_summary
# matches any pattern, mark for rerun as Yes.
INTERMITTENT_PATTERNS_JSON = os.path.join(os.path.dirname(__file__), "intermittent_patterns.json")

def _load_intermittent_patterns():
    """Load intermittent regex patterns from JSON file."""
    default_patterns = [
        r"Timedout executing command source.*cluster start in .* secs with error:",
        r"Timedout executing command source.*cluster.*create in .* secs with error",
        r"Couldn't get handle to SVM VM object for ip",
    ]
    try:
        if os.path.exists(INTERMITTENT_PATTERNS_JSON):
            with open(INTERMITTENT_PATTERNS_JSON, "r") as f:
                data = json.load(f)
            raw = data.get("intermittent_patterns", data) if isinstance(data, dict) else data
            if isinstance(raw, list) and raw:
                return [re.compile(p, re.IGNORECASE) for p in raw]
    except Exception as e:
        logger.warning(f"Could not load intermittent_patterns.json: {e}, using defaults")
    return [re.compile(p, re.IGNORECASE) for p in default_patterns]

SETUP_EXC_LIST = _load_intermittent_patterns()

def is_intermittent_rerun(exception_summary):
    """If exception_summary matches setup_exc_list patterns, mark for rerun as Yes."""
    if not exception_summary:
        return "No"
    text = (exception_summary or "").strip()
    for pattern in SETUP_EXC_LIST:
        if pattern.search(text):
            return "Yes"
    return "No"

def classify_failure(exception_summary, exception, jira_data, glean_data):
    """Classify failure as Test Issue or Product Issue"""
    exception_lower = (exception_summary or "").lower() + " " + (exception or "").lower()
    
    # Test Issue indicators
    test_issue_keywords = [
        "assertion", "assert", "expected", "actual", "python", "import error",
        "syntax error", "indentation", "nameerror", "attributeerror", "typeerror",
        "test framework", "pytest", "unittest", "dependency", "library", "module not found"
    ]
    
    # Product Issue indicators
    product_issue_keywords = [
        "api", "backend", "server", "500", "503", "timeout", "connection refused",
        "feature", "regression", "bug", "defect", "broken", "not working"
    ]
    
    test_issue_score = sum(1 for keyword in test_issue_keywords if keyword in exception_lower)
    product_issue_score = sum(1 for keyword in product_issue_keywords if keyword in exception_lower)
    
    # Check Jira ticket if available
    if jira_data:
        jira_summary = jira_data.get("fields", {}).get("summary", "").lower()
        jira_description = jira_data.get("fields", {}).get("description", "").lower()
        jira_combined = f"{jira_summary} {jira_description}"
        
        if "test" in jira_combined and "fix" in jira_combined:
            test_issue_score += 2
        if "product" in jira_combined or "regression" in jira_combined:
            product_issue_score += 2
    
    if test_issue_score > product_issue_score and test_issue_score > 0:
        return "Test Issue"
    elif product_issue_score > test_issue_score and product_issue_score > 0:
        return "Product Issue"
    else:
        return "Unknown / Needs Manual Review"

def validate_triage_genie_ticket(jira_ticket_id, exception_summary, exception):
    """Validate Triage Genie / Jira ticket relevance"""
    if not jira_ticket_id:
        return "Invalid"
    
    jira_data = fetch_jira_ticket(jira_ticket_id)
    if not jira_data:
        return "Invalid"
    
    fields = jira_data.get("fields", {})
    ticket_status = fields.get("status", {}).get("name", "")
    resolution = fields.get("resolution", {}).get("name", "") if fields.get("resolution") else None
    summary = fields.get("summary", "").lower()
    description = fields.get("description", "").lower()
    
    # Check if ticket is resolved/closed
    if resolution or ticket_status in ["Closed", "Resolved", "Done"]:
        return "Invalid"
    
    # Compare exception with ticket description
    exception_lower = (exception_summary or "").lower() + " " + (exception or "").lower()
    ticket_text = f"{summary} {description}"
    
    # Check for keyword overlap
    exception_words = set(exception_lower.split())
    ticket_words = set(ticket_text.split())
    common_words = exception_words.intersection(ticket_words)
    
    if len(common_words) >= 3:
        return "Valid"
    elif len(common_words) >= 1:
        return "Partial"
    else:
        return "Invalid"

def generate_ai_suggestion(issue_type, exception_summary, exception, jira_tickets, jira_data, glean_data):
    """Generate AI suggestion based on analysis"""
    suggestions = []
    
    if issue_type == "Test Issue":
        exception_lower = (exception_summary or "").lower() + " " + (exception or "").lower()
        
        if "python" in exception_lower or "import" in exception_lower:
            suggestions.append("Failure caused by Python dependency or import issue. Update test dependencies and verify Python environment.")
        elif "assertion" in exception_lower or "assert" in exception_lower:
            suggestions.append("Assertion failure detected. Review expected vs actual values. Update test assertions if product behavior has changed.")
        elif "syntax" in exception_lower or "indentation" in exception_lower:
            suggestions.append("Syntax error in test code. Review and fix test script syntax.")
        else:
            suggestions.append("Test logic issue identified. Review test implementation and update test code. Consider creating a Nugerrit CR for test fixes.")
        
        if jira_tickets:
            suggestions.append(f"Review Jira ticket(s): {', '.join(jira_tickets)}")
    
    elif issue_type == "Product Issue":
        if jira_tickets and jira_data:
            ticket_id = jira_tickets[0]
            suggestions.append(f"Failure aligns with known product issue in {ticket_id}. Test behavior is valid. Monitor Jira ticket for fix.")
        elif jira_tickets:
            suggestions.append(f"Product regression detected. Track via Jira ticket(s): {', '.join(jira_tickets)}. Test should remain as-is until product fix.")
        else:
            suggestions.append("Product-level failure identified. Test behavior is valid. Consider creating a Jira ticket to track this product issue.")
        
        if glean_data:
            suggestions.append("Similar issues found in Glean search. Review related documentation or known issues.")
    
    else:
        suggestions.append("Unable to definitively classify failure. Manual review recommended. Check test logs and Jira tickets for context.")
        if jira_tickets:
            suggestions.append(f"Review existing Jira ticket(s): {', '.join(jira_tickets)}")
    
    return " ".join(suggestions)

def fetch_detailed_test_result(testcase_id):
    """Fetch detailed test result including exception and failure stage"""
    try:
        resp = session.get(
            f"{PHX_BASE}/agave_test_results/{testcase_id}",
            timeout=30
        )
        resp.raise_for_status()
        return resp.json().get("data", {})
    except Exception as e:
        logger.warning(f"Error fetching detailed test result {testcase_id}: {e}")
        return {}

def convert_testcase_name_to_path(testcase_name):
    """
    Convert testcase name to directory path.
    Example: cdp.curator.goldsuite_iointegrity.test_goldsuite.CuratorGoldSuiteTest.test_gold___cluster_upgrade_with_disk_balancing
    -> cdp/curator/goldsuite_iointegrity/test_goldsuite/CuratorGoldSuiteTest/test_gold___cluster_upgrade_with_disk_balancing
    """
    if not testcase_name:
        return ""
    # Replace dots with slashes
    path = testcase_name.replace(".", "/")
    return path

def fetch_log_from_url(log_url, timeout=30):
    """
    Fetch log content from a URL.
    Returns the log content as string, or empty string if fetch fails.
    """
    try:
        resp = session.get(log_url, timeout=timeout, verify=False)
        if resp.status_code == 200:
            return resp.text
        else:
            logger.warning(f"Failed to fetch log from {log_url}: HTTP {resp.status_code}")
            return ""
    except Exception as e:
        logger.warning(f"Error fetching log from {log_url}: {e}")
        return ""

def fetch_testcase_logs(testcase_name, tester_log_url):
    """
    Fetch steps.log and nutest_test.log for a testcase.
    
    Args:
        testcase_name: Full testcase name (e.g., cdp.curator.goldsuite_iointegrity.test_goldsuite.CuratorGoldSuiteTest.test_gold___cluster_upgrade_with_disk_balancing)
        tester_log_url: Base URL for logs directory (e.g., http://10.40.234.216/logs/...)
    
    Returns:
        dict with 'steps_log' and 'nutest_test_log' keys, containing log content or empty strings
    """
    logs = {
        "steps_log": "",
        "nutest_test_log": ""
    }
    
    if not testcase_name or not tester_log_url:
        return logs
    
    # Convert testcase name to path
    testcase_path = convert_testcase_name_to_path(testcase_name)
    if not testcase_path:
        return logs
    
    # Construct log URLs
    # Ensure tester_log_url ends with /
    base_url = tester_log_url.rstrip("/") + "/"
    
    # Construct full paths
    steps_log_url = f"{base_url}{testcase_path}/steps.log"
    nutest_test_log_url = f"{base_url}{testcase_path}/nutest_test.log"
    
    logger.info(f"Fetching logs for {testcase_name}")
    logger.debug(f"Steps log URL: {steps_log_url}")
    logger.debug(f"Nutest test log URL: {nutest_test_log_url}")
    
    # Fetch logs
    logs["steps_log"] = fetch_log_from_url(steps_log_url)
    logs["nutest_test_log"] = fetch_log_from_url(nutest_test_log_url)
    
    return logs

def generate_ai_failure_summary(exception, exception_summary, tester_log_url, testcase_name, steps_log="", nutest_test_log=""):
    """
    Generate AI-based failure summary using the Nutanix AI endpoint.
    
    Args:
        exception: Full exception text
        exception_summary: Exception summary
        tester_log_url: Base URL for logs
        testcase_name: Testcase name
        steps_log: Content of steps.log
        nutest_test_log: Content of nutest_test.log
    
    Returns:
        tuple (success: bool, summary: str or error message)
    """
    try:
        # Build the prompt content
        log_content = ""
        if steps_log:
            log_content += f"=== steps.log ===\n{steps_log[:5000]}\n\n"  # Limit to 5000 chars per log
        if nutest_test_log:
            log_content += f"=== nutest_test.log ===\n{nutest_test_log[:5000]}\n\n"
        
        # Build user content
        user_content = (
            f"Testcase: {testcase_name}\n\n"
            f"Exception Summary: {exception_summary or 'N/A'}\n\n"
            f"Exception:\n```\n{exception or 'N/A'}\n```\n\n"
        )
        
        if log_content:
            user_content += f"Test Logs:\n{log_content}\n"
        
        user_content += (
            "Provide a concise failure summary:\n"
            "(1) Root cause in one sentence\n"
            "(2) Failing component or line if clear\n"
            "(3) Suggested fix or next step"
        )
        
        # System prompt
        system_prompt = (
            "You are a test failure analyst. Given a Python test failure with exception details and logs, "
            "provide a concise failure summary: (1) Root cause in one sentence, (2) failing component or line if clear, "
            "(3) suggested fix or next step. Be specific and actionable."
        )
        
        # Prepare payload
        payload = {
            "model": "hack-reason",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            "max_tokens": 1024,
            "stream": False
        }
        
        # Make request to AI endpoint
        url = f"{AI_BASE}/chat/completions"
        headers = {
            "Authorization": f"Bearer {AI_API_KEY}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=60) as resp:
            if resp.getcode() != 200:
                return False, f"AI API returned HTTP {resp.getcode()}"
            
            response_data = json.loads(resp.read().decode())
            choices = response_data.get("choices", [])
            if not choices:
                return False, "AI returned no choices"
            
            content = (choices[0].get("message") or {}).get("content", "")
            return True, content.strip()
            
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        try:
            err_json = json.loads(body)
        except Exception:
            err_json = {"raw": body}
        return False, f"AI API error HTTP {e.code}: {json.dumps(err_json)[:300]}"
    except Exception as e:
        logger.error(f"Error generating AI failure summary: {e}", exc_info=True)
        return False, f"Error generating AI summary: {str(e)}"

def create_triage_genie_session():
    """
    Create a requests.Session authenticated via JITA login for Triage Genie API calls.
    Used by Failed Testcase Analysis when calling Triage Genie.
    """
    session_triage = requests.Session()
    session_triage.verify = False
    login_payload = {
        "username": JITA_USERNAME,
        "password": JITA_PASSWORD
    }
    try:
        login_response = session_triage.post(
            LOGIN_URL,
            data=login_payload,
            verify=False,
            timeout=15
        )
        if login_response.status_code != 200:
            logger.warning(f"Triage Genie login returned {login_response.status_code}, session may not be authenticated")
        return session_triage
    except Exception as e:
        logger.warning(f"Triage Genie login failed: {e}")
        return None


def fetch_triage_genie_ticket_id(agave_task_id, triage_session=None):
    """
    Fetch Triage Genie generated ticket ID using agave_task_id (testcase_id)
    
    Uses direct API call: GET /api/tasks/{testcase_id}
    Extracts dup_ticket_id from the response
    
    When triage_session is provided (e.g. from create_triage_genie_session()), uses
    login session for API calls; otherwise falls back to TRIAGE_GENIE_TOKEN.
    
    Returns:
        str: Jira ticket ID (e.g., "ENG-858029") or None if not found
    """
    if not agave_task_id:
        return None
    
    try:
        if triage_session:
            headers = {"Content-Type": "application/json"}
        else:
            triage_token = os.getenv("TRIAGE_GENIE_TOKEN", "TOKEN")
            headers = {
                "Authorization": f"Bearer {triage_token}",
                "Content-Type": "application/json"
            }
        
        # Approach 1: Direct task lookup using testcase_id
        # The testcase_id (agave_task_id) can be used directly as the Triage Genie task ID
        try:
            if triage_session:
                task_response = triage_session.get(
                    f"{TRIAGE_GENIE_BASE}/tasks/{agave_task_id}",
                    headers=headers,
                    verify=False,
                    timeout=15
                )
            else:
                task_response = requests.get(
                    f"{TRIAGE_GENIE_BASE}/tasks/{agave_task_id}",
                    headers=headers,
                    verify=False,
                    timeout=15
                )
            
            if task_response.status_code == 200:
                task_data = task_response.json()
                dup_ticket_id = task_data.get("dup_ticket_id")
                if dup_ticket_id:
                    logger.info(f"Found Triage Genie ticket {dup_ticket_id} for testcase_id {agave_task_id}")
                    return dup_ticket_id
            elif task_response.status_code == 404:
                logger.debug(f"Triage Genie task not found for testcase_id {agave_task_id}")
            else:
                logger.debug(f"Triage Genie API returned status {task_response.status_code} for testcase_id {agave_task_id}")
        
        except requests.exceptions.RequestException as e:
            logger.debug(f"Error in direct task lookup for testcase_id {agave_task_id}: {e}")
        
        # Approach 2: Fallback - Search through recent jobs if direct lookup fails
        # This is a backup method in case the testcase_id format doesn't match Triage Genie task ID
        try:
            if triage_session:
                jobs_response = triage_session.get(
                    f"{TRIAGE_GENIE_BASE}/jobs",
                    headers=headers,
                    params={"page": 1, "per_page": 20, "show_all": "true"},
                    verify=False,
                    timeout=15
                )
            else:
                jobs_response = requests.get(
                    f"{TRIAGE_GENIE_BASE}/jobs",
                    headers=headers,
                    params={"page": 1, "per_page": 20, "show_all": "true"},
                    verify=False,
                    timeout=15
                )
            
            if jobs_response.status_code == 200:
                jobs_data = jobs_response.json()
                jobs = jobs_data.get("jobs", []) or jobs_data.get("data", [])
                
                # Search through jobs to find matching task
                for job in jobs:
                    job_id = job.get("id") or job.get("_id")
                    if not job_id:
                        continue
                    
                    try:
                        if triage_session:
                            tasks_response = triage_session.get(
                                f"{TRIAGE_GENIE_BASE}/jobs/{job_id}/tasks",
                                headers=headers,
                                params={
                                    "page": 1,
                                    "per_page": 100
                                },
                                verify=False,
                                timeout=15
                            )
                        else:
                            tasks_response = requests.get(
                                f"{TRIAGE_GENIE_BASE}/jobs/{job_id}/tasks",
                                headers=headers,
                                params={
                                    "page": 1,
                                    "per_page": 100
                                },
                                verify=False,
                                timeout=15
                            )
                        
                        if tasks_response.status_code == 200:
                            tasks_data = tasks_response.json()
                            tasks = tasks_data.get("data", []) or tasks_data.get("tasks", [])
                            
                            # Find task with matching agave_task_id
                            for task in tasks:
                                task_agave_id = task.get("agave_task_id")
                                # Handle both string and object ID formats
                                if (task_agave_id == agave_task_id or 
                                    str(task_agave_id) == str(agave_task_id) or
                                    (isinstance(task_agave_id, dict) and task_agave_id.get("$oid") == agave_task_id)):
                                    dup_ticket_id = task.get("dup_ticket_id")
                                    if dup_ticket_id:
                                        logger.info(f"Found Triage Genie ticket {dup_ticket_id} for agave_task_id {agave_task_id} in job {job_id} (fallback method)")
                                        return dup_ticket_id
                    except Exception as e:
                        logger.debug(f"Error fetching tasks for job {job_id}: {e}")
                        continue
            
        except Exception as e:
            logger.debug(f"Error in fallback job search for Triage Genie lookup: {e}")
        
        return None
        
    except requests.exceptions.ConnectionError:
        logger.warning(f"Connection error fetching Triage Genie ticket for testcase_id {agave_task_id}")
        return None
    except requests.exceptions.Timeout:
        logger.warning(f"Timeout fetching Triage Genie ticket for testcase_id {agave_task_id}")
        return None
    except Exception as e:
        logger.warning(f"Error fetching Triage Genie ticket for testcase_id {agave_task_id}: {e}")
        return None

@app.route("/mcp/regression/failed-analysis/analyze", methods=["GET"])
@jwt_required
def analyze_failed_testcases():
    """Analyze failed testcases with AI agent"""
    start = time.time()
    
    tag = request.args.get("tag")
    task_ids_param = request.args.get("task_ids")
    include_param = request.args.get("include", "")
    include_set = {x.strip().lower() for x in include_param.split(",") if x.strip()} if include_param else set()
    # Default: basic + exception_summary + intermittent when no include given
    if not include_set:
        include_set = {"basic", "exception_summary", "intermittent"}
    
    # Parse task_ids if provided
    task_ids = None
    if task_ids_param:
        task_ids = [tid.strip() for tid in task_ids_param.split(",") if tid.strip()]
    
    if not tag and not task_ids:
        return jsonify({"error": "Either tag or task_ids is required"}), 400
    
    try:
        # Fetch regression tasks
        tasks = fetch_regression_tasks(tag=tag, task_ids=task_ids)
        if not tasks:
            return jsonify({
                "success": True,
                "results": [],
                "message": "No tasks found for the given criteria"
            })
        
        # Collect all task IDs
        collected_task_ids = [task["_id"]["$oid"] for task in tasks]
        
        # Fetch test results - only failed ones
        logger.info(f"Fetching failed test results for {len(collected_task_ids)} tasks")
        all_test_results = []
        
        if collected_task_ids:
            try:
                # Fetch all test results first
                all_results = fetch_test_results_batch_with_pagination(collected_task_ids)
                # Filter for failed tests
                failed_results = [
                    tr for tr in all_results 
                    if tr.get("status", "").lower() in ("failed", "failure")
                ]
                logger.info(f"Found {len(failed_results)} failed testcases")
                
                # Triage Genie session only when triage_genie_ticket requested
                triage_genie_session = create_triage_genie_session() if "triage_genie_ticket" in include_set else None
                
                # Current branch for history API (from first task)
                current_branch = (tasks[0].get("branch") or "") if tasks else ""
                
                # Analyze each failed testcase
                analysis_results = []
                
                for test_result in failed_results:
                    testcase_id = None
                    if isinstance(test_result.get("_id"), dict) and "$oid" in test_result.get("_id", {}):
                        testcase_id = test_result["_id"]["$oid"]
                    else:
                        testcase_id = str(test_result.get("_id", ""))
                    
                    # Fetch detailed test result for exception and failure stage
                    detailed_result = {}
                    if testcase_id:
                        detailed_result = fetch_detailed_test_result(testcase_id)
                    
                    # Extract data
                    test_field = test_result.get("test", {})
                    if isinstance(test_field, dict):
                        testcase_name = test_field.get("name", "")
                    elif isinstance(test_field, str):
                        testcase_name = test_field
                    else:
                        # Try from detailed result
                        detailed_test = detailed_result.get("test", {})
                        if isinstance(detailed_test, dict):
                            testcase_name = detailed_test.get("name", "")
                        elif isinstance(detailed_test, str):
                            testcase_name = detailed_test
                        else:
                            testcase_name = ""
                    
                    status = test_result.get("status", "FAILED")
                    exception_summary = test_result.get("exception_summary") or detailed_result.get("exception_summary", "")
                    exception = detailed_result.get("exception", "")
                    jira_tickets = test_result.get("jira_tickets", [])
                    test_log_url = test_result.get("test_log_url") or detailed_result.get("test_log_url", "")
                    comments = test_result.get("comments") or detailed_result.get("comments") or ""
                    
                    # Determine failure stage
                    failure_stage = determine_failure_stage({**test_result, **detailed_result})
                    
                    # Intermittent (mark for rerun) from exception_summary
                    intermittent_rerun = is_intermittent_rerun(exception_summary) if "intermittent" in include_set else None
                    
                    # Optional heavy fields: Jira/Glean, issue type, suggestion, Triage Genie ticket, AI Summary
                    jira_data = None
                    glean_data = None
                    issue_type = None
                    suggestion = None
                    triage_genie_ticket_id = None
                    ai_summary = None
                    if "issue_type" in include_set or "suggestion" in include_set:
                        if jira_tickets:
                            jira_data = fetch_jira_ticket(jira_tickets[0])
                        if exception_summary:
                            glean_data = search_glean(exception_summary)
                    if "issue_type" in include_set:
                        issue_type = classify_failure(exception_summary, exception, jira_data, glean_data)
                    if "suggestion" in include_set:
                        suggestion = generate_ai_suggestion(
                            issue_type or classify_failure(exception_summary, exception, jira_data, glean_data),
                            exception_summary, exception, jira_tickets, jira_data, glean_data
                        )
                    if "triage_genie_ticket" in include_set and testcase_id and triage_genie_session:
                        triage_genie_ticket_id = fetch_triage_genie_ticket_id(testcase_id, triage_session=triage_genie_session)
                    
                    # Generate AI Summary if requested
                    if "ai_summary" in include_set:
                        try:
                            # Fetch logs
                            logs = fetch_testcase_logs(testcase_name, test_log_url)
                            
                            # Generate AI summary
                            success, summary = generate_ai_failure_summary(
                                exception=exception,
                                exception_summary=exception_summary,
                                tester_log_url=test_log_url,
                                testcase_name=testcase_name,
                                steps_log=logs.get("steps_log", ""),
                                nutest_test_log=logs.get("nutest_test_log", "")
                            )
                            
                            if success:
                                ai_summary = summary
                            else:
                                ai_summary = f"Error generating summary: {summary}"
                                logger.warning(f"Failed to generate AI summary for {testcase_name}: {summary}")
                        except Exception as e:
                            logger.error(f"Error generating AI summary for {testcase_name}: {e}", exc_info=True)
                            ai_summary = f"Error: {str(e)}"
                    
                    # Resolve regression owner
                    regression_owner = resolve_owner(testcase_name) if testcase_name else "Unknown"
                    
                    row = {
                        "testcase_id": testcase_id,
                        "testcase_name": testcase_name,
                        "status": status,
                        "failure_stage": failure_stage,
                        "jira_tickets": jira_tickets,
                        "regression_owner": regression_owner,
                        "test_log_url": test_log_url,
                        "exception_summary": exception_summary,
                        "comments": comments,
                        "exception": exception[:200] if exception else ""
                    }
                    if intermittent_rerun is not None:
                        row["intermittent_rerun"] = intermittent_rerun
                    if issue_type is not None:
                        row["issue_type"] = issue_type
                    if suggestion is not None:
                        row["suggestion_by_ai_agent"] = suggestion
                    if triage_genie_ticket_id is not None:
                        row["triage_genie_ticket_id"] = triage_genie_ticket_id
                    if ai_summary is not None:
                        row["ai_summary"] = ai_summary
                    
                    analysis_results.append(row)
                
                logger.info(f"[END] Failed Analysis | results={len(analysis_results)} | time={time.time() - start:.2f}s")
                
                return jsonify({
                    "success": True,
                    "results": analysis_results,
                    "total_analyzed": len(analysis_results),
                    "current_branch": current_branch,
                    "tag": tag or None
                })
                
            except requests.exceptions.ConnectionError as e:
                logger.error(f"Connection error: {e}", exc_info=True)
                return jsonify({
                    "error": "Failed to connect to JITA API. Please check your network connection and ensure 'jita.eng.nutanix.com' is accessible.",
                    "details": str(e)
                }), 503
            except requests.exceptions.Timeout as e:
                logger.error(f"Timeout error: {e}", exc_info=True)
                return jsonify({
                    "error": "Request to JITA API timed out. Please try again.",
                    "details": str(e)
                }), 504
            except Exception as e:
                logger.error(f"Error fetching test results: {e}", exc_info=True)
                return jsonify({"error": f"Failed to fetch test results: {str(e)}"}), 500
        else:
            return jsonify({
                "success": True,
                "results": [],
                "message": "No task IDs found"
            })
            
    except ConnectionError as e:
        logger.error(f"Connection error: {e}", exc_info=True)
        return jsonify({
            "error": str(e),
            "type": "connection_error"
        }), 503
    except TimeoutError as e:
        logger.error(f"Timeout error: {e}", exc_info=True)
        return jsonify({
            "error": str(e),
            "type": "timeout_error"
        }), 504
    except Exception as e:
        logger.error(f"Error analyzing failed testcases: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


def _analyze_failed_testcases_stream(tag, task_ids, include_set):
    """Generator that yields SSE events: start, then one 'row' per result, then 'done' or 'error'."""
    try:
        tasks = fetch_regression_tasks(tag=tag, task_ids=task_ids)
        if not tasks:
            yield json.dumps({"type": "start", "total": 0, "current_branch": "", "tag": tag})
            yield json.dumps({"type": "done"})
            return
        collected_task_ids = [task["_id"]["$oid"] for task in tasks]
        all_results = fetch_test_results_batch_with_pagination(collected_task_ids)
        failed_results = [
            tr for tr in all_results
            if tr.get("status", "").lower() in ("failed", "failure")
        ]
        current_branch = (tasks[0].get("branch") or "") if tasks else ""
        triage_genie_session = create_triage_genie_session() if "triage_genie_ticket" in include_set else None

        yield json.dumps({
            "type": "start",
            "total": len(failed_results),
            "current_branch": current_branch,
            "tag": tag or None
        })

        for test_result in failed_results:
            testcase_id = None
            if isinstance(test_result.get("_id"), dict) and "$oid" in test_result.get("_id", {}):
                testcase_id = test_result["_id"]["$oid"]
            else:
                testcase_id = str(test_result.get("_id", ""))
            detailed_result = {}
            if testcase_id:
                detailed_result = fetch_detailed_test_result(testcase_id)
            test_field = test_result.get("test", {})
            if isinstance(test_field, dict):
                testcase_name = test_field.get("name", "")
            elif isinstance(test_field, str):
                testcase_name = test_field
            else:
                detailed_test = detailed_result.get("test", {})
                if isinstance(detailed_test, dict):
                    testcase_name = detailed_test.get("name", "")
                elif isinstance(detailed_test, str):
                    testcase_name = detailed_test
                else:
                    testcase_name = ""
            status = test_result.get("status", "FAILED")
            exception_summary = test_result.get("exception_summary") or detailed_result.get("exception_summary", "")
            exception = detailed_result.get("exception", "")
            jira_tickets = test_result.get("jira_tickets", [])
            test_log_url = test_result.get("test_log_url") or detailed_result.get("test_log_url", "")
            comments = test_result.get("comments") or detailed_result.get("comments") or ""
            failure_stage = determine_failure_stage({**test_result, **detailed_result})
            intermittent_rerun = is_intermittent_rerun(exception_summary) if "intermittent" in include_set else None
            jira_data = None
            glean_data = None
            issue_type = None
            suggestion = None
            triage_genie_ticket_id = None
            ai_summary = None
            if "issue_type" in include_set or "suggestion" in include_set:
                if jira_tickets:
                    jira_data = fetch_jira_ticket(jira_tickets[0])
                if exception_summary:
                    glean_data = search_glean(exception_summary)
            if "issue_type" in include_set:
                issue_type = classify_failure(exception_summary, exception, jira_data, glean_data)
            if "suggestion" in include_set:
                suggestion = generate_ai_suggestion(
                    issue_type or classify_failure(exception_summary, exception, jira_data, glean_data),
                    exception_summary, exception, jira_tickets, jira_data, glean_data
                )
            if "triage_genie_ticket" in include_set and testcase_id and triage_genie_session:
                triage_genie_ticket_id = fetch_triage_genie_ticket_id(testcase_id, triage_session=triage_genie_session)
            
            # Generate AI Summary if requested
            if "ai_summary" in include_set:
                try:
                    # Fetch logs
                    logs = fetch_testcase_logs(testcase_name, test_log_url)
                    
                    # Generate AI summary
                    success, summary = generate_ai_failure_summary(
                        exception=exception,
                        exception_summary=exception_summary,
                        tester_log_url=test_log_url,
                        testcase_name=testcase_name,
                        steps_log=logs.get("steps_log", ""),
                        nutest_test_log=logs.get("nutest_test_log", "")
                    )
                    
                    if success:
                        ai_summary = summary
                    else:
                        ai_summary = f"Error generating summary: {summary}"
                        logger.warning(f"Failed to generate AI summary for {testcase_name}: {summary}")
                except Exception as e:
                    logger.error(f"Error generating AI summary for {testcase_name}: {e}", exc_info=True)
                    ai_summary = f"Error: {str(e)}"
            
            regression_owner = resolve_owner(testcase_name) if testcase_name else "Unknown"
            row = {
                "testcase_id": testcase_id,
                "testcase_name": testcase_name,
                "status": status,
                "failure_stage": failure_stage,
                "jira_tickets": jira_tickets,
                "regression_owner": regression_owner,
                "test_log_url": test_log_url,
                "exception_summary": exception_summary,
                "comments": comments,
                "exception": exception[:200] if exception else ""
            }
            if intermittent_rerun is not None:
                row["intermittent_rerun"] = intermittent_rerun
            if issue_type is not None:
                row["issue_type"] = issue_type
            if suggestion is not None:
                row["suggestion_by_ai_agent"] = suggestion
            if triage_genie_ticket_id is not None:
                row["triage_genie_ticket_id"] = triage_genie_ticket_id
            if ai_summary is not None:
                row["ai_summary"] = ai_summary
            yield json.dumps({"type": "row", "result": row})
        yield json.dumps({"type": "done"})
    except Exception as e:
        logger.error(f"Error in analyze stream: {e}", exc_info=True)
        yield json.dumps({"type": "error", "message": str(e)})


@app.route("/mcp/regression/failed-analysis/analyze-stream", methods=["GET"])
@jwt_required
def analyze_failed_testcases_stream():
    """Stream analysis results as Server-Sent Events so the UI can display rows as they load."""
    tag = request.args.get("tag")
    task_ids_param = request.args.get("task_ids")
    include_param = request.args.get("include", "")
    include_set = {x.strip().lower() for x in include_param.split(",") if x.strip()} if include_param else set()
    if not include_set:
        include_set = {"basic", "exception_summary", "intermittent"}
    task_ids = None
    if task_ids_param:
        task_ids = [tid.strip() for tid in task_ids_param.split(",") if tid.strip()]
    if not tag and not task_ids:
        return jsonify({"error": "Either tag or task_ids is required"}), 400

    def gen():
        for chunk in _analyze_failed_testcases_stream(tag, task_ids, include_set):
            yield f"data: {chunk}\n\n"

    return Response(
        stream_with_context(gen()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.route("/mcp/regression/failed-analysis/update-triage", methods=["PUT"])
@jwt_required
def update_triage_comments():
    """Update comment and/or jira_ticket for an agave_test_result via JITA PUT API."""
    try:
        data = request.get_json() or {}
        test_id = data.get("test_id")
        comment = data.get("comment", "")
        jira_ticket = data.get("jira_ticket")
        if not test_id:
            return jsonify({"error": "test_id is required"}), 400
        triaged_by = os.getenv("TRIAGED_BY_USER", "sudharshan.musali")
        update_fields = {
            "comments": comment,
            "triaged_by": triaged_by
        }
        if jira_ticket is not None:
            update_fields["jira_ticket"] = jira_ticket
        payload = {
            "query": {"_id": {"$in": [{"$oid": test_id}]}},
            "data": {"$set": update_fields},
            "multi": True
        }
        url = f"{JITA_BASE}/agave_test_results"
        resp = requests.put(
            url,
            headers={"Content-Type": "application/json"},
            json=payload,
            auth=JITA_AUTH,
            verify=False,
            timeout=30
        )
        if resp.status_code == 200:
            return jsonify({"success": True, "message": "Updated"})
        return jsonify({"error": resp.text or f"HTTP {resp.status_code}"}), resp.status_code if resp.status_code >= 400 else 500
    except Exception as e:
        logger.error(f"Error updating triage: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


def _fetch_test_result_for_task_and_name(task_id, test_name):
    """Fetch test result for a single task and test name. Returns one result row or None."""
    payload = {
        "raw_query": {"agave_task_id": {"$oid": task_id}},
        "only": "test,status,jira_tickets,comments",
        "start": 0,
        "limit": 500,
        "merge": False
    }
    try:
        resp = session.post(
            f"{JITA_BASE}/reports/agave_test_results",
            json=payload,
            timeout=60
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        results = data.get("data", [])
        for r in results:
            t = r.get("test") or {}
            name = t.get("name") if isinstance(t, dict) else (t if isinstance(t, str) else "")
            if name == test_name:
                return {
                    "status": (r.get("status") or "").lower(),
                    "jira_ticket": (r.get("jira_tickets") or [None])[0] if r.get("jira_tickets") else None,
                    "comment": r.get("comments") or None
                }
        return None
    except Exception as e:
        logger.warning(f"History fetch for task {task_id} test {test_name}: {e}")
        return None


@app.route("/mcp/regression/failed-analysis/history", methods=["GET"])
@jwt_required
def failed_analysis_history():
    """Return past 3 runs for a test on same or other branch. For each run: status, jira_ticket or comment."""
    test_name = request.args.get("test_name")
    branch = request.args.get("branch", "")
    same_branch = request.args.get("same_branch", "true").lower() in ("1", "true", "yes")
    tag = request.args.get("tag")
    if not test_name or not tag:
        return jsonify({"error": "test_name and tag are required"}), 400
    try:
        tasks = fetch_regression_tasks(tag=tag, task_ids=None)
        if not tasks:
            return jsonify({"runs": []})
        if same_branch:
            filtered = [t for t in tasks if (t.get("branch") or "") == branch]
        else:
            filtered = [t for t in tasks if (t.get("branch") or "") != branch]
        # Sort by _id descending (newest first), take 3
        filtered.sort(key=lambda t: t.get("_id") or {}, reverse=True)
        selected = filtered[:3]
        runs = []
        for task in selected:
            task_id = task.get("_id", {}).get("$oid") if isinstance(task.get("_id"), dict) else task.get("_id")
            if not task_id:
                continue
            row = _fetch_test_result_for_task_and_name(task_id, test_name)
            if row:
                runs.append({
                    "status": "passed" if row["status"] in ("passed", "succeeded", "success") else "failed",
                    "jira_ticket": row["jira_ticket"],
                    "comment": row["comment"]
                })
            else:
                runs.append({"status": "unknown", "jira_ticket": None, "comment": None})
        return jsonify({"runs": runs})
    except Exception as e:
        logger.error(f"Error fetching history: {e}", exc_info=True)
        return jsonify({"error": str(e), "runs": []}), 500


# ======================================================
# Testcase Management
# ======================================================

TESTCASE_MGMT_BRANCHES = {
    "master": {"milestone": "master", "team_prefix": "master", "test_set_regex": "test_sets/milestones/master/"},
    "ganges-7.6-stable": {"milestone": "7.6", "team_prefix": "7.6", "test_set_regex": "test_sets/milestones/7.6/"},
    "ganges-7.5-stable": {"milestone": "7.5", "team_prefix": "7.5", "test_set_regex": "test_sets/milestones/7.5/"},
}

TESTCASE_MGMT_TEAMS = ["CDP", "AHV"]


def _tcms_auth():
    return (TCMS_USER, TCMS_PASSWORD)


def _build_aggregate_payload(milestone, team_prefix, team, test_set_regex, skip, limit):
    """Build the MongoDB aggregation pipeline payload for the POST API."""
    return json.dumps([
        {"$match": {"$and": [
            {
                "target_milestone": milestone,
                "last_result": {"$elemMatch": {"pass_name": "overall"}},
                "deleted": False,
            },
            {"test_case.test_sets": {"$regex": test_set_regex, "$options": "i"}},
            {"additional_data.team": f"{team_prefix}/{team}"},
            {"release_name": {"$ne": milestone}},
            {"test_case.metadata.tags": {"$nin": ["SYSTEST_LONGEVITY", "LIMITED_RUNS"]}},
            {"test_case.deprecated": False},
        ]}},
        {"$sort": {"name": 1}},
        {"$skip": skip},
        {"$limit": limit},
    ])


def _normalize_testcase(item):
    """Extract a flat dict from a raw TCMS milestone_all_test_cases record."""
    tc = item.get("test_case", {})
    meta = tc.get("metadata", {})
    ad = item.get("additional_data", {})
    score = item.get("test_score", {})

    last_result_list = item.get("last_result", [])
    last_status = ""
    is_triaged = False
    issue_type = ""
    last_run_tickets = []
    last_run_date = None
    last_passed_date = None
    if isinstance(last_result_list, list) and last_result_list:
        entry = last_result_list[0]
        run_info = entry.get("run", {})
        last_status = run_info.get("status", "")
        is_triaged = run_info.get("is_triaged", False)
        issue_type = run_info.get("issue_type", "")
        last_run_tickets = run_info.get("tickets", [])
        run_start = run_info.get("start_time", {})
        if isinstance(run_start, dict) and "$date" in run_start:
            last_run_date = datetime.utcfromtimestamp(run_start["$date"] / 1000).strftime("%Y-%m-%d %H:%M")
        succeeded_info = entry.get("succeeded", {})
        if isinstance(succeeded_info, dict) and succeeded_info:
            succ_start = succeeded_info.get("start_time", {})
            if isinstance(succ_start, dict) and "$date" in succ_start:
                last_passed_date = datetime.utcfromtimestamp(succ_start["$date"] / 1000).strftime("%Y-%m-%d %H:%M")

    published_qi = None
    published_success_ops = None
    published_total_ops = None
    if isinstance(last_result_list, list) and last_result_list:
        published_info = last_result_list[0].get("published", {})
        if isinstance(published_info, dict) and published_info:
            published_qi = published_info.get("operation_success_percentage")
            published_success_ops = published_info.get("successful_operations")
            published_total_ops = published_info.get("total_operations")

    ect = item.get("execution_cycle_time", {})
    automated_date_raw = ad.get("automated_date", {})
    automated_date = None
    if isinstance(automated_date_raw, dict) and "$date" in automated_date_raw:
        automated_date = datetime.utcfromtimestamp(automated_date_raw["$date"] / 1000).strftime("%Y-%m-%d")

    return {
        "oid": (item.get("_id", {}).get("$oid", "") if isinstance(item.get("_id"), dict) else ""),
        "name": item.get("name", ""),
        "path": tc.get("path", ""),
        "owners": tc.get("owners", []),
        "priority": meta.get("priority", ""),
        "summary": meta.get("summary", ""),
        "components": meta.get("components", []),
        "primary_component": meta.get("primary_component", ""),
        "services": meta.get("services", []),
        "tags": [],
        "metadata_tags": meta.get("tags", []),
        "test_sets": tc.get("test_sets", []),
        "team": ad.get("team", []),
        "target_service": item.get("target_service", ""),
        "target": item.get("target", ""),
        "framework": tc.get("framework", ""),
        "last_status": last_status,
        "last_run_date": last_run_date,
        "last_passed_date": last_passed_date,
        "is_triaged": is_triaged,
        "issue_type": issue_type,
        "last_run_tickets": last_run_tickets,
        "success_percentage": ad.get("success_percentage"),
        "avg_run_duration": ad.get("avg_run_duration"),
        "automated_date": automated_date,
        "one_month_mttr": ect.get("one_month_mttr"),
        "three_months_mttr": ect.get("three_months_mttr"),
        "published_qi": published_qi,
        "published_success_ops": published_success_ops,
        "published_total_ops": published_total_ops,
        "stability": score.get("stability"),
        "effectiveness": score.get("effectiveness"),
        "total_results": score.get("total_results"),
        "tickets": item.get("tickets", []),
        "resource_spec": tc.get("resource_spec", []),
    }


def _fetch_tags_for_testcases(testcases, branch_key):
    """Batch-fetch tags from the GET all_test_cases API using regex matching."""
    if not testcases:
        return testcases

    name_map = {tc["name"]: tc for tc in testcases}
    target_branch = branch_key

    batch_size = 50
    names = list(name_map.keys())

    def _fetch_batch(batch_names):
        for tc_name in batch_names:
            try:
                raw_query = json.dumps({
                    "$and": [
                        {
                            "target_service": "NutestPy3Tests",
                            "target_branch": target_branch,
                            "target_package_type": "tar",
                            "deleted": False,
                        },
                        {"test_case.name": tc_name},
                        {"test_case.deprecated": False},
                    ]
                })
                url = (
                    f"{TCMS_TESTDB_BASE}/all_test_cases"
                    f"?raw_query={urllib.parse.quote(raw_query)}&sort=name&limit=1"
                )
                resp = requests.get(url, auth=_tcms_auth(), verify=False, timeout=30)
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    if data:
                        tags = data[0].get("additional_data", {}).get("tags", [])
                        if tc_name in name_map:
                            name_map[tc_name]["tags"] = tags
            except Exception as exc:
                logger.warning(f"Failed to fetch tags for {tc_name}: {exc}")

    with ThreadPoolExecutor(max_workers=10) as pool:
        for i in range(0, len(names), batch_size):
            batch = names[i:i + batch_size]
            pool.submit(_fetch_batch, batch)

    return testcases


def _tc_data_file(branch, team):
    """Return the path for a per-branch/team JSON file."""
    safe_name = f"testcase_management_{branch}_{team}.json".replace("/", "_")
    return os.path.join(TESTCASE_MGMT_DATA_DIR, safe_name)


def _load_tc_data(branch, team):
    fpath = _tc_data_file(branch, team)
    try:
        with open(fpath, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_updated": None, "branch": branch, "team": team, "testcases": []}


def _save_tc_data(branch, team, data):
    os.makedirs(TESTCASE_MGMT_DATA_DIR, exist_ok=True)
    fpath = _tc_data_file(branch, team)
    with open(fpath, "w") as f:
        json.dump(data, f, indent=2, default=str)


@app.route("/mcp/regression/testcase-mgmt/fetch-data", methods=["GET"])
@jwt_required
def testcase_mgmt_fetch_data():
    """Fetch all test cases from TCMS for a given branch/team and persist to JSON."""
    branch = request.args.get("branch", "master")
    team = request.args.get("team", "CDP")
    page_limit = 500

    branch_cfg = TESTCASE_MGMT_BRANCHES.get(branch)
    if not branch_cfg:
        return jsonify({"error": f"Unknown branch: {branch}"}), 400

    milestone = branch_cfg["milestone"]
    team_prefix = branch_cfg["team_prefix"]
    test_set_regex = branch_cfg["test_set_regex"]

    all_testcases = []
    skip = 0

    try:
        while True:
            payload = _build_aggregate_payload(milestone, team_prefix, team, test_set_regex, skip, page_limit)
            resp = requests.post(
                f"{TCMS_BASE}/milestone_all_test_cases/aggregate",
                data=payload,
                auth=_tcms_auth(),
                verify=False,
                timeout=120,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code != 200:
                logger.error(f"TCMS aggregate API returned {resp.status_code}: {resp.text[:500]}")
                break

            batch = resp.json().get("data", [])
            if not batch:
                break

            for item in batch:
                all_testcases.append(_normalize_testcase(item))

            logger.info(f"Fetched {len(batch)} testcases (skip={skip}) for {branch}/{team}")
            if len(batch) < page_limit:
                break
            skip += page_limit

        logger.info(f"Total testcases fetched from aggregate API: {len(all_testcases)} for {branch}/{team}")

        _fetch_tags_for_testcases(all_testcases, branch)

        now = datetime.utcnow().isoformat() + "Z"
        data = {
            "last_updated": now,
            "branch": branch,
            "team": team,
            "testcases": all_testcases,
        }
        _save_tc_data(branch, team, data)

        return jsonify({
            "status": "ok",
            "branch": branch,
            "team": team,
            "count": len(all_testcases),
            "last_updated": now,
        })

    except Exception as e:
        logger.error(f"Error fetching testcase data: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/mcp/regression/testcase-mgmt/testcases", methods=["GET"])
@jwt_required
def testcase_mgmt_get_testcases():
    """Return testcases from local JSON with optional filters."""
    branch = request.args.get("branch", "master")
    team = request.args.get("team", "CDP")
    tag_filter = request.args.get("tags", "")
    name_filter = request.args.get("name", "").lower()
    status_filter = request.args.get("status", "")

    data = _load_tc_data(branch, team)
    all_testcases = data.get("testcases", [])
    testcases = list(all_testcases)

    if tag_filter:
        filter_tags = [t.strip().lower() for t in tag_filter.split(",") if t.strip()]
        testcases = [
            tc for tc in testcases
            if any(ft in [t.lower() for t in tc.get("tags", [])] for ft in filter_tags)
        ]

    if name_filter:
        testcases = [tc for tc in testcases if name_filter in tc.get("name", "").lower()]

    if status_filter:
        testcases = [tc for tc in testcases if tc.get("last_status", "").lower() == status_filter.lower()]

    all_tags = set()
    for tc in all_testcases:
        for t in tc.get("tags", []):
            all_tags.add(t)

    return jsonify({
        "branch": branch,
        "team": team,
        "count": len(testcases),
        "total_count": len(all_testcases),
        "last_updated": data.get("last_updated"),
        "available_tags": sorted(all_tags),
        "testcases": testcases,
    })


@app.route("/mcp/regression/testcase-mgmt/tags/add", methods=["POST"])
@jwt_required
def testcase_mgmt_add_tags():
    """Add tags to selected test cases via TCMS write API."""
    body = request.get_json(force=True)
    testcase_oids = body.get("testcase_oids", [])
    tags_to_add = body.get("tags", [])
    branch = body.get("branch", "master")
    team = body.get("team", "CDP")

    if not testcase_oids or not tags_to_add:
        return jsonify({"error": "testcase_oids and tags are required"}), 400

    results = {"success": 0, "failed": 0, "errors": []}

    for oid in testcase_oids:
        try:
            url = f"{TCMS_WRITE_BASE}/all_test_cases/tags/{oid}"
            resp = requests.post(
                url,
                auth=_tcms_auth(),
                data=json.dumps({"tags": tags_to_add}),
                verify=False,
                timeout=30,
            )
            if resp.status_code in (200, 201):
                results["success"] += 1
            else:
                results["failed"] += 1
                results["errors"].append({"oid": oid, "status": resp.status_code})
        except Exception as exc:
            results["failed"] += 1
            results["errors"].append({"oid": oid, "error": str(exc)})

    data = _load_tc_data(branch, team)
    for tc in data.get("testcases", []):
        if tc.get("oid") in testcase_oids:
            existing = tc.get("tags", [])
            for tag in tags_to_add:
                if tag not in existing:
                    existing.append(tag)
            tc["tags"] = existing
    data["last_updated"] = datetime.utcnow().isoformat() + "Z"
    _save_tc_data(branch, team, data)

    return jsonify(results)


@app.route("/mcp/regression/testcase-mgmt/tags/delete", methods=["POST"])
@jwt_required
def testcase_mgmt_delete_tags():
    """Delete tags from selected test cases via TCMS write API."""
    body = request.get_json(force=True)
    testcase_oids = body.get("testcase_oids", [])
    tags_to_delete = body.get("tags", [])
    branch = body.get("branch", "master")
    team = body.get("team", "CDP")

    if not testcase_oids or not tags_to_delete:
        return jsonify({"error": "testcase_oids and tags are required"}), 400

    results = {"success": 0, "failed": 0, "errors": []}

    for oid in testcase_oids:
        try:
            url = f"{TCMS_WRITE_BASE}/all_test_cases/tags/{oid}"
            resp = requests.delete(
                url,
                auth=_tcms_auth(),
                data=json.dumps({"tags": tags_to_delete}),
                verify=False,
                timeout=30,
            )
            if resp.status_code in (200, 204):
                results["success"] += 1
            else:
                results["failed"] += 1
                results["errors"].append({"oid": oid, "status": resp.status_code})
        except Exception as exc:
            results["failed"] += 1
            results["errors"].append({"oid": oid, "error": str(exc)})

    data = _load_tc_data(branch, team)
    for tc in data.get("testcases", []):
        if tc.get("oid") in testcase_oids:
            tc["tags"] = [t for t in tc.get("tags", []) if t not in tags_to_delete]
    data["last_updated"] = datetime.utcnow().isoformat() + "Z"
    _save_tc_data(branch, team, data)

    return jsonify(results)


@app.route("/mcp/regression/testcase-mgmt/resource-spec/download", methods=["GET"])
@jwt_required
def testcase_mgmt_resource_spec_download():
    """Generate an Excel workbook grouping test cases by unique resource_spec."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    branch = request.args.get("branch", "master")
    team = request.args.get("team", "CDP")
    data = _load_tc_data(branch, team)
    testcases = data.get("testcases", [])

    if not testcases:
        return jsonify({"error": "No testcase data found. Reload from TCMS first."}), 404

    def _spec_fingerprint(spec_list):
        """Canonical string key for grouping (ignores resource 'name')."""
        cleaned = []
        for item in (spec_list or []):
            entry = {k: v for k, v in sorted(item.items()) if k != "name"}
            cleaned.append(json.dumps(entry, sort_keys=True))
        return "|".join(sorted(cleaned))

    def _format_resource(r):
        """Return a multi-line human-readable string for one resource item."""
        lines = []
        lines.append(f"name: {r.get('name', '—')}")
        lines.append(f"type: {r.get('type', '—')}")
        hw = r.get("hardware", {})
        if hw:
            lines.append(f"min_host_gb_ram: {hw.get('min_host_gb_ram', '—')}")
            lines.append(f"min_host_cpu_cores: {hw.get('min_host_cpu_cores', '—')}")
            lines.append(f"cluster_min_nodes: {hw.get('cluster_min_nodes', r.get('cluster_min_nodes', '—'))}")
        elif "cluster_min_nodes" in r:
            lines.append(f"cluster_min_nodes: {r['cluster_min_nodes']}")
        sc = r.get("scaleout", {})
        if sc:
            lines.append(f"scaleout.num_instances: {sc.get('num_instances', '—')}")
        deps = r.get("dependencies")
        if deps:
            lines.append(f"dependencies: {', '.join(deps)}")
        prov = r.get("provider")
        if isinstance(prov, dict):
            lines.append(f"provider.host: {prov.get('host', '—')}")
        can_run = r.get("can_run_on_provider")
        if can_run:
            lines.append(f"can_run_on_provider: {', '.join(can_run) if isinstance(can_run, list) else can_run}")
        for k, v in sorted(r.items()):
            if k not in ("name", "type", "hardware", "cluster_min_nodes", "scaleout",
                         "dependencies", "provider", "can_run_on_provider"):
                lines.append(f"{k}: {json.dumps(v) if isinstance(v, (dict, list)) else v}")
        return "\n".join(lines)

    def _format_spec_full(spec_list):
        """Return the full resource_spec as readable text (all resources)."""
        if not spec_list:
            return "None"
        blocks = []
        for idx, r in enumerate(spec_list, 1):
            blocks.append(f"[Resource {idx}]\n{_format_resource(r)}")
        return "\n\n".join(blocks)

    groups = defaultdict(list)
    for tc in testcases:
        key = _spec_fingerprint(tc.get("resource_spec", []))
        groups[key].append(tc)
    key_to_id = {}
    for idx, key in enumerate(sorted(groups.keys(), key=lambda k: -len(groups[k])), 1):
        key_to_id[key] = idx

    wb = Workbook()
    hdr_font = Font(bold=True, color="FFFFFF", size=11)
    hdr_fill = PatternFill(start_color="34495E", end_color="34495E", fill_type="solid")
    grp_fill = PatternFill(start_color="EAF2F8", end_color="EAF2F8", fill_type="solid")
    thin = Border(left=Side(style="thin"), right=Side(style="thin"),
                  top=Side(style="thin"), bottom=Side(style="thin"))
    wrap = Alignment(wrap_text=True, vertical="top")

    def _write_header(ws, headers):
        for ci, h in enumerate(headers, 1):
            c = ws.cell(row=1, column=ci, value=h)
            c.font = hdr_font
            c.fill = hdr_fill
            c.alignment = wrap
            c.border = thin

    # ── Sheet 1: Testcases ──
    ws1 = wb.active
    ws1.title = "Testcases"
    _write_header(ws1, ["Testcase Name", "Resource Spec", "Group ID"])

    row = 2
    for tc in sorted(testcases, key=lambda t: key_to_id.get(_spec_fingerprint(t.get("resource_spec", [])), 0)):
        spec = tc.get("resource_spec", [])
        gid = key_to_id.get(_spec_fingerprint(spec), 0)
        ws1.cell(row=row, column=1, value=tc.get("name", "")).border = thin
        ws1.cell(row=row, column=1).alignment = wrap
        c_spec = ws1.cell(row=row, column=2, value=_format_spec_full(spec))
        c_spec.border = thin
        c_spec.alignment = wrap
        c_gid = ws1.cell(row=row, column=3, value=gid)
        c_gid.border = thin
        c_gid.alignment = Alignment(horizontal="center", vertical="top")
        row += 1

    ws1.column_dimensions["A"].width = 80
    ws1.column_dimensions["B"].width = 70
    ws1.column_dimensions["C"].width = 12

    # ── Sheet 2: Grouped Resource Specs ──
    ws2 = wb.create_sheet("Grouped Resource Specs")
    _write_header(ws2, ["Group ID", "Testcase Count", "Resource Spec", "Testcase Names"])

    sorted_groups = sorted(key_to_id.items(), key=lambda kv: kv[1])
    for key, gid in sorted_groups:
        tcs = groups[key]
        spec_list = tcs[0].get("resource_spec", [])
        r = gid + 1
        ws2.cell(row=r, column=1, value=gid).border = thin
        ws2.cell(row=r, column=1).alignment = Alignment(horizontal="center", vertical="top")
        ws2.cell(row=r, column=2, value=len(tcs)).border = thin
        ws2.cell(row=r, column=2).alignment = Alignment(horizontal="center", vertical="top")
        c_spec = ws2.cell(row=r, column=3, value=_format_spec_full(spec_list))
        c_spec.border = thin
        c_spec.alignment = wrap
        tc_names = "\n".join(t.get("name", "") for t in tcs)
        c_names = ws2.cell(row=r, column=4, value=tc_names)
        c_names.border = thin
        c_names.alignment = wrap

    ws2.column_dimensions["A"].width = 12
    ws2.column_dimensions["B"].width = 14
    ws2.column_dimensions["C"].width = 70
    ws2.column_dimensions["D"].width = 80

    # ── Sheet 3: Resource Spec Detail (flat table) ──
    ws3 = wb.create_sheet("Resource Detail")
    detail_headers = [
        "Group ID", "Resource #", "name", "type",
        "min_host_gb_ram", "min_host_cpu_cores", "cluster_min_nodes",
        "scaleout.num_instances", "dependencies", "provider.host",
        "Extra Parameters",
    ]
    _write_header(ws3, detail_headers)
    KNOWN_KEYS = {"name", "type", "hardware", "cluster_min_nodes", "scaleout",
                  "dependencies", "provider", "can_run_on_provider", "can_run_on_hardware"}

    dr = 2
    for key, gid in sorted_groups:
        tcs = groups[key]
        spec_list = tcs[0].get("resource_spec", [])
        for ri, res in enumerate(spec_list, 1):
            hw = res.get("hardware", {})
            extras = {k: v for k, v in res.items() if k not in KNOWN_KEYS}
            extra_str = "; ".join(f"{k}={json.dumps(v) if isinstance(v, (dict, list)) else v}"
                                  for k, v in sorted(extras.items())) if extras else ""
            vals = [
                gid,
                ri,
                res.get("name", ""),
                res.get("type", ""),
                hw.get("min_host_gb_ram", ""),
                hw.get("min_host_cpu_cores", ""),
                hw.get("cluster_min_nodes", res.get("cluster_min_nodes", "")),
                (res.get("scaleout") or {}).get("num_instances", ""),
                ", ".join(res.get("dependencies", [])) if res.get("dependencies") else "",
                (res.get("provider") or {}).get("host", "") if isinstance(res.get("provider"), dict) else "",
                extra_str,
            ]
            for ci, v in enumerate(vals, 1):
                c = ws3.cell(row=dr, column=ci, value=v)
                c.border = thin
                c.alignment = wrap
            if ri == 1:
                for ci in range(1, len(vals) + 1):
                    ws3.cell(row=dr, column=ci).fill = grp_fill
            dr += 1

    ws3.column_dimensions["A"].width = 10
    ws3.column_dimensions["B"].width = 12
    ws3.column_dimensions["C"].width = 22
    ws3.column_dimensions["D"].width = 18
    ws3.column_dimensions["E"].width = 16
    ws3.column_dimensions["F"].width = 18
    ws3.column_dimensions["G"].width = 18
    ws3.column_dimensions["H"].width = 20
    ws3.column_dimensions["I"].width = 30
    ws3.column_dimensions["J"].width = 20
    ws3.column_dimensions["K"].width = 40

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"resource_spec_{branch}_{team}.xlsx"
    return send_file(buf, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name=filename)


@app.route("/mcp/regression/testcase-mgmt/resolve-job-profiles", methods=["POST"])
@jwt_required
def testcase_mgmt_resolve_job_profiles():
    """Search JITA job profiles by prefix and cross-reference with testcase test_sets."""
    from urllib.parse import quote

    body = request.get_json(force=True)
    branch = body.get("branch", "master")
    team = body.get("team", "CDP")
    jp_prefix = body.get("jp_prefix", "").strip()
    tc_names = body.get("testcase_names", [])

    if not jp_prefix:
        return jsonify({"error": "jp_prefix is required"}), 400

    data = _load_tc_data(branch, team)
    all_tcs = data.get("testcases", [])

    if tc_names:
        name_set = set(tc_names)
        target_tcs = [tc for tc in all_tcs if tc.get("name") in name_set]
    else:
        target_tcs = all_tcs

    if not target_tcs:
        return jsonify({"error": "No matching testcases found"}), 404

    raw_query = json.dumps({"name": {"$regex": f"^{jp_prefix}", "$options": "i"}})
    try:
        resp = requests.get(
            f"{JITA_BASE}/job_profiles",
            params={"raw_query": quote(raw_query), "limit": 200},
            auth=JITA_SVC_AUTH,
            verify=False,
            timeout=30,
        )
        resp.raise_for_status()
        job_profiles = resp.json().get("data", [])
    except Exception as exc:
        return jsonify({"error": f"JITA search failed: {exc}"}), 502

    if not job_profiles:
        return jsonify({
            "matched": [],
            "unmatched_testcases": [tc.get("name", "") for tc in target_tcs],
            "total_matched_testcases": 0,
            "total_unmatched_testcases": len(target_tcs),
            "job_profiles_found": 0,
        })

    jp_map = {}
    for jp in job_profiles:
        jp_id = jp.get("_id", {}).get("$oid", "") if isinstance(jp.get("_id"), dict) else str(jp.get("_id", ""))
        jp_name = jp.get("name", "")
        jp_test_sets = set()
        for ts in jp.get("test_sets", jp.get("test_set", [])):
            if isinstance(ts, str):
                jp_test_sets.add(ts.lower())
            elif isinstance(ts, dict):
                ts_name = ts.get("name", ts.get("test_set", ""))
                if ts_name:
                    jp_test_sets.add(str(ts_name).lower())
        jp_map[jp_id] = {"name": jp_name, "test_sets": jp_test_sets, "testcases": []}

    matched_tc_names = set()
    for tc in target_tcs:
        tc_test_sets = {s.lower() for s in tc.get("test_sets", []) if isinstance(s, str)}
        for jp_id, jp_info in jp_map.items():
            if tc_test_sets & jp_info["test_sets"]:
                jp_info["testcases"].append(tc.get("name", ""))
                matched_tc_names.add(tc.get("name", ""))

    matched = []
    for jp_id, jp_info in jp_map.items():
        if jp_info["testcases"]:
            matched.append({
                "job_profile_id": jp_id,
                "job_profile_name": jp_info["name"],
                "testcase_count": len(jp_info["testcases"]),
                "testcases": jp_info["testcases"],
            })
    matched.sort(key=lambda x: -x["testcase_count"])

    unmatched = [tc.get("name", "") for tc in target_tcs if tc.get("name", "") not in matched_tc_names]

    return jsonify({
        "matched": matched,
        "unmatched_testcases": unmatched,
        "total_matched_testcases": len(matched_tc_names),
        "total_unmatched_testcases": len(unmatched),
        "job_profiles_found": len(job_profiles),
    })


@app.route("/mcp/regression/testcase-mgmt/branches", methods=["GET"])
@jwt_required
def testcase_mgmt_branches():
    """Return available branches and teams for the testcase management module."""
    return jsonify({
        "branches": list(TESTCASE_MGMT_BRANCHES.keys()),
        "teams": TESTCASE_MGMT_TEAMS,
    })
# Dynamic Job Profile APIs
# ======================================================

@app.route("/mcp/regression/dynamic-jp/test-execution-history", methods=["POST"])
def dynamic_jp_test_execution_history():
    """Fetch detailed test execution history from JITA.

    Mirrors JITA /test_history: each row is one execution. When ``branch`` is set in
    the JSON body, results are restricted to that ``system_under_test.branch`` (query
    + case-insensitive post-filter). ``test_set`` and ``job_profile`` come from the
    **history row** (``test_set`` / ``test_set_name`` / ``AgaveTask`` and run
    ``label`` for JP), with task lookups only as fallback.
    """
    try:
        req_data = request.json or {}
        test_name = (req_data.get("test_name") or "").strip()
        page = int(req_data.get("page", 1))
        limit = int(req_data.get("limit", 50))
        sort_field = req_data.get("sort", "-start_time")
        branch_filter = (req_data.get("branch") or "").strip()

        if not test_name:
            return jsonify({"error": "test_name is required"}), 400

        raw_query = {"test.name": test_name}
        if branch_filter:
            raw_query["system_under_test.branch"] = branch_filter

        start = max(0, limit * (page - 1))
        raw_items = []
        total = 0

        logger.info(f"[test-exec-history] Querying: test.name={test_name}, page={page}")

        # Primary: GET agave_test_results (mirrors JITA frontend)
        try:
            params = {
                "start": start,
                "limit": limit,
                "sort": sort_field,
                "raw_query": json.dumps(raw_query),
            }
            resp = requests.get(
                f"{JITA_BASE}/agave_test_results",
                params=params,
                auth=JITA_SVC_AUTH,
                verify=False,
                timeout=90,
            )
            if resp.status_code == 200:
                jita_data = resp.json()
                raw_items = jita_data.get("data", [])
                total = jita_data.get("total", 0)
                logger.info(f"[test-exec-history] GET returned {total} total, {len(raw_items)} items")
        except requests.exceptions.Timeout:
            logger.warning("[test-exec-history] GET timed out, trying POST fallback")
        except Exception as e:
            logger.warning(f"[test-exec-history] GET failed: {e}")

        # Fallback: POST reports/agave_test_results
        if not raw_items:
            try:
                payload = {
                    "raw_query": raw_query,
                    "start": start,
                    "limit": limit,
                    "sort": sort_field,
                }
                resp2 = requests.post(
                    f"{JITA_BASE}/reports/agave_test_results",
                    json=payload,
                    auth=JITA_SVC_AUTH,
                    verify=False,
                    timeout=90,
                )
                if resp2.status_code == 200:
                    data2 = resp2.json()
                    raw_items = data2.get("data", [])
                    total = data2.get("total", data2.get("metadata", {}).get("total", 0))
                    logger.info(f"[test-exec-history] POST returned {total} total, {len(raw_items)} items")
            except requests.exceptions.Timeout:
                logger.warning("[test-exec-history] POST also timed out")
            except Exception as e:
                logger.warning(f"[test-exec-history] POST fallback failed: {e}")

        jita_total_pre_branch = total

        def _sut_branch(item):
            return (item.get("system_under_test") or {}).get("branch") or ""

        def _branch_matches(item):
            if not branch_filter:
                return True
            return (_sut_branch(item) or "").strip().lower() == branch_filter.lower()

        # Match UI: use history rows for the selected branch (backup if JITA query is loose)
        if branch_filter:
            raw_items = [it for it in raw_items if _branch_matches(it)]
            total = len(raw_items)
            logger.info(
                f"[test-exec-history] After branch filter {branch_filter!r}: {len(raw_items)} rows "
                f"(pre-filter total from JITA was {jita_total_pre_branch})"
            )

        def _oid(val):
            if isinstance(val, dict) and "$oid" in val:
                return val["$oid"]
            return str(val) if val else None

        def _date(val):
            if isinstance(val, dict) and "$date" in val:
                return val["$date"]
            return val

        def _test_set_name_from_embedded_agave(agt):
            """Per-result test set from AgaveTask embed only."""
            if not isinstance(agt, dict):
                return ""
            s = (agt.get("test_set_name") or "").strip()
            if s:
                return s
            tso = agt.get("test_set")
            if isinstance(tso, dict):
                s = (tso.get("name") or "").strip()
                if s:
                    return s
            elif isinstance(tso, str) and tso.strip():
                return tso.strip()
            return ""

        def _test_set_from_history_row(item, agt):
            """JITA test history row: prefer top-level + AgaveTask (same as /test_history table)."""
            if isinstance(item, dict):
                s = (item.get("test_set_name") or "").strip()
                if s:
                    return s
                tso = item.get("test_set")
                if isinstance(tso, dict):
                    s = (tso.get("name") or "").strip()
                    if s:
                        return s
                elif isinstance(tso, str) and tso.strip():
                    return tso.strip()
            return _test_set_name_from_embedded_agave(agt)

        def _label_to_jp_display(label):
            """Run label is the JP line on JITA history, e.g. Some_JP_Name-(42)."""
            if not label or not isinstance(label, str):
                return ""
            return re.sub(r"-\(\d+\)$", "", label.strip())

        def _jp_name_from_history_row(item, agt):
            """JP for display: JITA /test_history uses run label; prefer that over job_profile_name."""
            if isinstance(agt, dict):
                lab = (agt.get("label") or "").strip()
                if lab:
                    d = _label_to_jp_display(lab)
                    if d:
                        return d
                    return lab
                jn = (agt.get("job_profile_name") or "").strip()
                if jn:
                    return jn
            if isinstance(item, dict):
                lab = (item.get("label") or "").strip()
                if lab:
                    d = _label_to_jp_display(lab)
                    if d:
                        return d
                    return lab
                jn = (item.get("job_profile_name") or "").strip()
                if jn:
                    return jn
            return ""

        def _test_names_from_ts_doc_tests_field(tests_field):
            """Normalize JITA test_sets.tests entries to full testcase name strings."""
            out = set()
            if not isinstance(tests_field, list):
                return out
            for entry in tests_field:
                if isinstance(entry, str) and entry.strip():
                    out.add(entry.strip())
                elif isinstance(entry, dict):
                    n = entry.get("name") or entry.get("test") or entry.get("path")
                    if isinstance(n, str) and n.strip():
                        out.add(n.strip())
            return out

        # Collect unique task IDs so we can look up test_set / job_profile
        unique_task_ids = list({
            _oid(item.get("agave_task_id"))
            for item in raw_items
            if _oid(item.get("agave_task_id"))
        })

        task_info = {}  # task_id -> {test_set_name, job_profile_name, branch}
        if unique_task_ids:
            try:
                tids_for_query = [{"$oid": tid} for tid in unique_task_ids[:100]]
                rq = json.dumps({"_id": {"$in": tids_for_query}})
                task_resp = requests.get(
                    f"{JITA_BASE}/tasks",
                    params={
                        "raw_query": rq,
                        "limit": len(tids_for_query),
                        "only": "_id,test_sets,label,branch,job_profile",
                    },
                    auth=JITA_SVC_AUTH,
                    verify=False,
                    timeout=60,
                )
                if task_resp.status_code == 200:
                    # Collect JP IDs to resolve names in bulk
                    jp_id_map = {}  # jp_oid -> None (will fill with name)
                    task_items = task_resp.json().get("data", [])
                    for t in task_items:
                        jp_ref = t.get("job_profile")
                        jp_id = _oid(jp_ref) if jp_ref else None
                        if jp_id:
                            jp_id_map[jp_id] = None

                    # Bulk-fetch JP names
                    if jp_id_map:
                        try:
                            jp_ids_for_q = [{"$oid": jid} for jid in list(jp_id_map.keys())[:100]]
                            jp_rq = json.dumps({"_id": {"$in": jp_ids_for_q}})
                            jp_resp = requests.get(
                                f"{JITA_BASE}/job_profiles",
                                params={"raw_query": jp_rq, "limit": len(jp_ids_for_q), "only": "_id,name"},
                                auth=JITA_SVC_AUTH, verify=False, timeout=30,
                            )
                            if jp_resp.status_code == 200:
                                for jp_item in jp_resp.json().get("data", []):
                                    jid = _oid(jp_item.get("_id"))
                                    if jid:
                                        jp_id_map[jid] = jp_item.get("name", "")
                        except Exception as e:
                            logger.warning(f"[test-exec-history] Failed to bulk-fetch JP names: {e}")

                    for t in task_items:
                        tid = _oid(t.get("_id"))
                        if not tid:
                            continue
                        ts_list = t.get("test_sets") or []
                        ts_name = ts_list[0].get("name", "") if ts_list else ""
                        ts_refs = []
                        for el in ts_list:
                            if not isinstance(el, dict):
                                continue
                            rid = _oid(el.get("_id") or el)
                            nm = (el.get("name") or "").strip()
                            if rid or nm:
                                ts_refs.append({"id": rid, "name": nm})

                        # Get JP name: prefer resolved name, fall back to label parsing
                        jp_ref = t.get("job_profile")
                        jp_id = _oid(jp_ref) if jp_ref else None
                        jp_name = jp_id_map.get(jp_id, "") if jp_id else ""
                        if not jp_name:
                            label = t.get("label", "")
                            jp_name = re.sub(r"-\(\d+\)$", "", label) if label else ""

                        task_info[tid] = {
                            "test_set_name": ts_name,
                            "ts_refs": ts_refs,
                            "job_profile_name": jp_name,
                            "branch": t.get("branch", ""),
                        }
                    logger.info(f"[test-exec-history] Fetched info for {len(task_info)} tasks, {len(jp_id_map)} unique JPs")
            except Exception as e:
                logger.warning(f"[test-exec-history] Failed to fetch task details: {e}")

        # When a task has multiple test sets and a row has no embedded test set, match by testcase name.
        ts_id_to_testnames = {}
        ts_id_to_tsname = {}
        if task_info:
            need_ids = set()
            for _tid, tmeta in task_info.items():
                refs = tmeta.get("ts_refs") or []
                if len(refs) > 1:
                    for r in refs:
                        rid = r.get("id")
                        if rid:
                            need_ids.add(rid)
            if need_ids:
                from urllib.parse import quote

                for chunk in (
                    list(need_ids)[i : i + 40] for i in range(0, min(len(need_ids), 200), 40)
                ):
                    if not chunk:
                        break
                    try:
                        tq = json.dumps({"_id": {"$in": [{"$oid": x} for x in chunk]}})
                        tsr = requests.get(
                            f"{JITA_BASE}/test_sets",
                            params={
                                "raw_query": quote(tq),
                                "limit": len(chunk),
                                "only": "_id,tests,name",
                            },
                            auth=JITA_SVC_AUTH,
                            verify=False,
                            timeout=45,
                        )
                        if tsr.status_code == 200:
                            for d in tsr.json().get("data", []):
                                oid = _oid(d.get("_id"))
                                if oid:
                                    ts_id_to_testnames[oid] = _test_names_from_ts_doc_tests_field(
                                        d.get("tests")
                                    )
                                    nm = (d.get("name") or "").strip()
                                    if nm:
                                        ts_id_to_tsname[oid] = nm
                    except Exception as e:
                        logger.warning(f"[test-exec-history] Batch test_sets fetch failed: {e}")

        rows = []
        seen_pairs = set()
        unique_pairs = []
        for item in raw_items:
            sut = item.get("system_under_test") or {}
            agave_task = item.get("AgaveTask") or {}
            exec_id = _oid(item.get("agave_task_id"))
            ti = task_info.get(exec_id, {})
            # Test set: JITA history row first (matches /test_history), then multi-TS membership, then task.
            row_ts = _test_set_from_history_row(item, agave_task)
            if not row_ts:
                refs = ti.get("ts_refs") or []
                if len(refs) > 1 and test_name:
                    for r in refs:
                        rid = r.get("id")
                        if not rid:
                            continue
                        tnames = ts_id_to_testnames.get(rid)
                        if tnames and test_name in tnames:
                            row_ts = (r.get("name") or "").strip() or ts_id_to_tsname.get(rid, "")
                            if row_ts:
                                break
            ts = row_ts or ti.get("test_set_name", "")
            # JP: history label / row fields first (user expects test set + label as on JITA for that branch).
            jp = _jp_name_from_history_row(item, agave_task) or ti.get("job_profile_name", "")
            rows.append({
                "id": _oid(item.get("_id")),
                "execution_id": exec_id,
                "branch": sut.get("branch", "") or ti.get("branch", ""),
                "test_set": ts,
                "job_profile": jp,
                "date_started": _date(item.get("start_time")),
                "date_ended": _date(item.get("end_time")),
                "status": item.get("status", ""),
                "jira_tickets": item.get("jira_tickets") or [],
                "exception_summary": item.get("exception_summary") or "",
                "label": agave_task.get("label", ""),
            })
            pair_key = f"{ts}|||{jp}"
            if (ts or jp) and pair_key not in seen_pairs:
                seen_pairs.add(pair_key)
                unique_pairs.append({"test_set": ts, "job_profile": jp})

        return jsonify({
            "success": True,
            "data": rows,
            "unique_pairs": unique_pairs,
            "total": total,
            "page": page,
            "limit": limit,
        })
    except requests.exceptions.Timeout:
        logger.warning("[test-exec-history] Timeout querying JITA")
        return jsonify({"error": "JITA request timed out", "data": [], "total": 0})
    except Exception as e:
        logger.error(f"[test-exec-history] Error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/mcp/regression/dynamic-jp/testcase-history", methods=["POST"])
def dynamic_jp_testcase_history():
    """Fetch testcase run history from JITA and return associated JPs and test sets."""
    try:
        req_data = request.json
        if not req_data:
            return jsonify({"error": "Request body is required (JSON)"}), 400

        testcase_names = req_data.get("testcase_names", [])
        branch = req_data.get("branch", "master")

        if not testcase_names or not isinstance(testcase_names, list):
            return jsonify({"error": "testcase_names must be a non-empty list"}), 400

        # Sanitize: limit to 20 testcases to avoid overloading
        testcase_names = [str(tc) for tc in testcase_names[:20]]

        results = []

        def extract_search_keyword(tc_name):
            """Extract a meaningful keyword from a fully qualified testcase name.
            e.g. 'cdp.stargate.storage_policy.api.test_storage_policy...' -> 'storage_policy'
            """
            parts = tc_name.replace(".", " ").replace("/", " ").split()
            # Pick the most specific non-generic part (skip cdp, stargate, test_, api, etc.)
            skip = {"cdp", "stargate", "test", "api", "tests", "module", "class", "self"}
            for part in parts:
                cleaned = re.sub(r"^test_", "", part)
                if cleaned and cleaned.lower() not in skip and len(cleaned) > 3:
                    return cleaned
            # Fallback: use the 3rd component if available
            dot_parts = tc_name.split(".")
            if len(dot_parts) >= 3:
                return dot_parts[2]
            return tc_name.split(".")[-1] if "." in tc_name else tc_name

        def _parse_jita_items(data, kind="jp"):
            """Parse JITA response data into a flat list of dicts."""
            items = []
            raw_list = data.get("data", []) if isinstance(data, dict) else []
            for item in raw_list:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("_id")
                if isinstance(item_id, dict) and "$oid" in item_id:
                    item_id = item_id["$oid"]
                elif not isinstance(item_id, str):
                    item_id = str(item_id) if item_id else None
                if kind == "jp":
                    items.append({
                        "_id": item_id,
                        "name": item.get("name", ""),
                        "description": item.get("description", ""),
                    })
                else:
                    items.append({
                        "_id": item_id,
                        "path": item.get("path", "") or "",
                        "name": item.get("name", "") or "",
                        "test_args": item.get("test_args", "") or "",
                        "framework_args": item.get("framework_args", "") or "",
                    })
            return items

        def _search_jps(keyword, branch_val):
            """Search job_profiles by keyword + branch, fall back to keyword only."""
            from urllib.parse import quote
            jp_details = []
            try:
                jp_pattern = f".*{re.escape(keyword)}.*{re.escape(branch_val)}"
                raw_q = json.dumps({"name": {"$regex": jp_pattern, "$options": "i"}})
                resp = requests.get(
                    f"{JITA_BASE}/job_profiles",
                    params={"raw_query": quote(raw_q), "limit": 10, "only": "_id,name,description"},
                    auth=JITA_SVC_AUTH, verify=False, timeout=45
                )
                if resp.status_code == 200:
                    jp_details = _parse_jita_items(resp.json(), "jp")
            except (requests.exceptions.RequestException, ValueError) as e:
                logger.warning(f"JP search failed for '{keyword}+{branch_val}': {e}")

            if not jp_details:
                try:
                    raw_q2 = json.dumps({"name": {"$regex": f".*{re.escape(keyword)}.*", "$options": "i"}})
                    resp2 = requests.get(
                        f"{JITA_BASE}/job_profiles",
                        params={"raw_query": quote(raw_q2), "limit": 10, "only": "_id,name,description"},
                        auth=JITA_SVC_AUTH, verify=False, timeout=45
                    )
                    if resp2.status_code == 200:
                        jp_details = _parse_jita_items(resp2.json(), "jp")
                except (requests.exceptions.RequestException, ValueError) as e:
                    logger.warning(f"JP fallback search failed for '{keyword}': {e}")
            return jp_details

        def _search_test_sets(keyword):
            """Search test_sets by keyword."""
            from urllib.parse import quote
            try:
                raw_q = json.dumps({"name": {"$regex": f".*{re.escape(keyword)}.*", "$options": "i"}})
                resp = requests.get(
                    f"{JITA_BASE}/test_sets",
                    params={"raw_query": quote(raw_q), "limit": 10, "only": "_id,name,path,test_args,framework_args"},
                    auth=JITA_SVC_AUTH, verify=False, timeout=60
                )
                if resp.status_code == 200:
                    return _parse_jita_items(resp.json(), "ts")
            except (requests.exceptions.RequestException, ValueError) as e:
                logger.warning(f"Test set search failed for '{keyword}': {e}")
            return []

        def fetch_single_testcase(tc_name):
            tc_name = tc_name.strip() if isinstance(tc_name, str) else ""
            if not tc_name:
                return None
            try:
                keyword = extract_search_keyword(tc_name)
                logger.info(f"[dynamic-jp] Searching for testcase '{tc_name}' using keyword '{keyword}' on branch '{branch}'")

                # Run JP and TS searches in parallel for speed
                from concurrent.futures import ThreadPoolExecutor, as_completed
                jp_details = []
                ts_details = []
                with ThreadPoolExecutor(max_workers=2) as mini_pool:
                    jp_future = mini_pool.submit(_search_jps, keyword, branch)
                    ts_future = mini_pool.submit(_search_test_sets, keyword)
                    try:
                        jp_details = jp_future.result(timeout=90)
                    except Exception as e:
                        logger.warning(f"JP parallel search error: {e}")
                    try:
                        ts_details = ts_future.result(timeout=90)
                    except Exception as e:
                        logger.warning(f"TS parallel search error: {e}")

                return {
                    "testcase": tc_name,
                    "keyword": keyword,
                    "runs": [],
                    "job_profiles": jp_details,
                    "test_sets": ts_details,
                }
            except requests.exceptions.Timeout:
                logger.warning(f"Timeout fetching history for {tc_name}")
                return {"testcase": tc_name, "error": "Request timed out", "runs": [], "job_profiles": [], "test_sets": []}
            except requests.exceptions.ConnectionError:
                logger.warning(f"Connection error fetching history for {tc_name}")
                return {"testcase": tc_name, "error": "Connection error to JITA", "runs": [], "job_profiles": [], "test_sets": []}
            except Exception as e:
                logger.error(f"Error fetching history for {tc_name}: {e}")
                return {"testcase": tc_name, "error": str(e), "runs": [], "job_profiles": [], "test_sets": []}

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(fetch_single_testcase, tc): tc for tc in testcase_names}
            for future in as_completed(futures):
                try:
                    result = future.result(timeout=60)
                    if result:
                        results.append(result)
                except Exception as e:
                    logger.error(f"Future exception in testcase-history: {e}")

        return jsonify({"success": True, "results": results})
    except Exception as e:
        logger.error(f"Error in dynamic-jp testcase-history: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


def _dynamic_jp_ts_prefix_for_date(dyn_date_str=None):
    """Prefixes for auto-named dynamic JP/TS: User_Dyn_<YYYYMMDD>_JP_ / User_Dyn_<YYYYMMDD>_TS_."""
    if (
        dyn_date_str
        and isinstance(dyn_date_str, str)
        and len(dyn_date_str.strip()) == 8
        and dyn_date_str.strip().isdigit()
    ):
        date_str = dyn_date_str.strip()
    else:
        date_str = datetime.now().strftime("%Y%m%d")
    jp_p = f"User_Dyn_{date_str}_JP_"
    ts_p = f"User_Dyn_{date_str}_TS_"
    return jp_p, ts_p


# Monotonic sequence per YYYYMMDD; increments on every /dynamic-jp/create attempt (success or fail after bump).
DYN_NAME_SEQ_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".dyn_name_sequence.json")
_dyn_name_seq_lock = threading.Lock()


def _dyn_name_seq_date_key(dyn_name_date):
    d = (dyn_name_date or "").strip()
    if d and len(d) == 8 and d.isdigit():
        return d
    return datetime.now().strftime("%Y%m%d")


def _load_dyn_name_seq() -> dict:
    try:
        with open(DYN_NAME_SEQ_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                out = {}
                for k, v in data.items():
                    ks = str(k)
                    if len(ks) == 8 and ks.isdigit():
                        try:
                            out[ks] = int(v)
                        except (TypeError, ValueError):
                            pass
                return out
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return {}


def _save_dyn_name_seq(data: dict) -> None:
    try:
        with open(DYN_NAME_SEQ_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=0)
    except OSError as e:
        logger.warning(f"Could not write {DYN_NAME_SEQ_FILE}: {e}")


def _peek_next_dyn_name_seq(dyn_name_date):
    """Next sequence number to suggest (1 + last used); does not modify store."""
    key = _dyn_name_seq_date_key(dyn_name_date)
    with _dyn_name_seq_lock:
        data = _load_dyn_name_seq()
        return int(data.get(key, 0)) + 1


def _bump_dyn_name_seq(dyn_name_date):
    """Increment and return the sequence to use for this /create call (one bump per create request)."""
    key = _dyn_name_seq_date_key(dyn_name_date)
    with _dyn_name_seq_lock:
        data = _load_dyn_name_seq()
        n = int(data.get(key, 0)) + 1
        data[key] = n
        _save_dyn_name_seq(data)
    logger.info(f"[dyn-seq] date={key} last_reserved={n}")
    return n


def _apply_reserved_seq_to_dyn_custom_name(name: str, date_key: str, seq: int) -> str:
    """Rewrite User_Dyn_{date}_JP_n / _TS_n to use `seq` (per create attempt)."""
    if not name or not date_key:
        return name
    s = re.sub(
        rf"(User_Dyn_{re.escape(date_key)}_JP_)\d+",
        rf"\g<1>{seq}",
        name,
        count=1,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        rf"(User_Dyn_{re.escape(date_key)}_TS_)\d+",
        rf"\g<1>{seq}",
        s,
        count=1,
        flags=re.IGNORECASE,
    )
    return s


# Default JITA test set "framework options" (framework_args JSON object) for dynamic creates.
# On clone, merge with source: source keys win; any default key missing in source is added.
# If a cloned TS already has all of these keys (order irrelevant), do not change framework_args.
DYN_TESTSET_DEFAULT_FRAMEWORK_OPTS = {
    "no_log_collection": False,
    "scatter_logs": "cascade",
    "use_logbay": "",
    "log_level": "DEBUG",
}


def _framework_args_value_to_dict(val):
    """Normalize test set framework_args from JITA (JSON string or dict) to a dict."""
    if val is None:
        return {}
    if isinstance(val, dict):
        return dict(val)
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return {}
        try:
            parsed = json.loads(s)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
    return {}


def _merge_dyn_testset_framework_args(existing):
    """Apply defaults, then overlay existing (from clone or prior fetch)."""
    ex = _framework_args_value_to_dict(existing)
    return {**DYN_TESTSET_DEFAULT_FRAMEWORK_OPTS, **ex}


def _framework_args_dict_has_all_dyn_default_keys(framework_args_val):
    """True if every key in DYN_TESTSET_DEFAULT_FRAMEWORK_OPTS exists (order does not matter)."""
    d = _framework_args_value_to_dict(framework_args_val)
    return all(k in d for k in DYN_TESTSET_DEFAULT_FRAMEWORK_OPTS)


def _apply_default_framework_options_to_test_set_payload(ts_payload):
    """Mutate a POST /test_sets body: set framework_args / frameworkArgs to merged JSON string."""
    if not isinstance(ts_payload, dict):
        return ts_payload
    existing = ts_payload.get("framework_args")
    if existing in (None, ""):
        existing = ts_payload.get("frameworkArgs")
    merged = _merge_dyn_testset_framework_args(existing)
    fa_str = json.dumps(merged, separators=(",", ":"))
    ts_payload["framework_args"] = fa_str
    ts_payload["frameworkArgs"] = fa_str
    return ts_payload


@app.route("/mcp/regression/dynamic-jp/check-existing", methods=["POST"])
def dynamic_jp_check_existing():
    """Search for existing dynamic JP/TS by name prefix; suggest next numeric suffix.

    Defaults to User_Dyn_<YYYYMMDD>_JP_ / User_Dyn_<YYYYMMDD>_TS_ (local server date, or
    optional dyn_name_date=YYYYMMDD). Clients may still pass jp_pattern / ts_pattern.
    """
    try:
        req_data = request.json or {}
        dyn_name_date = (req_data.get("dyn_name_date") or "").strip()
        if dyn_name_date and (len(dyn_name_date) != 8 or not dyn_name_date.isdigit()):
            return jsonify({"error": "dyn_name_date must be YYYYMMDD"}), 400

        def_jp, def_ts = _dynamic_jp_ts_prefix_for_date(dyn_name_date or None)
        jp_raw = (req_data.get("jp_pattern") or "").strip() or def_jp
        ts_raw = (req_data.get("ts_pattern") or "").strip() or def_ts

        # Sanitize patterns to avoid regex injection
        jp_pattern = re.escape(jp_raw)
        ts_pattern = re.escape(ts_raw)

        from urllib.parse import quote

        existing_jps = []
        try:
            raw_query = json.dumps({"name": {"$regex": f"^{jp_pattern}", "$options": "i"}})
            params = {"raw_query": quote(raw_query), "limit": 100}
            resp = requests.get(
                f"{JITA_BASE}/job_profiles",
                params=params,
                auth=JITA_SVC_AUTH,
                verify=False,
                timeout=30
            )
            if resp.status_code == 200:
                resp_data = resp.json()
                jp_list = resp_data.get("data", []) if isinstance(resp_data, dict) else []
                for jp in jp_list:
                    if not isinstance(jp, dict):
                        continue
                    jp_id = jp.get("_id")
                    if isinstance(jp_id, dict) and "$oid" in jp_id:
                        jp_id = jp_id["$oid"]
                    elif not isinstance(jp_id, str):
                        jp_id = str(jp_id) if jp_id else None
                    existing_jps.append({
                        "_id": jp_id,
                        "name": jp.get("name", ""),
                        "description": jp.get("description", ""),
                        "created_at": jp.get("created_at"),
                    })
            else:
                logger.warning(f"check-existing: JP search returned {resp.status_code}")
        except (requests.exceptions.RequestException, ValueError) as e:
            logger.warning(f"check-existing: Failed to fetch JPs: {e}")

        existing_ts = []
        try:
            raw_query_ts = json.dumps({"name": {"$regex": f"^{ts_pattern}", "$options": "i"}})
            params_ts = {"raw_query": quote(raw_query_ts), "limit": 100}
            resp_ts = requests.get(
                f"{JITA_BASE}/test_sets",
                params=params_ts,
                auth=JITA_SVC_AUTH,
                verify=False,
                timeout=30
            )
            if resp_ts.status_code == 200:
                resp_ts_data = resp_ts.json()
                ts_list = resp_ts_data.get("data", []) if isinstance(resp_ts_data, dict) else []
                for ts in ts_list:
                    if not isinstance(ts, dict):
                        continue
                    ts_id = ts.get("_id")
                    if isinstance(ts_id, dict) and "$oid" in ts_id:
                        ts_id = ts_id["$oid"]
                    elif not isinstance(ts_id, str):
                        ts_id = str(ts_id) if ts_id else None
                    existing_ts.append({
                        "_id": ts_id,
                        "name": ts.get("name", ""),
                        "description": ts.get("description", ""),
                    })
            else:
                logger.warning(f"check-existing: TS search returned {resp_ts.status_code}")
        except (requests.exceptions.RequestException, ValueError) as e:
            logger.warning(f"check-existing: Failed to fetch test sets: {e}")

        next_jp_num = 1
        next_ts_num = 1
        for jp in existing_jps:
            name = jp.get("name", "")
            parts = name.rsplit("_", 1)
            if len(parts) == 2:
                try:
                    num = int(parts[1])
                    next_jp_num = max(next_jp_num, num + 1)
                except (ValueError, TypeError):
                    pass
        for ts in existing_ts:
            name = ts.get("name", "")
            parts = name.rsplit("_", 1)
            if len(parts) == 2:
                try:
                    num = int(parts[1])
                    next_ts_num = max(next_ts_num, num + 1)
                except (ValueError, TypeError):
                    pass

        date_key = dyn_name_date if (dyn_name_date and len(dyn_name_date) == 8 and dyn_name_date.isdigit()) else datetime.now().strftime("%Y%m%d")
        seq_peek = _peek_next_dyn_name_seq(date_key)
        # Never suggest a number below JITA reality or our next reserved slot
        next_both = max(next_jp_num, next_ts_num, seq_peek)
        next_jp_num = next_both
        next_ts_num = next_both

        return jsonify({
            "success": True,
            "job_profiles": existing_jps,
            "test_sets": existing_ts,
            "next_jp_number": next_jp_num,
            "next_ts_number": next_ts_num,
            "jp_name_prefix": jp_raw,
            "ts_name_prefix": ts_raw,
        })
    except Exception as e:
        logger.error(f"Error in dynamic-jp check-existing: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/mcp/regression/dynamic-jp/fetch-testset", methods=["POST"])
def dynamic_jp_fetch_testset():
    """Fetch a test set's details (test_args, framework_args) by ID or path."""
    try:
        req_data = request.json
        if not req_data:
            return jsonify({"error": "Request body is required (JSON)"}), 400

        testset_id = req_data.get("testset_id")
        testset_path = req_data.get("testset_path")

        if not testset_id and not testset_path:
            return jsonify({"error": "testset_id or testset_path is required"}), 400

        # Sanitize inputs
        if testset_id:
            testset_id = str(testset_id).strip()
        if testset_path:
            testset_path = str(testset_path).strip()

        try:
            if testset_id:
                resp = requests.get(
                    f"{JITA_BASE}/test_sets/{testset_id}",
                    auth=JITA_SVC_AUTH,
                    verify=False,
                    timeout=30
                )
            else:
                from urllib.parse import quote
                raw_query = json.dumps({"path": testset_path})
                params = {"raw_query": quote(raw_query), "limit": 1}
                resp = requests.get(
                    f"{JITA_BASE}/test_sets",
                    params=params,
                    auth=JITA_SVC_AUTH,
                    verify=False,
                    timeout=30
                )
        except requests.exceptions.Timeout:
            return jsonify({"error": "Request to JITA timed out"}), 504
        except requests.exceptions.ConnectionError:
            return jsonify({"error": "Could not connect to JITA API"}), 503

        if resp.status_code != 200:
            return jsonify({"error": f"JITA API error: {resp.status_code}"}), 500

        try:
            data = resp.json()
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid JSON response from JITA"}), 500

        ts_data = data.get("data", {}) if isinstance(data, dict) else {}
        if isinstance(ts_data, list):
            ts_data = ts_data[0] if ts_data else {}
        if not isinstance(ts_data, dict):
            ts_data = {}

        ts_id = ts_data.get("_id")
        if isinstance(ts_id, dict) and "$oid" in ts_id:
            ts_id = ts_id["$oid"]
        elif ts_id and not isinstance(ts_id, str):
            ts_id = str(ts_id)

        tests = ts_data.get("tests", [])
        if not isinstance(tests, list):
            tests = []

        return jsonify({
            "success": True,
            "test_set": {
                "_id": ts_id,
                "name": ts_data.get("name", "") or "",
                "path": ts_data.get("path", "") or "",
                "test_args": ts_data.get("test_args", "") or "",
                "framework_args": ts_data.get("framework_args", "") or "",
                "tests": tests,
                "description": ts_data.get("description", "") or "",
            }
        })
    except Exception as e:
        logger.error(f"Error in dynamic-jp fetch-testset: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


JARVIS_BASE = "https://jarvis.eng.nutanix.com/api/v1"


@app.route("/mcp/regression/dynamic-jp/resolve-names", methods=["POST"])
def dynamic_jp_resolve_names():
    """Resolve JP and/or test set names to their JITA IDs."""
    try:
        req_data = request.json
        if not req_data:
            return jsonify({"error": "Request body is required (JSON)"}), 400

        jp_name = (req_data.get("jp_name") or "").strip()
        ts_name = (req_data.get("ts_name") or "").strip()

        if not jp_name and not ts_name:
            return jsonify({"error": "At least one of jp_name or ts_name is required"}), 400

        from urllib.parse import quote

        def _oid(val):
            if isinstance(val, dict) and "$oid" in val:
                return val["$oid"]
            return str(val) if val else None

        result = {"jp": None, "ts": None}

        if jp_name:
            try:
                raw_q = json.dumps({"name": jp_name})
                resp = requests.get(
                    f"{JITA_BASE}/job_profiles",
                    params={"raw_query": quote(raw_q), "limit": 1, "only": "_id,name,description,tags,tester_tags"},
                    auth=JITA_SVC_AUTH, verify=False, timeout=30,
                )
                logger.info(f"[resolve-names] JP lookup for '{jp_name}': HTTP {resp.status_code}")
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("data", []) if isinstance(data, dict) else []
                    if items and isinstance(items, list) and isinstance(items[0], dict):
                        item = items[0]
                        result["jp"] = {
                            "_id": _oid(item.get("_id")),
                            "name": item.get("name", ""),
                            "description": item.get("description", ""),
                            "tags": item.get("tags", []) or [],
                            "tester_tags": item.get("tester_tags", []) or [],
                        }
                else:
                    logger.warning(f"[resolve-names] JP search returned {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                logger.warning(f"[resolve-names] Failed to resolve JP '{jp_name}': {e}")

        if ts_name:
            try:
                raw_q = json.dumps({"name": ts_name})
                resp = requests.get(
                    f"{JITA_BASE}/test_sets",
                    params={"raw_query": quote(raw_q), "limit": 1, "only": "_id,name,test_args,framework_args"},
                    auth=JITA_SVC_AUTH, verify=False, timeout=30,
                )
                logger.info(f"[resolve-names] TS lookup for '{ts_name}': HTTP {resp.status_code}")
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("data", []) if isinstance(data, dict) else []
                    if items and isinstance(items, list) and isinstance(items[0], dict):
                        item = items[0]
                        ta = item.get("test_args") or item.get("testArgs") or ""
                        fa = item.get("framework_args") or item.get("frameworkArgs") or ""
                        result["ts"] = {
                            "_id": _oid(item.get("_id")),
                            "name": item.get("name", ""),
                            "test_args": str(ta).strip() if ta is not None else "",
                            "framework_args": str(fa).strip() if fa is not None else "",
                        }
                else:
                    logger.warning(f"[resolve-names] TS search returned {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                logger.warning(f"[resolve-names] Failed to resolve TS '{ts_name}': {e}")

        return jsonify({"success": True, **result})
    except Exception as e:
        logger.error(f"Error in dynamic-jp resolve-names: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/mcp/regression/dynamic-jp/search-node-pools", methods=["POST"])
def dynamic_jp_search_node_pools():
    """Search Jarvis node pools by name keyword."""
    try:
        req_data = request.json or {}
        query = (req_data.get("query") or "").strip()
        if len(query) < 2:
            return jsonify({"pools": []})

        tokens = re.split(r'[\s_\-]+', query)
        tokens = [t for t in tokens if t]

        primary = max(tokens, key=len) if tokens else query
        pattern = re.escape(primary)

        raw_q = json.dumps({"name": {"$regex": pattern, "$options": "i"}})
        resp = requests.get(
            f"{JARVIS_BASE}/pools",
            params={"raw_query": raw_q, "limit": 100},
            auth=JITA_SVC_AUTH, verify=False, timeout=15
        )
        pools = []
        if resp.status_code == 200:
            for item in resp.json().get("data", []):
                name = item.get("name") or ""
                if not name or name in pools:
                    continue
                name_lower = name.lower()
                if all(t.lower() in name_lower for t in tokens):
                    pools.append(name)
        return jsonify({"pools": pools})
    except requests.exceptions.Timeout:
        return jsonify({"error": "Timed out searching node pools", "pools": []}), 504
    except Exception as e:
        logger.error(f"Error searching node pools: {e}", exc_info=True)
        return jsonify({"error": str(e), "pools": []}), 500


@app.route("/mcp/regression/dynamic-jp/search-branches", methods=["POST"])
def dynamic_jp_search_branches():
    """Search JITA branches by name."""
    try:
        req_data = request.json or {}
        query = (req_data.get("query") or "").strip()
        if len(query) < 2:
            return jsonify({"branches": []})

        pattern = re.escape(query)
        raw_q = json.dumps({"name": {"$regex": pattern, "$options": "i"}})
        resp = requests.get(
            f"{JITA_BASE}/branches",
            params={"raw_query": raw_q, "limit": 20},
            auth=JITA_SVC_AUTH, verify=False, timeout=15
        )
        branches = []
        if resp.status_code == 200:
            for item in resp.json().get("data", []):
                name = item.get("name") or ""
                if name and name not in branches:
                    branches.append(name)
        # Sort so exact-prefix matches come first
        q_lower = query.lower()
        branches.sort(key=lambda b: (0 if b.lower().startswith(q_lower) else 1, b.lower()))
        return jsonify({"branches": branches})
    except requests.exceptions.Timeout:
        return jsonify({"error": "Timed out", "branches": []}), 504
    except Exception as e:
        logger.error(f"Error searching branches: {e}", exc_info=True)
        return jsonify({"error": str(e), "branches": []}), 500


@app.route("/mcp/regression/dynamic-jp/search-clusters", methods=["POST"])
def dynamic_jp_search_clusters():
    """Search JITA clusters by name or IP."""
    try:
        req_data = request.json or {}
        query = (req_data.get("query") or "").strip()
        if len(query) < 2:
            return jsonify({"clusters": []})

        pattern = re.escape(query)
        raw_q = json.dumps({"name": {"$regex": pattern, "$options": "i"}})
        resp = requests.get(
            f"{JITA_BASE}/clusters",
            params={"raw_query": raw_q, "limit": 20},
            auth=JITA_SVC_AUTH, verify=False, timeout=15
        )
        clusters = []
        seen = set()
        if resp.status_code == 200:
            for item in resp.json().get("data", []):
                name = item.get("name") or ""
                if not name or name in seen:
                    continue
                seen.add(name)
                clusters.append({
                    "name": name,
                    "status": item.get("status", ""),
                })
        return jsonify({"clusters": clusters})
    except requests.exceptions.Timeout:
        return jsonify({"error": "Timed out searching clusters", "clusters": []}), 504
    except Exception as e:
        logger.error(f"Error searching clusters: {e}", exc_info=True)
        return jsonify({"error": str(e), "clusters": []}), 500


@app.route("/mcp/regression/dynamic-jp/create", methods=["POST"])
def dynamic_jp_create():
    """Create a dynamic JP and test set. Two modes:
    - create_fresh=True: brand-new JP+TS from scratch with the given testcases
    - create_fresh=False: clone from source_jp_id, optionally copying test_args from source_testset_id
    """
    try:
        req_data = request.json
        if not req_data:
            return jsonify({"error": "Request body is required (JSON)"}), 400

        create_fresh = bool(req_data.get("create_fresh", False))
        source_jp_id = req_data.get("source_jp_id")
        source_testset_id = req_data.get("source_testset_id")
        nos_branch = req_data.get("nos_branch", "master") or "master"
        nos_tag = req_data.get("nos_tag", "Latest Smoke Passed") or "Latest Smoke Passed"
        pc_branch = req_data.get("pc_branch", "master") or "master"
        pc_tag = req_data.get("pc_tag", "Latest Smoke Passed") or "Latest Smoke Passed"
        nutest_branch = req_data.get("nutest_branch", "master") or "master"
        provider = req_data.get("provider", "global_pool") or "global_pool"
        resource_type = req_data.get("resource_type", "nested_2.0") or "nested_2.0"
        raw_np = req_data.get("node_pool") or []
        if isinstance(raw_np, list):
            node_pools = [p.strip() for p in raw_np if isinstance(p, str) and p.strip()]
        else:
            node_pools = [raw_np.strip()] if isinstance(raw_np, str) and raw_np.strip() else []
        framework_patch_url = (req_data.get("framework_patch_url") or "").strip() or None
        test_patch_url = (req_data.get("test_patch_url") or "").strip() or None
        testcase_names = req_data.get("testcase_names", [])
        custom_jp_name = (req_data.get("custom_jp_name") or "").strip() or None
        custom_ts_name = (req_data.get("custom_ts_name") or "").strip() or None
        jp_tags = req_data.get("jp_tags") or []
        if isinstance(jp_tags, str):
            jp_tags = [t.strip() for t in jp_tags.split(",") if t.strip()]
        elif not isinstance(jp_tags, list):
            jp_tags = []

        reuse_source_ts = bool(req_data.get("reuse_source_ts", False))
        if reuse_source_ts and create_fresh:
            return jsonify({
                "error": "reuse_source_ts is only valid when cloning from an existing job profile (not fresh create).",
            }), 400

        if not create_fresh:
            if not source_jp_id:
                return jsonify({"error": "source_jp_id is required when not creating fresh"}), 400
            source_jp_id = str(source_jp_id).strip()
            if not source_jp_id:
                return jsonify({"error": "source_jp_id cannot be empty"}), 400

        if source_testset_id:
            source_testset_id = str(source_testset_id).strip()

        if not isinstance(testcase_names, list):
            testcase_names = []
        testcase_names = [str(tc).strip() for tc in testcase_names if tc]

        if create_fresh and not testcase_names:
            return jsonify({"error": "testcase_names is required when creating fresh"}), 400

        logger.info(f"[create] mode={'fresh' if create_fresh else 'clone'}, "
                     f"source_jp_id={source_jp_id}, source_testset_id={source_testset_id}, "
                     f"source_testset_name={(req_data.get('source_testset_name') or '').strip() or None}, "
                     f"custom_jp_name={custom_jp_name}, custom_ts_name={custom_ts_name}, "
                     f"#testcases={len(testcase_names)}, #tags={len(jp_tags)}")

        from urllib.parse import quote

        # 1. Fetch source JP (only when cloning)
        source_jp = {}
        if not create_fresh:
            try:
                jp_resp = requests.get(
                    f"{JITA_BASE}/job_profiles/{source_jp_id}",
                    auth=JITA_SVC_AUTH,
                    verify=False,
                    timeout=30
                )
            except requests.exceptions.Timeout:
                return jsonify({"error": "Timed out fetching source job profile from JITA"}), 504
            except requests.exceptions.ConnectionError:
                return jsonify({"error": "Could not connect to JITA to fetch source job profile"}), 503

            if jp_resp.status_code != 200:
                return jsonify({"error": f"Failed to fetch source JP (HTTP {jp_resp.status_code}). Verify the JP ID is correct."}), 500

            try:
                source_jp = jp_resp.json().get("data", {})
            except (ValueError, TypeError):
                return jsonify({"error": "Invalid JSON from JITA when fetching source JP"}), 500

            if not source_jp or not isinstance(source_jp, dict):
                return jsonify({"error": f"Source JP '{source_jp_id}' returned empty data. It may not exist."}), 404

        def _test_set_ref_oid(ref):
            if isinstance(ref, dict):
                if "$oid" in ref:
                    return str(ref["$oid"]).strip() or None
                inner = ref.get("_id")
                if isinstance(inner, dict) and "$oid" in inner:
                    return str(inner["$oid"]).strip() or None
                if isinstance(inner, str) and inner.strip():
                    return inner.strip()
            if isinstance(ref, str) and ref.strip():
                return ref.strip()
            return None

        def _jit_ts_arg_strings(ts):
            """JITA may expose args as snake_case or camelCase; normalize to strings for POST payload."""
            if not isinstance(ts, dict):
                return "", ""

            def _coerce(val):
                if val is None:
                    return ""
                if isinstance(val, dict):
                    try:
                        return json.dumps(val, separators=(",", ":"))
                    except (TypeError, ValueError):
                        return ""
                if isinstance(val, list):
                    try:
                        return json.dumps(val, separators=(",", ":"))
                    except (TypeError, ValueError):
                        return ""
                s = str(val).strip()
                return s

            ta = _coerce(ts.get("test_args")) or _coerce(ts.get("testArgs"))
            fa = _coerce(ts.get("framework_args")) or _coerce(ts.get("frameworkArgs"))
            return ta, fa

        def _jit_pick_ts_dict_from_response(ts_json):
            """Normalize GET /test_sets/:id (or similar) JSON to one test set dict."""
            if not isinstance(ts_json, dict):
                return {}
            data = ts_json.get("data")
            if isinstance(data, list):
                for el in data:
                    if isinstance(el, dict):
                        return el
                return {}
            if isinstance(data, dict):
                d = data
                for wrap in ("test_set", "document", "result", "item"):
                    inner = d.get(wrap)
                    if isinstance(inner, dict) and any(
                        k in inner
                        for k in ("tests", "test_args", "testArgs", "framework_args", "frameworkArgs", "name", "_id")
                    ):
                        return inner
                return d
            return {}

        def _build_clone_test_set_post_payload(source_doc, new_name, test_entries, description):
            """POST /test_sets with same top-level shape as JITA GET, plus mirrored arg keys."""
            import copy

            strip_keys = {
                "_id", "id", "created_at", "updated_at", "created_by", "updated_by",
                "__v", "createdAt", "updatedAt", "path",
            }
            if not isinstance(source_doc, dict) or not source_doc:
                p = {"name": new_name, "tests": test_entries, "description": description}
                ta, fa = "", ""
                p["test_args"] = ta
                p["framework_args"] = fa
                p["testArgs"] = ta
                p["frameworkArgs"] = fa
                return p
            payload = {k: copy.deepcopy(v) for k, v in source_doc.items() if k not in strip_keys}
            payload["name"] = new_name
            payload["tests"] = test_entries
            payload["description"] = description
            for snake, camel in (("test_args", "testArgs"), ("framework_args", "frameworkArgs")):
                if snake in payload and camel not in payload:
                    payload[camel] = payload[snake]
                elif camel in payload and snake not in payload:
                    payload[snake] = payload[camel]
            return payload

        source_testset_name = (req_data.get("source_testset_name") or "").strip()

        # Template test set id: prefer exact name from UI (execution history), then explicit id, then JP's first TS.
        template_ts_id = None
        ts_name_resolved_id = None
        if not create_fresh and source_testset_name:
            try:
                raw_q = json.dumps({"name": source_testset_name})
                nm_resp = requests.get(
                    f"{JITA_BASE}/test_sets",
                    params={
                        "raw_query": quote(raw_q),
                        "limit": 1,
                        "only": "_id,name,test_args,framework_args,tests,description",
                    },
                    auth=JITA_SVC_AUTH,
                    verify=False,
                    timeout=30,
                )
                if nm_resp.status_code == 200:
                    items = nm_resp.json().get("data", []) if isinstance(nm_resp.json(), dict) else []
                    if items and isinstance(items[0], dict):
                        hit = items[0]
                        hit_name = (hit.get("name") or "").strip()
                        cand_id = _test_set_ref_oid(hit.get("_id"))
                        if cand_id:
                            if (
                                hit_name == source_testset_name
                                or hit_name.lower() == source_testset_name.lower()
                            ):
                                ts_name_resolved_id = cand_id
                                logger.info(
                                    f"[create] Template from source_testset_name={source_testset_name!r} -> id={ts_name_resolved_id}"
                                )
                            else:
                                ts_name_resolved_id = cand_id
                                logger.warning(
                                    f"[create] Name query returned '{hit_name}' for requested={source_testset_name!r}; "
                                    f"using single-hit id={ts_name_resolved_id}"
                                )
            except (requests.exceptions.RequestException, ValueError, TypeError) as e:
                logger.warning(f"[create] source_testset_name lookup failed: {e}")

        if ts_name_resolved_id:
            template_ts_id = ts_name_resolved_id
        elif source_testset_id:
            template_ts_id = str(source_testset_id).strip() or None
        if not create_fresh and not template_ts_id and source_jp:
            refs = source_jp.get("test_sets") or []
            if refs:
                template_ts_id = _test_set_ref_oid(refs[0])
                if template_ts_id and not source_testset_id and not source_testset_name:
                    logger.info(
                        f"[create] No source_testset_id/name in request; using source JP's first test set "
                        f"as clone template: {template_ts_id}"
                    )

        # 2. Fetch template test set (non-fatal if it fails)
        source_ts = {}
        ts_fetch_warning = None
        if template_ts_id:
            try:
                ts_resp = requests.get(
                    f"{JITA_BASE}/test_sets/{template_ts_id}",
                    auth=JITA_SVC_AUTH,
                    verify=False,
                    timeout=30
                )
                if ts_resp.status_code == 200:
                    source_ts = _jit_pick_ts_dict_from_response(ts_resp.json())
                    if source_ts:
                        logger.info(
                            f"[create] Loaded template TS id={template_ts_id} keys_sample={list(source_ts.keys())[:25]}"
                        )
                else:
                    ts_fetch_warning = f"Could not fetch source test set (HTTP {ts_resp.status_code}). Proceeding without test_args copy."
                    logger.warning(ts_fetch_warning)
            except (requests.exceptions.RequestException, ValueError) as e:
                ts_fetch_warning = f"Error fetching source test set: {e}. Proceeding without test_args copy."
                logger.warning(ts_fetch_warning)

        linked_ts_id_for_reuse = None
        reuse_ts_display_name = None

        if reuse_source_ts and not create_fresh:
            tid = source_testset_id or ts_name_resolved_id
            if not tid and source_jp:
                refs = source_jp.get("test_sets") or []
                if refs:
                    tid = _test_set_ref_oid(refs[0])
            if not tid:
                return jsonify({
                    "error": "reuse_source_ts requires a selected source test set or a source job profile "
                             "that has at least one test set.",
                }), 400
            linked_ts_id_for_reuse = str(tid).strip()
            st_oid = None
            if source_ts:
                st_oid = source_ts.get("_id")
                if isinstance(st_oid, dict) and "$oid" in st_oid:
                    st_oid = str(st_oid["$oid"])
                elif st_oid is not None:
                    st_oid = str(st_oid)
            if source_ts and st_oid == linked_ts_id_for_reuse:
                reuse_ts_display_name = (source_ts.get("name") or "").strip() or linked_ts_id_for_reuse
            else:
                try:
                    tr = requests.get(
                        f"{JITA_BASE}/test_sets/{linked_ts_id_for_reuse}",
                        auth=JITA_SVC_AUTH,
                        verify=False,
                        timeout=30,
                    )
                    if tr.status_code == 200:
                        tj = tr.json()
                        td = _jit_pick_ts_dict_from_response(tj)
                        if isinstance(td, dict):
                            reuse_ts_display_name = (td.get("name") or "").strip() or linked_ts_id_for_reuse
                    if not reuse_ts_display_name:
                        reuse_ts_display_name = linked_ts_id_for_reuse
                except (requests.exceptions.RequestException, ValueError):
                    reuse_ts_display_name = linked_ts_id_for_reuse
            logger.info(f"[create] reuse_source_ts=True, linked_ts_id={linked_ts_id_for_reuse}, name={reuse_ts_display_name}")

        # 3. Sequential names: User_Dyn_<YYYYMMDD>_JP_N / User_Dyn_<YYYYMMDD>_TS_N
        dyn_name_date = (req_data.get("dyn_name_date") or "").strip()
        if dyn_name_date and (len(dyn_name_date) != 8 or not dyn_name_date.isdigit()):
            return jsonify({"error": "dyn_name_date must be YYYYMMDD"}), 400
        jp_prefix, ts_prefix = _dynamic_jp_ts_prefix_for_date(dyn_name_date or None)
        date_key = _dyn_name_seq_date_key(dyn_name_date or None)
        # Monotonic: bumps every /create, success or later failure, so JP_/TS_ numbers always advance
        reserved_seq = _bump_dyn_name_seq(dyn_name_date or None)
        if custom_jp_name:
            custom_jp_name = _apply_reserved_seq_to_dyn_custom_name(custom_jp_name, date_key, reserved_seq)
        if custom_ts_name and not (reuse_source_ts and linked_ts_id_for_reuse):
            custom_ts_name = _apply_reserved_seq_to_dyn_custom_name(custom_ts_name, date_key, reserved_seq)

        if custom_jp_name:
            new_jp_name = custom_jp_name
        else:
            new_jp_name = f"{jp_prefix}{reserved_seq}"

        if reuse_source_ts and linked_ts_id_for_reuse:
            new_ts_name = reuse_ts_display_name or linked_ts_id_for_reuse
        elif custom_ts_name:
            new_ts_name = custom_ts_name
        else:
            new_ts_name = f"{ts_prefix}{reserved_seq}"

        # 4. Pre-check: verify JP and TS names; pick alternatives if the requested name exists
        def _name_exists(entity_type, name):
            """Check if a JP or TS with this exact name already exists. Returns ID or None."""
            try:
                rq = json.dumps({"name": name})
                logger.info(f"[create] Pre-check {entity_type} name='{name}', raw_query={rq}")
                resp = requests.get(
                    f"{JITA_BASE}/{entity_type}",
                    params={"raw_query": rq, "limit": 1, "only": "_id,name"},
                    auth=JITA_SVC_AUTH, verify=False, timeout=20,
                )
                logger.info(f"[create] Pre-check {entity_type} response: HTTP {resp.status_code}, "
                            f"body={resp.text[:300]}")
                if resp.status_code == 200:
                    items = resp.json().get("data", [])
                    if items and isinstance(items[0], dict):
                        matched_name = items[0].get("name", "")
                        if matched_name == name:
                            eid = items[0].get("_id")
                            if isinstance(eid, dict) and "$oid" in eid:
                                return eid["$oid"]
                            return str(eid) if eid else None
                        else:
                            logger.info(f"[create] Pre-check returned '{matched_name}' which doesn't exactly match '{name}', treating as no match")
            except Exception as e:
                logger.warning(f"[create] Pre-check for {entity_type} '{name}' failed: {e}")
            return None

        def _pick_unique_name(entity_type, base_name, kind="Job Profile"):
            """If base_name is free, return (base_name, None). Otherwise use base_2, base_3, ... in JITA."""
            if not base_name:
                return base_name, None
            if not _name_exists(entity_type, base_name):
                return base_name, None
            orig = base_name
            for n in range(2, 5000):
                candidate = f"{orig}_{n}"
                if not _name_exists(entity_type, candidate):
                    msg = f"{kind} name {orig!r} already exists in JITA; using {candidate!r} instead."
                    logger.info(f"[create] {msg}")
                    return candidate, msg
            fallback = f"{orig}_{int(time.time())}"
            return fallback, f"{kind} name {orig!r} exists; using time-based name {fallback!r}."

        jp_name_adjust_warn = None
        new_jp_name, _adj = _pick_unique_name("job_profiles", new_jp_name, "Job Profile")
        if _adj:
            jp_name_adjust_warn = _adj

        existing_ts_id = None
        if not (reuse_source_ts and linked_ts_id_for_reuse):
            existing_ts_id = _name_exists("test_sets", new_ts_name)

        ts_reused = False
        ts_create_warning = None

        if reuse_source_ts and linked_ts_id_for_reuse:
            created_ts_id = linked_ts_id_for_reuse
            ts_reused = True
            if testcase_names:
                ts_create_warning = (
                    "reuse_source_ts: cloned job profile uses the existing test set unchanged; "
                    "testcase names in the request were not written to JITA."
                )
            logger.info(f"[create] Linked JP to existing test set id={created_ts_id} (reuse_source_ts)")
        elif existing_ts_id:
            created_ts_id = existing_ts_id
            ts_reused = True
            ts_create_warning = f"Test set '{new_ts_name}' already exists (ID: {existing_ts_id}). Reusing it."
            logger.info(f"[create] TS '{new_ts_name}' already exists, reusing ID {existing_ts_id}")
        else:
            # Build test entries
            if testcase_names:
                row_tmpl = {}
                if isinstance(source_ts, dict):
                    src_tests = source_ts.get("tests") or []
                    if src_tests and isinstance(src_tests[0], dict):
                        row_tmpl = {
                            k: v
                            for k, v in src_tests[0].items()
                            if k not in ("_id", "name") and not str(k).startswith("__")
                        }
                test_entries = []
                for tc in testcase_names:
                    row = dict(row_tmpl)
                    row["name"] = tc
                    row.setdefault("framework_version", "nutest-py3-tests")
                    row.setdefault("package_type", "tar")
                    row.setdefault("service", "NutestPy3Tests")
                    test_entries.append(row)
            else:
                test_entries = source_ts.get("tests", []) or []

            if not create_fresh and source_ts:
                ts_label = source_ts.get("name") or source_testset_id or template_ts_id or "unknown"
                desc = f"Dynamic test set cloned from {ts_label}"
                new_ts_payload = _build_clone_test_set_post_payload(source_ts, new_ts_name, test_entries, desc)
            else:
                _ta, _fa = _jit_ts_arg_strings(source_ts) if source_ts else ("", "")
                new_ts_payload = {
                    "name": new_ts_name,
                    "tests": test_entries,
                    "description": f"Dynamic test set with {len(test_entries)} testcase(s)",
                    "test_args": _ta,
                    "framework_args": _fa,
                    "testArgs": _ta,
                    "frameworkArgs": _fa,
                }
            # Clone: if source already had every default framework key, leave framework_args unchanged.
            cloned_from_existing = bool(not create_fresh and source_ts)
            _fa_for_skip = new_ts_payload.get("framework_args")
            if _fa_for_skip in (None, ""):
                _fa_for_skip = new_ts_payload.get("frameworkArgs")
            if not (cloned_from_existing and _framework_args_dict_has_all_dyn_default_keys(_fa_for_skip)):
                _apply_default_framework_options_to_test_set_payload(new_ts_payload)
            _ta_log, _fa_log = _jit_ts_arg_strings(new_ts_payload)
            logger.info(f"[create] TS payload: name={new_ts_name}, #tests={len(test_entries)}, "
                        f"test_args_len={len(_ta_log)}, framework_args_len={len(_fa_log)}")

            created_ts_id = None
            try:
                ts_create_resp = requests.post(
                    f"{JITA_BASE}/test_sets",
                    json=new_ts_payload,
                    auth=JITA_SVC_AUTH, verify=False, timeout=30,
                )
                ts_resp_json = ts_create_resp.json() if ts_create_resp.content else {}
                if ts_resp_json.get("success"):
                    created_ts_id = str(ts_resp_json["id"]) if ts_resp_json.get("id") else None
                    logger.info(f"Created test set: {new_ts_name} (ID: {created_ts_id})")
                else:
                    msg = ts_resp_json.get("message", f"HTTP {ts_create_resp.status_code}")
                    ts_create_warning = f"Test set creation failed: {msg}"
                    logger.warning(f"Failed to create test set: {msg}")
                    return jsonify({"error": f"Failed to create test set '{new_ts_name}': {msg}"}), 500
            except (requests.exceptions.RequestException, ValueError) as e:
                logger.warning(f"Error creating test set: {e}")
                return jsonify({"error": f"Error creating test set: {e}"}), 500

        # 5. Build infra based on provider selection
        def _build_infra(prov, res_type, np_list):
            if prov == "global_pool":
                if res_type == "physical":
                    return [{"type": "physical", "kind": "PRIVATE_CLOUD", "params": {"category": "general"}}]
                pool_name = "global_nested_2.0" if res_type == "nested_2.0" else "global_nested_1.0"
                return [{"kind": "ON_PREM", "type": "cluster_pool", "entries": [pool_name]}]
            elif prov == "node_pool":
                entries = np_list if np_list else ["unknown"]
                return [{"kind": "ON_PREM", "type": "node_pool", "entries": entries}]
            elif prov == "static":
                entries = np_list if np_list else ["unknown"]
                return [{"kind": "ON_PREM", "type": "cluster", "entries": entries}]
            return [{"kind": "ON_PREM", "type": "cluster_pool", "entries": ["global_nested_2.0"]}]

        infra = _build_infra(provider, resource_type, node_pools)

        # Build new JP payload
        if create_fresh:
            new_jp_payload = {
                "name": new_jp_name,
                "description": f"Dynamic JP with {len(testcase_names)} testcase(s)",
                "test_sets": [],
                "git": {},
                "build_selection": {},
                "resource_manager_json": {},
                "infra": infra,
                "requested_hardware": {
                    "hypervisor": "kvm",
                    "hypervisor_version": "branch_symlink",
                    "imaging_options": {"redundancy_factor": "default", "min_ram": 32},
                },
                "services": ["NOS"],
                "service": "AOS",
                "test_framework": "nutest-py3-tests",
                "nutest-py3-tests_branch": nutest_branch,
                "system_under_test": {"product": "aos", "component": "main", "branch": nos_branch},
            }
        else:
            import copy
            new_jp_payload = copy.deepcopy(source_jp)
            for field in ["_id", "created_at", "updated_at", "created_by", "__v"]:
                new_jp_payload.pop(field, None)
            new_jp_payload["name"] = new_jp_name
            new_jp_payload["description"] = f"Dynamic JP cloned from {source_jp.get('name', source_jp_id)}"
            new_jp_payload["test_sets"] = []
            logger.info(f"[create] Source JP keys: {list(source_jp.keys())}")

        # Link to new test set if created
        if created_ts_id:
            new_jp_payload["test_sets"] = [{"$oid": created_ts_id}]

        if create_fresh:
            # Fresh mode: apply all config from the UI
            git = new_jp_payload.get("git") or {}
            if not isinstance(git, dict):
                git = {}
            git["branch"] = nos_branch
            git["repo"] = "main"
            new_jp_payload["git"] = git

            nos_build_type = "opt" if nos_branch.strip().lower() == "master" else "release"
            pc_build_type = "opt" if pc_branch.strip().lower() == "master" else "release"

            new_jp_payload["build_selection"] = {
                "by_latest_smoked": nos_tag == "Latest Smoke Passed",
                "commit_must_be_newer": False,
                "build_type": nos_build_type,
            }

            resource_manager_json = new_jp_payload.get("resource_manager_json") or {}
            if not isinstance(resource_manager_json, dict):
                resource_manager_json = {}
            if "NOS_CLUSTER" not in resource_manager_json:
                resource_manager_json["NOS_CLUSTER"] = {}
            resource_manager_json["PRISM_CENTRAL"] = {
                "build": {
                    "branch": pc_branch,
                    "build_selection_build_type": pc_build_type,
                    "build_selection_option": pc_tag,
                }
            }
            new_jp_payload["resource_manager_json"] = resource_manager_json

            test_framework_metadata = new_jp_payload.get("test_framework_metadata") or {}
            if not isinstance(test_framework_metadata, dict):
                test_framework_metadata = {}
            test_meta = test_framework_metadata.get("test") or {}
            if not isinstance(test_meta, dict):
                test_meta = {}
            test_meta["branch"] = nutest_branch
            test_meta["commit"] = None
            if test_patch_url:
                test_meta["patch_url"] = test_patch_url
            else:
                test_meta.pop("patch_url", None)
            framework_meta = test_framework_metadata.get("framework") or {}
            if not isinstance(framework_meta, dict):
                framework_meta = {}
            framework_meta["branch"] = nutest_branch
            framework_meta["commit"] = None
            if framework_patch_url:
                framework_meta["patch_url"] = framework_patch_url
            else:
                framework_meta.pop("patch_url", None)
            test_framework_metadata["test"] = test_meta
            test_framework_metadata["framework"] = framework_meta
            new_jp_payload["test_framework_metadata"] = test_framework_metadata
            new_jp_payload["test_framework"] = "nutest-py3-tests"
            new_jp_payload["nutest-py3-tests_branch"] = nutest_branch
            if framework_patch_url:
                new_jp_payload["patch_url"] = framework_patch_url
            else:
                new_jp_payload.pop("patch_url", None)
            new_jp_payload.pop("nutest_branch", None)

            new_jp_payload["infra"] = infra
        else:
            # Clone mode: preserve source JP's config (infra, git, build, etc.)
            # Only update name, description, test_sets (already done above)
            logger.info(f"[create] Clone mode — preserving source JP config "
                        f"(infra={new_jp_payload.get('infra', 'N/A')[:80] if isinstance(new_jp_payload.get('infra'), str) else 'present'})")
            # User-supplied patch URLs (clone flow): fresh-create applied these above; clone had skipped them
            if test_patch_url or framework_patch_url:
                logger.info(
                    f"[create] Clone mode — applying patches: test_patch_url={bool(test_patch_url)}, "
                    f"framework_patch_url={bool(framework_patch_url)}"
                )
                tmeta = new_jp_payload.get("test_framework_metadata")
                tmeta = dict(tmeta) if isinstance(tmeta, dict) else {}
                test_m = tmeta.get("test")
                test_m = dict(test_m) if isinstance(test_m, dict) else {}
                fw_m = tmeta.get("framework")
                fw_m = dict(fw_m) if isinstance(fw_m, dict) else {}
                test_m["branch"] = nutest_branch
                test_m["commit"] = None
                fw_m["branch"] = nutest_branch
                fw_m["commit"] = None
                if test_patch_url:
                    test_m["patch_url"] = test_patch_url
                if framework_patch_url:
                    fw_m["patch_url"] = framework_patch_url
                tmeta["test"] = test_m
                tmeta["framework"] = fw_m
                new_jp_payload["test_framework_metadata"] = tmeta
                new_jp_payload["test_framework"] = "nutest-py3-tests"
                new_jp_payload["nutest-py3-tests_branch"] = nutest_branch
                if framework_patch_url:
                    new_jp_payload["patch_url"] = framework_patch_url

        # Tags will be applied via a separate PUT after creation (same
        # approach as Run Plan) because JITA's POST ignores tag fields.

        # Deep sanitize: ensure JSON serializable (handle ObjectId, sets, bytes, etc.)
        def sanitize_value(val):
            if isinstance(val, dict):
                return {k: sanitize_value(v) for k, v in val.items()}
            elif isinstance(val, list):
                return [sanitize_value(item) for item in val]
            elif isinstance(val, (set, tuple)):
                return [sanitize_value(item) for item in val]
            elif isinstance(val, bytes):
                return val.decode("utf-8", errors="replace")
            elif val is Ellipsis:
                return None
            else:
                return val

        serializable_payload = sanitize_value(new_jp_payload)
        logger.info(f"[create] Final JP payload — name: {serializable_payload.get('name')}, "
                    f"tags: {serializable_payload.get('tags', 'MISSING')}, "
                    f"adv_opts_keys: {list((serializable_payload.get('advanced_options') or {}).keys())}, "
                    f"adv_tags: {(serializable_payload.get('advanced_options') or {}).get('tags', 'MISSING')}, "
                    f"test_sets: {serializable_payload.get('test_sets', 'MISSING')}")

        # 6. POST new JP
        try:
            jp_create_resp = requests.post(
                f"{JITA_BASE}/job_profiles",
                json=serializable_payload,
                auth=JITA_SVC_AUTH,
                verify=False,
                timeout=30
            )
        except requests.exceptions.Timeout:
            note = f" Note: Test set '{new_ts_name}' (ID: {created_ts_id}) was already created." if created_ts_id and not ts_reused else ""
            return jsonify({"error": f"Timed out creating job profile on JITA.{note}"}), 504
        except requests.exceptions.ConnectionError:
            note = f" Note: Test set '{new_ts_name}' (ID: {created_ts_id}) was already created." if created_ts_id and not ts_reused else ""
            return jsonify({"error": f"Could not connect to JITA to create job profile.{note}"}), 503

        try:
            jp_resp_json = jp_create_resp.json()
        except (ValueError, TypeError):
            jp_resp_json = {}

        if not jp_resp_json.get("success"):
            error_msg = jp_resp_json.get("message", f"HTTP {jp_create_resp.status_code}")
            logger.error(f"Failed to create JP: {error_msg}")
            note = f" Note: Test set '{new_ts_name}' (ID: {created_ts_id}) was already created." if created_ts_id and not ts_reused else ""
            return jsonify({
                "error": f"Failed to create Job Profile: {error_msg}.{note}",
            }), 500

        created_jp_id = jp_resp_json.get("id")
        if created_jp_id:
            created_jp_id = str(created_jp_id)

        logger.info(f"Created JP: {new_jp_name} (ID: {created_jp_id})")

        # 7. Apply tags via PUT (same approach as Run Plan — JITA ignores
        # tag fields on POST but accepts them on PUT via tester_tags)
        tag_warning = None
        if jp_tags and created_jp_id:
            logger.info(f"[create] Applying tags {jp_tags} to JP {created_jp_id} via PUT (tester_tags)")
            try:
                get_resp = requests.get(
                    f"{JITA_BASE}/job_profiles/{created_jp_id}",
                    auth=JITA_SVC_AUTH,                     verify=False, timeout=30,
                )
                if get_resp.status_code == 200:
                    jp_data = get_resp.json().get("data", {})
                    if isinstance(jp_data, dict):
                        tester_tags = jp_data.get("tester_tags", [])
                        if not isinstance(tester_tags, list):
                            tester_tags = []
                        merged = list(dict.fromkeys(tester_tags + jp_tags))
                        jp_data["tester_tags"] = merged

                        put_payload = {}
                        for k, v in jp_data.items():
                            if isinstance(v, (set, tuple)):
                                put_payload[k] = list(v)
                            elif v is Ellipsis:
                                put_payload[k] = None
                            else:
                                put_payload[k] = v

                        put_resp = requests.put(
                            f"{JITA_BASE}/job_profiles/{created_jp_id}",
                            json=put_payload,
                            auth=JITA_SVC_AUTH,                             verify=False, timeout=30,
                        )
                        if put_resp.status_code == 200:
                            logger.info(f"[create] Tags applied successfully: tester_tags={merged}")
                        else:
                            tag_warning = f"JP created but tags could not be applied (HTTP {put_resp.status_code})"
                            logger.warning(f"[create] PUT tags failed: HTTP {put_resp.status_code} — {put_resp.text[:200]}")
                    else:
                        tag_warning = "JP created but could not re-fetch it to apply tags"
                else:
                    tag_warning = f"JP created but re-fetch for tags failed (HTTP {get_resp.status_code})"
                    logger.warning(f"[create] GET for tag update failed: HTTP {get_resp.status_code}")
            except Exception as e:
                tag_warning = f"JP created but tags could not be applied: {e}"
                logger.warning(f"[create] Tag update error: {e}")

        warnings = []
        if jp_name_adjust_warn:
            warnings.append(jp_name_adjust_warn)
        if ts_fetch_warning:
            warnings.append(ts_fetch_warning)
        if ts_create_warning:
            warnings.append(ts_create_warning)
        if tag_warning:
            warnings.append(tag_warning)

        ts_msg = ""
        if created_ts_id:
            ts_msg = f" (reused existing {new_ts_name})" if ts_reused else f" and {new_ts_name}"

        return jsonify({
            "success": True,
            "reuse_source_ts": reuse_source_ts,
            "job_profile": {
                "_id": created_jp_id,
                "name": new_jp_name,
            },
            "test_set": {
                "_id": created_ts_id,
                "name": new_ts_name,
                "reused": ts_reused,
            } if created_ts_id else None,
            "message": f"Created {new_jp_name}{ts_msg}",
            "warnings": warnings if warnings else None,
        })
    except Exception as e:
        logger.error(f"Error in dynamic-jp create: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/mcp/regression/dynamic-jp/update", methods=["POST"])
def dynamic_jp_update():
    """Update an existing dynamic job profile or test set."""
    try:
        req_data = request.json
        if not req_data:
            return jsonify({"error": "Request body is required (JSON)"}), 400

        jp_id = req_data.get("jp_id")
        ts_id = req_data.get("ts_id")
        updates = req_data.get("updates", {})

        if not jp_id and not ts_id:
            return jsonify({"error": "jp_id or ts_id is required"}), 400

        if not isinstance(updates, dict):
            return jsonify({"error": "updates must be a JSON object"}), 400

        if jp_id:
            jp_id = str(jp_id).strip()
        if ts_id:
            ts_id = str(ts_id).strip()

        results = {}

        if jp_id and updates.get("jp_updates"):
            jp_updates = updates["jp_updates"]
            if not isinstance(jp_updates, dict):
                return jsonify({"error": "jp_updates must be a JSON object"}), 400

            try:
                get_resp = requests.get(
                    f"{JITA_BASE}/job_profiles/{jp_id}",
                    auth=JITA_SVC_AUTH,
                    verify=False,
                    timeout=30
                )
            except requests.exceptions.RequestException as e:
                return jsonify({"error": f"Failed to connect to JITA to fetch JP {jp_id}: {e}"}), 503

            if get_resp.status_code != 200:
                return jsonify({"error": f"Failed to fetch JP {jp_id} (HTTP {get_resp.status_code})"}), 500

            try:
                existing = get_resp.json().get("data", {})
            except (ValueError, TypeError):
                return jsonify({"error": "Invalid JSON from JITA when fetching JP"}), 500

            if not isinstance(existing, dict):
                existing = {}
            existing.update(jp_updates)

            def sanitize(val):
                if isinstance(val, dict):
                    return {k: sanitize(v) for k, v in val.items()}
                elif isinstance(val, list):
                    return [sanitize(item) for item in val]
                elif isinstance(val, (set, tuple)):
                    return [sanitize(item) for item in val]
                elif isinstance(val, bytes):
                    return val.decode("utf-8", errors="replace")
                elif val is Ellipsis:
                    return None
                return val

            serializable = sanitize(existing)

            try:
                put_resp = requests.put(
                    f"{JITA_BASE}/job_profiles/{jp_id}",
                    json=serializable,
                    auth=JITA_SVC_AUTH,
                    verify=False,
                    timeout=30
                )
                results["jp"] = {
                    "success": put_resp.status_code == 200,
                    "status_code": put_resp.status_code,
                    "message": "Updated" if put_resp.status_code == 200 else f"JITA returned {put_resp.status_code}",
                }
            except requests.exceptions.RequestException as e:
                results["jp"] = {"success": False, "error": str(e)}

        if ts_id and updates.get("ts_updates"):
            ts_updates = updates["ts_updates"]
            if not isinstance(ts_updates, dict):
                return jsonify({"error": "ts_updates must be a JSON object"}), 400

            try:
                get_resp = requests.get(
                    f"{JITA_BASE}/test_sets/{ts_id}",
                    auth=JITA_SVC_AUTH,
                    verify=False,
                    timeout=30
                )
            except requests.exceptions.RequestException as e:
                return jsonify({"error": f"Failed to connect to JITA to fetch test set {ts_id}: {e}"}), 503

            if get_resp.status_code != 200:
                return jsonify({"error": f"Failed to fetch test set {ts_id} (HTTP {get_resp.status_code})"}), 500

            try:
                existing_ts = get_resp.json().get("data", {})
            except (ValueError, TypeError):
                return jsonify({"error": "Invalid JSON from JITA when fetching test set"}), 500

            if not isinstance(existing_ts, dict):
                existing_ts = {}
            existing_ts.update(ts_updates)

            try:
                put_resp = requests.put(
                    f"{JITA_BASE}/test_sets/{ts_id}",
                    json=existing_ts,
                    auth=JITA_SVC_AUTH,
                    verify=False,
                    timeout=30
                )
                results["ts"] = {
                    "success": put_resp.status_code == 200,
                    "status_code": put_resp.status_code,
                    "message": "Updated" if put_resp.status_code == 200 else f"JITA returned {put_resp.status_code}",
                }
            except requests.exceptions.RequestException as e:
                results["ts"] = {"success": False, "error": str(e)}

        return jsonify({"success": True, "results": results})
    except Exception as e:
        logger.error(f"Error in dynamic-jp update: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/mcp/regression/dynamic-jp/search", methods=["POST"])
def dynamic_jp_search():
    """Search Job Profiles and Test Sets by name pattern."""
    try:
        req_data = request.json or {}
        query = (req_data.get("query") or "").strip()
        if len(query) < 2:
            return jsonify({"error": "Search query must be at least 2 characters"}), 400

        pattern = re.escape(query)
        raw_q = json.dumps({"name": {"$regex": pattern, "$options": "i"}})
        limit = min(int(req_data.get("limit", 20)), 50)

        result = {"job_profiles": [], "test_sets": []}

        try:
            jp_resp = requests.get(
                f"{JITA_BASE}/job_profiles",
                params={"raw_query": raw_q, "limit": limit, "only": "_id,name,description,tags"},
                auth=JITA_SVC_AUTH, verify=False, timeout=20,
            )
            if jp_resp.status_code == 200:
                for item in (jp_resp.json().get("data", []) or []):
                    if not isinstance(item, dict):
                        continue
                    eid = item.get("_id")
                    if isinstance(eid, dict) and "$oid" in eid:
                        eid = eid["$oid"]
                    elif eid:
                        eid = str(eid)
                    result["job_profiles"].append({
                        "_id": eid,
                        "name": item.get("name", ""),
                        "description": item.get("description", ""),
                    })
        except Exception as e:
            logger.warning(f"[search] JP search failed: {e}")

        try:
            ts_resp = requests.get(
                f"{JITA_BASE}/test_sets",
                params={"raw_query": raw_q, "limit": limit, "only": "_id,name,description"},
                auth=JITA_SVC_AUTH, verify=False, timeout=20,
            )
            if ts_resp.status_code == 200:
                for item in (ts_resp.json().get("data", []) or []):
                    if not isinstance(item, dict):
                        continue
                    eid = item.get("_id")
                    if isinstance(eid, dict) and "$oid" in eid:
                        eid = eid["$oid"]
                    elif eid:
                        eid = str(eid)
                    result["test_sets"].append({
                        "_id": eid,
                        "name": item.get("name", ""),
                        "description": item.get("description", ""),
                    })
        except Exception as e:
            logger.warning(f"[search] TS search failed: {e}")

        return jsonify({"success": True, **result})
    except Exception as e:
        logger.error(f"Error in dynamic-jp search: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/mcp/regression/dynamic-jp/delete", methods=["POST"])
def dynamic_jp_delete():
    """Delete one or more Job Profiles and/or Test Sets by ID."""
    try:
        req_data = request.json or {}
        jp_ids = req_data.get("jp_ids", [])
        ts_ids = req_data.get("ts_ids", [])

        if not jp_ids and not ts_ids:
            return jsonify({"error": "At least one of jp_ids or ts_ids is required"}), 400

        if not isinstance(jp_ids, list):
            jp_ids = [jp_ids]
        if not isinstance(ts_ids, list):
            ts_ids = [ts_ids]

        jp_ids = [str(i).strip() for i in jp_ids if i]
        ts_ids = [str(i).strip() for i in ts_ids if i]

        results = {"job_profiles": [], "test_sets": []}

        for jp_id in jp_ids:
            try:
                resp = requests.delete(
                    f"{JITA_BASE}/job_profiles/{jp_id}",
                    auth=JITA_SVC_AUTH, verify=False, timeout=30,
                )
                success = resp.status_code in (200, 204)
                msg = "Deleted" if success else f"JITA returned HTTP {resp.status_code}"
                if not success:
                    try:
                        msg = resp.json().get("message", msg)
                    except Exception:
                        pass
                results["job_profiles"].append({
                    "_id": jp_id,
                    "success": success,
                    "message": msg,
                })
                logger.info(f"[delete] JP {jp_id}: {'OK' if success else 'FAILED'} ({msg})")
            except Exception as e:
                results["job_profiles"].append({
                    "_id": jp_id,
                    "success": False,
                    "message": str(e),
                })
                logger.warning(f"[delete] JP {jp_id} error: {e}")

        for ts_id in ts_ids:
            try:
                resp = requests.delete(
                    f"{JITA_BASE}/test_sets/{ts_id}",
                    auth=JITA_SVC_AUTH, verify=False, timeout=30,
                )
                success = resp.status_code in (200, 204)
                msg = "Deleted" if success else f"JITA returned HTTP {resp.status_code}"
                if not success:
                    try:
                        msg = resp.json().get("message", msg)
                    except Exception:
                        pass
                results["test_sets"].append({
                    "_id": ts_id,
                    "success": success,
                    "message": msg,
                })
                logger.info(f"[delete] TS {ts_id}: {'OK' if success else 'FAILED'} ({msg})")
            except Exception as e:
                results["test_sets"].append({
                    "_id": ts_id,
                    "success": False,
                    "message": str(e),
                })
                logger.warning(f"[delete] TS {ts_id} error: {e}")

        all_ok = all(r["success"] for r in results["job_profiles"] + results["test_sets"])
        return jsonify({"success": all_ok, "results": results})
    except Exception as e:
        logger.error(f"Error in dynamic-jp delete: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ======================================================
# App Runner
# ======================================================
if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", "5001"))
    debug = os.getenv("FLASK_DEBUG", "false").lower() in ("true", "1", "yes")
    app.run(host=host, port=port, debug=debug)
