"""
session_memory.py — Persistent key-fact store for Prometheus.

Extracts the handful of facts that matter (address, solar figures, homeowner
name, etc.) from tool responses and saves them to a JSON file next to this
module.  On every new ADK session the stored facts are re-injected as a tiny
[SESSION MEMORY] note so the model never has to ask the user to repeat info.

Survives: context window resets, WebSocket reconnects, server restarts.
Lost when: prometheus_memory.json is manually deleted.
"""

import json
import logging
import os
from datetime import datetime

log = logging.getLogger("prometheus.memory")

_MEMORY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "prometheus_memory.json"
)

# In-process cache — loaded once on import, written on every update.
_mem: dict = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load() -> None:
    global _mem
    if os.path.exists(_MEMORY_FILE):
        try:
            with open(_MEMORY_FILE, "r", encoding="utf-8") as f:
                _mem = json.load(f)
            log.info("session_memory: loaded %d facts from %s", len(_mem), _MEMORY_FILE)
        except Exception as exc:
            log.warning("session_memory: could not load %s: %s", _MEMORY_FILE, exc)
            _mem = {}
    else:
        _mem = {}


def _save() -> None:
    try:
        with open(_MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(_mem, f, indent=2)
    except Exception as exc:
        log.warning("session_memory: could not save %s: %s", _MEMORY_FILE, exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def update(**kwargs) -> None:
    """
    Update one or more memory fields and persist to disk immediately.
    None values and empty strings are ignored so callers can pass raw
    tool-response fields without guarding each one.
    """
    changed = False
    for key, val in kwargs.items():
        if val is not None and str(val).strip() not in ("", "N/A"):
            _mem[key] = val
            changed = True
    if changed:
        _mem["last_updated"] = datetime.now().isoformat()
        _save()
        log.info("session_memory: updated keys=%s", list(kwargs.keys()))


def build_injection() -> str:
    """
    Return a compact [SESSION MEMORY] note to inject at the start of every
    new ADK session.  Returns an empty string when nothing is stored yet.
    """
    if not _mem:
        return ""

    lines = ["── SESSION MEMORY (last known facts — may be overridden by the user) ──────",
             "If the user provides NEW information in this session (a different address,",
             "updated bill amount, etc.) always use their new input over these stored values."]

    if _mem.get("address"):
        lines.append(f"Property address       : {_mem['address']}")
    if _mem.get("homeowner_name"):
        lines.append(f"Homeowner name         : {_mem['homeowner_name']}")
    if _mem.get("state"):
        lines.append(f"State                  : {_mem['state']}")
    if _mem.get("yearly_sunshine_hours"):
        lines.append(f"Annual sunshine        : {_mem['yearly_sunshine_hours']} hrs/year")
    if _mem.get("max_panels"):
        lines.append(f"Recommended panels     : {_mem['max_panels']}")
    if _mem.get("roof_area_m2"):
        lines.append(f"Roof area              : {_mem['roof_area_m2']} m²")
    if _mem.get("upfront_cost_usd"):
        lines.append(f"System cost (pre-tax)  : {_mem['upfront_cost_usd']}")
    if _mem.get("revised_cost_usd") is not None:
        lines.append(f"Cost after incentives  : ${float(_mem['revised_cost_usd']):,.0f}")
    if _mem.get("revised_payback_years") is not None:
        lines.append(f"Payback period         : {_mem['revised_payback_years']} yrs (after incentives)")
    if _mem.get("monthly_bill_usd") is not None:
        lines.append(f"Monthly electricity bill: ${float(_mem['monthly_bill_usd']):,.0f}")
    if _mem.get("roof_age_years") is not None:
        lines.append(f"Roof age               : {_mem['roof_age_years']} years")

    # Image path — only include if the file still exists on disk
    img = _mem.get("last_image_path", "")
    if img and os.path.exists(img):
        lines.append(f"Last uploaded image    : {img}")

    updated = _mem.get("last_updated", "")
    if updated:
        lines.append(f"(Facts recorded: {updated[:10]})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Load on import
# ---------------------------------------------------------------------------
_load()
