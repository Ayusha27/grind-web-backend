"""Local dev server.

Why this exists rather than `uvicorn app.main:app`: on Windows, uvicorn builds a
ProactorEventLoop, and psycopg's async mode cannot run on it — the app dies during
startup on the first database connection. uvicorn only picks a SelectorEventLoop
there as a side effect of running a subprocess (i.e. with --reload), which is not
something to depend on. This selects the loop explicitly.

On Linux this is a plain uvicorn run; production uses Gunicorn (deploy/gunicorn.conf.py).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn  # noqa: E402

from app.core.config import settings  # noqa: E402


def main() -> None:
    config = uvicorn.Config(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        log_config=None,        # app/core/logging.py owns log formatting
        access_log=False,       # RequestContextMiddleware emits structured access logs
    )
    server = uvicorn.Server(config)
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    print(f"{settings.APP_NAME} -> http://127.0.0.1:8000  (env={settings.ENV})")
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(server.serve())


if __name__ == "__main__":
    main()
