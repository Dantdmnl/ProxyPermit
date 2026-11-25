# Verification report

[Nederlands](../nl/verification.md) · [Documentation](README.md)

## Protected HA acceptance checks for v1.4.0

Run these scenarios on a recoverable test deployment with direct management
access to both proxy hosts, independent access to the witness and a way to
restore fenced hosts. Record the version, platform, UTC start and end times,
time to restore client traffic, fencing result and VIP ownership as seen on both nodes.

| Scenario | Pass criteria | Current evidence |
| --- | --- | --- |
| Loss of the active host or its power feed | The surviving host fences the exact target when required, keeps quorum and serves the VIP. From a client, check an allowed destination and a blocked destination after takeover. | Abrupt loss of a VM verified on Proxmox. Independent physical host and power failure remain open. |
| Partition across cluster and witness links | No more than one VIP owner. With no quorum on either node, neither serves the VIP. If fencing is required and its management path is unavailable, verify that the other node waits. | VM partition and loss of quorum verified on Proxmox. A frozen owner with an unavailable fencer was tested on libvirt. |
| Standby returns after a long absence | Keep direct management access and check time, witness certificate and fencing authentication before making the node eligible. Compare active policy on A and B and verify B applies the latest revision. Include a run spanning credential expiry. | Standby rejoin, policy catch-up and rejection of expired fencing tokens and witness certificates were tested separately. Recovery and takeover after an absence spanning expiry remain open. |
| Retirement, removal and recovery | Verify the peer continues serving the VIP and direct SSH remains reachable. Compare firewall rules before and after. Interrupt retirement, resume it, and confirm the uninstaller only accepts completed retirement. | Local detachment, retirement and console removal verified on Proxmox. Interruption and resumption are covered by isolated regression tests. Removing a member from cluster configuration and replacing it remain open. |

Repeat the relevant scenarios for each platform and failure domain. A lab with
both nodes and a witness on one hypervisor can verify guest failures and
partitions, while a host-failure trial requires separate infrastructure.

## 25 November 2026

### Interrupted policy publication followed by a Tinyproxy restart

On the Ubuntu QEMU guest, killed the publication worker with `SIGKILL` in
two independent fixtures. When the worker stopped after replacing the
configuration and before replacing the filter, the disk retained a mixed
configuration and filter. When it stopped after replacing both files but
before activating the service, the running Tinyproxy kept returning HTTP 200
for the previously allowed destination. Restarting Tinyproxy from disk changed
that response to HTTP 403, applying the unconfirmed filter. The stored rollback
files restored the original policy in both fixtures when applied explicitly.
This first trial tested worker failure and exposed the missing automatic
recovery step before Tinyproxy starts.

### Recovery before Tinyproxy starts

Upgraded the standalone QEMU lab through the normal installer. Repeated
the worker interruption before filter replacement, after it, and after the
worker had requested and completed a service restart. In each case a
subsequent systemd restart restored the original configuration and filter
before launching the new Tinyproxy process. Both files matched the baseline
and HTTP 200 and 403 were restored for the allowed and blocked destinations.

Also cut power to the disposable QEMU guest after the unconfirmed pair had
been written. After boot, Tinyproxy again returned HTTP 200 and 403 for the
same destinations. Both policy files matched hashes recorded on the host
before the test. The boot journal showed recovery before Tinyproxy started.

### Protected Proxmox lab inspection

Node A had no quorum and did not hold the VIP. The witness accepted connections
and supplied a vote, while Corosync still reported `WaitForAll` and blocked
cluster activity. The newest retained VIP monitor timeout coincided with
Pacemaker IPC timeouts. VM 8107 still refused SSH, so replacement enrollment
and its failover could not be tested. Cluster policy and fencing were kept intact.

### Disk-full error during policy publication

Ran an isolated Tinyproxy process on the Ubuntu QEMU guest with its policy
files on a 160 KiB tmpfs. After the configuration replacement, filled the
filesystem before publishing the filter. The actual filter write failed
with `ENOSPC`. ProxyPermit restored both original files from reserved
rollback copies, kept the same Tinyproxy process running and still returned
HTTP 200 for the allowed destination and HTTP 403 for the blocked one.
The browser API regression also checked that the filter draft remained
available and could be published once space was available.

### Recovery from an expired management certificate

On the standalone QEMU candidate, temporarily served an expired leaf
certificate signed by the existing node CA. A client that trusted that CA
rejected the leaf as expired. The regular renewal command detected that
renewal was due, issued a new leaf and restored the HTTPS liveness endpoint.
The same client accepted the new certificate with its existing CA trust.

### Reboot with UFW enabled and management HTTPS

On the same candidate, verified that the stored firewall rule covered the
actual SSH client address before enabling UFW with a default inbound deny
policy. After a normal guest reboot, a new SSH session succeeded with UFW
active. The management endpoint answered over HTTPS with the same CA, the
renewal timer was active, and the Tinyproxy requests still returned HTTP 200
and 403 for the allowed and denied destinations respectively.

## 24 November 2026 at 17:39–17:53 UTC

### Standalone release archive on Ubuntu 24.04.5

Used two clean QEMU guests. Verified the checksum of the published v1.3.0
archive and the checksum and file manifest of a locally built
`1.4.0-dev.2` candidate. Candidate SHA-256:

```text
e18430bba5ca7d5455ad3be8eff441519944aacabab057c0c23357a1db709cf1
```

- Both clean installations passed sign-in and two Tinyproxy requests. The
  allowed origin returned HTTP 200 and the blocked destination returned HTTP 403.
- Upgraded v1.3.0, rolled back to v1.3.0 and upgraded again. After each step,
  sign-in and both proxy requests passed. The original filter checksum matched.
- Uninstalled the candidate. The console stopped, Tinyproxy and a new SSH
  session remained available, and the filter checksum and recorded UFW
  management rules matched the baseline.
- On the other guest, moved the HTTP console to HTTPS on the same port.
  Sign-in worked with the node CA and a secure session cookie. Forced renewal
  changed the server certificate while preserving the CA.

