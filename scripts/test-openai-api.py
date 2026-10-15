#!/usr/bin/env python3
"""独立 OpenAI 兼容接口测试（Python 3.11+，无第三方依赖）。

运行：python3 scripts/test-openai-api.py
自动读取脚本同目录 openai-api-config。
选择配置、接口类型；video 选择分辨率和参考素材类型（视频、图片、音频），再输入 model ID 和提示词。
支持 chat/completions、responses、video、seedance；--api-type 可直接指定类型。
seedance 为火山方舟原生接口；--action create/query/list/delete，--no-wait 不轮询。
--task-id 查询已有视频任务，跳过创建、接口类型菜单、Model ID 和提示词输入。
视频按 Seedance 指南发送 JSON，默认查询 /video/generations/{id} 获取下载地址和 Token 用量。
--video-query-api videos 可手动使用 /videos/{id} 查询路径。
也支持环境变量 OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL_ID。
报告默认按接口类型写入脚本同目录的 test-results/，失败或 Ctrl+C 也保存已获得的结果。
新测试每次提交一次生成请求；--task-id 仅查询；文本默认流式，视频成功后自动下载到结果目录。
"""

import argparse
import codecs
import getpass
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime, timezone
from urllib import error, parse, request
import uuid


class NoRedirect(request.HTTPRedirectHandler):
    # 保留接口原始 Location，也避免把 Authorization 转发到下载站点。
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def positive_number(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("必须为有限正数")
    return number


def validate_task_id(value):
    value = value.strip()
    if not value or value in ('.', '..') or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('task ID 不可为空、路径点或包含控制字符')
    return value


def reference_url(value):
    # asset 不包含可判断媒体类型的信息；类型由用户选择，标识原样传给上游。
    if re.fullmatch(r'asset://asset-[A-Za-z0-9][A-Za-z0-9_-]*', value):
        return value
    try:
        url = parse.urlsplit(value)
        if (url.scheme in ('http', 'https') and url.hostname and not url.username and
                not url.password and not url.fragment and not any(c.isspace() for c in value)):
            return value
    except ValueError:
        pass
    raise argparse.ArgumentTypeError(
        '参考素材必须是 HTTP(S) URL 或 asset://asset-<素材ID>；不可包含账号密码、fragment 或本地文件路径')


def video_payload(args, model):
    """按 Seedance 指南构造 JSON；互斥参数由 CLI 校验。"""
    payload = {'model': model, 'ratio': args.ratio,
               'generate_audio': args.generate_audio, 'watermark': args.watermark}
    if args.duration is not None:
        payload['duration'] = args.duration
    else:
        payload['seconds'] = args.seconds if args.seconds is not None else 5
    if args.size:
        payload['size'] = args.size
    else:
        payload['resolution'] = args.resolution or '720p'
    if args.content or args.reference_image or args.reference_video or args.reference_audio:
        payload['content'] = [{'type': 'text', 'text': args.prompt}]
        for kind, urls in (('image', args.reference_image), ('video', args.reference_video),
                           ('audio', args.reference_audio)):
            for url in urls:
                payload['content'].append({'type': kind + '_url', kind + '_url': {'url': url},
                                           'role': 'reference_' + kind})
    else:
        payload['prompt'] = args.prompt
    return payload


def seedance_payload(args, model):
    """方舟原生协议不接受兼容接口的 prompt/seconds/size 字段。"""
    payload = video_payload(args, model)
    payload.pop('prompt', None)
    payload.pop('seconds', None)
    payload['duration'] = int(args.duration if args.duration is not None else
                              args.seconds if args.seconds is not None else 5)
    payload.setdefault('content', [{'type': 'text', 'text': args.prompt}])
    for role, url in (('first_frame', args.first_frame), ('last_frame', args.last_frame)):
        if url:
            payload['content'].append({'type': 'image_url', 'image_url': {'url': url}, 'role': role})
    return payload


def normalize_base(value):
    url = parse.urlsplit(value.strip())
    if url.scheme not in ("http", "https") or not url.hostname:
        raise ValueError("base URL 必须是 http:// 或 https:// 地址")
    if url.username or url.password or url.query or url.fragment:
        raise ValueError("base URL 不可包含账号密码、查询参数或 fragment")
    path = url.path.rstrip("/")
    for endpoint in ('/contents/generations/tasks', '/chat/completions', '/responses', '/video/generations', '/videos'):
        if path.endswith(endpoint):
            path = path[:-len(endpoint)]
            break
    if not path:
        path = '/api/v3' if url.hostname == 'ark.cn-beijing.volces.com' else '/v1'
    return parse.urlunsplit((url.scheme, url.netloc, path, "", ""))


def load_profiles(path, required=False):
    """只读专用 dotenv 文件；不执行 shell、不展开变量、不写入环境。"""
    try:
        content = path.expanduser().read_text(encoding='utf-8-sig')
    except FileNotFoundError:
        if required:
            raise ValueError('指定的配置文件不存在') from None
        return []
    except (OSError, UnicodeError):
        raise ValueError('无法读取配置文件，请检查权限和 UTF-8 编码') from None
    groups = {}
    seen = set()
    for line_number, line in enumerate(content.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        name, sep, value = line.partition('=')
        match = re.fullmatch(r'(NAME|BASE_URL|API_KEY)([1-9][0-9]*)', name.strip())
        # 错误仅打印行号，避免配置值（尤其 key）随异常泄露。
        if not sep or not match or name.strip() in seen:
            raise ValueError(f'配置第 {line_number} 行字段无效或重复，应使用 NAME1/BASE_URL1/API_KEY1 等字段（编号为正整数）')
        seen.add(name.strip())
        value = value.strip()
        if value.startswith(('"', "'")):
            quoted = re.fullmatch(r"(['\"])(.*?)\1\s*(?:#.*)?", value)
            if not quoted:
                raise ValueError(f'配置第 {line_number} 行引号不匹配（仅支持单行值）')
            value = quoted.group(2)
        else:
            value = re.split(r'\s+#', value, maxsplit=1)[0].strip()
        field, number = match.groups()
        groups.setdefault(int(number), {})[field] = value.strip()
    profiles = []
    for number, group in sorted(groups.items()):
        base, key = group.get('BASE_URL', ''), group.get('API_KEY', '')
        if not base and not key:
            continue
        if not base or not key:
            print(f'跳过配置 {number}：BASE_URL 和 API_KEY 需同时填写。')
            continue
        try:
            base = normalize_base(base)
        except ValueError:
            print(f'跳过配置 {number}：BASE_URL 格式无效。')
            continue
        profiles.append({'name': group.get('NAME') or f'配置 {number}', 'base': base, 'key': key})
    return profiles


def select_credentials(profiles):
    env_base = os.environ.get('OPENAI_BASE_URL', '').strip()
    env_key = os.environ.get('OPENAI_API_KEY', '').strip()
    options = list(profiles)
    if env_base and env_key:
        # 没有文件配置时保持原环境变量无交互用法；绝不把环境中的半组配置拼进其他组。
        if not options:
            return env_key, normalize_base(env_base)
        try:
            options.append({'name': '当前环境变量', 'base': normalize_base(env_base), 'key': env_key})
        except ValueError:
            print('忽略环境变量配置：OPENAI_BASE_URL 格式无效。')
    if options:
        print('请选择常用配置（base URL 和 key 成对使用）：')
        for index, profile in enumerate(options, 1):
            label = f"  {index}. {profile['name']} | {profile['base']} | key 已配置"
            for item in options:
                label = redact(label, item['key'])
            print(label)
        print('  0. 临时自定义 base URL 和 key')
        while True:
            choice = input('输入编号：').strip()
            if choice == '0':
                break
            if choice in {str(i) for i in range(1, len(options) + 1)}:
                profile = options[int(choice) - 1]
                return profile['key'], profile['base']
            print('编号无效，请重新选择。')
    base = normalize_base(input('Base URL（可带 /v1）：'))
    key = getpass.getpass('API key（隐藏输入）：').strip()
    if not key:
        raise ValueError('key 不可为空')
    return key, base


def select_api_type():
    options = {'1': 'chat/completions', '2': 'responses', '3': 'video', '4': 'seedance'}
    print('请选择接口类型：\n  1. chat/completions\n  2. responses\n  3. video\n  4. seedance（火山方舟原生）')
    while True:
        choice = input('输入编号（默认 3）：').strip() or '3'
        if choice in options:
            return options[choice]
        print('编号无效，请重新选择。')


def select_seedance_action():
    options = {'1': 'create', '2': 'query', '3': 'list', '4': 'delete'}
    print('请选择 Seedance 操作：\n  1. 创建（默认）\n  2. 查询\n  3. 列表\n  4. 取消/删除')
    while True:
        choice = input('输入编号（默认 1）：').strip() or '1'
        if choice in options:
            return options[choice]
        print('编号无效，请重新选择。')


def select_video_resolution():
    options = {'1': '720p', '2': '1080p', '3': '4k'}
    print('请选择视频分辨率：\n  1. 720p（默认）\n  2. 1080p\n  3. 4K')
    while True:
        choice = input('输入编号或分辨率（默认 1）：').strip().lower() or '1'
        if choice in options:
            return options[choice]
        if choice in options.values():
            return choice
        print('选项无效，请选择 1/2/3 或 720p/1080p/4K。')


def select_reference_media():
    references = {'video': [], 'image': [], 'audio': []}
    options = {'2': ('video', '视频'), '3': ('image', '图片'), '4': ('audio', '音频')}
    print('请选择参考素材类型：\n  1. 不使用（默认）\n  2. 视频\n  3. 图片\n  4. 音频')
    print('可多选，用逗号分隔，例如 3,4 表示图片和音频。请按素材实际类型选择。')
    while True:
        choice = input('输入编号（默认 1）：').strip().lower() or '1'
        if choice in ('1', 'n', 'no', '否'):
            return references
        if choice in ('y', 'yes', '是'):
            choice = '2'
        choices = list(dict.fromkeys(re.split(r'[,，\s]+', choice)))
        if choices and all(item in options for item in choices):
            break
        print('选项无效，请选择 1（不使用）或 2/3/4（可多选）。')
    for choice in choices:
        kind, label = options[choice]
        while True:
            value = input(f'参考{label}地址（HTTP/HTTPS 或 asset://asset-<素材ID>）：').strip()
            try:
                references[kind].append(reference_url(value))
                break
            except argparse.ArgumentTypeError as exc:
                print(str(exc))
    return references


def text_result(api_type, response):
    """校验非流式响应，并提取文本；HTTP 200 中的错误也不能算成功。"""
    if response.get('error'):
        raise RuntimeError('接口返回 error，详见 result.json')
    result = {'response_id': response.get('id'), 'usage': response.get('usage')}
    messages = []
    if api_type == 'chat/completions':
        choices = response.get('choices')
        if not isinstance(choices, list) or not choices:
            raise ValueError('chat/completions 响应缺少有效 choices，详见 result.json')
        for choice in choices:
            if not isinstance(choice, dict) or not isinstance(choice.get('message'), dict):
                raise ValueError('chat/completions 响应缺少 message，详见 result.json')
            messages.append(choice['message'])
        result['finish_reasons'] = [choice.get('finish_reason') for choice in choices]
        result['response_status'] = 'incomplete' if 'length' in result['finish_reasons'] else 'completed'
    else:
        status = response.get('status')
        if status not in (None, 'completed', 'incomplete'):
            raise RuntimeError('responses 未完成或返回失败状态，详见 result.json；当前仅测试同步响应')
        output = response.get('output')
        if not isinstance(output, list):
            raise ValueError('responses 响应缺少有效 output，详见 result.json')
        if any(not isinstance(item, dict) for item in output):
            raise ValueError('responses output 格式无效，详见 result.json')
        messages = [item for item in output if item.get('type') == 'message']
        result.update(response_status=status or 'completed',
                      incomplete_details=response.get('incomplete_details'))
    texts = []
    for message in messages:
        content = message.get('content')
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    value = block.get('text') or block.get('refusal')
                    if isinstance(value, str):
                        texts.append(value)
        if isinstance(message.get('refusal'), str):
            texts.append(message['refusal'])
    result['response_text'] = '\n'.join(texts)
    return result


def stream_result(response, first, api_type, row, report, tick, deadline, checkpoint):
    """按 SSE 帧处理增量，保留部分内容；结束标记之外的 EOF 视为失败。"""
    decoder = codecs.getincrementaldecoder('utf-8-sig')('strict')
    row['events'] = []
    report.update(response_text='', ttft_ms=None, text_delta_times_ms=[])
    line, data, event_name = '', [], ''
    skip_lf = False
    received = 0
    parts, finished = {}, {}
    last_saved = time.perf_counter()

    def event(payload, name, at_ms):
        if payload == '[DONE]':
            if api_type != 'chat/completions' or not finished or any(v is None for v in finished.values()):
                raise ValueError('流结束但缺少有效完成信息，详见 result.json')
            report['finish_reasons'] = list(finished.values())
            report['response_status'] = 'incomplete' if 'length' in finished.values() else 'completed'
            row['events'].append({'event': name, 'at_ms': at_ms, 'data': '[DONE]'})
            return True
        try:
            value = json.loads(payload)
        except ValueError:
            row['events'].append({'event': name, 'at_ms': at_ms, 'raw_data': payload})
            raise ValueError('流式事件不是有效 JSON，详见 result.json') from None
        row['events'].append({'event': name, 'at_ms': at_ms, 'data': value})
        if not isinstance(value, dict):
            raise ValueError('流式事件必须是 JSON 对象')
        report['returned_links'].extend(dict(link, phase='stream') for link in find_links(value))
        kind = value.get('type') or name
        if value.get('error') or kind in ('error', 'response.failed'):
            raise RuntimeError('接口返回流式错误，详见 result.json')
        chunks = []
        if api_type == 'chat/completions':
            report['response_id'] = value.get('id') or report.get('response_id')
            if isinstance(value.get('usage'), dict):
                report['usage'] = value['usage']
            choices = value.get('choices', [])
            if not isinstance(choices, list):
                raise ValueError('流式 choices 格式无效')
            for choice in choices:
                if not isinstance(choice, dict) or not isinstance(choice.get('index', 0), int):
                    raise ValueError('流式 choice 格式无效')
                index = choice.get('index', 0)
                finished[index] = choice.get('finish_reason') or finished.get(index)
                delta = choice.get('delta') or {}
                if not isinstance(delta, dict):
                    raise ValueError('流式 delta 格式无效')
                chunk = delta.get('content') or delta.get('refusal')
                if isinstance(chunk, str) and chunk:
                    parts[index] = parts.get(index, '') + chunk
                    chunks.append(chunk)
            report['response_text'] = '\n'.join(parts[i] for i in sorted(parts))
        else:
            if kind in ('response.completed', 'response.incomplete'):
                final = value.get('response')
                if not isinstance(final, dict):
                    raise ValueError('responses 终止事件缺少 response')
                result = text_result(api_type, final)
                if not result['response_text']:
                    result['response_text'] = report['response_text']
                if kind == 'response.incomplete':
                    result['response_status'] = 'incomplete'
                report.update(result)
                return True
            if isinstance(value.get('response'), dict):
                report['response_id'] = value['response'].get('id') or report.get('response_id')
            if kind in ('response.output_text.delta', 'response.refusal.delta'):
                chunk = value.get('delta')
                if not isinstance(chunk, str):
                    raise ValueError('responses 文本 delta 格式无效')
                if chunk:
                    index = (value.get('output_index', 0), value.get('content_index', 0))
                    if any(not isinstance(i, int) for i in index):
                        raise ValueError('responses 文本索引格式无效')
                    parts[index] = parts.get(index, '') + chunk
                    report['response_text'] = '\n'.join(parts[i] for i in sorted(parts))
                    chunks.append(chunk)
        if chunks:
            if report['ttft_ms'] is None:
                report['ttft_ms'] = at_ms
                print(f'已收到首个非空文本片段：{at_ms:.2f} ms', flush=True)
            report['text_delta_times_ms'].append(at_ms)
        return False

    chunk = first
    while chunk:
        received += len(chunk)
        if received > 2 * 1024 * 1024:
            raise ValueError('流式响应超过 2 MiB，已保存接收的事件')
        if time.perf_counter() >= deadline:
            raise TimeoutError('达到流式请求总等待上限')
        # read1 只取已到达的数据，避免等待缓冲区填满而扭曲 TTFT。
        at_ms = round((time.perf_counter() - tick) * 1000, 2)
        for character in decoder.decode(chunk):
            if skip_lf and character == '\n':
                skip_lf = False
                continue
            skip_lf = False
            if character not in ('\r', '\n'):
                line += character
                if len(line) > 256 * 1024:
                    raise ValueError('单行 SSE 数据超过 256 KiB')
                continue
            skip_lf = character == '\r'
            if not line:
                if data and event('\n'.join(data), event_name, at_ms):
                    report['stream_completed'] = True
                    return
                data, event_name = [], ''
            elif not line.startswith(':'):
                field, _, value = line.partition(':')
                value = value[1:] if value.startswith(' ') else value
                if field == 'data':
                    data.append(value)
                elif field == 'event':
                    event_name = value
            line = ''
        if time.perf_counter() - last_saved >= 1:
            checkpoint()
            last_saved = time.perf_counter()
        chunk = response.read1(4096)
    decoder.decode(b'', final=True)
    row['unfinished_event'] = {'event': event_name, 'data_lines': data, 'line': line}
    raise ValueError('流提前结束：未收到有效结束事件，已保存部分输出')


def redact(value, key):
    if isinstance(value, str):
        return value.replace(key, "[REDACTED]") if key else value
    if isinstance(value, dict):
        return {redact(k, key): redact(v, key) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    return value


def find_links(value, path="$"):
    """保留所有 HTTP(S) URL 和字段路径，便于辨认视频、封面等不同链接。"""
    if isinstance(value, dict):
        return [link for k, v in value.items() for link in find_links(v, f"{path}.{k}")]
    if isinstance(value, list):
        return [link for i, v in enumerate(value) for link in find_links(v, f"{path}[{i}]")]
    if isinstance(value, str) and value.startswith(("https://", "http://")):
        return [{"field": path, "url": value}]
    return []


def redact_stream_fragments(report, key):
    """脱敏已复制的事件列表，避免完整 key 可由多个 delta 重新拼出。"""
    if not key:
        return
    for row in report['requests']:
        groups = {}
        for event in row.get('events', []):
            value = event.get('data')
            if not isinstance(value, dict):
                continue
            for choice in value.get('choices', []) if isinstance(value.get('choices'), list) else []:
                if not isinstance(choice, dict) or not isinstance(choice.get('delta'), dict):
                    continue
                for field in ('content', 'refusal'):
                    if isinstance(choice['delta'].get(field), str):
                        group = ('chat', str(choice.get('index', 0)), field)
                        groups.setdefault(group, []).append((choice['delta'], field))
            if value.get('type') in ('response.output_text.delta', 'response.refusal.delta') and isinstance(value.get('delta'), str):
                group = ('responses', str(value.get('output_index', 0)), str(value.get('content_index', 0)), value['type'])
                groups.setdefault(group, []).append((value, 'delta'))
        for references in groups.values():
            joined = ''.join(parent[field] for parent, field in references)
            spans = [(match.start(), match.end()) for match in re.finditer(re.escape(key), joined)]
            offset = 0
            for parent, field in references:
                value = parent[field]
                ranges = [(max(start - offset, 0), min(end - offset, len(value)))
                          for start, end in spans if start < offset + len(value) and end > offset]
                for start, end in reversed(ranges):
                    value = value[:start] + '[REDACTED]' + value[end:]
                offset += len(parent[field])
                parent[field] = value


def save_report(report, folder, key):
    safe = redact(report, key)
    redact_stream_fragments(safe, key)
    text = [
        f"{safe['mode']} 测试结果",
        f"状态：{safe['outcome']}",
        f"Model：{safe['model'] or '未返回'}",
        f"完整测试耗时：{safe.get('elapsed_ms', 0)} ms",
    ]
    if safe['mode'] == 'seedance' and safe.get('action') in ('list', 'delete'):
        text.append('操作：' + safe['action'])
        if safe['action'] == 'list':
            text.append(f"匹配任务总数：{safe.get('total', '未取得')}（仅显示当前页）")
            for item in safe.get('items', []):
                text.append(f"- {item.get('id', '-')} | {item.get('status', '-')} | {item.get('model', '-')}")
        else:
            text.append(f"任务 ID：{safe.get('task_id', '未取得')}")
            if safe['outcome'] == 'completed':
                text.append('取消/删除请求成功，具体操作由服务端任务状态决定。')
    elif safe['mode'] in ('openai-video', 'seedance'):
        text.append(f"任务 ID：{safe.get('task_id', '未取得')}")
        if safe.get('last_status'):
            text.append('服务端任务状态：' + safe['last_status'])
        if safe.get('wait') is False:
            text.append('本次不轮询；submitted 表示已受理，尚未确认生成完成。')
        if safe.get('query_only'):
            text += ['模式：查询已有任务（未创建新任务）',
                     f"本次查询至观察到完成：{safe.get('query_completion_ms', '未完成')} ms",
                     '此耗时不代表视频生成总耗时。']
        else:
            text += [f"创建请求耗时：{safe.get('create_ms', '未取得')} ms",
                     f"首次观察到完成的总耗时：{safe.get('completion_ms', '未完成')} ms"]
        text.append(f"轮询间隔：{safe['poll_interval_seconds']} s（完成时间含轮询观测误差）")
        usage = safe.get('usage') or {}
        text += [f"总 Token 数：{usage.get('total_tokens') if usage.get('total_tokens') is not None else '未上报'}",
                 f"生成 Token 数：{usage.get('completion_tokens') if usage.get('completion_tokens') is not None else '未上报'}"]
        if usage:
            text.append('Token 用量（服务端上报）：' + json.dumps(usage, ensure_ascii=False))
        if safe.get('result_url'):
            text.append('视频下载地址：' + safe['result_url'])
    else:
        text += [f"响应 ID：{safe.get('response_id') or '未取得'}",
                 f"请求耗时（含失败/中断）：{safe.get('request_ms', '未取得')} ms"]
        if safe.get('stream'):
            text += ['请求模式：流式 SSE',
                     f"TTFT（首个非空文本片段）：{safe.get('ttft_ms') if safe.get('ttft_ms') is not None else '未收到'} ms",
                     f"非空文本增量事件数：{len(safe.get('text_delta_times_ms', []))}",
                     '增量事件不等于单个 token；角色/心跳/推理事件不计入 TTFT。']
        else:
            text.append('请求模式：非流式；首个响应体字节耗时不等同于首 token 延时。')
        if safe.get('usage') is not None:
            text.append('Token 用量：' + json.dumps(safe['usage'], ensure_ascii=False))
        if 'response_text' in safe:
            text += ['', '返回文本：', safe['response_text'] or '未返回文本，详见 result.json。']
        if safe.get('response_status') == 'incomplete':
            text.append('输出未完成/被截断，详见 result.json 中的完成原因。')
    text += ['', '接口实际返回的链接（请结合字段路径辨认用途；视频下载结果另列）：']
    text += [f"- {x['phase']} {x['field']}: {x['url']}" for x in safe['returned_links']]
    if not safe['returned_links']:
        text.append("无；接口未返回独立 HTTP(S) URL 字段。")
    if safe.get('content_endpoint'):
        text += ["", "按协议拼出的下载入口（需要 Bearer 鉴权，不是接口返回链接）：",
                 safe['content_endpoint']]
    if safe.get('download'):
        download = safe['download']
        text += ['', f"视频下载：{download['status']}",
                 f"文件：{download.get('file_path', '未保存')}",
                 f"下载字节数：{download.get('bytes', 0)}；耗时：{download.get('duration_ms', 0)} ms"]
        if download.get('error'):
            text += [f"下载错误：{download['error']}", '可使用 --task-id 查询原任务并再次下载，无需重新生成。']
    if safe.get('error'):
        text += ["", f"错误：{safe['error']}"]
    text += ["", "各次请求（响应头 / 首个响应体字节 / 本次观测总耗时）："]
    for row in safe['requests']:
        text.append(f"- {row['phase']}: HTTP {row.get('http_status', '-')} | "
                    f"{row.get('headers_ms', '-')} / {row.get('first_body_byte_ms', '-')} / "
                    f"{row.get('duration_ms', '-')} ms")
    for name, data in (("result.json", json.dumps(safe, ensure_ascii=False, indent=2)),
                       ("summary.txt", "\n".join(text) + "\n")):
        temp = folder / (name + ".tmp")
        temp.write_text(data, encoding="utf-8")
        temp.chmod(0o600)
        temp.replace(folder / name)


def download_video(args, key, url, authenticated, folder, report, checkpoint):
    """流式下载完整 MP4；重定向及 result_url 请求不携带 API key。"""
    started = time.perf_counter()
    deadline = started + args.download_timeout
    destination = folder / 'video.mp4'
    partial = folder / 'video.mp4.part'
    download = report['download'] = {'status': 'downloading', 'bytes': 0}
    opener = request.build_opener(NoRedirect())
    try:
        for hop in range(6):
            parsed = parse.urlsplit(url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('视频下载地址必须是无账号密码的 HTTP(S) URL')
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise TimeoutError('视频下载超时')
            headers = {'Accept': 'video/mp4, application/octet-stream', 'Accept-Encoding': 'identity'}
            if authenticated:
                headers['Authorization'] = 'Bearer ' + key
            row = {'phase': 'download', 'method': 'GET', 'url': url}
            report['requests'].append(row)
            tick = time.perf_counter()
            try:
                try:
                    response = opener.open(request.Request(url, headers=headers),
                                           timeout=min(args.request_timeout, remaining))
                except error.HTTPError as exc:
                    response = exc
                with response:
                    row.update(http_status=response.code, headers_ms=round((time.perf_counter() - tick) * 1000, 2))
                    row['headers'] = {k: v for k, v in response.headers.items()
                                      if k.lower() in ('content-type', 'content-length', 'location', 'content-range')}
                    if response.code in (301, 302, 303, 307, 308):
                        location = response.headers.get('Location')
                        if not location or hop == 5:
                            raise ValueError('视频下载重定向缺少 Location 或超过 5 次')
                        report['returned_links'].append({'phase': 'download', 'field': 'header.Location', 'url': location})
                        target = parse.urljoin(url, location)
                        if parsed.scheme == 'https' and parse.urlsplit(target).scheme != 'https':
                            raise ValueError('拒绝视频下载从 HTTPS 降级')
                        url, authenticated = target, False
                        continue
                    content_type = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
                    if response.code != 200 or not (content_type.startswith('video/') or content_type == 'application/octet-stream'):
                        row['body_text'] = response.read(4096).decode('utf-8', errors='replace')
                        raise ValueError(f'视频下载 HTTP {response.code}，未返回完整视频响应，详见 result.json')
                    length = response.headers.get('Content-Length')
                    expected = int(length) if length is not None else None
                    prefix = b''
                    with partial.open('xb') as output:
                        partial.chmod(0o600)
                        while True:
                            if time.perf_counter() >= deadline:
                                raise TimeoutError('视频下载超时')
                            chunk = response.read1(256 * 1024)
                            if not chunk:
                                break
                            if not download['bytes']:
                                row['first_body_byte_ms'] = round((time.perf_counter() - tick) * 1000, 2)
                            prefix = (prefix + chunk[:16])[:16]
                            output.write(chunk)
                            download['bytes'] += len(chunk)
                    if not download['bytes'] or (expected is not None and download['bytes'] != expected):
                        raise ValueError('视频为空或下载字节数与 Content-Length 不一致')
                    if prefix[4:8] != b'ftyp':
                        raise ValueError('下载内容不是预期的 MP4 文件')
                    # 原子发布且不覆盖已有文件；失败时不留下看似成功的 MP4。
                    os.link(partial, destination)
                    download.update(status='completed', file_path=str(destination))
                    return
            except BaseException as exc:
                row['error'] = str(exc) or type(exc).__name__
                raise
            finally:
                row['duration_ms'] = round((time.perf_counter() - tick) * 1000, 2)
                checkpoint()
    except BaseException as exc:
        download.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                        error=str(exc) or type(exc).__name__)
        raise
    finally:
        partial.unlink(missing_ok=True)
        download['duration_ms'] = round((time.perf_counter() - started) * 1000, 2)
        checkpoint()


def run(args, key, base, model):
    folder = args.output.expanduser().resolve() / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True, mode=0o700)
    print(f"结果目录：{folder}", flush=True)
    started = time.perf_counter()
    deadline = started + args.max_wait
    is_seedance = args.api_type == 'seedance'
    is_video = args.api_type in ('video', 'seedance')
    if is_seedance:
        payload = seedance_payload(args, model) if args.action == 'create' else None
    elif args.api_type == 'video':
        payload = None if args.task_id else video_payload(args, model)
    elif args.api_type == 'chat/completions':
        payload = {'model': model, 'messages': [{'role': 'user', 'content': args.prompt}], 'stream': args.stream}
        if args.stream:
            payload['stream_options'] = {'include_usage': True}
    else:
        payload = {'model': model, 'input': args.prompt, 'stream': args.stream}
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(), "base_url": base,
        "model": model, "mode": 'openai-video' if args.api_type == 'video' else args.api_type,
        "outcome": "running", "request_body": payload,
        "requests": [], "returned_links": [],
    }
    if is_video:
        report['poll_interval_seconds'] = args.poll_interval
        report['query_only'] = bool(args.task_id)
        report['video_query_api'] = args.video_query_api
        report.update(usage=None, usage_source=None, usage_status='not_reported')
        if args.task_id:
            report['task_id'] = args.task_id
        if is_seedance:
            report.update(action=args.action, wait=args.wait, video_query_api='seedance')
    else:
        report['stream'] = args.stream
    opener = request.build_opener(NoRedirect())

    def checkpoint():
        report['elapsed_ms'] = round((time.perf_counter() - started) * 1000, 2)
        save_report(report, folder, key)

    def call(phase, url, payload=None, method=None):
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError("达到总等待上限；服务端任务可能仍在运行，可用已保存的任务 ID 查询")
        headers = {"Authorization": "Bearer " + key, "Accept": "application/json"}
        if args.stream:
            headers['Accept'] = 'text/event-stream'
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        method = method or ('POST' if body is not None else 'GET')
        row = {"phase": phase, "method": method, "url": url}
        report['requests'].append(row)
        tick = time.perf_counter()
        try:
            try:
                response = opener.open(request.Request(url, data=body, headers=headers, method=method),
                                       timeout=min(args.request_timeout, remaining))
            except error.HTTPError as exc:
                response = exc
            with response:
                row.update(http_status=response.code,
                           headers_ms=round((time.perf_counter() - tick) * 1000, 2))
                row['headers'] = {k: v for k, v in response.headers.items()
                                  if k.lower() in ('content-type', 'content-length', 'location',
                                                   'x-request-id', 'retry-after', 'content-range')}
                location = response.headers.get('Location')
                if location:
                    report['returned_links'].append({"phase": phase, "field": "header.Location",
                                                     "url": location})
                first = response.read(1)
                row['first_body_byte_ms'] = round((time.perf_counter() - tick) * 1000, 2) if first else None
                if args.stream and 200 <= response.code < 300 and 'text/event-stream' in response.headers.get('Content-Type', '').lower():
                    stream_result(response, first, args.api_type, row, report, tick, deadline, checkpoint)
                    return None
                raw = first + response.read(2 * 1024 * 1024)
                if len(raw) > 2 * 1024 * 1024:
                    row['body_text'] = raw[:2 * 1024 * 1024].decode('utf-8', errors='replace')
                    raise ValueError("响应体超过 2 MiB，已截断保存")
                decoded = raw.decode('utf-8', errors='replace')
                if method == 'DELETE' and 200 <= response.code < 300 and not raw:
                    row['body'] = None
                    return {}
                try:
                    value = json.loads(decoded)
                except ValueError:
                    row['body_text'] = decoded
                    raise ValueError(f"{phase}: HTTP {response.code}，响应不是 JSON")
                row['body'] = value
                report['returned_links'].extend(dict(x, phase=phase) for x in find_links(value))
                if not 200 <= response.code < 300:
                    if response.code == 429:
                        raise RuntimeError(f'{phase}: HTTP 429，请降低调用频率，查询时可增大 --poll-interval；详见 result.json')
                    raise RuntimeError(f"{phase}: HTTP {response.code}，详见 result.json")
                if not isinstance(value, dict):
                    raise ValueError(f"{phase}: 预期 JSON 对象")
                if is_seedance and value.get('error') and not (phase == 'poll' and value.get('status')):
                    raise RuntimeError(f'{phase}: 方舟接口返回业务错误，详见 result.json')
                if args.stream:
                    raise ValueError('已请求流式，但接口返回了非 SSE 响应，详见 result.json')
                return value
        except Exception as exc:
            row['error'] = str(exc)
            raise
        finally:
            row['duration_ms'] = round((time.perf_counter() - tick) * 1000, 2)
            if phase == 'create':
                report['create_ms'] = row['duration_ms']
            elif phase == 'request':
                report['request_ms'] = row['duration_ms']
            checkpoint()

    code = 1
    try:
        checkpoint()
        if not is_video:
            print(f"正在请求 {args.api_type}（{'流式' if args.stream else '非流式'}）…", flush=True)
            response = call('request', base + '/' + args.api_type, report['request_body'])
            if not args.stream:
                report.update(text_result(args.api_type, response))
            report['outcome'] = report['response_status']
            return 0 if report['outcome'] == 'completed' else 2
        collection_url = base + ('/contents/generations/tasks' if is_seedance else '/videos')
        if is_seedance and args.action == 'list':
            params = [('page_num', args.page_num), ('page_size', args.page_size)]
            for name, value in (('status', args.filter_status), ('model', args.filter_model)):
                if value:
                    params.append(('filter.' + name, value))
            params.extend(('filter.task_ids', value) for value in args.filter_task_id)
            response = call('list', collection_url + '?' + parse.urlencode(params))
            items, total = response.get('items'), response.get('total')
            if (not isinstance(items, list) or not all(isinstance(item, dict) for item in items)
                    or type(total) is not int or total < 0):
                raise ValueError('方舟列表响应缺少有效 items/total，详见 result.json')
            report.update(items=items, total=total, outcome='completed')
            return 0
        if is_seedance and args.action == 'delete':
            call('delete', collection_url + '/' + parse.quote(args.task_id, safe=''), method='DELETE')
            report['outcome'] = 'completed'
            return 0
        if args.task_id:
            print('正在查询已有视频任务…', flush=True)
            task_id = args.task_id
        else:
            print("正在创建一个视频任务…", flush=True)
            state = call('create', collection_url, report['request_body'])
            task_id = state.get('id')
            if not task_id and not is_seedance and args.video_query_api == 'generations':
                task_id = state.get('task_id')
            if not isinstance(task_id, str) or not task_id.strip():
                raise ValueError("创建响应缺少有效平台任务 ID，详见 result.json；不要自行拼接上游任务 ID")
        report['task_id'] = task_id
        if is_seedance and not args.wait and args.action == 'create':
            report['outcome'] = 'submitted'
            return 0
        encoded_id = parse.quote(task_id, safe='')
        query_path = '/videos/' if args.video_query_api == 'videos' else '/video/generations/'
        if is_seedance:
            query_path = '/contents/generations/tasks/'
        task_url = base + query_path + encoded_id
        report['query_endpoint'] = task_url
        if not is_seedance:
            report['content_endpoint'] = base + '/videos/' + encoded_id + '/content'

        def poll():
            response = call('poll', task_url)
            if (response.get('error') and not response.get('status')) or ('code' in response and response['code'] != 'success'):
                raise RuntimeError('视频查询接口返回业务错误，详见 result.json')
            state = response if is_seedance else response.get('data', response)
            if not isinstance(state, dict):
                raise ValueError('视频查询响应 data 不是 JSON 对象，详见 result.json')
            returned_id = state.get('task_id') or state.get('id')
            if is_seedance and not isinstance(returned_id, str):
                raise ValueError('方舟查询响应缺少任务 ID，详见 result.json')
            if returned_id is not None and returned_id != task_id:
                raise ValueError('视频查询响应任务 ID 与请求不一致，详见 result.json')
            state_path = '$.data' if 'data' in response else '$'
            nested = state.get('data')
            nested = nested if isinstance(nested, dict) else {}
            usage = nested.get('usage')
            usage_source = state_path + '.data.usage'
            if not isinstance(usage, dict) or not usage:
                usage = state.get('usage')
                usage_source = state_path + '.usage'
            # 仅使用服务端 usage；缺失不补零，不从 quota 推算或累加轮询计数。
            reported = isinstance(usage, dict) and bool(usage)
            report.update(usage=usage if reported else None,
                          usage_source=usage_source if reported else None,
                          usage_status='reported' if reported else 'not_reported')
            returned_model = state.get('model') or nested.get('model')
            if args.task_id and isinstance(returned_model, str):
                report['model'] = returned_model
            return state

        checkpoint()
        if is_seedance or args.task_id or (args.video_query_api == 'generations' and
                            str(state.get('status', '')).lower() in ('completed', 'succeeded', 'success')):
            # 即使创建响应已完成，也查询详情以取得下载地址和用量。
            state = poll()
        while True:
            if args.task_id and isinstance(state.get('model'), str):
                report['model'] = state['model']
            status = str(state.get('status', '')).lower()
            report['last_status'] = status
            print(redact(f"状态：{status or '缺失'}；已用时 {time.perf_counter() - started:.1f}s", key), flush=True)
            if status in ('completed', 'succeeded', 'success'):
                timing_field = 'query_completion_ms' if args.task_id else 'completion_ms'
                report[timing_field] = round((time.perf_counter() - started) * 1000, 2)
                report['outcome'] = 'completed'
                break
            if status in ('failed', 'failure', 'cancelled', 'canceled', 'expired') or state.get('error'):
                failure = state.get('error')
                reason = (failure.get('message') if isinstance(failure, dict) else failure) or state.get('fail_reason')
                report['fail_reason'] = reason or '接口未返回具体原因'
                raise RuntimeError(f"生成失败：{report['fail_reason']}，详见 result.json")
            if status not in ('queued', 'in_progress', 'pending', 'processing', 'running', 'submitted', 'not_start'):
                raise ValueError(f"无法识别任务状态：{status!r}，详见 result.json")
            if is_seedance and not args.wait:
                report['outcome'] = status
                return 0
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise TimeoutError("达到总等待上限；服务端任务可能仍在运行")
            checkpoint()
            time.sleep(min(args.poll_interval, remaining))
            state = poll()
        result_url = state.get('result_url')
        if is_seedance:
            content = state.get('content')
            result_url = content.get('video_url') if isinstance(content, dict) else None
        if isinstance(result_url, str) and result_url.startswith(('http://', 'https://')):
            report['result_url'] = result_url
        else:
            result_url = None
        # 先落盘下载地址和用量；下载失败或中断也能保留本次任务结果。
        checkpoint()
        try:
            if is_seedance and not result_url:
                raise ValueError('方舟任务已成功，但未返回有效 content.video_url；无法下载')
            print('视频生成成功，正在下载完整视频…', flush=True)
            download_video(args, key, result_url or report['content_endpoint'], not bool(result_url),
                           folder, report, checkpoint)
        except Exception as exc:
            report.update(outcome='completed_download_failed', error=str(exc))
        code = 0 if report['outcome'] == 'completed' else 2
    except KeyboardInterrupt:
        report.update(outcome='completed_download_interrupted' if report.get('download') else 'interrupted',
                      error='用户中断；服务端任务未取消')
        code = 130
    except Exception as exc:
        report.update(outcome='timeout' if isinstance(exc, TimeoutError) else 'failed', error=str(exc))
    finally:
        checkpoint()
        print(redact((folder / 'summary.txt').read_text(encoding='utf-8'), key))
        print(f"详细结果：{folder / 'result.json'}")
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', type=Path,
                        help='指定配置文件；默认读取脚本同目录 openai-api-config')
    parser.add_argument('--api-type', choices=['chat/completions', 'responses', 'video', 'seedance'], help='不指定则显示接口选择菜单')
    parser.add_argument('--task-id', help='已有任务 ID；默认查询，seedance --action delete 时取消/删除')
    parser.add_argument('--action', choices=['create', 'query', 'list', 'delete'],
                        help='仅 seedance：默认创建，有 --task-id 时默认查询；delete 取消/删除指定任务')
    parser.add_argument('--wait', action=argparse.BooleanOptionalAction, default=True,
                        help='seedance 默认轮询；--no-wait 只创建或单次查询，成功时仍下载视频')
    parser.add_argument('--page-num', type=int, default=1, help='seedance 列表页码，默认 1')
    parser.add_argument('--page-size', type=int, default=10, help='seedance 列表每页条数，默认 10')
    parser.add_argument('--filter-status', choices=['queued', 'running', 'cancelled', 'succeeded', 'failed', 'expired'],
                        help='seedance 列表状态筛选')
    parser.add_argument('--filter-model', help='seedance 列表模型/推理接入点筛选')
    parser.add_argument('--filter-task-id', action='append', default=[], help='seedance 列表任务 ID 筛选，可重复')
    parser.add_argument('--first-frame', type=reference_url, help='seedance 首帧图片 URL/asset 标识')
    parser.add_argument('--last-frame', type=reference_url, help='seedance 尾帧图片 URL/asset 标识，需同时指定首帧')
    parser.add_argument('--video-query-api', choices=['videos', 'generations'], default='generations',
                        help='默认 generations 查询 /video/generations/{id} 获取地址和用量；videos 手动使用 /videos/{id}')
    parser.add_argument('--stream', action=argparse.BooleanOptionalAction, default=None,
                        help='文本默认流式；--no-stream 使用非流式，video 不支持 SSE')
    parser.add_argument('--prompt', help='测试提示词；新建测试未指定时交互输入，不使用默认提示词')
    duration = parser.add_mutually_exclusive_group()
    duration.add_argument('--seconds', type=positive_number, help='视频秒数，默认 5 秒；与 --duration 互斥')
    duration.add_argument('--duration', type=positive_number, help='改用 duration 字段传递视频秒数')
    dimensions = parser.add_mutually_exclusive_group()
    dimensions.add_argument('--size', help='指定像素尺寸并跳过分辨率菜单；与 --resolution 互斥')
    dimensions.add_argument('--resolution', help='指定分辨率并跳过菜单，例如 720p、1080p、4k；不同时发送 size')
    parser.add_argument('--ratio', default='16:9', help='画面比例，默认 16:9')
    parser.add_argument('--generate-audio', action=argparse.BooleanOptionalAction, default=True,
                        help='请求同步音频（默认开启），--no-generate-audio 关闭')
    parser.add_argument('--watermark', action=argparse.BooleanOptionalAction, default=False,
                        help='水印（默认关闭），--watermark 开启')
    parser.add_argument('--content', action='store_true', help='使用 content 数组发送提示词')
    parser.add_argument('--reference-image', action='append', type=reference_url, default=[],
                        help='参考图片 HTTP(S) URL 或 asset://asset-<素材ID>，可重复指定；跳过素材菜单')
    parser.add_argument('--reference-audio', action='append', type=reference_url, default=[],
                        help='参考音频 HTTP(S) URL 或 asset://asset-<素材ID>，可重复指定；跳过素材菜单')
    parser.add_argument('--no-reference', action='store_true', help='不使用参考素材，跳过素材菜单')
    video_reference = parser.add_mutually_exclusive_group()
    video_reference.add_argument('--reference-video', action='append', type=reference_url, default=[],
                                 help='参考视频 HTTP(S) URL 或 asset://asset-<素材ID>，可重复指定；跳过素材菜单')
    video_reference.add_argument('--no-reference-video', action='store_true',
                                 help='兼容旧参数：不使用参考视频并跳过素材菜单；仍可显式指定图片或音频')
    parser.add_argument('--poll-interval', type=positive_number, default=5)
    parser.add_argument('--download-timeout', type=positive_number, default=300,
                        help='视频下载时间上限（秒，默认 300），独立于生成等待 --max-wait')
    parser.add_argument('--request-timeout', type=positive_number, default=120)
    parser.add_argument('--max-wait', type=positive_number, default=1800, help='总等待上限，默认 1800 秒')
    parser.add_argument('--output', type=Path, help='结果目录，默认脚本同目录 test-results/<接口类型>/')
    args = parser.parse_args()
    if args.no_reference and (args.reference_video or args.reference_image or args.reference_audio):
        parser.error('--no-reference 不可与参考素材参数同时使用')
    try:
        if args.task_id is not None:
            args.task_id = validate_task_id(args.task_id)
            if args.api_type not in (None, 'video', 'seedance'):
                raise ValueError('--task-id 仅支持 video 或 seedance')
            args.api_type = args.api_type or 'video'
        script_dir = Path(__file__).resolve().parent
        config = args.config if args.config is not None else script_dir / 'openai-api-config'
        profiles = load_profiles(config, required=args.config is not None)
        key, base = select_credentials(profiles)
        interactive_api = args.api_type is None
        args.api_type = args.api_type or select_api_type()
        is_seedance = args.api_type == 'seedance'
        is_video = args.api_type in ('video', 'seedance')
        if is_seedance:
            if args.action is None and interactive_api and args.task_id is None:
                args.action = select_seedance_action()
                if args.action in ('query', 'delete'):
                    args.task_id = input('任务 ID：')
            args.action = args.action or ('query' if args.task_id is not None else 'create')
            if args.action in ('query', 'delete') and args.task_id is None:
                raise ValueError('--action query/delete 必须提供 --task-id')
            if args.action in ('create', 'list') and args.task_id is not None:
                raise ValueError('--action create/list 不可与 --task-id 同用；列表筛选请用 --filter-task-id')
            if args.size:
                raise ValueError('seedance 使用 --resolution，不支持 --size')
            if args.video_query_api != 'generations':
                raise ValueError('seedance 使用原生查询路径，不支持 --video-query-api videos')
            seconds = args.duration if args.duration is not None else args.seconds
            if seconds is not None and not seconds.is_integer():
                raise ValueError('seedance 时长必须为整数秒')
            if args.page_num < 1 or args.page_size < 1:
                raise ValueError('页码和每页条数必须为正整数')
            if args.last_frame and not args.first_frame:
                raise ValueError('--last-frame 必须与 --first-frame 同用')
            if (args.first_frame or args.last_frame) and (
                    args.reference_video or args.reference_image or args.reference_audio or args.no_reference):
                raise ValueError('首尾帧不可与参考素材或 --no-reference 混用')
            has_filters = args.filter_status or args.filter_model or args.filter_task_id
            if args.action != 'list' and (has_filters or args.page_num != 1 or args.page_size != 10):
                raise ValueError('分页和筛选参数仅用于 --action list')
        elif (args.action is not None or not args.wait or args.first_frame or args.last_frame
              or args.filter_status or args.filter_model or args.filter_task_id
              or args.page_num != 1 or args.page_size != 10):
            raise ValueError('--action、--no-wait、首尾帧和列表参数仅支持 --api-type seedance')
        if args.task_id is not None:
            args.task_id = validate_task_id(args.task_id)
        new_request = args.action == 'create' if is_seedance else not args.task_id
        if is_video and args.stream:
            raise ValueError('video/seedance 使用异步任务轮询，不支持 --stream')
        if args.stream is None:
            args.stream = not is_video
        if is_video and new_request and args.size is None and args.resolution is None:
            args.resolution = select_video_resolution()
        if (is_video and new_request and not args.no_reference and not args.no_reference_video
                and not (args.first_frame or args.last_frame)
                and not (args.reference_video or args.reference_image or args.reference_audio)):
            references = select_reference_media()
            args.reference_video = references['video']
            args.reference_image = references['image']
            args.reference_audio = references['audio']
        if args.output is None:
            directory = 'openai-video' if args.api_type == 'video' else args.api_type.replace('/', '-')
            args.output = script_dir / 'test-results' / directory
        model = (os.environ.get('OPENAI_MODEL_ID') or input('Model ID：')).strip() if new_request else None
        if not key or (new_request and not model):
            raise ValueError('key 和 model ID 不可为空')
        if new_request:
            if args.prompt is None:
                args.prompt = input('提示词：')
            if not args.prompt.strip():
                raise ValueError('提示词不可为空')
        return run(args, key, base, model)
    except (ValueError, EOFError) as exc:
        print(f'输入错误：{exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    sys.exit(main())
