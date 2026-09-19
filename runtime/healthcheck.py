"""Liveness through both listeners; no credentials or mutations."""
import json
import sys
import urllib.request


def heartbeat(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/common/heartbeat", timeout=2) as response:
        data = json.load(response)
    if data.get("code") != 200 or data.get("data", {}).get("status") is not True:
        raise ValueError("invalid heartbeat response")


def check():
    heartbeat(8081)
    heartbeat(8080)
    for path in ("/", "/admin/"):
        with urllib.request.urlopen("http://127.0.0.1:8080" + path, timeout=2) as response:
            body = response.read()
            if response.status != 200 or b'id="app"' not in body:
                raise ValueError("frontend unavailable: " + path)


if __name__ == "__main__":
    try:
        check()
    except Exception as error:
        print(f"unhealthy: {type(error).__name__}", file=sys.stderr)
        sys.exit(1)
