"""
Prometheus – custom FastAPI server.
Bridges browser UI ↔ ADK Live runner with proper audio handling
and multi-mode orchestration support.
"""

import asyncio
import base64
import json
import logging
import os
import sys
import tempfile
import time
import uuid
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv(override=True)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("prometheus")

# Suppress the noisy "1000 None" clean-close error that the ADK live runner
# logs as ERROR whenever a browser disconnects normally (WebSocket code 1000
# is a clean close, not an actual error — ADK just doesn't distinguish).
class _SuppressCleanClose(logging.Filter):
    def filter(self, record):
        return "1000 None" not in str(record.getMessage())

logging.getLogger("google.adk.flows.llm_flows.base_llm_flow").addFilter(_SuppressCleanClose())
logging.getLogger("google.adk").addFilter(_SuppressCleanClose())

# Ensure app/ is on sys.path so sibling imports resolve
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.runners import Runner, RunConfig
from google.adk.sessions import InMemorySessionService
from google.genai import types
from google.genai.types import Modality

from Prometheus.agent import root_agent, _BASE_INSTRUCTION, _DESCRIPTION, _MODEL, _TOOLS


# ── Image resize helper ───────────────────────────────────────────────────────
# Resizes images before sending to Gemini Live to reduce context token usage.
# The ORIGINAL bytes are always saved to disk so mockup generation is unaffected.
_MODEL_IMAGE_MAX_PX = 512   # longer side limit for Gemini analysis context

def _resize_for_model(img_bytes: bytes, max_px: int = _MODEL_IMAGE_MAX_PX) -> bytes:
    """
    Downscale image so the longer side ≤ max_px, maintaining aspect ratio.
    Returns original bytes unchanged if already small enough or if PIL fails.
    Only affects what Gemini sees for analysis — NOT the file saved to disk.
    """
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(img_bytes))
        w, h = img.size
        if max(w, h) <= max_px:
            return img_bytes
        scale = max_px / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        resized = buf.getvalue()
        log.info("_resize_for_model: %dx%d → %dx%d  (%d → %d bytes)",
                 w, h, int(w * scale), int(h * scale), len(img_bytes), len(resized))
        return resized
    except Exception as exc:
        log.warning("_resize_for_model: failed (%s) — using original bytes", exc)
        return img_bytes

# Brain import — used for PDF analysis endpoint only
try:
    from brain import analyze_pdf_bytes as _analyze_pdf
    _brain_ok = True
except Exception as _brain_err:
    log.warning("brain import failed: %s — PDF analysis endpoint disabled", _brain_err)
    _brain_ok = False

# Solar mockup side-channel — tools store image bytes here; the send_loop
# drains them to the browser so the base64 never enters the model's context.
try:
    from solar_mockup import pop_pending_images as _pop_mockup_images
    _mockup_ok = True
except Exception as _mockup_err:
    log.warning("solar_mockup import failed: %s — mockup forwarding disabled", _mockup_err)
    _mockup_ok = False
    def _pop_mockup_images():
        return []

# Session memory is loaded on import (GCS on Cloud Run, local file in dev).
# Do NOT reset here — GCS persistence is the whole point; facts survive restarts.
# Memory is reset only when the user provides a new address (session_memory.reset()).

# Status channel — async before/after tool callbacks send status messages
# to the browser via WebSocket.  Because the callbacks are async they execute
# BEFORE the sync tool blocks the event loop, guaranteeing the browser sees
# the status while the tool is actually working.
try:
    from status_channel import (
        init as _init_status_channel,
        async_push_status as _async_push_status,
        async_clear_status as _async_clear_status,
        async_send_json as _async_send_json,
    )
    _status_ok = True
except Exception as _status_err:
    log.warning("status_channel import failed: %s — status messages disabled", _status_err)
    _status_ok = False
    def _init_status_channel(*a, **kw):
        pass