The candidate installer ran all 394 Python tests as root with no skips.
Repeat these checks against the final stable release archive.

## 24 November 2026 at 17:08–17:16 UTC

### Replacement power-off and VIP monitor recovery on Proxmox

The detached original B shut down normally. The clean replacement booted on
B's address and accepted SSH with its pinned key. It had no cluster services
or VIP. The test token powered it off through the Proxmox API. The separate
lab account started it again, and Proxmox reported it running. The guest
answered on the expected MAC address, but its SSH port refused connections
and the guest-agent network query failed. Guest recovery is still open.

A retained quorum with the witness. During the trial, the VIP monitor
returned an error without an exit reason and Pacemaker stopped the VIP.
After the local proxy health probe and old peer's fenced status passed,
clearing only A's five-second VIP monitor failure restored service. A proxy
request from the witness then received the expected HTTP 403. The cause of
the monitor error remains open. The updated agent correctly formatted an
isolated probe error on A. The active Pacemaker configuration and installed
agent were left in place. Cluster enrollment and failover on the replacement
remain unverified.

## 24 November 2026 at 17:04–17:06 UTC

### Preflight for replacing node B on Proxmox

A and the witness had quorum with two of three votes. A still held the VIP.
A clean replacement VM was provisioned and left powered off because it has
the same network settings as B. The detached original was retained for
recovery. Changing only B's VM ID in an offline plan changed the generated
Corosync cluster name. Since that name also identifies witness enrollment,
the initial setup procedure cannot be reused for this change on a running
protected cluster. For each new fencing token, the Proxmox API returned the
peer's power state and denied status requests for its own VM, the old B and
the witness. Membership, witness enrollment and live fencing were left in
place. Power-off fencing, replacement and failover still require verification.

## 24 November 2026 at 16:46–16:51 UTC

### VIP recovery and detached-node removal on Proxmox

Both nodes were online with three votes, while retained monitor failures had
left the VIP stopped. Each Tinyproxy probe and both peer-specific fencing
status checks passed. Clearing only A's failed 5-second VIP monitor let
Pacemaker restore the VIP on A. A request from the witness then received the
expected Tinyproxy denial. The cause of the original timeouts remains open.

Around the VIP timeout on B, Pacemaker reported that its scheduler,
controller, executor and other local processes stopped responding to IPC.
On A, the scheduler missed an IPC check before its VIP timeout, and the
controller missed one around the fencing monitor timeout on 24 November.
The timeouts therefore coincided with wider process delays. The logs do not
identify what caused those delays.

B was placed in standby and its cluster services were stopped and persistently
masked. The installer refused removal before local retirement completed. The
retirement removed seven recorded HA rules and kept the local console and
pinned SSH and management rules. Repeating retirement made no changes. The
subsequent uninstall left policy, service masks and the OS fencing agent in
place. A had quorum with the witness and served the VIP at each checkpoint,
and a fresh SSH connection to B succeeded after uninstall. Cluster membership
was preserved for recovery.

## 24 November 2026 at 16:28–16:35 UTC

### Upgrade and rollback on a Keepalived pair with HTTPS

On two Ubuntu 24.04 VMware guests, upgraded B, restored its previous release
from the installer backup, then upgraded B again before upgrading A. A used
Granian with the local CA. B used Gunicorn with an administrator-provided PEM
certificate. The first upgrade attempt exposed a test fixture that read the
running host's systemd unit. The installer kept the earlier release active.
After isolating that fixture, the installer passed all 388 tests on the guests.

Both consoles answered over HTTPS after the upgrade, the certificates or trust
root and the account, filter and firewall files were preserved, and B
acknowledged A's full published revision. A held the VIP before and after the
trial. This trial covered ordinary Keepalived HA.

## 23 November 2026 at 12:38–16:44 UTC

### VIP moved from B to A after a monitor timeout

| Time (UTC) | Pacemaker event |
| --- | --- |
| 12:38:35 | B’s VIP monitor exceeded its 10-second timeout. Pacemaker recorded a failure count of `1`, excluded B and scheduled recovery on A. |
| 12:38:42 | Pacemaker confirmed the successful VIP start on A after stopping the resource on B. |
| 12:53:44–12:53:46 | The monitor on A also timed out. With B still excluded, Pacemaker stopped the VIP on A. |
| 16:44 | Both hosts were online. Pacemaker still showed the VIP stopped and both failure counts were retained. |

The installed lab policy preferred A with a score of `100`, retained the
current owner with stickiness `1000`, and used a migration threshold of `1`.
The move to A was recovery from B’s monitor failure. The reason the monitor
process did not finish within its timeout remains open.

## 23 November 2026 at 12:15–12:18 UTC

### Recovery commands and a stopped Proxmox VIP

The candidate's `cluster_maintenance --status` ran on both assigned Proxmox
nodes and reported no reservation. The detached retirement and uninstall
guards refused the still-attached standby. The lab was running an older
development build and the proxy VIP was already stopped.

Pacemaker showed historical VIP monitor errors for both nodes. Both loopback
Tinyproxy health probes passed, quorum and fencing were confirmed, and no
maintenance lease existed. The operator cleared the failure counters for the
VIP resource only. Pacemaker returned the VIP to service. At 12:17:58 UTC,
both nodes reported one owner, B, and reached the proxy port. The reason for
the older monitor errors remains open. This trial did not detach or uninstall
either live node.

## 23 November 2026 at 11:56–12:04 UTC

### Proxmox fencing credential expiry

The actual fencing agent on the standby VM reported its peer as online with
the existing credential. With a past expiry in a private mount namespace, the
same read-only status action failed. The live profile stayed byte-for-byte
unchanged and the peer continued serving the VIP. Automated checks also
verify that the expired local credential prevents a healthy claim from the
management interface even when the shared fencer appears ready. With live
Corosync and Pacemaker evidence, the new status code reported `protected`
with the real profile, `degraded` with the expired copy, and `protected`
again with the real profile. In all three checks the VIP had one owner.

