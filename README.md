# Prometheus - AI Solar Advisor

> A real-time multimodal AI agent that guides homeowners through every step of going solar - from rooftop analysis and financial modelling to photorealistic mockups and installer quote requests - using voice, vision, and live Google data.

---

## What It Does

Prometheus is a **Live Agent** built on the Gemini Live API and Google ADK. Users speak naturally with it (hands-free, interruptible) while it:

- **Analyses their roof** via the Google Solar API - panels, annual production, payback period, federal + state incentives
- **Models outdoor alternatives** - solar canopies and ground-mount systems with full financial breakdowns
- **Generates photorealistic mockups** of panels on the user's actual property using Gemini image models / Imagen 3
- **Accepts camera and photo input** so users can show their outdoor space for spatial analysis
- **Parses uploaded documents** - electricity bills, HOA rules, roof inspection reports - via Document AI
- **Finds local installers** via Google Maps and sends personalised RFP emails via Gmail

All tool activity is narrated with real-time step-by-step status messages. Results appear as structured cards in the chat window while the agent voices a spoken summary.

---

## Architecture

![Architecture Diagram](architecture.svg)

**Key components:**

| Layer | Technology |
|---|---|
| Frontend | Vanilla JS + WebSocket client (served by FastAPI) |
| Backend | FastAPI + `google-adk` ADK Runner on **Google Cloud Run** |
| Voice model | `gemini-live-2.5-flash-native-audio` via **Vertex AI** |
| Reasoning | `gemini-3.1-flash-lite-preview` (Brain tier) |
| PDF analysis | `gemini-3.1-pro-preview` |
| Image generation | `gemini-3.1-flash-image-preview` → `gemini-2.5-flash-image` → Imagen 3 fallback |
| Solar data | **Google Solar API** |
| Geocoding / Maps | **Google Maps Platform** |
| Document parsing | **Google Document AI** |
| Secrets | **Google Secret Manager** |
| Session memory | **Google Cloud Storage** (persists across container restarts) |
| Deployment | **Cloud Run** + Artifact Registry + Cloud Build |

---

## Prerequisites

- Python 3.11+
- [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) (`gcloud` CLI) — authenticated and configured
- A Google Cloud project with **billing enabled**
- The following APIs enabled (Terraform enables these automatically — see Cloud Deployment):
  - Vertex AI API
  - Google Solar API
  - Maps JavaScript API + Street View Static API
  - Google Custom Search API
  - Document AI API
  - Gmail API
  - Secret Manager API
  - Cloud Run API
  - Artifact Registry API
  - Cloud Storage API
  - Cloud Build API

---

## Local Development Setup

### 1. Clone the repository

```bash
git clone https://github.com/pchanare/Prometheus.git
cd prometheus-agent
```

### 2. Create and activate a virtual environment

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 3. Install dependencies

```bash
# requirements.txt is in the project root
pip install -r requirements.txt
```

### 4. Set environment variables

Create a `.env` file in the `app/` directory (never commit this):

```env
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=us-central1
GOOGLE_GENAI_USE_VERTEXAI=1

MAPS_API_KEY=your-google-maps-api-key
GOOGLE_SEARCH_API_KEY=your-custom-search-api-key
GOOGLE_SEARCH_ENGINE_ID=your-search-engine-id
DOCUMENT_AI_PROCESSOR_ID=your-document-ai-processor-id
DOCUMENT_AI_LOCATION=us
SENDER_EMAIL=your-gmail-address@gmail.com
```

### 5. Authenticate with Google Cloud

```bash
gcloud auth application-default login
gcloud config set project your-gcp-project-id
```

### 6. Create a Document AI OCR Processor (one-time)

