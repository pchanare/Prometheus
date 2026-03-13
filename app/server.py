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
import uuid

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv(override=True)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("prometheus")

# Ensure app/ is on sys.path so sibling imports resolve
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.runners import Runner, RunConfig
from google.adk.sessions import InMemorySessionService
from google.genai import types
from google.genai.types import Modality

from Prometheus.agent import root_agent, _BASE_INSTRUCTION, _DESCRIPTION, _MODEL, _TOOLS

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

# Status channel — async before/after tool callbacks send status messages
# to the browser via WebSocket.  Because the callbacks are async they execute
# BEFORE the sync tool blocks the event loop, guaranteeing the browser sees
# the status while the tool is actually working.
try:
    from status_channel import (
        init as _init_status_channel,
        async_push_status as _async_push_status,
        async_clear_status as _async_clear_status,
    )
    _status_ok = True
except Exception as _status_err:
    log.warning("status_channel import failed: %s — status messages disabled", _status_err)
    _status_ok = False
    def _init_status_channel(*a, **kw):
        pass

# Tool name → status message shown while the tool is running
_TOOL_STATUS = {
    "get_solar_data":           "☀️ Fetching solar potential data for your address…",
    "find_local_installers":    "📋 Finding solar installers near you…",
    "generate_solar_mockup":    "🎨 Rendering AI solar panel mockup…",
    "analyze_space_for_solar":  "🔍 Analysing your space for solar potential…",
    "generate_rfp":             "✍️ Writing RFP…",
    "send_rfp_email":           "📧 Sending email…",
    "get_tax_benefits":         "💰 Calculating federal and state tax incentives…",
    "search_solar_incentives":  "🔍 Searching for local solar incentives and rebates…",
    "web_search":               "🔍 Searching the web…",
}


async def _before_tool_cb(tool, args, tool_context):
    """ADK before_tool_callback — show status in browser BEFORE the sync tool runs."""
    tool_name = getattr(tool, "name", "") or getattr(tool, "__name__", "")
    msg = _TOOL_STATUS.get(tool_name, f"⏳ {tool_name}…")
    await _async_push_status(msg)
    return None   # None = proceed with normal tool execution


async def _after_tool_cb(tool, args, tool_context, result):
    """ADK after_tool_callback — clear status AFTER the sync tool finishes."""
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
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Prometheus Solar AI")

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

    user_id = "user"
    session_id = str(uuid.uuid4())
    log.info("New session %s  mode=%s", session_id[:8], mode)

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
    # Receive loop: browser → ADK
    # -----------------------------------------------------------------------
    async def receive_loop():
        audio_chunks = 0
        try:
            while True:
                message = await websocket.receive()
                raw_bytes = message.get("bytes")
                raw_text  = message.get("text")

                if raw_bytes:
                    # Raw PCM audio – 16 kHz, mono, S16LE (resampled in browser)
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
                        live_queue.send_content(
                            types.Content(
                                role="user",
                                parts=[types.Part(text=payload["content"])],
                            )
                        )

                    elif kind == "image":
                        # Passive image (e.g. uploaded file) — sent as realtime context
                        img_bytes = base64.b64decode(payload["data"])
                        live_queue.send_realtime(
                            types.Blob(data=img_bytes, mime_type="image/jpeg")
                        )

                    elif kind == "camera_on":
                        # First frame captured when the user activates the camera.
                        # Sent as a full content turn (not just realtime context) so the
                        # agent immediately responds with a visual description of the space
                        # and an invitation to upload a photo for detailed solar analysis.
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
                                            "Please describe what you see in this space and give a quick "
                                            "assessment of its solar potential. "
                                            "Then invite me to take a clear photo and upload it to the chat "
                                            "for a detailed solar analysis and mockup."
                                        )
                                    ),
                                ],
                            )
                        )

                    elif kind == "capture":
                        # Explicit image turn (camera snapshot OR uploaded file).
                        # Save bytes to a temp file so analyze_space_for_solar can
                        # read them from disk — the tool expects a filesystem path.
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
                            # Append temp path so the model can pass it to analyze_space_for_solar
                            label = f"{user_label}\n[Image saved at: {tmp_path}]"
                        else:
                            label = (
                                f"I just shared this image (saved at: {tmp_path}). "
                                "Please describe what you see and how it relates to solar installation. "
                                f"If it shows an outdoor space (backyard, garden, courtyard, etc.), "
                                f"call analyze_space_for_solar with image_path=\"{tmp_path}\" "
                                "and the appropriate space_type."
                            )
                        log.info("Capture label: %r", label[:80])
                        live_queue.send_content(
                            types.Content(
                                role="user",
                                parts=[
                                    types.Part(
                                        inline_data=types.Blob(
                                            data=img_bytes,
                                            mime_type="image/jpeg",
                                        )
                                    ),
                                    types.Part(text=label),
                                ],
                            )
                        )

                    elif kind == "context_update":
                        # Injected context from the browser — the PDF Specialist model
                        # extracted structured data from any uploaded document.
                        # Format it as a natural-language note and inject into the
                        # live session so the agent can reference it immediately.
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
                        # User started speaking — tell the model to stop any current
                        # response (interruption) and start listening
                        log.info("activity_start — user began speaking")
                        live_queue.send_activity_start()

                    elif kind == "end_of_turn":
                        # User stopped speaking — explicit signal replaces the old
                        # silence-burst hack.  With VAD disabled this immediately
                        # tells the model the user's turn is over and to respond.
                        log.info("end_of_turn received — audio chunks so far: %d — sending activity_end", audio_chunks)
                        live_queue.send_activity_end()
                        # Tell the browser the model is now thinking — this lets
                        # the UI show a server-driven "Thinking…" state rather than
                        # relying on the JS-side "Processing…" hardcode.
                        try:
                            await websocket.send_text(json.dumps({"type": "thinking"}))
                        except Exception:
                            pass

        except (WebSocketDisconnect, Exception) as exc:
            log.info("Receive loop ended: %s", exc)
        finally:
            live_queue.close()

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
                                await websocket.send_bytes(inline.data)
                            if getattr(part, "text", None):
                                await websocket.send_text(
                                    json.dumps({"type": "transcript", "role": "model", "text": part.text})
                                )
                    output_tx = getattr(server_content, "output_transcription", None)
                    if output_tx and getattr(output_tx, "text", None):
                        await websocket.send_text(
                            json.dumps({"type": "transcript", "role": "model", "text": output_tx.text})
                        )
                    # server_content turn_complete / interrupted (non-native-audio models)
                    if getattr(server_content, "turn_complete", False):
                        log.info("turn_complete via server_content (fallback)")
                        await websocket.send_text(json.dumps({"type": "turn_complete"}))
                    if getattr(server_content, "interrupted", False):
                        log.info("interrupted via server_content (fallback)")
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
                            await websocket.send_text(
                                json.dumps({"type": "transcript", "role": role, "text": text})
                            )
                        if inline and getattr(inline, "data", None):
                            await websocket.send_bytes(inline.data)

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
                    # interrupted fires when the user speaks over the model
                    if getattr(actions, "interrupted", False):
                        log.info("interrupted via event.actions")
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

    await asyncio.gather(receive_loop(), send_loop())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"\n🔥  Prometheus running →  http://localhost:{port}\n")
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)
