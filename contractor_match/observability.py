"""Request-scoped diagnostics. Only explicit metadata is written to stderr."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
import json
import logging
from pathlib import Path
import time
from uuid import uuid4


ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)


@dataclass
class RequestTrace:
    request_id: str = field(default_factory=lambda: uuid4().hex)
    stage_ms: dict[str, float] = field(default_factory=dict)
    failed_stage: str | None = None


_trace: ContextVar[RequestTrace | None] = ContextVar("firebird_trace", default=None)


def configure_logging() -> None:
    """Configure this package only; leave host/root/third-party logging alone."""
    package = logging.getLogger("contractor_match")
    if not any(getattr(handler, "firebird_handler", False) for handler in package.handlers):
        handler = logging.StreamHandler()  # stderr; CLI --json stdout stays parseable.
        handler.firebird_handler = True
        handler.setFormatter(logging.Formatter("%(message)s"))
        package.addHandler(handler)
    package.setLevel(logging.INFO)
    package.propagate = False


@contextmanager
def request_trace():
    trace = RequestTrace()
    token = _trace.set(trace)
    try:
        yield trace
    finally:
        _trace.reset(token)


def log_event(target: logging.Logger, event: str, *, level: int = logging.INFO, **metadata) -> None:
    trace = _trace.get()
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "level": logging.getLevelName(level), "event": event,
        "request_id": trace.request_id if trace else None, **metadata,
    }
    target.log(level, json.dumps(payload, ensure_ascii=False, allow_nan=False))


def safe_exception(error: BaseException) -> dict:
    """No exception messages, source lines, arguments, local variables or full paths."""
    frames = []
    tb = error.__traceback__
    while tb is not None:
        code = tb.tb_frame.f_code
        path = Path(code.co_filename)
        try:
            filename = path.relative_to(ROOT).as_posix()
        except ValueError:
            filename = path.name
        frames.append({"file": filename, "function": code.co_name, "line": tb.tb_lineno})
        tb = tb.tb_next
    result = {"type": type(error).__name__, "frames": frames[-12:]}
    if isinstance(error, BaseExceptionGroup):
        # Retain useful leaf locations when a framework wraps the original error.
        leaf = error
        while isinstance(leaf, BaseExceptionGroup):
            leaf = leaf.exceptions[0]
        result["cause"] = safe_exception(leaf)
    return result


def log_exception(target: logging.Logger, error: BaseException) -> None:
    log_event(target, "api_internal_error", level=logging.ERROR, exception=safe_exception(error))


@contextmanager
def stage(name: str):
    trace = _trace.get()
    start = time.perf_counter()
    try:
        yield
    except Exception:
        if trace is not None:
            trace.failed_stage = name
        raise
    finally:
        if trace is not None:
            trace.stage_ms[name] = round((time.perf_counter() - start) * 1000, 3)


def observed_recommendation(function):
    @wraps(function)
    def observed(*args, **kwargs):
        def run():
            trace = _trace.get()
            start = time.perf_counter()
            try:
                result = function(*args, **kwargs)
            except Exception as error:
                log_event(logger, "recommendation_failed", level=logging.WARNING,
                          duration_ms=round((time.perf_counter() - start) * 1000, 3),
                          stage_ms=dict(trace.stage_ms), failed_stage=trace.failed_stage,
                          error_type=type(error).__name__)
                raise
            log_event(logger, "recommendation_complete",
                      duration_ms=round((time.perf_counter() - start) * 1000, 3),
                      stage_ms=dict(trace.stage_ms), status=result.status,
                      counts=result.counts.model_dump(), reasons=result.reasons,
                      ranking_mode=result.ranking_mode, ranking_reason=result.ranking_reason,
                      ai_mode=result.ai_mode, ai_reason=result.ai_reason)
            return result

        if _trace.get() is not None:
            return run()
        with request_trace():  # Direct CLI/library calls get their own isolated trace.
            return run()
    return observed


class RequestLoggingMiddleware:
    """Pure ASGI wrapper also covers OPTIONS; each HTTP request gets a new server ID."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        with request_trace() as trace:
            start = time.perf_counter()
            status = None

            async def send_with_diagnostics(message):
                nonlocal status
                if message["type"] == "http.response.start":
                    status = message["status"]
                    elapsed = (time.perf_counter() - start) * 1000
                    headers = [(key, value) for key, value in message.get("headers", [])
                               if key.lower() not in {b"x-request-id", b"x-process-time-ms"}]
                    headers.extend([(b"x-request-id", trace.request_id.encode("ascii")),
                                    (b"x-process-time-ms", f"{elapsed:.3f}".encode("ascii"))])
                    message = {**message, "headers": headers}
                await send(message)

            try:
                await self.app(scope, receive, send_with_diagnostics)
            finally:
                route = scope.get("route")
                method = scope.get("method")
                # Log the route template, never the raw URL/query/headers/body/IP.
                log_event(logger, "http_request", route=getattr(route, "path", "unmatched"),
                          method=method if method in {"GET", "POST", "OPTIONS", "HEAD", "PUT", "PATCH", "DELETE"} else "OTHER",
                          http_status=status, duration_ms=round((time.perf_counter() - start) * 1000, 3))
