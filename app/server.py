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
import uuid

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
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
    # Enable output transcription for voice mode so text appears in transcript.
    # Wrapped in try/except because AudioTranscriptionConfig may not exist in
    # older ADK/genai versions — the field is silently dropped if unsupported.
    try:
        transcription = types.AudioTranscriptionConfig() if mode == "voice" else None
        run_config = RunConfig(
            response_modalities=modalities,
            output_audio_transcription=transcription,
        )
    except (AttributeError, Exception):
        log.warning("output_audio_transcription not supported in this ADK version — skipping")
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
                        img_bytes = base64.b64decode(payload["data"])
                        live_queue.send_realtime(
                            types.Blob(data=img_bytes, mime_type="image/jpeg")
                        )

                    elif kind == "end_of_turn":
                        # 1.5 s silence burst — Gemini Live VAD needs enough trailing silence
                        # to detect end-of-speech and trigger a model response
                        log.info("end_of_turn received — audio chunks so far: %d — sending 1.5s silence", audio_chunks)
                        live_queue.send_realtime(
                            types.Blob(data=bytes(48000), mime_type="audio/pcm;rate=16000")
                        )

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
                # ── Log every event type so we can diagnose silence ─────
                log.info("ADK event: %s | server_content=%s | content=%s",
                         type(event).__name__,
                         bool(getattr(event, "server_content", None)),
                         bool(getattr(event, "content", None)))

                # ── Audio / text from model ──────────────────────────────
                server_content = getattr(event, "server_content", None)
                if server_content:
                    model_turn = getattr(server_content, "model_turn", None)
                    if model_turn:
                        for part in getattr(model_turn, "parts", []) or []:
                            inline = getattr(part, "inline_data", None)
                            if inline and getattr(inline, "data", None):
                                log.info("Sending audio chunk: %d bytes", len(inline.data))
                                await websocket.send_bytes(inline.data)
                            if getattr(part, "text", None):
                                await websocket.send_text(
                                    json.dumps({
                                        "type": "transcript",
                                        "role": "model",
                                        "text": part.text,
                                    })
                                )
                    # Audio output transcription (text version of model's spoken reply)
                    output_tx = getattr(server_content, "output_transcription", None)
                    if output_tx and getattr(output_tx, "text", None):
                        await websocket.send_text(
                            json.dumps({
                                "type": "transcript",
                                "role": "model",
                                "text": output_tx.text,
                            })
                        )
                    if getattr(server_content, "turn_complete", False):
                        log.info("turn_complete received from model")
                        await websocket.send_text(json.dumps({"type": "turn_complete"}))
                    # interrupted = model was cut off by user speech (VAD triggered).
                    # Treat it the same as turn_complete so the browser resets state.
                    if getattr(server_content, "interrupted", False):
                        log.info("model response interrupted by user speech")
                        await websocket.send_text(json.dumps({"type": "turn_complete"}))

                # ── Content events (text, tool calls, or audio via content path) ─
                content = getattr(event, "content", None)
                if content:
                    role = getattr(content, "role", "model")
                    parts = getattr(content, "parts", []) or []
                    log.info("Content event: role=%s  parts=%d", role, len(parts))
                    for i, part in enumerate(parts):
                        text   = getattr(part, "text", None)
                        inline = getattr(part, "inline_data", None)
                        fc     = getattr(part, "function_call", None)
                        fr     = getattr(part, "function_response", None)
                        log.info("  part[%d] text=%r  inline=%s  fc=%s  fr=%s",
                                 i,
                                 (text[:60] + "…") if text and len(text) > 60 else text,
                                 bool(inline), bool(fc), bool(fr))
                        if text:
                            await websocket.send_text(
                                json.dumps({"type": "transcript", "role": role, "text": text})
                            )
                        # Audio can arrive via content.parts in some ADK/model combos
                        if inline and getattr(inline, "data", None):
                            log.info("  Audio from content path: %d bytes", len(inline.data))
                            await websocket.send_bytes(inline.data)

        except WebSocketDisconnect:
            log.info("Send loop: browser disconnected")
        except Exception as exc:
            log.info("Send loop ended: %s", exc)
            # Gemini Live session died — close the browser WebSocket so the
            # browser's onclose handler reconnects and gets a fresh session.
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
