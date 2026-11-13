"""Flask application for ProxyPermit web management and cluster synchronization."""

from flask import Flask, render_template, request, jsonify, url_for, make_response, session, has_request_context
import json
import base64
import os
import sys
import re
import subprocess
import logging
import importlib.util
from io import StringIO
import hmac
import hashlib
import ipaddress
import stat
import socket
from app.core.listeners import tcp_listener
from app.core.proxy_health import proxy_responsive
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple, List
from functools import wraps
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, build_opener
from dotenv import load_dotenv
import threading
import requests
from werkzeug.security import check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

PYRAD_AVAILABLE = importlib.util.find_spec('pyrad') is not None

from ..core.filters import WhitelistManager
from ..core.accounts import account_role, permits, account_for, consume_totp, load_accounts, matching_totp_step, verify_password
from ..core.audit import SharedAuditHandler, bounded_setting
from ..core.files import file_lock
from ..core.tinyproxy import atomic_write
from ..core.validators import WhitelistValidator
from .proxy import domain_editor_error, PENDING_CHANGES, ha_enabled, has_pending_changes, managed_ha, paired_standby, record_published_policy, register_proxy_api, validate_filter
from .settings import register_settings
from .permissions import required_permission
from ..i18n import register_i18n


# Load the environment file if present.
load_dotenv()


# Configure INFO logging without personal data.

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configurable via env vars
AUDIT_LOG_PATH = os.getenv('AUDIT_LOG_PATH', 'audit.log')
AUDIT_NODE = socket.gethostname()
if os.getenv('PROXYPERMIT_HA_NODE') in ('a', 'b'):
    AUDIT_NODE += '/' + os.environ['PROXYPERMIT_HA_NODE'].upper()
try:
    AUDIT_LOG_MAX_BYTES = int(os.getenv('AUDIT_LOG_MAX_BYTES', str(2 * 1024 * 1024)))
except Exception:
    AUDIT_LOG_MAX_BYTES = 2 * 1024 * 1024
try:
    AUDIT_LOG_BACKUP_COUNT = int(os.getenv('AUDIT_LOG_BACKUP_COUNT', '5'))
except Exception:
    AUDIT_LOG_BACKUP_COUNT = 5

audit_logger = logging.getLogger('audit')
if not audit_logger.handlers:
    # Ensure directory exists for audit log
    try:
        audit_dir = os.path.dirname(AUDIT_LOG_PATH)
        if audit_dir:
            os.makedirs(audit_dir, exist_ok=True)
            if sys.platform != 'win32':
                try:
                    os.chmod(audit_dir, 0o750)
                except Exception:
                    pass
    except Exception as exc:
        logger.warning(f"Could not prepare audit log directory: {exc}")

    try:
        audit_handler = SharedAuditHandler(AUDIT_LOG_PATH, maxBytes=bounded_setting('AUDIT_LOG_MAX_BYTES', 2097152, 65536, 104857600),
                                           backupCount=bounded_setting('AUDIT_LOG_BACKUP_COUNT', 5, 1, 50), encoding='utf-8', delay=True)
        # We write JSON payloads as the raw message; keep file lines as JSON only
        audit_handler.setFormatter(logging.Formatter('%(message)s'))
        audit_logger.addHandler(audit_handler)
        audit_logger.setLevel(logging.INFO)
        # Prevent audit logger from propagating to root (avoid duplicate journald/syslog entries)
        audit_logger.propagate = False
    except Exception as exc:
        logger.warning(f"Could not configure audit logger at {AUDIT_LOG_PATH}: {exc}")


# Minimize logging of personal data.


# Initialiseer Flask app
app = Flask(__name__, template_folder='../templates', static_folder='../static', static_url_path='/static')
try:
    app.config['MAX_CONTENT_LENGTH'] = max(
        1024,
        int(os.getenv('MAX_REQUEST_SIZE', str(2 * 1024 * 1024))),
    )
except ValueError:
    app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024

# Optional reverse proxy header handling (e.g. Nginx -> Gunicorn)
PROXY_FIX_ENABLED = os.getenv('PROXY_FIX_ENABLED', 'false').lower() == 'true'
try:
    PROXY_FIX_X_FOR = max(0, int(os.getenv('PROXY_FIX_X_FOR', '1')))
except ValueError:
    PROXY_FIX_X_FOR = 1
try:
    PROXY_FIX_X_PROTO = max(0, int(os.getenv('PROXY_FIX_X_PROTO', '1')))
except ValueError:
    PROXY_FIX_X_PROTO = 1
try:
    PROXY_FIX_X_HOST = max(0, int(os.getenv('PROXY_FIX_X_HOST', '1')))
except ValueError:
    PROXY_FIX_X_HOST = 1
try:
    PROXY_FIX_X_PORT = max(0, int(os.getenv('PROXY_FIX_X_PORT', '1')))
except ValueError:
    PROXY_FIX_X_PORT = 1

if PROXY_FIX_ENABLED:
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=PROXY_FIX_X_FOR,
        x_proto=PROXY_FIX_X_PROTO,
        x_host=PROXY_FIX_X_HOST,
        x_port=PROXY_FIX_X_PORT
    )
    logger.info(
        "ProxyFix enabled (x_for=%s, x_proto=%s, x_host=%s, x_port=%s)",
        PROXY_FIX_X_FOR,
        PROXY_FIX_X_PROTO,
        PROXY_FIX_X_HOST,
        PROXY_FIX_X_PORT,
    )


# Require a configured secret key.
if not os.getenv('FLASK_SECRET_KEY'):
    raise RuntimeError('FLASK_SECRET_KEY environment variable must be set')


app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY')
app.config['JSON_AS_ASCII'] = True  # ASCII-only JSON
app.config['SESSION_COOKIE_HTTPONLY'] = True
ha_role = os.getenv('PROXYPERMIT_HA_NODE')
app.config['SESSION_COOKIE_NAME'] = os.getenv('SESSION_COOKIE_NAME') or (
    f'proxypermit-ha-{ha_role}'
    if os.getenv('PROXYPERMIT_MANAGED_HA') == 'true' and ha_role in ('a', 'b')
    else 'session'
)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.getenv('SESSION_COOKIE_SECURE', '').lower() == 'true' or os.getenv('MANAGER_SCHEME', 'http') == 'https'
app.permanent_session_lifetime = timedelta(hours=8)

# HSTS configuratie (RFC 6797)
ENABLE_HSTS = os.getenv('ENABLE_HSTS', 'true').lower() == 'true'
HSTS_MAX_AGE = int(os.getenv('HSTS_MAX_AGE', '31536000'))
HSTS_INCLUDE_SUBDOMAINS = os.getenv('HSTS_INCLUDE_SUBDOMAINS', 'false').lower() == 'true'
HSTS_PRELOAD = os.getenv('HSTS_PRELOAD', 'false').lower() == 'true'


def _canonicalize_whitelist_content(content: str) -> str:
    """Normalize whitelist content for semantic sync comparison."""
    normalized_entries = set()
    for raw_line in (content or '').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        line = re.split(r'\s+#', line, maxsplit=1)[0].strip()
        if not line:
            continue
        normalized_entries.add(line)
    return '\n'.join(sorted(normalized_entries))


def _compute_sync_hash_from_text(content: str) -> str:
    """Compute a stable hash for effective whitelist entries only."""
    canonical = _canonicalize_whitelist_content(content)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _preferred_sync_hash(info: Dict[str, Any]) -> str:
    """Get sync-comparison hash from remote sync info payload."""
    if not isinstance(info, dict):
        return ''
    return info.get('sync_hash') or info.get('hash') or ''

###########################################################
# ===== CORS SUPPORT =====
# Allow cross-origin requests from known nodes.
###########################################################
@app.after_request
def add_cors_headers(response):
    """Add CORS and security headers, granting cross-origin access to allowed peers."""
    if request.path.startswith('/api/') or request.path == '/health':
        response.headers['Cache-Control'] = 'no-store'
    origin = request.headers.get('Origin')
    allowed_origin = get_allowed_origin(origin)
    if allowed_origin:
        response.headers['Access-Control-Allow-Origin'] = allowed_origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.vary.add('Origin')

    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS, PATCH'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Auth-Token'
    response.headers['Access-Control-Max-Age'] = '3600'

    # Security headers, including HSTS over HTTPS.
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('X-XSS-Protection', '1; mode=block')
    response.headers.setdefault('Referrer-Policy', 'no-referrer')

    if ENABLE_HSTS and request.is_secure:
        hsts_value = f'max-age={HSTS_MAX_AGE}'
        if HSTS_INCLUDE_SUBDOMAINS:
            hsts_value += '; includeSubDomains'
        if HSTS_PRELOAD:
            hsts_value += '; preload'
        response.headers['Strict-Transport-Security'] = hsts_value

    logger.debug(f'CORS headers added to {request.method} {request.path}')
    return response

@app.route('/api/<path:path>', methods=['OPTIONS'])
def handle_preflight(path):
    """Handle CORS preflight requests."""
    logger.debug(f'CORS preflight request for /api/{path}')
    return '', 204

# Initialiseer managers (production defaults)
DEFAULT_WHITELIST = '/etc/tinyproxy/whitelist.txt'
DEFAULT_SERVICE = 'tinyproxy'

WHITELIST_PATH = os.getenv('WHITELIST_PATH', DEFAULT_WHITELIST)
TINYPROXY_SERVICE = os.getenv('TINYPROXY_SERVICE', DEFAULT_SERVICE)
TINYPROXY_LOGFILE = os.getenv('TINYPROXY_LOGFILE', '/var/log/tinyproxy/tinyproxy.log')

whitelist_manager = WhitelistManager(WHITELIST_PATH, TINYPROXY_SERVICE)
validator = WhitelistValidator()


def restart_tinyproxy_async():
    """Restart tinyproxy in a background thread to avoid blocking requests."""
    if DEMO_MODE:
        return
    def _run():
        filter_path = whitelist_manager.whitelist_path
        try:
            with file_lock(str(filter_path) + '.lock'):
                if has_pending_changes(filter_path):
                    logger.info(PENDING_CHANGES)
                    return
                success, error = whitelist_manager.restart_tinyproxy()
                if not success:
                    logger.error("Tinyproxy async restart failed: %s", error)
                else:
                    record_published_policy(filter_path, logger)
        except OSError:
            logger.exception('Tinyproxy async restart could not acquire the filter lock')

    threading.Thread(target=_run, daemon=True).start()


def get_app_config(public_only: bool = False):
    """Build application config.

    If `public_only` is True, return a minimal, non-sensitive config suitable for
    anonymous users. Otherwise return the full config including internal
    addresses/URLs.
    """
    vip_address = os.getenv('VIP_ADDRESS', '')
    node_addresses = [
        os.getenv('NODE0_ADDRESS', ''),
        os.getenv('NODE1_ADDRESS', '')
    ]
    node_addresses = [address for address in node_addresses if address]

    manager_port_raw = os.getenv('MANAGER_PORT', '8080')
    manager_port = int(manager_port_raw) if manager_port_raw.isdigit() else 8080
    manager_scheme = os.getenv('MANAGER_SCHEME', 'http')

    def build_url(address):
        if not address:
            return ''
        return f"{manager_scheme}://{address}:{manager_port}"

    # Read optional peer hostnames from the environment.
    try:
        peer_hostnames = json.loads(os.getenv('PEER_HOSTNAMES', '{}'))
    except Exception as e:
        logger.warning(f'Could not parse PEER_HOSTNAMES: {e}')
        peer_hostnames = {}

    # Optional logo config (can be a full URL or a path served by a reverse proxy)
    logo_url = os.getenv('MANAGER_LOGO_URL', '').strip()
    logo_alt = os.getenv('MANAGER_LOGO_ALT', '').strip()

    # Fallback to local static logo if not configured via env var.
    # Prefer common raster formats (png/webp/jpg) over svg when present.
    if not logo_url:
        try:
            static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static')
            candidates = ['logo.png', 'logo.webp', 'logo.jpg', 'logo.jpeg']
            for fname in candidates:
                path = os.path.join(static_dir, fname)
                if os.path.exists(path):
                    logo_url = f'/static/{fname}'
                    break
        except Exception:
            pass

    full = {
        'deploymentMode': 'ha' if ha_enabled() else 'standalone',
        'managedHa': managed_ha(),
        'canEditRules': permits(current_role(), 'proxy') and not paired_standby(),
        'vipAddress': vip_address,
        'nodeAddresses': node_addresses,
        'managerPort': manager_port,
        'managerScheme': manager_scheme,
        'vipUrl': '' if managed_ha() else build_url(vip_address),
        'nodeUrls': [] if managed_ha() else [build_url(address) for address in node_addresses],
        'authEnabled': ENABLE_AUTH,
        'authMode': 'session',
        'peerHostnames': peer_hostnames,
        'demoMode': DEMO_MODE,
        'role': current_role(),
        'canManageProxy': permits(current_role(), 'proxy'),
        'logoUrl': logo_url,
        'logoAlt': logo_alt
    }

    if public_only:
        return {
            'deploymentMode': 'ha' if ha_enabled() else 'standalone',
            'managedHa': managed_ha(),
            'authEnabled': ENABLE_AUTH,
            'authMode': 'session' if ENABLE_AUTH else '',
            'demoMode': DEMO_MODE,
            'logoUrl': logo_url,
            'logoAlt': logo_alt
        }

    return full

# Authentication settings.
ENABLE_AUTH = os.getenv('ENABLE_AUTH', 'false').lower() == 'true'
AUTH_USERNAME = os.getenv('AUTH_USERNAME', '')
AUTH_ACCOUNTS_FILE = os.getenv('AUTH_ACCOUNTS_FILE', '')
AUTH_PASSWORD_HASH = os.getenv('AUTH_PASSWORD_HASH', '')
AUTH_PASSWORD = os.getenv('AUTH_PASSWORD', '')
DEMO_MODE = os.getenv('DEMO_MODE', 'false').lower() == 'true'

# Optional RADIUS authentication requires pyrad.
ENABLE_RADIUS = not AUTH_ACCOUNTS_FILE and os.getenv('ENABLE_RADIUS', 'false').lower() == 'true' and PYRAD_AVAILABLE
RADIUS_SERVER = os.getenv('RADIUS_SERVER', '')  # Bijv. 'auth.example.com'
RADIUS_PORT = int(os.getenv('RADIUS_PORT', '1812'))
RADIUS_SECRET = os.getenv('RADIUS_SECRET', '')  # Shared secret tussen RADIUS client/server
RADIUS_TIMEOUT = int(os.getenv('RADIUS_TIMEOUT', '5'))  # Timeout in seconden
RADIUS_ALLOW_LOCAL_FALLBACK = os.getenv('RADIUS_ALLOW_LOCAL_FALLBACK', 'true').lower() == 'true'  # Fall back to the local password if RADIUS is unavailable.

# Validate RADIUS settings when enabled.
if ENABLE_RADIUS and (not RADIUS_SERVER or not RADIUS_SECRET):
    raise RuntimeError('When ENABLE_RADIUS=true, set RADIUS_SERVER and RADIUS_SECRET environment variables')

