#!/bin/bash
# Setup Let's Encrypt certificaat met DNS Challenge voor ProxyPermit
# Ondersteunt meerdere DNS providers (CloudFlare, Route53, Azure, etc.)

set -e

echo "============================================================"
echo "  Let's Encrypt Setup voor ProxyPermit"
echo "============================================================"
echo ""

# Check of root
if [ "$EUID" -ne 0 ]; then
   echo "ERROR: Dit script moet als root uitgevoerd worden."
   exit 1
fi

# Vraag naar domein
read -p "Domein voor certificaat (bijv. proxy.name.nl): " DOMAIN
if [ -z "$DOMAIN" ]; then
    echo "ERROR: Domein is verplicht."
    exit 1
fi

# Vraag naar DNS provider
echo ""
echo "Beschikbare DNS providers:"
echo "  1. CloudFlare"
echo "  2. AWS Route53"
echo "  3. Azure DNS"
echo "  4. Google Cloud DNS"
echo "  5. PowerDNS (manual met PowerDNS API ondersteund)"
echo "  6. Manual DNS (minder geautomatiseerd)"
echo ""
read -p "Kies DNS provider (1-6) [1]: " PROVIDER
PROVIDER=${PROVIDER:-1}

case $PROVIDER in
    1)
        DNS_PLUGIN="dns-cloudflare"
        PKG="python3-certbot-dns-cloudflare"
        CRED_FILE="/etc/letsencrypt/credentials/cloudflare.ini"
        echo ""
        echo "CloudFlare API Token nodig. Zie https://dash.cloudflare.com/profile/api-tokens"
        echo "Token moet minimaal 'Zone:Edit' permissions hebben."
        read -p "CloudFlare API Token: " CF_TOKEN
        ;;
    2)
        DNS_PLUGIN="dns-route53"
        PKG="python3-certbot-dns-route53"
        CRED_FILE="/etc/letsencrypt/credentials/route53.ini"
        echo ""
        echo "AWS credentials nodig (IAM user met Route53 permissions)"
        read -p "AWS Access Key ID: " AWS_KEY
        read -p "AWS Secret Access Key: " AWS_SECRET
        ;;
    3)
        DNS_PLUGIN="dns-azure"
        PKG="python3-certbot-dns-azure"
        CRED_FILE="/etc/letsencrypt/credentials/azure.ini"
        echo ""
        echo "Azure credentials nodig (Service Principal)"
        read -p "Azure Subscription ID: " AZURE_SUB
        read -p "Azure Resource Group: " AZURE_RG
        read -p "Azure Client ID: " AZURE_CID
        read -p "Azure Client Secret: " AZURE_SEC
        ;;
    4)
        DNS_PLUGIN="dns-google"
        PKG="python3-certbot-dns-google"
        CRED_FILE="/etc/letsencrypt/credentials/google.json"
        echo ""
        echo "Google Cloud credentials nodig (JSON key file)"
        read -p "Path naar Google Cloud JSON key file: " GCP_KEY
        ;;
    5)
        DNS_PLUGIN="manual"
        PKG=""
        echo ""
        echo "PowerDNS Setup"
        echo ""
        read -p "PowerDNS API URL (bijv. http://localhost:8001): " PDNS_URL
        read -p "PowerDNS API Key: " PDNS_KEY
        read -p "PowerDNS Server ID [localhost]: " PDNS_SERVER_ID
        PDNS_SERVER_ID=${PDNS_SERVER_ID:-localhost}
        echo ""
        echo "Geef de AUTHORITATIVE zone op in PowerDNS (niet de host), bijv: example.nl."
        read -p "PowerDNS Zone: " PDNS_ZONE
        if [ -z "$PDNS_ZONE" ]; then
            echo "ERROR: PowerDNS Zone is verplicht."
            exit 1
        fi
        case "$PDNS_ZONE" in
            *.) ;;
            *) PDNS_ZONE="${PDNS_ZONE}." ;;
        esac
        echo ""
        PDNS_AUTOMATION="0"
        read -p "Automatische DNS updates via PowerDNS API? (j/n) [j]: " PDNS_AUTO
        PDNS_AUTO=${PDNS_AUTO:-j}
        
        if [[ "$PDNS_AUTO" == "j" ]]; then
            PDNS_AUTOMATION="1"
            echo "✓ PowerDNS API automation zal geconfigureerd worden"
        else
            echo "✓ Manual mode: je voegt DNS records handmatig in PowerDNS toe"
        fi
        ;;
    6)
        DNS_PLUGIN="manual"
        PKG=""
        echo ""
        echo "Manual DNS mode: je moet DNS records handmatig aanmaken."
        ;;
    *)
        echo "ERROR: Ongeldig keuze."
        exit 1
        ;;