# Tool name → ordered list of (pill_text, send_tool_flag) status steps.
# send_tool_flag=True on the first entry causes that message to carry the
# 'tool' field, which triggers _addStep() in the browser step-tracker.
# Subsequent False entries only update the pill text — no new step created.
# All messages are sent from _before_tool_cb (async, before the sync tool
# blocks the event loop), giving a multi-step "agent is working" appearance.
_TOOL_STATUS_STEPS = {
    "run_solar_analysis": [
        ("Geocoding your address with Google Maps…",         True),
        ("Querying Google Solar API for rooftop data…",      False),
        ("Analysing sunshine hours and roof capacity…",      False),
        ("Computing optimal panel configuration…",           False),
        ("Fetching tax benefits and local incentives…",      False),
    ],
    "calculate_outdoor_solar": [
        ("Searching live market pricing for solar systems…", True),
        ("Computing outdoor solar financials and payback…",  False),
        ("Applying federal ITC and state incentives…",       False),
    ],
    "calculate_combined_solar": [
        ("Combining rooftop and outdoor system data…",       True),
        ("Computing total incentives and revised payback…",  False),
    ],
    "generate_solar_mockup": [
        ("Composing your solar panel layout…",               True),
        ("Rendering photorealistic mockup with Imagen 3…",   False),
    ],
    "send_all_rfps": [
        ("Generating personalised RFP emails…",              True),
        ("Sending emails to all 3 installers via Gmail…",    False),
    ],
    "find_local_installers": [
        ("Searching Google Maps for local solar installers…", True),
    ],
    "analyze_space_for_solar": [
        ("Analysing your space for ground-mount solar…",     True),
    ],
    "web_search": [
        ("Searching the web for current data…",              True),
    ],
}

_TOOL_ANNOUNCE = {
    "run_solar_analysis":       "Pulling your solar data now — this usually takes about 15 seconds.",
    "calculate_outdoor_solar":  "Calculating your outdoor system costs — just a moment.",
    "calculate_combined_solar": "Calculating your combined system now — just a moment.",
    "send_all_rfps":            "Sending your RFP emails now — this takes about 30 seconds.",
    "find_local_installers":    "Finding local installers near you — just a moment.",
    "generate_solar_mockup":    "Generating your solar mockup — give me about 30 seconds.",
    "analyze_space_for_solar":  "Analysing your space now — just a moment.",
}


async def _before_tool_cb(tool, args, tool_context, **kwargs):
    """ADK before_tool_callback — fires async BEFORE the sync tool runs.

    Sends a sequence of status bar texts with short delays between them so the
    browser shows a rich multi-step progress indicator before the sync tool
    blocks the event loop.  The final asyncio.sleep(0.15) lets the TCP stack
    flush all messages to the browser before the tool takes over the thread.
    """
    tool_name    = getattr(tool, "name", "") or getattr(tool, "__name__", "")
    steps        = _TOOL_STATUS_STEPS.get(tool_name, [(f"⏳ {tool_name}…", True)])
    announce_txt = _TOOL_ANNOUNCE.get(tool_name, "")

    for i, (text, send_tool) in enumerate(steps):
        await _async_push_status(
            text,
            speak=announce_txt if i == 0 else "",
            tool=tool_name    if i == 0 else "",
        )
        if i < len(steps) - 1:
            await asyncio.sleep(0.4)   # brief pause between sub-step updates

    await asyncio.sleep(0.15)   # yield so the TCP stack flushes to the browser NOW
    return None   # None = proceed with normal tool execution


async def _after_tool_cb(tool, args, tool_context, tool_response=None, **kwargs):
    """ADK after_tool_callback — fires async AFTER the sync tool finishes.

    ADK calls this with tool_response as a KEYWORD argument, so the parameter
    must be named 'tool_response' (not 'result') or it will always be None.

    For run_solar_analysis: forward the structured result to the browser so
    the UI can render a solar data card before the model starts speaking.

    IMPORTANT: the sleep(0.08) before the clear is not optional.
    Some tools call push_status() internally via run_coroutine_threadsafe, which
    queues the message on the NEXT event-loop tick.  Without the sleep, the
    async_clear_status() fires FIRST and the thread-queued status update arrives
    AFTER the clear — leaving a stale status pill stuck in the browser.
    """
    tool_name = getattr(tool, "name", "") or getattr(tool, "__name__", "")
    log.info("_after_tool_cb: tool=%s  tool_response type=%s",
             tool_name, type(tool_response).__name__)

    _CARD_MSG_TYPE = {
        "run_solar_analysis":       "solar_data",
        "calculate_outdoor_solar":  "outdoor_data",
        "calculate_combined_solar": "combined_data",
        "find_local_installers":    "installer_data",
    }
    if tool_name in _CARD_MSG_TYPE:
        if isinstance(tool_response, dict) and not tool_response.get("error"):
            msg_type = _CARD_MSG_TYPE[tool_name]
            log.info("_after_tool_cb: sending %s card (%d keys)", msg_type, len(tool_response))
            try:
                await _async_send_json({"type": msg_type, "data": tool_response})
            except Exception as exc:
                log.warning("_after_tool_cb: %s send failed: %s", msg_type, exc)
        else:
            log.warning("_after_tool_cb: card skipped for %s — tool_response=%s",
                        tool_name, str(tool_response)[:120])

    await asyncio.sleep(0.08)   # let any thread-queued push_status() calls flush first
    await _async_clear_status()
    return None   # None = don't modify the tool result

