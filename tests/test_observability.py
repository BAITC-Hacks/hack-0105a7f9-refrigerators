import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from contractor_match.api import create_app
from contractor_match.service import recommend
from test_hardening import request


ROOT = Path(__file__).resolve().parent.parent


def events(capture):
    return [json.loads(record.getMessage()) for record in capture.records]


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"AI_PROVIDER": "local", "RANKING_PROVIDER": "tfidf",
                                    "OPENAI_API_KEY": "", "NVIDIA_API_KEY": "",
                                    "CORS_ALLOWED_ORIGINS": "http://localhost:5173"})
        env.start()
        self.addCleanup(env.stop)

    def test_request_headers_cover_success_errors_and_preflight(self):
        with TestClient(create_app()) as client:
            origin = {"Origin": "http://localhost:5173", "X-Request-ID": "private-client-value"}
            responses = [client.get('/health', headers=origin),
                         client.get('/private-path?secret=private-query', headers=origin),
                         client.get('/recommendations', headers=origin),
                         client.post('/recommendations', json={}, headers=origin)]
            with patch.dict(os.environ, {"AI_PROVIDER": "bad"}):
                responses.append(client.get('/health', headers=origin))
            preflight = client.options('/recommendations', headers={
                'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'POST',
                'Access-Control-Request-Headers': 'content-type'})
        self.assertEqual([r.status_code for r in responses], [200, 404, 405, 422, 503])
        for result in responses:
            self.assertIn('X-Request-ID', result.headers['access-control-expose-headers'])
            self.assertIn('X-Process-Time-Ms', result.headers['access-control-expose-headers'])
            self.assertEqual(result.headers['access-control-allow-origin'], 'http://localhost:5173')
        self.assertEqual(preflight.status_code, 200)
        responses.append(preflight)
        identifiers = [r.headers['x-request-id'] for r in responses]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        for result in responses:
            self.assertRegex(result.headers['x-request-id'], r'^[a-f0-9]{32}$')
            self.assertGreaterEqual(float(result.headers['x-process-time-ms']), 0)

    def test_parallel_requests_correlate_without_mixing_context(self):
        with TestClient(create_app()) as client, self.assertLogs('contractor_match', level='INFO') as logs:
            def send(index):
                value = request(date='2026-10-15' if index % 2 == 0 else '2026-12-19', brief=f'PRIVATE-{index}')
                return client.post('/recommendations', json=value.model_dump(mode='json'))
            with ThreadPoolExecutor(max_workers=4) as pool:
                responses = list(pool.map(send, range(8)))
        records = events(logs)
        identifiers = {r.headers['x-request-id'] for r in responses}
        self.assertEqual(len(identifiers), 8)
        self.assertNotIn('PRIVATE', str(logs.output))
        for response in responses:
            own = [row for row in records if row['request_id'] == response.headers['x-request-id']]
            self.assertEqual({row['event'] for row in own}, {'http_request', 'recommendation_complete'})
            selection = next(row for row in own if row['event'] == 'recommendation_complete')
            self.assertEqual(selection['counts'], response.json()['counts'])
            self.assertEqual(set(selection['stage_ms']), {'configuration', 'understanding', 'catalogue', 'scope',
                                                         'filters', 'alternatives', 'ranking', 'explanations'})
            self.assertTrue(all(value >= 0 for value in selection['stage_ms'].values()))
            self.assertEqual(next(row for row in own if row['event'] == 'http_request')['http_status'], 200)

    def test_empty_outcomes_and_direct_calls_have_truthful_independent_traces(self):
        with self.assertLogs('contractor_match', level='INFO') as logs:
            recommend(request(city='Астана', category='Декоратор'))
            recommend(request(city='Астана', category='Флорист', budget_kzt=100000))
        rows = events(logs)
        self.assertEqual([r['status'] for r in rows], ['category_absent', 'no_eligible'])
        self.assertNotEqual(rows[0]['request_id'], rows[1]['request_id'])
        for row in rows:
            self.assertEqual(row['ai_mode'], 'not_used')
            self.assertNotIn('ranking', row['stage_ms'])
            self.assertNotIn('explanations', row['stage_ms'])

    def test_provider_fallback_logs_share_id_and_elapsed_stage(self):
        async def timeout(*args, **kwargs):
            await asyncio.sleep(0.01)
            raise TimeoutError('PRIVATE-provider-response')
        with TestClient(create_app()) as client, patch.dict(os.environ, {
            'AI_PROVIDER': 'openai', 'OPENAI_API_KEY': 'PRIVATE-key'
        }), patch('contractor_match.explanations._call_openai', new_callable=AsyncMock, side_effect=timeout), self.assertLogs(
            'contractor_match', level='INFO'
        ) as logs:
            response = client.post('/recommendations', json=request(brief='PRIVATE-brief').model_dump(mode='json'))
        rows = events(logs)
        self.assertEqual({row['request_id'] for row in rows}, {response.headers['x-request-id']})
        fallback = next(row for row in rows if row['event'] == 'ai_fallback')
        self.assertEqual(fallback['reason'], 'timeout')
        summary = next(row for row in rows if row['event'] == 'recommendation_complete')
        self.assertGreaterEqual(summary['stage_ms']['explanations'], 10)
        self.assertEqual(summary['ai_reason'], response.json()['ai_reason'])
        self.assertNotIn('PRIVATE', str(logs.output))

    def test_internal_error_has_location_but_never_payload_or_source(self):
        def explode(*args, **kwargs):
            secret_local = 'PRIVATE-local'
            raise RuntimeError('PRIVATE-error-' + secret_local)
        with TestClient(create_app(), raise_server_exceptions=False) as client, patch(
            'contractor_match.service.select_order', side_effect=explode
        ), self.assertLogs('contractor_match', level='INFO') as logs:
            response = client.post('/recommendations?key=PRIVATE-query', json=request(brief='PRIVATE-brief').model_dump(mode='json'),
                                   headers={'Origin': 'http://localhost:5173', 'Authorization': 'PRIVATE-header',
                                            'X-Request-ID': 'PRIVATE-client-id'})
        rows = events(logs)
        self.assertEqual(response.status_code, 500)
        self.assertEqual({row['request_id'] for row in rows}, {response.headers['x-request-id']})
        error = next(row for row in rows if row['event'] == 'api_internal_error')
        frames = error['exception']['frames']
        self.assertTrue(any(f['function'] == 'explode' and f['line'] > 0 for f in frames))
        self.assertTrue(all(not Path(f['file']).is_absolute() for f in frames))
        failed = next(row for row in rows if row['event'] == 'recommendation_failed')
        self.assertEqual(failed['failed_stage'], 'ranking')
        self.assertIn('ranking', failed['stage_ms'])
        self.assertNotIn('PRIVATE', response.text + str(logs.output))
        self.assertNotIn('exception', response.json()['error'])
        self.assertEqual(response.headers['access-control-allow-origin'], 'http://localhost:5173')

    def test_cli_json_stays_clean_and_emits_correlated_summary_on_stderr(self):
        result = subprocess.run([sys.executable, '-m', 'contractor_match', 'recommend', '--city', 'Алматы',
                                 '--date', '2026-10-15', '--event-format', 'корпоратив', '--category', 'Ведущий',
                                 '--budget-kzt', '1200000', '--json'], cwd=ROOT, capture_output=True,
                                encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'matched')
        rows = [json.loads(line) for line in result.stderr.splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['event'], 'recommendation_complete')
        self.assertRegex(rows[0]['request_id'], r'^[a-f0-9]{32}$')


if __name__ == '__main__':
    unittest.main()
