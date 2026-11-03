"""Benchmark lifecycle, safe credential resolution, and persisted history."""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
import threading
import fcntl
from .benchmark import BenchmarkConfig, BenchmarkRun, compare, atomic_json
from .runner_service import TestRunManager
from .results_store import resolve_result_folder


class BenchmarkManager:
    def __init__(self, config_path, results_root):
        self.config_path = config_path
        self.results_root = results_root
        self._lock = threading.Lock()
        self._run = None

    def start(self, payload):
        config = BenchmarkConfig.model_validate(payload.get('config', {}))
        with self._lock:
            if self._run and self._run.record['status'] in ('validating', 'warming_up', 'running', 'draining', 'stopping'):
                raise ValueError('已有压测任务运行中，请先停止或等待完成')
            credentials = {k: payload[k] for k in ('profile_id', 'custom_base_url', 'custom_api_key') if k in payload}
            resolver = TestRunManager(self.config_path, self.results_root)
            key, base, _ = resolver._resolve_credentials(credentials)
            # Profile id is useful for comparisons; do not persist endpoint or key.
            run = BenchmarkRun(config, self.results_root)
            run.record['profile_id'] = payload.get('profile_id', 'custom')
            self._run = run
            threading.Thread(target=lambda: asyncio.run(run.execute(base, key)), daemon=True).start()
            return {'run_id': run.run_id}

    def get(self, run_id):
        folder = resolve_result_folder('benchmark', run_id, self.results_root)
        value = json.loads((folder / 'result.json').read_text())
        with self._lock:
            active = self._run and self._run.run_id == run_id
        if not active and value.get('status') in ('validating', 'warming_up', 'running', 'draining', 'stopping'):
            with (folder / '.running.lock').open('a') as lease:
                try:
                    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    value.update(status='interrupted', outcome='interrupted')
                except BlockingIOError:
                    pass  # A CLI or another worker still owns this task.
        return value

    def stop(self, run_id, immediate=False):
        with self._lock:
            value = self.get_unlocked(run_id)
            if value.get('status') in ('completed', 'stopped', 'failed', 'interrupted'):
                raise ValueError('任务已结束')
            if self._run and self._run.run_id == run_id:
                self._run.stop(immediate)
            else:
                folder = resolve_result_folder('benchmark', run_id, self.results_root)
                atomic_json(folder / 'stop.json', {'immediate': immediate})
        return self.get(run_id)

    def get_unlocked(self, run_id):
        folder = resolve_result_folder('benchmark', run_id, self.results_root)
        return json.loads((folder / 'result.json').read_text())

    def list(self):
        root = self.results_root / 'benchmark'
        items = []
        if root.is_dir():
            for p in sorted(root.iterdir(), reverse=True):
                try:
                    r = self.get(p.name)
                    items.append({k: r.get(k) for k in ('run_id', 'status', 'model', 'started_at', 'summary', 'config')})
                except (ValueError, OSError):
                    continue
        return items

    def comparison(self, baseline, candidate):
        a, b = self.get(baseline), self.get(candidate)
        result = compare(a, b)
        if a.get('profile_id') != b.get('profile_id'):
            result['condition_differences']['profile_id'] = {'baseline': a.get('profile_id'), 'candidate': b.get('profile_id')}
        return result
