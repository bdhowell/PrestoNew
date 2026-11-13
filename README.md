# AutoPoster

AutoPoster is a browser-based application for creating, organizing, archiving, and presenting scientific and educational posters. It combines a rich poster editor, conference and collection management, downloadable rendering, and searchable storage in a single web app.

For a detailed feature inventory, see [FEATURES.md](FEATURES.md). For search and retrieval details, see [SEARCH.md](SEARCH.md).

## What the project does

- Manages account access for authors and administrators.
- Supports poster authoring with a layout-driven editor.
- Stores poster content, attachments, metadata, and conference associations.
- Renders posters for browser viewing and PDF download.
- Organizes posters into conferences, archives, and browseable listings.
- Provides search and autocomplete using PostgreSQL full-text search and vector embeddings.

## Main capabilities

- Text, math, image, table and graph content blocks.
- Poster layouts with drag-and-drop positioning and resizing
- Conference hosting and poster collection workflows
- Attachment upload and viewing
- Download-ready PDF output
- Optional AI-assisted features for extraction and enrichment

## Tech stack

- Python 3.14
- aiohttp and aiohttp-jinja2
- PostgreSQL with asyncpg
- pgvector
- Playwright for PDF generation
- Stripe for payment-related routes

## Repository layout

- [app.py](app.py): application entrypoint, startup logic, and shared helpers
- [blueprints/](blueprints/): route modules for the main web areas
- [templates/](templates/): HTML templates
- [static/js/render.js](static/js/render.js): editor and renderer behavior
- [static/css/](static/css/): layout and styling assets
- [schema/schema.sql](schema/schema.sql): PostgreSQL schema and search functions
- [specs/](specs/): layout and size definitions used by the renderer
- [extract3/pdfplumber_pdf_reader.py](extract3/pdfplumber_pdf_reader.py): PDFPlumber-based text/image/table extractor

## Prerequisites

- Python 3.14
- PostgreSQL 14 or newer, with the pgvector extension
- A database role that can create tables, indexes, functions, and types

Homebrew's pgvector is built only for PostgreSQL 17 and 18. For PostgreSQL 14,
build it from source and load the schema:

```sh
git clone --branch v0.8.2 --depth 1 https://github.com/pgvector/pgvector.git
cd pgvector
make PG_CONFIG=/opt/homebrew/opt/postgresql@14/bin/pg_config PG_SYSROOT="$(xcrun --show-sdk-path)"
make install PG_CONFIG=/opt/homebrew/opt/postgresql@14/bin/pg_config PG_SYSROOT="$(xcrun --show-sdk-path)"
createdb presto
psql -d presto -v ON_ERROR_STOP=1 --single-transaction -f schema/schema.sql
```

The schema objects must be owned by the role in `DATABASE_URL`. The schema is
for initializing the maintained local database, not for upgrading older schemas.

For an existing database, apply each feature migration explicitly. Remove
obsolete source-analysis columns and install the poster thumbnail and image
assessment features with:

```sh
psql -d presto -v ON_ERROR_STOP=1 --single-transaction -f schema/source-analysis-cleanup.sql
psql -d presto -v ON_ERROR_STOP=1 --single-transaction -f schema/source-thumbnail.sql
psql -d presto -v ON_ERROR_STOP=1 --single-transaction -f schema/image-assessment.sql
psql -d presto -v ON_ERROR_STOP=1 --single-transaction -f schema/source-image-classification.sql
```

The graphics evaluation page skips to `/layout/edit` when every graphic has
current assessments for its Materials and Methods and Results sections.
Otherwise, it evaluates pending graphics and offers the section review page
when finished. `/content/evaluate` is its own resumption page: returning after
an interruption reuses current assessments and finishes the remaining graphics.
Each image evaluation is recorded in `assessment` for its Materials and Methods
and Results sections, including criteria, supported elements, limitations, and
suggested improvements, linked to the image source; the conference is optional.
Interrupted connections are retried automatically twice before offering a manual
retry; saved assessments are reused on each attempt.
Classified charts are normalized and stored as PNG images. For a classified
math-expression image, each extracted LaTeX expression is stored in `source.text`
on its own child source record, while the original image remains available.
Extracted and saved formulas are rendered with MathJax in source listings, the
Source graphics panel during section review, and
poster content; editing and saving poster sections preserves the original LaTeX.

