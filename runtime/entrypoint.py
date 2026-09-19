"""Copy config, start both children, propagate signals, fail as a unit."""
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import yaml

from healthcheck import heartbeat


def prepare_config(source=Path('/app/etc'), run=Path('/run/ppanel')):
    config = yaml.safe_load((source / 'ppanel.yaml').read_text(encoding='utf-8'))
    if not isinstance(config, dict):
        raise ValueError('An initialized ppanel.yaml is required')
    database = config.get('Database') or config.get('MySQL') or {}
    if not database.get('Addr') or not (config.get('JwtAuth') or {}).get('AccessSecret'):
        raise ValueError('Database/MySQL.Addr and JwtAuth.AccessSecret must be configured; first-install UI is unsupported')
    subscribe = config.get('Subscribe') or {}
    if subscribe.get('PanDomain'):
        raise ValueError('Subscribe.PanDomain uses / and conflicts with the user frontend; use a dedicated subscription service')
    path = subscribe.get('SubscribePath') or '/v1/subscribe/config'
    if not re.fullmatch(r'/[A-Za-z0-9_/-]+', path) or '..' in path or '//' in path:
        raise ValueError('Unsupported SubscribePath; use a simple absolute URL path')
    if path == '/' or path.startswith(('/admin', '/static', '/assets')):
        raise ValueError('SubscribePath conflicts with frontend paths')
    run.mkdir(parents=True, exist_ok=True)
    os.chmod(run, 0o700)
    target = run / 'etc'
    shutil.copytree(source, target, dirs_exist_ok=True)
    config['Host'] = '127.0.0.1'
    config['Port'] = 8081
    config.setdefault('TLS', {})
    config['TLS'] = {**(config['TLS'] or {}), 'Enable': False}
    output = target / 'ppanel.yaml'
    output.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding='utf-8')
    os.chmod(output, 0o600)
    custom = ''
    if not re.match(r'^/v[12](?:/|$)', path):
        custom = 'location = ' + path + ' { include /app/runtime/proxy.conf; proxy_pass http://127.0.0.1:8081; }\n'
    (run / 'subscribe.conf').write_text(custom, encoding='utf-8')
    return output


def main():
    os.umask(0o077)
    config = prepare_config()
    for app in ('admin', 'user'):
        if not Path(f'/app/web/{app}/index.html').is_file():
            raise ValueError(f'Missing {app} frontend')
    subprocess.run(['nginx', '-t', '-c', '/app/runtime/nginx.conf'], check=True)
    stopping = False
    children = []

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGQUIT):
        signal.signal(sig, stop)
    code = 1
    try:
        children.append(subprocess.Popen(['/app/modules/ppanel-server', 'run', '--config', str(config)], start_new_session=True))
        children.append(subprocess.Popen(['nginx', '-c', '/app/runtime/nginx.conf', '-g', 'daemon off;'], start_new_session=True))
        deadline = time.monotonic() + 90
        failures = 0
        next_check = 0
        while not stopping:
            if any(child.poll() is not None for child in children):
                print('A service exited; stopping container', flush=True)
                break
            if time.monotonic() >= next_check:
                next_check = time.monotonic() + 5
                try:
                    heartbeat(8081)
                    failures = 0
                except Exception:
                    if time.monotonic() > deadline:
                        failures += 1
                    if failures >= 3:
                        print('Backend listener unavailable; stopping container', flush=True)
                        break
            time.sleep(0.2)
        if stopping:
            code = 0
    finally:
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
        deadline = time.monotonic() + 25
        for child in children:
            try:
                child.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
    return code


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        # YAML errors can contain credentials; do not echo parser input.
        print('Startup failed: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__), file=sys.stderr)
        sys.exit(1)
