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

from Prometheus.agent import root_agent

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
# ADK runner pool  (one runner per agent instance)
# ---------------------------------------------------------------------------
APP_NAME = "Prometheus"
session_service = InMemorySessionService()

_runners: dict[str, Runner] = {}


def get_runner(mode: str) -> Runner:
    """Return (or create) a Runner for the given mode."""
    if mode not in _runners:
        cfg = MODES.get(mode, MODES[DEFAULT_MODE])
        _runners[mode] = Runner(
            agent=cfg["agent"],
            app_name=APP_NAME,
            session_service=session_service,
        )
    return _runners[mode]


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

    if mode not in MODES:
        await websocket.send_text(json.dumps({"type": "error", "text": f"Unknown mode: {mode}"}))
        await websocket.close()
        return

    mode_cfg = MODES[mode]
    runner = get_runner(mode)
    modalities = mode_cfg["response_modalities"]

    user_id = "user"
    session_id = str(uuid.uuid4())
    log.info("New session %s  mode=%s", session_id[:8], mode)

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

    # ── Session memory injection ─────────────────────────────────────────────
    # Re-inject facts captured in previous sessions / context resets so the
    # model doesn't ask the user to repeat their address, solar data, etc.
    try:
        from session_memory import build_injection as _build_mem
        _mem_note = _build_mem()
        if _mem_note:
            live_queue.send_content(
                types.Content(
                    role="user",
                    parts=[types.Part(text=_mem_note)],
                )
            )
            log.info("session_memory: injected %d chars into session %s",
                     len(_mem_note), session_id[:8])
    except Exception as _mem_exc:
        log.warning("session_memory: injection failed: %s", _mem_exc)
    # ────────────────────────────────────────────────────────────────────────

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

                # ── turn_complete / interrupted — PRIMARY path (event.actions) ──
                # With gemini-live-*-native-audio models, server_content is always
                # None; turn_complete arrives on event.actions.turn_complete instead.
                if actions:
                    if getattr(actions, "turn_complete", False):
                        log.info("turn_complete via event.actions")
                        await websocket.send_text(json.dumps({"type": "turn_complete"}))
                    # interrupted fires when the user speaks over the model
                    if getattr(actions, "interrupted", False):
                        log.info("interrupted via event.actions")
                        await websocket.send_text(json.dumps({"type": "turn_complete"}))

                # ── Audio / text via server_content (fallback for other models) ─
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
                    # Also check server_content paths in case this model variant uses them
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