# ---------------------------------------------------------------------------
# Mode registry – add new agents / models here as the project grows
# ---------------------------------------------------------------------------
MODES = {
    "voice": {
        "label": "Voice",
        "description": "Real-time native-audio conversation",
        "response_modalities": [Modality.AUDIO],
        "agent": root_agent,
    },
    "text": {
        "label": "Text",
        "description": "Text-based conversation (faster)",
        "response_modalities": [Modality.TEXT],
        "agent": root_agent,
    },
}

DEFAULT_MODE = "voice"

# ---------------------------------------------------------------------------
# ADK runner helpers
# ---------------------------------------------------------------------------
APP_NAME = "Prometheus"
session_service = InMemorySessionService()

# Counts WebSocket connections since this server process started.
# First connection (=1) gets the greeting; subsequent connections are
# mid-conversation Gemini Live reconnects — no greeting needed.
_ws_connection_count = 0

# Timestamp of the last WebSocket close. Used to distinguish a fast
# Gemini Live reconnect (<600 s) from a browser page refresh (longer gap).
_last_ws_close_time: float = 0.0
_RECONNECT_WINDOW_S: int = 900   # seconds — 15 min safely exceeds Gemini Live ~10 min session limit


def make_runner(mode: str, memory_note: str = "") -> Runner:
    """
    Create a Runner for *mode*, optionally with *memory_note* appended to the
    agent's system instruction.

    When *memory_note* is non-empty the session memory is injected as part of
    the system prompt — the model treats it as ground-truth context and never
    responds to it, unlike injecting it as a user-turn message.
    """
    from google.adk.agents import Agent as _Agent
    cfg = MODES.get(mode, MODES[DEFAULT_MODE])

    instruction = _BASE_INSTRUCTION
    if memory_note:
        instruction = _BASE_INSTRUCTION + "\n\n" + memory_note

    # Always create a fresh Agent so we can attach the before/after tool
    # callbacks that send real-time status messages to the browser.
    agent_kwargs = dict(
        name="Prometheus",
        model=_MODEL,
        description=_DESCRIPTION,
        instruction=instruction,
        tools=_TOOLS,
    )
    if _status_ok:
        agent_kwargs["before_tool_callback"] = _before_tool_cb
        agent_kwargs["after_tool_callback"] = _after_tool_cb

    agent = _Agent(**agent_kwargs)

    return Runner(
        agent=agent,
        app_name=APP_NAME,
        session_service=session_service,
    )


# ---------------------------------------------------------------------------
# TTL-based session pruning
# ---------------------------------------------------------------------------
_SESSION_TTL_SECONDS = 30 * 60          # 30 minutes
_PRUNE_INTERVAL_SECONDS = 5 * 60        # check every 5 minutes

# Maps session_id → (user_id, created_at_epoch)
_session_registry: dict[str, tuple[str, float]] = {}


def _register_session(session_id: str, user_id: str) -> None:
    """Record a newly created session so the pruner can evict it after TTL."""
    _session_registry[session_id] = (user_id, time.monotonic())
    log.info("session_registry: registered %s  total=%d", session_id[:8], len(_session_registry))


