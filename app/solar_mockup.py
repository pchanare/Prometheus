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
import uuid as _uuid

from brain import generate_solar_image

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


def generate_solar_mockup(address: str, panel_count: int = 20) -> dict:
    """
    Generate a photorealistic AI image showing solar panels installed on the
    property at *address*.

    Use this tool after you have presented the solar financial analysis and the
    user wants to see what their roof would look like with panels installed.

    Args:
        address:     Full street address of the property (e.g. "123 Main St, Austin TX").
        panel_count: Number of solar panels to render.  Default is 20.
                     Use the recommended panel count from get_solar_data if available,
                     or the panel count from an uploaded solar quote if one was provided.

    Returns:
        A dict with the following keys:
          success   (bool)  — True if the image was generated successfully.
          image_id  (str)   — Short token the server uses to retrieve the image
                              from the side-channel store (only when success=True).
          message   (str)   — Human-readable status to relay to the user.
    """
    log.info("generate_solar_mockup: address=%r  panels=%d", address, panel_count)

    image_bytes = generate_solar_image(address, panel_count)

    if image_bytes:
        image_id = str(_uuid.uuid4())[:8]
        _pending_images[image_id] = (image_bytes, "image/jpeg")
        log.info("generate_solar_mockup ✓ — %d bytes stored as image_id=%s", len(image_bytes), image_id)
        return {
            "success":  True,
            "image_id": image_id,
            "message": (
                f"Solar mockup generated for {address} with {panel_count} panels. "
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
