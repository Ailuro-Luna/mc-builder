import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import builder
import rcon
import test_builder

spec = importlib.util.spec_from_file_location('publication', Path(__file__).parent/'scripts/check_publication.py')
publication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publication)


class PublicationTests(unittest.TestCase):
    def test_rejects_deployment_details_without_printing_the_values(self):
        samples = ['/'+'home'+'/operator/world', '203.'+'0.113.9',
                   'person'+'@'+'example.invalid', 'ghp_'+'x'*30,
                   ''.join(['https', '://', 'user', ':', 'secret', '@', 'example.invalid'])]
        for sample in samples:
            with self.subTest(sample_type=type(sample).__name__):
                findings = publication.scan_text(sample)
                self.assertTrue(findings)
                self.assertNotIn(sample, str(findings))
        self.assertFalse(publication.scan_text('127.0.0.1 /path/to/minecraft-server'))

    def test_private_paths_are_outside_public_allowlist(self):
        for path in ['config.json','.local/guide.md','jobs/id/status.json','projects/site/plan.json','docs/reports/report.md']:
            self.assertNotIn(path,publication.PUBLIC_FILES)

    def test_clean_head_cannot_hide_sensitive_ancestor(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def git(*args):
                return subprocess.run(['git',*args],cwd=root,check=True,capture_output=True)
            git('init','-q')
            git('config','user.name','Example Contributor')
            git('config','user.email','contributor@users.noreply.github.com')
            p=root/'README.md';p.write_text('/'+'home'+'/operator/world')
            git('add','README.md');git('commit','-qm','Initial documentation')
            p.write_text('Generic project documentation')
            git('add','README.md');git('commit','-qm','Generic documentation')
            self.assertFalse(publication.scan_tree('HEAD',root))
            self.assertTrue(publication.scan_history('HEAD',root))


class DeploymentCompatibilityTests(unittest.TestCase):
    def test_worker_name_comes_from_private_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg={'shared_root':str(Path(folder)/'existing_worker'),'server_root':'/example/server'}
            with patch.object(builder,'config',return_value=cfg),patch.object(builder,'Rcon') as transport:
                transport.return_value.__enter__.return_value.command.return_value='EXISTING_JSON:{"ok":true}'
                self.assertTrue(builder.request('health')['ok'])
                command=transport.return_value.__enter__.return_value.command.call_args.args[0]
                self.assertTrue(command.startswith('existing_worker call '))
                cfg['worker_name']='invalid;command'
                with self.assertRaises(ValueError):builder.request('health')

    def test_old_and_generic_response_prefixes_are_accepted(self):
        for prefix in ['EXISTING','MC_BUILDER']:
            self.assertEqual(builder.decode_reply(prefix+'_ENTITIES:[]','ENTITIES'),[])
        with self.assertRaises(RuntimeError):builder.decode_reply('No structured result','JSON')

    def test_default_rcon_root_uses_local_config(self):
        socket=test_builder.FakeSocket(test_builder.packet(1,2,''))
        with patch('pathlib.Path.read_text',return_value=json.dumps({'server_root':'/example/server'})), \
             patch('rcon.properties',return_value={'enable-rcon':'true','rcon.port':'25575','rcon.password':'test-only'}) as properties, \
             patch('rcon.socket.create_connection',return_value=socket):
            with rcon.Rcon():pass
            properties.assert_called_once_with(Path('/example/server/server.properties'))


if __name__ == '__main__':unittest.main()
