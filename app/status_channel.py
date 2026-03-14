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

import asyncio
import json
import logging

log = logging.getLogger("prometheus.status_channel")

_send_fn = None   # websocket.send_text — the coroutine function
_loop    = None   # event loop — stored so sync tools can schedule sends


def init(loop, send_fn) -> None:
    """Register the event loop and websocket send function. Called once per session."""
    global _send_fn, _loop
    _send_fn = send_fn
    _loop    = loop


def push_status(text: str) -> None:
    """
    Sync version for use inside tool functions (which run in a thread pool).
    Schedules the WebSocket send on the stored event loop without blocking.
    """
    if _send_fn is None or _loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(
            _send_fn(json.dumps({"type": "status", "text": text})),
            _loop,
        )
    except Exception as exc:
        log.debug("push_status: %s", exc)


def clear_status() -> None:
    """Sync version — hide the status element in the browser."""
    push_status("")


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