async def _prune_sessions() -> None:
    """Background task: delete ADK sessions older than _SESSION_TTL_SECONDS."""
    while True:
        await asyncio.sleep(_PRUNE_INTERVAL_SECONDS)
        now = time.monotonic()
        expired = [
            (sid, uid)
            for sid, (uid, created) in list(_session_registry.items())
            if now - created > _SESSION_TTL_SECONDS
        ]
        for sid, uid in expired:
            try:
                session_service.delete_session(
                    app_name=APP_NAME, user_id=uid, session_id=sid
                )
            except Exception:
                pass  # session may already be gone
            _session_registry.pop(sid, None)
            log.info("session_registry: pruned expired session %s", sid[:8])
        if expired:
            log.info("session_registry: pruned %d sessions  remaining=%d",
                     len(expired), len(_session_registry))


@asynccontextmanager
async def _lifespan(app):  # noqa: ARG001
    pruner = asyncio.create_task(_prune_sessions())
    log.info("session_registry: TTL pruner started (TTL=%ds, interval=%ds)",
             _SESSION_TTL_SECONDS, _PRUNE_INTERVAL_SECONDS)
    try:
        yield
    finally:
        pruner.cancel()
        try:
            await pruner
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Prometheus Solar AI", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/modes")
async def list_modes():
    """Return available modes so the UI can populate its selector."""
    return JSONResponse(
        [
            {"id": k, "label": v["label"], "description": v["description"]}
            for k, v in MODES.items()
        ]
    )


