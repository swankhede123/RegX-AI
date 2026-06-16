import React, { useEffect, useState } from "react";
import api from "./api";
import { API_BASE_URL } from "./config";
import "./RegressionHome.css";

const API_URL = `${API_BASE_URL}/mcp/regression/home`;
const MANUAL_TASKS_API = `${API_BASE_URL}/mcp/regression/manual-tasks`;
const CONFIG_API = `${API_BASE_URL}/mcp/regression/config`;
const CONFIG_TAGS_API = `${API_BASE_URL}/mcp/regression/config/tags`;
const TCMS_OVERALL_QI_API = `${API_BASE_URL}/mcp/regression/tcms-overall-qi`;
const TEAM_CONFIG_API = `${API_BASE_URL}/mcp/regression/team-config`;
const DEFAULT_TAG = "cdp_master_full_reg";
const JITA_RESULTS_URL = "https://jita.eng.nutanix.com/results?task_ids=";
const JIRA_URL = "https://jira.nutanix.com/browse/";

// Load tag from localStorage or use default
const getStoredTag = () => {
  const stored = localStorage.getItem("regressionDashboardTag");
  return stored || DEFAULT_TAG;
};

// Load hidden branches from localStorage
const getStoredHiddenBranches = () => {
  const stored = localStorage.getItem("regressionDashboardHiddenBranches");
  return stored ? JSON.parse(stored) : [];
};

// Load advanced action options from localStorage
const getStoredAdvancedOptions = () => {
  const stored = localStorage.getItem("regressionDashboardAdvancedOptions");
  return stored ? JSON.parse(stored) : {
    triageCount: true, // Load by default
    triageAccuracy: false, // Triage Accuracy Analyzer
    qiSummaryReport: false,
    flakyTestInsights: false,
    aiRootCauseSummary: false,
    regressionRiskScore: false,
    bulkIssuesQiImpact: false,
    qiImpactedBulkIssue: false // QI Impacted Bulk issue - not loaded by default
  };
};

