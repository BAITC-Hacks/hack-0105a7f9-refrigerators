"""Bound JSON input before FastAPI decodes it, including chunked requests."""
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .models import ApiError, ErrorResponse

MAX_REQUEST_BYTES = 64 * 1024


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"].rstrip("/") != "/recommendations":
            return await self.app(scope, receive, send)

        async def reject():
            payload = ErrorResponse(error=ApiError(
                code="request_too_large", message="Запрос слишком большой. Максимум — 64 КиБ.", details=[],
            ))
            await JSONResponse(payload.model_dump(), status_code=413)(scope, receive, send)

        for key, value in scope.get("headers", []):
            if key == b"content-length":
                try:
                    if int(value) > MAX_REQUEST_BYTES:
                        return await reject()
                except ValueError:
                    pass  # Still enforce the actual streamed byte count below.

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > MAX_REQUEST_BYTES:
                return await reject()
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, bounded_receive, send)
