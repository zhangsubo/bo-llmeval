#!/usr/bin/env python3
"""Local loopback-only load generator calibration (never calls a real model API)."""
from pathlib import Path
import argparse
import asyncio
import json
import multiprocessing
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.benchmark import BenchmarkConfig, BenchmarkRun, Stage, atomic_json


async def simulator(port_pipe, peak_counter, hold):
    live = 0
    handlers = set()
    async def serve(reader, writer):
        nonlocal live
        task = asyncio.current_task()
        handlers.add(task)
        try:
            while True:
                raw = await reader.readuntil(b'\r\n\r\n')
                length = next((int(line.split(b':', 1)[1]) for line in raw.split(b'\r\n') if line.lower().startswith(b'content-length:')), 0)
                await reader.readexactly(length)
                live += 1
                peak_counter.value = max(peak_counter.value, live)
                try:
                    writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n')
                    await writer.drain()
                    pieces = [
                        {'choices': [{'index': 0, 'delta': {'content': 'hello '}, 'finish_reason': None}]},
                        {'choices': [{'index': 0, 'delta': {'content': 'world'}, 'finish_reason': None}]},
                        {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}], 'usage': {'completion_tokens': 2, 'prompt_tokens': 5}},
                    ]
                    for value in pieces:
                        body = ('data: ' + json.dumps(value) + '\n\n').encode()
                        writer.write(f'{len(body):x}\r\n'.encode() + body + b'\r\n')
                        await writer.drain()
                        await asyncio.sleep(hold / 3)
                    body = b'data: [DONE]\n\n'
                    writer.write(f'{len(body):x}\r\n'.encode() + body + b'\r\n0\r\n\r\n')
                    await writer.drain()
                finally:
                    live -= 1
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            handlers.discard(task)
    server = await asyncio.start_server(serve, '127.0.0.1', 0, backlog=2048)
    port_pipe.send(server.sockets[0].getsockname()[1])
    async with server:
        await server.serve_forever()


def simulator_process(pipe, peak, hold):
    asyncio.run(simulator(pipe, peak, hold))


async def calibrate(concurrencies, requests, hold, output):
    ctx = multiprocessing.get_context('spawn')
    parent_pipe, child_pipe = ctx.Pipe()
    peak_counter = ctx.Value('i', 0)
    process = ctx.Process(target=simulator_process, args=(child_pipe, peak_counter, hold), daemon=True)
    process.start()
    if not await asyncio.to_thread(parent_pipe.poll, 15):
        process.terminate()
        raise RuntimeError('本地模拟服务未启动')
    port = parent_pipe.recv()
    records = []
    try:
        for concurrency in concurrencies:
            peak_counter.value = 0
            config = BenchmarkConfig(model='local-sse-simulator', stages=[Stage(concurrency=concurrency,
                requests=max(requests, concurrency * 2), ramp_up_s=1)], timeout_s=60, total_timeout_s=600)
            run = BenchmarkRun(config, output)
            result = await run.execute(f'http://127.0.0.1:{port}/v1', 'local-test-only')
            evidence = {'run_id': run.run_id, 'target_concurrency': concurrency, 'server_peak_connections': peak_counter.value,
                'simulated_hold_s': hold, 'ramp_up_s': 1, 'same_process_simulator': False, 'environment': run.environment,
                'summary': result['summary'], 'warning': '本地短时校准，不等同五分钟稳态或真实模型容量认证'}
            atomic_json(run.folder / 'calibration.json', evidence)
            records.append(evidence)
            print(json.dumps({'target': concurrency, 'observed_peak': peak_counter.value, 'requests': result['summary']['requests'],
                  'successes': result['summary']['successes'], 'folder': str(run.folder)}, ensure_ascii=False), flush=True)
    finally:
        process.terminate()
        await asyncio.to_thread(process.join, 5)
        parent_pipe.close()
        child_pipe.close()
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--concurrency', type=int, nargs='+', default=[100, 200, 500])
    parser.add_argument('--requests', type=int, default=1000)
    parser.add_argument('--hold', type=float, default=2)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent / 'test-results')
    args = parser.parse_args()
    if min(args.concurrency) < 1 or args.requests < 1 or args.hold <= 0:
        parser.error('并发、请求数和模拟持有时间必须为正数')
    records = asyncio.run(calibrate(args.concurrency, args.requests, args.hold, args.output))
    return 0 if all(r['summary']['requests'] == r['summary']['successes'] for r in records) else 2


if __name__ == '__main__':
    raise SystemExit(main())
