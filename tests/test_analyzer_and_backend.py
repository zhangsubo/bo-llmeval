"""针对 AI 结果解读模块 (scripts/analyzer.py) 与 FastAPI 可视化后端 (backend/) 的集成测试。"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from fastapi.testclient import TestClient

from backend.main import create_app


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / 'scripts'


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AnalyzerAndBackendTest(unittest.TestCase):
    def setUp(self):
        self.analyzer = load_module('analyzer', SCRIPTS_DIR / 'analyzer.py')
        self.script = load_module('test_openai_api', SCRIPTS_DIR / 'test-openai-api.py')

    def test_load_profiles_ignores_analyzer_fields_and_loads_analyzer_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / 'openai-api-config'
            cfg.write_text(
                'NAME1="主测试配置"\n'
                'BASE_URL1=https://api.example.com/v1\n'
                'API_KEY1="sk-profile-secret-1111"\n'
                'ANALYZER_NAME="专属解读模型"\n'
                'ANALYZER_BASE_URL="https://llm.example.com/v1/chat/completions"\n'
                'ANALYZER_API_KEY="sk-analyzer-secret-2222"\n'
                'ANALYZER_MODEL="deepseek-v3"\n',
                encoding='utf-8',
            )
            profiles = self.script.load_profiles(cfg, required=True)
            self.assertEqual(len(profiles), 1)
            self.assertEqual(profiles[0]['name'], '主测试配置')
            self.assertEqual(profiles[0]['base'], 'https://api.example.com/v1')

            analyzer_cfg = self.analyzer.load_analyzer_config(cfg)
            self.assertTrue(analyzer_cfg['configured'])
            self.assertEqual(analyzer_cfg['name'], '专属解读模型')
            self.assertEqual(analyzer_cfg['base_url'], 'https://llm.example.com/v1')
            self.assertEqual(analyzer_cfg['model'], 'deepseek-v3')

    def test_diagnostic_digest_unwraps_nested_gateway_json_errors(self):
        failed_video_result = {
            'started_at': '2026-10-14T13:31:28.959367+00:00',
            'base_url': 'https://token.peopleopen.cn/v1',
            'model': 'doubao-seedance-2.5',
            'mode': 'openai-video',
            'outcome': 'failed',
            'request_body': {
                'model': 'doubao-seedance-2.5',
                'content': [
                    {'type': 'text', 'text': '测试人物在吃冰淇凌'},
                    {
                        'type': 'image_url',
                        'image_url': {'url': 'asset://asset-20261014210723-w5jjm'},
                        'role': 'reference_image',
                    },
                ],
            },
            'requests': [
                {
                    'phase': 'create',
                    'method': 'POST',
                    'url': 'https://token.peopleopen.cn/v1/videos',
                    'http_status': 400,
                    'headers_ms': 755.69,
                    'first_body_byte_ms': 755.8,
                    'body': {
                        'code': 'fail_to_fetch_task',
                        'message': '{"code":"fail_to_fetch_task","message":"{\\"error\\":{\\"code\\":\\"InvalidParameter\\",\\"message\\":\\"The parameter `content[1].image_url.url` specified in the request is not valid: The specified asset asset-20261014210723-w5jjm is not found.\\",\\"param\\":\\"content[1].image_url.url\\",\\"type\\":\\"BadRequest\\"}}","data":null}',
                        'data': None,
                    },
                    'error': 'create: HTTP 400，详见 result.json',
                    'duration_ms': 756.23,
                }
            ],
            'elapsed_ms': 789.42,
            'create_ms': 756.23,
            'error': 'create: HTTP 400，详见 result.json',
        }
        digest = self.analyzer.build_diagnostic_digest(failed_video_result)
        self.assertEqual(digest['health_level'], 'error')
        findings_text = '\n'.join(digest['auto_findings'])
        self.assertIn('InvalidParameter', findings_text)
        self.assertIn('content[1].image_url.url', findings_text)
        self.assertIn('asset-20261014210723-w5jjm is not found', findings_text)

    def test_digest_preserves_operation_and_completion_evidence_for_llm(self):
        cases = [
            {'mode': 'seedance', 'outcome': 'completed', 'action': 'list', 'wait': False},
            {'mode': 'seedance', 'outcome': 'completed', 'action': 'query',
             'query_only': True, 'task_id': 'task-example', 'query_completion_ms': 420},
            {'mode': 'responses', 'outcome': 'incomplete', 'stream': True,
             'response_status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'},
             'stream_completed': True},
            {'mode': 'chat/completions', 'outcome': 'incomplete', 'finish_reasons': ['length']},
        ]
        for result in cases:
            with self.subTest(mode=result['mode'], action=result.get('action')):
                result['requests'] = [{'phase': 'poll', 'http_status': 200, 'duration_ms': 20}]
                digest = self.analyzer.build_diagnostic_digest(result)
                messages = self.analyzer.build_analyzer_messages(digest)
                payload = json.loads(messages[1]['content'].split('```json\n', 1)[1].rsplit('```', 1)[0])
                self.assertEqual(payload['operation']['action'], result.get('action'))
                self.assertEqual(payload['operation']['query_only'], result.get('query_only'))
                self.assertEqual(payload['task_id'], result.get('task_id'))
                for key in ('finish_reasons', 'incomplete_details', 'stream_completed', 'response_status'):
                    self.assertEqual(payload['text_completion'][key], result.get(key))
                self.assertEqual(payload['request_timeline'][0]['http_status'], 200)
                self.assertEqual(payload['request_timeline'][0]['phase'], 'poll')

    def test_pending_and_partial_outcomes_are_not_generation_failures(self):
        for outcome in ('submitted', 'queued', 'running', 'timeout', 'interrupted',
                        'completed_download_failed', 'completed_download_interrupted'):
            with self.subTest(outcome=outcome):
                digest = self.analyzer.build_diagnostic_digest({'mode': 'seedance', 'outcome': outcome})
                self.assertEqual(digest['health_level'], 'warning')
        digest = self.analyzer.build_diagnostic_digest({'mode': 'seedance', 'outcome': 'failed'})
        self.assertEqual(digest['health_level'], 'error')

    def test_gateway_prefix_does_not_trigger_official_endpoint_advice(self):
        for host, expected in [('gateway.example.com', False), ('ark.cn-beijing.volces.com', True)]:
            with self.subTest(host=host):
                digest = self.analyzer.build_diagnostic_digest({
                    'mode': 'seedance', 'outcome': 'failed', 'base_url': f'https://{host}/v1',
                })
                self.assertEqual(any('建议将 Base URL 改为' in f for f in digest['auto_findings']), expected)

    def test_text_timing_does_not_invent_latency_cause_and_media_has_no_token_rate(self):
        digest = self.analyzer.build_diagnostic_digest({
            'mode': 'chat/completions', 'outcome': 'completed', 'stream': True,
            'ttft_ms': 4000, 'request_ms': 5000,
            'usage': {'completion_tokens': 100, 'completion_tokens_details': {'reasoning_tokens': 80}},
        })
        self.assertEqual(digest['usage_analysis']['effective_tokens_per_sec'], 20)
        findings = '\n'.join(digest['auto_findings'])
        self.assertNotIn('表现正常', findings)
        self.assertNotIn('TTFT 中包含了内部思考时间', findings)
        self.assertIn('无法从用量反推', findings)
        image_digest = self.analyzer.build_diagnostic_digest({
            'mode': 'images/generations', 'outcome': 'completed_download_failed', 'request_ms': 1000,
            'usage': {'output_tokens': 100},
            'images': [{'status': 'completed'}, {'status': 'failed', 'error': 'HTTP 403'}],
        })
        self.assertNotIn('effective_tokens_per_sec', image_digest['usage_analysis'])
        self.assertIn('图片已保存 1/2 张', '\n'.join(image_digest['auto_findings']))
        self.assertEqual(image_digest['extracted_errors'][0]['stage'], 'image_download')

    def test_analyze_result_folder_with_mock_openai_server(self):
        analyzer_key = 'sk-mock-analyzer-redaction-key'

        class MockLLMHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get('Content-Length', '0'))
                payload = json.loads(self.rfile.read(length))
                stream = payload.get('stream', False)
                if stream:
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.end_headers()
                    chunks = [
                        {'choices': [{'delta': {'content': '## 总体诊断结论\n\n'}}]},
                        {
                            'choices': [
                                {
                                    'delta': {
                                        'content': f'测试状态正常，密钥脱敏验证：{analyzer_key}。'
                                    }
                                }
                            ],
                            'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120},
                        },
                    ]
                    for c in chunks:
                        self.wfile.write(f'data: {json.dumps(c, ensure_ascii=False)}\n\n'.encode('utf-8'))
                    self.wfile.write(b'data: [DONE]\n\n')
                else:
                    body = json.dumps(
                        {
                            'id': 'mock-analysis',
                            'choices': [
                                {
                                    'message': {
                                        'role': 'assistant',
                                        'content': f'## 总体诊断结论\n非流式解读完成 {analyzer_key}',
                                    }
                                }
                            ],
                            'usage': {'prompt_tokens': 50, 'completion_tokens': 10, 'total_tokens': 60},
                        },
                        ensure_ascii=False,
                    ).encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

        server = ThreadingHTTPServer(('127.0.0.1', 0), MockLLMHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cfg = root / 'openai-api-config'
                cfg.write_text(
                    f'ANALYZER_NAME="本地测试解读器"\n'
                    f'ANALYZER_BASE_URL="http://127.0.0.1:{server.server_port}/v1"\n'
                    f'ANALYZER_API_KEY="{analyzer_key}"\n'
                    f'ANALYZER_MODEL="gpt-4o-mini"\n',
                    encoding='utf-8',
                )
                run_dir = root / 'chat-completions' / '20261015T000000Z-12345678'
                run_dir.mkdir(parents=True)
                (run_dir / 'result.json').write_text(
                    json.dumps(
                        {
                            'mode': 'chat/completions',
                            'outcome': 'completed',
                            'model': 'kimi-k3',
                            'base_url': 'https://api.example.com/v1',
                            'elapsed_ms': 1200.5,
                            'request_ms': 1180.0,
                            'ttft_ms': 450.2,
                            'stream': True,
                            'text_delta_times_ms': [450.2, 600.0, 900.0, 1150.0],
                            'usage': {
                                'prompt_tokens': 100,
                                'completion_tokens': 50,
                                'total_tokens': 150,
                                'prompt_tokens_details': {'cached_tokens': 80},
                                'completion_tokens_details': {'reasoning_tokens': 25},
                            },
                            'response_text': '你好，世界！',
                            'requests': [
                                {
                                    'phase': 'request',
                                    'http_status': 200,
                                    'headers_ms': 420.0,
                                    'first_body_byte_ms': 421.0,
                                    'duration_ms': 1180.0,
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    encoding='utf-8',
                )

                deltas: list[str] = []
                rec = self.analyzer.analyze_result_folder(
                    run_dir,
                    config_path=cfg,
                    stream=True,
                    on_delta=deltas.append,
                )
                self.assertEqual(rec['source'], 'llm')
                self.assertEqual(rec['analyzer_model'], 'gpt-4o-mini')
                self.assertIn('总体诊断结论', rec['content'])
                self.assertNotIn(analyzer_key, rec['content'])
                self.assertTrue((run_dir / 'analysis.md').is_file())
                self.assertTrue((run_dir / 'analysis.json').is_file())
                saved_md = (run_dir / 'analysis.md').read_text(encoding='utf-8')
                self.assertNotIn(analyzer_key, saved_md)
        finally:
            server.shutdown()
            server.server_close()

    def test_backend_config_results_and_run_endpoints(self):
        class MockTargetAPIHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.dumps(
                    {
                        'id': 'chatcmpl-test123',
                        'choices': [
                            {
                                'message': {'role': 'assistant', 'content': '测试响应成功'},
                                'finish_reason': 'stop',
                            }
                        ],
                        'usage': {'prompt_tokens': 10, 'completion_tokens': 6, 'total_tokens': 16},
                    },
                    ensure_ascii=False,
                ).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(('127.0.0.1', 0), MockTargetAPIHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cfg_path = root / 'openai-api-config'
                res_root = root / 'test-results'
                res_root.mkdir()

                app = create_app(config_path=cfg_path, results_root=res_root)
                client = TestClient(app)

                # 1. 保存配置并检查掩码脱敏与原 key 保留
                put_res = client.put(
                    '/api/config',
                    json={
                        'profiles': [
                            {
                                'name': '本地模拟服务',
                                'base_url': f'http://127.0.0.1:{server.server_port}/v1',
                                'api_key': 'sk-target-secret-9999',
                            }
                        ],
                        'analyzer': {
                            'name': 'AI 解读器',
                            'base_url': '',
                            'api_key': '',
                            'model': '',
                        },
                    },
                )
                self.assertEqual(put_res.status_code, 200, put_res.text)
                cfg_json = put_res.json()
                self.assertEqual(len(cfg_json['profiles']), 1)
                self.assertEqual(cfg_json['profiles'][0]['api_key_masked'], '••••（1Password）')
                self.assertNotIn('sk-target-secret-9999', json.dumps(cfg_json))

                # 再次保存时不传 api_key，应自动保留原 key
                put_res2 = client.put(
                    '/api/config',
                    json={
                        'profiles': [
                            {
                                'id': 1,
                                'name': '本地模拟服务（已改名）',
                                'base_url': f'http://127.0.0.1:{server.server_port}/v1',
                                'api_key': '',
                            }
                        ],
                    },
                )
                self.assertEqual(put_res2.status_code, 200)
                self.assertEqual(put_res2.json()['profiles'][0]['api_key_masked'], '••••（1Password）')

                # 2. 通过后端接口启动一次测试并等待完成
                run_res = client.post(
                    '/api/tests/run',
                    json={
                        'profile_id': 1,
                        'api_type': 'chat/completions',
                        'model': 'mock-gpt',
                        'prompt': '你好',
                        'stream': False,
                        'auto_analyze': True,
                    },
                )
                self.assertEqual(run_res.status_code, 200, run_res.text)
                run_id = run_res.json()['run_id']

                final_snap = None
                for _ in range(40):
                    poll = client.get(f'/api/tests/runs/{run_id}').json()
                    if poll.get('finished_at'):
                        final_snap = poll
                        break
                    time.sleep(0.1)

                self.assertIsNotNone(final_snap)
                self.assertEqual(final_snap['status'], 'completed')
                self.assertIsNotNone(final_snap['folder'])

                # 3. 查询结果列表与详情（验证自动生成的解读报告）
                list_res = client.get('/api/results').json()
                self.assertEqual(list_res['total'], 1)
                item = list_res['items'][0]
                self.assertTrue(item['has_analysis'])

                detail_res = client.get(f"/api/results/{item['category']}/{item['folder']}")
                self.assertEqual(detail_res.status_code, 200)
                detail_data = detail_res.json()
                self.assertEqual(detail_data['result']['response_text'], '测试响应成功')
                self.assertIsNotNone(detail_data['analysis'])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