esac

# Installeer certbot en plugin
echo ""
echo "Installeer certbot en DNS plugin..."
apt-get update
apt-get install -y certbot $PKG

# Maak credentials directory
mkdir -p /etc/letsencrypt/credentials
chmod 700 /etc/letsencrypt/credentials

# Zet credentials
if [ "$PROVIDER" = "1" ]; then
    cat > "$CRED_FILE" << EOF
dns_cloudflare_api_token = $CF_TOKEN
EOF
elif [ "$PROVIDER" = "2" ]; then
    cat > "$CRED_FILE" << EOF
dns_route53_access_key_id = $AWS_KEY
dns_route53_access_key_secret = $AWS_SECRET
EOF
elif [ "$PROVIDER" = "3" ]; then
    cat > "$CRED_FILE" << EOF
dns_azure_subscription_id = $AZURE_SUB
dns_azure_resource_group = $AZURE_RG
dns_azure_client_id = $AZURE_CID
dns_azure_client_secret = $AZURE_SEC
EOF
elif [ "$PROVIDER" = "4" ]; then
    cp "$GCP_KEY" "$CRED_FILE"
fi

if [ "$PROVIDER" != "1" ] && [ "$PROVIDER" != "2" ] && [ "$PROVIDER" != "3" ] && [ "$PROVIDER" != "4" ]; then
    chmod 600 "$CRED_FILE" 2>/dev/null || true
fi


# Verzoek certificaat
echo ""
# Zoek juiste CERT_NAME map (ook met -0001, -0002 etc.)
CERT_NAME="$DOMAIN"
if [[ "$DOMAIN" == \*.* ]]; then
    CERT_NAME="${DOMAIN#*.}"
fi
# Check of map bestaat, anders zoek met suffix
if [ ! -d "/etc/letsencrypt/live/$CERT_NAME" ]; then
    ALT_PATH=$(ls -1d /etc/letsencrypt/live/${CERT_NAME}-* 2>/dev/null | head -n1)
    if [ -n "$ALT_PATH" ]; then
        ALT_CERT_NAME=$(basename "$ALT_PATH")
        echo "Let op: map /etc/letsencrypt/live/$CERT_NAME niet gevonden, gebruik $ALT_CERT_NAME."
        CERT_NAME="$ALT_CERT_NAME"
    fi
fi

if [ "$PROVIDER" = "5" ] && [ "$PDNS_AUTOMATION" = "1" ]; then
    echo "Maak PowerDNS API auth hook..."
    mkdir -p /etc/letsencrypt/renewal-hooks/auth
    mkdir -p /etc/letsencrypt/renewal-hooks/cleanup
    
    # Sla credentials op in beveiligde file
    cat > /etc/letsencrypt/renewal-hooks/powerdns-credentials.sh << CRED_EOF