# Sync authentication settings.
SYNC_SECRET_FILE = os.getenv('SYNC_SECRET_FILE') or os.getenv('SYNC_SECRET_PATH') or '/etc/proxypermit/sync-secret.key'
MAX_TIMESTAMP_DIFF = 60  # Allow at most one minute of timestamp skew.
NONCE_STORE_FILE = os.getenv('NONCE_STORE_FILE', '/var/lib/proxypermit/sync-nonces.json')
MAX_NONCE_AGE = 120  # Nonces ouder dan 2 minuten worden verwijderd
SESSION_STATE_FILE = os.getenv('SESSION_STATE_FILE', '/var/lib/proxypermit/session-state.json')
SYNC_STATE_FILE = os.getenv('SYNC_STATE_FILE', '/var/lib/proxypermit/sync-state.json')
MANUAL_SYNC_STATE_FILE = os.getenv('MANUAL_SYNC_STATE_FILE', '/var/lib/proxypermit/manual-sync.json')

# Rate limiting for sync endpoints.
RATE_LIMIT_STORE = {}  # {ip: [(timestamp, endpoint), ...]}
RATE_LIMIT_MAX_REQUESTS = 20  # Max requests per minuut per IP
RATE_LIMIT_WINDOW = 60  # Window in seconden

# Login rate limiting / lockout (per client IP)
AUTH_LOGIN_RATE_LIMIT_ENABLED = os.getenv('AUTH_LOGIN_RATE_LIMIT_ENABLED', 'true').lower() == 'true'
try:
    AUTH_LOGIN_MAX_ATTEMPTS = max(1, int(os.getenv('AUTH_LOGIN_MAX_ATTEMPTS', '5')))
except ValueError:
    AUTH_LOGIN_MAX_ATTEMPTS = 5
try:
    AUTH_LOGIN_WINDOW_SECONDS = max(10, int(os.getenv('AUTH_LOGIN_WINDOW_SECONDS', '300')))
except ValueError:
    AUTH_LOGIN_WINDOW_SECONDS = 300
try:
    AUTH_LOGIN_LOCKOUT_SECONDS = max(10, int(os.getenv('AUTH_LOGIN_LOCKOUT_SECONDS', '120')))
except ValueError:
    AUTH_LOGIN_LOCKOUT_SECONDS = 120
AUTH_LOGIN_DYNAMIC_LOCKOUT_ENABLED = os.getenv('AUTH_LOGIN_DYNAMIC_LOCKOUT_ENABLED', 'false').lower() == 'true'
try:
    AUTH_LOGIN_MAX_LOCKOUT_SECONDS = max(10, int(os.getenv('AUTH_LOGIN_MAX_LOCKOUT_SECONDS', '900')))
except ValueError:
    AUTH_LOGIN_MAX_LOCKOUT_SECONDS = 900
try:
    AUTH_LOGIN_STRIKE_RESET_SECONDS = max(60, int(os.getenv('AUTH_LOGIN_STRIKE_RESET_SECONDS', '3600')))
except ValueError:
    AUTH_LOGIN_STRIKE_RESET_SECONDS = 3600
@contextmanager
def login_attempts(now):
    path = Path(SESSION_STATE_FILE).with_name('login-attempts.json')
    with file_lock(str(path) + '.lock'):
        try:
            attempts = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            attempts = {}
        cutoff = now - max(AUTH_LOGIN_WINDOW_SECONDS, AUTH_LOGIN_STRIKE_RESET_SECONDS)
        attempts = {ip: state for ip, state in attempts.items()
                    if max(state['failures'] + [state['lock_until'], state['last_lock']]) >= cutoff}
        yield attempts
        atomic_write(path, json.dumps(attempts))


# Log/output privacy tuning
LOG_IP_MODE = os.getenv('LOG_IP_MODE', 'masked').lower().strip()
if LOG_IP_MODE not in ('full', 'masked', 'hash'):
    LOG_IP_MODE = 'masked'

CLIENT_IP_MAP_PATH = os.getenv('CLIENT_IP_MAP_PATH', '').strip()
CLIENT_IP_MAP_JSON = os.getenv('CLIENT_IP_MAP_JSON', '').strip()

# Audit entry payload control
AUDIT_ENTRY_MODE = os.getenv('AUDIT_ENTRY_MODE', 'hash').lower().strip()
if AUDIT_ENTRY_MODE not in ('preview', 'full', 'hash'):
    AUDIT_ENTRY_MODE = 'hash'
try:
    AUDIT_ENTRY_MAX_LEN = max(40, int(os.getenv('AUDIT_ENTRY_MAX_LEN', '160')))
except ValueError:
    AUDIT_ENTRY_MAX_LEN = 160

# Validate authentication settings when enabled.
if ENABLE_AUTH and not AUTH_ACCOUNTS_FILE and (not AUTH_USERNAME or (not AUTH_PASSWORD_HASH and not AUTH_PASSWORD)):
    raise RuntimeError('When ENABLE_AUTH=true, set AUTH_USERNAME and either AUTH_PASSWORD_HASH (recommended) or AUTH_PASSWORD (legacy)')

if ENABLE_AUTH and AUTH_ACCOUNTS_FILE and not load_accounts(AUTH_ACCOUNTS_FILE):
    raise RuntimeError('AUTH_ACCOUNTS_FILE must contain at least one administrator.')

if ENABLE_AUTH and not AUTH_ACCOUNTS_FILE and AUTH_PASSWORD and not AUTH_PASSWORD_HASH:
    logger.warning('Using legacy AUTH_PASSWORD plaintext auth. Configure AUTH_PASSWORD_HASH for production-hardening.')


def _client_ip() -> str:
    """Use the connection IP, after the explicitly configured ProxyFix."""
    return (request.remote_addr or 'unknown').strip()


def _format_ip_for_logs(ip_value: str) -> str:
    """Format IP values for log output based on LOG_IP_MODE."""
    ip_value = (ip_value or '').strip()
    if not ip_value:
        return ''
    mode = LOG_IP_MODE
    if mode == 'masked':
        try:
            address = ipaddress.ip_address(ip_value)
            return str(ipaddress.ip_network(f'{address}/{24 if address.version == 4 else 64}', strict=False))
        except ValueError:
            return '-'
    if mode == 'hash':
        return hmac.new(app.config['SECRET_KEY'].encode(), ip_value.encode(), hashlib.sha256).hexdigest()[:12]
    return ip_value


def _load_client_ip_map() -> Dict[str, str]:
    """Load optional client IP -> system name mapping (JSON dict)."""
    mapping: Dict[str, str] = {}

    if CLIENT_IP_MAP_JSON:
        try:
            raw = json.loads(CLIENT_IP_MAP_JSON)
            if isinstance(raw, dict):
                for key, value in raw.items():
                    if not key or not isinstance(value, str):
                        continue
                    mapping[str(key).strip()] = value.strip()[:80]
        except Exception:
            pass

    if CLIENT_IP_MAP_PATH:
        try:
            if os.path.exists(CLIENT_IP_MAP_PATH):
                with open(CLIENT_IP_MAP_PATH, 'r', encoding='utf-8') as handle:
                    raw = json.load(handle)
                if isinstance(raw, dict):
                    for key, value in raw.items():
                        if not key or not isinstance(value, str):
                            continue
                        mapping[str(key).strip()] = value.strip()[:80]
        except Exception:
            pass

    return mapping


def _audit_entry_value(value: Optional[str]) -> str:
    """Format whitelist entry payloads for audit logging."""
    if value is None:
        return ''
    raw = str(value)
    if AUDIT_ENTRY_MODE == 'hash':
        return hmac.new(app.config['SECRET_KEY'].encode(), raw.encode('utf-8'), hashlib.sha256).hexdigest()
    if AUDIT_ENTRY_MODE == 'full':
        return raw
    if len(raw) > AUDIT_ENTRY_MAX_LEN:
        return raw[:AUDIT_ENTRY_MAX_LEN] + '...'
    return raw


def _safe_audit_value(value: Any, max_len: int = 200) -> str:
    if value is None:
        return ''
    from .logs import scrub_message
    text = scrub_message(str(value)).replace('\r', '\\r').replace('\n', '\\n')
    if len(text) > max_len:
        return text[:max_len] + '...'
    return text


def _audit_event(event: str, level: str = 'info', **details) -> None:
    """Write structured audit logs for configuration changes."""
    from_request = has_request_context()
    safe_details = {
        'event': event,
        'node': AUDIT_NODE,
        'ip': _format_ip_for_logs(_client_ip()) if from_request else '-',
        'path': request.path if from_request else '-',
        'method': request.method if from_request else '-',
        'role': current_role() if from_request else 'system',
        'username': (session.get('user') or '-') if from_request else 'proxypermit-system'
    }
    for key, value in (details or {}).items():
        if key not in safe_details and not re.search(r'password|passwd|secret|token|cookie|authorization|otp', key, re.I):
            if key == 'description' and AUDIT_ENTRY_MODE != 'full':
                continue
            safe_details[key] = _safe_audit_value(value)

    payload = ', '.join(f"{k}={safe_details[k]}" for k in sorted(safe_details.keys()))
    log_line = f"AUDIT {payload}"

    audit_entry = {
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'id': os.urandom(16).hex(),
        'level': (level or 'info').upper(),
        'message': log_line
    }
    for k, v in safe_details.items():
        if k in ('timestamp', 'level', 'message'):
            continue
        audit_entry[k] = v

    try:
        json_payload = json.dumps(audit_entry, ensure_ascii=False, default=str)
    except Exception:
        json_payload = json.dumps({'message': log_line}, ensure_ascii=False)

    if level == 'warning':
        try:
            audit_logger.warning(json_payload)
        except Exception:
            logger.debug('Failed to write to audit.log')
    elif level == 'error':
        try:
            audit_logger.error(json_payload)
        except Exception:
            logger.debug('Failed to write to audit.log')
    else:
        try:
            audit_logger.info(json_payload)
        except Exception:
            logger.debug('Failed to write to audit.log')


def _auth_audit(event: str, level: str = 'info', **details) -> None:
    """Write structured auth audit logs without sensitive values."""
    safe_details = {
        'event': event,
        'node': AUDIT_NODE,
        'ip': _format_ip_for_logs(_client_ip()),
        'path': request.path,
        'method': request.method
    }
    safe_details.update({key: _safe_audit_value(value) for key, value in details.items()
                         if key not in safe_details and not re.search(r'password|passwd|secret|token|cookie|authorization|otp', key, re.I)})

    # Human-readable message (kept for journalctl / UI compatibility)
    payload = ', '.join(f"{k}={safe_details[k]}" for k in sorted(safe_details.keys()))
    log_line = f"AUTH_AUDIT {payload}"

    # Structured audit entry for file logging (JSON LINES)
    audit_entry = {
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'id': os.urandom(16).hex(),
        'level': (level or 'info').upper(),
        'message': log_line
    }
    # Include structured fields (already sanitized)
    for k, v in safe_details.items():
        # Avoid overwriting top-level keys
        if k in ('timestamp', 'level', 'message'):
            continue
        audit_entry[k] = v

    try:
        json_payload = json.dumps(audit_entry, ensure_ascii=False, default=str)
    except Exception:
        json_payload = json.dumps({'message': log_line}, ensure_ascii=False)

    # Log to main logger (visible in journalctl) and to dedicated audit file
    if level == 'warning':
        try:
            audit_logger.warning(json_payload)
        except Exception:
            logger.debug('Failed to write to audit.log')
    elif level == 'error':
        try:
            audit_logger.error(json_payload)
        except Exception:
            logger.debug('Failed to write to audit.log')
    else:
        try:
            audit_logger.info(json_payload)
        except Exception:
            logger.debug('Failed to write to audit.log')


def _get_auth_login_state(ip: str, now_ts: Optional[int] = None) -> Dict[str, Any]:
    """Return current login failure state for an IP (pruned to active window)."""
    if now_ts is None:
        now_ts = int(time.time())

    with login_attempts(now_ts) as attempts:
        state = attempts.get(ip, {'failures': [], 'lock_until': 0, 'strikes': 0, 'last_lock': 0})
        failures = [ts for ts in state.get('failures', []) if ts >= now_ts - AUTH_LOGIN_WINDOW_SECONDS]
        lock_until = int(state.get('lock_until', 0) or 0)
        strikes = int(state.get('strikes', 0) or 0)
        last_lock = int(state.get('last_lock', 0) or 0)

        if last_lock and (now_ts - last_lock) > AUTH_LOGIN_STRIKE_RESET_SECONDS:
            strikes = 0

        if lock_until <= now_ts:
            lock_until = 0

        state = {'failures': failures, 'lock_until': lock_until, 'strikes': strikes, 'last_lock': last_lock}
        attempts[ip] = state

        return {
            'failures': len(failures),
            'remaining': max(0, AUTH_LOGIN_MAX_ATTEMPTS - len(failures)),
            'locked': lock_until > now_ts,
            'retry_after': max(0, lock_until - now_ts) if lock_until > now_ts else 0,
            'strikes': strikes
        }


def _register_auth_login_failure(ip: str, now_ts: Optional[int] = None) -> Dict[str, Any]:
    """Register a failed login attempt and return updated lockout state."""
    if now_ts is None:
        now_ts = int(time.time())

    with login_attempts(now_ts) as attempts:
        state = attempts.get(ip, {'failures': [], 'lock_until': 0, 'strikes': 0, 'last_lock': 0})
        failures = [ts for ts in state.get('failures', []) if ts >= now_ts - AUTH_LOGIN_WINDOW_SECONDS]
        lock_until = int(state.get('lock_until', 0) or 0)
        strikes = int(state.get('strikes', 0) or 0)
        last_lock = int(state.get('last_lock', 0) or 0)

        if last_lock and (now_ts - last_lock) > AUTH_LOGIN_STRIKE_RESET_SECONDS:
            strikes = 0

        if lock_until > now_ts:
            attempts[ip] = {
                'failures': failures,
                'lock_until': lock_until,
                'strikes': strikes,
                'last_lock': last_lock
            }
            return {
                'failures': len(failures),
                'remaining': 0,
                'locked': True,
                'retry_after': max(0, lock_until - now_ts),
                'strikes': strikes
            }

        failures.append(now_ts)
        locked = len(failures) >= AUTH_LOGIN_MAX_ATTEMPTS
        if locked:
            strikes += 1
            if AUTH_LOGIN_DYNAMIC_LOCKOUT_ENABLED:
                lockout_seconds = AUTH_LOGIN_LOCKOUT_SECONDS * (2 ** max(0, strikes - 1))
                lockout_seconds = min(lockout_seconds, AUTH_LOGIN_MAX_LOCKOUT_SECONDS)
            else:
                lockout_seconds = AUTH_LOGIN_LOCKOUT_SECONDS
            lock_until = now_ts + lockout_seconds
            # Reset failure window while lockout is active.
            failures = []
            last_lock = now_ts
        else:
            lock_until = 0
            lockout_seconds = 0

        attempts[ip] = {
            'failures': failures,
            'lock_until': lock_until,
            'strikes': strikes,
            'last_lock': last_lock
        }
        return {
            'failures': AUTH_LOGIN_MAX_ATTEMPTS if locked else len(failures),
            'remaining': 0 if locked else max(0, AUTH_LOGIN_MAX_ATTEMPTS - len(failures)),
            'locked': locked,
            'retry_after': lockout_seconds if locked else 0,
            'strikes': strikes
        }


def _clear_auth_login_failures(ip: str) -> None:
    """Clear login failure tracking for an IP after successful authentication."""
    with login_attempts(int(time.time())) as attempts:
        attempts.pop(ip, None)


