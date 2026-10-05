We are creating a new SaaS FastAPI application to create conference and fair presentation posters and allow their creators to print them and share them with the public.
The application integrates a LangChain and LangGraph chatbot as specified in "Wikkit the Wizard.md"

The database is PostgreSQL equipped with PGVector. 
Sessions are managed with Redis (installed).
On-going chats are stored in Postgres to allow review and continuation between sessions

# AutoPoster Features

## Core platform

- Browser-based application for creating, organizing, archiving, and presenting scientific and educational posters.
- Supports poster authoring, conference and collection management, downloadable rendering, and searchable storage in a single web app.
- Posters can be viewed in the browser and exported to PDF for download.

## Session and account management

- Session timeouts are enforced with a client warning and automatic redirect flow.
- The app supports fast timeout handling for stale tabs and server-side requests.
- Expired sessions land on a dedicated timeout page for a consistent experience.

## Poster editor

### Content blocks

- Text blocks support rich editing with Unicode input and contextual formatting controls.
- Formatting options include bold, italics, underline, strikethrough, code, superscript, subscript, blockquotes, headings, lists, indentation, alignment, line-height controls, text size, horizontal rules, emoji insertion, and basic style clearing.
- Math blocks support LaTeX or ASCIIMath entry with a live preview.
- Image blocks support drag-and-drop, paste, upload, cropping, and caption placement.
- Table blocks can render tabular data or charts, including bar, line, scatter, bubble, radar, and polar-area views.
- Tables accept pasted content from spreadsheet tools such as Excel and Numbers, and can resize dynamically to fit imported data.
- Charts and tables can share a content block area with adjustable apportionment.

### Layout and composition

- Content blocks can be dragged between regions on a poster.
- Blocks can be resized from the left, right, or bottom, with snapping to common panel proportions.
- Layout adjustments are automatic, and alignment cues briefly appear when items line up.
- Block headers can be hidden or collapsed, allowing more complex composite layouts.

## Attachments and media

- Posters can include uploaded attachments and related documents.
- Attachments can be viewed individually and downloaded.
- Attachment views support several media types, including text, images, video, audio, and other special files.

## Conferences and archive

- Posters can be grouped into conferences and collections.
- Conference archives support importing posters from external sources as PDF files within configured upload size limits.
- Related documents can be uploaded alongside posters.
- Posters and attachments can be browsed in public or authenticated workflows.

## Search and discovery

- The app provides search and autocomplete for conferences, posters, and personal collections.
- Search combines PostgreSQL full-text search with vector embeddings when available.
- Results can be ranked using hybrid retrieval to improve relevance for natural-language queries.

## AI-assisted features

- Optional OpenAI-based embeddings support semantic search and related enrichment workflows.
- The app includes optional AI-assisted extraction and enrichment paths for poster-related content.

## Payments and access

- Payment routes support poster-related access and handout flows through Stripe.

## Forthcoming work

- Safari clipboard paste event support.
- Additional refinement to snap-based block apportionment.
- Continued work on poster and attachment monetization experience.
