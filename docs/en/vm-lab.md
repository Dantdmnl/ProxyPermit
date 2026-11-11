# Test installation with VMs

[Nederlands](../nl/vm-lab.md) · [Documentation](README.md)

Use disposable Debian or Ubuntu VMs with systemd and Python 3.10 or later.
The lab runs the normal `install.sh`. Take a snapshot before the first
installation and keep the hypervisor console available for firewall testing.

| What to test | Setup |
| --- | --- |
| Installation, sign-in, proxy rules, upgrade, uninstall, and recovery | One standalone VM |
| Virtual IP, VRRP, UFW, and node retirement | Two VMs and a client on the allowed lab network |
| A quick proxy and failover smoke test | [Existing WSL lab](verification.md#reproduce-the-checks) |

## Prepare the VMs

1. Create a fresh VM in Proxmox or clone a clean cloud-init template.
   Let the ProxyPermit installer install Tinyproxy and Keepalived.
2. Put the HA VMs on the same isolated virtual network or VLAN. Allow unicast
   VRRP (IP protocol 112) between them, allow access to package repositories,
   and reserve an unused virtual IP.
3. Copy the same ProxyPermit source to each VM. Run commands from its
   repository root. Mark only disposable VMs as lab hosts:

```bash
hostname | sudo tee /etc/proxypermit-disposable-vm >/dev/null
sudo chmod 0600 /etc/proxypermit-disposable-vm
```

The lab uses `/opt/proxypermit-vm-lab` for releases. The real installer uses
normal host paths and systemd units, so start with a clean VM for each topology.
[Proxmox network configuration](https://pve.proxmox.com/pve-docs/pve-admin-guide.html#sysadmin_network_configuration)

### Local testing on Windows with VirtualBox

Create a clean Debian or Ubuntu VM with a NAT adapter for internet access.
Take a snapshot after installing the OS and use that VM for the standalone
test. For HA, make two clones of the clean state and give them distinct
hostnames. Add a second adapter of type **Internal Network** to each clone.
Name both networks `proxypermit-ha` and assign the static addresses from
the HA example below to those adapters, without adding a default gateway.
Give a test client on that internal network an address such as `10.77.0.20/24`.

For SSH management, forward NAT host port 2221 on A and 2222 on B to guest
port 22. Bind both forwarding rules to `127.0.0.1` on Windows and select
the host port with `ssh -p`.
[VirtualBox networking modes](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/networkingdetails.html)

## One standalone node

```bash
sudo python3 scripts/dev/vm_lab.py install
sudo python3 scripts/dev/vm_lab.py check
sudo python3 scripts/dev/vm_lab.py lifecycle
```

`install` runs the installer, creates the `lab-admin` test account, and checks
one allowed and one blocked destination through Tinyproxy. The password is in
`/var/lib/proxypermit-vm-lab/password`, readable by root only. Add
`--server granian` to test Granian.

`lifecycle` exercises upgrade, rollback, uninstall, and recovery in that
order. It checks sign-in and proxy traffic after each recovery step.
Run it from the source directory of the desired release to test a version
change.

Open the console from your workstation through SSH. Replace the user and address.

```bash
ssh -N -L 8100:127.0.0.1:8080 admin@10.77.0.11
```

Open `http://127.0.0.1:8100/`. To test 2FA, set it up under **Settings**
after running the automated checks. Those checks use a test account without 2FA.

## Two HA nodes

The example uses A at `10.77.0.11`, B at `10.77.0.12`, virtual IP
`10.77.0.10/24`, and client network `10.77.0.0/24`. Substitute your lab
addresses. Run this on both VMs with `node=b` on B and the correct interface
on each VM.

```bash
node=a
iface=ens18
sudo python3 scripts/dev/vm_lab.py install --mode ha \
  --ha-node "$node" --ha-interface "$iface" \
  --ha-node-a 10.77.0.11 --ha-node-b 10.77.0.12 \
  --ha-vip 10.77.0.10/24 --ha-client-cidr 10.77.0.0/24 --ha-vrid 51
```

The lab checks Keepalived and the three registered UFW rules on each node.
To test with **active** UFW, open the hypervisor console first. If needed,
allow SSH from the management address before enabling UFW. Then check
`sudo ufw status verbose` and `sudo ufw show added` on both VMs.

Open the network planner under **Settings → High availability → Validate
HA settings** on a standalone node. Once HA is configured, that section
shows synchronization and virtual IP status.

### Automatic policy synchronization

On A, configure the dedicated TLS listener from
`deploy/nginx/proxypermit-ha.conf.example`. Use the physical HA addresses,
bind port 8443 only on the HA interface, and allow TCP 8443 only from B in
UFW. Provide a certificate and private key readable by the TLS server.
Set `HA_POLICY_TLS_PIN` in `/etc/proxypermit/.env` on A to the certificate's
SHA-256 digest in lowercase without punctuation. Restart ProxyPermit on A.

On B, install `proxypermit-ha-policy.service` and
`proxypermit-ha-policy.timer` from `deploy/systemd` into
`/etc/systemd/system`. If the installer used a custom directory, adjust both
`WorkingDirectory` and `ExecStart` in the service. Run
`sudo systemctl daemon-reload` and
`sudo systemctl enable --now proxypermit-ha-policy.timer`.

Open **Settings → High availability → Policy synchronization**. On A
choose **Add node**. Paste the code on B and choose **Join cluster**.
Publish changes on A. B automatically applies the active filters and shared
Tinyproxy settings. If A or B is unavailable, the timer retries; B continues
to serve its last working configuration. Disconnect the pairing on both
nodes before replacing either peer or changing the TLS certificate.

1. Run `python3 scripts/dev/vm_lab.py origin` in a separate terminal on each
   node. This HTTP test origin listens on loopback and returns the node hostname.
2. From a client on the lab network, test through the virtual IP. Replace
   `proxy-a` and `proxy-b` with the actual output of `hostname` on the two VMs.

   ```bash
   python3 scripts/dev/vm_lab.py check-vip 10.77.0.10 proxy-a
   ```

3. Stop Keepalived on A with `sudo systemctl stop keepalived.service` and
   repeat the client test with `proxy-b`. Use the hypervisor console if you
   also want to test complete VM failure.
4. Test retirement separately. Keep A running with Keepalived stopped and B
   serving traffic. Open A’s console through an SSH tunnel to its physical IP.
   Use **Settings → High availability (HA)** to retire A.
5. Verify that Keepalived and Tinyproxy are stopped on A and the recorded HA
   rules are absent from `sudo ufw show added`. Repeat the client test with
   `proxy-b`. Use the backup name in the retirement journal to restore A, or
   revert the VM snapshots.

Use separate SSH tunnels for the management consoles, such as local ports
8101 and 8102. Test client traffic through the virtual IP on port 3128.