#!/bin/bash
# PowerDNS API credentials (NIET in git opslaan!)
export PDNS_API_URL="${PDNS_URL}"
export PDNS_API_KEY="${PDNS_KEY}"
export PDNS_SERVER_ID="${PDNS_SERVER_ID}"
export PDNS_ZONE="${PDNS_ZONE}"
CRED_EOF
    chmod 600 /etc/letsencrypt/renewal-hooks/powerdns-credentials.sh
    
    echo "✓ PowerDNS hooks aangemaakt"
    echo ""
    
    # test PowerDNS API vooraf (with timeout)
    echo "Test PowerDNS API connectivity..."
    
    # Extract hostname from URL
    PDNS_HOST=$(echo "${PDNS_URL}" | sed 's|http://||;s|https://||;s|:.*||')
    PDNS_PORT=$(echo "${PDNS_URL}" | sed 's|.*:||')
    if [ "$PDNS_PORT" = "$PDNS_URL" ]; then PDNS_PORT="8081"; fi
    
    echo "  Hostname: $PDNS_HOST"
    echo "  Port: $PDNS_PORT"
    echo ""
    
    # Quick pre-check: DNS resolution with timeout
    echo "  Checking DNS resolution..."
    if ! timeout 5 nslookup "$PDNS_HOST" >/dev/null 2>&1; then
        echo "✗ DNS resolution failed for $PDNS_HOST"
        echo "  Fix: Check /etc/resolv.conf or DNS server"
        exit 1
    fi
    echo "  ✓ DNS OK"
    
    # Check port connectivity
    echo "  Checking port $PDNS_PORT..."
    if ! timeout 5 bash -c "echo > /dev/tcp/$PDNS_HOST/$PDNS_PORT" 2>/dev/null; then
        echo "✗ Cannot reach $PDNS_HOST:$PDNS_PORT"
        echo "  Fix: Check firewall, PowerDNS is running, correct port"
        exit 1
    fi
    echo "  ✓ Port reachable"
    echo ""
    
    # Now test API with curl + timeout
    echo "  Testing PowerDNS API..."
    API_TEST=$(timeout 10 curl -s -w "\n%{http_code}" -H "X-API-Key: ${PDNS_KEY}" "${PDNS_URL}/api/v1/servers/${PDNS_SERVER_ID}" 2>&1)
    CURL_EXIT=$?
    API_STATUS=$(echo "$API_TEST" | tail -n1)
    
    if [ $CURL_EXIT -eq 124 ]; then
        echo "✗ PowerDNS API timeout (10s)"
        echo "  Fix: Check network latency or PowerDNS API performance"
        exit 1
    elif [ $CURL_EXIT -ne 0 ]; then
        echo "✗ PowerDNS API error (curl error $CURL_EXIT)"
        echo "  Response: $(echo "$API_TEST" | head -n5)"
        exit 1
    fi
    
    if [ "$API_STATUS" = "200" ]; then
        echo "  ✓ PowerDNS API reachable (HTTP 200)"
    elif [ "$API_STATUS" = "401" ]; then
        echo "✗ PowerDNS API Key incorrect (HTTP 401)"
        echo "  Fix: Controleer je API Key in PowerDNS"
        exit 1
    elif [ "$API_STATUS" = "404" ]; then
        echo "✗ PowerDNS API endpoint not found (HTTP 404)"
        echo "  Fix: Controleer je API URL: ${PDNS_URL}/api/v1/servers"
        exit 1
    else
        echo "✗ PowerDNS API error (HTTP $API_STATUS)"
        echo "  Response: $(echo "$API_TEST" | head -n-1)"
        exit 1
    fi
    
    echo "PowerDNS config: server=${PDNS_SERVER_ID}, zone=${PDNS_ZONE}"
    echo ""
    echo "Aanvraag certificaat van Let's Encrypt met PowerDNS API automation..."
    echo ""
    
    cat > /etc/letsencrypt/renewal-hooks/auth/powerdns-auth.sh << 'PDNS_HOOK'
#!/bin/bash
# PowerDNS API authentication hook voor certbot
# Source credentials (includes PDNS_ZONE)
source /etc/letsencrypt/renewal-hooks/powerdns-credentials.sh

DOMAIN="${CERTBOT_DOMAIN}"
CHALLENGE="${CERTBOT_VALIDATION}"
ZONE="${PDNS_ZONE}"
SERVER_ID="${PDNS_SERVER_ID}"

echo "[PowerDNS Auth] Domain: $DOMAIN, Zone: $ZONE"

if [ -z "$DOMAIN" ] || [ -z "$CHALLENGE" ] || [ -z "$ZONE" ] || [ -z "$SERVER_ID" ]; then
    echo "[PowerDNS Auth] ERROR: Missing variables (domain=$DOMAIN, zone=$ZONE, server_id=$SERVER_ID)"
    exit 1
