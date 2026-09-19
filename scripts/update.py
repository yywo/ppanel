"""Resolve official releases, pin hashes, stage inputs, optionally build an image.

Never changes running containers or production data. Python 3.11+ and Git required.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def remove_generated_tree(path):
    """Remove a generated tree even when archive/package tools made files read-only."""
    def onerror(function, failed_path, exc_info):
        os.chmod(failed_path, stat.S_IWRITE)
        function(failed_path)

    shutil.rmtree(path, onerror=onerror)


def fetch(url):
    headers = {'User-Agent': 'ppanel-single-container'}
    token = os.environ.get('GITHUB_TOKEN')
    if token and url.startswith('https://api.github.com/'):
        headers['Authorization'] = 'Bearer ' + token
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as response:
        return response.read()


def release(repo, tag):
    endpoint = 'latest' if tag == 'latest' else 'tags/' + tag
    data = json.loads(fetch(f'https://api.github.com/repos/perfect-panel/{repo}/releases/{endpoint}'))
    if data['draft'] or data['prerelease']:
        raise ValueError('Only stable published releases are supported')
    return data


def asset(data, name):
    item, = [a for a in data['assets'] if a['name'] == name]
    digest = item.get('digest') or ''
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError(f'No official SHA256 digest for {name}; refusing unverified asset')
    return {'url': item['browser_download_url'], 'sha256': digest[7:]}


def resolve(backend, frontend, arch):
    back = release('backend', backend)
    front = release('frontend', frontend)
    ref = 'refs/tags/' + front['tag_name']
    lines = subprocess.check_output(['git', 'ls-remote', 'https://github.com/perfect-panel/frontend.git', ref, ref + '^{}'], text=True).splitlines()
    refs = dict(line.split()[::-1] for line in lines)
    commit = refs.get(ref + '^{}', refs.get(ref))
    if not commit or not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('Cannot resolve frontend release commit')
    url = f'https://codeload.github.com/perfect-panel/frontend/tar.gz/{commit}'
    source = fetch(url)
    digest = hashlib.sha256(source).hexdigest()
    cache = ROOT / '.cache'
    cache.mkdir(exist_ok=True)
    (cache / digest).write_bytes(source)
    return {
        'schema': 1, 'arch': arch,
        'backend': {'tag': back['tag_name'], **asset(back, f'ppanel-server-linux-{arch}.tar.gz')},
        'frontend': {'tag': front['tag_name'], 'commit': commit, 'url': url, 'sha256': digest},
        'reference_frontend_assets': {kind: asset(front, f'ppanel-{kind}-web.tar.gz') for kind in ('admin', 'user')},
    }


def download(item):
    digest = item['sha256']
    if not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Invalid SHA256')
    cache = ROOT / '.cache'
    cache.mkdir(exist_ok=True)
    path = cache / digest
    payload = path.read_bytes() if path.exists() else fetch(item['url'])
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError('SHA256 mismatch for ' + item['url'])
    if not path.exists():
        path.write_bytes(payload)
    return path


def extract(archive, target, strip_root=False):
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar.getmembers():
            parts = member.name.replace('\\', '/').split('/')
            if strip_root:
                parts = parts[1:]
            parts = [p for p in parts if p not in ('', '.')]
            if not parts:
                continue
            if member.name.startswith('/') or '..' in parts or any(':' in p for p in parts):
                raise ValueError('Unsafe archive path')
            if not (member.isdir() or member.isfile()):
                raise ValueError('Links and special files in archives are unsupported')
            dest = target.joinpath(*parts)
            if member.isdir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as src, dest.open('wb') as out:
                    shutil.copyfileobj(src, out)
                dest.chmod(0o755 if member.mode & 0o111 else 0o644)


def prepare(lock):
    # Stage completely before replacing previous local build inputs.
    with tempfile.TemporaryDirectory(prefix='.prepare-', dir=ROOT) as temp:
        stage = Path(temp)
        extract(download(lock['backend']), stage / 'backend')
        extract(download(lock['frontend']), stage / 'frontend', strip_root=True)
        binary = stage / 'backend/ppanel-server'
        header = binary.read_bytes()[:20]
        machine = {'amd64': 62, 'arm64': 183}[lock['arch']]
        if header[:4] != b'\x7fELF' or header[4:6] != b'\x02\x01' or int.from_bytes(header[18:20], 'little') != machine:
            raise ValueError('Backend ELF architecture mismatch')
        (stage / 'manifest.json').write_text(json.dumps(lock, indent=2) + '\n', encoding='utf-8')
        target = ROOT / '.build'
        if target.exists():
            # Fixed path underneath this workspace; contains generated inputs only.
            remove_generated_tree(target)
        target.mkdir()
        for item in stage.iterdir():
            shutil.move(str(item), target / item.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resolve', action='store_true', help='Refresh pinned inputs; default uses existing lock')
    parser.add_argument('--backend', default='v1.20.3')
    parser.add_argument('--frontend', default='v1.21.0')
    parser.add_argument('--arch', choices=['amd64', 'arm64'], default='amd64')
    parser.add_argument('--lock', type=Path, default=ROOT / 'upstream.lock.json')
    parser.add_argument('--build', action='store_true')
    args = parser.parse_args()
    lock = resolve(args.backend, args.frontend, args.arch) if args.resolve else json.loads(args.lock.read_text(encoding='utf-8'))
    prepare(lock)
    if args.resolve:
        args.lock.write_text(json.dumps(lock, indent=2) + '\n', encoding='utf-8')
    image = f"ppanel-local:{lock['backend']['tag']}-{lock['frontend']['tag']}-{lock['arch']}"
    print('Prepared: ' + image, flush=True)
    if args.build:
        subprocess.run(['docker', 'build', '--platform', 'linux/' + lock['arch'], '-t', image, '.'], cwd=ROOT, check=True)
        print('Built: ' + image + '\nRun integration tests before selecting this image in Compose.')


if __name__ == '__main__':
    main()