The current draft's stored PNG thumbnail appears in both List and Large
thumbnails views. Opening the poster list refreshes it when the poster or its
sources are newer. Thumbnails preserve the saved poster aspect ratio inside a
640 x 640 PNG on a contrasting gray background, without stretching or cropping.
Both display modes use square thumbnail containers. Previews include wrapping
author lists and themed section borders.

- Poppler (`pdftocairo`) for importing vector figures from PDFs
- Playwright browser binaries installed

# To Do's

Create a .env file in the project root:

```env
APPLICATION_NAME=AutoPoster
DATABASE_URL=postgresql://localhost/poster
SECRET_KEY=replace-with-a-random-secret
SESSION_BACKEND=redis
REDIS_URL=redis://localhost:6379/0
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
SESSION_MAX_AGE=600
POSTER_UPLOAD_MAX_MB=32

# Optional AI features
OPENAI_API_KEY=
OPENAI_POSTER_MODEL=gpt-4.1
OPENAI_EMBEDDING_MODEL=text-embedding-3-small

# Optional autocomplete tuning
AUTOCOMPLETE_VECTOR_CANDIDATE_MULTIPLIER=5
AUTOCOMPLETE_VECTOR_CANDIDATE_MIN=25
AUTOCOMPLETE_MERGE_METHOD=rrf
AUTOCOMPLETE_MERGE_RRF_K=60
AUTOCOMPLETE_MERGE_FTS_WEIGHT=1
AUTOCOMPLETE_MERGE_VECTOR_WEIGHT=1
```

## Server-side sessions

After sign-in, an existing unpurchased poster is selected even in a fresh session.
Without a saved workflow resume URL (or when it points to the poster list), the
user goes directly to `/layout/edit`. Purchased posters retain the list fallback.

Sessions use Redis 7 or newer. Start Redis before the application and set
`SECRET_KEY` to a random secret of at least 32 bytes. `REDIS_URL` defaults to
`redis://localhost:6379/0`; use a private, access-controlled Redis service in
production. Redis keys use the `presto:session:` prefix and expire automatically.
Signed payloads retain the existing server-side chatbot memory and optimistic
concurrency checks. Redis failures fail requests rather than falling back to
PostgreSQL or accepting an unverified session.

`SESSION_MAX_AGE` (seconds, default 600) is an inactivity timeout: normal HTTP
requests renew it. `/system/timecheck` validates the HttpOnly session cookie and
returns `remaining_seconds`, `expires_at` (Unix seconds), and the display `name`
without renewing or creating a session by default. A timecheck marked with
`X-Session-Activity: 1` renews a live session after mouse movement, clicking,
touching, or a key press. Missing/expired sessions redirect to
`/account/signout`. Both `/account/signout` forms show Wikkit's personalized
expiry notice, clear session cookies, and do not start a new session.
Wikkit waits locally for the `login` button, which opens `/account/signin` just
like the page's sign-in link, without reconnecting the expired session.

Pages poll every minute and schedule five- and two-minute Wikkit warnings only
in the respective (5, 7) and (2, 4) minute windows. Repeated polls never reset
warnings for the same expiration; real activity starts a new expiration cycle.
The deadline is rechecked before redirecting, to honor renewal in another tab.

`SESSION_COOKIE_SECURE=true` is the default and requires HTTPS. Only local HTTP
development should set it to `false`. `SESSION_BACKEND=none` explicitly disables
sessions; `auto` enables Redis when `SECRET_KEY` or `REDIS_URL` is configured.
The old `postgres` backend is no longer supported. Deployment invalidates old
PostgreSQL sessions; users must sign in again. The unused `chatbot_session` table
can be dropped after deployment; no user or poster data is migrated.

Run session integration tests against an isolated Redis database:

```sh
TEST_REDIS_URL=redis://localhost:6379/15 bin/python -m unittest tests.test_sessions
node --test tests/sessions.test.js
```
