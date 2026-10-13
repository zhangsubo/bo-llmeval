"""三种接口选择与文本接口的本地 HTTP 集成验证，不调用真实 API。"""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
import io


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'test-openai-api.py'
FAKE_KEY = 'text-test-only-redaction-marker'


class TextInterfacesTest(unittest.TestCase):
    def exercise(self, api_type, scenario='success', menu=False, full_endpoint=False):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                calls.append(('GET', self.path))
                self.send_error(405)

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append(('POST', self.path, self.headers['Authorization'],
                              self.headers['Content-Type'], payload))
                status = 200
                if scenario == 'timeout':
                    time.sleep(0.15)
                if scenario in ('http_error', 'api_error'):
                    status = 429 if scenario == 'http_error' else 200
                    value = {'error': {'message': FAKE_KEY}}
                elif api_type == 'chat/completions':
                    value = {'id': 'chat-test', 'choices': [{'message': {'role': 'assistant',
                             'content': '你好 ' + FAKE_KEY},
                             'finish_reason': 'length' if scenario == 'incomplete' else 'stop'}],
                             'usage': {'prompt_tokens': 8, 'completion_tokens': 3, 'total_tokens': 11}}
                else:
                    value = {'id': 'response-test',
                             'status': 'incomplete' if scenario == 'incomplete' else 'completed',
                             'output': [{'type': 'reasoning', 'summary': []},
                                        {'type': 'message', 'role': 'assistant', 'content': [
                                            {'type': 'output_text', 'text': '你好 ' + FAKE_KEY,
                                             'annotations': [{'url': 'https://example.com/source'}]}]}],
                             'usage': {'input_tokens': 8, 'output_tokens': 3, 'total_tokens': 11}}
                if scenario == 'wrong_shape':
                    value = {'message': 'not an OpenAI response'}
                if scenario == 'pending':
                    value = {'id': 'response-test', 'status': 'in_progress', 'output': []}
                if scenario == 'bad_json':
                    body = b'<html>not JSON</html>'
                else:
                    body = json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                base = f'http://127.0.0.1:{server.server_port}/v1'
                if full_endpoint:
                    base += '/videos'  # 切换接口时也可复用原来带视频路径的配置。
                env = dict(os.environ, OPENAI_API_KEY=FAKE_KEY, OPENAI_BASE_URL=base,
                           OPENAI_MODEL_ID='mock-text-model')
                command = [sys.executable, str(SCRIPT), '--config', os.devnull, '--output', directory, '--no-stream',
                           '--prompt', '请回复你好', '--request-timeout', '0.05' if scenario == 'timeout' else '2']
                if not menu:
                    command += ['--api-type', api_type]
                result = subprocess.run(command, env=env, input='1\n' if api_type == 'chat/completions' else '2\n',
                                        capture_output=True, text=True, timeout=10)
                reports = list(Path(directory).glob('*/result.json'))
                self.assertEqual(len(reports), 1, result.stdout + result.stderr)
                raw = reports[0].read_text()
                summary = reports[0].with_name('summary.txt').read_text()
                self.assertNotIn(FAKE_KEY, raw + summary + result.stdout + result.stderr)
                data = json.loads(raw)
                self.assertEqual(len(calls), 1, calls)
                self.assertEqual(calls[0][:4], ('POST', '/v1/' + api_type, 'Bearer ' + FAKE_KEY, 'application/json'))
                expected = {'model': 'mock-text-model', 'stream': False}
                if api_type == 'chat/completions':
                    expected['messages'] = [{'role': 'user', 'content': '请回复你好'}]
                else:
                    expected['input'] = '请回复你好'
                self.assertEqual(calls[0][4], expected)
                self.assertEqual(data['mode'], api_type)
                self.assertNotIn('content_endpoint', data)
                self.assertNotIn('轮询间隔', summary)
                self.assertGreater(data['request_ms'], 0)
                return result.returncode, data, summary
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_both_text_protocols_save_text_usage_and_timings(self):
        for api_type in ('chat/completions', 'responses'):
            with self.subTest(api_type=api_type):
                code, data, summary = self.exercise(api_type, full_endpoint=True)
                self.assertEqual(code, 0)
                self.assertEqual(data['outcome'], 'completed')
                self.assertIn('你好 [REDACTED]', summary)
                self.assertEqual(data['usage']['total_tokens'], 11)
                self.assertGreaterEqual(data['request_ms'], data['requests'][0]['first_body_byte_ms'])
                if api_type == 'responses':
                    self.assertEqual(data['returned_links'][0]['url'], 'https://example.com/source')

    def test_interactive_menu_dispatches_correct_endpoint(self):
        for api_type in ('chat/completions', 'responses'):
            with self.subTest(api_type=api_type):
                self.assertEqual(self.exercise(api_type, menu=True)[0], 0)

    def test_errors_and_invalid_json_are_saved(self):
        for api_type in ('chat/completions', 'responses'):
            for scenario in ('http_error', 'api_error', 'bad_json', 'wrong_shape'):
                with self.subTest(api_type=api_type, scenario=scenario):
                    code, data, _ = self.exercise(api_type, scenario)
                    self.assertEqual(code, 1)
                    self.assertEqual(data['outcome'], 'failed')
                    self.assertTrue(data['error'])

    def test_truncated_outputs_are_preserved_but_not_marked_complete(self):
        for api_type in ('chat/completions', 'responses'):
            with self.subTest(api_type=api_type):
                code, data, summary = self.exercise(api_type, 'incomplete')
                self.assertEqual(code, 2)
                self.assertEqual(data['outcome'], 'incomplete')
                self.assertIn('你好', data['response_text'])
                self.assertIn('未完成', summary)

    def test_responses_pending_is_not_a_success(self):
        code, data, _ = self.exercise('responses', 'pending')
        self.assertEqual(code, 1)
        self.assertEqual(data['outcome'], 'failed')

    def test_network_timeout_preserves_request_timing(self):
        code, data, _ = self.exercise('chat/completions', 'timeout')
        self.assertEqual(code, 1)
        self.assertEqual(data['outcome'], 'timeout')
        self.assertIn('error', data['requests'][0])


class InterfaceInputTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('openai_interface_script', SCRIPT)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_menu_default_and_retry(self):
        for choices, expected in (([''], 'video'), (['invalid', '2'], 'responses')):
            with mock.patch('builtins.input', side_effect=choices), mock.patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(self.module.select_api_type(), expected)

    def test_all_endpoint_suffixes_preserve_custom_prefix(self):
        for suffix in ('', '/chat/completions', '/responses', '/videos'):
            self.assertEqual(self.module.normalize_base('https://example.com/gateway/v1' + suffix + '/'),
                             'https://example.com/gateway/v1')


if __name__ == '__main__':
    unittest.main()
