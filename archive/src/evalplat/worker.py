"""EvalPlat 普通 Worker 常驻进程入口。"""

import logging
import os
import signal
import socket
import threading

from evalplat.config import get_settings
from evalplat.core.crypto import get_crypto_service
from evalplat.core.logging import setup_logging
from evalplat.db.session import SessionLocal
from evalplat.services.run_queue_service import fail_expired_runs
from evalplat.services.run_worker import process_one
from evalplat.services.security_service import build_ssrf_guard
from evalplat.services.worker_heartbeat_service import clear_heartbeat, record_heartbeat
from evalplat.services.task_precheck_service import get_global_concurrency_limit

logger = logging.getLogger(__name__)


def run_forever(stop_event: threading.Event | None = None) -> None:
    """持续消费 Run；每次迭代使用独立数据库 Session。"""
    settings = get_settings()
    setup_logging(settings.log_level)
    stop = stop_event or threading.Event()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    crypto = get_crypto_service()
    while not stop.is_set():
        handled = False
        session = SessionLocal()
        try:
            record_heartbeat(session, worker_id)
            fail_expired_runs(session)
            # Re-read the whitelist each iteration so an operator change takes
            # effect for the next request without restarting the worker.
            guard = build_ssrf_guard(session)
            handled = process_one(
                session,
                worker_id,
                crypto,
                guard,
                max_concurrency=get_global_concurrency_limit(session, settings.worker_max_concurrency),
                session_factory=SessionLocal,
            ) is not None
        except Exception:
            session.rollback()
            logger.exception("worker iteration failed")
        finally:
            session.close()
        if not handled:
            stop.wait(settings.worker_poll_interval_seconds)
    session = SessionLocal()
    try:
        clear_heartbeat(session, worker_id)
    except Exception:
        session.rollback()
        logger.exception("failed to clear worker heartbeat")
    finally:
        session.close()


def main() -> None:
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    run_forever(stop)


if __name__ == "__main__":
    main()