fi

RECORD_NAME="_acme-challenge.${DOMAIN}"
echo "[PowerDNS Auth] Adding TXT record: $RECORD_NAME"

# Create TXT record via PowerDNS API
RESPONSE=$(curl -s -w "\n%{http_code}" -X PATCH \
  -H "X-API-Key: ${PDNS_API_KEY}" \
  -H "Content-Type: application/json" \
    -d "{\"rrsets\":[{\"changetype\":\"REPLACE\",\"name\":\"${RECORD_NAME}.\",\"type\":\"TXT\",\"ttl\":60,\"records\":[{\"content\":\"\\\"${CHALLENGE}\\\"\",\"disabled\":false}]}]}" \
    "${PDNS_API_URL}/api/v1/servers/${SERVER_ID}/zones/${ZONE}")

HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | head -n-1)

echo "[PowerDNS Auth] HTTP: $HTTP_CODE"

if [ "$HTTP_CODE" -ge 400 ]; then
    echo "[PowerDNS Auth] ERROR: API failed"
    echo "[PowerDNS Auth] Zone: $ZONE"
    echo "[PowerDNS Auth] Response: $(echo "$BODY" | head -c 200)"
    exit 1
fi

echo "[PowerDNS Auth] ✓ Created"
sleep 30
exit 0
PDNS_HOOK
    chmod 755 /etc/letsencrypt/renewal-hooks/auth/powerdns-auth.sh
    
    # Create cleanup hook
    cat > /etc/letsencrypt/renewal-hooks/cleanup/powerdns-cleanup.sh << 'PDNS_CLEANUP'
#!/bin/bash
# PowerDNS cleanup hook
source /etc/letsencrypt/renewal-hooks/powerdns-credentials.sh

DOMAIN="${CERTBOT_DOMAIN}"
ZONE="${PDNS_ZONE}"
SERVER_ID="${PDNS_SERVER_ID}"

echo "[PowerDNS Cleanup] Removing TXT for $DOMAIN in zone $ZONE"

curl -s -X PATCH \
  -H "X-API-Key: ${PDNS_API_KEY}" \
  -H "Content-Type: application/json" \
    -d "{\"rrsets\":[{\"name\":\"_acme-challenge.${DOMAIN}.\",\"type\":\"TXT\",\"changetype\":\"DELETE\"}]}" \
    "${PDNS_API_URL}/api/v1/servers/${SERVER_ID}/zones/${ZONE}" > /dev/null 2>&1

echo "[PowerDNS Cleanup] ✓ Removed"
exit 0
PDNS_CLEANUP
    chmod 755 /etc/letsencrypt/renewal-hooks/cleanup/powerdns-cleanup.sh
    
    certbot certonly --manual \
        --preferred-challenges dns \
        --manual-auth-hook /etc/letsencrypt/renewal-hooks/auth/powerdns-auth.sh \
        --manual-cleanup-hook /etc/letsencrypt/renewal-hooks/cleanup/powerdns-cleanup.sh \
        -d "$DOMAIN"
        
elif [ "$PROVIDER" = "5" ]; then
    echo "PowerDNS Manual Mode"
    echo "Volg de prompts en voeg DNS records handmatig in PowerDNS toe."
    echo "PowerDNS API: $PDNS_URL"
    echo ""
    echo "Aanvraag certificaat van Let's Encrypt..."
    echo ""
    certbot certonly --manual --preferred-challenges dns -d "$DOMAIN"
else
    certbot certonly \
        --"$DNS_PLUGIN" \
        --"$DNS_PLUGIN"-credentials "$CRED_FILE" \
        --preferred-challenges dns \
        -d "$DOMAIN"
fi

