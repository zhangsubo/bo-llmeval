"""Offline SDK/c2patool adapter; orchestration uses standard library only.

No remote manifest/OCSP fetching. Unknown verification evidence stays unknown.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid


def safe_value(value):
    if isinstance(value, dict):
        return {k: ('[REDACTED]' if re.search(r'api.?key|authorization|password|secret|token', k, re.I)
                    else safe_value(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [safe_value(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r'https?://[^\s"<>]+', '[external URL omitted]', value)
        value = re.sub(r'(?i)Bearer\s+[^\s"<>]+|\bsk-[A-Za-z0-9_-]+', '[REDACTED]', value)
    return value


def normalize(data):
    if not isinstance(data, dict):
        raise ValueError('校验器输出不是 JSON 对象')
    active = data.get('active_manifest')
    manifests = data.get('manifests') or {}
    if not isinstance(manifests, dict):
        raise ValueError('无法识别 manifest 格式')
    result = {'discovery': 'present' if active else 'unknown', 'signature': 'unknown',
              'content_binding': 'unknown', 'trust': 'unknown', 'validation_codes': [],
              'active_manifest': active, 'declared_information': {}}
    results = data.get('validation_results') or {}
    group = results.get('activeManifest') or {}
    success = [s.get('code', '') for s in group.get('success', []) if isinstance(s, dict)]
    failure = [s.get('code', '') for s in group.get('failure', []) if isinstance(s, dict)]
    informational = [s.get('code', '') for s in group.get('informational', []) if isinstance(s, dict)]
    # Older readers report only failures. Do not infer success from their absence.
    legacy = [s.get('code', '') for s in data.get('validation_status', []) if isinstance(s, dict)]
    failure += legacy
    codes = success + failure + informational
    result['validation_codes'] = codes
    if 'claimSignature.validated' in success:
        result['signature'] = 'passed'
    if any(c.startswith('claimSignature.') and c not in ('claimSignature.validated', 'claimSignature.insideValidity') for c in failure):
        result['signature'] = 'failed'
    if any(c in success for c in ('assertion.dataHash.match', 'assertion.bmffHash.match', 'assertion.boxesHash.match')):
        result['content_binding'] = 'passed'
    if any(('Hash.' in c and c.endswith(('mismatch', 'malformed'))) for c in failure):
        result['content_binding'] = 'failed'
    if 'signingCredential.trusted' in success:
        result['trust'] = 'passed'
    if 'signingCredential.untrusted' in codes:
        result['trust'] = 'untrusted'
    manifest = manifests.get(active, {})
    if isinstance(manifest, dict):
        result['declared_information'] = {k: manifest[k] for k in
            ('title', 'claim_generator', 'claim_generator_info', 'signature_info', 'assertions', 'ingredients') if k in manifest}
    result['validation_state'] = data.get('validation_state', 'unknown')
    result['validation_results'] = results
    result['validation_status'] = data.get('validation_status', [])
    return safe_value(result)


def inspect_video(path: Path, *, executable=None, timeout=60, trust_anchors=None):
    started = time.perf_counter()
    record = {'schema_version': 1, 'checked_at': datetime.now(timezone.utc).isoformat(),
              'execution': 'error', 'discovery': 'unknown', 'signature': 'unknown',
              'content_binding': 'unknown', 'trust': 'unknown', 'network_policy': 'offline',
              'notice': '凭证验证不证明画面内容真实；外部凭证和在线吊销检查未执行。'}
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError('视频不存在或为符号链接')
        if path.suffix.lower() != '.mp4':
            raise ValueError('首期仅支持 MP4')
        with path.open('rb') as f:
            record['file_sha256'] = hashlib.file_digest(f, 'sha256').hexdigest()
        record['file_bytes'] = path.stat().st_size
        local_tool = Path(sys.executable).parent / 'c2patool'
        binary = executable or os.environ.get('C2PATOOL_PATH')
        if binary:
            engine = [binary]
            record['engine'] = 'c2patool'
        elif importlib.util.find_spec('c2pa') is not None:
            engine = [sys.executable, str(Path(__file__).with_name('c2pa_sdk_worker.py'))]
            record['engine'] = 'c2pa-python'
        else:
            binary = shutil.which('c2patool') or (str(local_tool) if local_tool.is_file() else None)
            if not binary:
                raise ValueError('C2PA 依赖未就绪；请安装平台依赖，或为独立 CLI 配置 C2PATOOL_PATH')
            engine = [binary]
            record['engine'] = 'c2patool'
        # An isolated settings file avoids user/global network-enabled configuration.
        env = {k: v for k, v in os.environ.items() if not k.startswith(('C2PA', 'OPENAI_', 'ANALYZER_'))}
        with tempfile.TemporaryDirectory(prefix='c2pa-verify-') as tmp:
            settings = Path(tmp) / 'settings.json'
            settings.write_text(json.dumps({'verify': {'verify_after_reading': True,
                'remote_manifest_fetch': False, 'ocsp_fetch': False}}))
            version = subprocess.run([*engine, '--version'], capture_output=True, timeout=min(timeout, 10), env=env)
            if version.returncode:
                raise ValueError('无法确认 C2PA 校验器版本')
            record['tool_version'] = version.stdout.decode(errors='replace').strip()[:200]
            command = [*engine, str(path.resolve()), '--settings', str(settings)]
            trust = trust_anchors or os.environ.get('C2PA_TRUST_ANCHORS')
            if trust:
                trust_path = Path(trust).expanduser().resolve(strict=True)
                record['trust_anchors_sha256'] = hashlib.sha256(trust_path.read_bytes()).hexdigest()
                command += ['trust', '--trust_anchors', str(trust_path)]
            else:
                record['trust_policy'] = '未配置自定义信任锚；以校验器明确证据为准'
            # Bounded subprocess output, without buffering untrusted output in RAM.
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                proc = subprocess.Popen(command, stdout=stdout, stderr=stderr, env=env)
                deadline = time.monotonic() + timeout
                try:
                    while proc.poll() is None:
                        if time.monotonic() > deadline:
                            raise TimeoutError('C2PA 检测超时')
                        if os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size > 8 * 1024 * 1024:
                            raise ValueError('C2PA 报告超过 8 MiB')
                        time.sleep(.05)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait()
                stdout.seek(0); stderr.seek(0)
                raw = stdout.read(8 * 1024 * 1024 + 1)
                err = stderr.read(8192).decode(errors='replace')
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError('C2PA 报告超过 8 MiB')
            if not raw.strip() and 'No claim found' in err:
                record.update(execution='completed', discovery='missing')
            elif raw.strip():
                record.update(normalize(json.loads(raw)), execution='completed')
                record['exit_code'] = proc.returncode
                if proc.returncode and record.get('discovery') != 'present':
                    record.update(execution='error', error='校验器未能完成检测')
            else:
                record.update(error='校验器未能完成检测（可能为外部凭证、格式或文件错误）', exit_code=proc.returncode)
    except (OSError, ValueError, TimeoutError, subprocess.TimeoutExpired) as exc:
        record['error'] = str(exc) if isinstance(exc, (ValueError, TimeoutError)) else '校验器不可用或执行失败'
    record['duration_ms'] = (time.perf_counter() - started) * 1000
    return safe_value(record)


def check_and_save(folder: Path, **kwargs):
    record = inspect_video(folder / 'video.mp4', **kwargs)
    # Each recheck is retained; latest summary is atomically replaced.
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8]
    history = folder / 'c2pa-history'
    history.mkdir(exist_ok=True)
    text = json.dumps(record, ensure_ascii=False, indent=2)
    (history / (stamp + '.json')).write_text(text, encoding='utf-8')
    temp = folder / ('.c2pa-' + stamp + '.tmp')
    temp.write_text(text, encoding='utf-8')
    os.replace(temp, folder / 'c2pa.json')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path, help='包含 video.mp4 的结果目录')
    parser.add_argument('--timeout', type=float, default=60)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('timeout 必须大于 0')
    result = check_and_save(args.folder, timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['execution'] == 'completed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
