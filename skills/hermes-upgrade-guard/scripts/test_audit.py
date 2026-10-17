"""Exercise drift detection in temporary fixtures; never modifies the real profile."""
import copy
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

import yaml

spec = importlib.util.spec_from_file_location('guard_audit', Path(__file__).with_name('audit.py'))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'home'
        self.repo = Path(self.tmp.name) / 'repo'
        self.home.mkdir()
        (self.repo / 'gateway').mkdir(parents=True)
        self.soul = '## Custom autonomy\nKeep working within the authorized goal.\n'
        (self.home / 'SOUL.md').write_text(self.soul)
        (self.home / 'collector.py').write_bytes(b'print("fixture")\n')
        (self.home / 'config.yaml').write_text(yaml.safe_dump({'agent': {'max_turns': 300}, 'updates': {'pre_update_backup': False}}))
        (self.repo / 'gateway/run_startup.py').write_text('async def _recover_unclean_sessions(self):\n    max_age = max(3600, int(_auto_continue_freshness_window()))\n')
        self.policy = {'base_commit': 'fixture', 'config': {'agent.max_turns': 300},
                       'soul_sections': [{'heading': '## Custom autonomy', 'sha256': hashlib.sha256(self.soul.strip().encode()).hexdigest()}],
                       'files': {'collector.py': hashlib.sha256((self.home / 'collector.py').read_bytes()).hexdigest()}}

    def run_check(self):
        return audit.check(self.home, self.repo, self.policy)

    def test_valid_is_readonly_and_never_claims_runtime_success(self):
        before = {str(p): p.read_bytes() for p in Path(self.tmp.name).rglob('*') if p.is_file()}
        result = self.run_check()
        self.assertTrue(result['static_passed'])
        self.assertFalse(result['runtime_verified'])
        self.assertTrue(any('backup is off' in w for w in result['warnings']))
        self.assertEqual(before, {str(p): p.read_bytes() for p in Path(self.tmp.name).rglob('*') if p.is_file()})

    def test_changed_parameter_is_detected_without_echoing_secret(self):
        (self.home / 'config.yaml').write_text('agent:\n  max_turns: SECRET_FIXTURE\n')
        result = self.run_check()
        self.assertFalse(result['static_passed'])
        self.assertNotIn('SECRET_FIXTURE', str(result))

    def test_removed_or_duplicate_behavior_section_is_detected(self):
        for text in ('## Other\n', self.soul + self.soul):
            (self.home / 'SOUL.md').write_text(text)
            self.assertFalse(self.run_check()['static_passed'])

    def test_script_change_or_missing_file_is_detected(self):
        (self.home / 'collector.py').write_text('changed')
        self.assertFalse(self.run_check()['static_passed'])
        (self.home / 'collector.py').unlink()
        self.assertFalse(self.run_check()['static_passed'])

    def test_parked_recovery_patch_is_detected(self):
        (self.repo / 'gateway/run_startup.py').write_text('async def _recover_unclean_sessions(self):\n    max_age = 3600\n')
        self.assertFalse(self.run_check()['static_passed'])

    def test_unparseable_configuration_is_detected(self):
        (self.home / 'config.yaml').write_text('agent: [')
        self.assertFalse(self.run_check()['static_passed'])


if __name__ == '__main__':
    unittest.main()
