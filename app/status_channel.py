"""
status_channel.py — WebSocket status sender for real-time tool progress.

HOW IT WORKS
─────────────────────────────────────────────────────────────────────────────
Status messages are sent via ADK's before_tool_callback / after_tool_callback,
which are async and execute BEFORE / AFTER the sync tool function.  This
guarantees the browser sees the status while the tool is actually working.

Call init(loop, send_fn) once per WebSocket session from server.py.
Use async_push_status / async_clear_status from the async callbacks.
"""

import json
import logging

log = logging.getLogger("prometheus.status_channel")

_send_fn = None   # websocket.send_text — the coroutine function


def init(loop, send_fn) -> None:
    """Register the websocket send function. Called once per session."""
    global _send_fn
    _send_fn = send_fn


async def async_push_status(text: str) -> None:
    """
    Send a status message to the browser.  MUST be called from an async
    context (e.g. before/after tool callbacks).  The await ensures the
    WebSocket send completes before returning.
    """
    if _send_fn is None:
        return
    try:
        await _send_fn(json.dumps({"type": "status", "text": text}))
    except Exception as exc:
        log.debug("async_push_status: %s", exc)


async def async_clear_status() -> None:
    """Hide the status element in the browser."""
    await async_push_status("")
