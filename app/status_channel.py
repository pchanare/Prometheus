"""
status_channel.py — direct WebSocket status sender for real-time tool progress.

HOW IT WORKS
─────────────────────────────────────────────────────────────────────────────
Tools call push_status(text) or clear_status() from the thread-pool executor.

Instead of a 300 ms polling loop, messages are scheduled on the event loop
immediately via asyncio.run_coroutine_threadsafe — so they arrive at the
browser at the exact moment the tool reaches that line, not after a poll delay.

Call init(loop, send_fn) once per WebSocket session from server.py.
"""

import asyncio
import json
import logging

log = logging.getLogger("prometheus.status_channel")

_loop: asyncio.AbstractEventLoop | None = None
_send_fn = None   # websocket.send_text — the coroutine function


def init(loop: asyncio.AbstractEventLoop, send_fn) -> None:
    """Register the running event loop and websocket send function. Called once per session."""
    global _loop, _send_fn
    _loop = loop
    _send_fn = send_fn


def push_status(text: str) -> None:
    """
    Show *text* in the browser's status-detail element immediately.
    Safe to call from any thread (tool pool or event loop thread).
    """
    if _loop is None or _send_fn is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(
            _send_fn(json.dumps({"type": "status", "text": text})),
            _loop,
        )
    except Exception as exc:
        log.debug("push_status: %s", exc)


def clear_status() -> None:
    """
    Hide the status-detail element in the browser.
    Call this immediately after a tool operation completes.
    Safe to call from any thread.
    """
    push_status("")