On the same standby, OpenSSL accepted the enrolled public witness-client
chain at the current time and rejected it with a verification time after the
client certificate expired. The proxy at the VIP remained reachable. These
two checks isolate expiry without changing a live token, certificate or clock.
They provide no timing measurement for recovery after an actual offline period.

## 23 November 2026 at 11:28–11:35 UTC

### KVM/libvirt fencing and return

In the disposable OS-managed libvirt lab, A initially served the VIP
through the KVM guests. After A was suspended at 11:29:17 UTC, the host confirmed
it had been fenced before B took the VIP. The TCP proxy listener on B was
reachable 19.1 seconds after suspension. A then restarted and remained without
the VIP. Both nodes reported a single owner and an available proxy listener.

After rejoining, A's qdevice snapshot still showed two expected votes even
though votequorum showed three and mutual TLS with the witness was active. The
updated status code confirmed protection on both live nodes using the current
votequorum report. Regression tests cover missing quorum and missing client TLS.

At 11:34:36 UTC, the lab fencer was stopped and the new VIP owner B was
suspended. A did not serve the VIP during 24 seconds of observation. When the
fencer returned, B was seen powered off and A served the proxy port within
seven seconds. After B returned, both nodes reported one owner and a reachable
proxy. The witness and VMs shared one hypervisor throughout these tests.

## 23 November 2026 at 11:12–11:17 UTC

### Returning a missing Proxmox node

On the existing protected `v1.4.0-dev.1` lab, B was online and connected to
the witness while A was powered off. B reported no quorum and no VIP owner.
After starting A at 11:12:21 UTC, quorum and the two healthy Pacemaker resources
were observed by 11:16:37. A held the only VIP at the 11:17 check. Both nodes
reported a ready fencer, reached the proxy TCP listener at the VIP, and had the
same filter-file hash. The observation samples bound the recovery time. The
offline duration and behavior across expiry were outside this check.

## 22 November 2026

### Live checks for protected HA preflight

On the Proxmox test pair, the preflight checked both local NSS certificate
stores against the witness CA. Both nodes returned the same client
fingerprint and rejected an incorrect CA fingerprint. A separate live
fencing test used the peer-only agent to turn off the standby VM. The new
preflight recorded authenticated OFF and ON observations after the VM was
restored independently. The surviving node kept the virtual IP.

Regression tests cover missing or differing transport proofs before
reservation, changed or expired fencing evidence, and exclusion of
preflight files from release archives.

### Network split with no quorum

The lab blocked only the witness and Corosync peer traffic while keeping
SSH open. Once both nodes lost quorum, neither held the virtual IP. After
removing the temporary rules, both nodes returned to protected status
with one VIP owner and unchanged configuration, filter rules and accounts.
This test used the existing protected Proxmox cluster and its shared host.

### Preparing protected HA from Settings

The browser tests review the plan and firewall preview, confirm local
staging, prepare packages, and reload the saved fields. The isolated Linux
worker writes and reads back a root-only plan. Tests cover expired or
changed plans, the node identity, roles, CSRF, reauthentication and duplicate
requests. The systemd unit and its single allowed sudo command were checked
on Linux. This verification used an isolated worker and browser, with
package installation simulated.

### Migrating an existing HA pair on Proxmox

A separate lab with two proxy nodes and a witness VM migrated from Keepalived
to protected HA with Pacemaker. The lab started with a paired HA deployment
whose active filter rules had already synchronized. Each node powered off its
peer through Proxmox. After each test, the stopped VM was restarted through
independent lab access.

The migration on B was interrupted just before Keepalived would stop and
resumed with the same migration ID. A kept the VIP until B was ready for the
handoff. A refused to proceed when its clock was still unsynchronized after
a reboot. Once its clock was synchronized, A also rejected an expired
confirmation from B. With a fresh confirmation, both nodes completed the
migration. Both reported quorum, a connected witness and fencing readiness.
Exactly one node held the VIP. Both nodes could reach the proxy port at the
VIP. The filter rules, Tinyproxy configuration and local accounts remained
unchanged on each node.

When the witness service was stopped temporarily, both nodes retained quorum
and the VIP stayed on the same node. After restarting the service, both nodes
reported full protection again. All three VMs ran on one Proxmox host.
Complete host loss, network partitions and a node returning after a long
absence were outside the scope of this test.

### Active-node power loss after migration

In the protected lab, the peer's fencing agent powered off the active Proxmox
guest A. The independent Proxmox API confirmed that A was off. B took over
with quorum and accepted a TCP connection to the proxy port on the VIP. The
test observed takeover within 17 seconds of issuing the power-off command.
After A restarted, both nodes reported protection and exactly one VIP owner.
The local accounts, Tinyproxy configuration and filter rules were unchanged.

The updated resource agent was also run in read-only monitor mode on both
live guests. It recognized A's healthy VIP and B's inactive service. The
power-loss test used the previously installed cluster agent.

### Network partition and protected release rollback

The Proxmox lab cut only the active node's Corosync traffic to its peer and
its TLS witness connection. Direct SSH and access to the Proxmox API remained
available. Pacemaker fenced the isolated node. The independent Proxmox API
confirmed it was powered off, and the other node held the VIP with quorum and
accepted a TCP connection on the proxy port. Both nodes returned to protected
status after recovery. Configuration, filters and local accounts were unchanged.

In the same lab, B was upgraded under a cluster reservation, then rolled back
to the application source associated with the current upgrade receipt. A
retained the VIP. Both nodes reported protection and the hashes of the
Tinyproxy configuration, filter rules and local accounts were unchanged.

### Standby return and policy reconciliation

B rejoined the protected lab after more than five minutes offline. In a
separate trial, B was stopped again for longer than the status freshness
window. A kept the VIP and published a new revision with a temporary filter
comment. When B returned, it applied that revision without taking the VIP.
A then published the original policy again. Both nodes returned to their
original Tinyproxy configuration, filter rules and account data.

These trials used the same Proxmox host. A longer absence spanning
certificate or fencing-token expiration, total host loss and live fencing
with other platforms still require verification.

### Protected HA planning and recovery of the original lab

