terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# ─────────────────────────────────────────────────────────────────
# Enable required APIs
# ─────────────────────────────────────────────────────────────────
resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "secretmanager.googleapis.com",
    "aiplatform.googleapis.com",
    "solar.googleapis.com",
    "documentai.googleapis.com",
    "gmail.googleapis.com",
  ])
  service            = each.key
  disable_on_destroy = false
}

# ─────────────────────────────────────────────────────────────────
# Artifact Registry — Docker repository
# ─────────────────────────────────────────────────────────────────
resource "google_artifact_registry_repository" "prometheus" {
  location      = var.region
  repository_id = "prometheus"
  format        = "DOCKER"
  description   = "Prometheus agent Docker images"
  depends_on    = [google_project_service.apis]
}

# ─────────────────────────────────────────────────────────────────
# Service Account for Cloud Run
# ─────────────────────────────────────────────────────────────────
resource "google_service_account" "prometheus_sa" {
  account_id   = "prometheus-sa"
  display_name = "Prometheus Agent — Cloud Run SA"
  description  = "Least-privilege SA for the Prometheus Cloud Run service"
}

# IAM — Vertex AI (model inference)
resource "google_project_iam_member" "sa_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.prometheus_sa.email}"
}

# IAM — Secret Manager (read secrets at runtime)
resource "google_project_iam_member" "sa_secrets" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.prometheus_sa.email}"
}

# IAM — Document AI
resource "google_project_iam_member" "sa_docai" {
  project = var.project_id
  role    = "roles/documentai.apiUser"
  member  = "serviceAccount:${google_service_account.prometheus_sa.email}"
}

# ─────────────────────────────────────────────────────────────────
# IAM — Cloud Build service account permissions
# (needed so Cloud Build can push images + deploy Cloud Run)
# ─────────────────────────────────────────────────────────────────
data "google_project" "project" {}

locals {
  cloudbuild_sa = "${data.google_project.project.number}@cloudbuild.gserviceaccount.com"
}

resource "google_project_iam_member" "cloudbuild_run" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${local.cloudbuild_sa}"
}

resource "google_project_iam_member" "cloudbuild_registry" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${local.cloudbuild_sa}"
}

resource "google_project_iam_member" "cloudbuild_sa_user" {
  project = var.project_id
  role    = "roles/iam.serviceAccountUser"
  member  = "serviceAccount:${local.cloudbuild_sa}"
}

# ─────────────────────────────────────────────────────────────────
# Reference existing secrets (created by setup_secrets.sh)
# ─────────────────────────────────────────────────────────────────
data "google_secret_manager_secret" "maps_api_key" {
  secret_id  = "MAPS_API_KEY"
  depends_on = [google_project_service.apis]
}

data "google_secret_manager_secret" "search_api_key" {
  secret_id  = "GOOGLE_SEARCH_API_KEY"
  depends_on = [google_project_service.apis]
}

data "google_secret_manager_secret" "search_engine_id" {
  secret_id  = "GOOGLE_SEARCH_ENGINE_ID"
  depends_on = [google_project_service.apis]
}

data "google_secret_manager_secret" "docai_processor_id" {
  secret_id  = "DOCUMENT_AI_PROCESSOR_ID"
  depends_on = [google_project_service.apis]
}

data "google_secret_manager_secret" "gmail_token" {
  secret_id  = "GMAIL_TOKEN"
  depends_on = [google_project_service.apis]
}

# ─────────────────────────────────────────────────────────────────
# Cloud Run v2 Service
# ─────────────────────────────────────────────────────────────────
resource "google_cloud_run_v2_service" "prometheus_agent" {
  name     = var.service_name
  location = var.region

  template {
    service_account = google_service_account.prometheus_sa.email

    scaling {
      min_instance_count = 1
      max_instance_count = 5
    }

    timeout = "3600s"

    containers {
      image = var.image

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
        cpu_idle = true
      }

      # ── Plain environment variables ──────────────────────────
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.region
      }
      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "1"
      }
      env {
        name  = "SENDER_EMAIL"
        value = var.sender_email
      }

      # ── Secrets injected at runtime ──────────────────────────
      env {
        name = "MAPS_API_KEY"
        value_source {
          secret_key_ref {
            secret  = data.google_secret_manager_secret.maps_api_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "GOOGLE_SEARCH_API_KEY"
        value_source {
          secret_key_ref {
            secret  = data.google_secret_manager_secret.search_api_key.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "GOOGLE_SEARCH_ENGINE_ID"
        value_source {
          secret_key_ref {
            secret  = data.google_secret_manager_secret.search_engine_id.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "DOCUMENT_AI_PROCESSOR_ID"
        value_source {
          secret_key_ref {
            secret  = data.google_secret_manager_secret.docai_processor_id.secret_id
            version = "latest"
          }
        }
      }

      ports {
        container_port = 8080
      }
    }
  }

  # Ignore image changes — cloudbuild.yaml updates the image on each push
  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }

  depends_on = [
    google_artifact_registry_repository.prometheus,
    google_service_account.prometheus_sa,
    google_project_iam_member.sa_vertex,
    google_project_iam_member.sa_secrets,
  ]
}

# ─────────────────────────────────────────────────────────────────
# Allow public (unauthenticated) access to Cloud Run
# ─────────────────────────────────────────────────────────────────
resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.prometheus_agent.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
