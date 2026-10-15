"""方舟原生视频协议的本地 HTTP 集成测试；不调用供应商 API。"""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'test-openai-api.py'
FAKE_KEY = 'seedance-test-only-redaction-marker'
TASKS = '/api/v3/contents/generations/tasks'
VIDEO = b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 64


class SeedanceTest(unittest.TestCase):
    def exercise(self, flags=(), scenario='success', interactive=None, full_endpoint=False):
        calls = []
        polls = 0

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, value):
                body = json.dumps(value).encode() if isinstance(value, dict) else value
                self.send_response(status)
                self.send_header('Content-Type', 'application/json' if isinstance(value, dict) else 'video/mp4')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def record(self, body=None):
                calls.append((self.command, self.path, self.headers.get('Authorization'), body))

            def do_POST(self):
                self.record(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                if scenario == 'create_error':
                    return self.reply(200, {'id': 'cgt-test', 'error': {'message': FAKE_KEY}})
                if scenario == 'missing_id':
                    return self.reply(200, {'task_id': 'upstream-id'})
                # 方舟创建成功仅返回 id，没有 status。
                self.reply(200, {'id': 'cgt-test'})

            def do_GET(self):
                nonlocal polls
                self.record()
                if self.path == '/video.mp4':
                    return self.reply(200, VIDEO)
                if urlsplit(self.path).path == TASKS:
                    if scenario == 'invalid_list':
                        return self.reply(200, {'items': [None], 'total': 1})
                    return self.reply(200, {'items': [{'id': 'cgt-test', 'model': 'mock-seedance',
                                                     'status': 'queued'}], 'total': 12})
                polls += 1
                if scenario == 'http_error':
                    return self.reply(429, {'error': {'message': FAKE_KEY}})
                if scenario == 'poll_error':
                    return self.reply(200, {'error': {'message': FAKE_KEY}})
                status = 'succeeded'
                if scenario in ('queued', 'timeout') or (scenario == 'pending_once' and polls == 1):
                    status = 'queued'
                elif scenario in ('failed', 'cancelled', 'expired', 'unknown'):
                    status = scenario
                state = {'id': 'different' if scenario == 'mismatch' else 'cgt-test',
                         'status': status, 'model': 'mock-seedance',
                         'content': {'video_url': f'http://127.0.0.1:{server.server_port}/video.mp4'},
                         'usage': {'completion_tokens': 42, 'total_tokens': 42}}
                if scenario == 'missing_url':
                    state['content'] = {}
                if scenario == 'no_usage':
                    state.pop('usage')
                if scenario == 'failed':
                    state['error'] = {'code': 'failed', 'message': FAKE_KEY}
                self.reply(200, state)

            def do_DELETE(self):
                self.record()
                if scenario == 'delete_error':
                    return self.reply(400, {'error': {'message': FAKE_KEY}})
                self.reply(204 if scenario == 'empty_delete' else 200,
                           b'' if scenario == 'empty_delete' else {})

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                env = dict(os.environ, OPENAI_API_KEY=FAKE_KEY, OPENAI_MODEL_ID='mock-seedance',
                           OPENAI_BASE_URL=f'http://127.0.0.1:{server.server_port}' +
                           (TASKS if full_endpoint else '/api/v3'))
                command = [sys.executable, '-B', str(SCRIPT), '--config', os.devnull,
                           '--output', folder, '--poll-interval', '0.01', '--max-wait', '0.15']
                if interactive is None:
                    command += ['--api-type', 'seedance']
                command += list(flags)
                result = subprocess.run(command, input=interactive or '', env=env,
                                        text=True, capture_output=True, timeout=10)
                reports = list(Path(folder).glob('*/result.json'))
                report = json.loads(reports[0].read_text()) if reports else None
                saved = ''.join(p.read_text() + p.with_name('summary.txt').read_text() for p in reports)
                self.assertNotIn(FAKE_KEY, saved + result.stdout + result.stderr)
                if report and report.get('download', {}).get('status') == 'completed':
                    self.assertEqual(reports[0].with_name('video.mp4').read_bytes(), VIDEO)
                return result, calls, report
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def create_flags(self):
        return ['--action', 'create', '--prompt', '小猫在花园散步', '--resolution', '720p', '--no-reference']

    def test_create_id_only_poll_usage_and_unauthenticated_download(self):
        result, calls, report = self.exercise(self.create_flags(), full_endpoint=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([(x[0], x[1]) for x in calls],
                         [('POST', TASKS), ('GET', TASKS + '/cgt-test'), ('GET', '/video.mp4')])
        payload = calls[0][3]
        self.assertEqual(payload['content'], [{'type': 'text', 'text': '小猫在花园散步'}])
        self.assertEqual(payload['duration'], 5)
        self.assertIs(type(payload['duration']), int)
        self.assertTrue(payload['generate_audio'])
        self.assertFalse(payload['watermark'])
        for field in ('prompt', 'seconds', 'size'):
            self.assertNotIn(field, payload)
        self.assertEqual(calls[0][2], 'Bearer ' + FAKE_KEY)
        self.assertIsNone(calls[-1][2])
        self.assertEqual(report['usage_source'], '$.usage')
        self.assertEqual(report['usage']['total_tokens'], 42)
        self.assertNotIn('content_endpoint', report)

    def test_create_without_wait_submits_once(self):
        result, calls, report = self.exercise(self.create_flags() + ['--no-wait'])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(report['outcome'], 'submitted')
        self.assertEqual(report['task_id'], 'cgt-test')
        self.assertNotIn('completion_ms', report)

    def test_multimodal_and_first_last_frames(self):
        for media, roles in [
            (['--reference-image', 'asset://asset-img', '--reference-video', 'https://example.com/v.mp4',
              '--reference-audio', 'asset://asset-audio'], ['reference_image', 'reference_video', 'reference_audio']),
            (['--first-frame', 'asset://asset-first', '--last-frame', 'asset://asset-last'],
             ['first_frame', 'last_frame']),
        ]:
            with self.subTest(roles=roles):
                result, calls, _ = self.exercise(self.create_flags()[:-1] + media + ['--no-wait', '--seconds', '6'])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(calls[0][3]['duration'], 6)
                self.assertEqual([x['role'] for x in calls[0][3]['content'][1:]], roles)

    def test_query_no_wait_and_task_id_encoding(self):
        result, calls, report = self.exercise(['--task-id', 'cgt-test', '--no-wait'], 'queued')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(report['outcome'], 'queued')
        self.assertTrue(report['query_only'])
        self.assertNotIn('create_ms', report)
        _, calls, _ = self.exercise(['--task-id', 'a/b ?#', '--no-wait'])
        self.assertEqual(calls[0][1], TASKS + '/a%2Fb%20%3F%23')

    def test_query_polls_then_downloads(self):
        result, calls, report = self.exercise(['--task-id', 'cgt-test'], 'pending_once')
        self.assertEqual(result.returncode, 0)
        self.assertEqual([c[0] for c in calls], ['GET', 'GET', 'GET'])
        self.assertIn('query_completion_ms', report)
        self.assertNotIn('completion_ms', report)

    def test_list_pagination_filters_and_no_generation_prompts(self):
        result, calls, report = self.exercise(['--action', 'list', '--page-num', '2', '--page-size', '3',
            '--filter-status', 'queued', '--filter-model', 'ep-test',
            '--filter-task-id', 'cgt-a', '--filter-task-id', 'cgt-b'])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 'GET')
        self.assertEqual(parse_qs(urlsplit(calls[0][1]).query), {
            'page_num': ['2'], 'page_size': ['3'], 'filter.status': ['queued'],
            'filter.model': ['ep-test'], 'filter.task_ids': ['cgt-a', 'cgt-b']})
        self.assertEqual(report['total'], 12)
        self.assertIn('cgt-test | queued', result.stdout)
        for prompt in ('Model ID：', '提示词：', '请选择视频分辨率', '请选择参考素材'):
            self.assertNotIn(prompt, result.stdout)

    def test_delete_json_or_empty_success_and_http_failure(self):
        for scenario, code in [('success', 0), ('empty_delete', 0), ('delete_error', 1)]:
            with self.subTest(scenario=scenario):
                result, calls, _ = self.exercise(['--action', 'delete', '--task-id', 'cgt-test'], scenario)
                self.assertEqual(result.returncode, code)
                self.assertEqual([(c[0], c[1]) for c in calls], [('DELETE', TASKS + '/cgt-test')])

    def test_interactive_query_list_delete(self):
        for choice in ('2', '3', '4'):
            with self.subTest(choice=choice):
                text = '4\n' + choice + '\n' + ('cgt-test\n' if choice != '3' else '')
                result, calls, _ = self.exercise(interactive=text)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn('POST', [c[0] for c in calls])

    def test_failures_save_report_and_do_not_fallback(self):
        for scenario in ('failed', 'cancelled', 'expired', 'unknown', 'mismatch', 'poll_error', 'http_error', 'timeout'):
            with self.subTest(scenario=scenario):
                result, calls, report = self.exercise(['--task-id', 'cgt-test'], scenario)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(report['task_id'], 'cgt-test')
                self.assertTrue(all(c[0] == 'GET' and c[1] == TASKS + '/cgt-test' for c in calls))
                if scenario != 'timeout':
                    self.assertEqual(len(calls), 1)

    def test_create_errors_never_poll_or_retry(self):
        for scenario in ('create_error', 'missing_id'):
            with self.subTest(scenario=scenario):
                result, calls, report = self.exercise(self.create_flags(), scenario)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(len(calls), 1)
                self.assertEqual(report['outcome'], 'failed')

    def test_missing_url_is_download_failure_without_fallback(self):
        result, calls, report = self.exercise(['--task-id', 'cgt-test'], 'missing_url')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(report['outcome'], 'completed_download_failed')

    def test_missing_usage_not_estimated(self):
        result, _, report = self.exercise(['--task-id', 'cgt-test'], 'no_usage')
        self.assertEqual(result.returncode, 0)
        self.assertIsNone(report['usage'])
        self.assertEqual(report['usage_status'], 'not_reported')

    def test_invalid_list_response_is_failure(self):
        result, calls, report = self.exercise(['--action', 'list'], 'invalid_list')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(report['outcome'], 'failed')

    def test_invalid_arguments_do_not_send_requests(self):
        for flags in (
            ['--action', 'query'], ['--action', 'delete'], ['--action', 'create', '--task-id', 'cgt-test'],
            ['--action', 'list', '--task-id', 'cgt-test'], ['--size', '1280x720'], ['--duration', '5.5'],
            ['--stream'], ['--page-num', '0'], ['--page-size', '0'], ['--filter-status', 'queued'],
            ['--last-frame', 'asset://asset-last'], ['--first-frame', 'asset://asset-first', '--no-reference'],
            ['--first-frame', 'asset://asset-first', '--reference-image', 'asset://asset-img'],
            ['--task-id', '..'], ['--video-query-api', 'videos'],
        ):
            with self.subTest(flags=flags):
                result, calls, report = self.exercise(flags)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])
                self.assertIsNone(report)

    def test_base_url_normalization(self):
        spec = importlib.util.spec_from_file_location('manual_seedance', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for suffix in ('', '/api/v3', TASKS + '/'):
            self.assertEqual(module.normalize_base('https://ark.cn-beijing.volces.com' + suffix),
                             'https://ark.cn-beijing.volces.com/api/v3')
        self.assertEqual(module.normalize_base('https://example.com/custom/api/v3/contents/generations/tasks'),
                         'https://example.com/custom/api/v3')


if __name__ == '__main__':
    unittest.main()
