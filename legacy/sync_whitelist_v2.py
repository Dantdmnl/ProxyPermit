#!/usr/bin/env python3
"""
Bidirectionele whitelist sync tussen HA nodes.
Gebruikt API calls en timestamp-based conflict resolution.
"""
import os
import sys
import json
import time
import socket
import hashlib
import fcntl  # pylint: disable=import-error
import stat
import tempfile
from urllib.request import HTTPRedirectHandler, HTTPSHandler, build_opener
import hmac
import re
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

SYNC_ENGINE_VERSION = 'v2.3.0'

# Load environment file if present (systemd oneshot may not have it)
ENV_FILE_CANDIDATES = [
    '/etc/proxypermit/.env',
    str(Path(__file__).resolve().parent / '.env'),
    '/opt/proxypermit/.env'
]


def load_env_file() -> None:
    """Load key=value pairs from the first available env file."""
    for candidate in ENV_FILE_CANDIDATES:
        if not os.path.exists(candidate):
            continue
        try:
            with open(candidate, 'r', encoding='utf-8') as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key.strip(), value.strip())
            logger.info(f"Env geladen uit {candidate}")
            return
        except Exception as exc:
            logger.warning(f"Kon env bestand niet laden ({candidate}): {exc}")


load_env_file()

WHITELIST_PATH = os.getenv('WHITELIST_PATH', '/etc/tinyproxy/whitelist.txt')
LOCK_FILE = '/var/lock/whitelist-sync.lock'
STATE_FILE = os.getenv('SYNC_STATE_FILE') or '/var/lib/proxypermit/sync-state.json'
SYNC_SCHEME = (os.getenv('SYNC_SCHEME') or os.getenv('MANAGER_SCHEME', 'http')).lower()
SYNC_PORT_RAW = os.getenv('SYNC_PORT') or os.getenv('MANAGER_PORT', '8080')
SYNC_PORT = int(SYNC_PORT_RAW) if SYNC_PORT_RAW.isdigit() else 8080
SYNC_VERIFY_CERT = os.getenv('SYNC_VERIFY_CERT', 'true').lower() == 'true'
# Optioneel: forceer een master node (host/IP) als autoriteit tijdens conflicten.
# Wanneer `SYNC_MASTER_PREFERENCE=true` en `SYNC_MASTER_HOST` is ingesteld, zal de
# sync-job bij near-simultane wijzigingen de opgegeven master als bron-van-waarheid
# gebruiken in plaats van automatisch in conflict-mode te gaan.
SYNC_MASTER_HOST = os.getenv('SYNC_MASTER_HOST', '').strip()
SYNC_MASTER_PREFERENCE = os.getenv('SYNC_MASTER_PREFERENCE', 'false').lower() == 'true'
try:
    SYNC_NEAR_SIMULTANEOUS_WINDOW = max(5, int(os.getenv('SYNC_NEAR_SIMULTANEOUS_WINDOW', '30')))
except Exception:
    SYNC_NEAR_SIMULTANEOUS_WINDOW = 30
SYNC_DEFER_ON_NEAR_SIMULTANEOUS = os.getenv('SYNC_DEFER_ON_NEAR_SIMULTANEOUS', 'true').lower() == 'true'
# Configurable safety cap: prefer SYNC_MAX_WHITELIST_SIZE, fallback to legacy MAX_WHITELIST_SIZE, default 1 MiB
try:
    MAX_WHITELIST_SIZE = int(os.getenv('SYNC_MAX_WHITELIST_SIZE') or os.getenv('MAX_WHITELIST_SIZE') or 1048576)
except Exception:
    MAX_WHITELIST_SIZE = 1048576


def resolve_host_ips(host: str):
    """Resolve a hostname to a set of IPs; returns empty set on failure or empty host."""
    if not host:
        return set()

    try:
        import ipaddress

        try:
            ipaddress.ip_address(host)
            return {host}
        except Exception:
            pass

        addrs = set()
        try:
            res = socket.getaddrinfo(host, None)
            for item in res:
                addr = item[4][0]
                addrs.add(addr)
        except Exception:
            try:
                addrs.update(socket.gethostbyname_ex(host)[2])
            except Exception:
                pass

        return addrs
    except Exception:
        return set()


def get_local_identifiers() -> set:
    """Build identifiers that represent this node only."""
    identifiers = set()
    try:
        hostname = socket.gethostname()
        if hostname:
            identifiers.add(hostname)

        fqdn = socket.getfqdn()
        if fqdn:
            identifiers.add(fqdn)

        try:
            identifiers.update(socket.gethostbyname_ex(hostname)[2])
        except Exception:
            pass

        try:
            identifiers.update(socket.gethostbyname_ex(fqdn)[2])
        except Exception:
            pass
    except Exception:
        pass

    keepalived_src = os.getenv('KEEPALIVED_UNICAST_SRC', '').strip()
    if keepalived_src:
        identifiers.add(keepalived_src)

    return {item for item in identifiers if item}


