# RFC-001: Presto Deployment Architecture & Roadmap on Google Cloud Platform (GCP)

- **Document ID:** RFC-001
- **Title:** Presto Production & Staging Architecture on GCP (Cloud Run + Cloud SQL)
- **Author / Project Manager:** Project Management Office / Lead DevOps
- **Created Date:** 2026-11-09
- **Target Release:** Q4 2026
- **Status:** `PROPOSED` (Open for Team Review)
- **Reviewers / Stakeholders:** Backend Engineering, DevOps / SRE, Security & Infrastructure, Product Management

---

## 1. Executive Summary

**Presto** (formerly AutoPoster) is a scientific poster authoring, rendering, and discovery platform built with Python (FastAPI/aiohttp), PostgreSQL (with the `pgvector` extension), and automated PDF rendering (Playwright/Poppler).

This RFC establishes the target infrastructure architecture, operational standards, security baselines, and milestone-driven implementation plan for deploying Presto on **Google Cloud Platform (GCP)** using **Cloud Run (Fully Managed)** and **Cloud SQL for PostgreSQL 14+**.

It is designed to serve as a shared source of truth and maps directly into **GitHub Milestones and Issues** to track engineering progress across the team.

---

## 2. Problem Statement & Motivation

Currently, Presto development and verification occur locally using Docker and Docker Compose. To support multi-user collaboration, automated builds, reliable rendering, and public demo accessibility, the project requires:
1. **Serverless Scalability:** Cloud Run provides automatic scaling (down to zero for cost-saving in staging, auto-scaling up for rendering traffic).
2. **Managed Vector Database:** Full support for `pgvector` on PostgreSQL 14+ managed by Cloud SQL with automatic backups, replication, and high availability.
3. **Reproducible Infrastructure:** Declarative management via Terraform (`terraform/`).
4. **Automated CI/CD:** Seamless building, vulnerability scanning, and promotion from GitHub commits to GCP.

---

## 3. High-Level Architecture

```
[ User Browser / Client ]
            │
            ▼ (HTTPS / Custom Domain)
    [ Cloud Load Balancer / Cloud Armor ]
            │
            ▼
    [ Cloud Run: presto-app ]
       ├── Container (FastAPI / Uvicorn)
       ├── Playwright / Headless Chromium (PDF generation)
       ├── Cloud Run Secret Manager Integration (Secrets: DB credentials, OpenAI keys)
       └── Serverless VPC Access Connector
            │
            ▼ (Private VPC IP)
    [ Cloud SQL: PostgreSQL 14 + pgvector ]
       ├── Cloud SQL Auth Proxy / Unix Socket or Private IP
       ├── Vector embeddings (`vector(1536)`)
       └── Daily Automated Snapshots
            │
            ▼
    [ Cloud Storage (GCS) ]
       └── Poster Assets, Uploads & Exported PDFs
```

---

## 4. Architectural & Technology Decisions (ADR)

| Component | Choice | Rationale | Alternatives Considered |
| :--- | :--- | :--- | :--- |
| **Compute Engine** | **Cloud Run (Fully Managed)** | Zero-ops infrastructure, container-native, fast autoscaling, per-second billing, native secret mounting. | GKE (too high operational overhead for current stage), Compute Engine VMs (manual patching). |
| **Database** | **Cloud SQL (PostgreSQL 14+)** | Native `cloudsql.enable_pgvector` support, automated point-in-time recovery (PITR), managed maintenance. | Self-hosted Postgres on VM (operational toil), AlloyDB (cost prohibitive at demo/MVP stage). |
| **Container Registry** | **GCP Artifact Registry** | Replaces legacy GCR (`gcr.io`), fine-grained IAM, regional caching, vulnerability scanning. | Docker Hub, GitHub Container Registry (GHCR). |
| **Secrets Management** | **Google Secret Manager** | Centralized secrets storage; injects credentials directly into Cloud Run environment variables at runtime. | `.env` files in images (insecure), GitHub Repository Secrets alone. |
| **Storage (Blobs)** | **Google Cloud Storage (GCS)** | Scalable object storage for scientific poster attachments, figures, and rendered PDF files. | Container local filesystem (ephemeral, not persistent). |
| **IaC** | **Terraform (`hashicorp/google`)** | Existing config under `terraform/`; modular, declarative, reproducible across staging/prod environments. | Pulumi, Google Cloud Deployment Manager. |