export default function RegressionHome() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [tag, setTag] = useState(getStoredTag());
  const [showConfigModal, setShowConfigModal] = useState(false);
  const [configTagInput, setConfigTagInput] = useState(tag);
  const [addedTags, setAddedTags] = useState([]);
  const [defaultTag, setDefaultTag] = useState(null);
  const [newTagInput, setNewTagInput] = useState("");
  const [inputMode, setInputMode] = useState(() => {
    return localStorage.getItem("regressionDashboardInputMode") || "tag";
  });
  // Local state for modal input mode (doesn't trigger API calls)
  const [modalInputMode, setModalInputMode] = useState("tag");
  const [configTaskIdsInput, setConfigTaskIdsInput] = useState(() => {
    const savedTaskIds = localStorage.getItem("regressionDashboardTaskIds");
    return savedTaskIds ? JSON.parse(savedTaskIds).join(", ") : "";
  });
  // Track task IDs as string to trigger useEffect when they change
  const [taskIdsKey, setTaskIdsKey] = useState(() => {
    const savedTaskIds = localStorage.getItem("regressionDashboardTaskIds");
    return savedTaskIds ? savedTaskIds : null; // Store as string for comparison
  });
  const [manualTasks, setManualTasks] = useState({}); // {branch: [task_ids]}
  const [editingBranch, setEditingBranch] = useState(null);
  const [newTaskId, setNewTaskId] = useState("");
  const [hiddenBranches, setHiddenBranches] = useState(getStoredHiddenBranches()); // Branches to hide
  const [newBranchTagInput, setNewBranchTagInput] = useState("");
  const [loadingBranches, setLoadingBranches] = useState(false);
  const [availableBranches, setAvailableBranches] = useState([]);
  const [advancedOptions, setAdvancedOptions] = useState(getStoredAdvancedOptions());
  const [triageCount, setTriageCount] = useState(null);
  const [qiSummaryReport, setQiSummaryReport] = useState(null);
  const [loadingTriage, setLoadingTriage] = useState(false);
  const [loadingQiSummary, setLoadingQiSummary] = useState(false);
  const [loadingBulkQi, setLoadingBulkQi] = useState(false); // Separate loading state for bulk QI
  const [triageAccuracyData, setTriageAccuracyData] = useState(null);
  const [loadingTriageAccuracy, setLoadingTriageAccuracy] = useState(false);
  const [configLoaded, setConfigLoaded] = useState(false); // Track if config has been loaded from JSON
  const [branchQiData, setBranchQiData] = useState({});
  const [branchQiLoading, setBranchQiLoading] = useState({});
  const [teamConfig, setTeamConfig] = useState(null);

  // Parse JITA task link or comma-separated task IDs
  const parseTaskIds = (input) => {
    if (!input || !input.trim()) return null;
    
    const trimmed = input.trim();
    
    // Check if it's a JITA link
    if (trimmed.includes("jita.eng.nutanix.com") || trimmed.includes("jita.nutanix.com")) {
      try {
        const url = new URL(trimmed);
        const taskIdsParam = url.searchParams.get("task_ids");
        if (taskIdsParam) {
          return taskIdsParam.split(",").map(id => id.trim()).filter(id => id);
        }
      } catch (e) {
        console.error("Error parsing JITA link:", e);
      }
    }
    
    // Otherwise, treat as comma-separated task IDs
    return trimmed.split(",").map(id => id.trim()).filter(id => id);
  };

  // Load configuration from JSON file on component mount
  useEffect(() => {
    const loadConfigFromJSON = async () => {
      try {
        const response = await api.get(CONFIG_API);
        const config = response.data;
        
        setAddedTags(config.added_tags || []);
        setDefaultTag(config.default_tag || null);
        
        if (config.input_mode === "tag") {
          const effectiveTag = config.default_tag || config.tag || "";
          setTag(effectiveTag || null);
          setInputMode("tag");
          setConfigTagInput(effectiveTag);
          setModalInputMode("tag");
          localStorage.setItem("regressionDashboardTag", effectiveTag || "");
          localStorage.setItem("regressionDashboardInputMode", "tag");
          localStorage.removeItem("regressionDashboardTaskIds");
          setTaskIdsKey(null);
        } else if (config.input_mode === "task_ids" && config.task_ids && config.task_ids.length > 0) {
          // Load task IDs configuration
          setTag(null);
          setInputMode("task_ids");
          setModalInputMode("task_ids");
          setConfigTaskIdsInput(config.task_ids.join(", "));
          const taskIdsString = JSON.stringify(config.task_ids);
          setTaskIdsKey(taskIdsString);
          localStorage.setItem("regressionDashboardInputMode", "task_ids");
          localStorage.setItem("regressionDashboardTaskIds", taskIdsString);
          localStorage.removeItem("regressionDashboardTag");
        }
        setConfigLoaded(true);
      } catch (error) {
        console.error("Error loading configuration from JSON:", error);
        // Fallback to localStorage if JSON load fails
        setConfigLoaded(true);
      }
    };
    
    loadConfigFromJSON();

    api.get(TEAM_CONFIG_API)
      .then((res) => setTeamConfig(res.data?.team_config || {}))
      .catch((err) => console.error("Error loading team config:", err));
  }, []); // Only run on mount

  // When config modal opens, refresh config to sync addedTags and defaultTag
  useEffect(() => {
    if (showConfigModal) {
      api.get(CONFIG_API).then((res) => {
        const c = res.data;
        setAddedTags(c.added_tags || []);
        setDefaultTag(c.default_tag || null);
        if (c.input_mode === "tag") {
          setConfigTagInput(c.default_tag || c.tag || "");
        }
      }).catch(() => {});
    }
  }, [showConfigModal]);

  // Fetch data based on current configuration
  const fetchData = async (params) => {
    try {
      setLoading(true);
      const response = await api.get(API_URL, { params });
      if (response.data && response.data.runs && Array.isArray(response.data.runs)) {
        // Debug: Log branch information for troubleshooting
        console.log("Raw runs data:", response.data.runs.map(r => ({ task_id: r.task_id, branch: r.branch, label: r.label })));
        
        // Check if there are missing task IDs and show a warning
        if (response.data.missing_task_ids && response.data.missing_task_ids.length > 0) {
          const missingCount = response.data.missing_task_ids.length;
          const requestedCount = response.data.requested_count || 0;
          const foundCount = response.data.found_count || response.data.runs.length;
          console.warn(`Warning: ${missingCount} out of ${requestedCount} task IDs were not found in the database. Only ${foundCount} tasks were found.`);
          if (missingCount === requestedCount) {
            alert(`None of the ${requestedCount} task IDs were found in the database. Please verify the task IDs are correct.`);
          } else {
            alert(`Warning: ${missingCount} out of ${requestedCount} task IDs were not found. Only ${foundCount} tasks will be displayed.`);
          }
        }
        
        const aggregated = aggregateByBranch(response.data.runs, response.data.branch_start_dates || {});
        console.log("Aggregated by branch:", aggregated);
        console.log("Hidden branches:", hiddenBranches);
        
        // Filter out hidden branches
        const hiddenSet = new Set(hiddenBranches);
        const filtered = aggregated.filter(row => {
          const isHidden = hiddenSet.has(row.branch);
          if (isHidden) {
            console.log(`Branch "${row.branch}" is hidden, filtering out`);
          }
          return !isHidden;
        });
        console.log("After filtering hidden branches:", filtered);
        
        // If no rows after filtering, check if all were filtered out
        if (filtered.length === 0 && aggregated.length > 0) {
          console.warn("All branches were filtered out! Aggregated branches:", aggregated.map(r => r.branch));
        }
        
        setRows(filtered);
        
        // Fetch manual tasks for each branch (only if tag mode)
        if (params.tag) {
          const branches = filtered.map(r => r.branch);
          fetchManualTasksForBranches(branches);
        }
      } else {
        console.error("Invalid response data or empty runs:", response.data);
        setRows([]);
        if (params.task_ids) {
          alert("No data was returned for the provided task IDs. Please verify the task IDs are correct and exist in the database.");
        }
      }
      setLoading(false);
    } catch (err) {
      console.error("Error fetching regression data:", err);
      setRows([]);
      setLoading(false);
      if (params.task_ids) {
        alert(`Failed to fetch data for the provided task IDs: ${err.message || "Unknown error"}`);
      }
    }
  };

  useEffect(() => {
    // Wait for config to be loaded before fetching data
    if (!configLoaded) {
      return;
    }
    
    const savedMode = localStorage.getItem("regressionDashboardInputMode") || "tag";
    let params = {};
    
    if (savedMode === "tag" && tag) {
      params = { tag: tag };
    } else if (savedMode === "task_ids") {
      const savedTaskIds = localStorage.getItem("regressionDashboardTaskIds");
      if (savedTaskIds) {
        try {
          const taskIds = JSON.parse(savedTaskIds);
          if (taskIds && taskIds.length > 0) {
            params = { task_ids: taskIds.join(",") };
          } else {
            setLoading(false);
            return;
          }
        } catch (e) {
          console.error("Error parsing task IDs:", e);
          setLoading(false);
          return;
        }
      } else {
        setLoading(false);
        return;
      }
    } else {
      setLoading(false);
      return;
    }
    
    fetchData(params);
  }, [tag, hiddenBranches, inputMode, taskIdsKey, configLoaded]);

  // Load Triage Count automatically on page load and when tag/task_ids change
  useEffect(() => {
    const savedMode = localStorage.getItem("regressionDashboardInputMode") || "tag";
    
    // Only load if we have valid parameters
    if (savedMode === "tag" && tag) {
      fetchTriageCount(tag, null);
    } else if (savedMode === "task_ids") {
      const savedTaskIds = localStorage.getItem("regressionDashboardTaskIds");
      if (savedTaskIds) {
        try {
          const taskIds = JSON.parse(savedTaskIds);
          if (taskIds && taskIds.length > 0) {
            fetchTriageCount(null, taskIds.join(","));
          }
        } catch (e) {
          console.error("Error parsing saved task IDs:", e);
        }
      }
    }
  }, [tag, inputMode, taskIdsKey]); // Re-run when tag, inputMode, or taskIdsKey changes

  // Load Triage Accuracy Analyzer when enabled and config changes
  useEffect(() => {
    if (!advancedOptions.triageAccuracy) return;
    const savedMode = localStorage.getItem("regressionDashboardInputMode") || "tag";
    if (savedMode === "tag" && tag) {
      fetchTriageAccuracy(tag, null);
    } else if (savedMode === "task_ids") {
      const savedTaskIds = localStorage.getItem("regressionDashboardTaskIds");
      if (savedTaskIds) {
        try {
          const taskIds = JSON.parse(savedTaskIds);
          if (taskIds && taskIds.length > 0) {
            fetchTriageAccuracy(null, taskIds.join(","));
          }
        } catch (e) {
          console.error("Error parsing saved task IDs:", e);
        }
      }
    }
  }, [advancedOptions.triageAccuracy, tag, inputMode, taskIdsKey]);

  // Fetch manual tasks for all branches
  const fetchManualTasksForBranches = async (branches) => {
    const tasks = {};
    await Promise.all(
      branches.map(async (branch) => {
        try {
          const res = await api.get(MANUAL_TASKS_API, {
            params: { tag: tag, branch }
          });
          tasks[branch] = res.data.manual_tasks || [];
        } catch (err) {
          console.error(`Error fetching manual tasks for ${branch}:`, err);
          tasks[branch] = [];
        }
      })
    );
    setManualTasks(tasks);
  };

  // Add manual task
  const handleAddManualTask = async (branch) => {
    if (!newTaskId.trim()) return;

    try {
      const res = await api.post(MANUAL_TASKS_API, {
        tag: tag,
        branch,
        task_ids: [newTaskId.trim()]
      });
      setManualTasks(prev => ({
        ...prev,
        [branch]: res.data.manual_tasks
      }));
      setNewTaskId("");
      setEditingBranch(null);
    } catch (err) {
      console.error("Error adding manual task:", err);
      alert("Failed to add manual task. Please try again.");
    }
  };

  // Remove manual task
  const handleRemoveManualTask = async (branch, taskId) => {
    try {
      const res = await api.delete(MANUAL_TASKS_API, {
        params: { tag: tag, branch, task_id: taskId }
      });
      setManualTasks(prev => ({
        ...prev,
        [branch]: res.data.manual_tasks
      }));
    } catch (err) {
      console.error("Error removing manual task:", err);
      alert("Failed to remove manual task. Please try again.");
    }
  };

  // Handle configuration save
  const handleSaveConfig = async () => {
    try {
      if (modalInputMode === "tag") {
        const selectedTag = defaultTag || null;
        
        // Save to JSON file
        const configData = {
          input_mode: "tag",
          default_tag: selectedTag,
          added_tags: addedTags,
          tag: selectedTag || "",
          task_ids: []
        };
        await api.post(CONFIG_API, configData);
        
        // Update local state
        setTag(selectedTag || null);
        setConfigTagInput(selectedTag || "");
        localStorage.setItem("regressionDashboardTag", selectedTag || "");
        localStorage.setItem("regressionDashboardInputMode", "tag");
        localStorage.removeItem("regressionDashboardTaskIds");
        setTaskIdsKey(null);
        
        // Update global inputMode state (this will trigger useEffects)
        setInputMode("tag");
        
        // Close modal first
        setShowConfigModal(false);
        
        // Clear cached QI data so buttons re-appear for fresh load
        setBranchQiData({});

        // Fetch data only if a tag is selected
        if (selectedTag) {
          await fetchData({ tag: selectedTag });
          await fetchTriageCount(selectedTag);
          try {
            if (advancedOptions.qiSummaryReport) {
              await fetchQiSummaryReport(selectedTag);
            } else {
              setQiSummaryReport(null);
              setLoadingQiSummary(false);
            }
            if (advancedOptions.triageAccuracy) {
              await fetchTriageAccuracy(selectedTag);
            }
          } catch (error) {
            console.error("Error loading advanced options data:", error);
          }
        } else {
          setRows([]);
          setTriageCount(null);
          setTriageAccuracyData(null);
          setQiSummaryReport(null);
        }
      } else {
        // Task IDs mode
        const taskIds = parseTaskIds(configTaskIdsInput);
        if (!taskIds || taskIds.length === 0) {
          alert("Please enter JITA Task IDs (comma-separated) or a JITA task link");
          return;
        }
        
        // Save to JSON file
        const configData = {
          input_mode: "task_ids",
          tag: "",
          task_ids: taskIds
        };
        await api.post(CONFIG_API, configData);
        
        // Store task IDs and clear tag
        setTag(null);
        setDefaultTag(null);
        localStorage.removeItem("regressionDashboardTag");
        localStorage.setItem("regressionDashboardInputMode", "task_ids");
        const taskIdsString = JSON.stringify(taskIds);
        localStorage.setItem("regressionDashboardTaskIds", taskIdsString);
        
        // Update global inputMode state and taskIdsKey state (this will trigger useEffects)
        setInputMode("task_ids");
        setTaskIdsKey(taskIdsString); // Update key to trigger useEffect
        
        // Close modal first
        setShowConfigModal(false);

        // Clear cached QI data so buttons re-appear for fresh load
        setBranchQiData({});
        
        // Fetch data automatically after save
        await fetchData({ task_ids: taskIds.join(",") });
        
        // Triage Count is always loaded by default, checkbox only controls visibility
        // Refresh triage count using task IDs
        await fetchTriageCount(null, taskIds.join(","));
        
        // Fetch data if advanced options are enabled
        try {
          if (advancedOptions.qiSummaryReport) {
            await fetchQiSummaryReport(null, taskIds.join(","));
          } else {
            setQiSummaryReport(null);
            setLoadingQiSummary(false);
          }
          if (advancedOptions.triageAccuracy) {
            await fetchTriageAccuracy(null, taskIds.join(","));
          }
        } catch (error) {
          console.error("Error loading advanced options data:", error);
        }
      }
      
      // Save advanced options
      localStorage.setItem("regressionDashboardAdvancedOptions", JSON.stringify(advancedOptions));
    } catch (error) {
      console.error("Error saving configuration:", error);
      alert("Failed to save configuration. Please try again.");
    }
  };

  // Add tag to added_tags (lenient - adds even if JITA validation fails)
  const handleAddTag = async () => {
    const tagToAdd = newTagInput.trim();
    if (!tagToAdd) {
      alert("Tag name cannot be empty");
      return;
    }
    setLoadingBranches(true);
    try {
      const response = await api.post(CONFIG_TAGS_API, { tag: tagToAdd });
      const updatedAdded = response.data.added_tags || [];
      setAddedTags(updatedAdded);
      setNewTagInput("");
      setAvailableBranches([]);
      // Optionally set newly added tag as default for quick selection
      if (!defaultTag && updatedAdded.includes(tagToAdd)) {
        setDefaultTag(tagToAdd);
        setConfigTagInput(tagToAdd);
      }
      // Preload triage accuracy data in background (saves to per-tag JSON)
      api.get(`${API_BASE_URL}/mcp/regression/triage-accuracy`, { params: { tag: tagToAdd } })
        .then(() => { /* cache warmed */ })
        .catch(() => { /* non-blocking; user can load later when selecting tag */ });
    } catch (err) {
      alert(err.response?.data?.error || "Failed to add tag.");
    } finally {
      setLoadingBranches(false);
    }
  };

  // Delete tag from added_tags (also removes per-tag triage JSON)
  const handleDeleteTag = async (tagToDelete) => {
    if (!window.confirm(`Delete tag "${tagToDelete}"? This will also remove its triage accuracy data.`)) return;
    try {
      const response = await api.delete(CONFIG_TAGS_API, {
        params: { tag: tagToDelete }
      });
      setAddedTags(response.data.added_tags || []);
      if (defaultTag === tagToDelete) {
        setDefaultTag(null);
        setTag(null);
        setConfigTagInput("");
      }
    } catch (err) {
      alert(err.response?.data?.error || "Failed to delete tag.");
    }
  };

  // Fetch branches from tag
  const fetchBranchesFromTag = async (tagName) => {
    if (!tagName.trim()) {
      alert("Tag name cannot be empty");
      return;
    }
    
    setLoadingBranches(true);
    try {
      const response = await api.get(`${API_BASE_URL}/mcp/regression/branches`, {
        params: { tag: tagName.trim() }
      });
      setAvailableBranches(response.data.branches || []);
    } catch (err) {
      console.error("Error fetching branches:", err);
      alert("Failed to fetch branches. Please check if the tag name is correct.");
      setAvailableBranches([]);
    } finally {
      setLoadingBranches(false);
    }
  };

  // Add new branch
  const handleAddNewBranch = (branchName) => {
    if (!branchName || !branchName.trim()) {
      alert("Please select a branch");
      return;
    }
    
    const branch = branchName.trim();
    
    // Remove from hidden branches if it was hidden
    setHiddenBranches(prev => {
      const updated = prev.filter(b => b !== branch);
      localStorage.setItem("regressionDashboardHiddenBranches", JSON.stringify(updated));
      return updated;
    });
    
    setNewBranchTagInput("");
    setAvailableBranches([]);
    
    // The table will automatically refresh due to useEffect dependency on hiddenBranches
    alert(`Branch "${branch}" will be shown in the table.`);
  };

  // Delete branch (hide it)
  const handleDeleteBranch = (branchName) => {
    if (window.confirm(`Are you sure you want to hide branch "${branchName}" from the table?`)) {
      setHiddenBranches(prev => {
        if (!prev.includes(branchName)) {
          const updated = [...prev, branchName];
          localStorage.setItem("regressionDashboardHiddenBranches", JSON.stringify(updated));
          return updated;
        }
        return prev;
      });
      alert(`Branch "${branchName}" has been hidden from the table.`);
    }
  };

  // Fetch Triage Accuracy Analyzer - supports both tag and task_ids; reload=true invalidates cache
  const fetchTriageAccuracy = async (tagToUse = null, taskIdsToUse = null, reload = false) => {
    setLoadingTriageAccuracy(true);
    try {
      const params = {};
      if (tagToUse || tag) {
        params.tag = tagToUse || tag;
      } else if (taskIdsToUse) {
        params.task_ids = Array.isArray(taskIdsToUse) ? taskIdsToUse.join(",") : taskIdsToUse;
      } else {
        const savedMode = localStorage.getItem("regressionDashboardInputMode") || "tag";
        if (savedMode === "tag" && tag) {
          params.tag = tag;
        } else if (savedMode === "task_ids") {
          const savedTaskIds = localStorage.getItem("regressionDashboardTaskIds");
          if (savedTaskIds) {
            params.task_ids = JSON.parse(savedTaskIds).join(",");
          } else {
            setLoadingTriageAccuracy(false);
            return;
          }
        } else {
          setLoadingTriageAccuracy(false);
          return;
        }
      }
      if (reload) {
        params.reload = "true";
        params._t = Date.now(); // Cache-bust to avoid any HTTP caching
      }
      const response = await api.get(`${API_BASE_URL}/mcp/regression/triage-accuracy`, {
        params,
        timeout: 900000, // 15 minutes - Triage Genie lookups can be slow for large runs
        headers: reload ? { "Cache-Control": "no-cache", "Pragma": "no-cache" } : {}
      });
      setTriageAccuracyData(response.data);
    } catch (err) {
      console.error("Error fetching triage accuracy:", err);
      const status = err.response?.status;
      const data = err.response?.data;
      let msg;
      if (status === 404) {
        msg = "Triage Accuracy endpoint not found (404). Restart the Flask backend to load the latest routes: ./start_backend.sh or python3 backend/test_flask.py";
      } else if (typeof data === "object" && data?.error) {
        msg = data.error;
      } else if (data && typeof data === "string" && (data.toLowerCase().includes("<!doctype") || data.toLowerCase().includes("<html"))) {
        msg = status ? `Backend returned ${status}. Ensure the Flask backend is running and REACT_APP_API_URL is correct. Restart backend: ./start_backend.sh` : "Invalid response from server. Check that the backend is running.";
      } else {
        const networkError = err.code === "ECONNABORTED" ? "Request timed out (15 min)" : err.message || "";
        msg = (status ? `Backend returned ${status}. ` : "") + (networkError ? `Network: ${networkError}. ` : "") + "Ensure Flask backend is running (./start_backend.sh) and REACT_APP_API_URL points to it.";
      }
      setTriageAccuracyData({ error: msg.trim() });
    } finally {
      setLoadingTriageAccuracy(false);
    }
  };

  // Reload Triage Accuracy data (invalidate cache + refetch from JITA/Triage Genie)
  // Uses same param resolution as initial load (useEffect) to ensure correct tag/task_ids
  const handleReloadTriageAccuracy = () => {
    const savedMode = localStorage.getItem("regressionDashboardInputMode") || "tag";
    if (savedMode === "tag" && (tag || defaultTag)) {
      const effectiveTag = tag || defaultTag;
      fetchTriageAccuracy(effectiveTag, null, true);
    } else if (savedMode === "task_ids") {
      const savedTaskIds = localStorage.getItem("regressionDashboardTaskIds");
      if (savedTaskIds) {
        try {
          const taskIds = JSON.parse(savedTaskIds);
          if (taskIds && taskIds.length > 0) {
            fetchTriageAccuracy(null, taskIds.join(","), true);
          } else {
            alert("No task IDs configured. Configure JITA Task IDs in Configuration first.");
          }
        } catch (e) {
          alert("Invalid task IDs in config.");
        }
      } else {
        alert("No task IDs configured. Configure JITA Task IDs in Configuration first.");
      }
    } else {
      alert("No tag or task IDs configured. Configure in Configuration first.");
    }
  };

  // Download Excel report for Triage Accuracy
  const handleDownloadTriageAccuracyExcel = async () => {
    try {
      const params = {};
      const effectiveTag = tag || defaultTag;
      if (inputMode === "tag" && effectiveTag) {
        params.tag = effectiveTag;
      }
      const response = await api.get(`${API_BASE_URL}/mcp/regression/triage-accuracy/export-excel`, {
        params,
        responseType: "blob",
      });
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", "triage_accuracy_report.xlsx");
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      console.error("Error downloading Excel:", err);
      alert(err.response?.data?.error || "Failed to download Excel report. Load Triage Accuracy data first.");
    }
  };

  // Fetch Triage Count - supports both tag and task_ids
  // By default, exclude bulk issues QI calculation for faster loading
  const fetchTriageCount = async (tagToUse = null, taskIdsToUse = null, includeBulkQi = false) => {
    setLoadingTriage(true);
    try {
      const params = {};
      if (tagToUse || tag) {
        params.tag = tagToUse || tag;
      } else if (taskIdsToUse) {
        params.task_ids = Array.isArray(taskIdsToUse) ? taskIdsToUse.join(",") : taskIdsToUse;
      } else {
        // Try to get from localStorage
        const savedMode = localStorage.getItem("regressionDashboardInputMode") || "tag";
        if (savedMode === "tag" && tag) {
          params.tag = tag;
        } else if (savedMode === "task_ids") {
          const savedTaskIds = localStorage.getItem("regressionDashboardTaskIds");
          if (savedTaskIds) {
            params.task_ids = JSON.parse(savedTaskIds).join(",");
          } else {
            setLoadingTriage(false);
            return;
          }
        } else {
          setLoadingTriage(false);
          return;
        }
      }
      
      // Only include bulk QI calculation if explicitly requested
      if (includeBulkQi) {
        params.include_bulk_qi = "true";
      }
      
      const response = await api.get(`${API_BASE_URL}/mcp/regression/triage-count`, {
        params,
        timeout: 180000 // 3 minutes timeout
      });
      setTriageCount(response.data);
    } catch (err) {
      console.error("Error fetching triage count:", err);
      setTriageCount({ error: "Failed to fetch triage count. Please check backend endpoint." });
    } finally {
      setLoadingTriage(false);
    }
  };

  // Fetch Bulk Issues QI Impact separately (when button is clicked)
  const fetchBulkIssuesQi = async () => {
    if (!triageCount || triageCount.error) {
      return; // Need triage count data first
    }
    
    setLoadingBulkQi(true);
    try {
      const params = {};
      const savedMode = localStorage.getItem("regressionDashboardInputMode") || "tag";
      
      if (savedMode === "tag" && tag) {
        params.tag = tag;
      } else if (savedMode === "task_ids") {
        const savedTaskIds = localStorage.getItem("regressionDashboardTaskIds");
        if (savedTaskIds) {
          params.task_ids = JSON.parse(savedTaskIds).join(",");
        } else {
          setLoadingBulkQi(false);
          return;
        }
      } else {
        setLoadingBulkQi(false);
        return;
      }
      
      // Request bulk QI calculation
      params.include_bulk_qi = "true";
      
      const response = await api.get(`${API_BASE_URL}/mcp/regression/triage-count`, {
        params,
        timeout: 300000 // 5 minutes timeout for QI calculation
      });
      
      // Update triage count with bulk issues QI data
      if (response.data.bulk_issues_with_qi) {
        setTriageCount(prev => ({
          ...prev,
          bulk_issues_with_qi: response.data.bulk_issues_with_qi
        }));
      }
    } catch (err) {
      console.error("Error fetching bulk issues QI:", err);
      // Don't set error, just log it
    } finally {
      setLoadingBulkQi(false);
    }
  };

  // Fetch QI Summary Report - supports both tag and task_ids
  const fetchQiSummaryReport = async (tagToUse = null, taskIdsToUse = null) => {
    setLoadingQiSummary(true);
    try {
      const params = {};
      if (tagToUse || tag) {
        params.tag = tagToUse || tag;
      } else if (taskIdsToUse) {
        params.task_ids = Array.isArray(taskIdsToUse) ? taskIdsToUse.join(",") : taskIdsToUse;
      } else {
        // Try to get from localStorage
        const savedMode = localStorage.getItem("regressionDashboardInputMode") || "tag";
        if (savedMode === "tag" && tag) {
          params.tag = tag;
        } else if (savedMode === "task_ids") {
          const savedTaskIds = localStorage.getItem("regressionDashboardTaskIds");
          if (savedTaskIds) {
            params.task_ids = JSON.parse(savedTaskIds).join(",");
          } else {
            setLoadingQiSummary(false);
            return;
          }
        } else {
          setLoadingQiSummary(false);
          return;
        }
      }
      
      const response = await api.get(`${API_BASE_URL}/mcp/regression/qi-summary`, {
        params,
        timeout: 180000 // 3 minutes timeout
      });
      setQiSummaryReport(response.data);
    } catch (err) {
      console.error("Error fetching QI Summary Report:", err);
      setQiSummaryReport({ error: "Failed to fetch QI Summary Report. Please check backend endpoint." });
    } finally {
      setLoadingQiSummary(false);
    }
  };

  const resolveTeamName = (currentTag) => {
    if (!teamConfig) return "CDP";
    const cfg = teamConfig[currentTag] || teamConfig["default"];
    return cfg ? cfg.team : "CDP";
  };

  const fetchBranchQi = async (branch, timeFilter) => {
    const qiKey = `${branch}_${timeFilter}`;
    setBranchQiLoading((prev) => ({ ...prev, [qiKey]: true }));
    try {
      const teamName = resolveTeamName(tag);
      const dateOnly = timeFilter === "all" ? "all" : timeFilter.split(" ")[0];
      const response = await api.get(TCMS_OVERALL_QI_API, {
        params: { team_name: teamName, branch_name: branch, time_filter: dateOnly },
        timeout: 60000,
      });
      const qiValue = response.data?.qi_value;
      setBranchQiData((prev) => ({
        ...prev,
        [branch]: {
          ...prev[branch],
          [timeFilter === "all" ? "overall" : "customDate"]: qiValue,
          [timeFilter === "all" ? "overallDetail" : "customDateDetail"]: response.data,
        },
      }));
    } catch (err) {
      console.error(`Error fetching QI for branch ${branch}:`, err);
      setBranchQiData((prev) => ({
        ...prev,
        [branch]: {
          ...prev[branch],
          [timeFilter === "all" ? "overall" : "customDate"]: "error",
        },
      }));
    } finally {
      setBranchQiLoading((prev) => ({ ...prev, [qiKey]: false }));
    }
  };

  // Note: Advanced options data is NOT loaded automatically on page load
  // Data is only fetched when user explicitly saves the advanced options

  if (loading) {
    return (
      <div className="container">
        <div style={{ padding: "20px", textAlign: "center" }}>
          <div>Loading Regression Dashboard...</div>
        </div>
      </div>
    );
  }
  
    return (
      <div className="container">
      <div style={{ 
        display: "flex", 
        justifyContent: "space-between", 
        alignItems: "center", 
        marginBottom: "20px", 
        flexWrap: "wrap", 
        gap: "10px" 
      }}>
        <h2 style={{ margin: 0 }}>Regression Dashboard</h2>
        <div style={{ 
          display: "flex", 
          gap: "10px", 
          flexWrap: "nowrap",
          alignItems: "center"
        }}>
          {inputMode === "tag" && (
            <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <label style={{ fontSize: "13px", fontWeight: "500", whiteSpace: "nowrap" }}>Tag:</label>
              <select
                value={tag || defaultTag || ""}
                onChange={async (e) => {
                  const selected = e.target.value || null;
                  if (selected === (tag || defaultTag)) return;
                  setTag(selected);
                  setDefaultTag(selected);
                  localStorage.setItem("regressionDashboardTag", selected || "");
                  if (selected) {
                    try {
                      await api.post(CONFIG_API, {
                        input_mode: "tag",
                        default_tag: selected,
                        added_tags: addedTags,
                        tag: selected,
                        task_ids: []
                      });
                    } catch (err) {
                      console.error("Failed to save tag config:", err);
                    }
                    setBranchQiData({});
                    await fetchData({ tag: selected });
                    await fetchTriageCount(selected);
                    if (advancedOptions.qiSummaryReport) await fetchQiSummaryReport(selected);
                    if (advancedOptions.triageAccuracy) await fetchTriageAccuracy(selected);
                  } else {
                    setRows([]);
                    setTriageCount(null);
                    setTriageAccuracyData(null);
                    setQiSummaryReport(null);
                    setBranchQiData({});
                    try {
                      await api.post(CONFIG_API, {
                        input_mode: "tag",
                        default_tag: null,
                        added_tags: addedTags,
                        tag: "",
                        task_ids: []
                      });
                    } catch (err) {
                      console.error("Failed to save tag config:", err);
                    }
                  }
                }}
                style={{
                  padding: "6px 10px",
                  fontSize: "13px",
                  border: "1px solid #ddd",
                  borderRadius: "4px",
                  minWidth: "180px",
                  background: "white",
                  cursor: "pointer"
                }}
                title="Quick-select tag to load regression overview"
              >
                <option value="">None</option>
                {addedTags.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>
          )}
          <button
            onClick={() => {
              // Restore input mode and values from localStorage
              const savedMode = localStorage.getItem("regressionDashboardInputMode") || "tag";
              setModalInputMode(savedMode); // Use modal-specific state
              if (savedMode === "tag") {
                setConfigTagInput(tag || "");
                setConfigTaskIdsInput("");
              } else {
                const savedTaskIds = localStorage.getItem("regressionDashboardTaskIds");
                setConfigTaskIdsInput(savedTaskIds ? JSON.parse(savedTaskIds).join(", ") : "");
                setConfigTagInput("");
              }
              setShowConfigModal(true);
            }}
            style={{
              padding: "8px 16px",
              background: "#6c757d",
              color: "white",
              border: "none",
              borderRadius: "4px",
              cursor: "pointer",
              fontSize: "14px",
              display: "inline-flex",
              alignItems: "center",
              gap: "6px",
              whiteSpace: "nowrap",
              minWidth: "fit-content"
            }}
            title="Configuration Settings"
          >
            ⚙️ Configuration
          </button>
        </div>
      </div>

      {/* Configuration Modal */}
      {showConfigModal && (
        <div 
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: "rgba(0, 0, 0, 0.5)",
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            zIndex: 1000
          }}
          onClick={() => setShowConfigModal(false)}
        >
          <div 
            style={{
              background: "white",
              padding: "24px",
              borderRadius: "8px",
              boxShadow: "0 4px 6px rgba(0, 0, 0, 0.1)",
              minWidth: "500px",
              maxWidth: "90%",
              maxHeight: "90vh",
              overflowY: "auto"
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginTop: 0, marginBottom: "20px", color: "#333" }}>Configuration</h3>
            
            {/* Input Mode Selection */}
            <div style={{ marginBottom: "20px" }}>
              <label style={{ display: "block", marginBottom: "10px", fontWeight: "bold" }}>
                Fetch Regression Overview By:
              </label>
              <div style={{ display: "flex", gap: "20px", marginBottom: "15px" }}>
                <label style={{ display: "flex", alignItems: "center", cursor: "pointer" }}>
                  <input
                    type="radio"
                    name="inputMode"
                    value="tag"
                    checked={modalInputMode === "tag"}
                    onChange={(e) => setModalInputMode(e.target.value)}
                    style={{ marginRight: "8px" }}
                  />
                  Default Tag Name
                </label>
                <label style={{ display: "flex", alignItems: "center", cursor: "pointer" }}>
                  <input
                    type="radio"
                    name="inputMode"
                    value="task_ids"
                    checked={modalInputMode === "task_ids"}
                    onChange={(e) => setModalInputMode(e.target.value)}
                    style={{ marginRight: "8px" }}
                  />
                  JITA Task IDs / Link
                </label>
        </div>
            </div>
            
            {/* Tag Mode: Default Tag Name + Added Tag List */}
            {modalInputMode === "tag" && (
            <>
            <div style={{ marginBottom: "20px" }}>
              <label style={{ display: "block", marginBottom: "5px", fontWeight: "bold" }}>
                1. Default Tag Name:
              </label>
              <select
                value={defaultTag || ""}
                onChange={(e) => {
                  const v = e.target.value;
                  setDefaultTag(v || null);
                  setConfigTagInput(v || "");
                }}
                style={{
                  width: "100%",
                  padding: "8px",
                  fontSize: "14px",
                  border: "1px solid #ddd",
                  borderRadius: "4px",
                  boxSizing: "border-box"
                }}
              >
                <option value="">None</option>
                {addedTags.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
              {defaultTag && (
                <div style={{ marginTop: "10px" }}>
                  <button
                    type="button"
                    onClick={() => fetchBranchesFromTag(defaultTag)}
                    disabled={loadingBranches}
                    style={{
                      padding: "6px 12px",
                      fontSize: "12px",
                      background: loadingBranches ? "#ccc" : "#17a2b8",
                      color: "white",
                      border: "none",
                      borderRadius: "4px",
                      cursor: loadingBranches ? "not-allowed" : "pointer"
                    }}
                  >
                    {loadingBranches ? "Loading..." : "Fetch Branches"}
                  </button>
                  {availableBranches.length > 0 && (
                    <select
                      onChange={(e) => {
                        if (e.target.value) handleAddNewBranch(e.target.value);
                      }}
                      style={{
                        marginLeft: "10px",
                        padding: "6px",
                        fontSize: "13px",
                        border: "1px solid #ddd",
                        borderRadius: "4px"
                      }}
                    >
                      <option value="">-- Select branch to show --</option>
                      {availableBranches.map((b) => (
                        <option key={b} value={b}>{b}</option>
                      ))}
                    </select>
                  )}
                </div>
              )}
            </div>
            <div style={{ marginBottom: "20px" }}>
              <label style={{ display: "block", marginBottom: "5px", fontWeight: "bold" }}>
                2. Added Tag List:
              </label>
              <div style={{ display: "flex", gap: "8px", marginBottom: "8px" }}>
                <input
                  type="text"
                  value={newTagInput}
                  onChange={(e) => setNewTagInput(e.target.value)}
                  placeholder="Enter tag name to add"
                  style={{
                    flex: 1,
                    padding: "8px",
                    fontSize: "14px",
                    border: "1px solid #ddd",
                    borderRadius: "4px"
                  }}
                  onKeyDown={(e) => e.key === "Enter" && handleAddTag()}
                />
                <button
                  onClick={handleAddTag}
                  disabled={loadingBranches || !newTagInput.trim()}
                  style={{
                    padding: "8px 16px",
                    background: loadingBranches || !newTagInput.trim() ? "#ccc" : "#28a745",
                    color: "white",
                    border: "none",
                    borderRadius: "4px",
                    cursor: loadingBranches || !newTagInput.trim() ? "not-allowed" : "pointer",
                    whiteSpace: "nowrap"
                  }}
                >
                  {loadingBranches ? "Adding..." : "Fetch & Add"}
                </button>
              </div>
              {addedTags.length > 0 ? (
                <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
                  {addedTags.map((t) => (
                    <li key={t} style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "6px", padding: "6px", background: "#f8f9fa", borderRadius: "4px" }}>
                      <span style={{ flex: 1 }}>{t}</span>
                      <button
                        type="button"
                        onClick={() => handleDeleteTag(t)}
                        title="Delete tag"
                        style={{
                          padding: "4px 10px",
                          background: "#dc3545",
                          color: "white",
                          border: "none",
                          borderRadius: "4px",
                          cursor: "pointer",
                          fontSize: "12px"
                        }}
                      >
                        Delete
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <div style={{ color: "#666", fontSize: "13px" }}>No tags added yet. Add a tag above.</div>
              )}
            </div>
            </>
            )}
            
            {/* JITA Task IDs / Link */}
            {modalInputMode === "task_ids" && (
            <div style={{ marginBottom: "20px" }}>
              <label style={{ display: "block", marginBottom: "5px", fontWeight: "bold" }}>
                1. JITA Task IDs or Link:
              </label>
              <textarea
                value={configTaskIdsInput}
                onChange={(e) => setConfigTaskIdsInput(e.target.value)}
                placeholder="Enter comma-separated task IDs (e.g., 69786c3e2bc0c4e5a95ff046,69786c032bc0c4e5b6bee89f) or JITA link (e.g., https://jita.eng.nutanix.com/results?task_ids=69786c3e2bc0c4e5a95ff046,69786c032bc0c4e5b6bee89f)"
                style={{
                  width: "100%",
                  padding: "8px",
                  fontSize: "14px",
                  border: "1px solid #ddd",
                  borderRadius: "4px",
                  boxSizing: "border-box",
                  minHeight: "80px",
                  resize: "vertical",
                  fontFamily: "monospace"
                }}
              />
              <small style={{ display: "block", marginTop: "5px", color: "#666", fontSize: "12px" }}>
                You can enter either comma-separated task IDs or paste a JITA results link. The link will be automatically parsed to extract task IDs.
              </small>
            </div>
            )}

            {/* Delete Branch */}
            <div style={{ marginBottom: "20px" }}>
              <label style={{ display: "block", marginBottom: "5px", fontWeight: "bold" }}>
                {modalInputMode === "tag" ? "3" : "2"}. Hide Branch:
              </label>
              {rows.length > 0 ? (
                <select
                  onChange={(e) => {
                    if (e.target.value) {
                      handleDeleteBranch(e.target.value);
                      e.target.value = ""; // Reset selection
                    }
                  }}
                  style={{
                    width: "100%",
                    padding: "8px",
                    fontSize: "14px",
                    border: "1px solid #ddd",
                    borderRadius: "4px",
                    boxSizing: "border-box"
                  }}
                >
                  <option value="">-- Select a branch to hide --</option>
                  {rows.map((row) => (
                    <option key={row.branch} value={row.branch}>
                      {row.branch}
                    </option>
                  ))}
                </select>
              ) : (
                <div style={{ color: "#666", fontSize: "13px" }}>No branches available</div>
              )}
            </div>

            {/* Advanced Options */}
            <div style={{ marginBottom: "20px", paddingTop: "20px", borderTop: "1px solid #ddd" }}>
              <label style={{ display: "block", marginBottom: "15px", fontWeight: "bold", fontSize: "16px" }}>
                {modalInputMode === "tag" ? "4" : "3"}. Advanced Options:
              </label>
              
              <div style={{ marginBottom: "15px" }}>
                <label style={{ 
                  display: "flex", 
                  alignItems: "center", 
                  gap: "10px", 
                  marginBottom: "12px", 
                  cursor: "pointer",
                  padding: "8px",
                  borderRadius: "4px",
                  transition: "background 0.2s"
                }}
                onMouseEnter={(e) => e.currentTarget.style.background = "#f8f9fa"}
                onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}
                >
                  <input
                    type="checkbox"
                    checked={advancedOptions.triageCount || false}
                    onChange={(e) => {
                      setAdvancedOptions(prev => ({
                        ...prev,
                        triageCount: e.target.checked
                      }));
                    }}
                    style={{ width: "18px", height: "18px", cursor: "pointer" }}
                  />
                  <span style={{ fontSize: "14px", fontWeight: "500" }}>Triage Count by Owner</span>
                </label>
              </div>

              <div style={{ marginBottom: "15px" }}>
                <label style={{ 
                  display: "flex", 
                  alignItems: "center", 
                  gap: "10px", 
                  marginBottom: "12px", 
                  cursor: "pointer",
                  padding: "8px",
                  borderRadius: "4px",
                  transition: "background 0.2s"
                }}
                onMouseEnter={(e) => e.currentTarget.style.background = "#f8f9fa"}
                onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}
                >
                  <input
                    type="checkbox"
                    checked={advancedOptions.triageAccuracy || false}
                    onChange={(e) => {
                      setAdvancedOptions(prev => ({
                        ...prev,
                        triageAccuracy: e.target.checked
                      }));
                    }}
                    style={{ width: "18px", height: "18px", cursor: "pointer" }}
                  />
                  <span style={{ fontSize: "14px", fontWeight: "500" }}>Triage Accuracy Analyzer</span>
                </label>
              </div>

              <div style={{ marginBottom: "15px" }}>
                <label style={{ 
                  display: "flex", 
                  alignItems: "center", 
                  gap: "10px", 
                  marginBottom: "12px", 
                  cursor: "pointer",
                  padding: "8px",
                  borderRadius: "4px",
                  transition: "background 0.2s"
                }}
                onMouseEnter={(e) => e.currentTarget.style.background = "#f8f9fa"}
                onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}
                >
                  <input
                    type="checkbox"
                    checked={advancedOptions.qiSummaryReport || false}
                    onChange={(e) => {
                      setAdvancedOptions(prev => ({
                        ...prev,
                        qiSummaryReport: e.target.checked
                      }));
                    }}
                    style={{ width: "18px", height: "18px", cursor: "pointer" }}
                  />
                  <span style={{ fontSize: "14px", fontWeight: "500" }}>QI Summary Report</span>
                </label>
              </div>

              <div style={{ marginBottom: "15px" }}>
                <label style={{ 
                  display: "flex", 
                  alignItems: "center", 
                  gap: "10px", 
                  marginBottom: "12px", 
                  cursor: "not-allowed",
                  padding: "8px",
                  borderRadius: "4px",
                  transition: "background 0.2s",
                  opacity: 0.6
                }}
                onMouseEnter={(e) => e.currentTarget.style.background = "#f8f9fa"}
                onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}
                >
                  <input
                    type="checkbox"
                    checked={advancedOptions.flakyTestInsights || false}
                    onChange={(e) => {
                      setAdvancedOptions(prev => ({
                        ...prev,
                        flakyTestInsights: e.target.checked
                      }));
                    }}
                    style={{ width: "18px", height: "18px", cursor: "not-allowed" }}
                    disabled
                  />
                  <span style={{ fontSize: "14px", fontWeight: "500" }}>(Future) Flaky Test Insights</span>
                </label>
              </div>

              <div style={{ marginBottom: "15px" }}>
                <label style={{ 
                  display: "flex", 
                  alignItems: "center", 
                  gap: "10px", 
                  marginBottom: "12px", 
                  cursor: "not-allowed",
                  padding: "8px",
                  borderRadius: "4px",
                  transition: "background 0.2s",
                  opacity: 0.6
                }}
                onMouseEnter={(e) => e.currentTarget.style.background = "#f8f9fa"}
                onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}
                >
                  <input
                    type="checkbox"
                    checked={advancedOptions.aiRootCauseSummary || false}
                    onChange={(e) => {
                      setAdvancedOptions(prev => ({
                        ...prev,
                        aiRootCauseSummary: e.target.checked
                      }));
                    }}
                    style={{ width: "18px", height: "18px", cursor: "not-allowed" }}
                    disabled
                  />
                  <span style={{ fontSize: "14px", fontWeight: "500" }}>(Future) AI Root Cause Summary</span>
                </label>
              </div>

              <div style={{ marginBottom: "15px" }}>
                <label style={{ 
                  display: "flex", 
                  alignItems: "center", 
                  gap: "10px", 
                  marginBottom: "12px", 
                  cursor: "not-allowed",
                  padding: "8px",
                  borderRadius: "4px",
                  transition: "background 0.2s",
                  opacity: 0.6
                }}
                onMouseEnter={(e) => e.currentTarget.style.background = "#f8f9fa"}
                onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}
                >
                  <input
                    type="checkbox"
                    checked={advancedOptions.regressionRiskScore || false}
                    onChange={(e) => {
                      setAdvancedOptions(prev => ({
                        ...prev,
                        regressionRiskScore: e.target.checked
                      }));
                    }}
                    style={{ width: "18px", height: "18px", cursor: "not-allowed" }}
                    disabled
                  />
                  <span style={{ fontSize: "14px", fontWeight: "500" }}>(Future) Regression Risk Score</span>
                </label>
              </div>

              <div style={{ marginBottom: "15px" }}>
                <label style={{ 
                  display: "flex", 
                  alignItems: "center", 
                  gap: "10px", 
                  marginBottom: "12px", 
                  cursor: "pointer",
                  padding: "8px",
                  borderRadius: "4px",
                  transition: "background 0.2s"
                }}
                onMouseEnter={(e) => e.currentTarget.style.background = "#f8f9fa"}
                onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}
                >
                  <input
                    type="checkbox"
                    checked={advancedOptions.bulkIssuesQiImpact || false}
                    onChange={(e) => {
                      setAdvancedOptions(prev => ({
                        ...prev,
                        bulkIssuesQiImpact: e.target.checked
                      }));
                    }}
                    style={{ width: "18px", height: "18px", cursor: "pointer" }}
                  />
                  <span style={{ fontSize: "14px", fontWeight: "500" }}>Bulk Issues QI Impacting Testcases</span>
                </label>
              </div>
            </div>

            <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end" }}>
              <button
                onClick={() => setShowConfigModal(false)}
                style={{
                  padding: "8px 16px",
                  background: "#6c757d",
                  color: "white",
                  border: "none",
                  borderRadius: "4px",
                  cursor: "pointer"
                }}
              >
                Cancel
              </button>
              <button
                onClick={handleSaveConfig}
                style={{
                  padding: "8px 16px",
                  background: "#007bff",
                  color: "white",
                  border: "none",
                  borderRadius: "4px",
                  cursor: "pointer"
                }}
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}

        <table className="dashboard-table">
          <thead>
            <tr>
            <th>{inputMode === "task_ids" ? "Task IDs" : "Tag"}</th>
              <th>Branch</th>
              <th>Start Date</th>
              <th>Status</th>
              <th>Actual Tasks</th>
              <th>Manual Tasks</th>
              <th>Merged Tasks</th>
              <th colSpan="2" style={{ textAlign: "center" }}>Tests Overview</th>
              <th style={{ textAlign: "center" }}>Overall QI</th>
            </tr>
          </thead>

          <tbody>
            {rows.map((row) => {
              const branchManualTasks = manualTasks[row.branch] || [];
              const mergedTaskIds = [...row.actualTasks, ...branchManualTasks];
              
              return (
                <tr key={row.branch}>
                <td>
                  {inputMode === "task_ids" ? (
                    (() => {
                      const savedTaskIds = localStorage.getItem("regressionDashboardTaskIds");
                      if (savedTaskIds) {
                        try {
                          const taskIds = JSON.parse(savedTaskIds);
                          return taskIds.length > 3 
                            ? `${taskIds.slice(0, 3).join(", ")}... (${taskIds.length} tasks)`
                            : taskIds.join(", ");
                        } catch (e) {
                          return "Task IDs";
                        }
                      }
                      return "Task IDs";
                    })()
                  ) : (
                    tag || "-"
                  )}
                </td>
                  <td>{row.branch}</td>
                  <td>{row.startDate || "-"}</td>
                  <td className={`status ${(row.status || "").toLowerCase()}`}>
                    {row.status}
                  </td>
                  <td>
                    {renderTaskButton(row.actualTasks, "Regression_Run_Tasks")}
                  </td>
                  <td>
                    {editingBranch === row.branch ? (
                      <div style={{ display: "flex", gap: "5px", alignItems: "center" }}>
                        <input
                          type="text"
                          value={newTaskId}
                          onChange={(e) => setNewTaskId(e.target.value)}
                          placeholder="Enter task ID"
                          style={{ padding: "4px", fontSize: "12px", width: "120px" }}
                          onKeyPress={(e) => {
                            if (e.key === "Enter") {
                              handleAddManualTask(row.branch);
                            }
                          }}
                        />
                        <button
                          onClick={() => handleAddManualTask(row.branch)}
                          style={{ padding: "4px 8px", fontSize: "12px" }}
                        >
                          Add
                        </button>
                        <button
                          onClick={() => {
                            setEditingBranch(null);
                            setNewTaskId("");
                          }}
                          style={{ padding: "4px 8px", fontSize: "12px" }}
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <div>
                        <button
                          onClick={() => setEditingBranch(row.branch)}
                          style={{ padding: "4px 8px", fontSize: "12px", marginBottom: "5px" }}
                        >
                          + Add Task
                        </button>
                        {branchManualTasks.length > 0 && (
                          <div style={{ marginTop: "5px" }}>
                            {branchManualTasks.map((taskId) => (
                              <div
                                key={taskId}
                                style={{
                                  display: "inline-block",
                                  margin: "2px",
                                  padding: "2px 6px",
                                  background: "#f0f0f0",
                                  borderRadius: "3px",
                                  fontSize: "11px"
                                }}
                              >
                                {taskId}
                                <button
                                  onClick={() => handleRemoveManualTask(row.branch, taskId)}
                                  style={{
                                    marginLeft: "5px",
                                    background: "red",
                                    color: "white",
                                    border: "none",
                                    borderRadius: "2px",
                                    cursor: "pointer",
                                    fontSize: "10px",
                                    padding: "1px 4px"
                                  }}
                                >
                                  ×
                                </button>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </td>
                  <td>
                    {renderTaskButton(mergedTaskIds, "Merged_Tasks")}
                  </td>
                  <td style={{ textAlign: "center", verticalAlign: "middle", fontSize: "12px" }}>
                    <div style={{ marginBottom: "10px" }}>
                      <div style={{ fontWeight: "bold", marginBottom: "3px" }}>SUCCEEDED</div>
                    <div style={{ color: "#28a745" }}>{row.succeeded || 0}</div>
                    </div>
                    <div>
                      <div style={{ fontWeight: "bold", marginBottom: "3px" }}>FAILED</div>
                      <div style={{ color: "#dc3545" }}>{row.failed || 0}</div>
                    </div>
                  </td>
                  <td style={{ textAlign: "center", verticalAlign: "middle" }}>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px", fontSize: "12px" }}>
                      <div>
                        <div style={{ fontWeight: "bold", marginBottom: "3px" }}>SKIPPED</div>
                        <div style={{ color: "#ffc107" }}>{row.skipped || 0}</div>
                      </div>
                      <div>
                        <div style={{ fontWeight: "bold", marginBottom: "3px" }}>PENDING</div>
                        <div style={{ color: "#17a2b8" }}>{row.pending || 0}</div>
                      </div>
                      <div>
                        <div style={{ fontWeight: "bold", marginBottom: "3px" }}>WARNING</div>
                        <div style={{ color: "#fd7e14" }}>{row.warning || 0}</div>
                      </div>
                      <div>
                        <div style={{ fontWeight: "bold", marginBottom: "3px" }}>RUNNING</div>
                        <div style={{ color: "#6f42c1" }}>{row.running || 0}</div>
                      </div>
                    </div>
                  </td>
                  <td style={{ textAlign: "center", verticalAlign: "middle", minWidth: "140px" }}>
                    {(() => {
                      const qiData = branchQiData[row.branch];
                      const overallLoading = branchQiLoading[`${row.branch}_all`];
                      const customDateStr = row.startDate ? row.startDate.split(" ")[0] : null;
                      const customLoading = customDateStr && branchQiLoading[`${row.branch}_${row.startDate}`];
                      const qiColor = (val) =>
                        val === "error" ? "#dc3545"
                        : val >= 80 ? "#28a745"
                        : val >= 50 ? "#fd7e14"
                        : "#dc3545";
                      return (
                        <div style={{ fontSize: "12px" }}>
                          {/* Overall QI (time_filter=all) */}
                          <div style={{ marginBottom: "8px" }}>
                            {qiData?.overall != null && qiData.overall !== "error" ? (
                              <div style={{ fontWeight: "bold", color: qiColor(qiData.overall) }}>
                                {qiData.overall}%
                                <div style={{ fontWeight: "normal", color: "#666", fontSize: "10px" }}>Overall QI</div>
                              </div>
                            ) : qiData?.overall === "error" ? (
                              <div style={{ color: "#dc3545", fontSize: "11px" }}>Failed</div>
                            ) : overallLoading ? (
                              <span style={{ color: "#666", fontStyle: "italic" }}>Loading...</span>
                            ) : (
                              <button
                                onClick={() => fetchBranchQi(row.branch, "all")}
                                style={{
                                  padding: "3px 8px", fontSize: "11px", cursor: "pointer",
                                  background: "#007bff", color: "white", border: "none",
                                  borderRadius: "3px",
                                }}
                              >
                                Overall QI
                              </button>
                            )}
                          </div>
                          {/* Custom Date QI (time_filter=startDate) */}
                          {customDateStr && (
                            <div>
                              {qiData?.customDate != null && qiData.customDate !== "error" ? (
                                <div style={{ fontWeight: "bold", color: qiColor(qiData.customDate) }}>
                                  {qiData.customDate}%
                                  <div style={{ fontWeight: "normal", color: "#666", fontSize: "10px" }}>
                                    QI ({customDateStr})
                                  </div>
                                </div>
                              ) : qiData?.customDate === "error" ? (
                                <div style={{ color: "#dc3545", fontSize: "11px" }}>Failed</div>
                              ) : customLoading ? (
                                <span style={{ color: "#666", fontStyle: "italic" }}>Loading...</span>
                              ) : (
                                <button
                                  onClick={() => fetchBranchQi(row.branch, row.startDate)}
                                  style={{
                                    padding: "3px 8px", fontSize: "11px", cursor: "pointer",
                                    background: "#17a2b8", color: "white", border: "none",
                                    borderRadius: "3px",
                                  }}
                                >
                                  QI ({customDateStr})
                                </button>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })()}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

      {/* Triage Count Section */}
      {advancedOptions.triageCount && (
        <div style={{ marginTop: "40px", padding: "20px", background: "#f8f9fa", borderRadius: "8px" }}>
          <h3 style={{ marginTop: 0, marginBottom: "15px", color: "#333" }}>Triage Count by Regression Owner</h3>
          {loadingTriage ? (
            <div style={{ color: "#666", fontStyle: "italic" }}>
              Loading triage count... This may take a minute as the backend processes data...
            </div>
          ) : triageCount ? (
            <div style={{ fontSize: "14px" }}>
              {triageCount.error ? (
                <div style={{ color: "#dc3545" }}>{triageCount.error}</div>
              ) : (
                <div>
                  {/* Display Triage Summary */}
                  {triageCount.triage_summary && (
                    <div style={{ marginBottom: "20px" }}>
                      <h4 style={{ marginBottom: "10px" }}>Triage Summary:</h4>
                      <table style={{ width: "100%", borderCollapse: "collapse", marginBottom: "15px" }}>
                        <thead>
                          <tr style={{ background: "#e9ecef" }}>
                            <th style={{ padding: "8px", border: "1px solid #ddd", textAlign: "left" }}>Owner</th>
                            <th style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>Total Failed</th>
                            <th style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>Triaged</th>
                            <th style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>UnTriaged</th>
                            <th style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>Bulk Issues</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(triageCount.triage_summary).map(([owner, stats]) => (
                            <tr key={owner}>
                              <td style={{ padding: "8px", border: "1px solid #ddd" }}>{owner}</td>
                              <td style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>{stats["Total Failed"]}</td>
                              <td style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center", color: "#28a745" }}>{stats["Triaged"]}</td>
                              <td style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center", color: "#dc3545" }}>{stats["UnTriaged"]}</td>
                              <td style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center", color: "#ffc107" }}>{stats["Bulk Issues"]}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {/* Display Bulk Issues Table - Always shown, QI Impact loaded on demand */}
                  {triageCount.bulk_issues && Object.keys(triageCount.bulk_issues).length > 0 && (
                    <div style={{ marginBottom: "20px" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
                        <h4 style={{ margin: 0 }}>Bulk Issues (tickets with &gt;5 testcases):</h4>
                        {(!triageCount.bulk_issues_with_qi || Object.keys(triageCount.bulk_issues_with_qi).length === 0) && (
                          <button
                            onClick={async () => {
                              await fetchBulkIssuesQi();
                            }}
                            disabled={loadingBulkQi}
                            style={{
                              padding: "6px 12px",
                              background: loadingBulkQi ? "#6c757d" : "#007bff",
                              color: "white",
                              border: "none",
                              borderRadius: "4px",
                              cursor: loadingBulkQi ? "not-allowed" : "pointer",
                              fontSize: "12px",
                              fontWeight: "500"
                            }}
                          >
                            {loadingBulkQi ? "Loading QI Impact..." : "Load QI Impact"}
                          </button>
                        )}
                      </div>
                      <div style={{ overflowX: "auto" }}>
                        <table style={{ width: "100%", borderCollapse: "collapse", marginTop: "10px" }}>
                          <thead>
                            <tr style={{ backgroundColor: "#f8f9fa" }}>
                              <th style={{ padding: "8px", border: "1px solid #ddd", textAlign: "left" }}>Bulk Issue Jita Ticket</th>
                              <th style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>Testcase Impacted</th>
                              <th style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>QI Impact due to this bug</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(() => {
                              // Sort entries: if QI data is available, sort by QI impact, otherwise keep original order
                              const entries = Object.entries(triageCount.bulk_issues);
                              const hasQiData = triageCount.bulk_issues_with_qi && Object.keys(triageCount.bulk_issues_with_qi).length > 0;
                              
                              const sortedEntries = hasQiData
                                ? entries.sort((a, b) => {
                                    const qiA = triageCount.bulk_issues_with_qi[a[0]]?.overall_qi_impact ?? 0;
                                    const qiB = triageCount.bulk_issues_with_qi[b[0]]?.overall_qi_impact ?? 0;
                                    return qiA - qiB; // Sort by QI impact (most negative first)
                                  })
                                : entries;
                              
                              return sortedEntries.map(([ticket, tests]) => {
                                // Check if QI data is available for this ticket
                                const qiData = triageCount.bulk_issues_with_qi?.[ticket];
                                const showLoading = loadingBulkQi && !qiData;
                                
                                return (
                                  <tr key={ticket}>
                                    <td style={{ padding: "8px", border: "1px solid #ddd" }}>
                                      <a
                                        href={`${JIRA_URL}${ticket}`}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        style={{ color: "#0066cc", textDecoration: "none" }}
                                      >
                                        {ticket}
                                      </a>
                                    </td>
                                    <td style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>
                                      {qiData ? qiData.testcase_count : tests.length}
                                    </td>
                                    <td style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>
                                      {showLoading ? (
                                        <span style={{ color: "#666", fontStyle: "italic" }}>Loading...</span>
                                      ) : qiData ? (
                                        qiData.overall_qi_impact.toFixed(2) + "%"
                                      ) : (
                                        "-"
                                      )}
                                    </td>
                                  </tr>
                                );
                              });
                            })()}
                          </tbody>
                        </table>
                      </div>
                      {loadingBulkQi && (
                        <div style={{ color: "#666", fontStyle: "italic", padding: "10px", fontSize: "12px" }}>
                          Calculating QI impact for all bulk issues... This may take a few minutes...
                        </div>
                      )}
                    </div>
                  )}
                  
                  {/* Display Pending Tests */}
                  {triageCount.pending_tests !== undefined && (
                    <div style={{ marginBottom: "10px", color: "#17a2b8" }}>
                      <strong>Pending/Running Tests:</strong> {triageCount.pending_tests}
                    </div>
                  )}
                  
                  {/* Display Owner Ticket Map */}
                  {triageCount.owner_ticket_map && (
                    <div style={{ marginTop: "20px" }}>
                      <h4 style={{ marginBottom: "10px" }}>Owner-wise Jira Ticket Breakdown:</h4>
                      {Object.entries(triageCount.owner_ticket_map).map(([owner, tickets]) => (
                        <div key={owner} style={{ marginBottom: "15px" }}>
                          <strong>{owner}:</strong>
                          <ul style={{ marginLeft: "20px", marginTop: "5px" }}>
                            {Object.entries(tickets).map(([ticket, count]) => (
                              <li key={ticket}>
                                <a href={`${JIRA_URL}${ticket}`} target="_blank" rel="noreferrer" style={{ color: "#007bff" }}>
                                  {ticket}
                                </a>: {count} test(s)
                              </li>
                            ))}
                          </ul>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          ) : (
            <div style={{ color: "#666", fontStyle: "italic" }}>
              Click "Save" in Advanced Action to load triage count data.
            </div>
          )}
        </div>
      )}

      {/* Triage Accuracy Analyzer Section */}
      {advancedOptions.triageAccuracy && (
        <div style={{ marginTop: "40px", padding: "20px", background: "#f8f9fa", borderRadius: "8px" }}>
          <h3 style={{ marginTop: 0, marginBottom: "15px", color: "#333" }}>Triage Accuracy Analyzer</h3>
          {loadingTriageAccuracy ? (
            <div style={{ color: "#666", fontStyle: "italic" }}>
              Loading triage accuracy... This may take several minutes as Triage Genie tickets are fetched for each testcase...
            </div>
          ) : triageAccuracyData ? (
            <div style={{ fontSize: "14px" }}>
              {triageAccuracyData.error ? (
                <div style={{ color: "#dc3545" }}>{triageAccuracyData.error}</div>
              ) : (
                <div>
                  {/* Triage Summary */}
                  {triageAccuracyData.triage_summary && (
                    <div style={{ marginBottom: "20px" }}>
                      <h4 style={{ marginBottom: "10px" }}>Triage Summary:</h4>
                      {/* Summary message above table */}
                      <p style={{ marginBottom: "12px", lineHeight: "1.6", color: "#333" }}>
                        Total failed/warning testcases: <strong>{triageAccuracyData?.triage_summary?.total_failed_warning_count ?? triageAccuracyData?.testcases?.length ?? 0}</strong>.
                        Triage Completed: <strong>{(triageAccuracyData?.triage_summary?.triage_completed_percent ?? 0)}%</strong> ({(triageAccuracyData?.triage_summary?.triaged_count ?? 0)} testcases).
                        Total Triage Genie Tagged: <strong>{(triageAccuracyData?.triage_summary?.total_triage_genie_percent ?? 0)}%</strong> ({(triageAccuracyData?.triage_summary?.total_triage_genie_count ?? 0)} testcases).
                      </p>
                      {/* Table with Metric | Count | Percentage */}
                      <table style={{ width: "100%", maxWidth: "450px", borderCollapse: "collapse", marginBottom: "15px" }}>
                        <thead>
                          <tr style={{ background: "#e9ecef" }}>
                            <th style={{ padding: "8px", border: "1px solid #ddd", textAlign: "left" }}>Metric</th>
                            <th style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>Count</th>
                            <th style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>Percentage</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr>
                            <td style={{ padding: "8px", border: "1px solid #ddd" }}>Triage Genie Ticket %(based on completed triaged)</td>
                            <td style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>{triageAccuracyData?.triage_summary?.triage_genie_count ?? 0}</td>
                            <td style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>{triageAccuracyData?.triage_summary?.triage_genie_percent ?? 0}%</td>
                          </tr>
                          <tr>
                            <td style={{ padding: "8px", border: "1px solid #ddd" }}>Matched %</td>
                            <td style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>{triageAccuracyData?.triage_summary?.matched_count ?? 0}</td>
                            <td style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>{triageAccuracyData?.triage_summary?.matched_percent ?? 0}%</td>
                          </tr>
                          <tr>
                            <td style={{ padding: "8px", border: "1px solid #ddd" }}>Unmatched %</td>
                            <td style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>{triageAccuracyData?.triage_summary?.unmatched_count ?? 0}</td>
                            <td style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>{triageAccuracyData?.triage_summary?.unmatched_percent ?? 0}%</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  )}
                  {/* Reload Data and Download Excel Report Buttons */}
                  <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
                    <button
                      onClick={handleReloadTriageAccuracy}
                      disabled={loadingTriageAccuracy}
                      style={{
                        padding: "8px 16px",
                        background: loadingTriageAccuracy ? "#ccc" : "#17a2b8",
                        color: "white",
                        border: "none",
                        borderRadius: "4px",
                        cursor: loadingTriageAccuracy ? "not-allowed" : "pointer",
                        fontSize: "14px",
                        fontWeight: "500"
                      }}
                    >
                      {loadingTriageAccuracy ? "Reloading..." : "Reload Data"}
                    </button>
                    <button
                      onClick={handleDownloadTriageAccuracyExcel}
                      style={{
                        padding: "8px 16px",
                        background: "#28a745",
                        color: "white",
                        border: "none",
                        borderRadius: "4px",
                        cursor: "pointer",
                        fontSize: "14px",
                        fontWeight: "500"
                      }}
                    >
                      Download Excel Report
                    </button>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div style={{ color: "#666", fontStyle: "italic" }}>
              Enable this option and click "Save" in Advanced Action to load triage accuracy data.
            </div>
          )}
        </div>
      )}

      {/* QI Impacted Bulk issue Section - Separate from Triage Count */}
      {advancedOptions.qiImpactedBulkIssue && triageCount && (
        <div style={{ marginTop: "40px", padding: "20px", background: "#f8f9fa", borderRadius: "8px" }}>
          <h3 style={{ marginTop: 0, marginBottom: "15px", color: "#333" }}>QI Impacted Bulk issue</h3>
          {loadingBulkQi ? (
            <div style={{ color: "#666", fontStyle: "italic" }}>
              Loading QI Impacted Bulk issue data... This may take a few minutes as the backend calculates QI impact for each testcase...
            </div>
          ) : triageCount.error ? (
            <div style={{ color: "#dc3545" }}>{triageCount.error}</div>
          ) : triageCount.bulk_issues && Object.keys(triageCount.bulk_issues).length > 0 ? (
            <div style={{ fontSize: "14px" }}>
              {/* Display Bulk Issues Table */}
              <div style={{ marginBottom: "20px" }}>
                <h4 style={{ marginBottom: "10px" }}>Bulk Issues (tickets with &gt;5 testcases):</h4>
                <div style={{ overflowX: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", marginTop: "10px" }}>
                    <thead>
                      <tr style={{ backgroundColor: "#f8f9fa" }}>
                        <th style={{ padding: "8px", border: "1px solid #ddd", textAlign: "left" }}>Bulk Issue Jita Ticket</th>
                        <th style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>Testcase Impacted</th>
                        <th style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>QI Impact due to this bug</th>
                      </tr>
                    </thead>
                    <tbody>
                      {triageCount.bulk_issues_with_qi ? (
                        // Use QI impact data if available
                        Object.entries(triageCount.bulk_issues_with_qi)
                          .sort((a, b) => a[1].overall_qi_impact - b[1].overall_qi_impact) // Sort by QI impact (most negative first)
                          .map(([ticket, data]) => (
                            <tr key={ticket}>
                              <td style={{ padding: "8px", border: "1px solid #ddd" }}>
                                <a
                                  href={`${JIRA_URL}${ticket}`}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  style={{ color: "#0066cc", textDecoration: "none" }}
                                >
                                  {ticket}
                                </a>
                              </td>
                              <td style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>
                                {data.testcase_count}
                              </td>
                              <td style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>
                                {data.overall_qi_impact.toFixed(2)}%
                              </td>
                            </tr>
                          ))
                      ) : (
                        // Fallback to simple list format if QI data not available
                        Object.entries(triageCount.bulk_issues).map(([ticket, tests]) => (
                          <tr key={ticket}>
                            <td style={{ padding: "8px", border: "1px solid #ddd" }}>
                              <a
                                href={`${JIRA_URL}${ticket}`}
                                target="_blank"
                                rel="noopener noreferrer"
                                style={{ color: "#0066cc", textDecoration: "none" }}
                              >
                                {ticket}
                              </a>
                            </td>
                            <td style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>
                              {tests.length}
                            </td>
                            <td style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>
                              N/A
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Display Bulk Issues QI Impacting Testcases - Detailed Table */}
              {advancedOptions.bulkIssuesQiImpact && 
               triageCount.bulk_issues_with_qi && 
               Object.keys(triageCount.bulk_issues_with_qi).length > 0 && (
                <div style={{ marginBottom: "20px", marginTop: "30px" }}>
                  <h4 style={{ marginBottom: "10px" }}>Bulk Issues QI Impacting Testcases:</h4>
                  <div style={{ overflowX: "auto" }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", marginTop: "10px" }}>
                      <thead>
                        <tr style={{ backgroundColor: "#f8f9fa" }}>
                          <th style={{ padding: "8px", border: "1px solid #ddd", textAlign: "left" }}>Bulk Issue Jita Ticket</th>
                          <th style={{ padding: "8px", border: "1px solid #ddd", textAlign: "left" }}>Testcase Impacted</th>
                          <th style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>QI Value</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(triageCount.bulk_issues_with_qi)
                          .sort((a, b) => a[1].overall_qi_impact - b[1].overall_qi_impact)
                          .map(([ticket, data]) => {
                            // Use testcase_qi_details if available, otherwise fallback to testcases array
                            const testcaseDetails = data.testcase_qi_details || 
                              (data.testcases ? data.testcases.map(tc => ({ testcase: tc, qi: 0 })) : []);
                            
                            return testcaseDetails.map((detail, index) => (
                              <tr key={`${ticket}-${index}`}>
                                {index === 0 && (
                                  <td 
                                    rowSpan={testcaseDetails.length}
                                    style={{ padding: "8px", border: "1px solid #ddd", verticalAlign: "top" }}
                                  >
                                    <a
                                      href={`${JIRA_URL}${ticket}`}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      style={{ color: "#0066cc", textDecoration: "none" }}
                                    >
                                      {ticket}
                                    </a>
                                    <div style={{ fontSize: "12px", color: "#666", marginTop: "4px" }}>
                                      Avg QI: {data.average_qi}% | Impact: {data.overall_qi_impact.toFixed(2)}%
                                    </div>
                                  </td>
                                )}
                                <td style={{ padding: "8px", border: "1px solid #ddd", fontFamily: "monospace", fontSize: "12px" }}>
                                  {detail.testcase}
                                </td>
                                <td style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>
                                  <span style={{ 
                                    color: detail.qi < 50 ? "#dc3545" : detail.qi < 100 ? "#ffc107" : "#28a745",
                                    fontWeight: "bold"
                                  }}>
                                    {detail.qi.toFixed(2)}%
                                  </span>
                                </td>
                              </tr>
                            ));
                          })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div style={{ color: "#666", fontStyle: "italic" }}>
              No bulk issues found. Bulk issues are tickets with more than 5 testcases.
            </div>
          )}
        </div>
      )}

      {/* QI Summary Report Section */}
      {advancedOptions.qiSummaryReport && (
        <div style={{ marginTop: "40px", padding: "20px", background: "#f8f9fa", borderRadius: "8px" }}>
          <h3 style={{ marginTop: 0, marginBottom: "15px", color: "#333" }}>QI Summary Report</h3>
          {loadingQiSummary ? (
            <div style={{ color: "#666", fontStyle: "italic" }}>
              Loading QI Summary Report... This may take a minute as the backend processes data...
            </div>
          ) : qiSummaryReport ? (
            <div style={{ fontSize: "14px" }}>
              {qiSummaryReport.error ? (
                <div style={{ color: "#dc3545" }}>{qiSummaryReport.error}</div>
              ) : (
                <div>
                  {/* Display Status Summary */}
                  {qiSummaryReport.status_summary && (
                    <div style={{ marginBottom: "20px" }}>
                      <h4 style={{ marginBottom: "10px" }}>Status Summary:</h4>
                      <table style={{ width: "100%", borderCollapse: "collapse", marginBottom: "15px" }}>
                        <tbody>
                          <tr>
                            <td style={{ padding: "8px", border: "1px solid #ddd", fontWeight: "bold" }}>Total Tasks:</td>
                            <td style={{ padding: "8px", border: "1px solid #ddd" }}>{qiSummaryReport.total_tasks}</td>
                          </tr>
                          <tr>
                            <td style={{ padding: "8px", border: "1px solid #ddd", fontWeight: "bold" }}>Testing:</td>
                            <td style={{ padding: "8px", border: "1px solid #ddd", color: "#6f42c1" }}>{qiSummaryReport.status_summary.testing}</td>
                          </tr>
                          <tr>
                            <td style={{ padding: "8px", border: "1px solid #ddd", fontWeight: "bold" }}>Completed:</td>
                            <td style={{ padding: "8px", border: "1px solid #ddd", color: "#28a745" }}>{qiSummaryReport.status_summary.completed}</td>
                          </tr>
                          <tr>
                            <td style={{ padding: "8px", border: "1px solid #ddd", fontWeight: "bold" }}>Pending:</td>
                            <td style={{ padding: "8px", border: "1px solid #ddd", color: "#17a2b8" }}>{qiSummaryReport.status_summary.pending}</td>
                          </tr>
                          <tr>
                            <td style={{ padding: "8px", border: "1px solid #ddd", fontWeight: "bold" }}>Failed:</td>
                            <td style={{ padding: "8px", border: "1px solid #ddd", color: "#dc3545" }}>{qiSummaryReport.status_summary.failed}</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  )}
                  
                  {/* Display Test Summary */}
                  {qiSummaryReport.test_summary && (
                    <div style={{ marginBottom: "20px" }}>
                      <h4 style={{ marginBottom: "10px" }}>Test Summary:</h4>
                      <table style={{ width: "100%", borderCollapse: "collapse", marginBottom: "15px" }}>
                        <tbody>
                          <tr>
                            <td style={{ padding: "8px", border: "1px solid #ddd", fontWeight: "bold" }}>Total:</td>
                            <td style={{ padding: "8px", border: "1px solid #ddd" }}>{qiSummaryReport.test_summary.total}</td>
                          </tr>
                          <tr>
                            <td style={{ padding: "8px", border: "1px solid #ddd", fontWeight: "bold" }}>Succeeded:</td>
                            <td style={{ padding: "8px", border: "1px solid #ddd", color: "#28a745" }}>{qiSummaryReport.test_summary.succeeded}</td>
                          </tr>
                          <tr>
                            <td style={{ padding: "8px", border: "1px solid #ddd", fontWeight: "bold" }}>Failed:</td>
                            <td style={{ padding: "8px", border: "1px solid #ddd", color: "#dc3545" }}>{qiSummaryReport.test_summary.failed}</td>
                          </tr>
                          <tr>
                            <td style={{ padding: "8px", border: "1px solid #ddd", fontWeight: "bold" }}>Pending:</td>
                            <td style={{ padding: "8px", border: "1px solid #ddd", color: "#17a2b8" }}>{qiSummaryReport.test_summary.pending}</td>
                          </tr>
                          <tr>
                            <td style={{ padding: "8px", border: "1px solid #ddd", fontWeight: "bold" }}>Warning:</td>
                            <td style={{ padding: "8px", border: "1px solid #ddd", color: "#fd7e14" }}>{qiSummaryReport.test_summary.warning}</td>
                          </tr>
                          <tr>
                            <td style={{ padding: "8px", border: "1px solid #ddd", fontWeight: "bold" }}>Running:</td>
                            <td style={{ padding: "8px", border: "1px solid #ddd", color: "#6f42c1" }}>{qiSummaryReport.test_summary.running}</td>
                          </tr>
                          <tr>
                            <td style={{ padding: "8px", border: "1px solid #ddd", fontWeight: "bold" }}>Skipped:</td>
                            <td style={{ padding: "8px", border: "1px solid #ddd", color: "#ffc107" }}>{qiSummaryReport.test_summary.skipped}</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  )}
                  
                  {/* Display Branch Summary */}
                  {qiSummaryReport.branch_summary && Object.keys(qiSummaryReport.branch_summary).length > 0 && (
                    <div style={{ marginTop: "20px" }}>
                      <h4 style={{ marginBottom: "10px" }}>Branch Summary:</h4>
                      <table style={{ width: "100%", borderCollapse: "collapse" }}>
                        <thead>
                          <tr style={{ background: "#e9ecef" }}>
                            <th style={{ padding: "8px", border: "1px solid #ddd", textAlign: "left" }}>Branch</th>
                            <th style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>Total Tasks</th>
                            <th style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>Total Tests</th>
                            <th style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>Failed Tests</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(qiSummaryReport.branch_summary).map(([branch, stats]) => (
                            <tr key={branch}>
                              <td style={{ padding: "8px", border: "1px solid #ddd" }}>{branch}</td>
                              <td style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>{stats.total_tasks}</td>
                              <td style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center" }}>{stats.total_tests}</td>
                              <td style={{ padding: "8px", border: "1px solid #ddd", textAlign: "center", color: "#dc3545" }}>{stats.failed_tests}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </div>
          ) : (
            <div style={{ color: "#666", fontStyle: "italic" }}>
              Click "Save" in Advanced Action to load QI Summary Report data.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------
   Helpers
--------------------------------*/

function aggregateByBranch(runs, branchStartDates = {}) {
  const map = {};

  runs.forEach((run) => {
    // Handle different branch name variations for master branch
    let branch = run.branch;
    if (!branch || branch === "" || branch === null || branch === undefined) {
      branch = "unknown";
    } else {
      // Normalize branch name - handle case variations
      branch = branch.trim();
      // Check if it's a master branch variant
      if (branch.toLowerCase() === "master" || branch.toLowerCase() === "main") {
        branch = "master";
      }
    }

    if (!map[branch]) {
      map[branch] = {
        branch,
        succeeded: 0,
        failed: 0,
        skipped: 0,
        pending: 0,
        warning: 0,
        running: 0,
        statuses: new Set(),
        actualTasks: [],
        mergedTasks: [],
        startDate: branchStartDates[branch] || null  // Add start date from backend
      };
    }

    // Aggregate all test counts from backend
    const counts = run.test_counts || {};
    map[branch].succeeded += counts.Succeeded || counts.succeeded || 0;
    map[branch].failed += counts.Failed || counts.failed || 0;
    map[branch].skipped += counts.Skipped || counts.skipped || 0;
    map[branch].pending += counts.Pending || counts.pending || 0;
    map[branch].warning += counts.Warning || counts.warning || 0;
    map[branch].running += counts.Running || counts.running || 0;
    
    map[branch].statuses.add(run.status);

    map[branch].actualTasks.push(run.task_id);
    map[branch].mergedTasks.push(run.task_id);
  });

  return Object.values(map).map((b) => ({
    branch: b.branch,
    succeeded: b.succeeded,
    failed: b.failed,
    skipped: b.skipped,
    pending: b.pending,
    warning: b.warning,
    running: b.running,
    status: deriveStatus([...b.statuses]),
    actualTasks: b.actualTasks,
    mergedTasks: b.mergedTasks,
    startDate: b.startDate
  }));
}

function deriveStatus(statuses) {
  if (statuses.includes("testing")) return "Running";
  if (statuses.includes("pending")) return "Pending";
  return "Completed";
}

function renderTaskButton(taskIds, buttonName) {
  if (!taskIds || taskIds.length === 0) return "-";
  
  const taskIdsString = taskIds.join(",");
  const url = `${JITA_RESULTS_URL}${taskIdsString}&active_tab=1&merge_tests=true`;
  
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="task-btn"
      style={{
        display: "inline-block",
        padding: "6px 12px",
        background: "#007bff",
        color: "white",
        textDecoration: "none",
        borderRadius: "4px",
        fontSize: "13px"
      }}
    >
      {buttonName}
    </a>
  );
}