The current development build was tested locally. The Settings planner used
the configured A and B addresses, downloaded JSON in the format accepted by
the OS validator and rejected a witness configured on the VIP. Browser tests
covered both languages, themes and viewport sizes. Permission tests confirmed
that only a system administrator with a valid CSRF token can validate a plan.

In the existing Proxmox lab, Pacemaker had stopped the VIP after monitor
timeouts left a failure count on each node. Both nodes reported quorum, a
connected witness, fencing readiness and no VIP owner. After checking local
Tinyproxy health and confirming there was no maintenance reservation, the OS
operator cleared the recorded failures for the VIP resource. Pacemaker restored
the VIP on A. Both nodes then reported protection and exactly one owner.
The standalone lab VM could reach Tinyproxy at the VIP. The original cause
of the monitor timeouts remains unconfirmed. Later checks found new monitor
failures on both nodes and the original VIP was stopped again. The migration
and power-loss tests were run on the separate lab.

## 18 November 2026 — management access during a VIP handoff

In the development lab, rolling maintenance moved the shared management address
from A to B. HTTPS validated against the lab CA bundle throughout the successful
requests. B rejected requests from a stale A page and rejected A's session cookie.
Signing in with B's local administrator account restored access at the same URL.
The reverse handoff was also tested in a Dutch mobile browser session. An
open form retained its draft on the old page until the operator reopened the
console. The browser then showed A and completed sign-in with a temporary
local account and 2FA. The fixture account was removed after the test.

