"""EvalPlat 独立代码 Worker 常驻进程入口。"""

import logging
import os
import signal
import socket
import threading

from evalplat.config import get_settings
from evalplat.core.logging import setup_logging
from evalplat.db.session import SessionLocal
from evalplat.services.code_execution_service import fail_expired_code_executions
from evalplat.services.code_worker import process_one
from evalplat.services.worker_heartbeat_service import clear_heartbeat, record_heartbeat

logger = logging.getLogger(__name__)


def run_forever(stop_event: threading.Event | None = None) -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    stop = stop_event or threading.Event()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    heartbeat_stop = threading.Event()

    def heartbeat_loop() -> None:
        while not heartbeat_stop.wait(20.0):
            heartbeat_session = SessionLocal()
            try:
                record_heartbeat(
                    heartbeat_session,
                    worker_id,
                    worker_type="code",
                    status="running",
                )
            except Exception:
                heartbeat_session.rollback()
                logger.exception("code worker heartbeat iteration failed")
            finally:
                heartbeat_session.close()

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        name=f"code-worker-heartbeat-{worker_id}",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        while not stop.is_set():
            handled = False
            session = SessionLocal()
            try:
                record_heartbeat(session, worker_id, worker_type="code")
                fail_expired_code_executions(session)
                handled = process_one(session, worker_id) is not None
            except Exception:
                session.rollback()
                logger.exception("code worker iteration failed")
            finally:
                session.close()
            if not handled:
                stop.wait(settings.worker_poll_interval_seconds)
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=5)
        session = SessionLocal()
        try:
            clear_heartbeat(session, worker_id, worker_type="code")
        except Exception:
            session.rollback()
            logger.exception("failed to clear code worker heartbeat")
        finally:
            session.close()


def main() -> None:
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    run_forever(stop)


if __name__ == "__main__":
    main()
