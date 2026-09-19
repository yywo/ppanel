"""Build the same patched, pinned frontend outside Docker (Bun + Node required)."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
from patch_frontend import patch

ROOT = Path(__file__).resolve().parents[1]


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--bun', default='bun')
    args = parser.parse_args()
    bun = Path(args.bun)
    if not bun.is_absolute() and (ROOT / bun).is_file():
        bun = (ROOT / bun).resolve()
    elif not bun.is_absolute():
        bun_on_path = shutil.which(args.bun)
        if not bun_on_path:
            raise SystemExit(f'Bun not found: {args.bun}')
        bun = Path(bun_on_path)
    source = ROOT / '.build/frontend'
    patch(source)
    env = {**os.environ, 'HUSKY': '0', 'CI': '1', 'VITE_API_BASE_URL': '', 'VITE_API_PREFIX': ''}
    subprocess.run([str(bun), 'install', '--frozen-lockfile', '--ignore-scripts'], cwd=source, env=env, check=True)
    for app in ('admin', 'user'):
        subprocess.run([str(bun), 'run', 'build'], cwd=source / 'apps' / app, env=env, check=True)
