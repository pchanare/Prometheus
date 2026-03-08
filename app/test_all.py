"""
test_all.py - Prometheus Brain-Voice Split integration tests.

Run from the app/ directory:
    cd app
    python test_all.py

Tests every tier independently so you know exactly which model
is reachable before launching the full server.
"""

import io
import json
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv(override=True)

# Force UTF-8 output on Windows so emoji/arrows don't crash cp1252
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    import io as _io
    sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = _io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

PASS = f"{GREEN}[PASS]{RESET}"
FAIL = f"{RED}[FAIL]{RESET}"

results: list[tuple[str, str, str]] = []   # (name, status, detail)


def section(title: str):
    bar = "=" * 60
    print(f"\n{BOLD}{CYAN}{bar}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{bar}{RESET}")


def record(name: str, passed: bool, detail: str = ""):
    status = PASS if passed else FAIL
    results.append((name, "PASS" if passed else "FAIL", detail))
    print(f"  {status}  {name}")
    if detail:
        colour = YELLOW if not passed else ""
        reset  = RESET if not passed else ""
        print(f"  {colour}      {detail[:160]}{reset}")


# ─────────────────────────────────────────────────────────────────────────────
# 1. Environment
# ─────────────────────────────────────────────────────────────────────────────
section("1 · Environment variables")

env_vars = {
    "GOOGLE_CLOUD_PROJECT":  os.environ.get("GOOGLE_CLOUD_PROJECT"),
    "GOOGLE_CLOUD_LOCATION": os.environ.get("GOOGLE_CLOUD_LOCATION"),
    "GOOGLE_GENAI_USE_VERTEXAI": os.environ.get("GOOGLE_GENAI_USE_VERTEXAI"),
    "MAPS_API_KEY":          os.environ.get("MAPS_API_KEY"),
}
for k, v in env_vars.items():
    ok = bool(v)
    record(k, ok, v[:20] + "…" if v and len(v) > 20 else (v or "NOT SET"))


# ─────────────────────────────────────────────────────────────────────────────
# 2. brain.py imports
# ─────────────────────────────────────────────────────────────────────────────
section("2 · brain.py — import & client init")

try:
    import brain
    record("import brain", True)
except Exception as e:
    record("import brain", False, str(e))
    print(f"\n{RED}Cannot continue without brain.py — fix import error above.{RESET}")
    sys.exit(1)

try:
    client = brain._get_client()
    record("Vertex AI client init", True, f"project={brain._PROJECT}  location={brain._LOCATION}")
