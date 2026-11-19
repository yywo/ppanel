import copy
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'scripts')]
import yaml
from entrypoint import prepare_config
from patch_frontend import (
    PATCH_VERSION,
    SUBSCRIBE_FORM_CHECK,
    SUBSCRIBE_FORM_DEFAULTS,
    SUBSCRIBE_FORM_FILTER,
    SUBSCRIBE_FORM_RESET,
    SUBSCRIBE_NORMALIZE_HELPER,
    patch,
    patch_subscribe_form,
)
from test_container import validate_backend_probe
from update import extract


def subscribe_form_source():
    """A minimal fixture assembled from the exact snippets the patch targets."""
    return (
        "const defaultValues = {\n  nodes: [],\n  node_tags: [],\n};\n\n"
        "const form = useForm({\n  resolver: zodResolver(formSchema),\n"
        + SUBSCRIBE_FORM_DEFAULTS
        + "\n});\n\n"
        "useEffect(() => {\n  form?.reset(\n"
        + SUBSCRIBE_FORM_RESET
        + "\n}, [form, initialValues, open]);\n\n"
        "const node = (\n  <Checkbox\n"
        + SUBSCRIBE_FORM_CHECK
        + "\n    onCheckedChange={(checked) =>\n      checked\n"
        "        ? form.setValue(field.name, [...value, item.id])\n"
        "        : form.setValue(\n            field.name,\n"
        + SUBSCRIBE_FORM_FILTER
        + "\n          )\n    }\n  />\n);\n"
    )


SUBSCRIBE_FORM = subscribe_form_source()

ADMIN_COMMON = '''export function getRedirectUrl(): string {
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
}
'''

COOKIES = '''  if (typeof document === "undefined") return;
  if (typeof document === "undefined") return;
  if (typeof document === "undefined") return;
'''


def frontend_tree(base):
    """Minimal pinned-shaped frontend source tree for the fail-closed patch."""
    paths = {
        'apps/admin/src/sections/product/subscribe-form.tsx': SUBSCRIBE_FORM,
        'apps/admin/src/utils/common.ts': ADMIN_COMMON,
        'apps/admin/src/routes/__root.tsx': '<link href="/site.webmanifest" />\n',
        'apps/admin/public/site.webmanifest': json.dumps(
            {'start_url': '/', 'scope': '/', 'icons': [{'src': '/icon.png'}]}
        ),
        'packages/ui/src/lib/cookies.ts': COOKIES,
    }
    for name, text in paths.items():
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    return base


