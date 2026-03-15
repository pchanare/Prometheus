#!/bin/bash
# ─────────────────────────────────────────────────────────────────
#  Prometheus – one-time Secret Manager setup
#  Run this ONCE from your local machine before deploying.
#  Prerequisites: gcloud CLI logged in, project set to prometheus-489421
# ─────────────────────────────────────────────────────────────────

PROJECT=prometheus-489421
SA=prometheus-sa@${PROJECT}.iam.gserviceaccount.com

echo "==> Enabling Secret Manager API..."
gcloud services enable secretmanager.googleapis.com --project=$PROJECT

echo ""
echo "==> Creating secrets..."

# MAPS_API_KEY
echo -n "AIzaSyCQNZnZTh46o4zdxVHdnAU0cZlaPh3fqI8" | \
  gcloud secrets create MAPS_API_KEY --data-file=- --project=$PROJECT

# GOOGLE_SEARCH_API_KEY  (same key value as MAPS — both needed as separate secrets)
echo -n "AIzaSyCQNZnZTh46o4zdxVHdnAU0cZlaPh3fqI8" | \
  gcloud secrets create GOOGLE_SEARCH_API_KEY --data-file=- --project=$PROJECT

# GOOGLE_SEARCH_ENGINE_ID
echo -n "8218df535bfaa4538" | \
  gcloud secrets create GOOGLE_SEARCH_ENGINE_ID --data-file=- --project=$PROJECT

# DOCUMENT_AI_PROCESSOR_ID
echo -n "fce1a89ec107b64a" | \
  gcloud secrets create DOCUMENT_AI_PROCESSOR_ID --data-file=- --project=$PROJECT

echo ""
echo "==> Granting service account access to each secret..."

for SECRET in MAPS_API_KEY GOOGLE_SEARCH_API_KEY GOOGLE_SEARCH_ENGINE_ID DOCUMENT_AI_PROCESSOR_ID; do
  gcloud secrets add-iam-policy-binding $SECRET \
    --member="serviceAccount:${SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --project=$PROJECT
  echo "    ✓ $SECRET"
done

echo ""
echo "==> Done. Secrets are ready in Secret Manager."
echo "    Use these in your Cloud Run deploy command:"
echo ""
echo "    --set-secrets=MAPS_API_KEY=MAPS_API_KEY:latest,\\"
echo "    GOOGLE_SEARCH_API_KEY=GOOGLE_SEARCH_API_KEY:latest,\\"
echo "    GOOGLE_SEARCH_ENGINE_ID=GOOGLE_SEARCH_ENGINE_ID:latest,\\"
echo "    DOCUMENT_AI_PROCESSOR_ID=DOCUMENT_AI_PROCESSOR_ID:latest"