# ---------------------------------------------------------------------------
# PDF analysis endpoint  POST /api/analyze-pdf
# ---------------------------------------------------------------------------
@app.post("/api/analyze-pdf")
async def analyze_pdf(file: UploadFile = File(...)):
    """
    Accept any relevant PDF upload (electricity bill, solar quote, roof inspection,
    HOA rules, building permit, etc.), extract structured data using the PDF
    Specialist model tier, and return a JSON object.

    The browser sends the extracted data back through the WebSocket as a
    { type: 'context_update', data: {...} } message so the agent can use it.
    """
    if not _brain_ok:
        return JSONResponse(
            {"error": "PDF analysis module is not available."},
            status_code=503,
        )

    try:
        pdf_bytes = await file.read()
        log.info("analyze-pdf: received %d bytes (%s)", len(pdf_bytes), file.filename)

        # Run in thread-pool so we don't block the event loop during model I/O
        loop = asyncio.get_event_loop()
        json_str = await loop.run_in_executor(None, _analyze_pdf, pdf_bytes)

        # Parse and re-serialise to validate the JSON
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            log.warning("analyze-pdf: model returned non-JSON: %s", json_str[:200])
            data = {"raw": json_str, "error": "Could not parse model output as JSON"}

        log.info("analyze-pdf: extracted %s", data)
        return JSONResponse({"status": "ok", "data": data})

    except Exception as exc:
        log.error("analyze-pdf error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# WebSocket  /ws?mode=voice|text
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, mode: str = DEFAULT_MODE):
    await websocket.accept()

    # Wire the status channel so tools can push messages directly to this socket.
    # Must be called before the session starts so init(loop, send_fn) is ready
    # when the first tool runs.
    _init_status_channel(asyncio.get_running_loop(), websocket.send_text)

    if mode not in MODES:
        await websocket.send_text(json.dumps({"type": "error", "text": f"Unknown mode: {mode}"}))
        await websocket.close()
        return

    mode_cfg = MODES[mode]
    modalities = mode_cfg["response_modalities"]

    global _ws_connection_count, _last_ws_close_time
    _ws_connection_count += 1
    time_since_close = time.time() - _last_ws_close_time
    # A Gemini Live reconnect happens within ~30 s of session timeout.
    # A browser page refresh takes longer — treat it as a fresh session.
    _is_reconnect = _ws_connection_count > 1 and time_since_close < _RECONNECT_WINDOW_S

    user_id = "user"
    session_id = str(uuid.uuid4())
    log.info("New session %s  mode=%s  connection#=%d%s",
             session_id[:8], mode, _ws_connection_count,
             " (reconnect)" if _is_reconnect else "")

    # ── Session memory → system instruction ─────────────────────────────────
    # Append stored facts to the agent's system instruction so the model
    # treats them as ground-truth context — no user-turn injection, no
    # response triggered.
    _mem_note = ""
    try:
        from session_memory import build_injection as _build_mem
        _mem_note = _build_mem()
        if _mem_note:
            log.info("session_memory: appending %d chars to system instruction for session %s",
                     len(_mem_note), session_id[:8])
    except Exception as _mem_exc:
        log.warning("session_memory: build_injection failed: %s", _mem_exc)

    runner = make_runner(mode, _mem_note)
    # ────────────────────────────────────────────────────────────────────────

    # Create ADK session (handle sync / async ADK versions)
    try:
        await session_service.create_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )
    except TypeError:
        session_service.create_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )
    _register_session(session_id, user_id)

    live_queue = LiveRequestQueue()

    # Disable server-side VAD and use explicit ActivityStart/ActivityEnd signals
    # instead of silence bursts.  This gives deterministic turn control and avoids
    # the ~3-4 minute delay that happens when VAD gets confused by interleaved
    # camera frames in the realtime stream.
    try:
        transcription = types.AudioTranscriptionConfig() if mode == "voice" else None

        # Build RunConfig — try context_window_compression (not yet in all ADK builds)
        run_config_kwargs = dict(
            response_modalities=modalities,
            output_audio_transcription=transcription,
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=True  # we send ActivityStart/End explicitly
                )
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Leda"
                    )
                )
            ),
        )
        # Attach context-window compression if the ADK version supports it
        try:
            from google.adk.runners import ContextWindowCompressionConfig  # type: ignore
            run_config_kwargs["context_window_compression"] = ContextWindowCompressionConfig()
            log.info("Context window compression enabled")
        except ImportError:
            log.info("ContextWindowCompressionConfig not available in this ADK build — skipping")

        run_config = RunConfig(**run_config_kwargs)

    except (AttributeError, Exception) as e:
        log.warning("Could not configure realtime_input_config (%s) — falling back to VAD mode", e)
        run_config = RunConfig(response_modalities=modalities)

    # Notify client which mode is active
    await websocket.send_text(
        json.dumps({"type": "mode", "mode": mode, "label": mode_cfg["label"],
                    "model": str(mode_cfg["agent"].model)})
    )

    # -----------------------------------------------------------------------
    # Loop guard — prevents Gemini Live self-generated turns from reaching
    # the browser.  After every turn_complete the model is "silenced" until
    # the user provides explicit input (activity_start, text, image, etc.).
    # Using a dict so both nested coroutines share the same mutable object.
    # -----------------------------------------------------------------------
    _loop_guard = {"user_spoke": True}   # True → allow first model turn

    # -----------------------------------------------------------------------
    # Send loop: ADK → browser
    # -----------------------------------------------------------------------
    async def send_loop():
        try:
            async for event in runner.run_live(
                user_id=user_id,
                session_id=session_id,
                live_request_queue=live_queue,
                run_config=run_config,
            ):
                # ── Log every event (actions.turn_complete is the key field) ──
                actions = getattr(event, "actions", None)
                log.info("ADK event: %s | server_content=%s | content=%s | turn_complete=%s | interrupted=%s",
                         type(event).__name__,
                         bool(getattr(event, "server_content", None)),
                         bool(getattr(event, "content", None)),
                         bool(actions and getattr(actions, "turn_complete", False)),
                         bool(actions and getattr(actions, "skip_summarization", False)))

                # ── Audio / text via server_content (fallback for other models) ─
                # IMPORTANT: audio bytes must be sent BEFORE turn_complete so the
                # browser doesn't reset to "Ready" while audio is still queued.
                server_content = getattr(event, "server_content", None)
                if server_content:
                    model_turn = getattr(server_content, "model_turn", None)
                    if model_turn:
                        for part in getattr(model_turn, "parts", []) or []:
                            inline = getattr(part, "inline_data", None)
                            if inline and getattr(inline, "data", None):
                                if _loop_guard["user_spoke"]:
                                    await websocket.send_bytes(inline.data)
                                else:
                                    log.warning("loop-guard: suppressing self-generated audio (server_content)")
                            if getattr(part, "text", None):
                                if _loop_guard["user_spoke"]:
                                    await websocket.send_text(
                                        json.dumps({"type": "transcript", "role": "model", "text": part.text})
                                    )
                    output_tx = getattr(server_content, "output_transcription", None)
                    if output_tx and getattr(output_tx, "text", None):
                        if _loop_guard["user_spoke"]:
                            await websocket.send_text(
                                json.dumps({"type": "transcript", "role": "model", "text": output_tx.text})
                            )
                    # server_content turn_complete / interrupted (non-native-audio models)
                    if getattr(server_content, "turn_complete", False):
                        log.info("turn_complete via server_content (fallback)")
                        await websocket.send_text(json.dumps({"type": "turn_complete"}))
                        _loop_guard["user_spoke"] = False
                    if getattr(server_content, "interrupted", False):
                        log.info("interrupted via server_content (fallback) — keeping model audio suppressed")
                        _loop_guard["user_spoke"] = False
                        await websocket.send_text(json.dumps({"type": "turn_complete"}))

                # ── Content events (audio / text via event.content path) ─────────
                content = getattr(event, "content", None)
                if content:
                    role  = getattr(content, "role", "model")
                    parts = getattr(content, "parts", []) or []
                    for part in parts:
                        text   = getattr(part, "text", None)
                        inline = getattr(part, "inline_data", None)
                        if text:
                            if _loop_guard["user_spoke"]:
                                await websocket.send_text(
                                    json.dumps({"type": "transcript", "role": role, "text": text})
                                )
                            else:
                                log.warning("loop-guard: suppressing self-generated text (content)")
                        if inline and getattr(inline, "data", None):
                            if _loop_guard["user_spoke"]:
                                await websocket.send_bytes(inline.data)
                            else:
                                log.warning("loop-guard: suppressing self-generated audio (content)")

                # ── Solar mockup side-channel drain ──────────────────────────────
                # generate_solar_mockup() stores image bytes in solar_mockup._pending_images
                # keyed by a short UUID, then returns only a tiny dict to the voice model.
                # This prevents the 2-3 MB base64 blob from entering the 32K context window.
                # We drain the store after every ADK event so the image reaches the browser
                # as soon as the tool finishes (model response event arrives right after).
                for img_info in _pop_mockup_images():
                    log.info("solar_mockup image ready (id=%s, %d chars) — forwarding to browser",
                             img_info["image_id"], len(img_info["image_b64"]))
                    await websocket.send_text(json.dumps({
                        "type":      "solar_mockup",
                        "image_b64": img_info["image_b64"],
                        "mime_type": img_info.get("mime_type", "image/jpeg"),
                        "message":   "",
                    }))

                # ── turn_complete / interrupted — sent LAST so all audio bytes
                # for this event reach the browser before the state resets.
                # With gemini-live-*-native-audio the final audio chunk and
                # turn_complete often arrive in the same ADK event; sending
                # turn_complete first caused the browser to flash "Ready" while
                # audio was still playing, then re-enter "Speaking…".
                if actions:
                    if getattr(actions, "turn_complete", False):
                        log.info("turn_complete via event.actions")
                        await websocket.send_text(json.dumps({"type": "turn_complete"}))
                        _loop_guard["user_spoke"] = False
                        log.info("loop-guard: awaiting user input before next model turn")
                    # interrupted fires when the user speaks over the model.
                    # Keep user_spoke=False so any follow-up model turn generated
                    # from stale tool context stays silent until the user finishes
                    # speaking (activity_end re-enables it).
                    if getattr(actions, "interrupted", False):
                        log.info("interrupted via event.actions — keeping model audio suppressed until activity_end")
                        _loop_guard["user_spoke"] = False
                        await websocket.send_text(json.dumps({"type": "turn_complete"}))

            # ── Generator exhausted (session ended cleanly) ───────────────────
            # Close the WebSocket so the browser reconnects with a fresh session.
            log.info("run_live generator exhausted — closing WS for fresh session")
            try:
                await websocket.close(1000, "Session complete")
            except Exception:
                pass

        except WebSocketDisconnect:
            log.info("Send loop: browser disconnected")
        except Exception as exc:
            log.info("Send loop ended with error: %s", exc)
            try:
                await websocket.close(1011, "Live session ended")
            except Exception:
                pass

    # ── Auto-greeting trigger ────────────────────────────────────────────────
    # Gemini Live does not auto-speak at session start — it waits for user input.
    # We delay the [SESSION_START] injection by 0.6 s so that if the user sends
    # a message (e.g. "hello") at the same moment the server starts up, the
    # greeting turn is skipped and we avoid two overlapping model responses.
    # The _user_spoke_first flag is set by receive_loop on the very first user
    # text / voice / image message.
    _user_spoke_first = {"val": False}

    async def receive_loop():
        audio_chunks = 0
        try:
            while True:
                message = await websocket.receive()
                raw_bytes = message.get("bytes")
                raw_text  = message.get("text")

                if raw_bytes:
                    _user_spoke_first["val"] = True
                    live_queue.send_realtime(
                        types.Blob(data=raw_bytes, mime_type="audio/pcm;rate=16000")
                    )
                    audio_chunks += 1
                    if audio_chunks == 1:
                        log.info("First audio chunk received (%d bytes)", len(raw_bytes))
                    if audio_chunks % 20 == 0:
                        log.info("Audio chunks forwarded: %d", audio_chunks)

                elif raw_text:
                    payload = json.loads(raw_text)
                    kind = payload.get("type")

                    if kind == "text":
                        _user_spoke_first["val"] = True
                        _loop_guard["user_spoke"] = True
                        live_queue.send_content(
                            types.Content(
                                role="user",
                                parts=[types.Part(text=payload["content"])],
                            )
                        )

                    elif kind == "image":
                        img_bytes = base64.b64decode(payload["data"])
                        live_queue.send_realtime(
                            types.Blob(data=_resize_for_model(img_bytes), mime_type="image/jpeg")
                        )

                    elif kind == "camera_on":
                        _user_spoke_first["val"] = True
                        _loop_guard["user_spoke"] = True
                        img_bytes = base64.b64decode(payload["data"])
                        log.info("camera_on: first frame received (%d bytes) — triggering visual commentary", len(img_bytes))
                        live_queue.send_content(
                            types.Content(
                                role="user",
                                parts=[
                                    types.Part(
                                        inline_data=types.Blob(data=img_bytes, mime_type="image/jpeg")
                                    ),
                                    types.Part(
                                        text=(
                                            "I've just turned on my camera. "
                                            "Please look at this space and: "
                                            "1) Identify whether it's a rooftop or house exterior, or an outdoor space "
                                            "(backyard, patio, yard, open land, etc.). "
                                            "2) Describe what you see and call out any shading sources or obstacles "
                                            "(trees, chimneys, neighbouring structures, skylights) that would affect solar output. "
                                            "3) Give a specific solar recommendation for this exact space — rooftop solar if "
                                            "it's a roof, or canopy/ground-mount for outdoor spaces — and factor any visible "
                                            "obstacles into your recommendation (e.g. shade from a tree reduces viable panel area). "
                                            "4) Invite me to take a clear photo and upload it for a full detailed analysis."
                                        )
                                    ),
                                ],
                            )
                        )

                    elif kind == "capture":
                        _user_spoke_first["val"] = True
                        _loop_guard["user_spoke"] = True
                        img_bytes = base64.b64decode(payload["data"])
                        tmp_path = os.path.join(
                            tempfile.gettempdir(),
                            f"prometheus_{uuid.uuid4().hex[:8]}.jpg",
                        )
                        with open(tmp_path, "wb") as _f:
                            _f.write(img_bytes)
                        log.info("Capture saved → %s (%d bytes)", tmp_path, len(img_bytes))
                        try:
                            from session_memory import update as _mem
                            _mem(last_image_path=tmp_path)
                        except Exception:
                            pass

                        user_label = payload.get("label")
                        if user_label:
                            label = f"{user_label}\n[Image saved at: {tmp_path}]"
                        else:
                            label = (
                                f"I just uploaded this image (saved at: {tmp_path}). "
                                "Look at what the image shows:\n"
                                "- If it shows a ROOFTOP or house exterior: describe the roof, note any "
                                "shading obstacles (trees, chimneys, skylights, neighbouring structures), "
                                "and ask what they would like to do next (rooftop solar analysis or mockup).\n"
                                f"- If it shows an OUTDOOR SPACE (backyard, garden, courtyard, patio, open land): "
                                f"call analyze_space_for_solar with image_path=\"{tmp_path}\" and the "
                                "appropriate space_type. Note any visible shading or obstacles in your spoken response.\n"
                                "Always describe what you see before taking any action."
                            )
                        log.info("Capture label: %r", label[:80])
                        # Send a resized copy to Gemini Live to save context tokens.
                        # img_bytes (full resolution) is already on disk for mockup generation.
                        live_queue.send_content(
                            types.Content(
                                role="user",
                                parts=[
                                    types.Part(
                                        inline_data=types.Blob(
                                            data=_resize_for_model(img_bytes),
                                            mime_type="image/jpeg",
                                        )
                                    ),
                                    types.Part(text=label),
                                ],
                            )
                        )

                    elif kind == "context_update":
                        data = payload.get("data", {})
                        log.info("context_update received: type=%s", data.get("document_type", "unknown"))

                        doc_type  = data.get("document_type", "Document")
                        summary   = data.get("summary", "")
                        key_facts = data.get("key_facts") or []

                        lines = [f"[SYSTEM NOTE — {doc_type} analysed by PDF Specialist]"]
                        if summary:
                            lines.append(summary)
                        for fact in key_facts:
                            k = fact.get("key", "").strip()
                            v = fact.get("value", "")
                            if k and v is not None and str(v).strip():
                                lines.append(f"  {k}: {v}")
                        lines.append(
                            "Use all information above when answering the user's questions. "
                            "Do NOT ask the user for data that is already present here."
                        )

                        # Persist any monthly bill found in the document to session memory
                        # so future sessions can skip asking for it.
                        try:
                            from session_memory import update as _smem_update
                            _BILL_KEYS = ("monthly cost", "average monthly bill",
                                          "monthly bill", "monthly charge",
                                          "monthly electricity", "monthly payment")
                            for fact in key_facts:
                                k = (fact.get("key") or "").lower().strip()
                                v = str(fact.get("value") or "").replace("$", "").replace(",", "").strip()
                                if any(bk in k for bk in _BILL_KEYS) and v:
                                    try:
                                        bill_val = float(v.split("/")[0].split(" ")[0])
                                        if 10 < bill_val < 10000:
                                            _smem_update(monthly_bill_usd=bill_val)
                                            log.info("context_update: saved monthly_bill_usd=%.0f from document", bill_val)
                                            break
                                    except (ValueError, TypeError):
                                        pass
                        except Exception as _sme:
                            log.warning("context_update: session_memory bill save failed: %s", _sme)

                        context_text = "\n".join(lines)
                        if context_text:
                            live_queue.send_content(
                                types.Content(
                                    role="user",
                                    parts=[types.Part(text=context_text)],
                                )
                            )
                            log.info("context_update injected into live session (%d chars)", len(context_text))

                    elif kind == "activity_start":
                        log.info("activity_start — user began speaking; suppressing model audio")
                        _loop_guard["user_spoke"] = False
                        live_queue.send_activity_start()

                    elif kind == "end_of_turn":
                        _user_spoke_first["val"] = True
                        log.info("end_of_turn received — audio chunks so far: %d — re-enabling model audio, sending activity_end", audio_chunks)
                        _loop_guard["user_spoke"] = True
                        live_queue.send_activity_end()
                        try:
                            await websocket.send_text(json.dumps({"type": "thinking"}))
                        except Exception:
                            pass

        except (WebSocketDisconnect, Exception) as exc:
            log.info("Receive loop ended: %s", exc)
        finally:
            live_queue.close()

    async def _session_start_task():
        """Inject [SESSION_START] after a short delay.

        Skipped entirely on mid-conversation reconnects (Gemini Live timeout)
        so the model resumes silently instead of replaying the welcome greeting.
        Also skipped if the user already spoke first (avoids double-response race).
        """
        await asyncio.sleep(0.6)
        if _is_reconnect:
            log.info("Reconnect — skipping SESSION_START greeting (session %s)", session_id[:8])
            return
        if not _user_spoke_first["val"]:
            live_queue.send_content(
                types.Content(
                    role="user",
                    parts=[types.Part(text="[SESSION_START]")],
                )
            )
            log.info("SESSION_START injected for session %s", session_id[:8])
        else:
            log.info("SESSION_START skipped — user spoke first (session %s)", session_id[:8])

    try:
        await asyncio.gather(receive_loop(), send_loop(), _session_start_task())
    finally:
        # Record when this WebSocket closed so the next connection can decide
        # whether it's a fast Gemini Live reconnect or a browser page refresh.
        _last_ws_close_time = time.time()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"\n🔥  Prometheus running →  http://localhost:{port}\n")
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)
