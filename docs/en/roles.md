# Users and roles

[Nederlands](../nl/rollen.md) · [Documentation](README.md)

Choose the role that matches the person's work. A role applies to one node;
accounts, passwords, 2FA and roles are not copied to the other HA node.

| Task | Monitor | Proxy manager | System administrator |
| --- | --- | --- | --- |
| View status, filter rules and logs | Yes | Yes | Yes |
| Export filter rules | No | Yes | Yes |
| Change own password and optional 2FA | Yes | Yes | Yes |
| Add, edit, import, remove or reorder rules | No | Yes | Yes |
| View Tinyproxy configuration and edit/publish proxy policy | No | Yes | Yes |
| Restart Tinyproxy | No | Yes | Yes |
| Change service user/group, listener bindings or file paths | No | No | Yes |
| Create/join/retire HA nodes or revoke pairing | No | No | Yes |
| Add/remove users or change roles | No | No | Yes |
| Sign out all sessions | No | No | Yes |

On a paired backup, shared policy stays read only for every role. Publish on A.
Monitors can read retained logs, which may contain operational or personal data;
grant that role only to people who should have that access. Native configuration
may contain proxy credentials, so it is available only to proxy managers and
system administrators. These are web-console roles, not Linux user accounts.

## Create an account

1. Sign in as a **System administrator** and open **Settings → Users and roles**.
2. Choose **Add account**, enter a personal username and set the intended role.
   The default is **Monitor**.
3. Enter the new user's initial password and confirm with your own current
   password and a fresh authenticator code if your account uses 2FA.
4. Give the user the console address and credentials through your approved channel.
   Ask them to change their initial password. They may enable their own 2FA.

## Change a role

Choose **Change role** next to the account, select the intended role and confirm
with your own credentials. Existing sessions for that user are invalidated;
their password and 2FA setup are kept. They must sign in again.

You cannot change your own role or remove your own account through the GUI.
The account tools also prevent removal or demotion of the last valid system
administrator. Existing accounts without a role retain system administrator
rights when upgraded; review them and reduce access where appropriate.

## Recover through the host

An authorized OS administrator can inspect and change roles in the account file:

```sh
cd /opt/proxypermit/current
sudo .venv/bin/python scripts/tools/accounts.py list
sudo .venv/bin/python scripts/tools/accounts.py role alice manager
```

Use the actual installation path. CLI `set USER --role ROLE` also changes the
password; `role USER ROLE` changes only the role and session revision. Role
values are `admin`, `manager` and `monitor`. New CLI accounts default to monitor;
the installation's initial account remains a system administrator.

Roles apply to local accounts. Legacy single-login or RADIUS deployments keep
their existing administrator access until migrated to local accounts.
