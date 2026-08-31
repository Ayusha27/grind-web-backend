# deploy/gunicorn.conf.py
import multiprocessing
import os

bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"

# Async workers are CPU-bound, not I/O-bound: one per core (Phase 0.2).
workers = int(os.getenv("WEB_CONCURRENCY", multiprocessing.cpu_count()))
worker_class = "uvicorn.workers.UvicornWorker"

# Nginx already buffers slow clients, so a modest keepalive is fine.
keepalive = 5

# Bound any slow leak. The jitter stops all workers recycling at once,
# which would show up as a throughput cliff every N requests.
max_requests = 10000
max_requests_jitter = 1000

# Must exceed your slowest legitimate request. DB_STATEMENT_TIMEOUT_MS is
# 5s and Razorpay's timeout is 10s, so 30s leaves room without letting a
# wedged worker linger.
timeout = 30
graceful_timeout = 30

# Log to stdout; the container runtime ships it. Our own access line
# (Phase 8.1) carries the request id, so Gunicorn's is disabled.
accesslog = None
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info").lower()

# Fork before touching sockets: connection pools MUST be created per worker
# (that is what the lifespan in Phase 8.2 does), never shared across fork.
preload_app = False