# Pre-resolve configured master host to IPs for faster comparisons and stronger matching
_resolved_master_ips = resolve_host_ips(SYNC_MASTER_HOST) if SYNC_MASTER_HOST else set()
SYNC_MASTER_HOST_IPS = _resolved_master_ips or set()


def _normalize_identifier(value: str) -> str:
    """Normalize host/IP-like identifiers for stable comparisons."""
    if value is None:
        return ''
    return str(value).strip().lower()


def _is_master_peer(peer_ip: str) -> bool:
    """Return True when the given peer address matches configured master."""
    if not SYNC_MASTER_HOST:
        return False

    peer_norm = _normalize_identifier(peer_ip)
    master_norm = _normalize_identifier(SYNC_MASTER_HOST)
    master_ip_norms = {_normalize_identifier(ip) for ip in (SYNC_MASTER_HOST_IPS or set())}
    return peer_norm == master_norm or peer_norm in master_ip_norms


def _is_local_node_master(local_identifiers: set) -> bool:
    """Return True when current node matches configured master host/IP."""
    if not SYNC_MASTER_HOST:
        return False

    local_norms = {_normalize_identifier(identifier) for identifier in (local_identifiers or set())}
    master_norm = _normalize_identifier(SYNC_MASTER_HOST)
    master_ip_norms = {_normalize_identifier(ip) for ip in (SYNC_MASTER_HOST_IPS or set())}

    return master_norm in local_norms or any(master_ip in local_norms for master_ip in master_ip_norms)


SYNC_SECRET_FILE = os.getenv('SYNC_SECRET_FILE') or os.getenv('SYNC_SECRET_PATH') or '/etc/proxypermit/sync-secret.key'


def get_sync_secret() -> Optional[str]:
    """Lees shared secret voor sync authenticatie."""
    try:
        if not os.path.exists(SYNC_SECRET_FILE):
            logger.error(f"Sync secret bestand niet gevonden: {SYNC_SECRET_FILE}")
            logger.error("Genereer een secret met: openssl rand -hex 32 > /etc/proxypermit/sync-secret.key")
            return None
        
        with open(SYNC_SECRET_FILE, 'r') as f:
            secret = f.read().strip()
        
        if len(secret) < 32:
            logger.error(f"Sync secret te kort (min 32 chars): {len(secret)}")
            return None
        
        return secret
    except Exception as e:
        logger.error(f"Fout bij lezen sync secret: {e}")
        return None


