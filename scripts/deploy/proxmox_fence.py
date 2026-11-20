"""Opt-in pinned ClusterLabs Proxmox agent with node-local private tokens.

The upstream application remains GPL-2.0; see deploy/fence-agents. This module
does not create a cluster or replace the distribution's fencing agent.
"""
import argparse
import hashlib
import os
from pathlib import Path
import stat

from scripts.deploy.install import maintenance_lock, require_linux_root, require_trusted_path
from app.core.tinyproxy import atomic_write

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'deploy/fence-agents/fence_pve.py.in'
SOURCE_SHA256 = 'a3a2501518ed11054e8bdfbaa61e76326d514cdcf3a6ca98b0607a5738ca2b79'
TARGET = Path('/usr/sbin/fence_proxypermit_pve')
MARKER = '# Managed by ProxyPermit pinned ClusterLabs Proxmox fencing\n'

GUARD = '''
def proxypermit_credentials(options):
    import os, stat, re, time
    path = "/etc/proxypermit/proxmox-fencing.json"
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor) as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_nlink != 1:
            raise ValueError("Untrusted fencing profile")
        content = source.read(16385)
    if len(content) > 16384:
        raise ValueError("Invalid fencing profile")
    value = json.loads(content)
    if (not isinstance(value, dict) or set(value) != {"version", "host", "pveNode", "target", "expires", "tokenId", "token"}
            or value["version"] != 1 or type(value["expires"]) is not int or value["expires"] <= time.time()
            or type(value["target"]) is not int or not 100 <= value["target"] <= 999999999
            or not isinstance(value["host"], str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}", value["host"])
            or not isinstance(value["pveNode"], str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,62}", value["pveNode"])
            or not isinstance(value["tokenId"], str) or not re.fullmatch(r"[a-zA-Z0-9_.-]+@pve![a-zA-Z0-9_.-]+", value["tokenId"])
            or not isinstance(value["token"], str) or not re.fullmatch(r"[a-fA-F0-9-]{36}", value["token"])):
        raise ValueError("Invalid fencing profile")
    if (options.get("--ip") != value["host"] or str(options.get("--ipport", 8006)) != "8006"
            or options.get("--pve-node") != value["pveNode"] or options.get("--vmtype", "qemu") != "qemu"
            or options.get("--plug", str(value["target"])) != str(value["target"])
            or "--api-token" in options or "--password" in options or "--ssl-insecure" in options
            or "--pve-node-auto" in options or options.get("--action") not in ("status", "monitor", "off")):
        raise ValueError("Fencing request differs from the peer-only OS profile")
    options["--plug"] = str(value["target"])
    options["--api-token"] = value["tokenId"] + "=" + value["token"]


def proxypermit_ca():
    import os, stat
    path = "/etc/proxypermit/proxmox-fencing-ca.pem"
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_nlink != 1 or info.st_size > 16384:
        raise ValueError("Untrusted fencing CA")
    return path

'''


def content():
    source = SOURCE.read_bytes()
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        raise ValueError('The pinned upstream fencing source differs from the reviewed version.')
    text = source.decode().replace('@PYTHON@', '/usr/bin/python3').replace('@FENCEAGENTSLIBDIR@', '/usr/share/fence')
    replacements = {
        'def main():': GUARD + '\ndef main():',
        '\tshow_docs(options, docs)': '\tshow_docs(options, docs)\n\ttry:\n\t\tproxypermit_credentials(options)\n\texcept Exception:\n\t\tfail_usage("The private peer-only fencing profile or request could not be verified")',
        '\tconn = pycurl.Curl()': '\tconn = pycurl.Curl()\n\tconn.setopt(pycurl.CAINFO, proxypermit_ca())',
        '\t\tconn.setopt(pycurl.VERBOSE, True)': '\t\tpass  # Never dump token-bearing HTTP headers.',
    }
    for before, after in replacements.items():
        if text.count(before) != 1:
            raise ValueError('The pinned fencing transformation is ambiguous.')
        text = text.replace(before, after)
    return text.replace('\n', '\n' + MARKER, 1)


def install():
    require_linux_root('Proxmox fencing agent installation')
    with maintenance_lock():
        require_trusted_path(TARGET.parent, directory=True)
        for path in (Path('/usr/share/fence/fencing.py'), Path('/usr/bin/python3')):
            info = path.stat()
            if info.st_uid != 0 or info.st_mode & 0o022 or not stat.S_ISREG(info.st_mode):
                raise ValueError('Install the OS fencing library and Python first.')
        desired = content()
        if TARGET.exists() or TARGET.is_symlink():
            info = TARGET.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or TARGET.read_text() != desired:
                raise ValueError('An existing agent differs; review it without overwriting.')
        atomic_write(TARGET, desired, 0o755)
        os.chown(TARGET, 0, 0)  # pylint: disable=no-member


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        content()
        if args.apply:
            install()
        print('Pinned peer-only agent ' + ('installed' if args.apply else 'planned') + '; cluster activation is separate.')
        return 0
    except (OSError, ValueError):
        print('Agent installation refused. Review source integrity, OS dependencies and existing files.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
