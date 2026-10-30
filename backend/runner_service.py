"""管理可视化测试任务的异步执行、实时状态追踪与中断控制。"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
from typing import Any
import uuid

from .config_store import DEFAULT_CONFIG_PATH, _load_analyzer_module, _load_script_module, read_raw_config
from .results_store import DEFAULT_RESULTS_ROOT, get_result_detail


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / 'scripts'
SCRIPT_FILE = SCRIPTS_DIR / 'test-openai-api.py'


class TestRunManager:
    """在后台线程中驱动 scripts/test-openai-api.py 子进程，并实时同步检查点状态。"""

    def __init__(self, config_path: Path | None = None, results_root: Path | None = None):
        self.config_path = config_path or DEFAULT_CONFIG_PATH
        self.results_root = results_root or DEFAULT_RESULTS_ROOT
        self._lock = threading.Lock()
        self._runs: dict[str, dict[str, Any]] = {}
        self._processes: dict[str, subprocess.Popen] = {}

    def _resolve_credentials(self, payload: dict[str, Any]) -> tuple[str, str, str]:
        script_mod = _load_script_module()
        raw_cfg = read_raw_config(self.config_path, resolve_secrets=False)
        profile_id = payload.get('profile_id')
        if profile_id is not None and profile_id != 0 and profile_id != 'custom':
            for p in raw_cfg['profiles']:
                if str(p['id']) == str(profile_id):
                    if not p.get('base_url') or not p.get('api_key'):
                        raise ValueError(f"所选配置「{p.get('name')}」缺少 Base URL 或 API Key")
                    override_base = (payload.get('custom_base_url') or '').strip()
                    effective_base = override_base or p['base_url'].strip()
                    if (
                        not override_base
                        and payload.get('api_type') == 'seedance'
                        and effective_base.rstrip('/').endswith('/v1')
                    ):
                        effective_base = effective_base.rstrip('/')[:-3] + '/api/v3'
                    from scripts import secret_store
                    return secret_store.resolve(p['api_key'], self.config_path), script_mod.normalize_base(effective_base), p['name']
            raise ValueError(f'未找到编号为 {profile_id} 的配置组')

        custom_base = (payload.get('custom_base_url') or '').strip()
        custom_key = (payload.get('custom_api_key') or '').strip()
        if not custom_base or not custom_key:
            raise ValueError('临时自定义配置必须同时填写 Base URL 和 API Key')
        return custom_key, script_mod.normalize_base(custom_base), '临时自定义配置'

    def _build_cli_args(self, payload: dict[str, Any], category_dir: Path) -> list[str]:
        api_type = payload.get('api_type') or 'chat/completions'
        if api_type not in ('chat/completions', 'responses', 'video', 'seedance', 'images/generations', 'bailian-video'):
            raise ValueError(f'不支持的接口类型：{api_type}')

        cmd = [
            sys.executable,
            '-u',
            str(SCRIPT_FILE),
            '--config',
            os.devnull,
            '--api-type',
            api_type,
            '--output',
            str(category_dir),
        ]

        request_format = payload.get('video_request_format') or 'default'
        _load_script_module().validate_video_request_format(
            api_type, request_format, (payload.get('model') or '').strip(), payload.get('task_id'),
            [str(u).strip() for u in (payload.get('reference_image') or []) if str(u).strip()],
            payload.get('reference_video') or [], payload.get('reference_audio') or [],
            payload.get('content_array') is True,
        )
        if api_type == 'video':
            cmd += ['--video-request-format', request_format]

        is_seedance = api_type == 'seedance'
        is_video = api_type in ('video', 'seedance', 'bailian-video')

        task_id = (payload.get('task_id') or '').strip()
        action = (payload.get('action') or '').strip()

        if is_seedance:
            action = action or ('query' if task_id else 'create')
            cmd += ['--action', action]
            if action in ('query', 'delete'):
                if not task_id:
                    raise ValueError(f'seedance {action} 操作必须提供任务 ID')
                cmd += ['--task-id', task_id]
            if payload.get('wait') is False:
                cmd.append('--no-wait')
            if action == 'list':
                if payload.get('page_num'):
                    cmd += ['--page-num', str(int(payload['page_num']))]
                if payload.get('page_size'):
                    cmd += ['--page-size', str(int(payload['page_size']))]
                if payload.get('filter_status'):
                    cmd += ['--filter-status', str(payload['filter_status']).strip()]
                if payload.get('filter_model'):
                    cmd += ['--filter-model', str(payload['filter_model']).strip()]
                for tid in payload.get('filter_task_ids') or []:
                    if str(tid).strip():
                        cmd += ['--filter-task-id', str(tid).strip()]
        elif is_video and task_id:
            cmd += ['--task-id', task_id]

        new_request = (action == 'create') if is_seedance else (not bool(task_id))

        if api_type == 'images/generations':
            prompt = (payload.get('prompt') or '').strip()
            if not prompt:
                raise ValueError('提示词不可为空')
            cmd += ['--prompt', prompt, '--no-stream', '--image-count', str(payload.get('image_count', 1))]
            if payload.get('size'):
                cmd += ['--size', str(payload['size'])]
            if payload.get('reference_image'):
                cmd.append('--image-input-stdin')
            for field in ('quality', 'background', 'moderation', 'response_format', 'style', 'user'):
                if payload.get(field) not in (None, ''):
                    cmd += ['--' + field.replace('_', '-'), str(payload[field])]
            if payload.get('watermark'):
                cmd.append('--watermark')
        elif not is_video:
            if payload.get('stream') is False:
                cmd.append('--no-stream')
            else:
                cmd.append('--stream')
            prompt = (payload.get('prompt') or '').strip()
            if not prompt:
                raise ValueError('提示词不可为空')
            cmd += ['--prompt', prompt]
        else:
            if not is_seedance and payload.get('video_query_api') in ('generations', 'videos'):
                cmd += ['--video-query-api', payload['video_query_api']]
            if new_request:
                prompt = (payload.get('prompt') or '').strip()
                if not prompt:
                    raise ValueError('视频测试提示词不可为空')
                cmd += ['--prompt', prompt]

                if not is_seedance and payload.get('size'):
                    cmd += ['--size', str(payload['size']).strip()]
                else:
                    resolution = (payload.get('resolution') or '720p').strip()
                    cmd += ['--resolution', resolution]

                if payload.get('duration') is not None and str(payload.get('duration')).strip() != '':
                    cmd += ['--duration', str(payload['duration'])]
                elif payload.get('seconds') is not None and str(payload.get('seconds')).strip() != '':
                    cmd += ['--seconds', str(payload['seconds'])]

                if payload.get('ratio'):
                    cmd += ['--ratio', str(payload['ratio']).strip()]

                if payload.get('generate_audio') is False:
                    cmd.append('--no-generate-audio')
                else:
                    cmd.append('--generate-audio')

                if payload.get('watermark') is True:
                    cmd.append('--watermark')
                else:
                    cmd.append('--no-watermark')

                if payload.get('content_array') is True:
                    cmd.append('--content')

                first_frame = (payload.get('first_frame') or '').strip()
                last_frame = (payload.get('last_frame') or '').strip()
                ref_images = [str(u).strip() for u in (payload.get('reference_image') or []) if str(u).strip()]
                ref_videos = [str(u).strip() for u in (payload.get('reference_video') or []) if str(u).strip()]
                ref_audios = [str(u).strip() for u in (payload.get('reference_audio') or []) if str(u).strip()]

                if is_seedance and (first_frame or last_frame):
                    if first_frame:
                        cmd += ['--first-frame', first_frame]
                    if last_frame:
                        cmd += ['--last-frame', last_frame]
                elif ref_images or ref_videos or ref_audios:
                    for u in ref_images:
                        cmd += ['--reference-image', u]
                    for u in ref_videos:
                        cmd += ['--reference-video', u]
                    for u in ref_audios:
                        cmd += ['--reference-audio', u]
                else:
                    cmd.append('--no-reference')

        if api_type in ('images/generations', 'bailian-video'):
            for field in ('negative_prompt', 'seed', 'shot_type'):
                if payload.get(field) is not None and payload[field] != '':
                    cmd += ['--' + field.replace('_', '-'), str(payload[field])]
            if payload.get('prompt_extend') is not None:
                cmd.append('--prompt-extend' if payload['prompt_extend'] else '--no-prompt-extend')

        for flag, field in (
            ('--poll-interval', 'poll_interval'),
            ('--request-timeout', 'request_timeout'),
            ('--download-timeout', 'download_timeout'),
            ('--max-wait', 'max_wait'),
        ):
            if payload.get(field) is not None and str(payload.get(field)).strip() != '':
                cmd += [flag, str(payload[field])]

        return cmd

    def start_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        api_key, base_url, profile_name = self._resolve_credentials(payload)
        api_type = payload.get('api_type') or 'chat/completions'
        is_seedance = api_type == 'seedance'
        task_id = (payload.get('task_id') or '').strip()
        action = (payload.get('action') or '').strip() or ('query' if task_id else 'create')
        new_request = (action == 'create') if is_seedance else (not bool(task_id))

        model = (payload.get('model') or '').strip()
        if new_request and not model:
            raise ValueError('新建测试请求必须填写 Model ID')

        category_name = 'openai-video' if api_type == 'video' else api_type.replace('/', '-')
        category_dir = (self.results_root / category_name).expanduser().resolve()
        category_dir.mkdir(parents=True, exist_ok=True)

        cmd = self._build_cli_args(payload, category_dir)
        run_id = uuid.uuid4().hex[:12]

        run_record: dict[str, Any] = {
            'run_id': run_id,
            'status': 'running',
            'api_type': api_type,
            'category': category_name,
            'folder': None,
            'folder_path': None,
            'result_json_path': None,
            'result_id': None,
            'profile_name': profile_name,
            'base_url': base_url,
            'model': model,
            'auto_analyze': bool(payload.get('auto_analyze', False)),
            'started_at': datetime.now(timezone.utc).isoformat(),
            'finished_at': None,
            'exit_code': None,
            'logs': [],
            'live_report': None,
            'analysis': None,
            'error': None,
        }

        with self._lock:
            self._runs[run_id] = run_record

        thread = threading.Thread(
            target=self._execute_worker,
            args=(run_id, cmd, api_key, base_url, model,
                  json.dumps(payload['reference_image']) if api_type == 'images/generations' and payload.get('reference_image') else None),
            daemon=True,
        )
        thread.start()
        return self.get_run(run_id)

    def _execute_worker(
        self,
        run_id: str,
        cmd: list[str],
        api_key: str,
        base_url: str,
        model: str,
        stdin_data: str | None = None,
    ) -> None:
        env = dict(os.environ)
        env['OPENAI_API_KEY'] = api_key
        env['OPENAI_BASE_URL'] = base_url
        if model:
            env['OPENAI_MODEL_ID'] = model
        else:
            env.pop('OPENAI_MODEL_ID', None)

        folder_path: Path | None = None

        def _refresh_live_report():
            nonlocal folder_path
            if folder_path and (folder_path / 'result.json').is_file():
                try:
                    data = json.loads((folder_path / 'result.json').read_text(encoding='utf-8'))
                    with self._lock:
                        self._runs[run_id]['live_report'] = data
                except (OSError, ValueError):
                    pass

        try:
            proc = subprocess.Popen(
                cmd,
                env=env,
                stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            with self._lock:
                self._processes[run_id] = proc

            if stdin_data is not None and proc.stdin:
                try:
                    proc.stdin.write(stdin_data)
                except BrokenPipeError:
                    pass
                finally:
                    try:
                        proc.stdin.close()
                    except BrokenPipeError:
                        pass

            # 后台采样 result.json 检查点，以便流式输出每秒更新到前端
            stop_watcher = threading.Event()

            def _checkpoint_watcher():
                while not stop_watcher.wait(0.35):
                    _refresh_live_report()

            watcher = threading.Thread(target=_checkpoint_watcher, daemon=True)
            watcher.start()

            if proc.stdout:
                for raw_line in proc.stdout:
                    safe_line = raw_line.replace(api_key, '[REDACTED]') if api_key else raw_line
                    line_stripped = safe_line.rstrip('\r\n')
                    m = re.match(r'^结果目录：(.+)$', line_stripped.strip())
                    if m:
                        candidate = Path(m.group(1).strip())
                        folder_path = candidate
                        with self._lock:
                            self._runs[run_id]['folder'] = candidate.name
                            self._runs[run_id]['folder_path'] = str(candidate)
                            self._runs[run_id]['result_json_path'] = str(candidate / 'result.json')
                            self._runs[run_id]['result_id'] = f"{self._runs[run_id]['category']}/{candidate.name}"
                    with self._lock:
                        self._runs[run_id]['logs'].append(line_stripped)
                    _refresh_live_report()

            exit_code = proc.wait()
            stop_watcher.set()
            _refresh_live_report()

            with self._lock:
                self._runs[run_id]['exit_code'] = exit_code
                live = self._runs[run_id].get('live_report') or {}
                outcome = live.get('outcome')
                if outcome:
                    self._runs[run_id]['status'] = outcome
                else:
                    self._runs[run_id]['status'] = 'completed' if exit_code == 0 else 'failed'

            # 如果开启了自动解读且结果目录已生成
            should_analyze = False
            with self._lock:
                should_analyze = bool(self._runs[run_id].get('auto_analyze')) and folder_path is not None
            if should_analyze and folder_path and (folder_path / 'result.json').is_file():
                with self._lock:
                    self._runs[run_id]['logs'].append('正在自动执行 AI 结果诊断解读…')
                try:
                    analyzer_mod = _load_analyzer_module()
                    analysis_rec = analyzer_mod.analyze_result_folder(
                        folder_path,
                        config_path=self.config_path,
                        stream=True,
                        allow_fallback=True,
                        extra_secrets=[api_key] if api_key else [],
                    )
                    with self._lock:
                        self._runs[run_id]['analysis'] = analysis_rec
                        self._runs[run_id]['logs'].append(
                            f"AI 解读已完成（{analysis_rec.get('analyzer_model')}）。"
                        )
                except Exception as exc:
                    safe_err = str(exc).replace(api_key, '[REDACTED]') if api_key else str(exc)
                    with self._lock:
                        self._runs[run_id]['logs'].append(f'AI 解读失败：{safe_err}')

        except Exception as exc:
            safe_err = str(exc).replace(api_key, '[REDACTED]') if api_key else str(exc)
            with self._lock:
                self._runs[run_id]['status'] = 'failed'
                self._runs[run_id]['error'] = safe_err
                self._runs[run_id]['logs'].append(f'执行异常：{safe_err}')
        finally:
            with self._lock:
                self._processes.pop(run_id, None)
                self._runs[run_id]['finished_at'] = datetime.now(timezone.utc).isoformat()

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            if run_id not in self._runs:
                raise KeyError(f'任务不存在：{run_id}')
            proc = self._processes.get(run_id)
        if proc and proc.poll() is None:
            try:
                proc.send_signal(signal.SIGINT)
            except OSError:
                proc.terminate()
        time.sleep(0.15)
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            if run_id not in self._runs:
                raise KeyError(f'任务不存在：{run_id}')
            snapshot = dict(self._runs[run_id])
            snapshot['logs'] = list(self._runs[run_id]['logs'])
        if snapshot.get('folder') and snapshot.get('category'):
            try:
                detail = get_result_detail(
                    snapshot['category'],
                    snapshot['folder'],
                    results_root=self.results_root,
                )
                snapshot['detail'] = detail
            except FileNotFoundError:
                pass
        return snapshot

    def list_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            ids = list(self._runs.keys())
        out = [self.get_run(rid) for rid in reversed(ids)]
        return out
