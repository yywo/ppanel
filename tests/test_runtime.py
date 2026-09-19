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
from update import extract


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


if __name__ == '__main__':
    unittest.main()
