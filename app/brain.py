"""
brain.py — model dispatch layer for Prometheus Brain-Voice Split architecture.

Provides three tiers of AI models accessed via the Vertex AI API:
  - Brain  : gemini-3.1-flash-lite-preview  (fast, smart reasoning)
  - PDF    : gemini-3.1-pro-preview          (deep document extraction)
  - Image  : gemini-3.1-flash-image-preview  (solar mockup generation)

Each tier falls back to gemini-2.5-pro so the hackathon demo never
breaks when a preview model is behind an allowlist or quota.
"""

import logging
import os

import google.genai as genai
from google.genai import types

log = logging.getLogger("prometheus.brain")

# ── Model tier priority lists ───────────────────────────────────────────────
BRAIN_MODELS = ["gemini-3.1-flash-lite-preview", "gemini-2.5-pro"]
PDF_MODELS   = ["gemini-3.1-pro-preview",         "gemini-2.5-pro"]

# Image gen: try the native Gemini image-gen model first, then fall back to
# Imagen 3 (stable GA).  Imagen uses a different API — handled separately in
# generate_solar_image() rather than through _call().
IMAGE_MODELS_GEMINI = ["gemini-3.1-flash-image"]
IMAGEN_MODEL        = "imagen-3.0-generate-001"

# ── Vertex AI client (picks up env vars set in .env) ─────────────────────────
_PROJECT  = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(
            vertexai=True,
            project=_PROJECT,
            location=_LOCATION,
        )
    return _client


# ── Generic dispatcher ───────────────────────────────────────────────────────

def _call(models: list[str], contents, config=None) -> genai.types.GenerateContentResponse:
    """
    Try each model in *models* in order.  Any 404 / allowlist / quota / permission
    error causes a fallback to the next model.  Re-raises the last exception if
    all models are exhausted.
    """
    client = _get_client()
    last_exc: Exception = RuntimeError("No models provided")
    for model_id in models:
        try:
            log.info("brain._call → model=%s", model_id)
            resp = client.models.generate_content(
                model=model_id,
                contents=contents,
                config=config,
            )
            log.info("brain._call ✓ model=%s", model_id)
            return resp
        except Exception as exc:
            err_str = str(exc).lower()
            transient = any(
                k in err_str
                for k in ("404", "not found", "allowlist", "permission denied",
                           "quota", "unavailable", "no model")
            )
            if transient:
                log.warning(
                    "brain._call: model %s unavailable (%s) — trying next", model_id, exc
                )
                last_exc = exc
                continue
            # Unexpected error — propagate immediately
            raise
    raise last_exc


# ── Public helpers ───────────────────────────────────────────────────────────

def call_brain(prompt: str) -> str:
    """
    Send a single text prompt to the Brain model tier and return the text reply.
    Used by tool functions that need heavy reasoning without adding latency to
    the voice model's real-time context.
    """
    resp = _call(BRAIN_MODELS, prompt)
    return (resp.text or "").strip()


