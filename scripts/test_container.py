"""Linux Docker integration: disposable database, Redis, application, no host config.

Run: python3 scripts/test_container.py IMAGE [--browser]
Needs Docker, and node + tests/node_modules for --browser.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def wait_for(test, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if test():
                return
        except (OSError, ValueError, subprocess.CalledProcessError):
            pass
        time.sleep(1)
    raise AssertionError('Timed out waiting for service')


def get(base, path):
    try:
        response = urllib.request.urlopen(base + path, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image')
    parser.add_argument('--browser', action='store_true')
    args = parser.parse_args()
    prefix = 'ppanel-test-' + uuid.uuid4().hex[:10]
    containers = []
    docker('network', 'create', prefix)
    try:
        with tempfile.TemporaryDirectory(prefix=prefix) as temp:
            config = Path(temp) / 'ppanel.yaml'
            config.write_text(json.dumps({
                'Host': '0.0.0.0', 'Port': 8080,
                'JwtAuth': {'AccessSecret': uuid.uuid4().hex, 'AccessExpire': 604800},
                'Database': {'Driver': 'mysql', 'Addr': 'db:3306', 'Username': 'root', 'Password': 'isolated-test', 'Dbname': 'ppanel', 'Config': 'charset=utf8mb4&parseTime=true&loc=UTC'},
                'Redis': {'Host': 'redis:6379'}, 'Logger': {'Mode': 'console'},
            }), encoding='utf-8')
            digest = hashlib.sha256(config.read_bytes()).hexdigest()
            for name, alias, image, extras in (
                ('db', 'db', 'mysql:8.0', ['-e', 'MYSQL_ROOT_PASSWORD=isolated-test', '-e', 'MYSQL_DATABASE=ppanel', '--health-cmd', 'mysqladmin ping -h 127.0.0.1 -pisolated-test', '--health-interval', '2s', '--health-retries', '60']),
                ('redis', 'redis', 'redis:7-alpine', []),
            ):
                container = prefix + '-' + name
                containers.append(container)
                docker('run', '-d', '--name', container, '--network', prefix, '--network-alias', alias, *extras, image)
            wait_for(lambda: docker('inspect', '-f', '{{.State.Health.Status}}', prefix + '-db') == 'healthy', 180)
            app = prefix + '-app'
            containers.append(app)
            docker('run', '-d', '--name', app, '--network', prefix, '-p', '127.0.0.1::8080', '--mount', f'type=bind,source={temp},target=/app/etc,readonly', args.image)
            port = docker('port', app, '8080/tcp').rsplit(':', 1)[1]
            base = 'http://127.0.0.1:' + port
            wait_for(lambda: docker('inspect', '-f', '{{.State.Health.Status}}', app) == 'healthy')
            for path in ('/', '/admin/'):
                status, headers, body = get(base, path)
                assert status == 200 and b'id="app"' in body, path
            for path in ('/static/missing.js', '/admin/static/missing.js', '/admin/assets/locales/missing.json'):
                assert get(base, path)[0] == 404, path
            assert urllib.request.urlopen(base + '/admin').url == base + '/admin/'
            status, headers, body = get(base, '/v1/common/heartbeat')
            assert status == 200 and json.loads(body)['data']['status'] is True
            for path in ('/v1/admin/tool/version', '/v2/public/orders'):
                status, headers, body = get(base, path)
                assert 'json' in headers.get('Content-Type', '') and json.loads(body)['code'] not in (0, 200), path
            if args.browser:
                subprocess.run(['node', str(ROOT / 'tests/browser.mjs')], env={**os.environ, 'BASE_URL': base}, check=True)
            # Graceful SIGTERM must finish, and must not mutate the read-only source.
            started = time.monotonic()
            docker('stop', '-t', '35', app)
            assert time.monotonic() - started < 33
            assert docker('inspect', '-f', '{{.State.ExitCode}}', app) == '0'
            assert hashlib.sha256(config.read_bytes()).hexdigest() == digest
            # Either process dying must cause the entire container to exit nonzero.
            for child in ('nginx', 'backend'):
                docker('start', app)
                wait_for(lambda: docker('inspect', '-f', '{{.State.Health.Status}}', app) == 'healthy')
                if child == 'nginx':
                    kill = "import os,signal; os.kill(int(open('/run/ppanel/nginx.pid').read()), signal.SIGKILL)"
                else:
                    kill = "import os,signal,pathlib; p=[int(x.name) for x in pathlib.Path('/proc').iterdir() if x.name.isdigit() and (x/'cmdline').exists() and (x/'cmdline').read_bytes().startswith(b'/app/modules/ppanel-server\\0')]; assert len(p)==1; os.kill(p[0],signal.SIGKILL)"
                docker('exec', app, 'python3', '-c', kill)
                wait_for(lambda: docker('inspect', '-f', '{{.State.Running}}', app) == 'false', 40)
                assert docker('inspect', '-f', '{{.State.ExitCode}}', app) != '0'
            print('PASS: startup, health, HTTP/API routes, read-only config, SIGTERM, both child failures')
    except Exception:
        for container in containers:
            subprocess.run(['docker', 'logs', '--tail', '100', container], check=False)
        raise
    finally:
        for container in reversed(containers):
            subprocess.run(['docker', 'rm', '-fv', container], check=False, stdout=subprocess.DEVNULL)
        subprocess.run(['docker', 'network', 'rm', prefix], check=False, stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
