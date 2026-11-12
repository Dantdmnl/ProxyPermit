# Troubleshooting

[Nederlands](../nl/problemen-oplossen.md) · [Documentation](README.md)

Start with the symptom. Avoid repeating a write action if its response was lost:
the server may already have saved it. Reconnect and check the actual result first.

## The console does not open

1. Check the address supplied by your administrator. The console and proxy port
   are different services.
2. Check your VPN, management-network connection or SSH tunnel.
3. If the page was already open, wait for the connection notice to clear.
   Unsaved text stays in that tab; reloading can lose it.
4. If it stays unavailable, ask the host administrator to check the web service.
   Do not expose the console publicly as a workaround.

## Sign-in is blocked or a 2FA code is rejected

Wait until the displayed retry time. Switching language does not reset the
server's lockout. Check the username and keyboard layout before trying again.

For 2FA, use a current code and check that your authenticator device has the
correct time. If the authenticator is lost, contact the administrator responsible
for recovery. Never put a password, 2FA secret or setup QR code in a ticket.

## A website is blocked unexpectedly

Check these separately:

1. **Client access:** is this device's source network allowed to use the proxy?
2. **Filter mode:** is the list allowing matches or blocking matches?
3. **Destination rule:** is the correct domain present, including subdomains if needed?
4. **Activation:** did you publish the configuration or save with restart enabled?
5. **Client setup:** is the device using the intended proxy address and port?

An HTTP 403 means a request was denied; it does not by itself tell you which
rule caused it. Use **Live logs**, filter by Tinyproxy and a narrow date range,
and compare with a known allowed request. The [user guide](user-guide.md)
explains domain rules and mode changes.

## A saved change is not active

Open **Proxy configuration → Your draft**. A saved draft is deliberately separate
from the active configuration. Review the difference and use **Publish and activate**
when ready. If activation failed, read the error; do not assume the draft became active.

If someone else changed the file, reload the latest version and review your change
again. Do not overwrite a newer version just to dismiss a conflict message.

## I cannot edit rules on B

That is expected for a paired backup. Edit shared policy on A, even when B holds
the VIP. If A is unavailable, restore A; do not revoke pairing to bypass the
restriction. See [High availability](high-availability.md).

## The nodes show different revisions or the VIP does not move

A revision identifies a policy version. Allow time for B's next fetch, then check
the last successful contact and any error shown on each node. Confirm that the
change was actually published on A and the nodes are still paired.

For takeover problems, check which host actually has the VIP and test traffic
through that address. A stale or unavailable status is not proof of health.
The host administrator should inspect Keepalived and network/firewall state using
the [administrator guide](administration.md#troubleshoot-a-deployment).

## Older logs are missing

Narrow the date range and check the selected source. Defaults are 30 days for
audit/management and 7 days for Tinyproxy/traffic. Storage limits can remove
records sooner. Exports, old host logs and custom files have separate retention.
Read any incomplete-result notice before assuming there were no matching events.
See [Logging](logging.md).

## What to include in a support request

Include the installed version, node, approximate time and time zone, page/action,
exact error and whether real proxy traffic still works. Say what you expected
and what happened. Remove credentials, pairing codes, 2FA data and unnecessary
client addresses or destination details from attachments.