# Verifieer certificaat
echo ""
echo "Verificatie van certificaat..."
CERT_PATH="/etc/letsencrypt/live/$CERT_NAME/fullchain.pem"
if [ -f "$CERT_PATH" ]; then
    echo "✓ Certificaat succesvol aangevraagd!"
    echo ""
    echo "Paden voor .env:"
    echo "  MANAGER_CERT=$CERT_PATH"
    echo "  MANAGER_KEY=/etc/letsencrypt/live/$CERT_NAME/privkey.pem"
    echo "  SYNC_VERIFY_CERT=true"
    echo ""
    # Check expiry
    EXPIRY=$(openssl x509 -enddate -noout -in "$CERT_PATH" | cut -d= -f 2)
    echo "Certificaat verloopt op: $EXPIRY"
else
    echo "✗ Certificaat niet gevonden. Check de output hierboven op errors."
    echo "  Gezocht pad: $CERT_PATH"
    # Ga niet exit 1, maar sla sync over
    SKIP_SYNC=1
fi

# Optioneel: sync naar secundaire node + remote reload
echo ""
read -p "Wil je certificaat naar secundaire node syncen via rsync/ssh? (j/n) [n]: " SYNC_SECONDARY
SYNC_SECONDARY=${SYNC_SECONDARY:-n}


if [[ "$SYNC_SECONDARY" == "j" ]]; then
    if [ "$SKIP_SYNC" = "1" ]; then
        echo "Sync wordt overgeslagen: certificaat niet gevonden."
    else
        read -p "Secundaire host/IP: " SECONDARY_HOST
        read -p "SSH user op secundaire node [certsync]: " SECONDARY_USER
        SECONDARY_USER=${SECONDARY_USER:-certsync}
        read -p "Pad naar SSH private key [/root/.ssh/id_ed25519_certsync]: " SECONDARY_SSH_KEY
        SECONDARY_SSH_KEY=${SECONDARY_SSH_KEY:-/root/.ssh/id_ed25519_certsync}

        if [ -z "$SECONDARY_HOST" ]; then
            echo "✗ Secundaire host is verplicht voor sync"
            exit 1
        fi

        if [ ! -f "$SECONDARY_SSH_KEY" ]; then
            echo "✗ SSH key niet gevonden: $SECONDARY_SSH_KEY"
            exit 1
        fi

        SSH_OPTS="-o IdentitiesOnly=yes -o BatchMode=yes -i $SECONDARY_SSH_KEY"

        if [ "$SECONDARY_USER" = "root" ]; then
            echo "⚠ Waarschuwing: root via SSH is niet aanbevolen in productie."
            read -p "Toch doorgaan met root? (j/n) [n]: " CONFIRM_ROOT_SSH
            CONFIRM_ROOT_SSH=${CONFIRM_ROOT_SSH:-n}
            if [ "$CONFIRM_ROOT_SSH" != "j" ]; then
                echo "Afgebroken. Gebruik bij voorkeur een dedicated sync-user met sudo rechten."
                exit 1
            fi
        fi

        echo "Controleer passwordless sudo op secundaire node..."
        if ! ssh $SSH_OPTS "${SECONDARY_USER}@${SECONDARY_HOST}" "sudo -n /usr/bin/rsync --version >/dev/null" 2>/dev/null; then
            echo "✗ Passwordless sudo niet beschikbaar voor ${SECONDARY_USER}@${SECONDARY_HOST}"
            echo "  Vereist voor unattended renewals. Voeg sudoers toe, bijv.:"
            echo "  ${SECONDARY_USER} ALL=(root) NOPASSWD:/usr/bin/rsync,/usr/bin/systemctl restart proxypermit"
            exit 1
        fi
        echo "✓ Passwordless sudo OK"

        echo ""
        echo "Sync certificaat naar ${SECONDARY_USER}@${SECONDARY_HOST}..."
        rsync -az --mkpath --delete -e "ssh $SSH_OPTS" --rsync-path="sudo rsync" "/etc/letsencrypt/live/$CERT_NAME/" "${SECONDARY_USER}@${SECONDARY_HOST}:/etc/letsencrypt/live/$CERT_NAME/"
        rsync -az --mkpath --delete -e "ssh $SSH_OPTS" --rsync-path="sudo rsync" "/etc/letsencrypt/archive/$CERT_NAME/" "${SECONDARY_USER}@${SECONDARY_HOST}:/etc/letsencrypt/archive/$CERT_NAME/"
        rsync -az --mkpath -e "ssh $SSH_OPTS" --rsync-path="sudo rsync" "/etc/letsencrypt/renewal/$CERT_NAME.conf" "${SECONDARY_USER}@${SECONDARY_HOST}:/etc/letsencrypt/renewal/$CERT_NAME.conf"

        echo "Restart proxypermit op secundaire node..."
        ssh $SSH_OPTS "${SECONDARY_USER}@${SECONDARY_HOST}" "sudo systemctl restart proxypermit >/dev/null 2>&1 || true; sudo systemctl reload nginx >/dev/null 2>&1 || true"
        echo "✓ Sync + remote restart voltooid"

        echo "Maak post-renewal hook (sync secundaire node)..."
        mkdir -p /etc/letsencrypt/renewal-hooks/post
        cat > /etc/letsencrypt/renewal-hooks/post/sync-secondary-node.sh << SYNC_HOOK
