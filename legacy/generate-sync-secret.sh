#!/bin/bash
# Genereer shared secret voor veilige sync tussen nodes

set -euo pipefail

SECRET_FILE="/etc/proxypermit/sync-secret.key"
SECRET_DIR=$(dirname "$SECRET_FILE")

echo "=== Genereren Sync Secret ==="

# Check root
if [[ $EUID -ne 0 ]]; then
   echo "Error: Dit script moet als root uitgevoerd worden"
   exit 1
fi

# Maak directory als niet bestaat
if [ ! -d "$SECRET_DIR" ]; then
    echo "Aanmaken directory: $SECRET_DIR"
    mkdir -p "$SECRET_DIR"
    chmod 755 "$SECRET_DIR"
fi

# Check of secret al bestaat
if [ -f "$SECRET_FILE" ]; then
    echo ""
    echo "WARN: Secret bestand bestaat al!"
    echo "Bestand: $SECRET_FILE"
    echo ""
    read -p "Overschrijven? Dit zal synchronisatie breken totdat op alle nodes bijgewerkt! (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Afgebroken."
        exit 0
    fi
    
    # Backup oude secret
    BACKUP_FILE="$SECRET_FILE.backup.$(date +%Y%m%d-%H%M%S)"
    cp "$SECRET_FILE" "$BACKUP_FILE"
    echo "Backup gemaakt: $BACKUP_FILE"
fi

# Genereer nieuw 64-character hex secret (256 bits)
echo ""
echo "Genereren nieuw secret..."
NEW_SECRET=$(openssl rand -hex 32)

# Schrijf naar bestand
echo -n "$NEW_SECRET" > "$SECRET_FILE"
chmod 600 "$SECRET_FILE"

echo "OK: Secret gegenereerd en opgeslagen in: $SECRET_FILE"
echo ""
echo "=== BELANGRIJK ==="
echo "Dit secret moet op ALLE nodes identiek zijn!"
echo ""
echo "Kopieer het secret naar andere nodes met:"
echo "  scp $SECRET_FILE root@<other-node>:$SECRET_FILE"
echo ""
echo "Of kopieer de inhoud handmatig:"
echo "---SECRET START---"
cat "$SECRET_FILE"
echo ""
echo "---SECRET END---"
echo ""
echo "Herstart daarna de synchronisatie service op alle nodes:"
echo "  systemctl restart whitelist-sync-v2.timer"
echo ""
