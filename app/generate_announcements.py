"""
generate_announcements.py — One-time script to pre-bake announcement audio files.

Uses Google Cloud Text-to-Speech with the Chirp 3 HD "Aoede" voice — the same
voice as the Gemini Live agent — to generate a WAV file for each tool announcement.

Run once from the app/ directory:
    python generate_announcements.py

Output: app/static/audio/announce_<tool_name>.wav  (7 files, ~20–60 KB each)
"""

import subprocess
import sys
import os

# ---------------------------------------------------------------------------
# Auto-install google-cloud-texttospeech if not present
# ---------------------------------------------------------------------------
try:
    from google.cloud import texttospeech
except ImportError:
    print("Installing google-cloud-texttospeech…")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "google-cloud-texttospeech"])
    from google.cloud import texttospeech

# ---------------------------------------------------------------------------
# Announcement phrases — must match _TOOL_ANNOUNCE in server.py exactly
# ---------------------------------------------------------------------------
ANNOUNCEMENTS = {
    "run_solar_analysis":       "Pulling your solar data now — this usually takes about 15 seconds.",
    "calculate_outdoor_solar":  "Calculating your outdoor system costs — just a moment.",
    "calculate_combined_solar": "Calculating your combined system now — just a moment.",
    "send_all_rfps":            "Sending your RFP emails now — this takes about 30 seconds.",
    "find_local_installers":    "Finding local installers near you — just a moment.",
    "generate_solar_mockup":    "Generating your solar mockup — give me about 30 seconds.",
    "analyze_space_for_solar":  "Analysing your space now — just a moment.",
}

# ---------------------------------------------------------------------------
# Voice config — Chirp 3 HD Aoede matches the Gemini Live agent's voice
# ---------------------------------------------------------------------------
VOICE_NAME     = "en-US-Chirp3-HD-Aoede"
LANGUAGE_CODE  = "en-US"
AUDIO_ENCODING = texttospeech.AudioEncoding.LINEAR16   # WAV / PCM

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "static", "audio")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    client = texttospeech.TextToSpeechClient()

    voice = texttospeech.VoiceSelectionParams(
        language_code=LANGUAGE_CODE,
        name=VOICE_NAME,
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=AUDIO_ENCODING,
    )

    generated = []
    for tool_name, phrase in ANNOUNCEMENTS.items():
        out_path = os.path.join(OUTPUT_DIR, f"announce_{tool_name}.wav")
        synthesis_input = texttospeech.SynthesisInput(text=phrase)
        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config,
        )
        with open(out_path, "wb") as f:
            f.write(response.audio_content)
        size_kb = len(response.audio_content) // 1024
        print(f'  \u2713  {out_path}  ({size_kb} KB)  "{phrase}"')
        generated.append(out_path)

    print(f"\nDone — {len(generated)} files written to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