def generate_hmac_signature(secret: str, method: str, path: str, timestamp: str, nonce: str, body: str = '') -> str:
    """Genereer HMAC-SHA256 signature voor request authenticatie."""
    # Concateneer alle relevante data inclusief nonce
    message = f"{method}|{path}|{timestamp}|{nonce}|{body}"
    
    # Genereer HMAC signature
    signature = hmac.new(
        secret.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    return signature


def get_keepalived_config() -> Optional[Dict[str, Any]]:
    """Parse keepalived.conf voor node IPs en prioriteiten."""
    conf_path = '/etc/keepalived/keepalived.conf'
    
    if not os.path.exists(conf_path):
        logger.warning(f"Keepalived-configuratie niet gevonden: {conf_path}")
        return None
    
    try:
        with open(conf_path, 'r') as f:
            content = f.read()
        
        # Extract VIP
        import re
        vip_match = re.search(r'virtual_ipaddress\s*\{[^}]*?(\d+\.\d+\.\d+\.\d+)', content, re.DOTALL)
        vip = vip_match.group(1) if vip_match else None
        
        # Extract priority
        priority_match = re.search(r'priority\s+(\d+)', content)
        priority = int(priority_match.group(1)) if priority_match else None
        
        # Extract unicast peers
        peers = []
        unicast_match = re.search(r'unicast_peer\s*\{([^}]+)\}', content, re.DOTALL)
        if unicast_match:
            peer_block = unicast_match.group(1)
            peers = re.findall(r'(\d+\.\d+\.\d+\.\d+)', peer_block)
        
        return {
            'vip': vip,
            'priority': priority,
            'peers': peers,
            'is_master': priority >= 120 if priority else False
        }
    except Exception as e:
        logger.error(f"Fout bij het parsen van keepalived-configuratie: {e}")
        return None


def get_current_role() -> str:
    """Detecteer huidige VRRP role (MASTER/BACKUP)."""
    # Check keepalived master flag
    if os.path.exists('/run/keepalived/master'):
        return 'MASTER'
    
    # Fallback: check journalctl
    try:
        result = subprocess.run(
            ['journalctl', '-u', 'keepalived', '-n', '50', '--no-pager'],
            capture_output=True,
            text=True,
            timeout=3
        )
        
        lines = result.stdout.strip().split('\n')
        for line in reversed(lines):
            if 'Entering MASTER STATE' in line:
                return 'MASTER'
            elif 'Entering BACKUP STATE' in line:
                return 'BACKUP'
    except:
        pass
    
    return 'UNKNOWN'


def get_file_info(path: str) -> Dict[str, Any]:
    """Haal file metadata op."""
    try:
        stat = os.stat(path)
        with open(path, 'rb') as f:
            content = f.read()

        raw_hash = hashlib.sha256(content).hexdigest()
        sync_hash = compute_sync_hash(content.decode('utf-8', errors='replace'))
        
        return {
            'exists': True,
            'mtime': stat.st_mtime,
            'size': stat.st_size,
            'hash': raw_hash,
            'raw_hash': raw_hash,
            'sync_hash': sync_hash,
            'timestamp': datetime.fromtimestamp(stat.st_mtime).isoformat()
        }
    except FileNotFoundError:
        return {'exists': False}
    except Exception as e:
        logger.error(f"Fout bij het ophalen van bestandsinformatie: {e}")
        return {'exists': False, 'error': str(e)}

def create_backup(filepath: str) -> Optional[str]:
    """Maak backup van bestand voordat synchronisatie plaatsvindt."""
    try:
        if not os.path.exists(filepath):
            return None
        
        backup_dir = '/var/lib/proxypermit/backups'
        os.makedirs(backup_dir, exist_ok=True)
        
        timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f"whitelist_{timestamp_str}.txt"
        backup_path = os.path.join(backup_dir, backup_filename)
        
        import shutil
        shutil.copy2(filepath, backup_path)
        
        logger.info(f"Backup aangemaakt: {backup_path}")
        
        # Cleanup oude backups (bewaar alleen laatste 10)
        cleanup_old_backups(backup_dir, keep=10)
        
        return backup_path
    except Exception as e:
        logger.error(f"Kon geen backup maken: {e}")
        return None


def cleanup_old_backups(backup_dir: str, keep: int = 10) -> None:
    """Verwijder oude backups, bewaar alleen de meest recente."""
    try:
        backups = []
        for filename in os.listdir(backup_dir):
            if filename.startswith('whitelist_') and filename.endswith('.txt'):
                filepath = os.path.join(backup_dir, filename)
                backups.append((os.path.getmtime(filepath), filepath))
        
        # Sorteer op modificatie tijd (nieuwste eerst)
        backups.sort(reverse=True)
        
        # Verwijder oude backups
        for _, filepath in backups[keep:]:
            os.remove(filepath)
            logger.debug(f"Oude backup verwijderd: {filepath}")
    except Exception as e:
        logger.warning(f"Kon oude backups niet opruimen: {e}")



def verify_content_hash(content: str, expected_hash: str) -> bool:
    """Verifieer of content hash overeenkomt met verwachte hash."""
    try:
        actual_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
        if not hmac.compare_digest(actual_hash, expected_hash):
            logger.error(f"Hash mismatch: verwacht {expected_hash[:8]}..., gekregen {actual_hash[:8]}...")
            return False
        return True
    except Exception as e:
        logger.error(f"Hash verificatie fout: {e}")
        return False


def _canonicalize_whitelist_content(content: str) -> str:
    """Normalize whitelist content for semantic sync comparisons.

    Removes blank/comment-only lines and inline comments, then sorts unique
    entries so cosmetic differences do not trigger sync conflicts.
    """
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


def compute_sync_hash(content: str) -> str:
    """Compute stable hash for semantic whitelist contents."""
    canonical = _canonicalize_whitelist_content(content)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def get_sync_hash(info: Dict[str, Any]) -> str:
    """Return preferred hash used for sync decisions (semantic first)."""
    if not isinstance(info, dict):
        return ''
    return info.get('sync_hash') or info.get('hash') or ''


def verify_content_against_remote_info(content: str, remote_info: Dict[str, Any]) -> bool:
    """Verify downloaded content against remote hash data with compatibility fallbacks."""
    if not isinstance(remote_info, dict):
        return False

    raw_expected = remote_info.get('raw_hash') or remote_info.get('hash')
    if raw_expected and verify_content_hash(content, raw_expected):
        return True

    sync_expected = remote_info.get('sync_hash')
    if sync_expected:
        actual_sync = compute_sync_hash(content)
        if hmac.compare_digest(actual_sync, sync_expected):
            logger.info("Raw hash mismatch, but sync_hash matched (comment/format-only verschil)")
            return True

    return False

class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_peer(request, timeout, context):
    return build_opener(RejectRedirects(), HTTPSHandler(context=context)).open(request, timeout=timeout)


def check_node_health(ip: str, port: int = SYNC_PORT, timeout: int = 3) -> bool:
    """Controleer of andere node bereikbaar is."""
    try:
        import urllib.request
        import ssl
        url = f"{SYNC_SCHEME}://{ip}:{port}/health"
        req = urllib.request.Request(url, method='GET')
        context = None
        if SYNC_SCHEME == 'https':
            context = ssl.create_default_context()
            if not SYNC_VERIFY_CERT:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
        with open_peer(req, timeout, context) as response:
            if response.status == 200:
                data = json.loads(response.read())
                status = data.get('status')
                checks = data.get('checks', {})
                if status == 'healthy':
                    return True
                if status == 'degraded':
                    if checks.get('keepalived_active') and checks.get('tinyproxy_active'):
                        return True
        return False
    except Exception as e:
        logger.debug(f"Node {ip} is niet bereikbaar: {e}")
        return False
    return False


def get_remote_file_info(ip: str, port: int = SYNC_PORT) -> Optional[Dict[str, Any]]:
    """Haal bestandsinformatie op van remote node via API."""
    try:
        import urllib.request
        
        # Haal secret op
        secret = get_sync_secret()
        if not secret:
            logger.error("Kan sync niet uitvoeren zonder secret")
            return None
        
        # Genereer authenticatie headers met nonce
        timestamp = str(int(time.time()))
        nonce = hashlib.sha256(os.urandom(32)).hexdigest()
        path = '/api/sync/info'
        signature = generate_hmac_signature(secret, 'GET', path, timestamp, nonce)
        
        import ssl
        url = f"{SYNC_SCHEME}://{ip}:{port}{path}"
        req = urllib.request.Request(url, method='GET')
        req.add_header('X-Sync-Timestamp', timestamp)
        req.add_header('X-Sync-Nonce', nonce)
        req.add_header('X-Sync-Signature', signature)
        context = None
        if SYNC_SCHEME == 'https':
            context = ssl.create_default_context()
            if not SYNC_VERIFY_CERT:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
        with open_peer(req, 5, context) as response:
            if response.status == 200:
                return json.loads(response.read())
    except Exception as e:
        logger.error(f"Kon bestandsinformatie niet ophalen van {ip}: {e}")
        return None


def download_whitelist(ip: str, port: int = SYNC_PORT) -> Optional[str]:
    """Download whitelist van remote node."""
    try:
        import urllib.request
        
        # Haal secret op
        secret = get_sync_secret()
        if not secret:
            logger.error("Kan sync niet uitvoeren zonder secret")
            return None
        
        # Genereer authenticatie headers met nonce
        timestamp = str(int(time.time()))
        nonce = hashlib.sha256(os.urandom(32)).hexdigest()
        path = '/api/sync/download'
        signature = generate_hmac_signature(secret, 'GET', path, timestamp, nonce)
        
        import ssl
        url = f"{SYNC_SCHEME}://{ip}:{port}{path}"
        req = urllib.request.Request(url, method='GET')
        req.add_header('X-Sync-Timestamp', timestamp)
        req.add_header('X-Sync-Nonce', nonce)
        req.add_header('X-Sync-Signature', signature)
        context = None
        if SYNC_SCHEME == 'https':
            context = ssl.create_default_context()
            if not SYNC_VERIFY_CERT:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
        with open_peer(req, 10, context) as response:
            if response.status == 200:
                return response.read().decode('utf-8')
    except Exception as e:
        logger.error(f"Kon whitelist niet downloaden van {ip}: {e}")
        return None


def upload_whitelist(ip: str, content: str, port: int = SYNC_PORT) -> bool:
    """Upload whitelist naar remote node."""
    try:
        import urllib.request
        
        # Haal secret op
        secret = get_sync_secret()
        if not secret:
            logger.error("Kan sync niet uitvoeren zonder secret")
            return False
        
        # Genereer authenticatie headers met nonce
        timestamp = str(int(time.time()))
        nonce = hashlib.sha256(os.urandom(32)).hexdigest()
        path = '/api/sync/upload'
        signature = generate_hmac_signature(secret, 'POST', path, timestamp, nonce, content)
        
        import ssl
        url = f"{SYNC_SCHEME}://{ip}:{port}{path}"
        data = content.encode('utf-8')
        req = urllib.request.Request(url, data=data, method='POST')
        req.add_header('Content-Type', 'text/plain')
        req.add_header('X-Sync-Timestamp', timestamp)
        req.add_header('X-Sync-Nonce', nonce)
        req.add_header('X-Sync-Signature', signature)
        context = None
        if SYNC_SCHEME == 'https':
            context = ssl.create_default_context()
            if not SYNC_VERIFY_CERT:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
        with open_peer(req, 10, context) as response:
            if response.status == 200:
                return True
        return False
    except Exception as e:
        logger.error(f"Kon whitelist niet uploaden naar {ip}: {e}")
        return False


def set_remote_conflict(ip: str, payload: Dict[str, Any], port: int = SYNC_PORT) -> bool:
    """Markeer conflict state op remote node via API."""
    try:
        import urllib.request

        secret = get_sync_secret()
        if not secret:
            logger.error("Kan conflict niet zetten zonder secret")
            return False

        timestamp = str(int(time.time()))
        nonce = hashlib.sha256(os.urandom(32)).hexdigest()
        path = '/api/sync/set-conflict'
        body = json.dumps(payload)
        signature = generate_hmac_signature(secret, 'POST', path, timestamp, nonce, body)

        import ssl
        url = f"{SYNC_SCHEME}://{ip}:{port}{path}"
        data = body.encode('utf-8')
        req = urllib.request.Request(url, data=data, method='POST')
        req.add_header('Content-Type', 'application/json')
        req.add_header('X-Sync-Timestamp', timestamp)
        req.add_header('X-Sync-Nonce', nonce)
        req.add_header('X-Sync-Signature', signature)
        context = None
        if SYNC_SCHEME == 'https':
            context = ssl.create_default_context()
            if not SYNC_VERIFY_CERT:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
        with open_peer(req, 6, context) as response:
            if response.status == 200:
                return True
        return False
    except Exception as e:
        logger.warning(f"Kon conflict state niet zetten op {ip}: {e}")
        return False


def acquire_lock():
    """Keep the lock inode stable and let the OS release it on exit."""
    handle = open(LOCK_FILE, 'a+', encoding='utf-8')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except BlockingIOError:
        handle.close()
        logger.info("Synchronisatie is al bezig, overgeslagen")
        return None
    except OSError:
        handle.close()
        raise


def atomic_write(path, content, mode):
    fd, name = tempfile.mkstemp(prefix='.whitelist.sync.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            current = path.stat()
            os.chown(name, current.st_uid, current.st_gid)  # pylint: disable=no-member
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def apply_download(content, expected_hash):
    path = Path(WHITELIST_PATH)
    with open(str(path) + '.lock', 'a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = path.read_bytes()
        if hashlib.sha256(previous).hexdigest() != expected_hash:
            raise RuntimeError('Lokale filterregels zijn tijdens synchronisatie gewijzigd.')
        mode = stat.S_IMODE(path.stat().st_mode)
        create_backup(WHITELIST_PATH)
        atomic_write(path, content.encode('utf-8'), mode)
        if reload_tinyproxy():
            return
        atomic_write(path, previous, mode)
        reload_tinyproxy()
        raise RuntimeError('Activeren van ontvangen filterregels is mislukt.')


def save_sync_state(state: Dict[str, Any]) -> None:
    """Sla sync state op."""
    path = Path(STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + '.lock', 'a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        atomic_write(path, json.dumps(state, indent=2).encode('utf-8'), 0o600)


def load_sync_state() -> Dict[str, Any]:
    """Laad sync state."""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Kon state niet laden: {e}")
    
    return {}


def reload_tinyproxy() -> bool:
    """Probeer tinyproxy te reloaden, fallback naar restart."""
    try:
        result = subprocess.run(
            ['systemctl', 'reload', os.getenv('TINYPROXY_SERVICE', 'tinyproxy')],
            timeout=15,
            check=True,
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            logger.info("Tinyproxy reload uitgevoerd na sync")
            return True
    except subprocess.CalledProcessError as exc:
        logger.warning(f"Tinyproxy reload faalde, probeer restart: {exc}")
    except Exception as exc:
        logger.warning(f"Tinyproxy reload fout, probeer restart: {exc}")

    try:
        subprocess.run(
            ['systemctl', 'restart', os.getenv('TINYPROXY_SERVICE', 'tinyproxy')],
            timeout=30,
            check=True,
            capture_output=True,
            text=True
        )
        logger.info("Tinyproxy restart uitgevoerd na sync")
        return True
    except Exception as exc:
        logger.error(f"Tinyproxy restart faalde na sync: {exc}")
        return False


def sync_whitelist() -> None:
    """Hoofdfunctie: synchroniseer whitelist tussen nodes."""
    
    if (SYNC_SCHEME != 'https' or not SYNC_VERIFY_CERT) and os.getenv('SYNC_ALLOW_INSECURE_TRANSPORT', '').lower() != 'true':
        raise RuntimeError('Synchronisatie vereist HTTPS met certificaatcontrole.')
    lock = acquire_lock()
    if lock is None:
        return

    # Check op actieve conflict-state
    sync_state = load_sync_state()
    if sync_state.get('conflict'):
        if SYNC_MASTER_PREFERENCE and SYNC_MASTER_HOST:
            logger.warning("Actieve conflict-state gedetecteerd, maar master preference is actief; ga door met autoritatieve sync.")
            for k, v in sync_state.items():
                logger.warning(f"  {k}: {v}")
        else:
            logger.warning("Synchronisatie geblokkeerd: er is een actieve conflict-state. Details:")
            for k, v in sync_state.items():
                logger.warning(f"  {k}: {v}")
            lock.close()
            return

    try:
        logger.info("=== Start whitelist sync ===")
        logger.info(
            "Sync engine %s | master_pref=%s master_host=%s window=%ss defer_on_near=%s scheme=%s:%s verify_cert=%s",
            SYNC_ENGINE_VERSION,
            SYNC_MASTER_PREFERENCE,
            SYNC_MASTER_HOST or '-',
            SYNC_NEAR_SIMULTANEOUS_WINDOW,
            SYNC_DEFER_ON_NEAR_SIMULTANEOUS,
            SYNC_SCHEME,
            SYNC_PORT,
            SYNC_VERIFY_CERT
        )
        
        # Haal keepalived-configuratie op
        ka_config = get_keepalived_config()
        if not ka_config:
            logger.error("Kon keepalived-configuratie niet lezen")
            return
        
        current_role = get_current_role()
        logger.info(f"Huidige rol: {current_role} (prioriteit: {ka_config['priority']})")
        
        # Haal lokale bestandsinformatie op
        local_info = get_file_info(WHITELIST_PATH)
        if not local_info['exists']:
            logger.warning("Lokale whitelist bestaat niet")
            return
        
        logger.info(f"Lokaal: {local_info['timestamp']} (sync_hash: {get_sync_hash(local_info)[:8]}...)")
        
        # Check alle peers
        for peer_ip in ka_config['peers']:
            logger.info(f"Check peer: {peer_ip}")
            
            # Controleer of peer bereikbaar is
            peer_available = check_node_health(peer_ip)
            if not peer_available:
                logger.warning(f"Peer {peer_ip} is niet beschikbaar - overgeslagen")
                # Log peer status in merge log
                try:
                    merge_log_path = '/var/lib/proxypermit/whitelist_merge_log.json'
                    merge_entry = {
                        'timestamp': datetime.now().isoformat(),
                        'peer': peer_ip,
                        'type': 'peer-unavailable',
                        'role': current_role,
                        'local_hash': local_info.get('hash'),
                        'local_timestamp': local_info.get('timestamp')
                    }
                    if os.path.exists(merge_log_path):
                        with open(merge_log_path, 'r') as f:
                            log_data = json.load(f)
                    else:
                        log_data = []
                    log_data.append(merge_entry)
                    with open(merge_log_path, 'w') as f:
                        json.dump(log_data, f, indent=2)
                except Exception as log_exc:
                    logger.warning(f"Kon peer-unavailable log niet schrijven: {log_exc}")
                continue
            
            # Haal externe bestandsinformatie op
            remote_info = get_remote_file_info(peer_ip)
            if not remote_info or not remote_info.get('exists'):
                logger.warning(f"Peer {peer_ip} heeft geen whitelist")
                continue
            
            logger.info(f"Remote: {remote_info['timestamp']} (sync_hash: {get_sync_hash(remote_info)[:8]}...)")

            # Fast path: bij gelijke hash niets doen (voorkomt onnodige authoritative acties)
            local_sync_hash = get_sync_hash(local_info)
            remote_sync_hash = get_sync_hash(remote_info)
            if local_sync_hash and local_sync_hash == remote_sync_hash:
                logger.info("Bestanden zijn identiek - geen synchronisatie nodig")
                continue
            
            # If master-preference is enabled and a SYNC_MASTER_HOST is configured,
            # enforce master authority: if the peer is the configured master, always
            # download from it; if this node is the configured master, always upload
            # to the peer. This avoids relying solely on mtime.
            if SYNC_MASTER_PREFERENCE and SYNC_MASTER_HOST:
                # gather local identifiers
                try:
                    local_identifiers = get_local_identifiers()
                except Exception:
                    local_identifiers = set()

                logger.debug(f"Master-preference enabled. SYNC_MASTER_HOST={SYNC_MASTER_HOST}, local_identifiers={local_identifiers}")

                # If the peer is the configured master, download from it regardless of mtimes
                # If the configured master matches peer (either hostname/IP), prefer it
                if _is_master_peer(peer_ip):
                    logger.info(f"Master-preference: peer {peer_ip} configured as master — attempting authoritative download")
                    # Security checks: ensure remote_info provides size and hash, and size under threshold
                    try:
                        rsize = int(remote_info.get('size', 0) or 0)
                    except Exception:
                        rsize = 0
                    if rsize and rsize > MAX_WHITELIST_SIZE:
                        logger.warning(f"Authoritative download refused: remote file too large ({rsize} bytes)")
                    elif not remote_info.get('hash'):
                        logger.warning("Authoritative download refused: remote did not provide hash")
                    else:
                        content = download_whitelist(peer_ip)
                        if content and verify_content_against_remote_info(content, remote_info):
                            try:
                                apply_download(content, local_info['hash'])
                                save_sync_state({'last_sync': datetime.now().isoformat(), 'direction': 'download', 'peer': peer_ip, 'note': 'master_authoritative'})
                                logger.info(f"Applied authoritative content from master {peer_ip}")
                                continue
                            except Exception as e:
                                logger.error(f"Failed to apply authoritative download atomically: {e}")
                        else:
                            logger.warning(f"Failed to download/verify authoritative content from master {peer_ip}")

                # If this node is the configured master, push local to peer regardless of mtimes
                # If this node matches the configured master (by resolved IPs or identifiers), upload authoritative copy
                if _is_local_node_master(local_identifiers):
                    logger.info(f"Master-preference: this node is configured master — uploading authoritative copy to {peer_ip}")
                    try:
                        with open(WHITELIST_PATH, 'r', encoding='utf-8') as f:
                            content = f.read()
                        if len(content.encode('utf-8')) > MAX_WHITELIST_SIZE:
                            logger.error("Authoritative upload aborted: local whitelist too large")
                        else:
                            if upload_whitelist(peer_ip, content):
                                # Post-upload: verify remote hash matches local
                                time.sleep(1)
                                post_info = get_remote_file_info(peer_ip)
                                if post_info and get_sync_hash(post_info) == compute_sync_hash(content):
                                    save_sync_state({'last_sync': datetime.now().isoformat(), 'direction': 'upload', 'peer': peer_ip, 'note': 'master_authoritative'})
                                    logger.info(f"Uploaded authoritative copy to {peer_ip} and verification succeeded")
                                    continue
                                else:
                                    logger.warning(f"Uploaded to {peer_ip} but remote verification failed")
                            else:
                                logger.warning(f"Upload of authoritative copy to {peer_ip} failed")
                    except Exception as e:
                        logger.warning(f"Error reading local whitelist for authoritative upload: {e}")

            # Conflict detection: wie is nieuwer?
            local_mtime = local_info['mtime']
            remote_mtime = remote_info['mtime']
            
            time_diff = abs(local_mtime - remote_mtime)
            
            # VEILIGHEID: Als beide recent gewijzigd zijn (< 30s), probeer master-preferentie
            # Als er een SYNC_MASTER_HOST is ingesteld en SYNC_MASTER_PREFERENCE=true,
            # gebruiken we die host als bron-van-waarheid om onnodige conflicts te reduceren.
            if time_diff < SYNC_NEAR_SIMULTANEOUS_WINDOW:
                logger.warning(f"Near-simultane wijziging gedetecteerd (verschil: {time_diff}s)")
                logger.debug(
                    "Details: local_ts=%s local_hash=%s remote_ts=%s remote_hash=%s",
                    local_info.get('timestamp'),
                    get_sync_hash(local_info)[:8],
                    remote_info.get('timestamp'),
                    get_sync_hash(remote_info)[:8]
                )

                # Als master-preferentie actief is, kies de geconfigureerde master als bron
                if SYNC_MASTER_PREFERENCE and SYNC_MASTER_HOST:
                    try:
                        # Verzamel lokale identifiers (hostnaam/FQDN/locale IPs)
                        local_identifiers = get_local_identifiers()
                        logger.debug(f"Local identifiers for master-preference: {local_identifiers}")
                        logger.debug(f"Configured SYNC_MASTER_HOST={SYNC_MASTER_HOST}, peer={peer_ip}")

                        # Als de geconfigureerde master overeenkomt met de remote peer, download remote
                        if _is_master_peer(peer_ip):
                            logger.info(f"Master preference: peer {peer_ip} is geconfigureerde master - downloaden.")
                            content = download_whitelist(peer_ip)
                            if not content:
                                logger.error("Download failed (master preference)")
                            else:
                                if not verify_content_against_remote_info(content, remote_info):
                                    logger.error("Hash verificatie mislukt - download genegeerd (master preference)")
                                else:
                                    apply_download(content, local_info['hash'])
                                    save_sync_state({
                                        'last_sync': datetime.now().isoformat(),
                                        'direction': 'download',
                                        'peer': peer_ip,
                                        'hash': get_sync_hash(remote_info),
                                        'note': 'master_preference'
                                    })
                                    continue

                        # Als de geconfigureerde master overeenkomt met lokaal, push lokaal naar remote
                        if _is_local_node_master(local_identifiers):
                            logger.info(f"Master preference: deze node is geconfigureerde master - upload naar {peer_ip}.")
                            with open(WHITELIST_PATH, 'r') as f:
                                content = f.read()
                            if upload_whitelist(peer_ip, content):
                                logger.info(f"Whitelist succesvol geupload naar {peer_ip} (master preference)")
                                save_sync_state({
                                    'last_sync': datetime.now().isoformat(),
                                    'direction': 'upload',
                                    'peer': peer_ip,
                                    'hash': get_sync_hash(local_info),
                                    'note': 'master_preference'
                                })
                                continue
                            else:
                                logger.error(f"Upload naar {peer_ip} mislukt (master preference)")
                    except Exception as e:
                        logger.warning(f"Master preference check faalde: {e}")

                # Fallback op node-rol om onnodige conflict-state te voorkomen.
                if SYNC_MASTER_PREFERENCE:
                    try:
                        if current_role == 'MASTER':
                            logger.info(f"Master preference fallback: lokale node is MASTER - upload naar {peer_ip}.")
                            with open(WHITELIST_PATH, 'r', encoding='utf-8') as f:
                                content = f.read()
                            if upload_whitelist(peer_ip, content):
                                save_sync_state({
                                    'last_sync': datetime.now().isoformat(),
                                    'direction': 'upload',
                                    'peer': peer_ip,
                                    'hash': get_sync_hash(local_info),
                                    'note': 'master_preference_role_fallback_master'
                                })
                                continue
                            logger.warning(f"Master preference fallback upload naar {peer_ip} mislukt")

                        elif current_role == 'BACKUP':
                            logger.info(f"Master preference fallback: lokale node is BACKUP - download van {peer_ip}.")
                            content = download_whitelist(peer_ip)
                            if content and verify_content_against_remote_info(content, remote_info):
                                apply_download(content, local_info['hash'])
                                save_sync_state({
                                    'last_sync': datetime.now().isoformat(),
                                    'direction': 'download',
                                    'peer': peer_ip,
                                    'hash': get_sync_hash(remote_info),
                                    'note': 'master_preference_role_fallback_backup'
                                })
                                continue
                            logger.warning(f"Master preference fallback download van {peer_ip} mislukt")
                    except Exception as e:
                        logger.warning(f"Master preference role fallback faalde: {e}")

                    if SYNC_DEFER_ON_NEAR_SIMULTANEOUS:
                        logger.warning(
                            "Near-simultane wijziging kon niet autoritatief opgelost worden; conflict wordt gedeferd naar volgende sync-run."
                        )
                        save_sync_state({
                            'last_sync': datetime.now().isoformat(),
                            'direction': 'defer-near-simultaneous',
                            'peer': peer_ip,
                            'local_timestamp': local_info.get('timestamp'),
                            'remote_timestamp': remote_info.get('timestamp'),
                            'note': 'master_preference_deferred'
                        })
                        continue

                # VEILIGHEID: Als beide recent gewijzigd zijn (< 30s), overslaan (mogelijk conflict)
                logger.warning(f"CONFLICT: Beide bestanden recent gewijzigd (verschil: {time_diff}s)")
                logger.warning(
                    "Details: local_ts=%s local_hash=%s remote_ts=%s remote_hash=%s",
                    local_info.get('timestamp'),
                    get_sync_hash(local_info)[:8],
                    remote_info.get('timestamp'),
                    get_sync_hash(remote_info)[:8]
                )
                conflict_state = {
                    'conflict': True,
                    'timestamp': datetime.now().isoformat(),
                    'local_mtime': local_mtime,
                    'remote_mtime': remote_mtime,
                    'peer': peer_ip,
                    'local_hash': get_sync_hash(local_info),
                    'remote_hash': get_sync_hash(remote_info),
                    'local_timestamp': local_info.get('timestamp'),
                    'remote_timestamp': remote_info.get('timestamp'),
                    'direction': 'auto-conflict'
                }
                save_sync_state(conflict_state)
                if set_remote_conflict(peer_ip, conflict_state):
                    logger.info(f"Conflict state gezet op peer {peer_ip}")
                else:
                    logger.warning(f"Kon conflict state niet zetten op peer {peer_ip}")
                continue
            
            # Bepaal sync richting
            if remote_mtime > local_mtime:
                # Remote is nieuwer - download
                logger.info(f"Remote is nieuwer - download van {peer_ip}")
                
                content = download_whitelist(peer_ip)
                if not content:
                    logger.error("Download failed")
                    continue
                
                # SECURITY: Verifieer hash van gedownloade content
                if not verify_content_against_remote_info(content, remote_info):
                    logger.error("Hash verificatie mislukt - download genegeerd")
                    continue
                
                apply_download(content, local_info['hash'])

                save_sync_state({
                    'last_sync': datetime.now().isoformat(),
                    'direction': 'download',
                    'peer': peer_ip,
                    'hash': get_sync_hash(remote_info)
                })
                
            elif local_mtime > remote_mtime:
                # Lokaal bestand is nieuwer - upload naar remote
                logger.info(f"Lokaal bestand is nieuwer - upload naar {peer_ip}")
                
                # Lees lokale content
                with open(WHITELIST_PATH, 'r') as f:
                    content = f.read()
                
                # Upload naar peer
                if upload_whitelist(peer_ip, content):
                    logger.info(f"Whitelist succesvol geupload naar {peer_ip}")
                    
                    save_sync_state({
                        'last_sync': datetime.now().isoformat(),
                        'direction': 'upload',
                        'peer': peer_ip,
                        'hash': get_sync_hash(local_info)
                    })
                else:
                    logger.error(f"Upload naar {peer_ip} mislukt")
        
        logger.info("=== Synchronisatie voltooid ===")
        
    finally:
        lock.close()


if __name__ == '__main__':
    try:
        sync_whitelist()
    except Exception as e:
        logger.error(f"Synchronisatie mislukt: {e}", exc_info=True)
        sys.exit(1)
