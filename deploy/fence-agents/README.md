# Pinned ClusterLabs Proxmox fencing agent

`fence_pve.py.in` is unmodified upstream source from ClusterLabs/fence-agents,
revision `c6d1b7e8892661f1204572a8b5ce1241a451fe9b`. Its SHA-256 and license
files are recorded in `source.json`. The agent is GPL-2.0-or-later; see
`COPYING.applications` and upstream `README.licence`. These terms apply to
the third-party agent separately from ProxyPermit's own code.

The opt-in `scripts.deploy.proxmox_fence` installer verifies the source hash
and installs a separately named `fence_proxypermit_pve`. It retains the OS
fencing library and does not overwrite the distribution's `fence_pve`.

Documented build transformations replace the interpreter/library macros, load
one node-local root-only peer token, require a fixed protected CA file, reject
different endpoints/targets and power-on/reset operations, and disable verbose
HTTP header output. Only status, monitor and off are permitted. No credentials
belong in the CIB, command arguments, source tree or published archives.

This is an experimental opt-in backport, not a released distribution package.
Verify real off/recovery and TLS rejection on the intended platform before
cluster activation. Installation, preparation and activation remain separate.
