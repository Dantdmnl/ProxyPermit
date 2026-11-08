#!/bin/bash
# Deployment script voor nieuwe bidirectionele sync
set -euo pipefail

echo "=== Deploying Bidirectionele Whitelist Sync v2 ==="

# Check root
if [[ $EUID -ne 0 ]]; then
   echo "Error: Dit script moet als root uitgevoerd worden"
   exit 1
fi

# Bepaal de directory waar dit script staat
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "Bronbestanden directory: $SCRIPT_DIR"
echo ""

if ! command -v logrotate >/dev/null 2>&1; then
    echo "Installeer logrotate package..."
    apt-get update
    apt-get install -y logrotate
    echo "  OK: logrotate geinstalleerd"
else
    echo "Logrotate package al aanwezig"
fi

echo "Installeer logrotate configuratie (indien aanwezig)..."
if [ -f "$SCRIPT_DIR/deploy/logrotate/proxypermit" ]; then
    install -m 644 "$SCRIPT_DIR/deploy/logrotate/proxypermit" /etc/logrotate.d/proxypermit
    echo "  OK: /etc/logrotate.d/proxypermit bijgewerkt"
else
    echo "  INFO: proxypermit.logrotate niet gevonden, overgeslagen"
fi

# Source bestanden
SYNC_SCRIPT="$SCRIPT_DIR/legacy/sync_whitelist_v2.py"
SYNC_SERVICE="$SCRIPT_DIR/deploy/systemd/whitelist-sync-v2.service"
SYNC_TIMER="$SCRIPT_DIR/deploy/systemd/whitelist-sync-v2.timer"

echo ""
echo "[1/7] Verwijderen oude synchronisatieconfiguratie..."
echo "----------------------------------------"

# Stop oude services
for service in whitelist-sync whitelist-rsync; do
    if systemctl is-active "$service.timer" 2>/dev/null; then
        echo "  Stoppen $service.timer..."
        systemctl stop "$service.timer" || true
    fi
    if systemctl is-active "$service.service" 2>/dev/null; then
        echo "  Stoppen $service.service..."
        systemctl stop "$service.service" || true
    fi
done

# Disable oude services
for service in whitelist-sync whitelist-rsync; do
    if systemctl is-enabled "$service.timer" 2>/dev/null; then
        echo "  Disablen $service.timer..."
        systemctl disable "$service.timer" || true
    fi
    if systemctl is-enabled "$service.service" 2>/dev/null; then
        echo "  Disablen $service.service..."
        systemctl disable "$service.service" || true
    fi
done

# Verwijder oude systemd units
echo "  Verwijderen oude systemd-units..."
rm -f /etc/systemd/system/whitelist-sync.service
rm -f /etc/systemd/system/whitelist-sync.timer
rm -f /etc/systemd/system/whitelist-rsync.service
rm -f /etc/systemd/system/whitelist-rsync.timer

# Verwijder oude sync scripts
echo "  Verwijderen oude synchronisatiescripts..."
rm -f /usr/local/bin/sync-whitelist.sh
rm -f /usr/local/bin/sync_whitelist.py
rm -f /usr/local/bin/whitelist-rsync.sh
rm -f /opt/proxypermit/sync_whitelist.py

# Verwijder oude state files (maar backup eerst)
if [ -f /var/lib/proxypermit/sync-state.json ]; then
    echo "  Backup oude synchronisatiestatus..."
    cp /var/lib/proxypermit/sync-state.json \
       /var/lib/proxypermit/sync-state.json.old.$(date +%Y%m%d-%H%M%S) || true
fi

# Reload na verwijderen
systemctl daemon-reload

echo "  OK: Oude synchronisatie volledig verwijderd"

echo ""
echo "[2/8] Maak state directory..."
mkdir -p /var/lib/proxypermit
chmod 755 /var/lib/proxypermit
mkdir -p /etc/proxypermit
chmod 755 /etc/proxypermit
echo "  OK: Directories klaar"

