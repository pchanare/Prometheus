#!/bin/bash
# ─────────────────────────────────────────────────────────────────
#  Prometheus – build + deploy to Cloud Run
#  Run from the project root (where Dockerfile lives).
#  Prerequisites: Docker running, gcloud CLI authenticated.
# ─────────────────────────────────────────────────────────────────

PROJECT=prometheus-489421
REGION=us-central1
REPO=prometheus
IMAGE=${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/agent:latest
SA=prometheus-sa@${PROJECT}.iam.gserviceaccount.com

# ── Step 1: Enable APIs ───────────────────────────────────────────
echo "==> Enabling required APIs..."
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  aiplatform.googleapis.com \
  secretmanager.googleapis.com \
  --project=$PROJECT

# ── Step 2: Create Artifact Registry repo (safe to re-run) ───────
echo "==> Creating Artifact Registry repo (skips if already exists)..."
gcloud artifacts repositories create $REPO \
  --repository-format=docker \
  --location=$REGION \
  --project=$PROJECT 2>/dev/null || true

# ── Step 3: Create service account (safe to re-run) ──────────────
echo "==> Creating service account..."
gcloud iam service-accounts create prometheus-sa \
  --display-name="Prometheus Agent SA" \
  --project=$PROJECT 2>/dev/null || true

# Grant Vertex AI access
gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:${SA}" \
  --role="roles/aiplatform.user"

# Grant Document AI access
gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:${SA}" \
  --role="roles/documentai.apiUser"

# ── Step 4: Build and push image ─────────────────────────────────
echo "==> Configuring Docker auth..."
gcloud auth configure-docker ${REGION}-docker.pkg.dev --quiet

echo "==> Building and pushing image (this takes ~2–4 minutes)..."
gcloud builds submit \
  --tag $IMAGE \
  --project=$PROJECT

# ── Step 5: Deploy to Cloud Run ──────────────────────────────────
echo "==> Deploying to Cloud Run..."
gcloud run deploy prometheus-agent \
  --image=$IMAGE \
  --region=$REGION \
  --service-account=$SA \
  --allow-unauthenticated \
  --timeout=3600 \
  --concurrency=10 \
  --min-instances=1 \
  --max-instances=5 \
  --memory=1Gi \
  --cpu=1 \
  --port=8080 \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=${REGION},GOOGLE_GENAI_USE_VERTEXAI=true,SENDER_EMAIL=raizadamisc@gmail.com,DOCUMENT_AI_LOCATION=us" \
  --set-secrets="MAPS_API_KEY=MAPS_API_KEY:latest,GOOGLE_SEARCH_API_KEY=GOOGLE_SEARCH_API_KEY:latest,GOOGLE_SEARCH_ENGINE_ID=GOOGLE_SEARCH_ENGINE_ID:latest,DOCUMENT_AI_PROCESSOR_ID=DOCUMENT_AI_PROCESSOR_ID:latest" \
  --project=$PROJECT

echo ""
echo "==> Deploy complete! Your app is live at the URL above."
