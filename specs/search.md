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

## Related Files

- `blueprints/api_blueprint.py` contains the autocomplete query and rank fusion logic.
- `schema/schema.sql` defines the FTS columns, triggers, and indexes.
- `app.py` handles embedding generation and syncing for posters and conferences.