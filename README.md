# Presto / AutoPoster – Local Development with Docker

This repository contains **AutoPoster**, a browser‑based scientific poster authoring platform built with FastAPI, PostgreSQL, and advanced AI‑powered search.

---

## 📦 What this repository offers
- **FastAPI server** (`presto-demo/app.py`) exposing the web UI and a JSON API.
- **PostgreSQL schema** (`schema/schema.sql`) with the `pgvector` extension for semantic search.
- **Docker Compose** configuration that spins up a ready‑to‑use **Postgres** container (including `pgvector`) and the **application** container.
- A **`.env`** file template that the app reads for configuration (database URL, secrets, AI keys, etc.).

---

## ⚙️ Quick start – Docker‑only (no host DB required)
1. **Clone the repo** (if you haven’t already) and `cd` into it:
   ```bash
   git clone <repo‑url>
   cd PrestoNew
   ```

2. **Create/adjust the environment file** – a minimal example is already present at the project root. Ensure the `DATABASE_URL` points to the Docker service name `db`:
   ```env
   APPLICATION_NAME="AutoPoster"
   DATABASE_URL="postgresql://root:Fr33d0m@db/presto"
   SECRET_KEY="$(openssl rand -hex 32)"
   SERVER_HOST=0.0.0.0
   SERVER_PORT=8000
   # …other optional variables…
   ```

3. **Build and start the containers**:
   ```bash
   docker compose up -d        # builds images and runs them in background
   docker compose logs -f db   # wait for the DB healthcheck to pass
   docker compose logs -f app  # then watch the FastAPI server start
   ```

4. **Open the app** in your browser:
   - http://localhost:8000/   (the default FastAPI root)
   - If you added a front‑end, it will be served from the same address.

5. **Stop & clean up** when you’re done:
   ```bash
   docker compose down -v   # removes containers and the persisted DB volume
   ```

---

## 🗄️ Database schema
The schema lives in `schema/schema.sql`. It creates:
- Core tables: `user`, `conference`, `poster`, `source`, `download`, …
- Full‑text search (`tsvector`) and **vector** columns for AI‑based similarity search.
- The `pgvector` extension is automatically installed in the custom PostgreSQL image.

You can explore the schema with:
```bash
psql postgresql://root:Fr33d0m@localhost:5432/presto -c "\dt"
```

---

## 🧪 Running the test suite
If you prefer to run tests inside the container (so they use the same DB), you can exec into the `app` service:
```bash
docker compose exec app pytest -q
```
Otherwise, activate a Python virtual environment locally and run `pytest` against the same PostgreSQL instance.

---

## 🔑 Optional AI features
The `.env` template includes placeholders for:
- `OPENAI_API_KEY`
- `OPENAI_MODEL` (e.g., `gpt-5.5`)
- `OPENAI_EMBEDDING` (e.g., `text-embedding-3-small`)
These enable:
- AI‑assisted poster extraction
- Vector‑based autocomplete
- Content enrichment via the Docling service (see `DOCLING_*` variables)

---

## 📂 Repository layout (relevant parts)
```
PrestoNew/
├─ app.py                # entry point for the FastAPI server
├─ docker/               # Dockerfile for the custom Postgres image
│   ├─ Dockerfile.postgres
│   └─ init_vector.sql   # creates the pgvector extension on init
├─ docker-compose.yml    # orchestration file (services: db, app)
├─ .env                  # environment configuration (example provided)
├─ schema/               # PostgreSQL schema files
│   └─ schema.sql
├─ presto-demo/          # FastAPI application code
│   ├─ app.py
│   └─ ...
└─ README.md             # <‑ you are reading it now
```

---

## 🙋‍♀️ Need help?
- Check the logs: `docker compose logs -f <service>`
- Verify the DB health: `docker compose exec db pg_isready -U root`
- Open an issue on the repository or ping the maintainer.

Enjoy building and presenting your scientific posters! 🚀