def analyze_pdf_bytes(pdf_bytes: bytes) -> str:
    """
    Send any PDF to the PDF Specialist tier and extract all relevant information.

    Works on any document type relevant to solar projects:
    electricity bills, solar installer quotes, roof inspection reports,
    HOA rules, building permits, property assessments, etc.

    Returns a JSON string:
        {
          "document_type": "<human-readable type, e.g. Solar Quote>",
          "summary":       "<2-3 sentence plain-English overview>",
          "key_facts": [
            {"key": "<field name>", "value": "<extracted value>"},
            ...
          ]
        }
    Returns an error JSON string on failure.
    """
    extraction_prompt = (
        "You are a document analysis expert specialising in solar energy, "
        "home construction, and utility documents. "
        "Analyse the attached document and return ONLY valid JSON "
        "(no markdown fences, no extra commentary):\n"
        "{\n"
        '  "document_type": "<e.g. Electricity Bill, Solar Installer Quote, '
        "Roof Inspection Report, HOA Rules, Building Permit, Property Assessment, etc.>\",\n"
        '  "summary": "<2-3 sentence plain-English summary of what this document contains and why it matters for solar>\",\n'
        '  "key_facts": [\n'
        '    {"key": "<field name>", "value": "<extracted value>"}\n'
        '  ]\n'
        "}\n\n"
        "Extract ALL facts relevant to a solar installation decision.  Examples by type:\n"
        "- Electricity bill: monthly kWh, monthly cost (USD), utility provider, "
        "billing period, service address, rate plan.\n"
        "- Solar quote: company name, system size (kW), panel model, panel count, "
        "inverter type, total cost, incentives mentioned, warranty, estimated install date.\n"
        "- Roof inspection: roof age, material, condition rating, areas needing repair, "
        "load capacity, inspector name.\n"
        "- HOA rules: rules about solar panels, approval process, aesthetic requirements.\n"
        "- Other: extract whatever is most relevant to solar installation or energy savings.\n\n"
        "Omit fields that cannot be found rather than using null.  "
        "Return ONLY the JSON object, nothing else."
    )

    contents = [
        types.Part(text=extraction_prompt),
        types.Part(
            inline_data=types.Blob(data=pdf_bytes, mime_type="application/pdf")
        ),
    ]

    try:
        resp = _call(PDF_MODELS, contents)
        text = (resp.text or "").strip()
        # Strip accidental markdown fences if the model adds them despite instructions
        if text.startswith("```"):
            parts = text.split("```")
            if len(parts) >= 2:
                text = parts[1]
                if text.startswith("json"):
                    text = text[4:]
        return text.strip()
    except Exception as exc:
        log.error("analyze_pdf_bytes failed: %s", exc)
        return f'{{"error": "{exc}"}}'


def generate_solar_image(address: str, panel_count: int = 20) -> bytes | None:
    """
    Generate a photorealistic solar panel mockup image.

    Strategy (in order):
      1. gemini-3.1-flash-image-preview  — native Gemini image gen (via generate_content)
      2. imagen-3.0-generate-001          — Imagen 3 GA (via generate_images)

    Returns raw image bytes or None if all tiers fail.
    """
    prompt = (
        f"Photorealistic aerial view of a residential home at {address} "
        f"with {panel_count} modern black solar panels neatly installed on the roof. "
        "4K ultra-detailed quality, bright sunny day, no text overlays, "
        "cinematic lighting, bird's-eye perspective."
    )

    client = _get_client()

    # ── Tier 1: Gemini image-gen model (generate_content path) ───────────────
    for model_id in IMAGE_MODELS_GEMINI:
        try:
            log.info("generate_solar_image → model=%s (generate_content)", model_id)
            config = types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"])
            resp   = client.models.generate_content(model=model_id, contents=prompt, config=config)
            candidates = getattr(resp, "candidates", None) or []
            for candidate in candidates:
                content = getattr(candidate, "content", None)
                parts   = getattr(content, "parts", None) or []
                for part in parts:
                    inline = getattr(part, "inline_data", None)
                    if inline and getattr(inline, "data", None):
                        log.info("generate_solar_image ✓ via %s — %d bytes", model_id, len(inline.data))
                        return inline.data
            log.warning("generate_solar_image: no image part in %s response", model_id)
        except Exception as exc:
            err_str = str(exc).lower()
            if any(k in err_str for k in ("404", "not found", "allowlist", "permission", "quota")):
                log.warning("generate_solar_image: %s unavailable (%s)", model_id, exc)
                continue
            log.error("generate_solar_image: unexpected error from %s: %s", model_id, exc)

    # ── Tier 2: Imagen 3 (generate_images path — stable GA) ──────────────────
    try:
        log.info("generate_solar_image → model=%s (generate_images)", IMAGEN_MODEL)
        imagen_resp = client.models.generate_images(
            model=IMAGEN_MODEL,
            prompt=prompt,
            config=types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio="16:9",
            ),
        )
        generated = getattr(imagen_resp, "generated_images", None) or []
        if generated:
            image_bytes = generated[0].image.image_bytes
            if image_bytes:
                log.info("generate_solar_image ✓ via %s — %d bytes", IMAGEN_MODEL, len(image_bytes))
                return image_bytes
        log.warning("generate_solar_image: no images in %s response", IMAGEN_MODEL)
    except Exception as exc:
        log.error("generate_solar_image: %s failed: %s", IMAGEN_MODEL, exc)

    return None
