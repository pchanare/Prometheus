"""
brain.py — model dispatch layer for Prometheus Brain-Voice Split architecture.

All Gemini 3.1 preview models and Gemini 2.5 image models require the global
endpoint (location="global").  Regional endpoints (us-central1) return 404 for
these models.  The regional client is kept only for Imagen 3 (generate_images).

Tier hierarchy:
  Brain : gemini-3.1-flash-lite-preview (global) → gemini-2.5-pro (global)
  PDF   : gemini-3.1-pro-preview (global)        → gemini-2.5-pro (global)
  Image : gemini-3.1-flash-image-preview (global)
        → gemini-2.5-flash-image (global)
        → imagen-3.0-generate-001 (us-central1, generate_images, retry on 429)
"""

import logging
import os
import time

import google.genai as genai
from google.genai import types

log = logging.getLogger("prometheus.brain")

# ── Model tier priority lists ───────────────────────────────────────────────
BRAIN_MODELS = ["gemini-3.1-flash-lite-preview", "gemini-2.5-pro"]
PDF_MODELS   = ["gemini-3.1-pro-preview",         "gemini-2.5-pro"]

# Image gen — all Gemini models use global endpoint via generate_content().
# Imagen 3 is the final fallback (regional, generate_images API).
GEMINI_IMAGE_MODELS = [
    "gemini-3.1-flash-image-preview",   # primary   (global, generate_content)
    "gemini-2.5-flash-image",           # secondary (global, generate_content)
]
IMAGEN_MODEL           = "imagen-3.0-generate-001"  # final fallback (us-central1)
_IMAGEN_RETRY_ATTEMPTS = 3                           # retry up to 3× on 429
_IMAGEN_RETRY_DELAY_S  = 4                           # seconds between retries

# ── Vertex AI clients ─────────────────────────────────────────────────────────
_PROJECT  = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

# Global endpoint client — used for ALL Gemini models (Brain, PDF, Image).
# Gemini 3.1 preview and 2.5 image models only exist in the global location.
_global_client: genai.Client | None = None

# Regional client — kept solely for imagen-3.0-generate-001 (generate_images API).
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


def _get_global_client() -> genai.Client:
    """Return a client pointed at the Vertex AI global endpoint.
    Used exclusively for Gemini image-generation models which are only
    registered in the global location, not regional endpoints.
    """
    global _global_client
    if _global_client is None:
        _global_client = genai.Client(
            vertexai=True,
            project=_PROJECT,
            location="global",
        )
    return _global_client


# ── Generic dispatcher ───────────────────────────────────────────────────────

def _call(models: list[str], contents, config=None) -> genai.types.GenerateContentResponse:
    """
    Try each model in *models* in order using the global endpoint.
    Any 404 / allowlist / quota / permission error causes a fallback to the
    next model.  Re-raises the last exception if all models are exhausted.
    """
    client = _get_global_client()
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