#!/bin/bash
set -e

CERT_NAME="$CERT_NAME"
SECONDARY_HOST="$SECONDARY_HOST"
SECONDARY_USER="$SECONDARY_USER"
SECONDARY_SSH_KEY="$SECONDARY_SSH_KEY"
SSH_OPTS="-o IdentitiesOnly=yes -o BatchMode=yes -i \$SECONDARY_SSH_KEY"

# Run only for the configured certificate lineage.
if [ "\${RENEWED_LINEAGE:-}" != "/etc/letsencrypt/live/\$CERT_NAME" ]; then
    exit 0
fi

rsync -az --mkpath --delete -e "ssh \$SSH_OPTS" --rsync-path="sudo rsync" "/etc/letsencrypt/live/\$CERT_NAME/" "\${SECONDARY_USER}@\${SECONDARY_HOST}:/etc/letsencrypt/live/\$CERT_NAME/"
rsync -az --mkpath --delete -e "ssh \$SSH_OPTS" --rsync-path="sudo rsync" "/etc/letsencrypt/archive/\$CERT_NAME/" "\${SECONDARY_USER}@\${SECONDARY_HOST}:/etc/letsencrypt/archive/\$CERT_NAME/"
rsync -az --mkpath -e "ssh \$SSH_OPTS" --rsync-path="sudo rsync" "/etc/letsencrypt/renewal/\$CERT_NAME.conf" "\${SECONDARY_USER}@\${SECONDARY_HOST}:/etc/letsencrypt/renewal/\$CERT_NAME.conf"
ssh \$SSH_OPTS "\${SECONDARY_USER}@\${SECONDARY_HOST}" "sudo systemctl restart proxypermit >/dev/null 2>&1 || true; sudo systemctl reload nginx >/dev/null 2>&1 || true"
SYNC_HOOK
        chmod 700 /etc/letsencrypt/renewal-hooks/post/sync-secondary-node.sh
        echo "✓ Post-renewal sync hook ingesteld"
    fi
fi

# Activeer auto-renewal
echo ""
echo "Activeer automatische renewal..."
systemctl enable certbot.timer 2>/dev/null || true
systemctl restart certbot.timer 2>/dev/null || true

# Post-renewal script
if systemctl is-active --quiet proxypermit; then
    echo ""
    echo "Maak post-renewal hook (restart proxypermit)..."
    mkdir -p /etc/letsencrypt/renewal-hooks/post
    cat > /etc/letsencrypt/renewal-hooks/post/restart-proxypermit.sh << 'HOOK_EOF'
#!/bin/bash
systemctl restart proxypermit
HOOK_EOF
    chmod 755 /etc/letsencrypt/renewal-hooks/post/restart-proxypermit.sh
    echo "✓ Post-renewal hook ingesteld"
fi

# Post-renewal script (reload nginx if present)
if systemctl is-active --quiet nginx || [ -f /etc/nginx/nginx.conf ] || command -v nginx >/dev/null 2>&1; then
    echo ""
    echo "Maak post-renewal hook (reload nginx)..."
    mkdir -p /etc/letsencrypt/renewal-hooks/post
    cat > /etc/letsencrypt/renewal-hooks/post/reload-nginx.sh << 'NGINX_HOOK'