def _check_auth_radius(username: str, password: str) -> Tuple[bool, str]:
    """Check credentials against RADIUS and return a diagnostic message."""
    if not ENABLE_RADIUS or not PYRAD_AVAILABLE:
        return False, 'RADIUS_DISABLED'
    
    if not RADIUS_SERVER or not RADIUS_SECRET:
        return False, 'RADIUS_NOT_CONFIGURED'
    
    try:
        pyrad_client = importlib.import_module('pyrad.client')
        pyrad_dictionary = importlib.import_module('pyrad.dictionary')
        pyrad_packet = importlib.import_module('pyrad.packet')

        # Send a PAP (Password Authentication Protocol) request.
        dictionary = pyrad_dictionary.Dictionary(StringIO(
            'ATTRIBUTE User-Name 1 string\n'
            'ATTRIBUTE User-Password 2 octets\n'
        ))
        client = pyrad_client.Client(
            server=RADIUS_SERVER,
            authport=RADIUS_PORT,
            secret=RADIUS_SECRET.encode('utf-8'),
            dict=dictionary,
            timeout=RADIUS_TIMEOUT
        )
        
        # Bouw Access-Request pakket
        req = client.CreateAuthPacket(code=pyrad_packet.AccessRequest)
        req['User-Name'] = username
        req['User-Password'] = req.PwCrypt(password)
        
        # Send the authentication request.
        reply = client.SendPacket(req)
        
        # Check response
        if reply.code == pyrad_packet.AccessAccept:
            logger.info(f'RADIUS accepted authentication for user: {username[:32]}...')
            return True, 'RADIUS_SUCCESS'
        else:
            logger.warning(f'RADIUS rejected authentication for user: {username[:32]}...')
            return False, 'RADIUS_REJECT'

    except Exception as e:
        # Keep diagnostics for RADIUS and network errors brief and safe.
        logger.warning(f"RADIUS communication error: {str(e)[:100]}")
        return False, 'RADIUS_ERROR'


def check_auth(username, password):
    """Check the supplied username and password."""
    if not hmac.compare_digest((username or '').encode('utf-8'), AUTH_USERNAME.encode('utf-8')):
        return False

    # Try RADIUS first when enabled.
    if ENABLE_RADIUS:
        radius_success, radius_status = _check_auth_radius(username, password)
        if radius_success:
            return True
        
        # Use local authentication when RADIUS is unavailable and fallback is enabled.
        if not RADIUS_ALLOW_LOCAL_FALLBACK and radius_status not in ['RADIUS_DISABLED', 'RADIUS_NOT_CONFIGURED']:
            # RADIUS answered and rejected the credentials.
            return False
        
        # Fall back only when RADIUS is unavailable.
        if radius_status in ['RADIUS_ERROR', 'RADIUS_EXCEPTION'] and RADIUS_ALLOW_LOCAL_FALLBACK:
            logger.info(f"RADIUS onbereikbaar, fallback op local auth")
            # Continue with local authentication below.
        elif radius_status not in ['RADIUS_ERROR', 'RADIUS_EXCEPTION']:
            # RADIUS gaf explicit reject
            return False

    # Check the local password or password hash.
    if AUTH_PASSWORD_HASH:
        try:
            return check_password_hash(AUTH_PASSWORD_HASH, password or '')
        except (ValueError, TypeError):
            logger.error('AUTH_PASSWORD_HASH has an invalid format')
            return False

    return bool(AUTH_PASSWORD) and hmac.compare_digest((password or '').encode('utf-8'), AUTH_PASSWORD.encode('utf-8'))


def authenticate():
    """Return HTTP 401 when a session is required."""
    return jsonify({'error': 'Authentication required'}), 401