def generate_solar_image(
    address: str,
    panel_count: int = 20,
    installation_type: str = "rooftop",
    image_bytes: bytes | None = None,
) -> bytes | None:
    """
    Generate a photorealistic solar panel mockup image.

    Args:
        address:           Full street address of the property.
        panel_count:       Number of panels to render.
        installation_type: One of "rooftop" (default), "canopy", or "ground_mount".
        image_bytes:       Optional JPEG bytes of the user's actual photo.
                           When provided, Gemini models receive the photo as input
                           and edit it to add solar panels — producing a personalised
                           result instead of a generic house.  Imagen fallback always
                           uses a text-only prompt regardless of this parameter.

    Strategy:
      Tier 1 — gemini-3.1-flash-image-preview (global, generate_content)
      Tier 2 — gemini-2.5-flash-image          (global, generate_content)
      Tier 3 — imagen-3.0-generate-001          (us-central1, generate_images, retry on 429)

    Returns raw image bytes on success, or None if all tiers fail.
    """
    _type = (installation_type or "rooftop").lower().strip()

    if image_bytes:
        # Photo-based editing prompts: keep the user's actual image, add panels
        if _type == "canopy":
            prompt = (
                f"Edit this photo to add a solar canopy structure over the outdoor space. "
                f"Add {panel_count} modern black solar panels forming the roof of an elegant "
                "open-sided pergola canopy with metal posts. Keep everything else in the photo "
                "exactly the same — the garden, furniture, background, perspective. "
                "Photorealistic, 4K quality, bright sunny day, no text overlays."
            )
        elif _type == "ground_mount":
            prompt = (
                f"Edit this photo to add {panel_count} modern black solar panels mounted on "
                "ground-level aluminium racking systems in neat rows in the yard or open area. "
                "Keep everything else in the photo exactly the same — the background, "
                "surroundings, and perspective. "
                "Photorealistic, 4K quality, bright sunny day, no text overlays."
            )
        else:  # rooftop
            prompt = (
                f"Edit this photo to add {panel_count} modern black solar panels neatly "
                "installed on the roof of this house. Keep the house, garden, surroundings, "
                "and perspective exactly the same — only add the solar panels to the roof. "
                "Photorealistic, 4K quality, bright sunny day, no text overlays."
            )
    else:
        # Text-only prompts: generate a generic property from the address
        if _type == "canopy":
            prompt = (
                f"Photorealistic backyard view of a residential property at {address} "
                f"with a stunning solar canopy structure. "
                f"{panel_count} modern black solar panels form the roof of an elegant "
                "open-sided pergola canopy. The space underneath is bright and liveable — "
                "outdoor furniture, garden, people relaxing. "
                "4K ultra-detailed quality, bright sunny day, no text overlays, eye-level perspective."
            )
        elif _type == "ground_mount":
            prompt = (
                f"Photorealistic view of the yard at {address} "
                f"with {panel_count} modern black solar panels mounted on ground-level "
                "aluminium racking systems arranged in neat, evenly spaced rows. "
                "Lush green grass surrounds the array. "
                "4K ultra-detailed quality, bright sunny day, no text overlays, wide-angle perspective."
            )
        else:  # rooftop (default)
            prompt = (
                f"Photorealistic aerial view of a residential home at {address} "
                f"with {panel_count} modern black solar panels neatly installed on the roof. "
                "4K ultra-detailed quality, bright sunny day, no text overlays, "
                "cinematic lighting, bird's-eye perspective."
            )

    global_client = _get_global_client()
    config = types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"])

    # Build contents: multimodal (photo + prompt) when image_bytes provided, text-only otherwise
    if image_bytes:
        gemini_contents = [
            types.Part(inline_data=types.Blob(data=image_bytes, mime_type="image/jpeg")),
            types.Part(text=prompt),
        ]
    else:
        gemini_contents = prompt

    # ── Tiers 1 & 2: Gemini image models (global, generate_content) ──────────
    for model_id in GEMINI_IMAGE_MODELS:
        try:
            log.info("generate_solar_image → model=%s (generate_content, global) photo=%s",
                     model_id, bool(image_bytes))
            resp = global_client.models.generate_content(
                model=model_id, contents=gemini_contents, config=config
            )
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
            if any(k in err_str for k in ("404", "not found", "allowlist", "permission",
                                           "quota", "403", "401", "precondition")):
                log.warning("generate_solar_image: %s unavailable (%s) — trying next", model_id, exc)
            else:
                log.error("generate_solar_image: unexpected error from %s: %s — trying next",
                          model_id, exc)

    # ── Tier 3: imagen-3.0-generate-001 via generate_images (us-central1) ────
    regional_client = _get_client()
    for attempt in range(1, _IMAGEN_RETRY_ATTEMPTS + 1):
        try:
            log.info(
                "generate_solar_image → model=%s (generate_images) attempt %d/%d",
                IMAGEN_MODEL, attempt, _IMAGEN_RETRY_ATTEMPTS,
            )
            imagen_resp = regional_client.models.generate_images(
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
                    log.info(
                        "generate_solar_image ✓ via %s — %d bytes (attempt %d)",
                        IMAGEN_MODEL, len(image_bytes), attempt,
                    )
                    return image_bytes
            log.warning("generate_solar_image: no images in %s response (attempt %d)", IMAGEN_MODEL, attempt)

        except Exception as exc:
            err_str = str(exc).lower()
            is_rate_limit = any(k in err_str for k in ("429", "resource_exhausted", "too many requests", "quota"))
            if is_rate_limit and attempt < _IMAGEN_RETRY_ATTEMPTS:
                log.warning(
                    "generate_solar_image: %s rate-limited (429) — retry %d/%d in %ds",
                    IMAGEN_MODEL, attempt, _IMAGEN_RETRY_ATTEMPTS, _IMAGEN_RETRY_DELAY_S,
                )
                time.sleep(_IMAGEN_RETRY_DELAY_S)
                continue
            log.error(
                "generate_solar_image: %s failed (attempt %d): %s",
                IMAGEN_MODEL, attempt, exc,
            )
            break  # non-retryable error or last attempt — give up

    return None