---

## 5. Security & Networking Considerations

1. **Database Access Security:**
   - Private IP communication using **Serverless VPC Access** or **Cloud SQL Auth Proxy**.
   - No direct 0.0.0.0/0 ingress allowed on the database instance.
   - Credentials stored in Secret Manager and rotated periodically.

2. **Application Runtime Security:**
   - Run container as a non-root user.
   - Cloud Run service execution under a dedicated Service Account (`presto-service-sa`) with minimum privilege (Least Privilege Principle).
   - Ingress configured to allow public HTTP/HTTPS on Cloud Run endpoint (or behind Cloud Armor WAF in production).

3. **Rendering Sandbox:**
   - Playwright PDF generation requires adequate container memory allocation (minimum 2 GiB / 2 vCPU on Cloud Run) with `/tmp` mounted in-memory.

---

## 6. GitHub Milestones & Implementation Roadmap

To track progress collaboratively in GitHub, this project is broken down into **6 Milestones**, each containing concrete, testable issues.

```
M1: Local Validation ──► M2: GCP Foundation & IaC ──► M3: Artifact Registry & CI/CD
                                                                    │
M6: Observability & Prod ◄── M5: Domain & Security ◄── M4: Cloud Run Deployment
```

---

### 📍 Milestone 1: Local Containerization & Validation (Current Baseline)
*Target: 100% complete (In final review)*

- [x] **Issue 1.1:** Standardize Dockerfile for Presto (FastAPI + dependencies + Playwright browser binaries).
- [x] **Issue 1.2:** Compose local multi-container environment (`db` with `pgvector/pgvector:pg14` and `app`).
- [x] **Issue 1.3:** Bootstrap and verify `schema/schema.sql` database initialization.
- [ ] **Issue 1.4:** Verify root endpoint redirect (`GET /` -> `307 /wikkit/magic`) and capture healthcheck baseline.

---

### 📍 Milestone 2: GCP Foundation & Infrastructure as Code (IaC)
*Target: Week 1*

- [ ] **Issue 2.1:** **GCP Project & IAM Setup**
  - Create/configure GCP Project (`presto-production` / `presto-demo`).
  - Provision Terraform state bucket (`gs://<project>-tf-state`).
  - Provision Service Accounts: `presto-deployer-sa` (CI/CD) and `presto-runtime-sa` (Cloud Run).
- [ ] **Issue 2.2:** **Artifact Registry Provisioning**
  - Create Docker repository `us-central1-docker.pkg.dev/<project>/presto-repo/presto-app`.
- [ ] **Issue 2.3:** **Terraform Cloud SQL Instance**
  - Update `terraform/main.tf` to enable private service access (VPC peering).
  - Verify flag `cloudsql.enable_pgvector = "on"`.
  - Provision database `presto` and run initial `schema/schema.sql` migration.
- [ ] **Issue 2.4:** **Secret Manager Configuration**
  - Store `DATABASE_URL`, `SECRET_KEY`, and AI API keys (`OPENAI_API_KEY`) in Google Secret Manager.

---

### 📍 Milestone 3: CI/CD Pipeline & Automated Image Publishing
*Target: Week 2*

- [ ] **Issue 3.1:** **GitHub Actions Workflows Setup**
  - Create `.github/workflows/ci.yml` for automated linting, test suite execution, and Docker build check.
- [ ] **Issue 3.2:** **Workload Identity Federation / Service Account Key Setup**
  - Configure secure keyless GCP authentication via GitHub Actions Workload Identity.
- [ ] **Issue 3.3:** **Automated Container Push**
  - Build multi-stage Docker container on `main` merge and publish tag `:latest` and `:<commit_sha>` to Artifact Registry.

---

### 📍 Milestone 4: Cloud Run Service Deployment & Configuration
*Target: Week 2-3*

