"""
status_channel.py — thread-safe side-channel for real-time tool status messages.

PATTERN (mirrors solar_mockup._pending_images):
  Tools call push_status(text) from the thread-pool executor.
  server.py's status_loop drains pop_status_messages() every 300 ms and sends
  { type: "status", text: "..." } WebSocket messages to the browser.

Thread safety:
  collections.deque.append() and popleft() are GIL-protected in CPython,
  making them safe to call from multiple threads without an explicit lock.
"""

import collections

_queue: collections.deque = collections.deque()


def push_status(text: str) -> None:
    """Push a status string from a tool (runs in thread-pool executor)."""
    _queue.append(text)


def pop_status_messages() -> list:
    """Drain all pending status messages. Called from the async status_loop."""
    msgs = []
    while True:
        try:
            msgs.append(_queue.popleft())
        except IndexError:
            break
    return msgs
