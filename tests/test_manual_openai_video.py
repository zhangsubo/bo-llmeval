"""运行：python3 -m unittest discover -s tests -p 'test_manual_openai*.py' -v"""

import json
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.parse import unquote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'test-openai-api.py'
FAKE_KEY = 'test-only-redaction-marker'
VIDEO_BYTES = b'\x00\x00\x00\x18ftypmp42' + b'\x00' * (300 * 1024)


class VideoScriptTest(unittest.TestCase):
    def exercise(self, scenario, task_id=None, query_api='videos', extra_args=(), interactive_input=None):
        calls = []
        download_snapshots = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, value, content_type='application/json', location=None):
                payload = json.dumps(value).encode() if isinstance(value, dict) else value
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(payload)))
                if location:
                    self.send_header('Location', location)
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                calls.append(('POST', self.path, self.headers['Authorization'], body, self.headers.get('Content-Type')))
                if scenario == 'http_error':
                    return self.reply(401, {'error': {'message': FAKE_KEY}})
                if scenario == 'bad_json':
                    return self.reply(200, b'not json', 'text/plain')
                if scenario == 'missing_id':
                    return self.reply(200, {'status': 'queued'})
                if scenario == 'upstream_id_only':
                    return self.reply(200, {'task_id': 'upstream-other', 'status': 'queued'})
                if scenario == 'dual_id':
                    return self.reply(200, {'id': 'video-test', 'task_id': 'upstream-other', 'status': 'queued'})
                if scenario == 'wrapped_created_complete':
                    return self.reply(200, {'id': 'video-test', 'status': 'completed'})
                self.reply(200, {'id': 'video-test', 'status': 'queued'})

            def do_GET(self):
                calls.append(('GET', self.path, self.headers['Authorization'], None))
                if self.path == '/movie.mp4?sig=mock' or self.path.endswith('/content'):
                    paths = list(Path(folder).glob('*/result.json'))
                    download_snapshots.append(json.loads(paths[0].read_text()) if paths else {})
                if self.path == '/movie.mp4?sig=mock':
                    if scenario == 'wrapped_download_error':
                        return self.reply(403, {'error': 'expired ' + FAKE_KEY})
                    return self.reply(200, VIDEO_BYTES, 'application/octet-stream')
                if self.path.endswith('/content'):
                    assert self.headers.get('Range') is None
                    if scenario == 'redirect':
                        return self.reply(302, b'', 'text/plain', download_url)
                    if scenario == 'redirect_loop':
                        return self.reply(302, b'', 'text/plain', self.path)
                    if scenario == 'redirect_invalid':
                        return self.reply(302, b'', 'text/plain', 'file:///etc/passwd')
                    if scenario == 'content_error':
                        return self.reply(403, {'error': 'denied ' + FAKE_KEY})
                    if scenario == 'content_json':
                        return self.reply(200, {'message': 'not a video'})
                    if scenario == 'content_partial':
                        return self.reply(206, VIDEO_BYTES[:20], 'video/mp4')
                    if scenario == 'content_empty':
                        return self.reply(200, b'', 'video/mp4')
                    if scenario == 'content_invalid':
                        return self.reply(200, b'not really a video', 'video/mp4')
                    if scenario in ('content_truncated', 'content_slow'):
                        self.send_response(200)
                        self.send_header('Content-Type', 'video/mp4')
                        self.send_header('Content-Length', str(len(VIDEO_BYTES)))
                        self.end_headers()
                        self.wfile.write(VIDEO_BYTES[:20])
                        self.wfile.flush()
                        if scenario == 'content_slow':
                            time.sleep(0.3)
                        return
                    if scenario == 'content_after_deadline':
                        time.sleep(0.25)
                    return self.reply(200, VIDEO_BYTES, 'video/mp4')
                query_prefix = '/v1/videos/' if query_api == 'videos' else '/v1/video/generations/'
                if not self.path.startswith(query_prefix):
                    return self.reply(404, {'error': 'unexpected query path'})
                current_id = unquote(self.path.removeprefix(query_prefix))
                if scenario == 'unknown':
                    return self.reply(200, {'id': current_id, 'status': 'unknown'})
                if scenario == 'rate_limited':
                    return self.reply(429, {'error': {'message': 'Too many requests'}})
                if scenario == 'documented_failure':
                    return self.reply(200, {'id': current_id, 'status': 'failed', 'error': {'message': '素材处理失败'}})
                if scenario.startswith('wrapped_'):
                    if scenario == 'wrapped_error':
                        return self.reply(200, {'code': 'error', 'message': FAKE_KEY})
                    if scenario == 'wrapped_bad_data':
                        return self.reply(200, {'code': 'success', 'data': None})
                    if scenario == 'wrapped_pending' and len(calls) == 1:
                        return self.reply(200, {'code': 'success', 'data': {'task_id': current_id, 'status': 'IN_PROGRESS',
                                          'data': {'usage': {'total_tokens': 1}}}})
                    detail = {
                        'id': 21, 'quota': 1164674,
                        'task_id': 'different-task' if scenario == 'wrapped_mismatch' else current_id,
                        'status': 'FAILURE' if scenario == 'wrapped_failed' else 'SUCCESS',
                        'result_url': download_url, 'progress': '100%',
                        'data': {'id': 'upstream-task', 'model': 'mock-video-model',
                                 'usage': {'completion_tokens': 50638, 'total_tokens': 50638}}}
                    if scenario == 'wrapped_no_usage':
                        detail['data'].pop('usage')
                    if scenario == 'wrapped_invalid_usage':
                        detail['data']['usage'] = 'not an object'
                    if scenario == 'wrapped_partial_usage':
                        detail['data']['usage'] = {'completion_tokens': 0}
                    if scenario == 'wrapped_no_url':
                        detail.pop('result_url')
                    return self.reply(200, {'code': 'success', 'data': detail})
                if scenario == 'timeout':
                    return self.reply(200, {'id': current_id, 'status': 'in_progress'})
                if scenario == 'pending_once' and len(calls) == 1:
                    return self.reply(200, {'id': current_id, 'status': 'queued'})
                if scenario == 'poll_error':
                    return self.reply(500, {'error': {'code': 'convert_to_openai_video_failed', 'message': FAKE_KEY}})
                if scenario == 'failed':
                    return self.reply(200, {'id': current_id, 'status': 'failed', 'error': FAKE_KEY})
                self.reply(200, {'id': current_id, 'status': 'completed',
                                'metadata': {'url': 'https://cdn.example.com/movie.mp4?sig=mock'}})

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        download_server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        download_url = f'http://127.0.0.1:{download_server.server_port}/movie.mp4?sig=mock'
        download_thread = threading.Thread(target=download_server.serve_forever, daemon=True)
        download_thread.start()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                env = dict(os.environ, OPENAI_API_KEY=FAKE_KEY,
                           OPENAI_BASE_URL=f'http://127.0.0.1:{server.server_port}/v1/',
                           OPENAI_MODEL_ID='mock-video-model')
                command = [sys.executable, str(SCRIPT), '--output', folder, '--config', os.devnull,
                           '--poll-interval', '0.01', '--max-wait', '0.2']
                if query_api != 'generations':
                    command += ['--video-query-api', query_api]
                command += list(extra_args)
                if interactive_input is None and '--reference-video' not in extra_args:
                    command += ['--no-reference-video']
                if task_id:
                    command += ['--task-id', task_id]
                    env.pop('OPENAI_MODEL_ID', None)
                else:
                    command += ['--api-type', 'video', '--prompt', '一只小熊在花园里跳舞']
                result = subprocess.run(command, input=interactive_input if interactive_input is not None else ('' if task_id else '\n'),
                                        env=env, capture_output=True, text=True, timeout=10)
                reports = list(Path(folder).glob('*/result.json'))
                self.assertEqual(len(reports), 1, result.stderr)
                saved = reports[0].read_text()
                summary = reports[0].with_name('summary.txt').read_text()
                self.assertNotIn(FAKE_KEY, saved + summary + result.stdout + result.stderr)
                data = json.loads(saved)
                video = reports[0].with_name('video.mp4').resolve()
                self.assertFalse(video.with_suffix('.mp4.part').exists())
                if data.get('download', {}).get('status') == 'completed':
                    self.assertEqual(video.read_bytes(), VIDEO_BYTES)
                    self.assertEqual(video.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(data['download']['file_path'], str(video))
                    self.assertEqual(data['download']['bytes'], len(VIDEO_BYTES))
                    self.assertIn(str(video), summary)
                else:
                    self.assertFalse(video.exists())
                for call in calls:
                    if call[1] == '/movie.mp4?sig=mock':
                        self.assertIsNone(call[2])
                for snapshot in download_snapshots:
                    self.assertEqual(snapshot['outcome'], 'completed')
                    self.assertEqual(snapshot.get('usage'), data.get('usage'))
                    self.assertEqual(snapshot.get('result_url'), data.get('result_url'))
                if data.get('usage'):
                    self.assertIn('Token 用量（服务端上报）', summary)
                    if 'total_tokens' not in data['usage']:
                        self.assertIn('总 Token 数：未上报', summary)
                else:
                    self.assertIn('总 Token 数：未上报', summary)
                self.assertEqual(sum(c[0] == 'POST' for c in calls), 0 if task_id else 1)
                self.assertEqual(calls[0][2], 'Bearer ' + FAKE_KEY)
                if task_id:
                    self.assertTrue(data['query_only'])
                    self.assertEqual(data['task_id'], task_id)
                    self.assertIsNone(data['request_body'])
                    self.assertNotIn('create_ms', data)
                    self.assertNotIn('completion_ms', data)
                    self.assertIn('不代表视频生成总耗时', summary)
                    self.assertTrue(all(c[0] == 'GET' for c in calls))
                else:
                    self.assertEqual(calls[0][1], '/v1/videos')
                    self.assertEqual(calls[0][4], 'application/json')
                    self.assertEqual(json.loads(calls[0][3])['model'], 'mock-video-model')
                    self.assertEqual(json.loads(calls[0][3]), data['request_body'])
                    self.assertGreater(data['create_ms'], 0)
                return result.returncode, data, calls
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            download_server.shutdown()
            download_server.server_close()
            download_thread.join()

    def test_success_timing_and_returned_url(self):
        code, data, calls = self.exercise('success')
        self.assertEqual(code, 0)
        self.assertGreater(data['completion_ms'], data['create_ms'])
        self.assertEqual(data['returned_links'][0]['field'], '$.metadata.url')
        self.assertEqual(data['returned_links'][0]['url'], 'https://cdn.example.com/movie.mp4?sig=mock')
        self.assertEqual(len(calls), 3)
        self.assertEqual(data['download']['status'], 'completed')
        self.assertGreater(data['elapsed_ms'], data['completion_ms'])

    def test_redirect_downloads_without_forwarding_key(self):
        code, data, calls = self.exercise('redirect')
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 4)
        self.assertEqual(data['returned_links'][-1]['field'], 'header.Location')
        self.assertEqual(calls[-2][2], 'Bearer ' + FAKE_KEY)
        self.assertIsNone(calls[-1][2])

    def test_error_responses_are_saved_and_key_is_redacted(self):
        for scenario in ('http_error', 'bad_json', 'missing_id', 'upstream_id_only', 'failed'):
            with self.subTest(scenario=scenario):
                code, data, _ = self.exercise(scenario)
                self.assertEqual(code, 1)
                self.assertEqual(data['outcome'], 'failed')
                self.assertTrue(data['error'])

    def test_timeout_preserves_task_id(self):
        code, data, _ = self.exercise('timeout')
        self.assertEqual(code, 1)
        self.assertEqual(data['outcome'], 'timeout')
        self.assertEqual(data['task_id'], 'video-test')

    def test_content_error_does_not_erase_generation_success(self):
        code, data, _ = self.exercise('content_error')
        self.assertEqual(code, 2)
        self.assertEqual(data['outcome'], 'completed_download_failed')
        self.assertIn('completion_ms', data)

    def test_download_failures_preserve_completed_task(self):
        for scenario in ('content_json', 'content_partial', 'content_empty', 'content_invalid',
                         'content_truncated', 'redirect_loop', 'redirect_invalid', 'content_slow'):
            with self.subTest(scenario=scenario):
                code, data, calls = self.exercise(scenario, extra_args=['--download-timeout', '0.1'])
                self.assertEqual(code, 2)
                self.assertEqual(data['outcome'], 'completed_download_failed')
                self.assertEqual(data['last_status'], 'completed')
                self.assertEqual(data['task_id'], 'video-test')
                self.assertEqual(data['download']['status'], 'failed')
                self.assertTrue(data['download']['error'])
                if scenario == 'redirect_loop':
                    self.assertEqual(len(calls), 8)
                    self.assertTrue(all(c[2] is None for c in calls[3:]))

    def test_download_has_independent_deadline(self):
        code, data, _ = self.exercise('content_after_deadline')
        self.assertEqual(code, 0)
        self.assertGreater(data['download']['duration_ms'], 200)
        self.assertLess(data['completion_ms'], 200)

    def test_query_existing_task_never_creates_and_saves_results(self):
        for scenario, expected in (('success', 0), ('pending_once', 0), ('redirect', 0),
                                   ('content_error', 2), ('poll_error', 1), ('failed', 1), ('timeout', 1)):
            with self.subTest(scenario=scenario):
                code, data, calls = self.exercise(scenario, task_id='existing/task?x=1')
                self.assertEqual(code, expected)
                self.assertEqual(calls[0][1], '/v1/videos/existing%2Ftask%3Fx%3D1')
                if expected == 0:
                    self.assertGreater(data['query_completion_ms'], 0)
                    self.assertTrue(data['returned_links'])
                if scenario == 'poll_error':
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(data['requests'][0]['body']['error']['code'], 'convert_to_openai_video_failed')

    def test_generations_envelope_and_result_url(self):
        for scenario in ('wrapped_success', 'wrapped_pending', 'wrapped_error',
                         'wrapped_bad_data', 'wrapped_failed', 'wrapped_mismatch'):
            with self.subTest(scenario=scenario):
                code, data, calls = self.exercise(scenario, task_id='existing-task', query_api='generations')
                expected = 0 if scenario in ('wrapped_success', 'wrapped_pending') else 1
                self.assertEqual(code, expected)
                self.assertTrue(all(c[1] in ('/v1/video/generations/existing-task', '/movie.mp4?sig=mock') for c in calls))
                if expected == 0:
                    self.assertEqual(data['last_status'], 'success')
                    self.assertTrue(data['result_url'].endswith('/movie.mp4?sig=mock'))
                    self.assertEqual(data['returned_links'][0]['field'], '$.data.result_url')
                    self.assertEqual(data['download']['status'], 'completed')
                    self.assertIsNone(calls[-1][2])

    def test_creation_uses_generations_for_followup_query(self):
        code, data, calls = self.exercise('wrapped_success', query_api='generations')
        self.assertEqual(code, 0)
        self.assertEqual([(c[0], c[1]) for c in calls],
                         [('POST', '/v1/videos'), ('GET', '/v1/video/generations/video-test'),
                          ('GET', '/movie.mp4?sig=mock')])
        self.assertEqual(data['last_status'], 'success')

    def test_default_generations_records_usage_before_download(self):
        for scenario, task_id in (('wrapped_success', None), ('wrapped_pending', 'existing-task'),
                                  ('wrapped_created_complete', None)):
            with self.subTest(scenario=scenario):
                code, data, calls = self.exercise(scenario, task_id=task_id, query_api='generations')
                self.assertEqual(code, 0)
                self.assertEqual(data['video_query_api'], 'generations')
                self.assertEqual(data['usage'], {'completion_tokens': 50638, 'total_tokens': 50638})
                self.assertEqual(data['usage_source'], '$.data.data.usage')
                self.assertEqual(data['usage_status'], 'reported')
                self.assertEqual(data['model'], 'mock-video-model')
                self.assertFalse(any(c[1].endswith('/content') for c in calls))
                self.assertTrue(any('/video/generations/' in c[1] for c in calls))

    def test_missing_or_partial_usage_is_not_invented(self):
        for scenario in ('wrapped_no_usage', 'wrapped_invalid_usage', 'wrapped_partial_usage'):
            with self.subTest(scenario=scenario):
                code, data, _ = self.exercise(scenario, task_id='existing-task', query_api='generations')
                self.assertEqual(code, 0)
                if scenario == 'wrapped_partial_usage':
                    self.assertEqual(data['usage'], {'completion_tokens': 0})
                else:
                    self.assertIsNone(data['usage'])
                    self.assertIsNone(data['usage_source'])
                    self.assertEqual(data['usage_status'], 'not_reported')

    def test_generations_no_url_downloads_content_and_retains_usage(self):
        code, data, calls = self.exercise('wrapped_no_url', query_api='generations')
        self.assertEqual(code, 0)
        self.assertEqual(calls[-1][1], '/v1/videos/video-test/content')
        self.assertEqual(calls[-1][2], 'Bearer ' + FAKE_KEY)
        self.assertEqual(data['usage']['total_tokens'], 50638)

    def test_download_error_preserves_result_url_and_usage(self):
        code, data, calls = self.exercise('wrapped_download_error', task_id='existing-task', query_api='generations')
        self.assertEqual(code, 2)
        self.assertEqual(data['outcome'], 'completed_download_failed')
        self.assertTrue(data['result_url'].endswith('/movie.mp4?sig=mock'))
        self.assertEqual(data['usage']['total_tokens'], 50638)
        self.assertEqual(calls[-1][1], '/movie.mp4?sig=mock')
        self.assertIsNone(calls[-1][2])

    def test_guide_json_defaults_and_platform_id(self):
        code, data, calls = self.exercise('dual_id')
        self.assertEqual(code, 0)
        self.assertEqual(data['request_body'], {
            'model': 'mock-video-model', 'prompt': '一只小熊在花园里跳舞', 'seconds': 5,
            'resolution': '720p', 'ratio': '16:9', 'generate_audio': True, 'watermark': False})
        self.assertEqual(calls[1][1], '/v1/videos/video-test')
        self.assertEqual(calls[2][1], '/v1/videos/video-test/content')

    def test_guide_content_references_and_boolean_parameters(self):
        code, data, _ = self.exercise('success', extra_args=[
            '--duration', '6', '--resolution', '720p', '--ratio', '9:16',
            '--no-generate-audio', '--watermark',
            '--reference-image', 'https://example.com/image.jpg?signature=mock',
            '--reference-video', 'https://example.com/video.mp4'])
        self.assertEqual(code, 0)
        body = data['request_body']
        self.assertEqual(body['duration'], 6)
        self.assertNotIn('seconds', body)
        self.assertNotIn('size', body)
        self.assertNotIn('prompt', body)
        self.assertEqual(body['resolution'], '720p')
        self.assertEqual(body['ratio'], '9:16')
        self.assertIs(body['generate_audio'], False)
        self.assertIs(body['watermark'], True)
        self.assertEqual(body['content'], [
            {'type': 'text', 'text': '一只小熊在花园里跳舞'},
            {'type': 'image_url', 'image_url': {'url': 'https://example.com/image.jpg?signature=mock'}, 'role': 'reference_image'},
            {'type': 'video_url', 'video_url': {'url': 'https://example.com/video.mp4'}, 'role': 'reference_video'}])

    def test_content_text_only_and_seconds_override(self):
        code, data, _ = self.exercise('success', extra_args=['--content', '--seconds', '8'])
        self.assertEqual(code, 0)
        self.assertEqual(data['request_body']['content'], [{'type': 'text', 'text': '一只小熊在花园里跳舞'}])
        self.assertEqual(data['request_body']['seconds'], 8)

    def test_interactive_reference_video_is_sent_in_content(self):
        url = 'https://example.com/reference.mp4?sig=mock'
        code, data, _ = self.exercise('success', extra_args=['--resolution', '720p'],
                                     interactive_input='bad-choice\n2\n\n/tmp/video.mp4\nhttps://[\n' + url + '\n')
        self.assertEqual(code, 0)
        self.assertNotIn('prompt', data['request_body'])
        self.assertEqual(data['request_body']['content'], [
            {'type': 'text', 'text': '一只小熊在花园里跳舞'},
            {'type': 'video_url', 'video_url': {'url': url}, 'role': 'reference_video'}])

    def test_interactive_reference_video_defaults_to_no(self):
        code, data, _ = self.exercise('success', extra_args=['--resolution', '720p'], interactive_input='\n')
        self.assertEqual(code, 0)
        self.assertNotIn('content', data['request_body'])
        self.assertEqual(data['request_body']['prompt'], '一只小熊在花园里跳舞')

    def test_task_query_skips_reference_video_menu(self):
        code, _, calls = self.exercise('success', task_id='video-test', interactive_input='')
        self.assertEqual(code, 0)
        self.assertTrue(all(c[0] == 'GET' for c in calls))

    def test_guide_errors_preserve_response_and_stop(self):
        for scenario in ('unknown', 'rate_limited', 'documented_failure'):
            with self.subTest(scenario=scenario):
                code, data, calls = self.exercise(scenario, task_id='video-test')
                self.assertEqual(code, 1)
                self.assertEqual(len(calls), 1)
                if scenario == 'documented_failure':
                    self.assertEqual(data['fail_reason'], '素材处理失败')
                    self.assertIn('素材处理失败', data['error'])
                if scenario == 'rate_limited':
                    self.assertIn('--poll-interval', data['error'])


class ConfigSelectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('video_test_script', SCRIPT)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def profiles(self, text):
        with mock.patch.object(Path, 'read_text', return_value=text):
            return self.module.load_profiles(Path('mock.env'))

    def test_download_interruption_cleans_partial_and_preserves_existing_file(self):
        for interrupted in (True, False):
            with self.subTest(interrupted=interrupted), tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                destination = folder / 'video.mp4'
                if not interrupted:
                    destination.write_bytes(b'existing video')
                response = mock.MagicMock()
                response.__enter__.return_value = response
                response.code = 200
                response.headers = {'Content-Type': 'video/mp4', 'Content-Length': str(len(VIDEO_BYTES))}
                response.read1.side_effect = [VIDEO_BYTES, KeyboardInterrupt() if interrupted else b'']
                report = {'requests': [], 'returned_links': []}
                args = mock.Mock(download_timeout=300, request_timeout=120)
                with mock.patch.object(self.module.request, 'build_opener') as opener:
                    opener.return_value.open.return_value = response
                    with self.assertRaises(KeyboardInterrupt if interrupted else FileExistsError):
                        self.module.download_video(args, FAKE_KEY, 'https://example.com/video.mp4',
                                                   False, folder, report, lambda: None)
                self.assertFalse((folder / 'video.mp4.part').exists())
                self.assertEqual(report['download']['status'], 'interrupted' if interrupted else 'failed')
                if interrupted:
                    self.assertFalse(destination.exists())
                else:
                    self.assertEqual(destination.read_bytes(), b'existing video')

    def test_resolution_menu_sets_request_field_and_reprompts(self):
        for choice, expected in (('', '720p'), ('1', '720p'), ('2', '1080p'),
                                 ('3', '4k'), ('1080p', '1080p'), ('4K', '4k')):
            with self.subTest(choice=choice), \
                    mock.patch.object(sys, 'argv', [str(SCRIPT), '--api-type', 'video', '--prompt', '测试提示词', '--no-reference-video']), \
                    mock.patch.dict(os.environ, {'OPENAI_MODEL_ID': 'test-model'}, clear=True), \
                    mock.patch.object(self.module, 'load_profiles', return_value=[]), \
                    mock.patch.object(self.module, 'select_credentials', return_value=('fake-key', 'https://example.com/v1')), \
                    mock.patch('builtins.input', side_effect=['invalid', choice]) as prompt, \
                    mock.patch('sys.stdout', new_callable=io.StringIO), \
                    mock.patch.object(self.module, 'run', return_value=0) as run:
                self.assertEqual(self.module.main(), 0)
            self.assertEqual(prompt.call_count, 2)
            payload = self.module.video_payload(run.call_args.args[0], 'test-model')
            self.assertEqual(payload['resolution'], expected)
            self.assertNotIn('size', payload)

    def test_explicit_video_dimensions_and_task_query_skip_menu(self):
        for flags in (['--resolution', '4k'], ['--size', '1920x1080'], ['--task-id', 'existing-task']):
            with self.subTest(flags=flags), \
                    mock.patch.object(sys, 'argv', [str(SCRIPT), '--api-type', 'video', '--prompt', '测试提示词', '--no-reference-video'] + flags), \
                    mock.patch.dict(os.environ, {'OPENAI_MODEL_ID': 'test-model'}, clear=True), \
                    mock.patch.object(self.module, 'load_profiles', return_value=[]), \
                    mock.patch.object(self.module, 'select_credentials', return_value=('fake-key', 'https://example.com/v1')), \
                    mock.patch('builtins.input') as prompt, \
                    mock.patch.object(self.module, 'run', return_value=0) as run:
                self.assertEqual(self.module.main(), 0)
            prompt.assert_not_called()
            args = run.call_args.args[0]
            if flags[0] == '--size':
                payload = self.module.video_payload(args, 'test-model')
                self.assertEqual(payload['size'], '1920x1080')
                self.assertNotIn('resolution', payload)
            elif flags[0] == '--resolution':
                self.assertEqual(self.module.video_payload(args, 'test-model')['resolution'], '4k')

    def test_invalid_task_query_does_not_prompt_or_run(self):
        for flags in (['--task-id', ' '], ['--task-id', '..'],
                      ['--task-id', 'existing', '--api-type', 'responses']):
            with self.subTest(flags=flags), mock.patch.object(sys, 'argv', [str(SCRIPT)] + flags), \
                    mock.patch.object(self.module, 'select_credentials') as credentials, \
                    mock.patch.object(self.module, 'run') as run, \
                    mock.patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(self.module.main(), 1)
                credentials.assert_not_called()
                run.assert_not_called()

    def test_invalid_video_parameters_fail_before_credentials(self):
        for flags in (['--size', '1280x720', '--resolution', '720p'],
                      ['--seconds', '5', '--duration', '6'], ['--seconds', '0'],
                      ['--duration', 'nan'], ['--reference-image', '/tmp/image.jpg'],
                      ['--reference-video', 'file:///tmp/video.mp4'],
                      ['--reference-video', 'https://example.com/video.mp4', '--no-reference-video']):
            with self.subTest(flags=flags), mock.patch.object(sys, 'argv', [str(SCRIPT)] + flags), \
                    mock.patch.object(self.module, 'select_credentials') as credentials, \
                    mock.patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit) as exc:
                self.module.main()
            self.assertEqual(exc.exception.code, 2)
            credentials.assert_not_called()

    def test_pairs_quotes_literal_values_and_numeric_order(self):
        profiles = self.profiles('''
            NAME10="第二组" # comment
            BASE_URL10=https://second.example.com/v1/
            API_KEY10='literal-$VALUE#keep=me'
            export NAME2='第一组'
            BASE_URL2=https://first.example.com
            API_KEY2=test-first-marker # comment
        ''')
        self.assertEqual([p['name'] for p in profiles], ['第一组', '第二组'])
        self.assertEqual(profiles[1]['key'], 'literal-$VALUE#keep=me')
        self.assertEqual(profiles[0]['base'], 'https://first.example.com/v1')
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch('builtins.input', side_effect=['9', '2']), \
                mock.patch('sys.stdout', new_callable=io.StringIO) as output, \
                mock.patch.object(self.module.getpass, 'getpass') as password:
            key, base = self.module.select_credentials(profiles)
        self.assertEqual((key, base), (profiles[1]['key'], profiles[1]['base']))
        password.assert_not_called()
        for profile in profiles:
            self.assertNotIn(profile['key'], output.getvalue())

    def test_custom_does_not_reuse_file_or_environment_key(self):
        profiles = [{'name': '配置', 'base': 'https://saved.example.com/v1', 'key': 'saved-marker'}]
        with mock.patch.dict(os.environ, {'OPENAI_BASE_URL': 'https://env.example.com',
                                          'OPENAI_API_KEY': 'env-marker'}, clear=True), \
                mock.patch('builtins.input', side_effect=['0', 'https://custom.example.com']), \
                mock.patch.object(self.module.getpass, 'getpass', return_value='custom-marker'), \
                mock.patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(self.module.select_credentials(profiles),
                             ('custom-marker', 'https://custom.example.com/v1'))

    def test_incomplete_and_blank_groups_are_skipped_without_leaking(self):
        with mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            profiles = self.profiles('''
                NAME1="empty"
                BASE_URL1=
                API_KEY1=
                API_KEY2=do-not-show-this-marker
                BASE_URL3=https://third.example.com
            ''')
        self.assertEqual(profiles, [])
        self.assertNotIn('do-not-show-this-marker', output.getvalue())
        self.assertIn('跳过配置 2', output.getvalue())
        self.assertIn('跳过配置 3', output.getvalue())

    def test_missing_config_and_errors_do_not_echo_values(self):
        with mock.patch.object(Path, 'read_text', side_effect=FileNotFoundError):
            self.assertEqual(self.module.load_profiles(Path('missing.env')), [])
            with self.assertRaises(ValueError):
                self.module.load_profiles(Path('missing.env'), required=True)
        for text in ('API_KEY1="secret-marker', 'secret-marker',
                     'API_KEY1=secret-marker\nAPI_KEY1=duplicate'):
            with self.subTest(text_kind=text.split('=')[0]), self.assertRaises(ValueError) as context:
                self.profiles(text)
            self.assertNotIn('secret-marker', str(context.exception))

    def test_environment_pair_can_be_selected_without_mixing(self):
        profiles = [{'name': '配置', 'base': 'https://saved.example.com/v1', 'key': 'saved-marker'}]
        env = {'OPENAI_BASE_URL': 'https://env.example.com', 'OPENAI_API_KEY': 'env-marker'}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch('builtins.input', return_value='2'), \
                mock.patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(self.module.select_credentials(profiles), ('env-marker', 'https://env.example.com/v1'))
        with mock.patch.dict(os.environ, env, clear=True), mock.patch('builtins.input') as prompt:
            self.assertEqual(self.module.select_credentials([]), ('env-marker', 'https://env.example.com/v1'))
            prompt.assert_not_called()

    def test_main_uses_script_directory_and_explicit_paths(self):
        for mode in ('default', 'explicit'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                script = root / 'scripts' / 'test-openai-api.py'
                script.parent.mkdir()
                primary = script.with_name('openai-api-config')
                explicit = root / 'custom-config'
                for path, label in ((primary, 'default'), (explicit, 'explicit')):
                    path.write_text(f'BASE_URL1=https://{label}.example.com\nAPI_KEY1={label}-marker')
                argv = [str(script), '--api-type', 'responses']
                expected_output = script.resolve().parent / 'test-results' / 'responses'
                if mode == 'explicit':
                    expected_output = root / 'custom-results'
                    argv += ['--config', str(explicit), '--output', str(expected_output)]
                with mock.patch.object(self.module, '__file__', str(script)), \
                        mock.patch.dict(os.environ, {}, clear=True), \
                        mock.patch.object(sys, 'argv', argv), \
                        mock.patch('builtins.input', side_effect=['1', 'test-model', '自定义文本提示词']), \
                        mock.patch.object(self.module.getpass, 'getpass') as password, \
                        mock.patch('sys.stdout', new_callable=io.StringIO), \
                        mock.patch.object(self.module, 'run', return_value=0) as run:
                    self.assertEqual(self.module.main(), 0)
                password.assert_not_called()
                self.assertEqual(run.call_args.args[1:],
                                 (f'{mode}-marker', f'https://{mode}.example.com/v1', 'test-model'))
                self.assertEqual(run.call_args.args[0].output, expected_output)
                self.assertEqual(run.call_args.args[0].prompt, '自定义文本提示词')

    def test_main_passes_selected_pair_and_model_to_run(self):
        config = 'BASE_URL1=https://selected.example.com\nAPI_KEY1=selected-marker'
        with mock.patch.object(Path, 'read_text', return_value=config), \
                mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(sys, 'argv', [str(SCRIPT)]), \
                mock.patch('builtins.input', side_effect=['1', '3', '2', '1', 'test-model', '自定义视频提示词']), \
                mock.patch('sys.stdout', new_callable=io.StringIO), \
                mock.patch.object(self.module, 'run', return_value=0) as run:
            self.assertEqual(self.module.main(), 0)
        self.assertEqual(run.call_args.args[1:], ('selected-marker', 'https://selected.example.com/v1', 'test-model'))
        self.assertEqual(run.call_args.args[0].api_type, 'video')
        self.assertEqual(run.call_args.args[0].prompt, '自定义视频提示词')
        self.assertEqual(run.call_args.args[0].resolution, '1080p')
        self.assertIsNone(run.call_args.args[0].size)

    def test_empty_prompt_does_not_send_request(self):
        for flags in ([], ['--prompt', '   ']):
            with self.subTest(flags=flags), \
                    mock.patch.object(sys, 'argv', [str(SCRIPT), '--api-type', 'video', '--resolution', '720p', '--no-reference-video'] + flags), \
                    mock.patch.dict(os.environ, {'OPENAI_MODEL_ID': 'test-model'}, clear=True), \
                    mock.patch.object(self.module, 'load_profiles', return_value=[]), \
                    mock.patch.object(self.module, 'select_credentials', return_value=('fake-key', 'https://example.com/v1')), \
                    mock.patch('builtins.input', return_value='   ') as prompt, \
                    mock.patch.object(self.module, 'run') as run, \
                    mock.patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(self.module.main(), 1)
                run.assert_not_called()
                self.assertEqual(prompt.call_count, 0 if flags else 1)


if __name__ == '__main__':
    unittest.main()