- [ ] **Issue 4.1:** **Cloud Run Service Definition (Terraform / gcloud)**
  - Deploy service `presto-app` with 2 vCPU, 2 GiB RAM, minimum instances = 0, maximum instances = 10.
  - Set execution port to `8080`.
- [ ] **Issue 4.2:** **Database Connectivity (Cloud SQL Integration)**
  - Attach Cloud Run service to Cloud SQL instance connection name using Cloud SQL Auth Proxy connector.
- [ ] **Issue 4.3:** **Environment & Secret Ingestion**
  - Link Secret Manager secrets as environment variables in Cloud Run.
- [ ] **Issue 4.4:** **Smoke Testing & Endpoint Verification**
  - Validate `curl -i -L https://<service-url>/` returns HTTP 307 redirect to `/wikkit/magic`.
  - Validate database read/write queries and poster retrieval.

---

### 📍 Milestone 5: Domain, Networking, Ingress & Security
*Target: Week 3-4*

- [ ] **Issue 5.1:** **Custom Domain Mapping & SSL Certificate**
  - Map custom domain / subdomain (e.g., `presto.domain.com` or demo URL) using Cloud Run custom domain or Google Cloud Load Balancer.
- [ ] **Issue 5.2:** **Ingress & CORS Policies**
  - Configure allowed origins and headers in FastAPI router for production domain.
- [ ] **Issue 5.3:** **Static & Media Asset Handling (Cloud Storage)**
  - Ensure uploaded posters and rendered output stream directly to/from GCS buckets.

---

### 📍 Milestone 6: Observability, UAT & Production Cutover
*Target: Week 4*

- [ ] **Issue 6.1:** **Cloud Monitoring & Alerting**
  - Set up Cloud Monitoring dashboards for request count, container CPU/RAM utilization, and Cloud SQL latency.
  - Configure alerts for HTTP 5xx spikes and container restart anomalies.
- [ ] **Issue 6.2:** **User Acceptance Testing (UAT)**
  - Execute end-to-end user workflows: account registration, poster creation, AI extraction, search/autocomplete, and PDF export.
- [ ] **Issue 6.3:** **Operational Runbook & Disaster Recovery**
  - Document rollback steps, DB backup restoration, and secret rotation procedures in `docs/RUNBOOK.md`.

---

## 7. Risk Analysis & Mitigation Matrix

| Risk | Impact | Likelihood | Mitigation Strategy |
| :--- | :--- | :--- | :--- |
| **pgvector Compatibility on Cloud SQL** | High | Low | Validated via `database_flags { name = "cloudsql.enable_pgvector", value = "on" }` on PG14+. Tested in staging first. |
| **Playwright PDF Memory Spikes** | High | Medium | Configure Cloud Run instances with at least 2GB-4GB RAM; limit concurrent PDF render requests per worker using internal queue/semaphores. |
| **Database Connection Exhaustion** | High | Medium | Use asyncpg connection pooling with conservative `max_connections` or Cloud SQL Auth Proxy connection pooling. |
| **GCP Cloud Cost Overruns** | Medium | Low | Cloud Run scales to 0 instances when idle; Cloud SQL tier set to standard development tier (`db-custom-2-8192` or `db-f1-micro` for staging). |

---

## 8. Open Questions for Team Review

1. **Authentication Mode for Initial Demo:** Should the Cloud Run deployment remain publicly accessible (`--allow-unauthenticated`) for initial evaluation, or be guarded behind Google Cloud IAP (Identity-Aware Proxy)?
2. **Database Migration Strategy:** Do we run migrations via a Cloud Run Job or an initialization entrypoint inside the app container?
3. **Storage Bucket Provider:** Will poster attachments and PDFs persist in Cloud SQL byte arrays or immediately transition to Google Cloud Storage (GCS)?

---

## 9. Next Steps & Action Items

1. **Team Sign-off:** Review this RFC in GitHub PR and add comments.
2. **Milestone Setup:** Create Milestones 1 through 6 in the GitHub repository and populate corresponding issues.
3. **Execution:** Begin execution on Milestone 2 (Terraform and GCP foundational setup).