The development work below is included in **v1.4.0-dev.1**, a prerelease for
testing. Historical entries describe the state at the time of each test.
The final release-candidate checks passed 342 Python tests on Linux, Windows
tests with expected platform skips, both settings/HA browser suites, workflow
lint, installer plans and the strict documentation build. See the
[release notes](https://github.com/Dantdmnl/ProxyPermit/releases/tag/v1.4.0-dev.1)
for scope and known limitations; v1.3.0 remains the stable release.

## 17 November 2026 — management security and cluster preparation

### Protected-cluster preparation and Proxmox fencing

The next build adds root-exported, expiring local prerequisite checks and
secret-free staged node/witness plans. Browser tests cover NL/EN, both themes,
mobile/desktop, evidence expiry and administrator-only access. These checks
do not configure Pacemaker, a witness or quorum, and never claim that a copied
plan proves working fencing. The full root Linux suite passes 324 tests.

On the Proxmox lab, a host-root helper created two privilege-separated tokens
and verified their effective permissions: each token can manage only its peer
VM, with no host administration or access to other guests. Tokens were placed
only in node-local root-only files after explicit approval. The separately named,
hash-pinned ClusterLabs agent verified the Proxmox CA and certificate hostname.
It refused requests for other targets and unverified TLS in isolated tests.
Live checks also rejected a wrong CA and wrong target, restored the correct CA
and verified that failure output contained no token.

A's real agent powered B off, and B's agent then powered A off in a separate
test. The hypervisor confirmed each stopped VM, the surviving node owned the
VIP, and independent lab API recovery restored all application services. Native
filter/configuration/account hashes stayed unchanged; the web account could
not read either token. These are agent off/recovery tests, not a completed
protected-cluster partition matrix. Guided quorum/witness activation remains open. The power-test
prerequisite is not automatically signed off by these external lab scripts.

The experimental backport preserves the distribution agent and includes its
GPL source/license and exact upstream revision. Lab tokens expire after 14 days;
production credential renewal and cluster lifecycle work remain open.

### Native quorum activation in the disposable Proxmox lab

All four lab guests now use a responding NTP source through the existing OS
time service. The source was checked against independently verified HTTPS Date
responses before configuration; the guest OS reports synchronized network time.
The Proxmox host and gateway time configuration were not changed.

Root-only tooling prepared packages with cluster daemons initially masked and
disabled. The existing witness CA signed the cluster client certificate; private
bundles and a fresh 1024-byte Corosync key were transferred only through trusted
SSH into root-only storage. Qnetd requires TLS and a client certificate, listens
on the witness address and accepts only the two nodes through its firewall.
The witness retains its private management journal for at most 30 days/100 MiB.
No remote PCSD administration service or cluster password was enabled.

The standby was drained before the current owner. Durable reservations prevent
independent Keepalived startup while setup is incomplete. Both nodes then
confirmed encrypted Corosync transport, two members, three expected votes and
an authenticated witness connection. Pacemaker received only a fresh, empty
configuration, with fencing required and the VIP initially stopped. Activation
was a separate, version-checked step after both nodes confirmed safe policy and
ready fencing. Both subsequently reported protected status with exclusive VIP
ownership; independent Keepalived boot activation remains disabled.

A suspended active VM was powered off by its peer's native fencing agent.
Independent hypervisor checks confirmed it was stopped before the peer acquired
the healthy VIP. Independent recovery restored the VM, both nodes returned to
protected status and native filter/configuration/account hashes stayed unchanged.
Stopping the witness with both members connected retained quorum and exclusive
VIP ownership while protection status became degraded; restarting it restored
protected status.

An additional run verified the unavailable-fencing case. A temporary guest-only
deny rule was first proven to block a fresh connection to the fencing API. After
the active VM was suspended, its peer withheld VIP takeover for 45 seconds.
Removing only the test rule allowed fencing to complete; independent hypervisor
confirmation again preceded observed VIP ownership. Both nodes recovered with
one owner and unchanged native policy/accounts. An earlier attempt that did not
actually install its conflicting test rule was rejected as invalid evidence.

These are experimental, operator-coordinated lab results. They do not complete
GUI provisioning, all interrupted-setup/recovery paths or the Proxmox partition
matrix. All lab guests share one physical host; an independent production
witness and fencing failure domain are still necessary.

The final regression run passed 340 Python tests without skips, lint and
installer type checks. Frontend checks ran on Windows because the WSL image has
no Node executable; Linux version/shell checks passed separately. Settings
Playwright and the strict NL/EN documentation build passed. Supported sequential
cluster-maintenance upgrades then passed on A and B, alongside a normal upgrade
on the standalone node. Live browser checks confirmed CA-verified HTTPS,
synchronized policy and fresh protected status on both HA nodes. No test firewall
rule, maintenance checkpoint or fencing token in CIB/audit records remained.

The final time check also caught an unreachable member of the lab's DNS time
pool after reboot. The guest-only drop-in now tries a freshly verified responding
endpoint first, with fallback entries; all four guests again reported synchronized
time. This is lab configuration, not a hardcoded time server in the installer.

### Management HTTPS and browser reliability

The standalone Proxmox node passed the complete browser workflow: prepare a
certificate, download the public CA, acknowledge trust, activate HTTPS and follow
the secure address. The resulting cookie was Secure/HttpOnly. Each of the three
application nodes also renewed its certificate through Settings with fresh admin
authentication. CA verification passed independently, the CA stayed unchanged,
and audit records identified the requesting administrator. Proxy process IDs,
native configuration, filters and accounts stayed unchanged during TLS jobs.

Real testing exposed a stop-timeout mismatch. The managed web service now uses
mixed systemd termination and a bounded Gunicorn grace period. Direct Gunicorn
TLS uses threaded workers. Both supported servers passed parallel, CA-verified
asset requests while six idle TCP preconnections remained open, plus the existing
HTTP/HTTPS account, optional 2FA, audit and configuration checks.

Browser regressions deliberately blocked `rules.js`: both languages showed a
reload action without a `RuleView` exception, and recovered after reloading.
HTTPS dialogs passed both languages/themes, mobile/desktop, focus, dirty-input
confirmation and role restrictions. A lost activation reply offered the secure
address without repeating the request. Log regressions preserve raw history and
redaction while grouping repeated client trust alerts and retaining other errors.
The Linux suite passes 324 tests without skips; lint, installer types, frontend
checks and the strict documentation build pass. These changes remain unreleased.

## 16 November 2026 — fresh Proxmox lab

Four disposable Ubuntu 24.04 VMs were created in a dedicated Proxmox pool:
a standalone node, two HA nodes and a prepared witness host. The supported
installer installed v1.3.0 on the three application nodes. Existing guests,
host networking and the upstream firewall were left unchanged. Management
interfaces remained bound to guest loopback and were accessed through SSH tunnels.

The browser workflow converted A from standalone to managed HA and joined B
with a one-time invitation, after reviewing the network plan and confirming the
administrator password. Guest UFW rules were applied. Separate administrator
passwords remained valid. Filter additions and removals, and a published shared
Tinyproxy setting, replicated automatically from A to B. Direct rule changes on B
were refused. All temporary policy changes were reverted.

Stopping Tinyproxy on A moved the VIP to B, which answered a real proxy request.
Starting Tinyproxy again restored the preferred owner A; exactly one node owned
the VIP and both nodes' services were healthy afterward. This checks service
failure, not network partitions or physical-host failure. The witness is prepared
only: quorum and fencing are **not configured** in this lab.

Fresh GUI conversion exposed a v1.3.0 permissions bug: the root helper's private
umask made a public Keepalived systemd directory inaccessible to the application,
causing the settings page to return HTTP 500. A local, unreleased correction keeps
new public metadata directories traversable and narrowly repairs the exact
installer-owned affected directory. Unreadable cluster reservations now block
independent service control without breaking the settings page. The supported
upgrade applied this correction to all three application nodes before the tests
above completed. The published v1.3.0 tag was not changed.

Linux checks passed all 286 Python tests without skips, plus lint and installer
type checks. The settings and HA Playwright suites passed both languages, both
themes, keyboard interaction and small viewports.

### Pairing lifecycle and protection evidence

The next local build requires confirmed revocation and administrator
reauthentication on either node, including fresh optional TOTP. Isolated tests
check missing CSRF/confirmation, wrong passwords and TOTP replay. Playwright
checks both languages and themes, mobile/desktop, focus, retaining input after
failure, discard confirmation, successful revocation and redacted actor audit.

On the Proxmox pair, revocation rejected the previous peer credential. Both
nodes were re-paired successfully. Expired, superseded and replayed invitations
were refused; two simultaneous join requests produced exactly one successful
join. Native filter/configuration bytes and separate local credentials remained
unchanged, and automatic synchronization resumed. The live expiry test uses an
unambiguous past timestamp; isolated tests also advance the issuer clock beyond
the original invitation lifetime.

Protection-status regressions reject duplicate targets across separate fencing
devices, mismatched running agent types, disabled or rule-driven devices and
Proxmox mappings without verified TLS or explicit numeric guest IDs. These are
status-evidence checks, not proof that Proxmox power fencing has been exercised.
The Linux Python suite now passes 290 tests without skips.

Ubuntu 24.04's packaged `fence_pve` in this lab does not expose API-token
authentication. The lab-pool automation token was not copied to the proxy nodes.
Proxmox fencing, witness activation and protected-cluster provisioning remain open.

Time-source checks found no external NTP replies and an answering gateway clock
with a similar offset. The four guest clocks received a one-time, forward-only
correction from the trusted workstation after comparison with a verified HTTPS
Date response. The Proxmox host and NTP configurations were not changed. This is
a lab correction, not continuous time synchronization; a reliable time source
remains an operating prerequisite, especially for 2FA and certificate transitions.

### Management HTTPS

The next local build serves management HTTPS directly, without a reverse proxy,
on the standalone node and both Proxmox HA nodes. Each node has a different CA;
the application account cannot read its CA key. Enabling and renewing TLS left
the Tinyproxy PID unchanged. A subsequent supported app upgrade preserved the
standalone HTTPS listener and certificate binding.

The standalone fault tests forced early renewal, served an intentionally expired
leaf and confirmed client rejection, then restored valid HTTPS through the normal
renewal service. The CA stayed unchanged. A prepared interruption checkpoint was
recovered by the real systemd boot guard. Injected readiness failure restored the
previous valid HTTPS configuration. Native proxy files and accounts stayed unchanged.
These were controlled certificate/service fault injections, not a physical power-cut test.

Browser checks on all three nodes verified CA trust independently, HTTPS login,
Secure/HttpOnly/SameSite session cookies, NL/EN, both themes and a mobile viewport.
A temporary rule published through HTTPS replicated to B and was removed; the
original filter revision and healthy synchronization were restored. Browser test
trust was limited to the expected certificate public keys, without altering the
workstation trust store. The public node CA files were exported over trusted SSH.

The Linux suite passes 302 Python tests, lint, installer types and shell checks.
Both two-worker WSGI backends passed real HTTP and CA-verified HTTPS account,
2FA, audit and configuration tests, including HSTS and secure-cookie checks.
The strict documentation build and localized responsive browser regressions pass.
Installer self-tests now isolate managed TLS discovery from the host's real profile.

HTTPS remains opt-in; default installation, guided CA import/replacement and ACME
are still open. HSTS is not enabled automatically for the private node CA.

## 6 November 2026

Ubuntu 26.04.1 LTS on WSL 2 ran systemd, Python 3.14.4, and Tinyproxy 1.11.3.
The tests covered Gunicorn 26.2.0 and Granian 2.8.7 with two workers, real HTTP
requests, sign-in, configuration writes, CSRF, revision conflicts, and assets.
The installer deployed Granian, upgraded to Gunicorn while preserving settings,
and rolled back to the previous release. Tinyproxy returned HTTP 403 for a
blocked destination and HTTP 200 for an allowed destination. File writes and
restarts worked under the service account.

A listener configured only in the systemd unit was lost on upgrade. The
installer now migrates it into the environment and probes the configured HTTP
endpoint, Unix socket, or local TLS endpoint. The TLS probe checks reachability.

The initial run passed 44 Python tests. After reorganizing the repository,
58 Python tests and JavaScript locale and rendering checks passed. Both HTTP
servers were retested. The workflow passed local actionlint validation, and
pip-audit reported no known vulnerabilities in the production dependencies at
the time. The hosted workflow was still pending.

## 8 November 2026

- The installer upgraded the standalone WSL installation at
  `/opt/proxypermit-integration`. Windows could open the console on loopback
  port 8098. The unauthenticated configuration API returned 401. Real proxy
  traffic returned 403 before adding a test rule and 200 afterward. The
  original rules were restored.
- A two-node lab with hand-configured network namespaces verified console
  access and proxy traffic on both nodes. After Tinyproxy stopped on node A,
  node B took over and served a new request through the virtual IP after 2.1 s.
- A separate lab checked both installer-generated Keepalived configurations
  with `--config-test`. With the generated health check, a request through the
  same virtual IP returned 200 after 3.2 s when Tinyproxy stopped on node A.

The failover tests shared one WSL host. Outstanding checks include Ubuntu
24.04, upgrade from a production legacy cluster, Nginx/TLS client access,
and a full HA install and network-partition test on separate Linux hosts.

## Reproduce the checks

```bash
python scripts/dev/test.py
python scripts/dev/check_server.py --backend gunicorn
python scripts/dev/check_server.py --backend granian
```

The HA lab requires a Linux test host with Tinyproxy, Keepalived, iproute2, and
the dependencies in `requirements/granian.txt`. Start it from the repository
root with the virtual environment in `.venv`.

```bash
sudo unshare --mount --propagation private .venv/bin/python scripts/dev/ha_lab.py --hold
```

Two isolated network namespaces use a dedicated bridge. The consoles listen on
loopback ports 8101 and 8102 and the proxy on port 8130. The startup test covers
2FA sign-in, replay rejection, and new HTTP traffic after failover. The lab
prints paths to root-only credential files. The management consoles use demo
mode to avoid restarting host services. Tinyproxy and Keepalived run as real
processes. The lab does not exercise automatic policy synchronization.

## 11 November 2026: managed HA and log retention

Two disposable Ubuntu 24.04 VirtualBox guests were converted from standalone
through reviewed GUI plans and a one-time pairing code. Both retained UFW
incoming/outgoing deny defaults. Published filters and shared Tinyproxy settings
arrived automatically on B. Actual proxy requests passed through the VIP on A
and after failover to B. Automatic failure recovery and manual standalone
recovery were exercised. These VMs share one physical host; this does not prove
physical host-loss recovery, fencing, or partition safety.

The new audit and journal retention services ran on a real guest. Synthetic
31-day records were removed from both active and rotated audit files, while
2-day records remained searchable through a UTC date range. The operational
and Tinyproxy journal namespaces were readable through the authenticated API.
Vacuuming those namespaces preserved a sentinel in the default host journal.
Both namespace budgets and successful maintenance service results were checked.

Regression tests cover parallel audit writers/rotation, age and size cleanup,
archive reads across rotation, anchored pagination, malformed records,
IPv4/IPv6 and credential redaction, private-log path restrictions, access
control, and public notices. Playwright covers NL/EN, both themes, mobile logs,
date-filter requests, history/live transitions, and destructive-button contrast.
The deployment organization must still validate its own privacy notice,
external logs, backup/export retention, network plan, and workload.

## 12 November 2026

### Operator workflows and HA responsiveness

The working build was checked with the full project checks and isolated browser
fixtures. The Windows Python suite runs 144 tests with 7 platform-specific skips;
the Linux installer runs the full suite during each lab upgrade. Playwright covers
EN/NL, light/dark themes, small viewports, keyboard interaction, backup write
blocking and the reviewed filter-mode draft workflow. Documentation screenshots
are generated from isolated fixtures with `PP_DOC_SCREENSHOTS=1`.

On the running VirtualBox lab, rule order was changed on A and verified in B's
already-open page without manual reload, then restored. Tinyproxy on A was
temporarily stopped with SIGSTOP while Keepalived stayed active. B acquired the
VIP, its page reported the active role, and a client received an HTTP response
through the VIP. A resumed automatically, recovered the preferred VIP and the
backup display returned to standby. A separate rescue timer also sent SIGCONT.
The standalone GUI activated the opposite filter mode and restored its original
configuration. Both HA nodes remained paired and policy synchronization recovered.

These checks do not establish production availability across a physical network
partition, upstream outage or shared hypervisor failure. Account replication and
granular roles were still separate work at this stage. The later [role implementation](roles.md) adds restricted local access.

### Anti-lockout and visible drafts

The standalone and both HA lab nodes were upgraded with a reviewed single-host
management source, their actual SSH/web ports and a root-owned safety profile
(mode 0640, service-group readable). Existing firewall activation state was kept.
The empty-firewall installation path exposed an invalid numeric insert position;
it rolled back, and the corrected prepend operation passed on that same host.

Authenticated API attempts to remove the protected Allow block, change the pinned
proxy port or put a Deny before the management exceptions were rejected without
changing the active file or creating a draft. A shared Timeout change was then
published on A, reconciled on B with B's local safety block intact, and restored
on both nodes. The complete real failover/recovery and rule-order checks passed
again after the upgrades. Dedicated UFW safety rules are excluded from HA cleanup.

Browser checks cover unsaved/saved/active draft states, the redacted difference
view, save/discard actions and the existing theme/language/mobile matrix.
The installer review and non-root handoff retain the explicit protected addresses
and host-side ports. Windows checks run 144 Python tests with 7 platform skips.
Destination filters and proxy authentication still apply: this protects the
configured network access, not arbitrary routing or upstream policy changes.

The final Linux run passed all 144 Python tests without skips, the shell checks,
and isolated HTTP/account/2FA/audit/save checks with both Gunicorn and Granian
(two workers each). The extra backend was installed in a separate test venv;
the live deployment dependencies were kept intact.

### Local account roles

The Windows suite passes 153 Python tests (7 platform-specific skips); the Linux
installer runs all 153 without skips. The complete checks also pass for typing,
Python quality, frontend contracts, shell syntax and isolated HTTP/account/2FA
checks with Gunicorn and Granian. Browser coverage includes monitor and proxy
manager interfaces, NL/EN, both themes, mobile layout, role selection and session
revocation. Existing role-less accounts retain administrator access.

The three VirtualBox nodes were upgraded. Temporary monitor accounts were
created through the authenticated API, promoted and removed again. The old
sessions became invalid while passwords remained usable. Monitors could not
read native configuration or restart Tinyproxy; proxy managers could restart
standalone Tinyproxy but could not administer HA or accounts. The paired backup
kept shared policy read only, and both HA status panels remained synchronized.
Attempts to change the service user were rejected without creating a draft or
changing active files. Isolated tests also cover protected system drafts,
optional monitor 2FA and last-administrator protection in the CLI.

All three SSH connections report `10.0.2.2` as their source through VirtualBox
NAT. The shared management-source display is therefore expected in this lab;
it is not each node's own IP address. Source restrictions do not replace sign-in.

Monitor rule-layout regression checks cover full-width text at 320, 390 and
1440 pixels in both languages and themes. Filter exports are now reserved for
proxy/system administrators; direct monitor export requests return 403. These
checks passed on all three lab nodes using temporary accounts that were removed.

## Documentation website

The Zensical site builds with `--clean --strict`, including link and anchor
validation. The same build was checked from a generated release archive.
Playwright checks cover English/Dutch guides, light/dark mode, desktop/mobile
layout, translation links, real search results and keyboard closing. The local
preview uses the `/ProxyPermit/` project prefix to check Pages-relative paths.
Actionlint validates the Pages workflow. This prepares publication; it does not
mean GitHub Pages has already been enabled or deployed.

## Release documentation and historical Actions cleanup

The release instructions now separate preparation, CI, tagging, GitHub Release
assets and Pages deployment. Account-migration examples explicitly create an
admin before enabling local accounts. A strict documentation build is a required
dependency of release packaging. Actionlint, version checks and clean/strict
Zensical builds passed, including a build from the generated source archive.

On 12 November 2026, 39 completed runs of the obsolete Pylint workflow were
removed. A before/after inventory confirmed that all 46 current workflow runs
and 61 current artifacts were retained. No current artifacts belonged to the
obsolete runs. An old commit still resolved directly by SHA afterwards: Actions
cleanup does not erase GitHub source history. These changes do not create a
release, move a stable tag or establish a new remote CI result.

## 13 November 2026 — 1.2.1 regression checks

The Windows run passed 159 Python tests (seven platform-specific skips), frontend
checks, Pylint, installer type checks for Windows/Linux and the Granian HTTP
integration checks. Isolated browser tests confirmed that stale edits and
deletions preserve the newer rule, including a background refresh while the
editor or confirmation is open. The check runs in English and Dutch.

Regression tests also cover simultaneous edits from one revision, drafts or a
changed filter mode appearing before a waiting write obtains its lock, malformed
requests and import skip counts. Existing HA tests cover primary publication,
standby write rejection and authenticated policy transfer. These checks do not
constitute a new failover test on separate VMs.

The strict documentation build and screenshot-viewer checks cover both languages,
both themes and 390/1440-pixel widths. Screenshots use isolated test data.

A separate Linux virtual environment with the current requirements also passed
159 Python tests (one root-only skip). The older WSL service environment still
had pyrad 2.4; its RADIUS test failures disappeared with the required 2.6.0 in
the isolated environment. No service environment was modified.


## 15 November 2026

### Reload verification

The isolated Linux checks passed 191 tests. The Windows run passed the same
suite with 15 POSIX/root-specific skips, plus quality, type and frontend checks.
The installer was also upgraded successfully from a private source directory.

The standalone node and both VirtualBox HA nodes passed a real service-account
reload check: a fresh start/completion pair in the private journal, the same
systemd invocation and process ID, and a successful local HTTP probe. The check
rejects old startup messages. Native read ACLs survive atomic edits and rollback;
Tinyproxy does not receive access to management secrets through group membership.

An isolated Tinyproxy probe on versions 1.11.1 and 1.11.3 kept an HTTP transfer
and a CONNECT tunnel open through three reloads and allowlist/blocklist changes.
New requests followed the changed policy:

```bash
python3 scripts/dev/check_reload.py
```

The probe uses its own temporary files, ports and process. It does not reload
the installed service. For managed HA verification, use disposable nodes: record
`systemctl show tinyproxy.service -p MainPID -p InvocationID` on both nodes,
add a temporary `.invalid` domain through A with automatic activation enabled,
wait for B's matching revision, then remove the same rule. Both process IDs must
remain unchanged and both revisions must converge after each operation.


The authenticated A → B test passed for both addition and removal. Both Tinyproxy
process IDs stayed unchanged, the original filter bytes were restored and both
nodes returned to the original shared revision. OS checks on all three VMs
confirmed native config/filter read-only access for Tinyproxy and denied reads
of the management environment, account file and existing peer credentials.

These reload checks do not prove split-brain protection. Quorum/fencing and
partition-failure tests remain open in the [roadmap](planned-work.md).

### Key rotation and TLS verification

The isolated Linux suite passed all 208 tests with no skips. The Windows suite
passed with 19 platform- or OpenSSL-dependent tests skipped. Rotation tests cover
old-key revocation, transition expiry, concurrent requests and recovery after a
lost commit response. This does not yet verify the complete browser-driven key
rotation on two VMs.

TLS tests use real local connections with temporary generated certificates.
A first handshake checks the previously trusted SHA-256 pin without an HTTP
request or peer key. A second handshake validates the pinned certificate with
OpenSSL, including validity dates and signature; the pin is then checked again.
Only then may the peer key be sent. The pin identifies the node; hostname checks
and system CAs are not used for this purpose. A wrong pin, an expired certificate
and a certificate switch between handshakes are rejected. Controlled certificate
renewal and pin migration remain open, as do additional storage-failure and
restart tests for key rotation.

### Extended HA verification

The Linux suite now passes 231 tests without skips, including storage-failure,
optional fresh-TOTP, VIP-resource and publication-order checks. Both the existing
Playwright settings suite and the new isolated HA-security suite passed. The HA
suite covers NL/EN, both themes, mobile/desktop, focus, dirty-dialog dismissal,
incorrect passwords and monitor-role restrictions.

A real browser-driven key rotation passed on the VirtualBox HA pair. B confirmed
the new key automatically; the old key returned 403 and the new key returned 200.
The primary's filters and Tinyproxy process ID remained unchanged. The first run
exposed an outdated nginx path restriction; after updating only the internal
commit path, the complete test passed. See the upgrade note in
[Administration](administration.md#renewing-the-ha-peer-key).

A separate disposable KVM/libvirt prototype used two Ubuntu 24.04 nodes,
Pacemaker/Corosync, a TLS qdevice witness and the standard `fence_xvm` agent.
The host fencer was restricted to the two proxy VM UUIDs and source addresses;
the witness had no fencing key. Tests passed for a hung proxy, recovery without
automatic failback, a leftover VIP after killing Keepalived, witness loss with
both peers connected, asymmetric/full peer partitions and a frozen node while
the fencer was unavailable. Takeover was withheld until fencing was available;
the frozen VM was then powered off before a healthy VIP appeared on the peer.

This verifies the prototype's tested failure scenarios, not production integration.
The normal HA wizard and existing ProxyPermit nodes do not enable it automatically.
Platform adapters, complete lifecycle integration, broader fault coverage, certificate
renewal and management HTTPS remain open. Restoring an entire old OS image must
be quarantined through the platform until its cluster state has been reviewed;
guest software cannot protect against a restore that also removes its guards.

The final Linux run passed 247 tests without skips, plus shell checks and real
Gunicorn/Granian HTTP checks with two workers. The real HA pair also passed a
publication-order regression: B refused a temporarily restored lower sequence,
kept its native filter and Tinyproxy process, and synchronized after the original
publication was restored.

Internal certificate renewal then passed on that pair. B confirmed the new pin
and removed the old pin from its allowed set. Both Tinyproxy and Keepalived
process IDs and both filter files stayed unchanged. Peer-key rotation also passed
over the renewed TLS connection. OS-UID audit attribution and absence of the peer
key from audit records were verified. The completed recovery path was idempotent.

The separate KVM prototype's three VMs share one workstation; these tests do not
cover failure of that shared physical host. They were restarted for the maintenance tests below.
The existing standalone and two VirtualBox HA nodes remain running. Default HTTPS
for the management website is still planned; internal HA TLS renewal does not
enable HTTPS on that website.

#### Cluster status and rolling-maintenance verification

The separate KVM pair now runs real installer-managed ProxyPermit services on
top of its OS-prepared cluster. Both nodes passed the supported
`--upgrade --cluster-maintenance` path. Checks compared package versions,
Keepalived configuration and health helper, cluster configuration and profile,
native proxy configuration, filter file and accounts before and after each
upgrade; all were preserved. The application release changed on both nodes.

The active node handed the VIP to its peer before local changes. A second
maintenance request was rejected while the first reservation existed. A process
exit left the checkpoint and matching reservation intact; explicit recovery
restored standby. A separate failed post-maintenance health check also retained
the reservation. The real root recovery CLI then verified manager/proxy health
and peer protection before releasing it.

Root status export was checked on both nodes. The application account could read
only the reduced status and was denied CIB access. Witness loss changed the status
to degraded; stopping the exporter made its healthy evidence expire to unknown.
Isolated tests also reject fencing maps without distinct coverage of both nodes,
conflicting VIP ownership, malformed evidence and untrusted file permissions.
Playwright passed NL/EN, both themes, small and large viewports, keyboard/focus,
dirty-dialog handling, reauthentication and monitor restrictions.

These checks do not enable automatic production cluster provisioning. Other
platform adapters, cluster-aware removal/rollback and the remaining interruption
matrix are still open. See [Follow-up work](planned-work.md).

The latest Linux run passed all 280 Python tests without skips. Lint and installer
type checks passed, as did shell checks and the real two-worker Gunicorn and
Granian HTTP suites. The strict documentation build also passed. Fresh-install
testing found and fixed backup-directory preparation before the unprivileged
import; installer self-tests now isolate both cluster reservation and certificate
transition state from the host. Mutable native draft backups are validated
without recursive privileged ownership migration.
