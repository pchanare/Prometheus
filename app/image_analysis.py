import os
import re
import json
import base64
import requests
import google.auth
import google.auth.transport.requests

def analyze_space_for_solar(image_path: str, space_description: str = "outdoor space") -> dict:
    """
    Analyze an image of a space to determine ground-mounted solar panel potential.

    Args:
        image_path: Path to the image file e.g. C:/Users/umang/Downloads/courtyard.jpg
        space_description: Type of space e.g. 'backyard', 'kitchen garden', 'courtyard'

    Returns:
        Dict with solar potential analysis of the space
    """
    try:
        from status_channel import push_status as _push_status
        _push_status("🔍 Analysing your space for solar potential…")
    except Exception:
        pass

    try:
        # Normalize path - handle both forward and back slashes
        image_path = image_path.replace("\\", "/").strip()

        # Check file exists
        if not os.path.exists(image_path):
            return {
                "error": f"File not found at: {image_path}",
                "status": "failed",
                "analysis": f"Could not find image at {image_path}"
            }

        # Read and encode image
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        # Determine mime type
        lower_path = image_path.lower()
        if lower_path.endswith(".png"):
            mime_type = "image/png"
        elif lower_path.endswith(".webp"):
            mime_type = "image/webp"
        else:
            mime_type = "image/jpeg"

        # Get credentials using google-auth
        credentials, project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        auth_request = google.auth.transport.requests.Request()
        credentials.refresh(auth_request)
        token = credentials.token

        # Vertex AI config
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", project)
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        model = "gemini-2.0-flash-001"

        url = (
            f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}"
            f"/locations/{location}/publishers/google/models/{model}:generateContent"
        )

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        payload = {
            "contents": [{
                "role": "user",
                "parts": [
                    {
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": image_data
                        }
                    },
                    {
                        "text": f"""Analyze this {space_description} image for solar panel installation.

IMPORTANT RULES:
- Every outdoor space can support solar. Your job is to find the BEST option, not judge suitability.
- installation_type MUST be exactly "canopy" or "ground_mount" — no other values allowed.
  Choose "canopy" for patios, decks, backyards, and spaces where shade is valuable.
  Choose "ground_mount" for open land, fields, and large open yards.
- panel_count must be at least 2. Even a small or partially shaded space can fit a few panels.
- area_sq_ft must always be a positive integer — estimate the visible usable area.

Return your analysis ONLY as valid JSON with exactly these fields (no markdown, no extra text):
{{
  "area_sq_ft": <integer, estimated usable area in square feet — must be greater than 0>,
  "panel_count": <integer, number of standard 400W panels (3.5ft x 5.5ft) that fit — minimum 2>,
  "installation_type": "<string, must be exactly 'canopy' or 'ground_mount'>",
  "obstacles": "<string, description of any trees, shade, or structures to work around>",
  "sun_exposure": "<string, orientation and sunlight assessment>",
  "recommended_config": "<string, best panel layout for the chosen installation type>",
  "annual_energy_kwh": <integer, estimated annual energy generation in kWh>,
  "recommendations": ["<practical tip 1>", "<practical tip 2>", "<practical tip 3>"]
}}"""
                    }
                ]
            }],
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": 1024
            }
        }

        response = requests.post(url, json=payload, headers=headers, timeout=30)

        if response.ok:
            data = response.json()
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]

            # Parse the JSON response into structured fields
            try:
                json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
                analysis_data = json.loads(json_match.group() if json_match else raw_text)
                return {
                    "space_type": space_description,
                    "panel_count": analysis_data.get("panel_count"),
                    "area_sq_ft": analysis_data.get("area_sq_ft"),
                    "installation_type": analysis_data.get("installation_type", "canopy"),
                    "obstacles": analysis_data.get("obstacles", ""),
                    "sun_exposure": analysis_data.get("sun_exposure", ""),
                    "recommended_config": analysis_data.get("recommended_config", ""),
                    "annual_energy_kwh": analysis_data.get("annual_energy_kwh"),
                    "recommendations": analysis_data.get("recommendations", []),
                    "analysis": raw_text,
                    "status": "success",
                }
            except (json.JSONDecodeError, AttributeError):
                # Fallback: return raw text so the agent still has something to work with
                return {
                    "space_type": space_description,
                    "analysis": raw_text,
                    "status": "success",
                }
        else:
            return {
                "error": f"Vertex AI error {response.status_code}: {response.text}",
                "status": "failed",
                "analysis": "Vision API call failed"
            }

    except Exception as e:
        return {
            "error": str(e),
            "status": "failed",
            "analysis": f"Exception occurred: {str(e)}"
        }