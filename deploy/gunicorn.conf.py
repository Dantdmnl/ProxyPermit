# Gunicorn configuratie voor ProxyPermit
import os
import sys
from dotenv import load_dotenv

# Laad .env (indien aanwezig)
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
if os.path.exists(env_path):
    load_dotenv(env_path)
else:
    # Probeer ook /etc/proxypermit/.env
    alt_env = '/etc/proxypermit/.env'
    if os.path.exists(alt_env):
        load_dotenv(alt_env)

# Project root in Python path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

chdir = BASE_DIR
pythonpath = BASE_DIR

# === Dynamische configuratie ===
MANAGER_SCHEME = os.getenv('MANAGER_SCHEME', 'http').lower()
MANAGER_PORT = int(os.getenv('MANAGER_PORT', '8080'))
MANAGER_BIND = os.getenv('MANAGER_BIND', '0.0.0.0')
MANAGER_CERT = os.getenv('MANAGER_CERT', '')
MANAGER_KEY = os.getenv('MANAGER_KEY', '')
GUNICORN_BIND = os.getenv('GUNICORN_BIND', '').strip()
GUNICORN_USE_TLS = os.getenv('GUNICORN_USE_TLS', '').strip().lower()

bind = GUNICORN_BIND or f"{MANAGER_BIND}:{MANAGER_PORT}"
backlog = 2048

# SSL support
tls_enabled = (MANAGER_SCHEME == 'https')
if GUNICORN_USE_TLS in ('true', '1', 'yes'):
    tls_enabled = True
elif GUNICORN_USE_TLS in ('false', '0', 'no'):
    tls_enabled = False

if tls_enabled:
    if MANAGER_CERT and MANAGER_KEY:
        certfile = MANAGER_CERT
        keyfile = MANAGER_KEY
    else:
        raise RuntimeError('Gunicorn TLS vereist MANAGER_CERT en MANAGER_KEY in .env')

# Worker processen
workers = int(os.getenv('GUNICORN_WORKERS', '2'))
worker_class = "sync"
worker_connections = 1000
timeout = 120
keepalive = 2

# Logging
accesslog = "-"
errorlog = "-"
loglevel = os.getenv('GUNICORN_LOGLEVEL', 'info')
# AVG/GDPR: Geen IP adressen in access logs
access_log_format = '%(t)s "%(r)s" %(s)s %(b)s'

# Process naming
proc_name = "proxypermit"

# Server mechanics
daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

# Server hooks voor graceful restart
def on_starting(server):
    server.log.info("ProxyPermit wordt gestart...")

def on_reload(server):
    server.log.info("Gunicorn wordt herladen...")

def worker_int(worker):
    worker.log.info("Worker ontvangen interrupt signaal")

def worker_abort(worker):
    """Called when a worker receives the SIGABRT signal."""
    worker.log.info("Worker afgebroken")
