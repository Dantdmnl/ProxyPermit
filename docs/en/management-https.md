# HTTPS for the management interface

[Nederlands](../nl/beheer-https.md) · [Administration](administration.md)

!!! warning "Pre-release feature"

    This opt-in workflow is available for testing in **v1.4.0-dev.1** and is not
    in stable v1.3.0. Use a disposable or recoverable test deployment. HTTPS
    is not yet the fresh-install default.

Gunicorn and Granian can serve management HTTPS directly. A reverse proxy is
optional. Management certificates are separate from the internal HA certificates;
renewal does not change replication pins or restart Tinyproxy and Keepalived.

## Use the web interface

Open **Settings → Management HTTPS** using a **System administrator** account.
Proxy managers and monitors cannot start HTTPS jobs or download the CA through
the management API. Existing installations need one supported app upgrade to
install the restricted system helper.

- For an HTTP node, choose **Prepare HTTPS** and confirm your current password
  and a fresh authenticator code if 2FA is enabled. Preparation does not restart
  the interface. Initial HTTP setup is permitted only through a local SSH tunnel;
  a remote HTTP connection cannot perform these actions.
- Download the public node CA, verify its displayed fingerprint and trust it
  according to your organisation's policy. Confirm **I have verified and trusted
  this node CA**, choose **Activate HTTPS** and reauthenticate. The same address
  and port switch to HTTPS. The page offers the secure URL while the manager restarts.
  If the activation response is lost, open that secure URL before trying again;
  the interface does not automatically repeat the maintenance request.
- For a managed HTTPS node, choose **Renew certificate** and reauthenticate.
  The existing CA is retained. Daily automatic renewal remains enabled.

The panel identifies the target node and lists OS-approved management names.
The browser cannot select file paths, run root commands or issue certificates
for arbitrary domains. Additional DNS identities require OS approval. A stale
plan, changed configuration or revoked administrator role is refused. Passwords
and authenticator codes are never stored in maintenance job files.

Through the VIP, check the target node first. HTTPS management remains local to
that node; its certificates are not copied to the peer. A takeover can require
signing in again. Certificate replacement supplied by an external issuer still
uses that issuer's renewal process.

## OS fallback: enable HTTPS

Keep an SSH session open for recovery and check the node clock. Choose that
node's actual management DNS name and IP address:

```bash
cd /opt/proxypermit/current
sudo .venv/bin/python -m scripts.deploy.management_tls enable \
  --dns proxy.example.internal --ip 192.0.2.11
```

Without `--apply`, this prints a plan. Check the names and listener, then repeat
with `--apply`. For custom installations, also pass `--install-dir`. Repeat on
each node with its own addresses. The existing bind address, port and firewall
are preserved. Existing administrator-provided TLS is not overwritten.

The tool creates a unique node CA, whose key is root-only, and a 90-day server
certificate. The management service briefly restarts during activation and renewal.
Settings shows the expiry and fingerprint and warns in the final 30 days.

## Trust the node CA

Retrieve the **public** `/etc/proxypermit/management-tls/ca.pem` over trusted SSH.
Compare its SHA-256 fingerprint with the tool's output through that trusted
channel, then trust it in your browser/client according to organisational policy.
Never copy `ca-key.pem` or any `key.pem` to client devices. Each node has its own CA.

Use `https://` with the existing address and port. SSH tunnels still work; their
local port now carries HTTPS. `localhost`, `127.0.0.1` and `::1` are included for
local verification. A browser warning before explicitly trusting the CA is
expected: this CA is not publicly trusted.

## Renewal and recovery

The daily timer renews when 30 days or less remain, including an already expired
server certificate. The server key changes; the trusted CA stays the same.

```bash
sudo .venv/bin/python -m scripts.deploy.management_tls status
sudo systemctl list-timers proxypermit-management-tls-renew.timer
sudo systemctl start proxypermit-management-tls-renew.service
```

Use `renew --force --apply` for an early renewal test. The CA lasts ten years.
If it cannot cover a new server certificate's lifetime, renewal requires explicit
CA trust migration; it never silently replaces the trusted CA.

Activation validates the pair and the live HTTPS listener using CA validation
and the expected certificate fingerprint. Failed readiness restores the previous
management configuration. A durable checkpoint and boot guard recover interrupted
activation; a verified activation is completed forward. External environment
edits during a pending operation require OS review instead of being overwritten.

```bash
sudo .venv/bin/python -m scripts.deploy.management_tls recover --apply
sudo journalctl --namespace=proxypermit \
  -u proxypermit-management-tls-renew.service --since today
```

App upgrades retain TLS. App rollback preserves its current certificate binding
and rejects releases predating this workflow. Uninstallation removes matching
owned renewal/recovery units and retains certificates in `/etc/proxypermit`.

## HSTS and your own certificates

This private-CA workflow initially disables HSTS. After clients trust the CA and
HTTPS recovery is verified, the OS administrator can explicitly set
`ENABLE_HSTS=true` and a suitable `HSTS_MAX_AGE`, then restart the manager. HSTS
is emitted only over HTTPS and may prevent browser recovery to HTTP on that
hostname. Subdomain and preload options are not enabled by this workflow.

Own certificates still work through `MANAGER_CERT` and `MANAGER_KEY`; their
issuer's renewal procedure remains responsible for renewal. Granian needs a
PKCS#8 key. Guided import, CA replacement and ACME remain roadmap work.
