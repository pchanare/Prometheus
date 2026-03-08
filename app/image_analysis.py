import os
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
                        "text": f"""Analyze this {space_description} image for 
                        ground-mounted solar panel installation potential.
                        Please provide:
                        1. AVAILABLE AREA: Usable area in square feet
                        2. PANEL COUNT: How many ground-mounted panels could fit
                           (standard panel is 3.5ft x 5.5ft)
                        3. OBSTACLES: Trees, shade, structures or challenges
                        4. SUN EXPOSURE: Orientation and sunlight assessment
                        5. RECOMMENDED CONFIGURATION: Best panel layout
                        6. ENERGY POTENTIAL: Estimated annual energy in kWh
                        7. SUITABILITY SCORE: Rate the space 1-10
                        8. RECOMMENDATIONS: Top 3 specific action items
                        Be specific, practical and encouraging."""
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
            analysis = data["candidates"][0]["content"]["parts"][0]["text"]
            return {
                "space_type": space_description,
                "analysis": analysis,
                "status": "success"
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