1. Go to [console.cloud.google.com/ai/document-ai](https://console.cloud.google.com/ai/document-ai)
2. Click **Create Processor** → select **Document OCR**
3. Give it a name (e.g. `prometheus-ocr`) → select region `us`
4. Copy the **Processor ID** (looks like `abc1234def567890`) → paste into your `.env` as `DOCUMENT_AI_PROCESSOR_ID`

### 7. Set up Gmail OAuth credentials (for RFP email sending)

1. Go to [console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials)
2. Click **Create Credentials** → **OAuth 2.0 Client ID** → Application type: **Desktop app**
3. Download the JSON → save it as `app/credentials.json`
4. Run the auth flow:

```bash
cd app
python auth_test.py
```

This opens a browser — log in with your Gmail account and grant access. It writes `app/token.pickle` which the agent uses at runtime.

### 8. Run the server

```bash
cd app
python server.py
```

Open your browser at **http://localhost:8080**

---

## Cloud Deployment

Deployment is fully automated via CI/CD — every `git push origin main` builds and deploys automatically. The steps below are **one-time setup only**.

### Step 1 — Update project references

Before running anything, replace the hardcoded project ID in two files:

**`terraform/variables.tf`** — update the defaults:
```hcl
variable "project_id" {
  default = "YOUR-PROJECT-ID"   # ← change this
}
variable "image" {
  default = "us-central1-docker.pkg.dev/YOUR-PROJECT-ID/prometheus/agent:latest"  # ← change this
}
variable "sender_email" {
  default = "your-email@gmail.com"   # ← change this
}
```

**`deploy.sh`** — update the top line (only needed if using manual deploy):
```bash
PROJECT=YOUR-PROJECT-ID    # ← change this
```

### Step 2 — Provision infrastructure with Terraform

Run once from [Google Cloud Shell](https://shell.cloud.google.com) (Terraform is pre-installed) or locally:

```bash
cd terraform
terraform init
terraform plan   # review what will be created
terraform apply  # type 'yes' when prompted
```

This creates:
- Cloud Run service, Service Account, and all IAM role bindings
- Artifact Registry Docker repository
- GCS bucket for persistent session memory (`YOUR-PROJECT-ID-prometheus-memory`)
- All required API enablement

### Step 3 — Store secrets in Secret Manager

Run these commands in Cloud Shell or your terminal (replace placeholders with your real values):

```bash
PROJECT_ID=YOUR-PROJECT-ID

# Google Maps API Key
echo -n "YOUR_MAPS_API_KEY" | \
  gcloud secrets create MAPS_API_KEY --data-file=- --project=$PROJECT_ID

# Google Custom Search API Key
echo -n "YOUR_SEARCH_API_KEY" | \
  gcloud secrets create GOOGLE_SEARCH_API_KEY --data-file=- --project=$PROJECT_ID

# Google Custom Search Engine ID
echo -n "YOUR_SEARCH_ENGINE_ID" | \
  gcloud secrets create GOOGLE_SEARCH_ENGINE_ID --data-file=- --project=$PROJECT_ID

# Document AI Processor ID
echo -n "YOUR_PROCESSOR_ID" | \
  gcloud secrets create DOCUMENT_AI_PROCESSOR_ID --data-file=- --project=$PROJECT_ID
```

Then upload the Gmail OAuth token (generated by `auth_test.py` in local setup Step 7 above):

```bash
# Convert token.pickle to base64 and store in Secret Manager
base64 app/token.pickle > /tmp/token_b64.txt
gcloud secrets create GMAIL_TOKEN \
  --data-file=/tmp/token_b64.txt \
  --project=$PROJECT_ID
rm /tmp/token_b64.txt
```

### Step 4 — Connect GitHub to Cloud Build (one-time, ~5 minutes)

1. Go to [console.cloud.google.com/cloud-build/triggers](https://console.cloud.google.com/cloud-build/triggers)
2. Click **Connect Repository** → select **GitHub**
3. Authenticate → select your fork → click **Install Google Cloud Build**
4. Click **Create Trigger** with these settings:
   - Event: **Push to a branch**
   - Branch: `^main$`
   - Configuration: **Cloud Build configuration file** → `cloudbuild.yaml`
5. Click **Save**

### Step 5 — Deploy

```bash
git push origin main
```

Cloud Build picks it up automatically, builds the Docker image, and rolls out a new Cloud Run revision. The live URL is printed at the end of the build log, or run:

```bash
gcloud run services describe prometheus-agent --region=us-central1 --format='value(status.url)'
```

> **Manual deploy (alternative):** If you need to deploy without a git push, run `bash deploy.sh` from the project root. Make sure you've updated `PROJECT` at the top of the file first.

---

## Getting Your API Keys

| Key | Where to get it |
|---|---|
| `MAPS_API_KEY` | [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials) → Create API Key → restrict to Maps + Street View Static APIs |
| `GOOGLE_SEARCH_API_KEY` | [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials) → Create API Key → restrict to Custom Search API |
| `GOOGLE_SEARCH_ENGINE_ID` | [Programmable Search Engine](https://programmablesearchengine.google.com/) → Create search engine → search the whole web → copy the Engine ID |
| `DOCUMENT_AI_PROCESSOR_ID` | [Document AI Console](https://console.cloud.google.com/ai/document-ai) → Create Processor → Document OCR → region `us` → copy Processor ID |
| Gmail `credentials.json` | [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials) → OAuth 2.0 Client ID → Desktop App → Download JSON → save as `app/credentials.json` |

---

## Project Structure

```
prometheus-agent/
├── app/
│   ├── server.py                  # FastAPI app, WebSocket handler, ADK runner
│   ├── Prometheus/
│   │   └── agent.py               # ADK Agent definition + system prompt
│   ├── brain.py                   # Tiered model dispatch + image generation
│   ├── solar_api.py               # Google Solar API + Maps geocoding
│   ├── solar_mockup.py            # Photorealistic panel mockup (image side-channel)
│   ├── solar_analysis_tool.py     # Composite: Solar API + tax + incentive search
│   ├── outdoor_solar_tool.py      # Canopy / ground-mount financial calculations
│   ├── combined_solar_tool.py     # Combined rooftop + outdoor analysis
│   ├── find_installers.py         # Google Maps local installer search
│   ├── rfp_generator.py           # Personalised RFP email generation
│   ├── send_rfp_email.py          # Gmail API sender (OAuth via Secret Manager)
│   ├── send_all_rfps_tool.py      # Composite: generate + send all 3 RFPs in one call
│   ├── image_analysis.py          # Gemini Vision — outdoor space analysis
│   ├── tax_benefits.py            # Federal ITC + state incentive lookup
│   ├── search_tool.py             # Google Custom Search web tool
│   ├── search_installation_cost.py # Live pricing search
│   ├── session_memory.py          # GCS-backed persistent memory (local JSON in dev)
│   ├── status_channel.py          # Real-time status push to browser
│   ├── auth_test.py               # One-time Gmail OAuth flow
│   └── static/
│       └── index.html             # Single-page frontend (voice UI + chat)
├── terraform/
│   ├── main.tf                    # All GCP resources
│   ├── variables.tf               # Project ID, region, image path ← update before deploy
│   └── outputs.tf                 # Cloud Run URL after apply
├── Dockerfile
├── .dockerignore
├── requirements.txt               # Python dependencies (install from project root)
├── cloudbuild.yaml                # CI/CD: build → push → deploy on every git push
├── deploy.sh                      # Manual deploy script (alternative to Cloud Build)
├── architecture.svg               # System architecture diagram
└── README.md
```

---

## Technologies Used

- **[Google ADK](https://google.github.io/adk-docs/)** — Agent orchestration, tool callbacks, session management
- **[Gemini Live API](https://ai.google.dev/gemini-api/docs/live)** — Real-time bidirectional audio (`gemini-live-2.5-flash-native-audio`)
- **Gemini 3.1 Flash Image Preview** — Primary solar mockup image generation/editing
- **Imagen 3** — `imagen-3.0-generate-001` final fallback for photorealistic mockups
- **Google Solar API** — Rooftop solar potential, panel counts, LiDAR-derived irradiance data
- **Google Maps Platform** — Geocoding, Street View imagery, Places for local installers
- **Google Document AI** — OCR and structured extraction from uploaded PDFs
- **Google Custom Search API** — Live web search for pricing and incentives
- **Gmail API** — OAuth-authenticated RFP email delivery
- **Google Cloud Run** — Serverless container hosting with 1-hour session timeout
- **Google Secret Manager** — Runtime secret injection (API keys, OAuth tokens)
- **Google Artifact Registry** — Docker image storage
- **Terraform** — Full infrastructure as code
- **FastAPI + WebSocket** — Backend server and real-time browser communication

---

## CI/CD Pipeline

```
git push origin main
        │
        ▼
 Cloud Build Trigger  ──►  cloudbuild.yaml
                                │
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
               docker build   docker push  gcloud run deploy
               (image)        (Artifact    (Cloud Run —
                               Registry)    new revision)
```

Every push to `main` automatically builds a fresh Docker image tagged with the commit SHA and deploys it to Cloud Run — zero manual steps after the one-time setup.

---

## Built For

**Gemini Live Agent Challenge** — Category: **Live Agents 🗣️**
