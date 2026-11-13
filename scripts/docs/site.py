"""Build or preview the complete documentation, including build-time highlighting."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['build', 'serve'])
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    config = tomllib.loads((ROOT / 'zensical.toml').read_text(encoding='utf-8'))['project']
    site = ROOT / config.get('site_dir', 'site')
    zensical = Path(sys.executable).with_name('zensical.exe' if sys.platform == 'win32' else 'zensical')
    executable = str(zensical) if zensical.is_file() else shutil.which('zensical')
    if not executable or not shutil.which('node') or not (ROOT / 'scripts/docs/node_modules/shiki').is_dir():
        parser.error('Install requirements/docs.txt and run npm ci --prefix scripts/docs --ignore-scripts with Node 20+.')
    subprocess.run([executable, 'build', '--clean', '--strict'], cwd=ROOT, check=True)
    subprocess.run(['node', str(ROOT / 'scripts/docs/highlight.mjs'), str(site)], cwd=ROOT, check=True)
    if args.action == 'serve':
        prefix = urlsplit(config['site_url']).path.rstrip('/')
        class Handler(SimpleHTTPRequestHandler):
            def translate_path(self, path):
                if path.startswith(prefix + '/'):
                    path = path[len(prefix):]
                return super().translate_path(path)
        print(f'Preview: http://127.0.0.1:{args.port}{prefix}/ (Ctrl+C to stop; rebuild after edits)', flush=True)
        with ThreadingHTTPServer(('127.0.0.1', args.port), partial(Handler, directory=str(site))) as server:
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
