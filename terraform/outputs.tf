output "service_url" {
  description = "Live URL of the deployed Prometheus Cloud Run service"
  value       = google_cloud_run_v2_service.prometheus_agent.uri
}

output "service_account_email" {
  description = "Email of the Cloud Run service account"
  value       = google_service_account.prometheus_sa.email
}

output "artifact_registry_repo" {
  description = "Full Artifact Registry repository path"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.prometheus.repository_id}"
}
