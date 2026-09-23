import json
from importlib import metadata
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from contractor_match.doctor import ROOT, run_checks


class DoctorTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'AI_PROVIDER': 'local', 'RANKING_PROVIDER': 'tfidf',
                                    'OPENAI_API_KEY': '', 'NVIDIA_API_KEY': '',
                                    'CORS_ALLOWED_ORIGINS': 'http://localhost:5173'})
        env.start()
        self.addCleanup(env.stop)

    def test_offline_checks_real_data_without_network(self):
        with patch('socket.create_connection', side_effect=AssertionError('network called')):
            report = run_checks('http://localhost:5173')
        checks = {item['name']: item for item in report['checks']}
        self.assertEqual(report['status'], 'ok')
        self.assertFalse(report['live_provider_checked'])
        self.assertEqual(checks['catalogue']['profiles'], 66)
        self.assertEqual(checks['catalogue']['synthetic'], 13)
        self.assertTrue(checks['cors']['frontend_allowed'])
        self.assertEqual(checks['index']['status'], 'ok')

    def test_key_presence_is_distinct_from_live_access(self):
        for provider, key, expected in [('auto', '', 'warning'), ('openai', '', 'warning'),
                                         ('nvidia', '', 'warning'), ('openai', 'PRIVATE-key', 'ok')]:
            with self.subTest(provider=provider, key_present=bool(key)), patch.dict(os.environ, {
                'AI_PROVIDER': provider, 'OPENAI_API_KEY': key,
            }):
                report = run_checks()
                ai = next(row for row in report['checks'] if row['name'] == 'ai')
                self.assertEqual(ai['status'], expected)
                self.assertFalse(report['live_provider_checked'])
                self.assertNotIn('PRIVATE', json.dumps(report))

    def test_invalid_provider_nim_url_and_cors_are_safe_errors(self):
        for values, check in [({'AI_PROVIDER': 'PRIVATE-mistake'}, 'ai'),
                              ({'AI_PROVIDER': 'brev', 'NIM_MODEL': 'test',
                                'NIM_BASE_URL': 'https://example.org/v1?key=PRIVATE-token'}, 'ai'),
                              ({'CORS_ALLOWED_ORIGINS': 'https://PRIVATE-user@example.org'}, 'cors')]:
            with self.subTest(check=check), patch.dict(os.environ, values):
                report = run_checks()
                self.assertEqual(next(row for row in report['checks'] if row['name'] == check)['status'], 'error')
                self.assertNotIn('PRIVATE', json.dumps(report))
        report = run_checks('http://localhost:3000')
        self.assertEqual(report['status'], 'error')
        self.assertFalse(next(row for row in report['checks'] if row['name'] == 'cors')['frontend_allowed'])

    def test_missing_dependency_and_version_drift_are_distinguished(self):
        original = metadata.version
        def missing(name):
            if name == 'pydantic':
                raise metadata.PackageNotFoundError(name)
            return original(name)
        with patch('contractor_match.doctor.metadata.version', side_effect=missing):
            report = run_checks()
        self.assertEqual(report['status'], 'error')
        self.assertEqual(next(row for row in report['checks'] if row['name'] == 'catalogue')['status'], 'skipped')
        with patch('contractor_match.doctor.metadata.version', side_effect=lambda name: '0.0.0' if name == 'fastapi' else original(name)):
            report = run_checks()
        self.assertEqual(report['status'], 'warning')
        self.assertEqual(next(row for row in report['checks'] if row['name'] == 'dependencies')['different_versions'], ['fastapi'])

    def test_corrupt_catalogue_is_checked_from_disk_without_exposing_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'data').mkdir()
            (root / 'data' / 'contractors.csv').write_text('PRIVATE-broken-csv', encoding='utf-8')
            with patch('contractor_match.doctor.ROOT', root), patch('contractor_match.doctor._dependencies', return_value=('ok', 'ok', {})):
                report = run_checks()
        self.assertEqual(report['status'], 'error')
        self.assertEqual(next(row for row in report['checks'] if row['name'] == 'catalogue')['status'], 'error')
        self.assertNotIn('PRIVATE', json.dumps(report))

    def test_cli_runs_even_without_site_packages_and_uses_meaningful_exit_codes(self):
        for flags, env, expected in [([], os.environ.copy(), 0),
                                     ([], dict(os.environ, AI_PROVIDER='bad'), 1),
                                     (['-S'], os.environ.copy(), 1)]:
            with self.subTest(flags=flags, expected=expected):
                result = subprocess.run([sys.executable, *flags, '-m', 'contractor_match', 'doctor', '--json'],
                                        cwd=ROOT, env=env, capture_output=True, encoding='utf-8', timeout=15)
                report = json.loads(result.stdout)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertFalse(report['live_provider_checked'])
                self.assertNotIn('Traceback', result.stderr)
                if flags:
                    self.assertEqual(next(row for row in report['checks'] if row['name'] == 'dependencies')['status'], 'error')


if __name__ == '__main__':
    unittest.main()
