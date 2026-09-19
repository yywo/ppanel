"""Small fail-closed compatibility patch, applied only to prepared source."""
from pathlib import Path
import json
import sys


PATCH_VERSION = "2"


def replace(path, old, new, count=1):
    text = path.read_text(encoding="utf-8")
    if text.count(old) != count:
        raise RuntimeError(f"Upstream changed: expected {count} matches in {path}")
    path.write_text(text.replace(old, new), encoding="utf-8")


ADMIN_AUTH_BLOCK = '''export function getRedirectUrl(): string {
  if (typeof window === "undefined") return "/dashboard";
  const params = new URLSearchParams(window.location.search);
  const redirect = params.get("redirect");
  return redirect?.startsWith("/") ? redirect : "/dashboard";
}

export function setRedirectUrl(value?: string) {
  if (value) {
    sessionStorage.setItem("redirect-url", value);
  }
}

export function Logout() {
  if (!isBrowser()) return;
  removeCookie("Authorization");

  const pathname = location.pathname;
  const hash = location.hash.slice(1);

  if (!["", "/"].includes(pathname)) {
    setRedirectUrl(pathname);
    location.href = "/";
    return;
  }

  if (hash && !["", "/"].includes(hash)) {
    setRedirectUrl(hash);
    location.href = "/";
  }
}'''

ADMIN_AUTH_BLOCK_V1 = ADMIN_AUTH_BLOCK.replace(
    'location.href = "/";', 'location.href = "/admin/";'
)

ADMIN_AUTH_BLOCK_PATCHED = '''const ADMIN_ROOT = "/admin/";
const ADMIN_REDIRECT_KEY = "ppanel-admin-redirect-url";

export function getRedirectUrl(): string {
  if (typeof window === "undefined") return "/dashboard";
  const params = new URLSearchParams(window.location.search);
  const redirect = params.get("redirect");
  const stored = sessionStorage.getItem(ADMIN_REDIRECT_KEY);
  sessionStorage.removeItem(ADMIN_REDIRECT_KEY);
  const candidate = redirect || stored;
  return candidate?.startsWith("/") && !candidate.startsWith("//")
    ? candidate
    : "/dashboard";
}

export function setRedirectUrl(value?: string) {
  if (value?.startsWith("/") && !value.startsWith("//")) {
    sessionStorage.setItem(ADMIN_REDIRECT_KEY, value);
  }
}

export function Logout() {
  if (!isBrowser()) return;
  removeCookie("Authorization");

  const hash = location.hash.slice(1);
  setRedirectUrl(hash && !["", "/"].includes(hash) ? hash : "/dashboard");
  location.href = `${ADMIN_ROOT}#/`;
}'''


def patch(root):
    manifest_marker = root / ".ppanel-admin-manifest-patched"
    if not manifest_marker.exists():
        replace(
            root / "apps/admin/src/routes/__root.tsx",
            'href="/site.webmanifest"',
            'href="/admin/site.webmanifest"',
        )
        manifest_path = root / "apps/admin/public/site.webmanifest"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["start_url"] = "/admin/"
        manifest["scope"] = "/admin/"
        manifest["id"] = "/admin/"
        for icon in manifest["icons"]:
            icon["src"] = "/admin/" + icon["src"].lstrip("/")
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        manifest_marker.write_text("1\n", encoding="utf-8")

    marker = root / ".ppanel-single-container-patched"
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == PATCH_VERSION:
        return

    admin = root / "apps/admin/src/utils/common.ts"
    admin_text = admin.read_text(encoding="utf-8")
    if admin_text.count(ADMIN_AUTH_BLOCK) == 1:
        admin_text = admin_text.replace(ADMIN_AUTH_BLOCK, ADMIN_AUTH_BLOCK_PATCHED)
    elif admin_text.count(ADMIN_AUTH_BLOCK_V1) == 1:
        admin_text = admin_text.replace(ADMIN_AUTH_BLOCK_V1, ADMIN_AUTH_BLOCK_PATCHED)
    else:
        raise RuntimeError(f"Upstream changed: expected the admin auth/logout block in {admin}")
    admin.write_text(admin_text, encoding="utf-8")

    cookies = root / "packages/ui/src/lib/cookies.ts"
    cookie_text = cookies.read_text(encoding="utf-8")
    cookie_patch = '''  if (typeof document === "undefined") return;
  if (name === "Authorization" && location.pathname.startsWith("/admin/")) {
    name = "PPanelAdminAuthorization";
  }'''
    if "PPanelAdminAuthorization" not in cookie_text:
        replace(
            cookies,
            '  if (typeof document === "undefined") return;',
            cookie_patch,
            3,
        )

    # Separate the admin token from the user token and preserve hash-router redirects.
    marker.write_text(PATCH_VERSION + "\n", encoding="utf-8")


if __name__ == "__main__":
    patch(Path(sys.argv[1]).resolve())
