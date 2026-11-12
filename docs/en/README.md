# Start here

[Nederlands](../nl/README.md) · [ProxyPermit on GitHub](https://github.com/Dantdmnl/ProxyPermit)

ProxyPermit helps you manage a Tinyproxy server through a web interface. You
choose which devices may use the proxy and which websites they may reach.
You can also inspect logs and use two servers for automatic takeover.

## What do you want to do?

| Your situation | Start with |
| --- | --- |
| I have a console address and an account | [First steps](getting-started.md) |
| I want to add a website, import rules or change settings | [Using the console](user-guide.md) |
| I want to install, update or remove ProxyPermit | [Installation](installation.md) |
| I want two servers to take over from each other | [High availability](high-availability.md) |
| Something is not working | [Troubleshooting](troubleshooting.md) |
| I do not recognize a technical term | [Terms explained](glossary.md) |

## For the administrator

After installation, use the [administrator guide](administration.md) to check
access, test client traffic and prepare backups. The [logging guide](logging.md)
explains what is recorded, how long it is kept and how to search it.

The console address and the proxy address serve different purposes. Your browser
opens the console to manage settings; client devices use the proxy address to
reach websites. Creating a console account does not configure a client device.

## For contributors

- [Try the VM lab](vm-lab.md) before changing a live installation.
- [Architecture](architecture.md) and [source layout](layout.md) explain the code.
- [Releases and CI](releases.md) covers checks and packaging.
- [Documentation website](documentation-site.md) explains previewing and publishing.
- [Verification](verification.md) records what has actually been tested.
- [Planned work](planned-work.md) lists features that are not available yet.

These pages follow `main`, including work that has not been released yet.
Historical instructions are kept in the
[archive](https://github.com/Dantdmnl/ProxyPermit/tree/main/docs/legacy).

[Users and roles](roles.md) explains which permissions each account needs.
