"""FastAPI 后端主入口：提供配置管理、可视化测试执行、结果浏览与 AI 智能解读 API。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import queue
import threading
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config_store import (
    DEFAULT_CONFIG_PATH,
    _load_analyzer_module,
    get_public_config,
    mask_api_key,
    read_raw_config,
    save_config,
)
from .results_store import (
    DEFAULT_RESULTS_ROOT,
    delete_result_folder,
    get_result_detail,
    list_results,
    resolve_result_folder,
)
from .runner_service import TestRunManager
from .assets_service import run_asset_request
from .benchmark_service import BenchmarkManager


class ProfileItemInput(BaseModel):
    id: int | None = None
    name: str = ''
    base_url: str = ''
    api_key: str = ''


class AnalyzerConfigInput(BaseModel):
    name: str = 'AI 结果解读模型'
    base_url: str = ''
    api_key: str = ''
    model: str = ''
    clear_key: bool = False


class AssetConfigInput(BaseModel):
    id: str | None = None
    name: str = ''
    endpoint: str = ''
    access_key: str = ''
    secret_key: str = ''
    clear_credentials: bool = False


class OnePasswordInput(BaseModel):
    account: str = ''
    vault: str = ''


class ConfigUpdateRequest(BaseModel):
    onepassword: OnePasswordInput | None = None
    profiles: list[ProfileItemInput] | None = None
    analyzer: AnalyzerConfigInput | None = None
    assets: AssetConfigInput | None = None
    asset_profiles: list[AssetConfigInput] | None = None


class AnalyzerTestRequest(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class AnalyzeFolderRequest(BaseModel):
    allow_fallback: bool = True
    stream: bool = False


def create_app(
    config_path: Path | None = None,
    results_root: Path | None = None,
) -> FastAPI:
    cfg_path = config_path or DEFAULT_CONFIG_PATH
    res_root = results_root or DEFAULT_RESULTS_ROOT
    run_manager = TestRunManager(config_path=cfg_path, results_root=res_root)
    benchmark_manager = BenchmarkManager(cfg_path, res_root)

    app = FastAPI(
        title='OpenAI 兼容接口测试与 AI 诊断平台',
        version='2.0.0',
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=['*'],
        allow_credentials=True,
        allow_methods=['*'],
        allow_headers=['*'],
    )

    @app.get('/api/health')
    def health_check() -> dict[str, Any]:
        return {'status': 'ok', 'results_root': str(res_root), 'config_path': str(cfg_path)}

    @app.post('/api/benchmarks')
    def start_benchmark(payload: dict[str, Any]):
        try:
            return benchmark_manager.start(payload)
        except ValueError:
            raise HTTPException(400, '配置无效或已有任务运行；请检查模型、正整数并发、阶段和凭证') from None

    @app.get('/api/benchmarks')
    def list_benchmarks():
        return {'items': benchmark_manager.list()}

    @app.get('/api/benchmarks/compare')
    def compare_benchmarks(baseline: str, candidate: str):
        try:
            return benchmark_manager.comparison(baseline, candidate)
        except (ValueError, OSError):
            raise HTTPException(404, '压测结果不存在或格式无效') from None

    @app.get('/api/benchmarks/{run_id}')
    def get_benchmark(run_id: str):
        try:
            return benchmark_manager.get(run_id)
        except (ValueError, OSError):
            raise HTTPException(404, '压测结果不存在或格式无效') from None

    @app.post('/api/benchmarks/{run_id}/stop')
    def stop_benchmark(run_id: str, immediate: bool = False):
        try:
            return benchmark_manager.stop(run_id, immediate)
        except (ValueError, OSError):
            raise HTTPException(400, '当前任务无法停止') from None

    @app.get('/api/benchmarks/{run_id}/export/{kind}')
    def export_benchmark(run_id: str, kind: str):
        names = {'json': 'result.json', 'jsonl': 'requests.jsonl', 'markdown': 'report.md', 'csv': 'requests.csv'}
        if kind not in names:
            raise HTTPException(400, '不支持的导出格式')
        try:
            target = resolve_result_folder('benchmark', run_id, res_root) / names[kind]
            if target.is_symlink() or not target.is_file():
                raise FileNotFoundError()
            return FileResponse(target, filename=f'{run_id}-{names[kind]}')
        except (ValueError, OSError):
            raise HTTPException(404, '报告尚未生成') from None

    @app.post('/api/results/{category}/{folder}/c2pa')
    def check_result_c2pa(category: str, folder: str):
        from scripts.c2pa_check import check_and_save
        try:
            target = resolve_result_folder(category, folder, res_root)
            return check_and_save(target)
        except (ValueError, OSError):
            raise HTTPException(400, '无法读取视频结果或保存校验报告') from None

    @app.post('/api/assets/run')
    def run_assets_endpoint(payload: dict[str, Any]):
        try:
            result = run_asset_request(payload, res_root, cfg_path)
            return JSONResponse(result, headers={'Cache-Control': 'no-store'})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError:
            raise HTTPException(status_code=500, detail='素材库结果保存失败，请检查目录权限') from None

    @app.get('/api/config')
    def read_config_endpoint() -> dict[str, Any]:
        try:
            return get_public_config(cfg_path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put('/api/config')
    def update_config_endpoint(body: ConfigUpdateRequest) -> dict[str, Any]:
        try:
            profiles_data = [p.model_dump() for p in body.profiles] if body.profiles is not None else None
            analyzer_data = body.analyzer.model_dump() if body.analyzer else None
            return save_config(profiles_data, analyzer_data, cfg_path,
                               assets_input=body.assets.model_dump() if body.assets else None,
                               asset_profiles_input=[p.model_dump() for p in body.asset_profiles] if body.asset_profiles is not None else None,
                               onepassword_input=body.onepassword.model_dump() if body.onepassword is not None else None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post('/api/analyzer/test')
    def test_analyzer_connection(body: AnalyzerTestRequest) -> dict[str, Any]:
        analyzer_mod = _load_analyzer_module()
        raw = read_raw_config(cfg_path, resolve_secrets=False)
        saved_analyzer = raw['analyzer']

        base_url = (body.base_url or saved_analyzer.get('base_url') or '').strip()
        model = (body.model or saved_analyzer.get('model') or '').strip()
        provided_key = (body.api_key or '').strip()
        old_key = saved_analyzer.get('api_key') or ''
        if not provided_key or provided_key == mask_api_key(old_key):
            api_key = old_key
        else:
            api_key = provided_key

        try:
            from scripts import secret_store
            api_key = secret_store.resolve(api_key, cfg_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not base_url or not api_key or not model:
            raise HTTPException(
                status_code=400,
                detail='请先填写解读模型的 Base URL、API Key 和 Model ID',
            )
        try:
            res = analyzer_mod.call_openai_compatible_analyzer(
                {'base_url': base_url, 'api_key': api_key, 'model': model},
                messages=[
                    {'role': 'system', 'content': '你是连通性探测助手。'},
                    {'role': 'user', 'content': '请仅回复“连通正常”四个字。'},
                ],
                stream=False,
                timeout=30.0,
            )
            return {
                'status': 'ok',
                'model': model,
                'base_url': analyzer_mod.normalize_base_url(base_url),
                'reply': res['content'],
                'duration_ms': res['duration_ms'],
            }
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get('/api/results')
    def list_results_endpoint(
        category: str | None = Query(default=None),
        outcome: str | None = Query(default=None),
        search: str | None = Query(default=None),
    ) -> dict[str, Any]:
        items = list_results(
            results_root=res_root,
            category=category,
            outcome=outcome,
            search=search,
        )
        return {'total': len(items), 'items': items}

    @app.get('/api/results/{category}/{folder}')
    def get_result_detail_endpoint(category: str, folder: str) -> dict[str, Any]:
        try:
            return get_result_detail(category, folder, results_root=res_root)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete('/api/results/{category}/{folder}')
    def delete_result_endpoint(category: str, folder: str) -> dict[str, Any]:
        try:
            delete_result_folder(category, folder, results_root=res_root)
            return {'deleted': True, 'id': f'{category}/{folder}'}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get('/api/results/{category}/{folder}/images/{filename}')
    def result_image(category: str, folder: str, filename: str):
        import re
        if not re.fullmatch(r'image-[1-9][0-9]*\.(png|jpg|webp)', filename):
            raise HTTPException(status_code=400, detail='无效图片文件名')
        try:
            target = resolve_result_folder(category, folder, results_root=res_root)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        image_file = target / filename
        if image_file.is_symlink() or not image_file.is_file():
            raise HTTPException(status_code=404, detail='图片不存在')
        mime = {'png': 'image/png', 'jpg': 'image/jpeg', 'webp': 'image/webp'}[image_file.suffix[1:]]
        return FileResponse(image_file, media_type=mime)

    @app.get('/api/results/{category}/{folder}/video')
    def stream_result_video(category: str, folder: str):
        try:
            target = resolve_result_folder(category, folder, results_root=res_root)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        video_file = target / 'video.mp4'
        if not video_file.is_file():
            raise HTTPException(status_code=404, detail='该测试结果不包含本地 video.mp4 文件')
        return FileResponse(
            path=video_file,
            media_type='video/mp4',
            filename=f'{folder}-video.mp4',
        )

    @app.post('/api/results/{category}/{folder}/analyze')
    def analyze_result_endpoint(
        category: str,
        folder: str,
        body: AnalyzeFolderRequest | None = None,
    ) -> dict[str, Any]:
        req = body or AnalyzeFolderRequest()
        try:
            target = resolve_result_folder(category, folder, results_root=res_root)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        analyzer_mod = _load_analyzer_module()
        try:
            raw = read_raw_config(cfg_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        extra_keys = [p['api_key'] for p in raw['profiles'] if p.get('api_key')]
        try:
            record = analyzer_mod.analyze_result_folder(
                target,
                config_path=cfg_path,
                stream=req.stream,
                allow_fallback=req.allow_fallback,
                extra_secrets=extra_keys,
            )
            detail = get_result_detail(category, folder, results_root=res_root)
            return {'analysis': record, 'detail': detail}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get('/api/results/{category}/{folder}/analyze/stream')
    async def analyze_result_stream_endpoint(
        category: str,
        folder: str,
        allow_fallback: bool = Query(default=True),
    ):
        try:
            target = resolve_result_folder(category, folder, results_root=res_root)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        analyzer_mod = _load_analyzer_module()
        try:
            raw = read_raw_config(cfg_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        extra_keys = [p['api_key'] for p in raw['profiles'] if p.get('api_key')]
        q: queue.Queue[tuple[str, Any]] = queue.Queue()

        def _worker():
            try:
                record = analyzer_mod.analyze_result_folder(
                    target,
                    config_path=cfg_path,
                    stream=True,
                    allow_fallback=allow_fallback,
                    on_delta=lambda piece: q.put(('delta', piece)),
                    extra_secrets=extra_keys,
                )
                q.put(('complete', record))
            except Exception as exc:
                q.put(('error', str(exc)))

        threading.Thread(target=_worker, daemon=True).start()

        async def _event_generator():
            while True:
                try:
                    event_type, data = q.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.05)
                    continue
                if event_type == 'delta':
                    yield f"event: delta\ndata: {json.dumps({'text': data}, ensure_ascii=False)}\n\n"
                elif event_type == 'complete':
                    yield f"event: complete\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                    break
                elif event_type == 'error':
                    yield f"event: error\ndata: {json.dumps({'error': data}, ensure_ascii=False)}\n\n"
                    break

        return StreamingResponse(_event_generator(), media_type='text/event-stream')

    @app.post('/api/tests/run')
    def start_test_run_endpoint(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return run_manager.start_run(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get('/api/tests/runs')
    def list_test_runs_endpoint() -> dict[str, Any]:
        return {'items': run_manager.list_runs()}

    @app.get('/api/tests/runs/{run_id}')
    def get_test_run_endpoint(run_id: str) -> dict[str, Any]:
        try:
            return run_manager.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post('/api/tests/runs/{run_id}/cancel')
    def cancel_test_run_endpoint(run_id: str) -> dict[str, Any]:
        try:
            return run_manager.cancel_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get('/api/tests/runs/{run_id}/events')
    async def stream_test_run_events(run_id: str):
        try:
            run_manager.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        async def _generator():
            last_payload = ''
            while True:
                snapshot = run_manager.get_run(run_id)
                serialized = json.dumps(snapshot, ensure_ascii=False)
                if serialized != last_payload:
                    last_payload = serialized
                    yield f'event: update\ndata: {serialized}\n\n'
                if snapshot.get('finished_at') is not None:
                    yield f'event: done\ndata: {serialized}\n\n'
                    break
                await asyncio.sleep(0.25)

        return StreamingResponse(_generator(), media_type='text/event-stream')

    frontend_dist = Path(__file__).resolve().parents[1] / 'frontend' / 'dist'
    if frontend_dist.is_dir():
        app.mount('/', StaticFiles(directory=str(frontend_dist), html=True), name='frontend')

    return app


app = create_app()
