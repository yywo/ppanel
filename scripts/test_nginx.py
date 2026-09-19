"""Exercise the built static frontends and reverse proxy with local Nginx.

This is the Windows-friendly part of the integration test. It does not start the
Linux backend binary; a tiny local HTTP server stands in for backend responses so
the route, asset, and API forwarding behavior can be checked without Docker.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class BackendHandler(BaseHTTPRequestHandler):
    paths = []

    def do_GET(self):  # noqa: N802 - required by BaseHTTPRequestHandler
        self.paths.append(self.path)
        body = {"code": 200, "data": {"status": True}}
        if self.path.endswith("/common/site/config"):
            body = {"code": 200, "data": {}}
        elif "/public/subscribe/" in self.path:
            body = {"code": 200, "data": {"list": [], "total": 0}}
        elif self.path != "/v1/common/heartbeat":
            body = {"code": 401, "msg": "test backend response"}
        payload = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        return


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args):
        return None


def http_get(base, path, follow=True):
    try:
        if follow:
            response = urllib.request.urlopen(base + path, timeout=5)
        else:
            opener = urllib.request.build_opener(NoRedirectHandler)
            response = opener.open(base + path, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read()


def nginx_config(user_root, web_root, port):
    return f"""worker_processes 1;
pid logs/nginx.pid;
error_log logs/error.log notice;
events {{ worker_connections 128; }}
http {{
    include mime.types;
    default_type application/octet-stream;
    access_log logs/access.log;
    sendfile on;
    map $http_upgrade $connection_upgrade {{ default upgrade; '' close; }}
    map $http_x_forwarded_proto $forwarded_proto {{ default $scheme; http http; https https; }}
    server {{
        listen 127.0.0.1:{port};
        root {user_root.as_posix()};
        index index.html;
        absolute_redirect off;
        location = /admin {{ return 308 /admin/$is_args$args; }}
        location /admin/ {{
            root {web_root.as_posix()};
            try_files $uri $uri/ /admin/index.html;
        }}
        location ^~ /admin/static/ {{
            root {web_root.as_posix()};
            try_files $uri =404;
        }}
        location ^~ /admin/assets/ {{
            root {web_root.as_posix()};
            try_files $uri =404;
        }}
        location ^~ /static/ {{
            root {user_root.as_posix()};
            try_files $uri =404;
        }}
        location ^~ /assets/ {{
            root {user_root.as_posix()};
            try_files $uri =404;
        }}
        location ~ ^/v[12](?:/|$) {{
            include proxy.conf;
            proxy_pass http://127.0.0.1:8081;
        }}
        location / {{ try_files $uri $uri/ /index.html; }}
    }}
}}
"""


def wait_for_http(base):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            if http_get(base, "/")[0] == 200:
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    raise AssertionError("Nginx did not become ready")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--nginx",
        default=str(ROOT / ".tools/nginx-1.28.0/nginx.exe"),
        help="path to nginx executable",
    )
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    nginx = shutil.which(args.nginx) or args.nginx
    user_root = ROOT / ".build/frontend/apps/user/dist"
    admin_root = ROOT / ".build/frontend/apps/admin/dist"
    if not (user_root / "index.html").is_file() or not (admin_root / "index.html").is_file():
        raise SystemExit("Build both frontend dist directories first")

    BackendHandler.paths = []
    backend = ThreadingHTTPServer(("127.0.0.1", 8081), BackendHandler)
    backend_thread = threading.Thread(target=backend.serve_forever, daemon=True)
    backend_thread.start()
    nginx_process = None
    with tempfile.TemporaryDirectory(prefix="ppanel-nginx-") as temp:
        temp_path = Path(temp)
        logs = temp_path / "logs"
        logs.mkdir()
        for name in (
            "client_body_temp",
            "proxy_temp",
            "fastcgi_temp",
            "uwsgi_temp",
            "scgi_temp",
        ):
            (temp_path / "temp" / name).mkdir(parents=True)
        web_root = temp_path / "web"
        shutil.copytree(admin_root, web_root / "admin")
        shutil.copytree(user_root, web_root / "user")
        config = temp_path / "nginx.conf"
        config.write_text(
            nginx_config(
                user_root,
                web_root,
                free_port(),
            ),
            encoding="utf-8",
        )
        port = int(next(line.split(":")[-1].rstrip(";") for line in config.read_text().splitlines() if "listen 127.0.0.1:" in line))
        base = f"http://127.0.0.1:{port}"
        shutil.copy(ROOT / ".tools/nginx-1.28.0/conf/mime.types", temp_path / "mime.types")
        shutil.copy(ROOT / "runtime/proxy.conf", temp_path / "proxy.conf")
        check = subprocess.run([nginx, "-p", str(temp_path), "-t", "-c", str(config)], capture_output=True, text=True)
        if check.returncode:
            raise AssertionError(check.stderr or check.stdout)
        nginx_process = subprocess.Popen([nginx, "-p", str(temp_path), "-c", str(config), "-g", "daemon off;"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            wait_for_http(base)
            for path in ("/", "/admin/", "/admin/client-route", "/client-route"):
                status, _, body = http_get(base, path)
                assert status == 200 and b'id="app"' in body, path
            status, headers, _ = http_get(base, "/admin", follow=False)
            assert status == 308 and headers.get("Location") == "/admin/", (
                status,
                headers.get("Location"),
            )
            for path in ("/static/missing.js", "/admin/static/missing.js"):
                assert http_get(base, path)[0] == 404, path
            status, headers, body = http_get(base, "/v1/common/heartbeat")
            assert status == 200 and json.loads(body)["data"]["status"] is True
            status, headers, body = http_get(base, "/v1/admin/tool/version")
            assert status == 200 and json.loads(body)["code"] == 401
            assert "/v1/common/heartbeat" in BackendHandler.paths
            assert "/v1/admin/tool/version" in BackendHandler.paths
            if args.browser:
                subprocess.run(
                    ["node", str(ROOT / "tests/browser.mjs")],
                    env={**os.environ, "BASE_URL": base},
                    check=True,
                )
            print("PASS: Nginx frontend roots, SPA fallbacks, admin redirect, 404s, and API proxy")
        finally:
            subprocess.run([nginx, "-p", str(temp_path), "-c", str(config), "-s", "quit"], capture_output=True)
            try:
                nginx_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                nginx_process.kill()
                nginx_process.wait()
    backend.shutdown()
    backend.server_close()


if __name__ == "__main__":
    main()