class ConfigTests(unittest.TestCase):
    def test_only_listener_changes_and_source_unchanged(self):
        original = {'Host': '0.0.0.0', 'Port': 8080, 'MySQL': {'Addr': 'db:3306', 'Password': 'a:# secret'},
                    'JwtAuth': {'AccessSecret': 'keep-me'}, 'Redis': {'Pass': 'secret'},
                    'TLS': {'Enable': True, 'CertFile': 'etc/tls.pem'},
                    'Custom': {'enabled': False, 'number': 123, 'unicode': '保留'}}
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / 'source'
            source.mkdir()
            input_file = source / 'ppanel.yaml'
            input_file.write_text(yaml.safe_dump(original), encoding='utf-8')
            before = input_file.read_bytes()
            (source / 'tls.pem').write_text('certificate')
            output = prepare_config(source, base / 'run')
            expected = copy.deepcopy(original)
            expected.update(Host='127.0.0.1', Port=8081)
            expected['TLS']['Enable'] = False
            self.assertEqual(yaml.safe_load(output.read_text(encoding='utf-8')), expected)
            self.assertEqual(input_file.read_bytes(), before)
            self.assertEqual((output.parent / 'tls.pem').read_text(), 'certificate')

    def test_refuses_setup_mode_and_conflicting_subscriptions(self):
        for bad in ({}, {'Subscribe': {'PanDomain': True}}, {'Subscribe': {'SubscribePath': '/admin'}},
                    {'Subscribe': {'SubscribePath': '/x; return 200;'}}):
            with self.subTest(config=bad), tempfile.TemporaryDirectory() as temp:
                source = Path(temp) / 'source'
                source.mkdir()
                config = {'Database': {'Addr': 'db:3306'}, 'JwtAuth': {'AccessSecret': 'test'}} if bad else {}
                config.update(bad)
                (source / 'ppanel.yaml').write_text(json.dumps(config))
                with self.assertRaises(ValueError):
                    prepare_config(source, Path(temp) / 'run')

    def test_custom_subscription_forwarded_exactly(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source'
            source.mkdir()
            (source / 'ppanel.yaml').write_text(json.dumps({'Database': {'Addr': 'db:3306'}, 'JwtAuth': {'AccessSecret': 'test'}, 'Subscribe': {'SubscribePath': '/subscribe/custom'}}))
            prepare_config(source, Path(temp) / 'run')
            self.assertIn('location = /subscribe/custom', (Path(temp) / 'run/subscribe.conf').read_text())


class SubscribeNodePatchTests(unittest.TestCase):
    def test_normalizes_string_node_ids_and_empty_node_groups(self):
        with tempfile.TemporaryDirectory() as temp:
            root = frontend_tree(Path(temp))
            path = root / 'apps/admin/src/sections/product/subscribe-form.tsx'
            patch_subscribe_form(root)
            text = path.read_text(encoding='utf-8')
            self.assertIn('result.nodes = values.nodes.map(Number).filter(Number.isFinite);', text)
            self.assertIn('tag.trim() !== ""', text)
            self.assertIn('checked={value.some(', text)
            self.assertIn('Number(v) !== Number(item.id)', text)
            self.assertNotIn('value.includes(item.id!)', text)
            # Both the useForm defaults and the reopen/reset path normalize values.
            self.assertEqual(text.count('normalizeNodes('), 3)

    def test_is_idempotent_and_run_before_other_short_circuits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = frontend_tree(Path(temp))
            (root / '.ppanel-single-container-patched').write_text(PATCH_VERSION + '\n')
            path = root / 'apps/admin/src/sections/product/subscribe-form.tsx'
            patch(root)
            after_first = path.read_text(encoding='utf-8')
            self.assertIn('normalizeNodes(', after_first)
            patch(root)
            self.assertEqual(path.read_text(encoding='utf-8'), after_first)

    def test_fails_closed_when_upstream_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = frontend_tree(Path(temp))
            path = root / 'apps/admin/src/sections/product/subscribe-form.tsx'
            path.write_text('export default function Changed() {}\n', encoding='utf-8')
            with self.assertRaises(RuntimeError):
                patch_subscribe_form(root)

    def test_leaves_the_file_untouched_when_a_match_is_ambiguous(self):
        with tempfile.TemporaryDirectory() as temp:
            root = frontend_tree(Path(temp))
            path = root / 'apps/admin/src/sections/product/subscribe-form.tsx'
            path.write_text(SUBSCRIBE_FORM + SUBSCRIBE_FORM, encoding='utf-8')
            before = path.read_text(encoding='utf-8')
            with self.assertRaises(RuntimeError):
                patch_subscribe_form(root)
            self.assertEqual(path.read_text(encoding='utf-8'), before)
            self.assertFalse((root / '.ppanel-subscribe-nodes-patched').exists())

    def test_skips_when_upstream_merged_the_same_helper(self):
        with tempfile.TemporaryDirectory() as temp:
            root = frontend_tree(Path(temp))
            path = root / 'apps/admin/src/sections/product/subscribe-form.tsx'
            upstream_fix = SUBSCRIBE_FORM.replace(
                'const defaultValues = {', SUBSCRIBE_NORMALIZE_HELPER
            )
            path.write_text(upstream_fix, encoding='utf-8')
            patch_subscribe_form(root)
            self.assertEqual(path.read_text(encoding='utf-8'), upstream_fix)


class ArchiveTests(unittest.TestCase):
    def test_rejects_traversal_and_symlink(self):
        for name, symlink in (('../escape', False), ('safe', True), ('/absolute', False)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                archive = Path(temp) / 'input.tgz'
                with tarfile.open(archive, 'w:gz') as tar:
                    item = tarfile.TarInfo(name)
                    if symlink:
                        item.type = tarfile.SYMTYPE
                        item.linkname = '../escape'
                    else:
                        item.size = 1
                    tar.addfile(item, None if symlink else io.BytesIO(b'x'))
                with self.assertRaises(ValueError):
                    extract(archive, Path(temp) / 'output')


class ContainerProbeTests(unittest.TestCase):
    def test_accepts_backend_http_success_with_application_error(self):
        validate_backend_probe(
            '/v1/admin/tool/version',
            200,
            {'Content-Type': 'application/json'},
            b'{"code":40002,"msg":"User token is empty"}',
        )

    def test_accepts_backend_plain_text_not_found(self):
        validate_backend_probe(
            '/v2/public/orders',
            404,
            {'Content-Type': 'text/plain; charset=utf-8'},
            b'Not Found',
        )


if __name__ == '__main__':
    unittest.main()
