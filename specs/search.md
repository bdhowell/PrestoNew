# Filter Text Search

This project uses a hybrid autocomplete/search path: a user types a filter term, the UI sends that text to the backend, and the backend combines PostgreSQL full text search with vector similarity before returning ranked suggestions.

The goal is to keep search useful in two different cases:

- exact or near-exact keyword matches should surface through FTS
- broader natural-language matches should surface through embeddings and cosine similarity

## Request Flow

The browser sends `GET /api/autocomplete` with these query parameters:

- `q`: the filter text from the input field
- `scope`: which result set to search, such as `conferences`, `posters`, `myposters`, or `myconferences`
- `limit`: how many results to return

The handler normalizes the filter text by trimming whitespace and collapsing internal whitespace. Empty queries return no items.

If OpenAI embeddings are configured, the backend also computes a query embedding for the same text. If embedding generation fails or is unavailable, the search still works on FTS alone.

## PostgreSQL FTS Layer

The schema stores a `search_vector` column on searchable tables and maintains it with triggers. The main tables involved in autocomplete are `conference` and `poster`.

Postgres full text search is built with `websearch_to_tsquery('english', q)` so users can type natural search phrases instead of raw tsquery syntax.

The ranked FTS pass uses:

- `search_vector @@ tsquery` to filter matches
- `ts_rank_cd(search_vector, tsquery)` to score matches

The schema also keeps the vectors up to date with trigger-backed recomputation. For example:

- conference FTS includes `name`, `location`, `description`, and key terms
- poster FTS includes `title`, `subtitle`, `authors`, `footnotes`, content titles, content bodies, footnote rows, and key terms

The `source` and `section` tables also support FTS alongside their existing
1536-dimensional embeddings:

- source FTS includes `text` (weight A) and `description` (weight B); null values are treated as empty text
- section FTS includes `content`
- `BEFORE INSERT OR UPDATE` triggers compute vectors from the new row values when searchable text changes
- GIN indexes support `search_vector @@ websearch_to_tsquery('english', q)` on both tables
- the schema adds missing search columns on existing tables and backfills existing rows through the triggers

These row-level vectors do not change conference/poster autocomplete scopes or
automatically regenerate embeddings. The schema calls poster content rows
`section`; there is no separate `item` table.

## Vector Similarity Layer

Each searchable row can also have a pgvector `embedding` column.

For autocomplete, the backend ranks rows by cosine distance using the pgvector `<=>` operator. Lower distance means closer semantic similarity, so the vector candidate set is ordered by ascending `<=>`.

If a row has no embedding, it is skipped from the vector branch.

## How The Two Signals Are Combined

The autocomplete query runs the FTS and vector searches separately inside SQL common table expressions:

1. Filter rows to the current scope.
2. Build an FTS candidate list from rows whose `search_vector` matches the tsquery.
3. Build a vector candidate list from rows with embeddings, ordered by similarity to the query embedding.
4. Merge both lists with a full outer join.
5. Fuse the two rankings with reciprocal rank fusion.

The final ordering uses:

$$
\Large\text{score} = \frac{w_{fts}}{k + rank_{fts}} + \frac{w_{vec}}{k + rank_{vec}}
$$

Where:

- `k` is `AUTOCOMPLETE_MERGE_RRF_K`
- `w_fts` is `AUTOCOMPLETE_MERGE_FTS_WEIGHT`
- `w_vec` is `AUTOCOMPLETE_MERGE_VECTOR_WEIGHT`

This means a result can win because it is strong in FTS, strong in vector similarity, or both.

## Scope Filters

The same search endpoint supports different scopes:

- `conferences`: searches visible conferences, with extra checks for `authorized` and `hideafter` when those columns exist
- `posters`: searches posters shown in a specific conference and only includes approved shows
- `myposters`: searches the logged-in user's posters
- `myconferences`: searches conferences that contain at least one poster owned by the logged-in user

Each scope applies its own non-search filters before FTS and vector ranking run.

## Candidate Limits And Tuning

The backend does not rank the entire table with both methods. It first takes a bounded candidate window from each branch, then fuses the results.

Relevant environment variables:

- `AUTOCOMPLETE_VECTOR_CANDIDATE_MULTIPLIER`
- `AUTOCOMPLETE_VECTOR_CANDIDATE_MIN`
- `AUTOCOMPLETE_MERGE_METHOD`
- `AUTOCOMPLETE_MERGE_RRF_K`
- `AUTOCOMPLETE_MERGE_FTS_WEIGHT`
- `AUTOCOMPLETE_MERGE_VECTOR_WEIGHT`

The vector candidate limit is computed from the requested `limit`, then raised to at least the configured minimum.

## Practical Behavior

- Short or exact queries usually benefit from FTS.
- Broader or paraphrased queries can still find results through embeddings.
- If the embedding service is unavailable, autocomplete degrades gracefully to FTS-only results.
- The merged result set is deterministic because tie-breaking falls back to label and then ID.

## Current FastAPI Poster List

The poster list implements the `myposters` scope directly through
`GET /posters/list?q=...`, rather than the legacy `/api/autocomplete` endpoint.
Only posters whose `poster.uid` matches the authenticated user enter either
candidate branch. The list accepts `sort=name|conference_date|relevance`,
`direction=asc|desc`, and `view=list|grid`. Choose descending relevance for the
highest-ranked search results first. Whitespace-only input shows all owned
posters without calling the embedding service.

Poster FTS now indexes the actual schema: the title section (A), description
and author names/affiliations (B), other section content (D), and key terms (A).
Triggers refresh it on poster description and section/author/key-term changes,
including deletions. Apply `schema/schema.sql` to install the triggers and
backfill existing posters; it is safe to reapply.

Query embeddings use `OPENAI_API_KEY` and `EMBEDDING_MODEL` (default
`text-embedding-3-small`), with 1536 dimensions to match the schema. Service
failures or missing configuration produce an explicit keyword-only notice and
a server log entry. Vector ranking uses existing `poster.embedding` values;
generating and updating stored content embeddings remains a separate ingestion
responsibility. Rows without embeddings can still match FTS.

Each branch takes `max(100 * AUTOCOMPLETE_VECTOR_CANDIDATE_MULTIPLIER,
AUTOCOMPLETE_VECTOR_CANDIDATE_MIN)` candidates (defaults: 4 and 100). Fusion uses
the RRF settings above; only `AUTOCOMPLETE_MERGE_METHOD=rrf` is supported.
Without search text, the list is not candidate-limited.

## Related Files

- `posters.py` contains owner-scoped persistence, embedding requests, and rank fusion.
- `routers/posters.py` handles the poster list and viewer routes.
- `schema/schema.sql` defines the FTS columns, triggers, and indexes.