#!/bin/bash
# Reload nginx if possible. Try systemctl, then service, then nginx binary.
set -e
if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-active --quiet nginx || systemctl list-unit-files | grep -q '^nginx.service'; then
        systemctl reload nginx || systemctl restart nginx || true
        exit 0
    fi
fi
if [ -x /etc/init.d/nginx ]; then
    service nginx reload || service nginx restart || true
    exit 0
fi
if command -v nginx >/dev/null 2>&1; then
    nginx -s reload >/dev/null 2>&1 || true
fi
exit 0
NGINX_HOOK
    chmod 755 /etc/letsencrypt/renewal-hooks/post/reload-nginx.sh
    echo "✓ Post-renewal nginx reload hook ingesteld"
fi

echo ""
echo "============================================================"
echo "  Let's Encrypt Setup Voltooid!"
echo "============================================================"
echo ""
echo "Volgende stappen:"
echo "  1. Update je .env file met de MANAGER_CERT en MANAGER_KEY paden (zie hierboven)"
echo "  2. Restart proxypermit: sudo systemctl restart proxypermit"
echo "  3. Test automatische renewal: sudo certbot renew --dry-run"
echo ""
echo "Handige commando's:"
echo "  - Check certificaatdetails: openssl x509 -text -noout -in $CERT_PATH"
echo "  - Test manual renewal: sudo certbot renew --force-renewal"
echo "  - Renewal logs: sudo journalctl -u certbot -f"
echo ""

# PowerDNS specifieke instructies
if [ "$PROVIDER" = "5" ] && [ "$PDNS_AUTOMATION" = "1" ]; then
    echo "============================================================"
    echo "  PowerDNS API Automation - Voltooid!"
    echo "============================================================"
    echo ""
    echo "Auth hooks zijn automatisch ingesteld:"
    echo "  - /etc/letsencrypt/renewal-hooks/auth/powerdns-auth.sh"
    echo "  - /etc/letsencrypt/renewal-hooks/cleanup/powerdns-cleanup.sh"
    echo "  - /etc/letsencrypt/renewal-hooks/powerdns-credentials.sh (geheim!)"
    echo ""
    echo "Debug tips als het mislukt:"
    echo "  1. Test PowerDNS API connectivity:"
    echo "     curl -v -H 'X-API-Key: <API_KEY>' ${PDNS_URL}/api/v1/servers"
    echo ""
    echo "  2. Check PowerDNS zone:"
    echo "     pdnsutil list-zone ${DOMAIN}"
    echo ""
    echo "  3. Handmatig TXT record toevoegen via API:"
    echo "     curl -X PATCH -H 'X-API-Key: <API_KEY>' -H 'Content-Type: application/json' \\"
    echo "       -d '{\"rrsets\":[{\"name\":\"_acme-challenge.${DOMAIN}.\",\"changetype\":\"REPLACE\",\"ttl\":120,\"records\":[{\"content\":\"\\\\\\\"test\\\\\\\"\",\"disabled\":false}]}]}' \\"
    echo "       ${PDNS_URL}/api/v1/servers/localhost/zones/${DOMAIN}"
    echo ""
    echo "  4. Controleer hook logs:"
    echo "     tail -f /var/log/letsencrypt/letsencrypt.log"
    echo ""
    echo "  5. Handmatig hook testen:"
    echo "     source /etc/letsencrypt/renewal-hooks/powerdns-credentials.sh"
    echo "     CERTBOT_DOMAIN=${DOMAIN} CERTBOT_VALIDATION='test123' bash /etc/letsencrypt/renewal-hooks/auth/powerdns-auth.sh"
    echo ""
elif [ "$PROVIDER" = "5" ]; then
    echo "============================================================"
    echo "  PowerDNS Manual Mode"
    echo "============================================================"
    echo ""
    echo "Voeg DNS records handmatig in PowerDNS in:"
    echo "  PowerDNS API: $PDNS_URL"
    echo "  Voer record in via PowerDNS web interface of API"
    echo ""
fi

echo ""
