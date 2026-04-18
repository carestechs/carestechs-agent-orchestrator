"""ASGI middleware: raw-body capture for webhook HMAC verification."""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RawBodyMiddleware:
    """Stash the raw request body on ``scope["state"]["raw_body"]`` for routes
    under a configurable prefix (default ``/hooks/``).

    Outside the prefix the middleware is a no-op.  The body is replayed to
    downstream handlers so they can still ``await request.body()`` or parse
    JSON normally.
    """

    def __init__(self, app: ASGIApp, *, prefix: str = "/hooks/") -> None:
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith(self.prefix):
            await self.app(scope, receive, send)
            return

        # Read the entire body
        body = b""
        more = True
        while more:
            msg: Message = await receive()
            body += msg.get("body", b"")
            more = msg.get("more_body", False)

        # Stash on scope state so request.state.raw_body is available
        scope.setdefault("state", {})
        scope["state"]["raw_body"] = body

        # Replay the body as a single message for downstream handlers
        async def replay() -> Message:
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, send)
