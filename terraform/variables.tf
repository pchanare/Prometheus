variable "project_id" {
  description = "Google Cloud project ID"
  type        = string
  default     = "prometheus-489421"
}

variable "region" {
  description = "Google Cloud region"
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "Cloud Run service name"
  type        = string
  default     = "prometheus-agent"
}

variable "image" {
  description = "Full Artifact Registry image path (updated by cloudbuild.yaml on each deploy)"
  type        = string
  default     = "us-central1-docker.pkg.dev/prometheus-489421/prometheus/agent:latest"
}

variable "sender_email" {
  description = "Gmail address used for RFP emails"
  type        = string
  default     = "raizadamisc@gmail.com"
}
