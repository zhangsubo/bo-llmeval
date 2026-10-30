"""扫描、查询、解析与管理 scripts/test-results/ 下的测试结果与 AI 解读报告。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import shutil
from typing import Any


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / 'scripts'
DEFAULT_RESULTS_ROOT = SCRIPTS_DIR / 'test-results'
SAFE_SEGMENT_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')


def _load_analyzer_module():
    spec = importlib.util.spec_from_file_location('analyzer', SCRIPTS_DIR / 'analyzer.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve_result_folder(category: str, folder: str, results_root: Path | None = None) -> Path:
    root = (results_root or DEFAULT_RESULTS_ROOT).expanduser().resolve()
    if not SAFE_SEGMENT_RE.fullmatch(category or '') or not SAFE_SEGMENT_RE.fullmatch(folder or ''):
        raise ValueError('无效的结果目录名称')
    target = (root / category / folder).resolve()
    if root not in target.parents:
        raise ValueError('非法的结果目录路径')
    if not target.is_dir():
        raise FileNotFoundError(f'测试结果目录不存在：{category}/{folder}')
    return target


def _extract_prompt_preview(request_body: Any) -> str:
    if not isinstance(request_body, dict):
        return ''
    if isinstance(request_body.get('prompt'), str):
        return request_body['prompt']
    if isinstance(request_body.get('input'), dict):
        return str(request_body['input'].get('prompt') or '')
    if isinstance(request_body.get('input'), str):
        return request_body['input']
    if isinstance(request_body.get('messages'), list):
        texts = []
        for msg in request_body['messages']:
            if isinstance(msg, dict) and isinstance(msg.get('content'), str):
                texts.append(msg['content'])
        if texts:
            return ' | '.join(texts)
    if isinstance(request_body.get('content'), list):
        texts = []
        for item in request_body['content']:
            if isinstance(item, dict) and item.get('type') == 'text' and isinstance(item.get('text'), str):
                texts.append(item['text'])
        if texts:
            return ' | '.join(texts)
    return ''


def _extract_reference_summary(request_body: Any) -> list[dict[str, str]]:
    if isinstance(request_body, dict):
        inp = request_body.get('input')
        if isinstance(inp, dict):
            return [{'type': kind, 'role': role, 'url': inp[field]}
                    for field, kind, role in [('img_url', 'image', 'first_frame'), ('audio_url', 'audio', 'reference_audio')]
                    if isinstance(inp.get(field), str)]
        images = request_body.get('image')
        if isinstance(images, str):
            images = [images]
        if isinstance(images, list):
            return [{'type': 'image', 'role': 'reference_image', 'url': u} for u in images if isinstance(u, str)]
    if not isinstance(request_body, dict) or not isinstance(request_body.get('content'), list):
        return []
    refs = []
    for item in request_body['content']:
        if not isinstance(item, dict):
            continue
        item_type = item.get('type', '')
        if item_type in ('image_url', 'video_url', 'audio_url'):
            media_obj = item.get(item_type)
            url = media_obj.get('url', '') if isinstance(media_obj, dict) else ''
            refs.append({
                'type': item_type.replace('_url', ''),
                'role': item.get('role') or item_type,
                'url': url,
            })
    return refs


def summarize_result_folder(category: str, folder_path: Path, analyzer_mod=None) -> dict[str, Any] | None:
    result_file = folder_path / 'result.json'
    if not result_file.is_file():
        return None
    try:
        data = json.loads(result_file.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    video_file = folder_path / 'video.mp4'
    has_video = video_file.is_file()
    video_size = video_file.stat().st_size if has_video else 0

    analysis_json_file = folder_path / 'analysis.json'
    analysis_md_file = folder_path / 'analysis.md'
    has_analysis = analysis_json_file.is_file() or analysis_md_file.is_file()
    analysis_meta: dict[str, Any] = {}
    if analysis_json_file.is_file():
        try:
            a_data = json.loads(analysis_json_file.read_text(encoding='utf-8'))
            if isinstance(a_data, dict):
                analysis_meta = {
                    'analyzed_at': a_data.get('analyzed_at'),
                    'source': a_data.get('source'),
                    'analyzer_model': a_data.get('analyzer_model'),
                    'analyzer_name': a_data.get('analyzer_name'),
                }
        except (OSError, ValueError):
            pass

    if analyzer_mod is None:
        analyzer_mod = _load_analyzer_module()
    digest = analyzer_mod.build_diagnostic_digest(data)

    return {
        'id': f'{category}/{folder_path.name}',
        'category': category,
        'folder': folder_path.name,
        'started_at': data.get('started_at') or folder_path.name.split('-')[0],
        'mode': data.get('mode') or category,
        'outcome': data.get('outcome') or 'unknown',
        'health_level': digest.get('health_level', 'unknown'),
        'model': data.get('model') or '',
        'base_url': data.get('base_url') or '',
        'elapsed_ms': data.get('elapsed_ms'),
        'request_ms': data.get('request_ms'),
        'ttft_ms': data.get('ttft_ms'),
        'create_ms': data.get('create_ms'),
        'completion_ms': data.get('completion_ms'),
        'query_completion_ms': data.get('query_completion_ms'),
        'task_id': data.get('task_id'),
        'response_id': data.get('response_id'),
        'action': data.get('action'),
        'query_only': bool(data.get('query_only')),
        'stream': data.get('stream'),
        'usage': data.get('usage'),
        'error': data.get('error') or data.get('fail_reason'),
        'extracted_errors': digest.get('extracted_errors', []),
        'auto_findings': digest.get('auto_findings', []),
        'prompt_preview': _extract_prompt_preview(data.get('request_body'))[:200],
        'response_preview': (data.get('response_text') or '')[:200] if 'response_text' in data else '',
        'references': _extract_reference_summary(data.get('request_body')),
        'has_video': has_video,
        'video_size_bytes': video_size,
        'image_count': len([i for i in data.get('images', []) if isinstance(i, dict) and i.get('status') == 'completed']),
        'has_analysis': has_analysis,
        'analysis_meta': analysis_meta,
    }


def list_results(
    results_root: Path | None = None,
    category: str | None = None,
    outcome: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    root = (results_root or DEFAULT_RESULTS_ROOT).expanduser().resolve()
    if not root.is_dir():
        return []
    analyzer_mod = _load_analyzer_module()
    items: list[dict[str, Any]] = []
    for cat_dir in sorted(root.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name.startswith('.'):
            continue
        if category and category != 'all' and cat_dir.name != category:
            continue
        for run_dir in cat_dir.iterdir():
            if not run_dir.is_dir() or run_dir.name.startswith('.'):
                continue
            summary = summarize_result_folder(cat_dir.name, run_dir, analyzer_mod=analyzer_mod)
            if not summary:
                continue
            if outcome and outcome != 'all' and summary['outcome'] != outcome:
                continue
            if search:
                q = search.strip().lower()
                haystack = ' '.join(
                    str(summary.get(k) or '')
                    for k in ('folder', 'model', 'base_url', 'prompt_preview', 'response_preview', 'task_id', 'error')
                ).lower()
                if q not in haystack:
                    continue
            items.append(summary)

    items.sort(key=lambda x: (str(x.get('started_at') or ''), x.get('folder') or ''), reverse=True)
    return items


def get_result_detail(category: str, folder: str, results_root: Path | None = None) -> dict[str, Any]:
    target = resolve_result_folder(category, folder, results_root)
    analyzer_mod = _load_analyzer_module()
    result_file = target / 'result.json'
    if not result_file.is_file():
        raise FileNotFoundError(f'缺少 result.json：{category}/{folder}')
    result_data = json.loads(result_file.read_text(encoding='utf-8'))

    summary_file = target / 'summary.txt'
    summary_text = summary_file.read_text(encoding='utf-8') if summary_file.is_file() else ''

    analysis_data = None
    analysis_json_file = target / 'analysis.json'
    analysis_md_file = target / 'analysis.md'
    if analysis_json_file.is_file():
        try:
            analysis_data = json.loads(analysis_json_file.read_text(encoding='utf-8'))
        except ValueError:
            analysis_data = None
    elif analysis_md_file.is_file():
        analysis_data = {
            'analyzed_at': None,
            'source': 'markdown_file',
            'analyzer_model': 'unknown',
            'content': analysis_md_file.read_text(encoding='utf-8'),
        }

    digest = analyzer_mod.build_diagnostic_digest(result_data, summary_text)
    video_file = target / 'video.mp4'
    has_video = video_file.is_file()

    return {
        'id': f'{category}/{folder}',
        'category': category,
        'folder': folder,
        'folder_path': str(target),
        'result_json_path': str(result_file),
        'result': result_data,
        'summary_text': summary_text,
        'digest': digest,
        'analysis': analysis_data,
        'has_video': has_video,
        'video_url': f'/api/results/{category}/{folder}/video' if has_video else None,
        'images': [dict(i, local_url=f"/api/results/{category}/{folder}/images/{i['file']}")
                   for i in result_data.get('images', []) if isinstance(i, dict)
                   and re.fullmatch(r'image-[1-9][0-9]*\.(png|jpg|webp)', str(i.get('file', '')))
                   and (target / i['file']).is_file() and not (target / i['file']).is_symlink()],
        'video_size_bytes': video_file.stat().st_size if has_video else 0,
    }


def delete_result_folder(category: str, folder: str, results_root: Path | None = None) -> bool:
    target = resolve_result_folder(category, folder, results_root)
    shutil.rmtree(target)
    return True
