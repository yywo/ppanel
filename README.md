# PPanel single-container image

This project packages the PPanel backend and both official frontends into one
container. Nginx listens on container port `8080`, serves the user frontend at
`/`, serves the admin frontend at `/admin/`, and proxies `/v1`, `/v2`, and the
configured subscription path to the backend on an internal `127.0.0.1:8081`.

The host configuration remains mounted read-only at `/app/etc`. Startup copies it
to an internal runtime directory and changes only the internal listener to 8081;
the host `ppanel.yaml` is never rewritten.

## Prepare and build

The pinned inputs are recorded in `upstream.lock.json`. To refresh them from
stable GitHub releases, verify the diff, and stage a new build:

```bash
python3 scripts/update.py --resolve --backend v1.20.3 --frontend v1.21.0 --arch amd64
python3 scripts/update.py --build
```

The updater verifies SHA-256 before extracting either archive. The Docker build
also rebuilds both frontend applications from the pinned source with the admin
base-path and isolated admin-cookie patch.

Before selecting the image in Compose, run the disposable Linux integration test
on a machine with Docker:

```bash
python3 scripts/test_container.py ppanel-local:v1.20.3-v1.21.0-amd64 --browser
```

On this Windows workspace, the frontend build and the Nginx/static/API routing
test can be run without Docker:

```powershell
$env:PYTHONPATH = "$PWD/.tools/python"
python scripts/build_frontend.py --bun .tools/node_modules/.bin/bun.cmd
python scripts/test_nginx.py
python -m unittest discover -s tests -v
```

## Compose deployment

Point `PPANEL_IMAGE` at an image that has passed the disposable integration test:

```bash
export PPANEL_IMAGE=ppanel-local:v1.20.3-v1.21.0-amd64
docker compose up -d
docker compose ps
docker compose logs --tail=100 ppanel
```

The supplied Compose file preserves the production mapping
`127.0.0.1:8080:8080`, container name `ppanel-service`, read-only config bind,
and `restart: always`. Deploy by changing the image tag and recreating the
container only after the new image has passed the checks above; no production
directory is modified by the build scripts.

## GitHub Actions and GHCR

Every push to `main` and every `v*` tag builds the pinned `linux/amd64` image
and publishes it to `ghcr.io/yywo/ppanel`. The same workflow can be started
manually from the repository Actions page with **Run workflow**.

The published image can be selected in Compose without rebuilding on the
server:

```bash
export PPANEL_IMAGE=ghcr.io/yywo/ppanel:latest
docker login ghcr.io
docker compose pull
docker compose up -d
```

The workflow only has read access to repository contents and package-write
access for `GITHUB_TOKEN`; it does not deploy or change the production host.
