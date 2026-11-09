terraform {
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

resource "google_sql_database_instance" "presto_postgres" {
  name             = var.instance_name
  database_version = "POSTGRES_14"
  region           = var.region

  settings {
    tier = var.tier
    ip_configuration {
      ipv4_enabled    = true
      authorized_networks = [
        for net in var.authorized_networks : {
          name  = net.name
          value = net.cidr
        }
      ]
    }
    database_flags {
      name  = "cloudsql.enable_pgvector"
      value = "on"
    }
    activation_policy = "ALWAYS"
    backup_configuration {
      enabled = true
      start_time = "03:00"
    }
  }
}

resource "google_sql_user" "root_user" {
  name     = "root"
  instance = google_sql_database_instance.presto_postgres.name
  password = var.db_password
}

resource "google_sql_database" "default_db" {
  name     = var.database_name
  instance = google_sql_database_instance.presto_postgres.name
}
