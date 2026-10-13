"""本地 SSE 服务覆盖手工诊断脚本的流式请求、计时和失败留存。"""

import importlib.util
import io
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


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'test-openai-api.py'
FAKE_KEY = 'stream-test-redaction-marker'


def frame(data, event=None, multiline=False):
    value = json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else data
    if multiline:
        value = value.replace(', ', ',\r\ndata: ', 1)
    prefix = f'event: {event}\r\n' if event else ''
    return (prefix + 'data: ' + value + '\r\n\r\n').encode()


class StreamingProbeTest(unittest.TestCase):
    def exercise(self, api_type, scenario='success'):
        calls = []
        delays = []
        if api_type == 'chat/completions':
            chunks = [frame({'id': 'chat-stream', 'choices': [{'index': 0, 'delta': {'role': 'assistant'}}]})]
            delta = lambda content: frame({'id': 'chat-stream', 'choices': [{'index': 0, 'delta': {'content': content}}]})
            ending = [frame({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'length' if scenario == 'incomplete' else 'stop'}]}),
                      frame({'choices': [], 'usage': {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5}}, multiline=True),
                      frame('[DONE]')]
        else:
            chunks = [frame({'type': 'response.created', 'response': {'id': 'response-stream'}}, 'response.created')]
            delta = lambda content: frame({'type': 'response.output_text.delta', 'delta': content,
                                           'output_index': 0, 'content_index': 0}, 'response.output_text.delta')
            status = 'incomplete' if scenario == 'incomplete' else 'completed'
            ending = [frame({'type': 'response.' + status, 'response': {
                'id': 'response-stream', 'status': status,
                'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '你好'}]}],
                'usage': {'input_tokens': 3, 'output_tokens': 2, 'total_tokens': 5}}}, 'response.' + status, multiline=True)]
        chunks += [b': heartbeat\r\n\r\n', delta('你'), delta('好')]
        if scenario == 'error':
            chunks += [frame({'error': {'message': FAKE_KEY}}, 'error')]
        elif scenario == 'invalid_json':
            chunks += [frame('{oops')]
        elif scenario == 'timeout':
            chunks += [b': still working\n\n']
        elif scenario != 'truncated':
            chunks += ending
        if scenario == 'unicode_split':
            index = chunks[2].index('你'.encode())
            chunks[2:3] = [chunks[2][:index + 1], chunks[2][index + 1:]]
        if scenario == 'cr_only':
            chunks = [item.replace(b'\r\n', b'\r') for item in chunks]
        if scenario == 'split_key':
            # 完整文本和原始事件均不得泄露完整 key；分片不会打印到终端。
            chunks[2:4] = [delta(FAKE_KEY[:9]), delta(FAKE_KEY[9:])]
        if scenario == 'same_packet':
            chunks = [b''.join(chunks)]

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append((self.path, self.headers['Accept'], self.headers['Authorization'], body))
                self.send_response(401 if scenario == 'http_error' else 200)
                self.send_header('Content-Type', 'application/json' if scenario in ('http_error', 'ignored_stream') else 'text/event-stream; charset=utf-8')
                self.end_headers()
                if scenario in ('http_error', 'ignored_stream'):
                    self.wfile.write(json.dumps({'error': FAKE_KEY} if scenario == 'http_error' else {'choices': []}).encode())
                    return
                try:
                    for i, chunk in enumerate(chunks):
                        if i == 2:
                            time.sleep(0.04)  # 首个数据包是角色/心跳，不应成为 TTFT。
                        if scenario == 'timeout' and i == len(chunks) - 1:
                            time.sleep(0.2)
                        self.wfile.write(chunk)
                        self.wfile.flush()
                        delays.append(time.perf_counter())
                        time.sleep(0.002)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                env = dict(os.environ, OPENAI_API_KEY=FAKE_KEY,
                           OPENAI_BASE_URL=f'http://127.0.0.1:{server.server_port}', OPENAI_MODEL_ID='mock-stream')
                command = [sys.executable, str(SCRIPT), '--config', os.devnull, '--output', folder,
                           '--api-type', api_type, '--prompt', '请回复你好',
                           '--request-timeout', '0.09' if scenario == 'timeout' else '2']
                result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=10)
                paths = list(Path(folder).glob('*/result.json'))
                self.assertEqual(len(paths), 1, result.stdout + result.stderr)
                raw = paths[0].read_text()
                summary = paths[0].with_name('summary.txt').read_text()
                self.assertNotIn(FAKE_KEY, raw + summary + result.stdout + result.stderr)
                data = json.loads(raw)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0][:3], ('/v1/' + api_type, 'text/event-stream', 'Bearer ' + FAKE_KEY))
                self.assertIs(calls[0][3]['stream'], True)
                if api_type == 'chat/completions':
                    self.assertEqual(calls[0][3]['stream_options'], {'include_usage': True})
                else:
                    self.assertNotIn('stream_options', calls[0][3])
                return result.returncode, data, summary
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_stream_success_and_ttft_excludes_heartbeat_and_role(self):
        for api_type in ('chat/completions', 'responses'):
            with self.subTest(api_type=api_type):
                code, data, summary = self.exercise(api_type)
                self.assertEqual(code, 0)
                self.assertEqual(data['response_text'], '你好')
                self.assertTrue(data['stream_completed'])
                self.assertEqual(data['usage']['total_tokens'], 5)
                self.assertGreater(data['ttft_ms'], data['requests'][0]['first_body_byte_ms'] + 20)
                self.assertGreater(data['request_ms'], data['ttft_ms'])
                self.assertEqual(len(data['text_delta_times_ms']), 2)
                self.assertIn('TTFT', summary)

    def test_utf8_boundaries_cr_lines_and_multiple_events_per_read(self):
        for api_type in ('chat/completions', 'responses'):
            for scenario in ('unicode_split', 'cr_only', 'same_packet'):
                with self.subTest(api_type=api_type, scenario=scenario):
                    code, data, _ = self.exercise(api_type, scenario)
                    self.assertEqual(code, 0)
                    self.assertEqual(data['response_text'], '你好')

    def test_failed_and_truncated_streams_keep_partial_text(self):
        for api_type in ('chat/completions', 'responses'):
            for scenario in ('error', 'invalid_json', 'truncated', 'timeout'):
                with self.subTest(api_type=api_type, scenario=scenario):
                    code, data, _ = self.exercise(api_type, scenario)
                    self.assertEqual(code, 1)
                    self.assertIn(data['outcome'], ('failed', 'timeout'))
                    self.assertEqual(data['response_text'], '你好')
                    self.assertGreater(data['ttft_ms'], 0)
                    self.assertTrue(data['requests'][0]['events'])

    def test_incomplete_terminal_events_preserve_output(self):
        for api_type in ('chat/completions', 'responses'):
            with self.subTest(api_type=api_type):
                code, data, _ = self.exercise(api_type, 'incomplete')
                self.assertEqual(code, 2)
                self.assertEqual(data['outcome'], 'incomplete')
                self.assertEqual(data['response_text'], '你好')

    def test_http_errors_and_silent_nonstream_fallback_fail(self):
        for scenario in ('http_error', 'ignored_stream'):
            with self.subTest(scenario=scenario):
                code, data, _ = self.exercise('chat/completions', scenario)
                self.assertEqual(code, 1)
                self.assertEqual(data['outcome'], 'failed')
                self.assertIn('body', data['requests'][0])

    def test_key_split_across_deltas_is_not_printed(self):
        for api_type in ('chat/completions', 'responses'):
            with self.subTest(api_type=api_type):
                code, data, summary = self.exercise(api_type, 'split_key')
                self.assertEqual(code, 0)
                if api_type == 'chat/completions':
                    self.assertEqual(data['response_text'], '[REDACTED]')
                self.assertNotIn(FAKE_KEY, summary)
                fragments = []
                for event in data['requests'][0]['events']:
                    value = event.get('data')
                    if isinstance(value, dict):
                        fragments.extend(choice.get('delta', {}).get('content', '') for choice in value.get('choices', []))
                        if isinstance(value.get('delta'), str):
                            fragments.append(value['delta'])
                self.assertNotIn(FAKE_KEY, ''.join(fragments))


class StreamInterruptionTest(unittest.TestCase):
    def test_keyboard_interrupt_keeps_partial_report_in_memory(self):
        spec = importlib.util.spec_from_file_location('probe_stream_interrupt', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        response = mock.Mock()
        response.read1.side_effect = KeyboardInterrupt
        row, report = {}, {'returned_links': []}
        first = frame({'choices': [{'index': 0, 'delta': {'content': '保留'}}]})
        with mock.patch('sys.stdout', new_callable=io.StringIO), self.assertRaises(KeyboardInterrupt):
            module.stream_result(response, first, 'chat/completions', row, report,
                                 time.perf_counter(), time.perf_counter() + 10, mock.Mock())
        self.assertEqual(report['response_text'], '保留')
        self.assertGreaterEqual(report['ttft_ms'], 0)


if __name__ == '__main__':
    unittest.main()
