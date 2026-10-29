import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.routes import health_router, router
from backend.app.config import get_settings
from backend.app.logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)


class EventBodyLimitMiddleware:
    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app
        self.max_bytes = get_settings().max_request_bytes

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/api/v1/events"
        ):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = 0
            if declared_size > self.max_bytes:
                await self._too_large(send, self.max_bytes)
                return

        buffered: list[dict] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                await self.app(scope, receive, send)
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_bytes:
                await self._too_large(send, self.max_bytes)
                return
            buffered.append(message)
            if not message.get("more_body", False):
                break

        async def replay_receive() -> dict:
            if buffered:
                return buffered.pop(0)
            return await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _too_large(send: Callable, max_bytes: int) -> None:
        body = json.dumps(
            {"detail": f"Request body exceeds {max_bytes} bytes"}
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def create_app() -> FastAPI:
    app = FastAPI(
        title="Hook, Line & Sender",
        version="0.1.0",
        docs_url=None,
        description=(
            "A webhook delivery service: submit an event, save it safely in a "
            "database, and let a separate worker send it to a configured app. "
            "Temporary delivery failures are retried, and you can inspect the "
            "status and history of every attempt.\n\n"
            "The local receiver succeeds immediately by default; retry simulation "
            "is optional. CMS integrations such as Craft CMS "
            "and Payload are not connected yet.\n\n"
            "**Getting started:** click **Authorize** and enter your API key, "
            "then use **POST /api/v1/events** to submit an event and "
            "**GET /api/v1/deliveries/{delivery_id}** to follow its progress. "
            "Acceptance (HTTP 202) means the event was saved, not yet delivered. "
            "Receivers must handle duplicate delivery IDs safely."
        ),
    )
    app.add_middleware(EventBodyLimitMiddleware)
    assets = Path(__file__).parent / "static"
    app.mount("/explorer-assets", StaticFiles(directory=assets), name="explorer-assets")

    @app.get("/docs", include_in_schema=False)
    def explorer() -> FileResponse:
        return FileResponse(assets / "explorer.html")

    @app.get("/activity", include_in_schema=False)
    def activity_dashboard() -> FileResponse:
        return FileResponse(
            assets / "activity.html",
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": (
                    "default-src 'self'; frame-ancestors 'none'; "
                    "base-uri 'none'; form-action 'self'"
                ),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = []
        for error in exc.errors():
            safe_error = {key: value for key, value in error.items() if key != "input"}
            if isinstance(safe_error.get("ctx"), dict):
                safe_error["ctx"] = {
                    key: str(value) for key, value in safe_error["ctx"].items()
                }
            details.append(safe_error)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": details},
        )

    app.include_router(health_router)
    app.include_router(router)
    return app


app = create_app()
