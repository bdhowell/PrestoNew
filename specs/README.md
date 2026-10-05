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
- PostgreSQL 14 or newer
- A database role that can create and alter tables
- Poppler (`pdftocairo`) for importing vector figures from PDFs
- Playwright browser binaries installed

# To Do's

Create a .env file in the project root:

```env
APPLICATION_NAME=AutoPoster
DATABASE_URL=postgresql://localhost/poster
SECRET_KEY=replace-with-a-random-secret
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
SESSION_MAX_AGE=600
CLIENT_NOTIFICATION_INTERVAL=480
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
