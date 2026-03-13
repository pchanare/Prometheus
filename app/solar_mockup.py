"""
solar_mockup.py — ADK tool: generate a photorealistic solar-panel mockup image.

IMAGE SIDE-CHANNEL PATTERN
─────────────────────────────────────────────────────────────────────────────
The voice model (gemini-live-2.5-flash-native-audio) has a 32K context window.
Returning a 2-3 MB image as base64 (~2.7 M chars) in the tool's return dict
would send those bytes back to the model as a function response, immediately
blowing the context limit and crashing the session.

Instead, `generate_solar_mockup` stores the image bytes in the module-level
`_pending_images` dict and returns only a tiny status dict to the voice model.
`server.py` calls `pop_pending_images()` after each ADK event and forwards any
waiting images directly to the browser via WebSocket — the bytes never touch
the model's context window.
"""

import base64
import logging
import os
import uuid as _uuid

import requests

from brain import generate_solar_image

_MAPS_API_KEY = os.environ.get("MAPS_API_KEY", "")


def _fetch_street_view(address: str) -> bytes | None:
    """
    Fetch a Google Street View image for the given address.
    Returns JPEG bytes on success, None if unavailable or key missing.
    """
    if not _MAPS_API_KEY:
        return None
    try:
        resp = requests.get(
            "https://maps.googleapis.com/maps/api/streetview",
            params={
                "size":              "640x480",
                "location":          address,
                "key":               _MAPS_API_KEY,
                "return_error_code": "true",
            },
            timeout=10,
        )
        if resp.ok and resp.headers.get("content-type", "").startswith("image/"):
            log.info("_fetch_street_view: got %d bytes for %r", len(resp.content), address)
            return resp.content
        log.warning("_fetch_street_view: no image for %r (status=%s)", address, resp.status_code)
    except Exception as exc:
        log.warning("_fetch_street_view failed for %r: %s", address, exc)
    return None

log = logging.getLogger("prometheus.solar_mockup")

# ---------------------------------------------------------------------------
# Side-channel image store
# Keys are short UUIDs; values are (raw_bytes, mime_type).
# Written by the tool (possibly in a thread-pool executor),
# drained by the server's async send_loop after each ADK event.
# ---------------------------------------------------------------------------
_pending_images: dict[str, tuple[bytes, str]] = {}


def pop_pending_images() -> list[dict]:
    """
    Drain all waiting mockup images and return them as dicts ready to send to
    the browser.  Called from server.py after each ADK event.
    """
    items = []
    for image_id in list(_pending_images.keys()):
        img_bytes, mime_type = _pending_images.pop(image_id)
        items.append({
            "image_id":  image_id,
            "image_b64": base64.b64encode(img_bytes).decode("utf-8"),
            "mime_type": mime_type,
        })
    return items


def generate_solar_mockup(
    address: str,
    panel_count: int = 20,
    installation_type: str = "rooftop",
    image_path: str = "",
) -> dict:
    """
    Generate a photorealistic AI image showing solar panels at the property.

    Use this tool after presenting a solar analysis whenever the user wants to
    visualise what the installation would look like.

    Args:
        address:           Full street address (e.g. "123 Main St, Austin TX").
        panel_count:       Number of solar panels to render. Use the recommended
                           count from get_solar_data, or from an uploaded quote.
        installation_type: Visual style of the render. Must be one of:
                             "rooftop"      — panels on the roof (default)
                             "canopy"       — backyard solar canopy/pergola
                             "ground_mount" — panels on ground-level racking
                           Use "canopy" or "ground_mount" when the user has asked
                           about those types or when analyze_space_for_solar was used.
        image_path:        Optional filesystem path to the user's uploaded photo
                           (the temp path from the capture label, e.g.
                           C:\\Users\\...\\prometheus_abc123.jpg).
                           When provided, the AI edits the actual photo to add
                           solar panels instead of generating a generic house.
                           Always pass this when the user has shared a photo of
                           their house, roof, or outdoor space.

    Returns:
        A dict with the following keys:
          success   (bool)  — True if the image was generated successfully.
          image_id  (str)   — Short token the server uses to retrieve the image
                              from the side-channel store (only when success=True).
          message   (str)   — Human-readable status to relay to the user.
    """
    log.info(
        "generate_solar_mockup: address=%r  panels=%d  type=%s  image_path=%r",
        address, panel_count, installation_type, image_path or None,
    )

    _type = (installation_type or "rooftop").lower().strip()
    photo_bytes: bytes | None = None

    if _type == "rooftop":
        # Rooftop: always use Street View so the mockup shows the actual building,
        # not a generic house. User-uploaded photos are irrelevant here since Street
        # View captures the front facade that matters for rooftop installs.
        photo_bytes = _fetch_street_view(address)
        if photo_bytes:
            log.info("generate_solar_mockup: rooftop — using Street View for %r", address)
        else:
            log.info("generate_solar_mockup: rooftop — Street View unavailable, falling back to text-only")
    else:
        # Canopy / ground mount: Street View shows the street, not the backyard.
        # Only the user's uploaded photo of their outdoor space is useful here.
        if image_path:
            try:
                with open(image_path, "rb") as _f:
                    photo_bytes = _f.read()
                log.info("generate_solar_mockup: %s — loaded user photo (%d bytes) from %s",
                         _type, len(photo_bytes), image_path)
            except Exception as exc:
                log.warning("generate_solar_mockup: could not load image_path %r: %s — using text-only",
                            image_path, exc)
        else:
            log.info("generate_solar_mockup: %s — no user photo provided, using text-only prompt", _type)

    image_bytes = generate_solar_image(address, panel_count, installation_type, photo_bytes)

    if image_bytes:
        image_id = str(_uuid.uuid4())[:8]
        _pending_images[image_id] = (image_bytes, "image/jpeg")
        log.info("generate_solar_mockup ✓ — %d bytes stored as image_id=%s", len(image_bytes), image_id)
        type_label = {
            "canopy":       "solar canopy",
            "ground_mount": "ground-mount array",
        }.get((installation_type or "rooftop").lower(), "rooftop installation")
        return {
            "success":  True,
            "image_id": image_id,
            "message": (
                f"Solar mockup ({type_label}) generated for {address} with {panel_count} panels. "
                "The image is now being displayed in the chat for the user to see."
            ),
        }

    log.warning("generate_solar_mockup: image generation returned None")
    return {
        "success": False,
        "message": (
            "Solar mockup image generation is temporarily unavailable. "
            "Please try again in a moment."
        ),
    }