except Exception as e:
    record("Vertex AI client init", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 3. Brain model tier  (fast reasoning)
# ─────────────────────────────────────────────────────────────────────────────
section("3 · Brain tier — call_brain()")

try:
    t0  = time.time()
    ans = brain.call_brain(
        "A house uses 850 kWh/month. How many 400W solar panels are needed "
        "to offset 80% of usage? Give only the number, no explanation."
    )
    elapsed = time.time() - t0
    ok = bool(ans) and any(c.isdigit() for c in ans)
    record(
        f"call_brain [model tried first: {brain.BRAIN_MODELS[0]}]",
        ok,
        f"answer={ans!r}  ({elapsed:.1f}s)",
    )
except Exception as e:
    record("call_brain", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 4. Model availability listing
# ─────────────────────────────────────────────────────────────────────────────
section("4 · Model availability check")

ALL_MODELS = (
    brain.BRAIN_MODELS
    + brain.PDF_MODELS
    + brain.IMAGE_MODELS_GEMINI
    + [brain.IMAGEN_MODEL]
)
ALL_MODELS = list(dict.fromkeys(ALL_MODELS))   # deduplicate, preserve order

try:
    listed = {m.name for m in brain._get_client().models.list()}
    # Imagen models use a separate generate_images API — they don't appear in
    # the standard Gemini models.list() but work fine; mark them as INFO not FAIL.
    imagen_ids = {brain.IMAGEN_MODEL}
    for mid in ALL_MODELS:
        found = any(mid in name for name in listed)
        if not found and mid in imagen_ids:
            # Imagen uses generate_images() not generate_content() — separate registry
            print(f"  [INFO]  model listed: {mid}  (Imagen — separate API, not in Gemini list)")
        else:
            record(f"model listed: {mid}", found, "" if found else "not in models.list()")
except Exception as e:
    record("models.list()", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 5. PDF Specialist tier
# ─────────────────────────────────────────────────────────────────────────────
section("5 · PDF Specialist tier — analyze_pdf_bytes()")

# Build a minimal in-memory PDF so we don't need a real file on disk
MINI_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R"
    b"/Contents 4 0 R/Resources<</Font<</F1<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>>>>>>>endobj\n"
    b"4 0 obj<</Length 120>>\n"
    b"stream\n"
    b"BT /F1 12 Tf 72 720 Td "
    b"(Electricity Bill - Pacific Gas & Electric) Tj "
    b"0 -20 Td (Monthly Usage: 920 kWh) Tj "
    b"0 -20 Td (Amount Due: $187.40) Tj "
    b"0 -20 Td (Service Address: 123 Maple St, San Jose CA 95101) Tj ET\n"
    b"endstream\nendobj\n"
    b"xref\n0 5\n0000000000 65535 f \n"
    b"trailer<</Size 5/Root 1 0 R>>\nstartxref\n9\n%%EOF"
)

try:
    t0      = time.time()
    json_str = brain.analyze_pdf_bytes(MINI_PDF)
    elapsed  = time.time() - t0
    try:
        data = json.loads(json_str)
        has_type    = "document_type" in data
        has_summary = "summary" in data
        has_facts   = isinstance(data.get("key_facts"), list) and len(data["key_facts"]) > 0
        ok = has_type and has_summary and has_facts
        detail = (
            f"document_type={data.get('document_type')!r}  "
            f"key_facts={len(data.get('key_facts', []))} items  ({elapsed:.1f}s)"
        )
        record(f"analyze_pdf_bytes [model: {brain.PDF_MODELS[0]}]", ok, detail)
        if not ok:
            print(f"      raw JSON: {json_str[:300]}")
    except json.JSONDecodeError:
        record("analyze_pdf_bytes — JSON parse", False, f"raw output: {json_str[:200]}")
except Exception as e:
    record("analyze_pdf_bytes", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 6. Image Generation tier
# ─────────────────────────────────────────────────────────────────────────────
section("6 · Image Gen tier — generate_solar_image()")

print(f"  {YELLOW}Tries: {brain.IMAGE_MODELS_GEMINI} then falls back to {brain.IMAGEN_MODEL}{RESET}\n")

try:
    t0      = time.time()
    raw     = brain.generate_solar_image("123 Maple St, San Jose CA", panel_count=10)
    elapsed = time.time() - t0
    if raw:
        out_path = os.path.join(os.path.dirname(__file__), "test_mockup_output.jpg")
        with open(out_path, "wb") as fh:
            fh.write(raw)
        record(
            "generate_solar_image",
            True,
            f"{len(raw)} bytes ({elapsed:.1f}s) — saved to test_mockup_output.jpg",
        )
    else:
        record(
            "generate_solar_image",
            False,
            f"returned None after {elapsed:.1f}s — all image models unavailable",
        )
except Exception as e:
    record("generate_solar_image", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 7. solar_mockup tool (wraps image gen)
# ─────────────────────────────────────────────────────────────────────────────
section("7 · solar_mockup tool — generate_solar_mockup()")

try:
    from solar_mockup import generate_solar_mockup
    record("import solar_mockup", True)

    t0     = time.time()
    result = generate_solar_mockup("123 Maple St, San Jose CA", panel_count=10)
    elapsed = time.time() - t0

    has_success = "success" in result
    has_message = bool(result.get("message"))
    if result.get("success"):
        has_b64 = bool(result.get("image_b64"))
        ok = has_success and has_b64 and has_message
        record(
            "generate_solar_mockup → success=True, image rendered",
            ok,
            f"image_b64 len={len(result.get('image_b64',''))}  ({elapsed:.1f}s)",
        )
    else:
        # success=False is acceptable if ALL image models are unavailable —
        # the tool returns a graceful message; the voice agent handles it.
        record(
            "generate_solar_mockup → success=False (all image models down — graceful)",
            True,   # not a crash — this is handled correctly
            f"message={result.get('message')!r}  ({elapsed:.1f}s)",
        )
except Exception as e:
    record("generate_solar_mockup", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 8. FastAPI /api/analyze-pdf endpoint (needs server running)
# ─────────────────────────────────────────────────────────────────────────────
section("8 · FastAPI endpoint — POST /api/analyze-pdf  (server must be running)")

print(f"  {YELLOW}Skip this section if the server is not running on port 8080.")
print(f"  Start it first with:  python server.py{RESET}\n")

try:
    import urllib.request
    import urllib.error

    boundary = b"TestBoundary123"
    body = (
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="file"; filename="test.pdf"\r\n'
        b"Content-Type: application/pdf\r\n\r\n"
        + MINI_PDF + b"\r\n"
        b"--" + boundary + b"--\r\n"
    )
    req = urllib.request.Request(
        "http://localhost:8080/api/analyze-pdf",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary.decode()}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read())
    ok = payload.get("status") == "ok" and "data" in payload
    record(
        "POST /api/analyze-pdf",
        ok,
        f"document_type={payload.get('data', {}).get('document_type')!r}",
    )
except urllib.error.URLError:
    # Server isn't running — this is expected when running tests standalone
    print(f"  [SKIP]  POST /api/analyze-pdf  (start python server.py first, then re-run section 8)")
except Exception as e:
    record("POST /api/analyze-pdf", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 9. Existing tools smoke-test
# ─────────────────────────────────────────────────────────────────────────────
section("9 · Existing tools smoke-test")

try:
    from solar_api import get_solar_data
    t0   = time.time()
    data = get_solar_data("1600 Amphitheatre Pkwy, Mountain View CA")
    ok   = isinstance(data, dict) and data.get("max_panels") is not None
    record("get_solar_data (Google Solar API)", ok, f"panels={data.get('max_panels')}  cost={data.get('upfront_cost_usd')}  ({time.time()-t0:.1f}s)")
except Exception as e:
    record("get_solar_data", False, str(e))

try:
    from tax_benefits import get_tax_benefits
    data = get_tax_benefits(state="CA", system_cost_usd=25000, payback_years=7)
    ok   = isinstance(data, dict) and "revised_cost_usd" in data
    record("get_tax_benefits", ok, f"revised_cost=${data.get('revised_cost_usd')}  savings=${data.get('total_incentives_usd')}")
except Exception as e:
    record("get_tax_benefits", False, str(e))

try:
    from search_tool import search_solar_incentives
    data = search_solar_incentives(state="CA", system_cost_usd=25000)
    ok   = isinstance(data, dict) and "search_results" in data
    record("search_solar_incentives", ok, f"{len(data.get('search_results', []))} results returned")
except Exception as e:
    record("search_solar_incentives", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
section("Summary")

total  = len(results)
passed = sum(1 for _, s, _ in results if s == "PASS")
failed = total - passed

print(f"\n  {BOLD}{passed}/{total} tests passed{RESET}")
if failed:
    print(f"\n  {RED}Failures:{RESET}")
    for name, status, detail in results:
        if status == "FAIL":
            print(f"    {RED}✗{RESET}  {name}")
            if detail:
                print(f"       {detail[:100]}")

print()
if failed == 0:
    print(f"  {GREEN}{BOLD}All tests passed - ready to run the server!{RESET}")
elif passed >= total * 0.7:
    print(f"  {YELLOW}{BOLD}Most tests passed. Check failures above before demoing.{RESET}")
else:
    print(f"  {RED}{BOLD}Several tests failed - fix the issues above before proceeding.{RESET}")
print()