echo ""
echo "[3/8] Controleer sync secret..."
SECRET_FILE="/etc/proxypermit/sync-secret.key"
if [ ! -f "$SECRET_FILE" ]; then
    echo "  ERROR: Sync secret niet gevonden!"
    echo ""
    echo "  De synchronisatie vereist een shared secret voor veilige authenticatie."
    echo "  Genereer een secret met:"
    echo "    bash $SCRIPT_DIR/generate-sync-secret.sh"
    echo ""
    echo "  Kopieer daarna hetzelfde secret naar alle nodes!"
    echo ""
    exit 1
else
    SECRET_LENGTH=$(wc -c < "$SECRET_FILE" | tr -d ' ')
    if [ "$SECRET_LENGTH" -lt 32 ]; then
        echo "  ERROR: Sync secret te kort ($SECRET_LENGTH chars, min 32 nodig)"
        exit 1
    fi
    echo "  OK: Sync secret gevonden en geldig (${SECRET_LENGTH} chars)"
fi

echo ""
echo "[4/8] Installeer synchronisatiescript..."
if [ ! -f "$SYNC_SCRIPT" ]; then
    echo "  ERROR: $SYNC_SCRIPT niet gevonden!"
    echo "  Controleer of sync_whitelist_v2.py aanwezig is in $SCRIPT_DIR"
    exit 1
fi
install -m 755 "$SYNC_SCRIPT" /usr/local/bin/sync_whitelist_v2.py
echo "  OK: Script geinstalleerd naar /usr/local/bin/sync_whitelist_v2.py"

echo ""
echo "[5/8] Installeer systemd services..."
if [ ! -f "$SYNC_SERVICE" ]; then
    echo "  ERROR: $SYNC_SERVICE niet gevonden!"
    echo "  Controleer of whitelist-sync-v2.service aanwezig is in $SCRIPT_DIR"
    exit 1
fi
if [ ! -f "$SYNC_TIMER" ]; then
    echo "  ERROR: $SYNC_TIMER niet gevonden!"
    echo "  Controleer of whitelist-sync-v2.timer aanwezig is in $SCRIPT_DIR"
    exit 1
fi
install -m 644 "$SYNC_SERVICE" /etc/systemd/system/
install -m 644 "$SYNC_TIMER" /etc/systemd/system/
cat > /etc/systemd/system/whitelist-sync-v2.service <<EOF
[Unit]
Description=Bidirectionele Whitelist Sync (API-based)
After=network.target proxypermit.service keepalived.service
Wants=proxypermit.service

[Service]
Type=oneshot
WorkingDirectory=${SCRIPT_DIR}
EnvironmentFile=-/etc/proxypermit/.env
EnvironmentFile=-${SCRIPT_DIR}/.env
ExecStart=/usr/bin/python3 /usr/local/bin/sync_whitelist_v2.py
StandardOutput=journal
StandardError=journal
SyslogIdentifier=whitelist-sync-v2

# Security
PrivateTmp=yes
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/etc/tinyproxy /var/lib/proxypermit /var/lock

[Install]
WantedBy=multi-user.target
EOF
echo "  OK: Systemd-units geinstalleerd naar /etc/systemd/system/"

echo ""
echo "[6/8] Reload systemd..."
systemctl daemon-reload
echo "  OK: Daemon reloaded"

echo ""
echo "[7/8] Enable en start nieuwe synchronisatie..."
systemctl enable whitelist-sync-v2.timer
systemctl start whitelist-sync-v2.timer
echo "  OK: Synchronisatie v2 actief"

echo ""
echo "[8/8] Verificatie..."
if systemctl is-active whitelist-sync-v2.timer &>/dev/null; then
    echo "  OK: Timer draait"
else
    echo "  WARN: Timer niet actief!"
fi

# Status
echo ""
echo "=== Deployment succesvol! ==="
echo ""
echo "Status nieuwe synchronisatie:"
systemctl status whitelist-sync-v2.timer --no-pager --lines=0 || true
echo ""
echo "Volgende synchronisatie over:"
systemctl list-timers whitelist-sync-v2.timer --no-pager
echo ""
echo "Monitor logs met:"
echo "  journalctl -u whitelist-sync-v2 -f"
echo ""
echo "Test synchronisatie handmatig:"
echo "  systemctl start whitelist-sync-v2.service"

