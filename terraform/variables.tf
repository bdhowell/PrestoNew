variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for the Cloud SQL instance"
  type        = string
  default     = "us-central1"
}

variable "instance_name" {
  description = "Name of the Cloud SQL instance"
  type        = string
  default     = "presto-db"
}

variable "tier" {
  description = "Machine type for the instance (e.g., db-custom-2-8192)"
  type        = string
  default     = "db-custom-2-8192"
}

variable "authorized_networks" {
  description = "List of CIDR blocks allowed to connect (name and cidr)"
  type = list(object({
    name = string
    cidr = string
  }))
  default = []
}

variable "db_password" {
  description = "Password for the root user"
  type        = string
  sensitive   = true
}

variable "database_name" {
  description = "Initial database name created in the instance"
  type        = string
  default     = "postgres"
}
