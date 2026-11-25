# Recovery from the command line

[Nederlands](../nl/noodprocedures.md) · [Administrator guide](administration.md)

Open SSH sessions to the **individual node addresses**. Use an out-of-band
console if SSH is unavailable. Keep one session open while checking a new one.
Record the installed version and preserve the timestamped backup and private
checkpoint before making changes.

## Read the current state

```bash
cat /opt/proxypermit/current/VERSION
systemctl status proxypermit.service tinyproxy.service --no-pager
sudo journalctl --namespace=proxypermit -u proxypermit.service --since '30 minutes ago' --no-pager
```

On a protected cluster, check the cluster from **both** hosts:

```bash
sudo corosync-quorumtool -s
sudo crm_mon --one-shot --failcounts
ip -4 address show
```

Confirm quorum, fencing and which node actually has the VIP. Check the proxy
from a client too. When quorum or fencing cannot be verified, leave VIP
control with Pacemaker and recover access through the OS or fencing platform.

## Choose the matching recovery operation

| Situation | Command or procedure |
| --- | --- |
| Interrupted native Proxmox migration | From the installed app, run `sudo .venv/bin/python -m scripts.deploy.cluster_native --phase status` on both nodes. Compare the operation ID and plan hash. The status command is read-only. Follow the [phase table](high-availability.md#inspect-an-interrupted-protected-ha-activation). |
| Interrupted protected upgrade or rollback | From the installed app, run `sudo .venv/bin/python -m scripts.deploy.cluster_maintenance --status` to compare the private checkpoint with the live reservation. Repair the local manager and proxy first, then run the same command with `--recover`. It verifies local health and peer protection before releasing the reservation. |
| Roll back a completed protected application upgrade | From the extracted release that performed the upgrade, run `sudo bash install.sh --rollback BACKUP_NAME --cluster-maintenance --plan`. Review the selected release, then use the same command with `--apply`. The peer must serve the protected VIP first. |
| Retire an already detached native node | Confirm the peer can keep quorum and serve the VIP. On the departing host, use `sudo bash install.sh --retire-ha --detached-ha --plan` from the extracted release. See [what must be detached and masked](administration.md#remove-a-detached-protected-node) before applying. |
| Remove the console after successful protected retirement | Confirm a fresh SSH login first. Use `sudo bash install.sh --uninstall --detached-ha --plan`, then `--apply` after reviewing the plan. The local web console stops. SSH and pinned management firewall rules remain. |
| Fencing token expired | Use the affected host’s direct address and follow the [peer-token recovery procedure](high-availability.md#recover-after-local-proxmox-fencing-credentials-expire). |

The examples assume `/opt/proxypermit`. For a custom layout, enter its `current`
directory and pass `--install-dir` to commands that accept it. Use the actual
timestamped backup name for `BACKUP_NAME`. Keep plain installation-backup
rollback for independently managed nodes. Protected app rollback and OS cluster
recovery each have their own procedure.

If maintenance status reports `reservation-mismatch` or a checkpoint for a
different node, compare the two hosts and review the reservation at OS level.
Keep the checkpoint. `peerServingProtectedVIP: false` means peer takeover could
not be confirmed by the local check.
