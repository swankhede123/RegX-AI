import os
import logging
import functools
from datetime import datetime, timedelta, timezone

import jwt
import ssl
from ldap3 import Server, Connection, ALL, SUBTREE, Tls
from flask import request, jsonify, g

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Secret key — stable in production, random fallback in dev
# ---------------------------------------------------------------------------
_secret_key = os.environ.get("SECRET_KEY") or os.environ.get("REGX_SECRET_KEY")
if _secret_key:
    SECRET_KEY = _secret_key
else:
    _debug = os.environ.get("FLASK_DEBUG", "").lower()
    _env = os.environ.get("FLASK_ENV", "").lower()
    _is_dev = _debug in ("1", "true", "yes", "on") or _env in ("development", "dev")
    if not _is_dev:
        # In production without a key, generate a random one but warn loudly.
        # This means tokens won't survive a restart — set SECRET_KEY for prod.
        logger.warning(
            "SECRET_KEY not set. Generating a random key — sessions will not "
            "persist across restarts. Set SECRET_KEY env var for production."
        )
    SECRET_KEY = os.urandom(32)

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = int(os.environ.get("JWT_EXPIRY_HOURS", "24"))


# ---------------------------------------------------------------------------
# LDAP Authentication
# ---------------------------------------------------------------------------
class LDAPAuth:
    AD_SERVER = "ldaps://ldap.dyn.nutanix.com"
    AD_USERNAME = os.environ.get("JITA_USERNAME", "")
    AD_PASSWORD = os.environ.get("JITA_PASSWORD", "")
    BASE_DN = "DC=corp,DC=nutanix,DC=com"
    BIND_SUFFIX = "@corp.nutanix.com"

    def __init__(self):
        tls_ctx = Tls(validate=ssl.CERT_NONE)
        self._server = Server(self.AD_SERVER, use_ssl=True, tls=tls_ctx, get_info=ALL)

    def authenticate(self, username, password):
        """
        Authenticate a user against Active Directory.

        1. Attempt a direct bind with the user's own credentials.
        2. On success, look up user attributes using the same credentials.

        Returns dict with user info on success, None on failure.
        """
        if not username or not password:
            return None

        # Strip domain suffix if provided
        if "@" in username:
            username = username.split("@")[0]

        user_dn = f"{username}{self.BIND_SUFFIX}"
        try:
            with Connection(self._server, user_dn, password, auto_bind=True) as conn:
                logger.info(f"LDAP bind succeeded for {username}")
                return self._get_user_info(conn, username)
        except Exception as e:
            logger.warning(f"LDAP bind failed for {username}: {e}")
            return None

    def _get_user_info(self, conn, username):
        """Fetch displayName, mail, sAMAccountName from an already-bound connection."""
        query = f"(sAMAccountName={username})"
        attributes = ["sAMAccountName", "displayName", "mail", "title"]
        try:
            conn.search(self.BASE_DN, query, search_scope=SUBTREE, attributes=attributes)
            if not conn.entries:
                return {"username": username, "displayName": username, "email": ""}
            entry = conn.entries[0]
            return {
                "username": str(entry.sAMAccountName),
                "displayName": str(entry.displayName) if entry.displayName else username,
                "email": str(entry.mail) if entry.mail else "",
            }
        except Exception as e:
            logger.error(f"LDAP attribute lookup failed for {username}: {e}")
            return {"username": username, "displayName": username, "email": ""}

    def get_by_username(self, username):
        """Look up a user by sAMAccountName using the service account."""
        bind_user = self.AD_USERNAME
        bind_pass = self.AD_PASSWORD
        if not bind_user or not bind_pass:
            logger.warning("JITA_USERNAME/JITA_PASSWORD not set; cannot look up user")
            return None
        try:
            with Connection(self._server, bind_user, bind_pass, auto_bind=True) as conn:
                return self._get_user_info(conn, username)
        except Exception as e:
            logger.error(f"LDAP lookup failed for {username}: {e}")
            return None


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------
def create_jwt(username, display_name="", email=""):
    payload = {
        "sub": username,
        "name": display_name,
        "email": email,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_jwt(token):
    """Decode and verify a JWT. Returns the payload dict or None."""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        logger.info("JWT expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid JWT: {e}")
        return None


# ---------------------------------------------------------------------------
# Flask decorator
# ---------------------------------------------------------------------------
def jwt_required(fn):
    """Decorator that enforces a valid JWT on the request.

    Sets ``g.current_user`` with the decoded payload on success.
    Returns 401 JSON on missing / invalid / expired token.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid Authorization header"}), 401

        token = auth_header[7:]
        payload = decode_jwt(token)
        if payload is None:
            return jsonify({"error": "Invalid or expired token"}), 401

        g.current_user = payload
        return fn(*args, **kwargs)

    return wrapper