def requires_auth(f):
    """Require an authenticated session for a route."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not ENABLE_AUTH:
            return f(*args, **kwargs)

        if not _is_authenticated_request():
            _auth_audit('auth_required_failed', level='warning', reason='invalid_session')
            return authenticate()
        if not permits(current_role(), required_permission()):
            _audit_event('permission_denied', level='warning', required=required_permission())
            return jsonify(error='Your role does not permit this action.'), 403
        return f(*args, **kwargs)
    decorated.requires_session = True
    return decorated


def enforce_browser_permissions():
    view = app.view_functions.get(request.endpoint)
    if not ENABLE_AUTH or request.method == 'OPTIONS' or not getattr(view, 'requires_session', False):
        return None
    if not _is_authenticated_request():
        return authenticate()
    if not permits(current_role(), required_permission()):
        _audit_event('permission_denied', level='warning', required=required_permission())
        return jsonify(error='Your role does not permit this action.'), 403
    return None


def _read_session_state() -> Dict[str, Any]:
    try:
        with open(SESSION_STATE_FILE, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except FileNotFoundError:
        return {'version': 1}


def _get_session_version() -> int:
    state = _read_session_state()
    version = state.get('version')
    if type(version) is not int or version < 1:
        raise ValueError('Invalid session version.')
    return version


def _bump_session_version() -> int:
    with file_lock(SESSION_STATE_FILE + '.lock'):
        next_version = _get_session_version() + 1
        atomic_write(SESSION_STATE_FILE, json.dumps({'version': next_version}))
        return next_version


def _sign_auth_token(username: str, expires_at: int, version: int) -> str:
    payload = {'u': username, 'exp': expires_at, 'v': version, 'jti': os.urandom(16).hex()}
    payload_json = json.dumps(payload, separators=(',', ':'), ensure_ascii=True).encode('utf-8')
    payload_b64 = base64.urlsafe_b64encode(payload_json).decode('ascii').rstrip('=')
    signature = hmac.new(
        app.config['SECRET_KEY'].encode('utf-8'),
        payload_b64.encode('ascii'),
        hashlib.sha256
    ).hexdigest()
    return f"{payload_b64}.{signature}"


def _verify_auth_token(token: str) -> Optional[Dict[str, Any]]:
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,1024}\.[0-9a-f]{64}', token):
        return None
    payload_b64, signature = token.rsplit('.', 1)
    expected_signature = hmac.new(
        app.config['SECRET_KEY'].encode('utf-8'),
        payload_b64.encode('ascii'),
        hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return None

    padding = '=' * (-len(payload_b64) % 4)
    try:
        payload_json = base64.urlsafe_b64decode(payload_b64 + padding).decode('utf-8')
        payload = json.loads(payload_json)
    except Exception:
        return None

    expires_at = payload.get('exp')
    username = payload.get('u')
    version = payload.get('v')
    if not isinstance(expires_at, int) or not isinstance(username, str) or not isinstance(version, int):
        return None
    if int(time.time()) > expires_at:
        return None
    return payload


def _is_authenticated_request() -> bool:
    if not ENABLE_AUTH:
        return True
    user = session.get('user')
    if not user or session.get('session_version') != _get_session_version():
        return False
    if not AUTH_ACCOUNTS_FILE:
        return user == AUTH_USERNAME
    account = account_for(AUTH_ACCOUNTS_FILE, user)
    return bool(account and session.get('account_revision') == account['revision'])


def current_role():
    if not ENABLE_AUTH or not AUTH_ACCOUNTS_FILE:
        return 'admin'
    return account_role(account_for(AUTH_ACCOUNTS_FILE, session.get('user')))


@app.context_processor
def permission_context():
    role = current_role() if _is_authenticated_request() else None
    return dict(current_role=role, permissions={name: permits(role, name) for name in ('read', 'proxy', 'admin', 'self')},
                role_labels={'admin': 'Systeembeheerder', 'manager': 'Proxybeheerder', 'monitor': 'Monitor'})


def get_allowed_origin(origin: Optional[str]) -> Optional[str]:
    if not origin or origin == 'null':
        return None

    manager_port_raw = os.getenv('MANAGER_PORT', '8080')
    manager_port = int(manager_port_raw) if manager_port_raw.isdigit() else 8080
    manager_scheme = os.getenv('MANAGER_SCHEME', 'http')
    allowed_hosts = [
        os.getenv('VIP_ADDRESS', ''),
        os.getenv('NODE0_ADDRESS', ''),
        os.getenv('NODE1_ADDRESS', '')
    ]
    allowed_hosts = [host for host in allowed_hosts if host]
    allowed_origins = {f"{manager_scheme}://{host}:{manager_port}" for host in allowed_hosts}

    # Allow explicitly configured extra origins (comma-separated)
    extra = os.getenv('MANAGER_ALLOWED_ORIGINS', '')
    if extra:
        for part in [p.strip() for p in extra.split(',') if p.strip()]:
            allowed_origins.add(part)

    return origin if origin in allowed_origins else None


def protect_browser_mutations():
    if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE') or not request.path.startswith('/api/'):
        return None
    origin = request.headers.get('Origin')
    if not origin and request.referrer:
        try:
            source = urlsplit(request.referrer)
            origin = f'{source.scheme}://{source.netloc}'
        except ValueError:
            return jsonify(error='The request origin is not allowed.'), 403
    if request.endpoint == 'auth_consume' and get_allowed_origin(origin):
        return None
    if (origin and origin != request.host_url.rstrip('/')) or (
        not origin and request.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site')
    ):
        return jsonify(error='The request origin is not allowed.'), 403
    return None


# --- Merge preview proxy endpoint ---
@app.route('/api/merge/remote-preview', methods=['POST'])
@requires_auth
def merge_remote_preview():
    """Fetch the filter from a configured peer over the authenticated sync API."""
    import secrets
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Invalid input.'}), 400

    remote_url = data.get('remote_url')
    if not isinstance(remote_url, str) or not remote_url:
        return jsonify({'error': 'remote_url parameter required'}), 400

    try:
        parsed_remote = urlsplit(remote_url)
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid peer URL.'}), 400
    allowed_hosts = set(_get_allowed_peer_ips())
    try:
        peer_hostnames = json.loads(os.getenv('PEER_HOSTNAMES', '{}'))
        if isinstance(peer_hostnames, dict):
            allowed_hosts.update(str(value).strip() for value in peer_hostnames.values() if value)
    except (TypeError, ValueError, json.JSONDecodeError):
        pass

    configured_scheme = (os.getenv('SYNC_SCHEME') or os.getenv('MANAGER_SCHEME', 'http')).lower()
    configured_port = _get_sync_port()
    try:
        remote_port = parsed_remote.port or (443 if parsed_remote.scheme == 'https' else 80)
    except ValueError:
        return jsonify({'error': 'Invalid peer URL.'}), 400
    if (
        parsed_remote.scheme != configured_scheme
        or not parsed_remote.hostname
        or parsed_remote.hostname not in allowed_hosts
        or remote_port != configured_port
        or parsed_remote.username
        or parsed_remote.password
        or parsed_remote.path not in ("", "/")
        or parsed_remote.query
        or parsed_remote.fragment
    ):
        logger.warning('Rejected remote preview for an unconfigured peer')
        return jsonify({'error': 'Only configured sync peers are allowed.'}), 403

    verify = os.getenv('SYNC_VERIFY_CERT', 'true').lower() == 'true'
    if not sync_transport_allowed(configured_scheme, verify):
        return jsonify(error='Synchronization requires HTTPS with certificate verification.'), 403

    # Always append /api/sync/download to the remote_url (strip trailing slash)
    remote_url = remote_url.rstrip('/') + '/api/sync/download'

    # HMAC authenticatieheaders genereren
    secret = get_sync_secret()
    if not secret:
        return jsonify({'error': 'The sync secret is missing.'}), 500

    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    signature = generate_hmac_signature(secret, 'GET', '/api/sync/download', timestamp, nonce)
    headers = {
        'X-Sync-Timestamp': timestamp,
        'X-Sync-Nonce': nonce,
        'X-Sync-Signature': signature
    }
    logger.info('Fetching remote preview from configured peer %s', parsed_remote.hostname)
    try:
        resp = requests.get(remote_url, headers=headers, timeout=5, verify=verify, allow_redirects=False)
        logger.info('Remote preview response received: status=%s', resp.status_code)
        # Expect plain text response with whitelist lines
        if resp.status_code == 200:
            content_type = resp.headers.get('Content-Type', '')
            if 'text/plain' in content_type or content_type.startswith('text/'):
                try:
                    # Limit preview to 1000 lines to avoid huge payloads
                    lines = [line for line in resp.text.splitlines() if line.strip() != '']
                    max_preview_lines = 1000
                    if len(lines) > max_preview_lines:
                        logger.warning(f"Remote whitelist too large for preview: {len(lines)} lines, truncating to {max_preview_lines}")
                        lines = lines[:max_preview_lines]
                    return jsonify({'success': True, 'whitelist': lines, 'truncated': len(lines) == max_preview_lines}), 200
                except Exception as parse_exc:
                    logger.error(f"Error parsing remote whitelist for preview: {parse_exc}")
                    return jsonify({'error': f'Could not process the remote filter: {parse_exc}'}), 502
            else:
                # Received HTML or other unexpected content
                return jsonify({'error': 'Remote did not return text/plain', 'body': resp.text[:500]}), 502
        else:
            return jsonify({'error': f'Remote returned {resp.status_code}', 'details': resp.text}), 502
    except Exception as e:
        logger.error(f'Could not fetch or process the remote filter: {e}')
        return jsonify({'error': str(e)}), 502


@app.route('/api/auth/me', methods=['GET'])
def auth_me():
    authenticated = _is_authenticated_request()
    response = jsonify(authenticated=authenticated, username=session.get('user') if authenticated else None, role=current_role() if authenticated else None)
    response.headers['Cache-Control'] = 'no-store'
    return response


def login_locked(state):
    response = jsonify(success=False, error='Too many failed sign-in attempts. Wait a moment and try again.',
                       retry_after=state['retry_after'])
    response.headers['Retry-After'] = str(state['retry_after'])
    return response, 429


def settings_reauth(actor, account, password, otp):
    key = 'settings:' + actor
    state = _get_auth_login_state(key)
    if state['locked']:
        return login_locked(state), None
    step = None
    try:
        valid = isinstance(password, str) and 0 < len(password) <= 256 and verify_password(account, password)
    except ValueError:
        valid = False
    if valid and 'totp_secret' in account:
        step = matching_totp_step(account['totp_secret'], otp)
        valid = consume_totp(Path(SESSION_STATE_FILE).with_name('totp-steps.json'), actor, account['revision'], step)
    if valid:
        _clear_auth_login_failures(key)
        return None, step
    state = _register_auth_login_failure(key)
    _auth_audit('account_reauth_failed', level='warning', username=actor)
    error = login_locked(state) if state['locked'] else (jsonify(error='The password or authenticator code is incorrect.'), 401)
    return error, None


@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    if not ENABLE_AUTH:
        return jsonify({'success': True}), 200

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('username'), str) or not isinstance(data.get('password'), str):
        return jsonify({'success': False, 'error': 'Invalid input.'}), 400
    username = data['username'].strip()
    password = data['password']
    otp = data.get('otp', '')
    if not isinstance(otp, str) or len(otp) > 6:
        return jsonify(success=False, error='Invalid input.'), 400
    try:
        username.encode('utf-8')
        password.encode('utf-8')
    except UnicodeEncodeError:
        return jsonify({'success': False, 'error': 'Invalid input.'}), 400
    client_ip = _client_ip()

    if AUTH_LOGIN_RATE_LIMIT_ENABLED:
        login_state = _get_auth_login_state(client_ip)
        if login_state['locked']:
            _auth_audit(
                'login_blocked',
                level='warning',
                reason='lockout_active',
                retry_after=login_state['retry_after'],
                username=username[:64] or '-'
            )
            return login_locked(login_state)

    if not username or not password:
        _auth_audit('login_rejected', level='warning', reason='missing_credentials', username=username or '-')
        return jsonify({'success': False, 'error': 'Enter a username and password'}), 400

    if len(username) > 64 or len(password) > 256:
        _auth_audit('login_rejected', level='warning', reason='invalid_input_length', username=username[:64] or '-')
        return jsonify({'success': False, 'error': 'Invalid input.'}), 400

    account = account_for(AUTH_ACCOUNTS_FILE, username) if AUTH_ACCOUNTS_FILE else None
    authenticated = verify_password(account, password) if AUTH_ACCOUNTS_FILE else check_auth(username, password)
    if authenticated and account and 'totp_secret' in account:
        if not otp:
            return jsonify(success=False, otpRequired=True, error='Enter the code from your authenticator app.'), 401
        limit_key = 'totp:' + username
        factor_state = _get_auth_login_state(limit_key)
        if factor_state['locked']:
            return login_locked(factor_state)
        step = matching_totp_step(account['totp_secret'], otp)
        authenticated = consume_totp(Path(SESSION_STATE_FILE).with_name('totp-steps.json'),
                                     username, account['revision'], step)
        if authenticated:
            _clear_auth_login_failures(limit_key)
        else:
            factor_state = _register_auth_login_failure(limit_key)
            if factor_state['locked']:
                _auth_audit('totp_blocked', level='warning', username=username)
                return login_locked(factor_state)
    if not authenticated:
        if AUTH_LOGIN_RATE_LIMIT_ENABLED:
            state = _register_auth_login_failure(client_ip)
            if state['locked']:
                _auth_audit(
                    'login_blocked',
                    level='warning',
                    reason='too_many_failures',
                    username=username[:64] or '-',
                    failures=state['failures'],
                    retry_after=state['retry_after']
                )
                return login_locked(state)

            _auth_audit(
                'login_failed',
                level='warning',
                reason='invalid_credentials',
                username=username[:64] or '-',
                failures=state['failures'],
                remaining=state['remaining']
            )
        else:
            _auth_audit('login_failed', level='warning', reason='invalid_credentials', username=username[:64] or '-')
        return jsonify({'success': False, 'error': 'Invalid credentials.'}), 401

    session.clear()
    session['user'] = username
    if account:
        session['account_revision'] = account['revision']
    session['session_version'] = _get_session_version()
    session.permanent = True
    if AUTH_LOGIN_RATE_LIMIT_ENABLED:
        _clear_auth_login_failures(client_ip)
    auth_method = 'local' if account or not ENABLE_RADIUS else 'RADIUS'
    if account and 'totp_secret' in account:
        auth_method = 'local+totp'
    _auth_audit('login_success', username=username, auth_method=auth_method)
    return jsonify({'success': True}), 200


@app.route('/api/auth/logout', methods=['POST'])
@requires_auth
def auth_logout():
    _auth_audit('logout', username=session.get('user') or '-')
    session.clear()
    return jsonify({'success': True}), 200


@app.route('/api/auth/transfer', methods=['POST'])
@requires_auth
def auth_transfer():
    if AUTH_ACCOUNTS_FILE:
        return jsonify(success=False, error='Sign in separately on the other node.'), 409
    expires_at = int(time.time()) + 60
    token = _sign_auth_token(AUTH_USERNAME, expires_at, _get_session_version())
    _auth_audit('transfer_token_issued', username=AUTH_USERNAME, expires_at=expires_at)
    return jsonify({'success': True, 'token': token, 'expires_at': expires_at}), 200


@app.route('/api/auth/consume', methods=['POST'])
def auth_consume():
    if not ENABLE_AUTH:
        return jsonify({'success': True}), 200

    if AUTH_ACCOUNTS_FILE:
        return jsonify(success=False, error='Sign in separately on the other node.'), 409
    data = request.get_json(silent=True)
    if data is not None and (not isinstance(data, dict) or not isinstance(data.get('token', ''), str)):
        return jsonify({'success': False, 'error': 'Invalid input.'}), 400
    data = data or {}
    header_token = (request.headers.get('X-Auth-Token') or '').strip()
    token = (data.get('token') or '').strip() or header_token

    if not token:
        _auth_audit('token_consume_failed', level='warning', reason='missing_token')
        return jsonify({'success': False, 'error': 'The token is missing.'}), 401

    payload = _verify_auth_token(token)
    current_version = _get_session_version()
    if (not payload or payload.get('u') != AUTH_USERNAME or payload.get('v') != current_version
            or not isinstance(payload.get('jti'), str)
            or not claim_nonce('transfer:' + payload['jti'])):
        _auth_audit('token_consume_failed', level='warning', reason='invalid_or_expired_token')
        return jsonify({'success': False, 'error': 'Invalid or expired token.'}), 401

    session.clear()
    session['user'] = AUTH_USERNAME
    session['session_version'] = current_version
    session.permanent = True
    _auth_audit('token_consume_success', username=AUTH_USERNAME)
    return jsonify({'success': True}), 200


@app.route('/api/auth/force-logout', methods=['POST'])
@requires_auth
def auth_force_logout():
    version = _bump_session_version()
    _auth_audit('force_logout', level='warning', username=session.get('user') or '-', new_version=version)
    session.clear()
    return jsonify({'success': True, 'version': version}), 200


@app.route('/api/config', methods=['GET'])
@requires_auth
def api_config():
    """Return the full application config for authenticated clients."""
    try:
        return jsonify({'success': True, 'config': get_app_config()}), 200
    except Exception as e:
        logger.error(f"Error returning config: {e}")
        return jsonify({'success': False, 'error': 'Could not load configuration.'}), 500


def get_sync_secret():
    """Read the shared secret for sync authentication."""
    try:
        if not os.path.exists(SYNC_SECRET_FILE):
            logger.warning(f'Sync secret file not found: {SYNC_SECRET_FILE}')
            return None
        
        with open(SYNC_SECRET_FILE, 'r') as f:
            secret = f.read().strip()
        
        if len(secret) < 32:
            logger.warning(f"Sync secret is too short (minimum 32 characters): {len(secret)}")
            return None
        
        return secret
    except Exception as e:
        logger.error(f'Could not read the sync secret: {e}')
        return None


def _check_sync_secret_file():
    """Warn when the sync secret is missing or its file permissions are too broad."""
    if not ha_enabled() or managed_ha():
        return
    try:
        if not os.path.exists(SYNC_SECRET_FILE):
            logger.warning(f'Sync secret file is missing: {SYNC_SECRET_FILE}')
            return
        if sys.platform == 'win32':
            return
        st = os.stat(SYNC_SECRET_FILE)
        perms = st.st_mode & 0o777
        if perms & 0o077:
            logger.warning(f"Sync secret file {SYNC_SECRET_FILE} has broad permissions: {oct(perms)}. Use 0o600.")
    except Exception as e:
        logger.warning(f'Could not check the sync secret file: {e}')


# Run the configuration checks on import.
_check_sync_secret_file()



def generate_hmac_signature(secret, method, path, timestamp, nonce, body=''):
    """Generate an HMAC-SHA256 signature for request authentication."""
    message = f"{method}|{path}|{timestamp}|{nonce}|{body}"
    signature = hmac.new(
        secret.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature


def verify_hmac_signature(secret, method, path, timestamp, nonce, signature, body=''):
    """Verify the HMAC signature, timestamp, and nonce."""
    # Reject timestamps outside the allowed replay window.
    try:
        req_time = int(timestamp)
        current_time = int(time.time())
        time_diff = abs(current_time - req_time)
        
        if time_diff > MAX_TIMESTAMP_DIFF:
            logger.warning(f"Timestamp outside the allowed window: {time_diff}s difference")
            return False
    except ValueError:
        logger.error(f'Invalid timestamp: {timestamp}')
        return False
    
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', nonce) or not re.fullmatch(r'[0-9a-f]{64}', signature):
        return False
    expected = generate_hmac_signature(secret, method, path, timestamp, nonce, body)
    return hmac.compare_digest(signature, expected) and claim_nonce(nonce)



def _get_allowed_peer_ips() -> list:
    """Load allowed peer IPs from the environment or Keepalived configuration."""
    # Prefer explicit environment settings.
    node0 = os.getenv('NODE0_ADDRESS', '')
    node1 = os.getenv('NODE1_ADDRESS', '')
    peers = [node0, node1]
    
    # Filter out empty values and loopback addresses.
    peers = [p for p in peers if p and p not in ('localhost', '127.0.0.1')]
    
    # Allow loopback for local testing.
    if sys.platform == 'win32' or os.getenv('ALLOW_LOCALHOST_SYNC') == 'true':
        peers.append('127.0.0.1')
    
    return peers


def claim_nonce(nonce: str) -> bool:
    """Record a signed nonce once across all web workers."""
    path = Path(NONCE_STORE_FILE)
    try:
        with file_lock(str(path) + '.lock'):
            try:
                entries = json.loads(path.read_text(encoding='utf-8'))
            except FileNotFoundError:
                entries = {}
            now = int(time.time())
            entries = {key: ts for key, ts in entries.items() if now - ts <= MAX_NONCE_AGE}
            if nonce in entries:
                return False
            entries[nonce] = now
            atomic_write(path, json.dumps(entries))
            return True
    except (OSError, ValueError, TypeError, AttributeError):
        logger.exception('Could not update replay protection')
        return False


def _check_rate_limit(ip: str, endpoint: str) -> bool:
    """Check of IP rate limit overschrijdt."""
    current_time = int(time.time())
    
    # Cleanup oude entries
    if ip in RATE_LIMIT_STORE:
        RATE_LIMIT_STORE[ip] = [
            (ts, ep) for ts, ep in RATE_LIMIT_STORE[ip]
            if current_time - ts < RATE_LIMIT_WINDOW
        ]
    
    # Tel requests in window
    if ip not in RATE_LIMIT_STORE:
        RATE_LIMIT_STORE[ip] = []
    
    request_count = len(RATE_LIMIT_STORE[ip])
    
    if request_count >= RATE_LIMIT_MAX_REQUESTS:
        logger.warning(f'Rate limit exceeded for {ip}: {request_count} requests')
        return False
    
    # Record the current request.
    RATE_LIMIT_STORE[ip].append((current_time, endpoint))
    return True


def insecure_sync_allowed():
    return os.getenv('SYNC_ALLOW_INSECURE_TRANSPORT', '').lower() == 'true'


def sync_transport_allowed(scheme, verify):
    return (scheme == 'https' and verify) or insecure_sync_allowed()


class RejectPeerRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_sync_peer(request_to_peer, timeout, context):
    return build_opener(RejectPeerRedirects(), HTTPSHandler(context=context)).open(request_to_peer, timeout=timeout)


def requires_sync_auth(f):
    """Require HMAC authentication for sync routes."""
    @wraps(f)
    def decorated(*args, **kwargs):
        remote_ip = _client_ip()
        if not request.is_secure and not insecure_sync_allowed():
            return jsonify({'error': 'Synchronization requires HTTPS.'}), 403
        if remote_ip not in _get_allowed_peer_ips():
            return jsonify({'error': 'IP address is not allowed.'}), 403
        # Load the shared secret.
        secret = get_sync_secret()
        if not secret:
            logger.error('Sync secret is not configured')
            return jsonify({'error': 'Sync authentication is not configured.'}), 500
        # Read authentication headers.
        timestamp = request.headers.get('X-Sync-Timestamp')
        nonce = request.headers.get('X-Sync-Nonce')
        signature = request.headers.get('X-Sync-Signature')
        if not timestamp or not nonce or not signature:
            logger.warning("Sync request is missing authentication headers")
            return jsonify({'error': 'Authentication required'}), 401
        # Read the body for POST requests.
        try:
            body = request.get_data().decode('utf-8') if request.method == 'POST' else ''
        except UnicodeDecodeError:
            return jsonify(error='Invalid filter content'), 400
        # Verifieer signature
        if not verify_hmac_signature(secret, request.method, request.path, timestamp, nonce, signature, body):
            logger.warning(f'Invalid sync authentication from {remote_ip}')
            return jsonify({'error': 'Invalid authentication.'}), 401
        if not _check_rate_limit(remote_ip, request.path):
            return jsonify({'error': 'Rate limit overschreden'}), 429
        return f(*args, **kwargs)
    return decorated


@app.route('/connection', methods=['GET'])
def connection_check():
    """Public liveness only: no session, configuration or service inspection."""
    return '', 204, {'Cache-Control': 'no-store', 'X-ProxyPermit-Reachable': '1'}


@app.route('/health', methods=['GET'])
def health_check():
    """Return health information for monitoring."""
    from datetime import datetime
    import sys
    import socket

    checks = {
        'keepalived_active': None,
        'keepalived_state': 'UNKNOWN',
        'vip_active': None,
        'tinyproxy_active': None,
        'tinyproxy_port_open': None,
        'tinyproxy_responsive': None
    }

    if sys.platform != 'win32':
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', 'keepalived'],
                capture_output=True,
                text=True,
                timeout=2
            )
            checks['keepalived_active'] = result.returncode == 0 and result.stdout.strip() == 'active'
        except Exception:
            checks['keepalived_active'] = None

        try:
            result = subprocess.run(
                ['journalctl', '-u', 'keepalived', '-n', '20', '--no-pager'],
                capture_output=True,
                text=True,
                timeout=2
            )
            for line in reversed(result.stdout.split('\n')):
                if 'Entering MASTER STATE' in line or 'Entered MASTER STATE' in line:
                    checks['keepalived_state'] = 'MASTER'
                    break
                if 'Entering BACKUP STATE' in line or 'Entered BACKUP STATE' in line:
                    checks['keepalived_state'] = 'BACKUP'
                    break
                if 'Entering FAULT STATE' in line or 'Entered FAULT STATE' in line:
                    checks['keepalived_state'] = 'FAULT'
                    break
        except Exception:
            checks['keepalived_state'] = 'UNKNOWN'

        try:
            result = subprocess.run(
                ['ip', 'addr', 'show'],
                capture_output=True,
                text=True,
                timeout=2
            )
            vip_address = os.getenv('VIP_ADDRESS', '')
            checks['vip_active'] = bool(vip_address and vip_address in result.stdout)
        except Exception:
            checks['vip_active'] = None

        try:
            result = subprocess.run(
                ['systemctl', 'is-active', TINYPROXY_SERVICE],
                capture_output=True,
                text=True,
                timeout=2
            )
            checks['tinyproxy_active'] = result.returncode == 0 and result.stdout.strip() == 'active'
        except Exception:
            checks['tinyproxy_active'] = None

        tinyproxy_port_raw = os.getenv('TINYPROXY_PORT', '')
        if tinyproxy_port_raw.isdigit():
            tinyproxy_port = int(tinyproxy_port_raw)
            try:
                checks['tinyproxy_port_open'] = tcp_listener(tinyproxy_port)
                checks['tinyproxy_responsive'] = proxy_responsive(os.getenv('TINYPROXY_CONFIG_PATH', '/etc/tinyproxy/tinyproxy.conf'))
            except Exception:
                checks['tinyproxy_port_open'] = False

    degraded = False
    for key, value in checks.items():
        if value is False:
            degraded = True
            break

    return jsonify({
        'status': 'degraded' if degraded else 'healthy',
        'service': 'proxypermit',
        'timestamp': datetime.now().isoformat(),
        'checks': checks
    }), 200


@app.route('/api/sync/info', methods=['GET'])
@requires_sync_auth
def sync_info():
    """Return filter file metadata to an authenticated sync peer."""
    from datetime import datetime
    
    try:
        if not os.path.exists(WHITELIST_PATH):
            return jsonify({
                'exists': False
            }), 200
        
        stat = os.stat(WHITELIST_PATH)
        
        # Bereken raw + semantische sync hash
        with open(WHITELIST_PATH, 'rb') as f:
            content = f.read()
            raw_hash = hashlib.sha256(content).hexdigest()
            sync_hash = _compute_sync_hash_from_text(content.decode('utf-8', errors='replace'))
        
        return jsonify({
            'exists': True,
            'mtime': stat.st_mtime,
            'size': stat.st_size,
            'hash': raw_hash,
            'raw_hash': raw_hash,
            'sync_hash': sync_hash,
            'timestamp': datetime.fromtimestamp(stat.st_mtime).isoformat()
        }), 200
        
    except Exception as e:
        logger.error(f"Sync info error: {e}")
        return jsonify({
            'exists': False,
            'error': str(e)
        }), 500


@app.route('/api/sync/download', methods=['GET'])
@requires_sync_auth
def sync_download():
    """Download the filter file for an authenticated sync peer."""
    try:
        if not os.path.exists(WHITELIST_PATH):
            return jsonify({'error': 'Whitelist not found'}), 404
        
        with open(WHITELIST_PATH, 'r') as f:
            content = f.read()
        
        response = make_response(content)
        response.headers['Content-Type'] = 'text/plain'
        response.headers['Content-Disposition'] = 'attachment; filename=whitelist.txt'
        return response
        
    except Exception as e:
        logger.error(f"Sync download error: {e}")
        return jsonify({'error': str(e)}), 500


def apply_synced_filter(content: str) -> None:
    """Publish a legacy filter and restore the previous file if activation fails."""
    checks = validate_filter(content, 'ere')
    if not checks['valid']:
        raise ValueError('Invalid filter content')
    path = Path(WHITELIST_PATH)
    with file_lock(str(path) + '.lock'):
        if has_pending_changes(path):
            raise ValueError('Configuration changes are pending. Activate them through Proxy configuration first.')
        previous = path.read_bytes().decode('utf-8') if path.exists() else None
        mode = stat.S_IMODE(path.stat().st_mode) if previous is not None else 0o644
        atomic_write(path, content, mode)
        try:
            subprocess.run(['systemctl', 'reload', TINYPROXY_SERVICE], check=True,
                           capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            try:
                activated, _ = whitelist_manager.restart_tinyproxy()
                if activated:
                    return
            except Exception:
                logger.exception('Tinyproxy restart failed after sync')
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, previous, mode)
            restored, _ = whitelist_manager.restart_tinyproxy()
            if not restored:
                logger.error('Tinyproxy could not restart after restoring the filter')
            raise RuntimeError('Could not activate the synchronized rules.')


@app.route('/api/sync/upload', methods=['POST'])
@requires_sync_auth
def sync_upload():
    content = request.get_data(as_text=True)
    if not content or '\x00' in content:
        return jsonify({'error': 'Invalid filter content'}), 400
    try:
        apply_synced_filter(content)
        _audit_event('legacy_filter_synced', peer=_client_ip(), revision=hashlib.sha256(content.encode('utf-8')).hexdigest())
        return jsonify(success=True, timestamp=time.time(), hash=hashlib.sha256(content.encode('utf-8')).hexdigest())
    except ValueError as exc:
        return jsonify(error=str(exc)), 409
    except (OSError, RuntimeError):
        logger.exception('Sync upload failed')
        return jsonify(error='Filter rules could not be applied.'), 503


@app.route('/api/sync/status', methods=['GET'])
def sync_status():
    """Return sync status for the console."""
    try:
        # Return status to the console without requiring authentication.
        # Include the authentication flag so the console can react.
        authenticated = _is_authenticated_request()
        if not authenticated:
            logger.info(f"Unauthenticated /api/sync/status request from {_client_ip()}; returning sanitized state")
        state_file = SYNC_STATE_FILE
        
        if os.path.exists(state_file):
            with open(state_file, 'r') as f:
                import json as json_lib
                state = json_lib.load(f)
                # Sanitize sensitive fields for unauthenticated callers
                if not authenticated:
                    state = _sanitize_sync_state(state)
                return jsonify({
                    'success': True,
                    'state': state,
                    'authenticated': authenticated
                }), 200
        else:
            return jsonify({
                'success': True,
                'state': None,
                'message': 'No sync state yet',
                'authenticated': authenticated
            }), 200
    except Exception as e:
        logger.error(f"Sync status error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


def _sanitize_sync_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """Return a sanitized copy of the sync state for unauthenticated callers.

    Removes potentially sensitive fields such as peer IPs and hashes.
    """
    if not isinstance(state, dict):
        return {}
    # Return conflict metadata without file hashes.
    sanitized = {}
    if 'conflict' in state:
        sanitized['conflict'] = bool(state.get('conflict'))
    if 'peer' in state:
        sanitized['peer'] = state.get('peer')
    if 'direction' in state:
        sanitized['direction'] = state.get('direction')
    # Report the conflict timestamp or the last sync time.
    if 'timestamp' in state:
        sanitized['timestamp'] = state.get('timestamp')
    elif 'last_sync' in state:
        sanitized['timestamp'] = state.get('last_sync')
    return sanitized


@app.route('/api/sync/peer-status', methods=['GET'])
@requires_auth
def sync_peer_status():
    """Check the sync status of configured peers from the server."""
    try:
        import os as os_module
        
        # Get peer address from config
        node0_address = os_module.getenv('NODE0_ADDRESS', '').strip()
        node1_address = os_module.getenv('NODE1_ADDRESS', '').strip()
        
        # Determine which node is the peer
        def _collect_local_identifiers() -> set:
            identifiers = set()
            try:
                import socket
                identifiers.add(socket.gethostname())
                identifiers.add(socket.getfqdn())
            except Exception:
                pass
            try:
                result = subprocess.run(['hostname'], capture_output=True, text=True, timeout=2)
                if result.returncode == 0:
                    identifiers.add(result.stdout.strip())
            except Exception:
                pass
            try:
                result = subprocess.run(
                    ['ip', '-o', '-4', 'addr', 'show'],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                if result.returncode == 0:
                    for item in result.stdout.split():
                        identifiers.add(item.strip())
            except Exception:
                pass
            try:
                result = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=2)
                if result.returncode == 0:
                    for item in result.stdout.split():
                        identifiers.add(item.strip())
            except Exception:
                pass
            identifiers.update({'127.0.0.1', 'localhost'})
            return {item for item in identifiers if item}
        
        local_ids = _collect_local_identifiers()
        peer_address = None
        
        # Determine peer address (same logic as /api/status)
        if node0_address and node1_address:
            local_is_node0 = node0_address in local_ids
            local_is_node1 = node1_address in local_ids
            if local_is_node0 and not local_is_node1:
                peer_address = node1_address
            elif local_is_node1 and not local_is_node0:
                peer_address = node0_address
        elif node0_address or node1_address:
            peer_address = node0_address or node1_address
        
        if not peer_address:
            return jsonify({
                'success': False,
                'error': 'Peer address not configured',
                'peer_status': None
            }), 400
        
        # Check peer sync status via HTTP (backend does the call)
        scheme = os_module.getenv('MANAGER_SCHEME', 'http')
        manager_port_raw = os_module.getenv('MANAGER_PORT', '8080')
        manager_port = int(manager_port_raw) if manager_port_raw.isdigit() else 8080
        verify_cert = os_module.getenv('SYNC_VERIFY_CERT', 'true').lower() == 'true'
        
        peer_sync_url = f"{scheme}://{peer_address}:{manager_port}/api/sync/status"
        
        try:
            peer_response = requests.get(peer_sync_url, timeout=3, verify=verify_cert)
            if peer_response.status_code == 200:
                peer_data = peer_response.json()
                return jsonify({
                    'success': True,
                    'peer_address': peer_address,
                    'peer_status': peer_data.get('state'),
                    'peer_online': True
                }), 200
            else:
                return jsonify({
                    'success': True,
                    'peer_address': peer_address,
                    'peer_status': None,
                    'peer_online': False,
                    'error': f'HTTP {peer_response.status_code}'
                }), 200
        except requests.RequestException as e:
            logger.debug(f"Could not reach peer {peer_address}: {e}")
            return jsonify({
                'success': True,
                'peer_address': peer_address,
                'peer_status': None,
                'peer_online': False,
                'error': str(e)
            }), 200
    
    except Exception as e:
        logger.error(f"Error getting peer status: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/sync/set-conflict', methods=['POST'])
@requires_sync_auth
def sync_set_conflict():
    """Markeer conflict state op deze node (HMAC protected)."""
    try:
        data = request.get_json(silent=True) or {}
        state = {
            'conflict': True,
            'timestamp': datetime.now().isoformat(),
            'peer': _client_ip(),
            'local_mtime': data.get('local_mtime'),
            'remote_mtime': data.get('remote_mtime'),
            'local_hash': data.get('local_hash'),
            'remote_hash': data.get('remote_hash'),
            'local_timestamp': data.get('local_timestamp'),
            'remote_timestamp': data.get('remote_timestamp')
        }
        _write_sync_state(state)
        return jsonify({'success': True}), 200
    except Exception as exc:
        logger.error(f"Sync set conflict error: {exc}")
        return jsonify({'success': False, 'error': str(exc)}), 500


def _get_sync_port() -> int:
    raw = os.getenv('SYNC_PORT') or os.getenv('MANAGER_PORT', '8080')
    return int(raw) if raw.isdigit() else 8080


def _read_sync_state() -> Dict[str, Any]:
    state_file = SYNC_STATE_FILE
    if not os.path.exists(state_file):
        return {}
    try:
        with open(state_file, 'r') as f:
            import json as json_lib
            return json_lib.load(f)
    except Exception as exc:
        logger.warning(f'Could not read sync state: {exc}')
        return {}


def _write_sync_state(state: Dict[str, Any]) -> None:
    with file_lock(SYNC_STATE_FILE + '.lock'):
        atomic_write(SYNC_STATE_FILE, json.dumps(state, indent=2))


def _get_sync_script_path() -> Optional[str]:
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    script_path = os.path.join(base_dir, 'legacy', 'sync_whitelist_v2.py')
    if os.path.exists(script_path):
        return script_path
    return None


MANUAL_SYNC_LOCK = threading.Lock()


def _read_manual_sync_state() -> Dict[str, Any]:
    if not os.path.exists(MANUAL_SYNC_STATE_FILE):
        return {}
    try:
        with open(MANUAL_SYNC_STATE_FILE, 'r') as f:
            import json as json_lib
            return json_lib.load(f)
    except Exception as exc:
        logger.warning(f'Could not read manual sync state: {exc}')
        return {}


def _write_manual_sync_state(state: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(MANUAL_SYNC_STATE_FILE), exist_ok=True)
        with open(MANUAL_SYNC_STATE_FILE, 'w') as f:
            import json as json_lib
            json_lib.dump(state, f, indent=2)
    except Exception as exc:
        logger.warning(f'Could not save manual sync state: {exc}')


def _manual_sync_worker(started_at: str) -> None:
    success, message = _run_manual_sync()
    _write_manual_sync_state({
        'running': False,
        'started_at': started_at,
        'finished_at': datetime.now().isoformat(),
        'last_success': success,
        'last_message': message
    })


def _start_manual_sync() -> Tuple[bool, str, int]:
    script_path = _get_sync_script_path()
    if not script_path:
        return False, 'Sync script not found.', 500

    with MANUAL_SYNC_LOCK:
        state = _read_manual_sync_state()
        if state.get('running'):
            return False, 'Sync draait al', 409

        started_at = datetime.now().isoformat()
        _write_manual_sync_state({
            'running': True,
            'started_at': started_at,
            'last_success': None,
            'last_message': None
        })

        thread = threading.Thread(
            target=_manual_sync_worker,
            args=(started_at,),
            daemon=True
        )
        thread.start()

    return True, 'Sync started', 202


def _run_manual_sync() -> Tuple[bool, str]:
    script_path = _get_sync_script_path()
    if not script_path:
        return False, 'Sync script not found.'

    try:
        result = subprocess.run(
            [sys.executable, script_path],
            cwd=os.path.dirname(script_path),
            capture_output=True,
            text=True,
            timeout=120,
            check=False
        )
    except Exception as exc:
        return False, f'Sync failed: {exc}'

    combined = "\n".join([result.stdout.strip(), result.stderr.strip()]).strip()
    if result.returncode != 0:
        return False, combined or 'Sync script faalde'

    return True, combined or 'Synchronisatie afgerond'


def _fetch_remote_whitelist(peer_ip: str, port: int) -> Tuple[Optional[str], Optional[str]]:
    try:
        import urllib.request
        if peer_ip not in _get_allowed_peer_ips():
            return None, 'IP address is not allowed.'

        secret = get_sync_secret()
        if not secret:
            return None, 'The sync secret is missing.'

        timestamp = str(int(time.time()))
        path = '/api/sync/download'
        nonce = hashlib.sha256(os.urandom(32)).hexdigest()
        signature = generate_hmac_signature(secret, 'GET', path, timestamp, nonce)

        scheme = (os.getenv('SYNC_SCHEME') or os.getenv('MANAGER_SCHEME', 'http')).lower()
        url = f"{scheme}://{peer_ip}:{port}{path}"
        req = urllib.request.Request(url, method='GET')
        req.add_header('X-Sync-Timestamp', timestamp)
        req.add_header('X-Sync-Nonce', nonce)
        req.add_header('X-Sync-Signature', signature)

        import ssl
        verify_cert = os.getenv('SYNC_VERIFY_CERT', 'true').lower() == 'true'
        if not sync_transport_allowed(scheme, verify_cert):
            raise ValueError('Synchronization requires HTTPS with certificate verification.')
        context = None
        if scheme == 'https' and not verify_cert:
            context = ssl._create_unverified_context()
        with open_sync_peer(req, 6, context) as response:
            if response.status == 200:
                return response.read().decode('utf-8'), None
        return None, f"Onverwachte HTTP status: {response.status}"
    except Exception as exc:
        return None, str(exc)


def _push_remote_whitelist(peer_ip: str, port: int, content: str) -> Tuple[bool, Optional[str]]:
    try:
        import urllib.request
        if peer_ip not in _get_allowed_peer_ips():
            return False, 'IP address is not allowed.'

        secret = get_sync_secret()
        if not secret:
            return False, 'The sync secret is missing.'

        timestamp = str(int(time.time()))
        path = '/api/sync/upload'
        nonce = hashlib.sha256(os.urandom(32)).hexdigest()
        signature = generate_hmac_signature(secret, 'POST', path, timestamp, nonce, content)

        scheme = (os.getenv('SYNC_SCHEME') or os.getenv('MANAGER_SCHEME', 'http')).lower()
        url = f"{scheme}://{peer_ip}:{port}{path}"
        data = content.encode('utf-8')
        req = urllib.request.Request(url, data=data, method='POST')
        req.add_header('Content-Type', 'text/plain')
        req.add_header('X-Sync-Timestamp', timestamp)
        req.add_header('X-Sync-Nonce', nonce)
        req.add_header('X-Sync-Signature', signature)
        logger.info('Push remote whitelist to configured peer %s', peer_ip)
        import ssl
        verify_cert = os.getenv('SYNC_VERIFY_CERT', 'true').lower() == 'true'
        if not sync_transport_allowed(scheme, verify_cert):
            raise ValueError('Synchronization requires HTTPS with certificate verification.')
        context = None
        if scheme == 'https' and not verify_cert:
            context = ssl._create_unverified_context()
        try:
            with open_sync_peer(req, 8, context) as response:
                logger.info(f"Peer upload response: status={response.status}")
                if response.status == 200:
                    return True, None
                return False, f"Onverwachte HTTP status: {response.status}"
        except Exception as exc:
            logger.error(f"Peer upload exception: {exc}")
            return False, str(exc)
    except Exception as exc:
        logger.error(f"Push remote whitelist fatal error: {exc}")
        return False, str(exc)


def _clear_remote_conflict(peer_ip: str, port: int) -> Tuple[bool, Optional[str]]:
    try:
        import urllib.request
        if peer_ip not in _get_allowed_peer_ips():
            return False, 'IP address is not allowed.'

        secret = get_sync_secret()
        if not secret:
            return False, 'The sync secret is missing.'

        timestamp = str(int(time.time()))
        path = '/api/sync/clear-conflict'
        # Sign the nonce and an empty body.
        nonce = hashlib.sha256(os.urandom(32)).hexdigest()
        signature = generate_hmac_signature(secret, 'POST', path, timestamp, nonce, '')

        scheme = (os.getenv('SYNC_SCHEME') or os.getenv('MANAGER_SCHEME', 'http')).lower()
        url = f"{scheme}://{peer_ip}:{port}{path}"
        req = urllib.request.Request(url, data=b'', method='POST')
        req.add_header('X-Sync-Timestamp', timestamp)
        req.add_header('X-Sync-Nonce', nonce)
        req.add_header('X-Sync-Signature', signature)

        import ssl
        verify_cert = os.getenv('SYNC_VERIFY_CERT', 'true').lower() == 'true'
        if not sync_transport_allowed(scheme, verify_cert):
            raise ValueError('Synchronization requires HTTPS with certificate verification.')
        context = None
        if scheme == 'https' and not verify_cert:
            context = ssl._create_unverified_context()
        with open_sync_peer(req, 6, context) as response:
            if response.status == 200:
                return True, None
        return False, f"Onverwachte HTTP status: {response.status}"
    except Exception as exc:
        return False, str(exc)


@app.route('/api/sync/merge-data', methods=['GET'])
@requires_auth
def sync_merge_data():
    """Return local and remote filters for comparison."""
    state = _read_sync_state()
    peer_ip = state.get('peer')

    try:
        if os.path.exists(WHITELIST_PATH):
            with open(WHITELIST_PATH, 'r') as f:
                local_content = f.read()
        else:
            local_content = ''
    except Exception as exc:
        return jsonify({'success': False, 'error': f'Could not read the local filter: {exc}'}), 500

    remote_content = ''
    remote_error = None
    if peer_ip:
        remote_content, remote_error = _fetch_remote_whitelist(peer_ip, _get_sync_port())

    return jsonify({
        'success': True,
        'conflict': bool(state.get('conflict')),
        'peer': peer_ip,
        'localContent': local_content,
        'remoteContent': remote_content or '',
        'remoteError': remote_error
    }), 200


@app.route('/api/sync/merge-apply', methods=['POST'])
@requires_auth
def sync_merge_apply():
    """Save the merged filter locally and optionally push it to a peer."""
    data = request.get_json() or {}
    content = data.get('content')
    push_peer = bool(data.get('pushPeer', True))

    if not isinstance(content, str) or not content or '\x00' in content:
        return jsonify({'success': False, 'error': 'Invalid filter content'}), 400

    try:
        apply_synced_filter(content)
        _audit_event('legacy_filter_merge_applied', revision=hashlib.sha256(content.encode('utf-8')).hexdigest())
        mtime = time.time()

        state = _read_sync_state()
        peer_ip = state.get('peer')
        peer_updated = False
        peer_error = None
        peer_cleared = False
        peer_clear_error = None

        if push_peer and peer_ip:
            peer_updated, peer_error = _push_remote_whitelist(peer_ip, _get_sync_port(), content)
            # After upload, verify remote hash matches what we sent, with retries
            remote_hash = None
            verify_error = None
            local_sync_hash = _compute_sync_hash_from_text(content)
            max_retries = 3
            retry_delay = 0.8  # seconds
            attempt = 0
            while peer_updated and attempt < max_retries:
                try:
                    scheme = (os.getenv('SYNC_SCHEME') or os.getenv('MANAGER_SCHEME', 'http')).lower()
                    remote_url = f"{scheme}://{peer_ip}:{_get_sync_port()}/api/sync/info"
                    secret = get_sync_secret()
                    if secret:
                        timestamp = str(int(time.time()))
                        path = '/api/sync/info'
                        nonce = hashlib.sha256(os.urandom(32)).hexdigest()
                        signature = generate_hmac_signature(secret, 'GET', path, timestamp, nonce)
                        headers = {
                            'X-Sync-Timestamp': timestamp,
                            'X-Sync-Nonce': nonce,
                            'X-Sync-Signature': signature
                        }
                        verify_cert = os.getenv('SYNC_VERIFY_CERT', 'true').lower() == 'true'
                        if not sync_transport_allowed(scheme, verify_cert):
                            raise ValueError('Synchronization requires HTTPS with certificate verification.')
                        resp = requests.get(remote_url, headers=headers, timeout=5, verify=verify_cert, allow_redirects=False)
                        if resp.status_code == 200:
                            info = resp.json()
                            remote_hash = _preferred_sync_hash(info)
                            verify_error = None
                        else:
                            verify_error = f"Remote info fetch failed: {resp.status_code}"
                    else:
                        verify_error = 'No sync secret for hash verify'
                except Exception as ve:
                    verify_error = str(ve)
                # Compare hashes
                if remote_hash == local_sync_hash:
                    break
                attempt += 1
                if attempt < max_retries:
                    time.sleep(retry_delay)
            if peer_updated and remote_hash == local_sync_hash:
                # Try clearing remote conflict with retries
                clear_attempts = 0
                max_clear_retries = 4
                clear_delay = 1.0
                clear_errors = []
                while clear_attempts < max_clear_retries:
                    clear_attempts += 1
                    try:
                        peer_cleared, peer_clear_error = _clear_remote_conflict(peer_ip, _get_sync_port())
                    except Exception as exc:
                        peer_cleared, peer_clear_error = False, str(exc)
                    if peer_cleared:
                        logger.info(f"Remote conflict cleared on attempt {clear_attempts}")
                        break
                    else:
                        clear_errors.append(f"attempt{clear_attempts}:{peer_clear_error}")
                        logger.warning(f"Clear attempt {clear_attempts} failed: {peer_clear_error}")
                        time.sleep(clear_delay)

                if not peer_cleared:
                    aggregated = '; '.join(clear_errors)
                    peer_clear_error = f"Failed to clear remote conflict after {max_clear_retries} attempts: {aggregated}"
            elif peer_updated:
                peer_cleared, peer_clear_error = False, f'Remote whitelist hash mismatch after upload (local: {local_sync_hash[:8]}, remote: {remote_hash[:8] if remote_hash else "?"}). {verify_error or ""} Conflict not cleared.'
            else:
                peer_cleared, peer_clear_error = False, 'Remote whitelist update failed; conflict not cleared.'

        # Clear local conflict state only after updating and clearing the peer.
        if (not push_peer or (peer_updated and peer_cleared)):
            logger.info("Cleared local conflict status after merging and updating the peer.")
            _write_sync_state({
                'conflict': False,
                'last_sync': datetime.now().isoformat(),
                'direction': 'manual-merge',
                'peer': peer_ip
            })
        else:
            logger.warning(f"Conflictstatus blijft staan: peer_updated={peer_updated}, peer_cleared={peer_cleared}, peer_error={peer_error}, peer_clear_error={peer_clear_error}")

        return jsonify({
            'success': True,
            'peer': peer_ip,
            'peer_updated': peer_updated,
            'peer_error': peer_error,
            'peer_cleared': peer_cleared,
            'peer_clear_error': peer_clear_error
        }), 200
    except Exception as exc:
        logger.error(f"Merge apply error: {exc}")
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/api/sync/clear-conflict', methods=['POST'])
@requires_sync_auth
def sync_clear_conflict():
    """Clear local sync conflict state (HMAC protected)."""
    try:
        _write_sync_state({
            'conflict': False,
            'last_sync': datetime.now().isoformat(),
            'direction': 'remote-clear',
            'peer': _client_ip()
        })
        return jsonify({'success': True}), 200
    except Exception as exc:
        logger.error(f"Sync clear conflict error: {exc}")
        return jsonify({'success': False, 'error': str(exc)}), 500


@app.route('/api/sync/run', methods=['POST'])
@requires_auth
def run_sync_now():
    success, message, status = _start_manual_sync()
    return jsonify({'success': success, 'message': message}), status


@app.route('/api/sync/run/status', methods=['GET'])
@requires_auth
def run_sync_status():
    state = _read_manual_sync_state()
    return jsonify({'success': True, 'state': state}), 200


@app.context_processor
def logging_privacy_context():
    from .logs import policy
    external = os.getenv('PRIVACY_POLICY_URL', '').strip()
    try:
        parsed = urlsplit(external)
        if external and (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password):
            external = ''
    except ValueError:
        external = ''
    return dict(log_privacy=policy(), privacy_contact=os.getenv('PRIVACY_CONTACT', '').strip()[:300],
                privacy_policy_url=external)


@app.get('/privacy')
def privacy_notice():
    return render_template('privacy.html'), 200, {'Cache-Control': 'no-store'}


@app.route('/api/logs', methods=['GET'])
@requires_auth
def get_logs():
    """Query only application logs within the configured retention window."""
    from .logs import query_logs
    try:
        path = next((getattr(handler, 'baseFilename') for handler in audit_logger.handlers
                     if getattr(handler, 'baseFilename', None)), AUDIT_LOG_PATH)
        return jsonify(query_logs(request.args, path))
    except ValueError as error:
        return jsonify(success=False, error=str(error)), 400
    except (OSError, TimeoutError):
        logger.warning('Log query could not be completed.')
        return jsonify(success=False, error='Cannot read logs. Check the service configuration.'), 503


@app.route('/')
def index():
    """Render the management workspace."""
    return workspace_response()


def workspace_response(status=200):
    if _is_authenticated_request():
        page = render_template('index.html', app_config=get_app_config())
    else:
        page = render_template('signin.html', destination='/')
    return page, status, {'Cache-Control': 'no-store'}


@app.route('/api/entries', methods=['GET'])
@requires_auth
def get_entries():
    """Haal alle whitelist entries op."""
    try:
        logger.debug(f"Loading entries from {WHITELIST_PATH}")
        
        # Check that the filter file exists.
        if not os.path.exists(WHITELIST_PATH):
            logger.warning(f"Whitelist file not found at {WHITELIST_PATH}")
            # Return an empty list if the filter file is missing.
            return jsonify({
                'success': True,
                'entries': [],
                'count': 0,
                'warning': f'Whitelist file not found at {WHITELIST_PATH}'
            })
        
        with file_lock(str(whitelist_manager.whitelist_path) + '.lock'):
            entries = whitelist_manager.read_whitelist()
            revision = hashlib.sha256(whitelist_manager.whitelist_path.read_bytes()).hexdigest()
        
        logger.debug(f"Successfully loaded {len(entries)} entries")
        return jsonify({
            'success': True,
            'entries': entries,
            'count': len(entries),
            'revision': revision,
            'canEditRules': permits(current_role(), 'proxy') and not paired_standby()
        })
    except FileNotFoundError as e:
        logger.warning(f"Whitelist file not found: {e}")
        return jsonify({
            'success': True,
            'entries': [],
            'count': 0,
            'warning': 'Whitelist file not found'
        })
    except PermissionError as e:
        logger.error(f"Permission denied reading whitelist: {e}")
        return jsonify({
            'success': False,
            'error': f'Permission denied: {str(e)}'
        }), 403
    except Exception as e:
        logger.error(f"Error reading entries: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'Error reading entries: {str(e)}'
        }), 500


def filter_mutation(handler):
    """Serialize browser rule changes and check the view revision before using IDs."""
    @wraps(handler)
    def guarded(*args, **kwargs):
        data = request.get_json(silent=True) if request.method != 'DELETE' else {}
        if not isinstance(data, dict):
            return jsonify(success=False, error='No request data received.'), 400
        if any(key in data and type(data[key]) is not bool for key in ('auto_restart', 'is_comment')):
            return jsonify(success=False, error='Restart and comment options must be booleans.'), 400
        expected = request.args.get('revision') if request.method == 'DELETE' else data.get('revision')
        if expected is not None and (not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected)):
            return jsonify(success=False, error='Invalid filter revision.'), 400
        try:
            with file_lock(str(whitelist_manager.whitelist_path) + '.lock'):
                # The earlier compatibility check is advisory; drafts may have
                # been saved while this request was waiting for the same lock.
                if has_pending_changes(whitelist_manager.whitelist_path):
                    return jsonify(success=False, error=PENDING_CHANGES), 409
                compatibility_error = domain_editor_error(whitelist_manager.whitelist_path)
                if compatibility_error:
                    return jsonify(success=False, error=compatibility_error), 409
                if expected is not None:
                    path = whitelist_manager.whitelist_path
                    current = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
                    if expected != current:
                        return jsonify(success=False, error='The rules changed. Refresh the list before making changes.'), 409
                return handler(*args, **kwargs)
        except TimeoutError:
            return jsonify(success=False, error='File is in use; try again.'), 409
    return guarded


@app.route('/api/entries', methods=['POST'])
@requires_auth
@filter_mutation
def add_entry():
    """Add a filter rule."""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': 'No request data received.'
            }), 400
        
        # Require content to be a string.
        content = data.get('content')
        if not isinstance(content, str):
            return jsonify({
                'success': False,
                'error': 'Content must be a string.'
            }), 400
        
        content = content.strip()
        is_comment = data.get('is_comment', False)
        auto_restart = data.get('auto_restart', True)
        
        # Handle description properly
        description = data.get('description')
        if isinstance(description, str):
            description = description.strip() or None
        else:
            description = None
        
        if not content:
            return jsonify({
                'success': False,
                'error': 'Content cannot be empty.'
            }), 400
        
        # Add the filter rule.
        success, error = whitelist_manager.add_entry(content, is_comment, description)
        
        if not success:
            return jsonify({
                'success': False,
                'error': error
            }), 400
        
        # Restart the service when requested.
        restart_success = None
        restart_error = None
        restart_queued = False
        if auto_restart and not is_comment:
            restart_queued = True
            restart_tinyproxy_async()

        try:
            new_line = None
            try:
                entries_after = whitelist_manager.read_whitelist()
                if entries_after:
                    new_line = entries_after[-1].get('original')
            except Exception:
                pass
            _audit_event(
                'whitelist_add',
                entry=_audit_entry_value(new_line or content),
                description=description or '',
                is_comment=is_comment
            )
        except Exception:
            pass
        
        return jsonify({
            'success': True,
            'message': 'Rule added.',
            'restart_attempted': auto_restart and not is_comment,
            'restart_success': restart_success,
            'restart_error': restart_error,
            'restart_queued': restart_queued
        })
        
    except Exception as e:
        logger.error(f'Could not add rule: {e}')
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/status', methods=['GET'])
@requires_auth
def get_status():
    """Return node role, virtual IP ownership, and Tinyproxy status."""
    try:
        filter_metadata = {'whitelist_mtime': None, 'whitelist_size': None, 'canEditRules': permits(current_role(), 'proxy') and not paired_standby()}
        try:
            metadata = os.stat(WHITELIST_PATH)
            filter_metadata.update(whitelist_mtime=metadata.st_mtime, whitelist_size=metadata.st_size)
        except OSError:
            pass
        if not ha_enabled():
            import socket
            service = whitelist_manager.get_service_status()
            return jsonify({
                'configured_role': 'STANDALONE', 'current_state': 'STANDALONE',
                'hostname': socket.gethostname(), 'tinyproxy_active': service['running'],
                'tinyproxy_check_status': service['status'], 'vip_active': False,
                'vip_address': '', 'peer_address': None, 'peer_online': None,
                'last_sync': None, 'split_brain_warning': False,
                **filter_metadata,
            })
        import subprocess
        import sys
        import os as os_module
        import re
        import socket

        if managed_ha():
            interfaces = json.loads(subprocess.run(
                ['ip', '-j', '-4', 'address', 'show'], check=True,
                capture_output=True, text=True, timeout=2,
            ).stdout)
            vip = os.getenv('VIP_ADDRESS', '')
            owns_vip = any(address.get('local') == vip for interface in interfaces
                           for address in interface.get('addr_info', []))
            keepalived = subprocess.run(['systemctl', 'is-active', '--quiet', 'keepalived.service'],
                                        check=False, timeout=2).returncode == 0
            service = whitelist_manager.get_service_status()
            responsive = proxy_responsive(os.getenv('TINYPROXY_CONFIG_PATH', '/etc/tinyproxy/tinyproxy.conf'))
            return jsonify(configured_role='MASTER' if os.getenv('PROXYPERMIT_HA_NODE') == 'a' else 'BACKUP',
                           current_state='FAULT' if not keepalived or not service['running'] or not responsive else
                           'MASTER' if owns_vip else 'BACKUP',
                           hostname=socket.gethostname(), tinyproxy_active=service['running'],
                           tinyproxy_check_status=service['status'] if responsive else 'unresponsive', vip_active=owns_vip, vip_address=vip,
                           peer_address=None, peer_online=None, last_sync=None, split_brain_warning=False,
                           **filter_metadata)
        
        status = {
            'configured_role': 'UNKNOWN',
            'current_state': 'UNKNOWN',
            'vip_active': False,
            'vip_address': os_module.getenv('VIP_ADDRESS', ''),
            'tinyproxy_active': False,
            'tinyproxy_check_status': 'unknown',  # Status of the chk_tinyproxy script
            'peer_address': None,
            'peer_online': None,
            'peer_check_method': 'none',
            'last_sync': None,
            **filter_metadata,
            'hostname': None,
            'split_brain_warning': False,
            'debug_info': {}
        }
        
        is_windows = sys.platform == 'win32'
        
        # Resolve the local hostname.
        try:
            result = subprocess.run(['hostname'], capture_output=True, text=True, timeout=2)
            if result.returncode == 0:
                status['hostname'] = result.stdout.strip()
        except:
            pass

        def _collect_local_identifiers() -> set:
            identifiers = set()
            try:
                hostname = socket.gethostname()
                fqdn = socket.getfqdn()

                if hostname:
                    identifiers.add(hostname)
                if fqdn:
                    identifiers.add(fqdn)

                for host in [hostname, fqdn]:
                    if not host:
                        continue
                    try:
                        identifiers.update(socket.gethostbyname_ex(host)[2])
                    except Exception:
                        pass
            except Exception:
                pass

            keepalived_src = os_module.getenv('KEEPALIVED_UNICAST_SRC', '').strip()
            if keepalived_src:
                identifiers.add(keepalived_src)

            # Also include IPv4s discovered from local interfaces for robust node matching.
            try:
                ip_result = subprocess.run(
                    ['ip', '-o', '-4', 'addr', 'show'],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                if ip_result.returncode == 0:
                    for line in ip_result.stdout.splitlines():
                        parts = line.split()
                        if 'inet' in parts:
                            idx = parts.index('inet')
                            if idx + 1 < len(parts):
                                identifiers.add(parts[idx + 1].split('/')[0])
            except Exception:
                pass

            try:
                host_ip_result = subprocess.run(
                    ['hostname', '-I'],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                if host_ip_result.returncode == 0:
                    for item in host_ip_result.stdout.split():
                        identifiers.add(item.strip())
            except Exception:
                pass

            identifiers.update({'127.0.0.1', 'localhost'})
            return {item for item in identifiers if item}

        def _check_peer_node(peer_address: str) -> tuple:
            """Check peer node reachability via manager health endpoint, fallback to TCP connect."""
            scheme = os_module.getenv('MANAGER_SCHEME', 'http')
            manager_port_raw = os_module.getenv('MANAGER_PORT', '8080')
            manager_port = int(manager_port_raw) if manager_port_raw.isdigit() else 8080
            verify_cert = os_module.getenv('SYNC_VERIFY_CERT', 'true').lower() == 'true'

            peer_url = f"{scheme}://{peer_address}:{manager_port}/health"
            try:
                response = requests.get(peer_url, timeout=2, verify=verify_cert)
                if response.status_code == 200:
                    return True, 'http'
                return False, 'http'
            except Exception as http_exc:
                status['debug_info']['peer_http_error'] = str(http_exc)

            try:
                with socket.create_connection((peer_address, manager_port), timeout=2):
                    return True, 'tcp'
            except Exception as tcp_exc:
                status['debug_info']['peer_tcp_error'] = str(tcp_exc)
                return False, 'tcp'
        
        # On Linux, determine the configured role from keepalived.conf.
        if not is_windows:
            try:
                keepalived_conf = '/etc/keepalived/keepalived.conf'
                if os_module.path.exists(keepalived_conf):
                    with open(keepalived_conf, 'r') as f:
                        conf_content = f.read()
                        
                        # Zoek priority in vrrp_instance block
                        priority_match = re.search(r'vrrp_instance\s+\w+\s*\{[^}]*priority\s+(\d+)', conf_content, re.DOTALL)
                        if priority_match:
                            priority = int(priority_match.group(1))
                            # Priority 150 = MASTER, 100 = BACKUP
                            status['configured_role'] = 'MASTER' if priority >= 120 else 'BACKUP'
                            status['debug_info']['priority'] = priority
                            logger.debug(f"Priority detected from config: {priority} -> {status['configured_role']}")
                        
                        # Zoek state declaration
                        state_match = re.search(r'state\s+(MASTER|BACKUP|FAULT)', conf_content)
                        if state_match:
                            status['debug_info']['config_state'] = state_match.group(1)
                            
            except Exception as e:
                logger.warning(f"Could not read keepalived.conf: {e}")
                status['debug_info']['conf_read_error'] = str(e)
            
            # Read recent Keepalived state changes from the journal.
            try:
                result = subprocess.run(
                    ['journalctl', '-u', 'keepalived', '-n', '50', '--no-pager'],
                    capture_output=True,
                    text=True,
                    timeout=3
                )
                
                output = result.stdout
                
                # Search from newest to oldest for the current state.
                lines = output.split('\n')
                for line in reversed(lines):
                    if 'Entering MASTER STATE' in line or 'Entered MASTER STATE' in line:
                        status['current_state'] = 'MASTER'
                        break
                    elif 'Entering BACKUP STATE' in line or 'Entered BACKUP STATE' in line:
                        status['current_state'] = 'BACKUP'
                        break
                    elif 'Entering FAULT STATE' in line or 'Entered FAULT STATE' in line:
                        status['current_state'] = 'FAULT'
                        break
                
                # Use the latest Tinyproxy check result.
                for line in reversed(lines):
                    lower_line = line.lower()
                    if 'chk_tinyproxy' not in lower_line:
                        continue

                    if ('failed' in lower_line or
                        'returning 1' in lower_line or
                        'exited with status 1' in lower_line):
                        status['tinyproxy_check_status'] = 'check_failed'
                        status['debug_info']['vrrp_script_status'] = 'failed'
                        status['debug_info']['vrrp_script_last_line'] = line.strip()
                        break

                    if ('returning 0' in lower_line or
                        'now returning 0' in lower_line or
                        'succeeded' in lower_line):
                        status['tinyproxy_check_status'] = 'check_passed'
                        status['debug_info']['vrrp_script_status'] = 'passed'
                        status['debug_info']['vrrp_script_last_line'] = line.strip()
                        break
                
                status['debug_info']['keepalived_status'] = 'running'
                
            except Exception as e:
                logger.warning(f"Could not get keepalived status via journalctl: {e}")
                status['debug_info']['keepalived_error'] = str(e)
                
                # Fallback: use systemctl status
                try:
                    result = subprocess.run(
                        ['systemctl', 'status', 'keepalived'],
                        capture_output=True,
                        text=True,
                        timeout=2
                    )
                    
                    output = result.stdout + result.stderr
                    
                    # Parse the current state from the command output.
                    if 'Entering MASTER STATE' in output or 'Entered MASTER STATE' in output:
                        status['current_state'] = 'MASTER'
                    elif 'Entering BACKUP STATE' in output or 'Entered BACKUP STATE' in output:
                        status['current_state'] = 'BACKUP'
                    elif 'Entering FAULT STATE' in output or 'Entered FAULT STATE' in output:
                        status['current_state'] = 'FAULT'
                    
                except:
                    pass
            
            # Check whether this node holds the virtual IP.
            try:
                result = subprocess.run(
                    ['ip', 'addr', 'show'],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                if status['vip_address'] in result.stdout:
                    status['vip_active'] = True
                    
            except Exception as e:
                logger.warning(f"Could not check VIP: {e}")
            
            # Check tinyproxy status
            try:
                result = subprocess.run(
                    ['systemctl', 'is-active', TINYPROXY_SERVICE],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                status['tinyproxy_active'] = result.returncode == 0 and result.stdout.strip() == 'active'
            except Exception as e:
                logger.warning(f"Could not check tinyproxy: {e}")

            # Check peer node status (other configured node)
            try:
                node0_address = os_module.getenv('NODE0_ADDRESS', '').strip()
                node1_address = os_module.getenv('NODE1_ADDRESS', '').strip()
                node_candidates = [item for item in [node0_address, node1_address] if item]
                local_ids = _collect_local_identifiers()

                peer_address = None

                # Prefer deterministic 2-node mapping instead of "first not local".
                if node0_address and node1_address:
                    local_is_node0 = node0_address in local_ids
                    local_is_node1 = node1_address in local_ids

                    if local_is_node0 and not local_is_node1:
                        peer_address = node1_address
                    elif local_is_node1 and not local_is_node0:
                        peer_address = node0_address
                    elif local_is_node0 and local_is_node1:
                        status['debug_info']['peer_detect_warning'] = 'Both NODE0 and NODE1 match local identifiers'
                    else:
                        status['debug_info']['peer_detect_warning'] = 'Neither NODE0 nor NODE1 matched local identifiers'
                elif len(node_candidates) == 1:
                    peer_address = node_candidates[0]
                else:
                    status['debug_info']['peer_detect_warning'] = 'No NODE0/NODE1 addresses configured'

                status['peer_address'] = peer_address
                if peer_address:
                    peer_online, method = _check_peer_node(peer_address)
                    status['peer_online'] = peer_online
                    status['peer_check_method'] = method
            except Exception as e:
                status['debug_info']['peer_check_error'] = str(e)
            
            # Check for inconsistent master and virtual IP status.
            if status['current_state'] == 'MASTER' and status['vip_active']:
                # Normally one master holds the virtual IP.
                status['split_brain_warning'] = False
            elif status['current_state'] == 'MASTER' and not status['vip_active']:
                # A node reporting master without the virtual IP may indicate
                # recent failover or split brain.
                status['split_brain_warning'] = True
                status['debug_info']['split_brain_indicator'] = 'master_without_vip'
            
            # Check laatste sync
            try:
                sync_log = '/var/log/tinyproxy-sync.log'
                if os_module.path.exists(sync_log):
                    result = subprocess.run(
                        ['tail', '-n', '1', sync_log],
                        capture_output=True,
                        text=True,
                        timeout=2
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        status['last_sync'] = result.stdout.strip()
            except:
                pass

                
        else:
            # Windows test mode
            status['configured_role'] = 'MASTER'
            status['current_state'] = 'TEST'
            status['vip_active'] = True
            status['tinyproxy_active'] = True if DEMO_MODE else False
            status['tinyproxy_check_status'] = 'check_passed' if DEMO_MODE else 'unknown'
            status['peer_address'] = 'NODE1'
            status['peer_online'] = True if DEMO_MODE else None
            status['peer_check_method'] = 'demo'
        
        return jsonify(status)
        
    except Exception as e:
        logger.error(f'Could not load status: {e}')
        return jsonify({
            'configured_role': 'ERROR',
            'current_state': 'ERROR',
            'vip_active': False,
            'vip_address': os.getenv('VIP_ADDRESS', ''),
            'tinyproxy_active': False,
            'error': str(e)
        }), 500


@app.route('/api/entries/<int:entry_id>', methods=['PUT'])
@requires_auth
@filter_mutation
def update_entry(entry_id):
    """Update a filter rule."""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                'success': False,
                'error': 'No request data received.'
            }), 400
        
        # Require content to be a string.
        content = data.get('content')
        if not isinstance(content, str):
            return jsonify({
                'success': False,
                'error': 'Content must be a string.'
            }), 400
        
        content = content.strip()
        auto_restart = data.get('auto_restart', True)
        is_comment = data.get('is_comment', False)
        
        # Handle description properly
        description = data.get('description')
        if isinstance(description, str):
            description = description.strip() or None
        else:
            description = None
        
        if not content:
            return jsonify({
                'success': False,
                'error': 'Content cannot be empty.'
            }), 400

        old_line = None
        try:
            current_entries = whitelist_manager.read_whitelist()
            if 0 <= entry_id < len(current_entries):
                old_line = current_entries[entry_id].get('original')
        except Exception:
            pass
        
        # Update entry
        success, error = whitelist_manager.update_entry(entry_id, content, description, is_comment=is_comment)
        
        if not success:
            logger.warning(f"Update entry rejected (id={entry_id}): {error}")
            return jsonify({
                'success': False,
                'error': error
            }), 400
        
        # Restart the service when requested.
        restart_success = None
        restart_error = None
        restart_queued = False
        if auto_restart:
            restart_queued = True
            restart_tinyproxy_async()

        try:
            new_line = None
            try:
                entries_after = whitelist_manager.read_whitelist()
                if 0 <= entry_id < len(entries_after):
                    new_line = entries_after[entry_id].get('original')
            except Exception:
                pass
            _audit_event(
                'whitelist_update',
                entry_id=entry_id,
                old_entry=_audit_entry_value(old_line),
                new_entry=_audit_entry_value(new_line or content),
                description=description or '',
                is_comment=is_comment
            )
        except Exception:
            pass
        
        return jsonify({
            'success': True,
            'message': 'Rule updated.',
            'restart_attempted': auto_restart,
            'restart_success': restart_success,
            'restart_error': restart_error,
            'restart_queued': restart_queued
        })
        
    except Exception as e:
        logger.error(f'Could not update rule: {e}')
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/entries/<int:entry_id>', methods=['DELETE'])
@requires_auth
@filter_mutation
def delete_entry(entry_id):
    """Remove a filter rule."""
    try:
        auto_restart = request.args.get('auto_restart', 'true').lower() == 'true'

        old_line = None
        try:
            current_entries = whitelist_manager.read_whitelist()
            if 0 <= entry_id < len(current_entries):
                old_line = current_entries[entry_id].get('original')
        except Exception:
            pass
        
        # Remove the filter rule.
        success, error = whitelist_manager.delete_entry(entry_id)
        
        if not success:
            return jsonify({
                'success': False,
                'error': error
            }), 400
        
        # Restart the service when requested.
        restart_success = None
        restart_error = None
        restart_queued = False
        if auto_restart:
            restart_queued = True
            restart_tinyproxy_async()

        try:
            _audit_event(
                'whitelist_delete',
                entry_id=entry_id,
                old_entry=_audit_entry_value(old_line)
            )
        except Exception:
            pass
        
        return jsonify({
            'success': True,
            'message': 'Rule removed.',
            'restart_attempted': auto_restart,
            'restart_success': restart_success,
            'restart_error': restart_error,
            'restart_queued': restart_queued
        })
        
    except Exception as e:
        logger.error(f'Could not remove rule: {e}')
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/entries/reorder', methods=['POST'])
@requires_auth
@filter_mutation
def reorder_entries():
    """Reorder filter rules."""
    try:
        data = request.get_json()
        
        if not data or 'entry_ids' not in data:
            return jsonify({
                'success': False,
                'error': 'entry_ids is required.'
            }), 400
        
        entry_ids = data['entry_ids']
        auto_restart = data.get('auto_restart', True)
        
        # Require entry_ids to be a list.
        if not isinstance(entry_ids, list):
            return jsonify({
                'success': False,
                'error': 'entry_ids must be an array.'
            }), 400
        
        # Order-only changes keep matching semantics, but must reach the peer too.
        with file_lock(str(whitelist_manager.whitelist_path) + '.lock'):
            if has_pending_changes(whitelist_manager.whitelist_path):
                return jsonify(error=PENDING_CHANGES), 409
            success, error = whitelist_manager.reorder_entries(entry_ids)
            if success:
                record_published_policy(whitelist_manager.whitelist_path, logger)
        
        if not success:
            return jsonify({
                'success': False,
                'error': error
            }), 400
        
        # Restart the service when requested.
        restart_success = None
        restart_error = None
        restart_queued = False
        if auto_restart:
            restart_queued = True
            restart_tinyproxy_async()

        try:
            order_hash = None
            try:
                entries_after = whitelist_manager.read_whitelist()
                joined = '\n'.join(entry.get('original', '') for entry in entries_after)
                order_hash = hashlib.sha256(joined.encode('utf-8')).hexdigest()[:12]
            except Exception:
                pass
            _audit_event(
                'whitelist_reorder',
                count=len(entry_ids),
                order_hash=order_hash or ''
            )
        except Exception:
            pass
        
        return jsonify({
            'success': True,
            'message': 'Rules reordered.',
            'restart_attempted': auto_restart,
            'restart_success': restart_success,
            'restart_error': restart_error,
            'restart_queued': restart_queued
        })
        
    except Exception as e:
        logger.error(f'Could not reorder rules: {e}')
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/validate', methods=['POST'])
@requires_auth
def validate_entry():
    """Validate a filter rule without saving it."""
    try:
        data = request.get_json(silent=True)
        
        if not isinstance(data, dict) or not isinstance(data.get('content'), str):
            return jsonify({
                'success': False,
                'error': 'No request data received.'
            }), 400
        
        content = data.get('content', '').strip()
        is_comment = data.get('is_comment', False)
        
        if not content:
            return jsonify({
                'valid': False,
                'error': 'Content cannot be empty.'
            })
        
        # Validate the input.
        if is_comment:
            content = f"# {content}"
        
        is_valid, error, entry_type = validator.validate_whitelist_entry(content)
        
        # Include the generated pattern for domain rules.
        generated_regex = None
        if is_valid and entry_type == 'domain':
            generated_regex = validator.domain_to_regex(content)
        
        return jsonify({
            'valid': is_valid,
            'error': error,
            'entry_type': entry_type,
            'generated_regex': generated_regex
        })
        
    except Exception as e:
        logger.error(f'Could not validate rule: {e}')
        return jsonify({
            'valid': False,
            'error': str(e)
        }), 500


@app.route('/api/service/status', methods=['GET'])
@requires_auth
def service_status():
    """Haal Tinyproxy service status op."""
    try:
        status = whitelist_manager.get_service_status()
        return jsonify({
            'success': True,
            'status': status
        })
    except Exception as e:
        logger.error(f'Could not load service status: {e}')
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/service/restart', methods=['POST'])
@requires_auth
def restart_service():
    """Restart the Tinyproxy service."""
    try:
        if has_pending_changes(whitelist_manager.whitelist_path):
            return jsonify(success=False, error=PENDING_CHANGES), 409
        if DEMO_MODE:
            return jsonify(success=False, error='Activation requires a Linux installation with Tinyproxy.'), 400
        restart_tinyproxy_async()
        _audit_event('tinyproxy_restart_requested')
        return jsonify({
            'success': True,
            'message': 'Tinyproxy restart started.',
            'restart_queued': True
        })
        
    except Exception as e:
        logger.error(f'Could not restart service: {e}')
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/domain-to-regex', methods=['POST'])
@requires_auth
def domain_to_regex():
    """Convert a domain to a regular expression."""
    try:
        data = request.get_json(silent=True)
        
        if not isinstance(data, dict) or not isinstance(data.get('domain'), str):
            return jsonify({
                'success': False,
                'error': 'No request data received.'
            }), 400
        
        domain = data.get('domain', '').strip()
        
        if not domain:
            return jsonify({
                'success': False,
                'error': 'Domain cannot be empty.'
            }), 400
        
        # Validate the domain first.
        is_valid, error = validator.validate_domain(domain)
        
        if not is_valid:
            return jsonify({
                'success': False,
                'error': error
            }), 400
        
        # Convert the domain to a regular expression.
        regex = validator.domain_to_regex(domain)
        
        return jsonify({
            'success': True,
            'domain': domain,
            'regex': regex
        })
        
    except Exception as e:
        logger.error(f'Could not convert domain: {e}')
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/export', methods=['GET'])
@requires_auth
def export_whitelist():
    """Export the current filter as a download."""
    try:
        # Client may request either 'regex' or 'domain' format. Default to
        # 'regex' to preserve exact stored patterns; convert to readable
        # domain/wildcard only when explicitly requested.
        fmt = (request.args.get('format') or 'regex').lower()
        if fmt == 'domain':
            content = whitelist_manager.render_export_content(prefer_readable=True)
        else:
            content = whitelist_manager.render_export_content(prefer_readable=False)
        
        # Set the download headers.
        response = make_response(content)
        response.headers['Content-Type'] = 'text/plain; charset=utf-8'
        response.headers['Content-Disposition'] = 'attachment; filename=whitelist_export.txt'
        
        logger.info("Whitelist geëxporteerd")
        return response
        
    except Exception as e:
        logger.error(f'Could not export filter: {e}')
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/import', methods=['POST'])
@requires_auth
@filter_mutation
def import_whitelist():
    """Normalize, validate, and import filter rules."""
    try:
        # Log incoming request for debugging import issues
        raw_body = request.get_data(as_text=True)
        logger.info(f"Import request from {request.remote_addr}: body_len={len(raw_body)}")
        try:
            data = request.get_json()
        except Exception:
            data = None
            logger.debug("Failed to parse JSON body for import")
        
        if not isinstance(data, dict) or not isinstance(data.get('content'), str):
            return jsonify({
                'success': False,
                'error': 'No filter content received.'
            }), 400
        
        content = data.get('content', '').strip()
        mode = data.get('mode', 'replace')  # replace or append
        if mode not in ('replace', 'append'):
            return jsonify(success=False, error='Choose append or replace as the import mode.'), 400
        
        if not content:
            return jsonify({
                'success': False,
                'error': 'The import is empty.'
            }), 400
        
        # Parse and validate filter rules.
        lines = content.split('\n')
        validated_entries = []
        errors = []
        skipped_count = 0
        
        # Only keep valid entries for deduplication and writing
        def _normalize_key(e: str) -> Optional[str]:
            e = e.split('#', 1)[0].strip()
            if not e:
                return None
            is_valid, err, etype = validator.validate_whitelist_entry(e)
            if not is_valid:
                return None
            if etype == 'domain':
                nd = validator.normalize_domain_input(e)
                return nd.lower() if nd else e.lower()
            if etype == 'regex':
                s = e
                s = s.replace(r'(^|\.)', '')
                s = s.replace(r'\.', '.')
                s = s.replace(r'(:[0-9]+)?$', '')
                s = s.strip('^$').strip()
                return s.lower()
            return e.lower()

        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            # Skip lege regels
            if not line:
                continue
            # Comments: direct toevoegen
            if line.startswith('#'):
                validated_entries.append(line)
                continue
            # Accept inline descriptions after the pattern.
            pattern_line = line
            description = None
            if re.search(r'\s+#', pattern_line):
                parts = pattern_line.split('#', 1)
                if len(parts) == 2:
                    pattern_line = parts[0].strip()
                    description = parts[1].strip()

            if not pattern_line:
                errors.append(f'Line {line_num}: empty comment.')
                skipped_count += 1
                continue
            # Validate and normalize each rule.
            try:
                is_valid, error_msg, entry_type = validator.validate_whitelist_entry(pattern_line)
                if is_valid and entry_type in ['regex', 'domain']:
                    # Convert domains to regular expressions.
                    if entry_type == 'domain':
                        normalized = validator.normalize_domain_input(pattern_line)
                        if normalized:
                            regex_pattern = validator.domain_to_regex(normalized)
                            if description:
                                validated_entries.append(f"{regex_pattern}  # {description}")
                            else:
                                validated_entries.append(regex_pattern)
                    else:
                        # Normalize simple regular expressions to the canonical form.
                        try:
                            # Extract a domain or IP from the pattern when possible.
                            extracted = whitelist_manager._extract_domain_from_regex(pattern_line)
                            if extracted:
                                # Generate the canonical pattern through the validator.
                                normalized = validator.normalize_domain_input(extracted)
                                if normalized:
                                    regex_pattern = validator.domain_to_regex(normalized)
                                    if description:
                                        validated_entries.append(f"{regex_pattern}  # {description}")
                                    else:
                                        validated_entries.append(regex_pattern)
                                else:
                                    if description:
                                        validated_entries.append(f"{pattern_line}  # {description}")
                                    else:
                                        validated_entries.append(pattern_line)
                            else:
                                if description:
                                    validated_entries.append(f"{pattern_line}  # {description}")
                                else:
                                    validated_entries.append(pattern_line)
                        except Exception:
                            # Preserve the original pattern as a fallback.
                            if description:
                                validated_entries.append(f"{pattern_line}  # {description}")
                            else:
                                validated_entries.append(pattern_line)
                else:
                    errors.append(f"Line {line_num}: {error_msg or 'Validatie gefaald'}")
                    skipped_count += 1
            except Exception as e:
                errors.append(f'Line {line_num}: {str(e)}')
                skipped_count += 1

        seen = set()
        deduped = []
        for e in validated_entries:
            k = _normalize_key(e)
            if k is None and (e.strip() == '' or e.strip().startswith('#')):
                deduped.append(e)
                continue
            if k is None:
                continue
            if k in seen:
                # Duplicate inside uploaded content
                errors.append(f"Duplicaat (upload): {e}")
                skipped_count += 1
                continue
            seen.add(k)
            deduped.append(e)

        if not deduped:
            return jsonify({
                'success': False,
                'error': 'No valid rules to import.',
                'errors': errors
            }), 400

        # Keep import diagnostics limited to counts.
        logger.info(f"Import validation: validated={len(validated_entries)}, skipped={skipped_count}, errors={len(errors)}")

        # Apply the selected import mode.
        if mode == 'replace':
            # Vervang volledige whitelist (use deduped list)
            logger.info(f"Import replace: deduped from {len(validated_entries)} to {len(deduped)} entries")
            success, error = whitelist_manager.replace_all_entries(deduped)
        else:  # append
            # Append rules and skip duplicates.
            current = whitelist_manager.read_whitelist()
            existing_keys = set()
            for c in current:
                if c.get('type') in ('regex', 'domain'):
                    nk = None
                    try:
                        nk = _normalize_key(c.get('content', ''))
                    except Exception:
                        nk = c.get('content', '').lower()
                    if nk:
                        existing_keys.add(nk)

            to_append = []
            for e in deduped:
                k = _normalize_key(e)
                if not k:
                    to_append.append(e)
                    continue
                if k in existing_keys:
                    logger.debug(f"Skipping duplicate append entry: {k}")
                    errors.append(f"Duplicaat (bestaand): {e}")
                    skipped_count += 1
                    continue
                to_append.append(e)

            logger.info(f"Import append: will append {len(to_append)} of {len(deduped)} deduped entries")
            if not to_append:
                return jsonify({'success': True, 'imported_count': 0, 'skipped_count': skipped_count, 'errors': errors or None, 'mode': mode}), 200

            success, error = whitelist_manager.append_entries(to_append)
        
        if not success:
            return jsonify({
                'success': False,
                'error': error,
                'errors': errors
            }), 500
        
        # Restart Tinyproxy when requested.
        restart_queued = False
        restart_success = None
        restart_error = None
        
        if data.get('auto_restart', False):
            try:
                # Start restart async to avoid blocking the HTTP request
                restart_tinyproxy_async()
                restart_queued = True
                restart_success = None
                restart_error = None
                logger.info('Tinyproxy restart queued (async)')
            except Exception as e:
                restart_queued = False
                restart_success = False
                restart_error = str(e)
                logger.error(f'Failed to queue tinyproxy restart: {e}')
        
        # Determine imported_count based on mode and deduplication
        # Include comments in the imported count so UI reflects actual lines written
        if mode == 'replace':
            imported_count = len([e for e in deduped if e.strip()])
        else:
            imported_count = len([e for e in to_append if e.strip()]) if 'to_append' in locals() else 0

        total_skipped = skipped_count

        logger.info(f'Imported {imported_count} filter rules, mode={mode}, skipped={total_skipped}')

        return jsonify({
            'success': True,
            'imported_count': imported_count,
            'skipped_count': total_skipped,
            'errors': errors if errors else None,
            'restart_queued': restart_queued,
            'restart_success': restart_success,
            'restart_error': restart_error,
            'mode': mode
        })
        
    except Exception as e:
        logger.error(f'Could not import filter: {e}')
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.errorhandler(404)
def not_found(e):
    """404 handler."""
    if request.path.startswith('/api/'):
        return jsonify({
            'success': False,
            'error': 'Endpoint not found.'
        }), 404
    return workspace_response(404)


@app.errorhandler(500)
def internal_error(e):
    """500 handler."""
    logger.error(f"Internal server error: {e}")
    if request.path.startswith('/api/'):
        return jsonify({
            'success': False,
            'error': 'Internal server error.'
        }), 500
    return "Internal Server Error", 500


app.extensions['has_permission'] = lambda permission: permits(current_role(), permission)
register_i18n(app)
app.before_request(protect_browser_mutations)
app.before_request(enforce_browser_permissions)
register_proxy_api(app, requires_auth, _is_authenticated_request, whitelist_manager, _audit_event)
register_settings(app, requires_auth, _is_authenticated_request,
                  lambda: (Path(AUTH_ACCOUNTS_FILE) if ENABLE_AUTH and AUTH_ACCOUNTS_FILE else None,
                           Path(SESSION_STATE_FILE)), settings_reauth, _audit_event)


if __name__ == '__main__':
    # Use this server for local testing. Use a production WSGI server for deployment.
    print('WARNING: Running Flask development server. Use Gunicorn in production!')
    app.run(host='127.0.0.1', port=8080, debug=False)
