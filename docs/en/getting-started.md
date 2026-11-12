# First steps

[Nederlands](../nl/eerste-stappen.md) · [Documentation](README.md)

**For:** someone who has a console address and a user account.
**Result:** you can sign in and identify your node. Proxy managers and system
administrators can also add a first rule.
If ProxyPermit is not installed yet, use [Installation](installation.md) first.

## 1. Get the right address

Ask your administrator for the **management console URL**, your username and your
initial password. Open that URL in your browser. The proxy address, often ending
in `:3128`, is for client traffic; it is not the management console.

Use the HTTPS address supplied by your organization, or the local SSH tunnel
provided by your administrator. Do not continue past unexpected certificate warnings.

## 2. Sign in

Choose **EN** or **NL** and enter your credentials. The eye button lets you check
your password. If two-factor authentication (2FA) is enabled for your account,
enter the current six-digit code from your authenticator app.

2FA is optional. Set it up later under **Settings → My account** if you want it.
Never send passwords, setup QR codes or pairing codes in a support ticket.

## 3. Check where you are

**Overview** shows the node and service status. A node is one server. In a paired setup, shared rule changes belong on
**node A**. **Node B**
receives those changes automatically and shows a read-only notice.

The node handling traffic can change during a failure. That does not make B the
node where you edit shared rules. See [High availability](high-availability.md).

## 4. Add one rule

This requires **Proxy manager** or **System administrator** rights. Monitors can
view the rules. See [Users and roles](roles.md).

Check the active mode under **Proxy configuration → Allowlist or blocklist**.
Monitors cannot open this page; ask a proxy manager if unsure. The mode determines
what a rule does:

- An allowlist permits matching destinations and blocks the rest.
- A blocklist blocks matching destinations and permits the rest.

A rule therefore has a different effect depending on the active mode.

1. On the standalone node or A, choose **New rule**.
2. Choose **Domain name** and enter a real test destination you control.
3. Add a description explaining why the rule is needed.
4. Wait for validation. Keep **Restart Tinyproxy after saving** selected if you
   intend to apply this change now, then choose **Save rule**. Wait for the save
   and restart results. A restart can interrupt active connections.
5. Test from a device that actually uses the proxy. Check one allowed and one
   blocked destination. Being able to open the console does not test proxy traffic.
6. For paired HA, open **Settings → High availability (HA) → Policy synchronization**
   and check that B has applied the revision published on A. Synchronization can
   take a little time.

A plain domain such as `example.com` matches that domain. `*.example.com` also
includes its subdomains. These are examples, not websites you must permit.

## 5. Know what saving means

| Action | What happens |
| --- | --- |
| Edit text in a form | Only your current browser view changes |
| Save a configuration draft | The server stores it, but Tinyproxy keeps using the active version |
| Publish and activate | The reviewed configuration is applied and Tinyproxy restarts |

Rule dialogs also have a restart choice. Saving a rule with restart turned off
does not activate the new policy. Follow the result shown by the interface.

Next: [Using the console](user-guide.md) for imports, changing filter modes,
logs and account settings. If a result is unexpected, start with
[Troubleshooting](troubleshooting.md) rather than repeating the change.
