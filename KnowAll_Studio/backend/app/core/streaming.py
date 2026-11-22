"""Deterministic ownership for async streams forwarded by learning routes."""
from contextlib import AsyncExitStack, aclosing

from anyio import CancelScope
from starlette.responses import StreamingResponse


class _shielded_exit:
    async def __aexit__(self, *exc_info):
        with CancelScope(shield=True):
            return await super().__aexit__(*exc_info)


class closing_stream(_shielded_exit, aclosing):
    """Use stdlib closing in the consuming task, shielding only final cleanup."""


class stream_resources(_shielded_exit, AsyncExitStack):
    """Close producer-owned HTTP contexts even when the next read is cancelled."""


class ClosingStreamingResponse(StreamingResponse):
    """Preserve Starlette ASGI handling and close a suspended body on send failure."""

    async def stream_response(self, send):
        async with closing_stream(self.body_iterator):
            await super().stream_response(send)
