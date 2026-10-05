-- PostgreSQL schema for the Poster conference system
-- Converted from schema.sql (MySQL) to PostgreSQL

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS administration (
    username    TEXT,
    password    TEXT,
    updated     TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS "user" (
    uid         SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    username    TEXT NOT NULL UNIQUE,
    email       TEXT NOT NULL UNIQUE,
    password    TEXT NOT NULL,
    superuser   INTEGER NOT NULL DEFAULT 0 CHECK (superuser IN (0, 1)),
    pwd_updated TIMESTAMPTZ NOT NULL,    -- track quarterly change requirement
    created     TIMESTAMPTZ NOT NULL,
    updated     TIMESTAMPTZ NOT NULL,
    verified    TIMESTAMPTZ              -- null = unverified
);

UPDATE "user" SET superuser = COALESCE(superuser, 0);

CREATE INDEX IF NOT EXISTS idx_user_verified ON "user" (verified) WHERE verified IS NULL;

CREATE TABLE IF NOT EXISTS conference (
   cid           SERIAL PRIMARY KEY,
   name          TEXT NOT NULL,
   description   TEXT NOT NULL,
   url           TEXT,
   starts        TIMESTAMPTZ NOT NULL,
   ends          TIMESTAMPTZ NOT NULL,
   location      TEXT NOT NULL,
   latitude      REAL,
   longitude     REAL,
   administrator TEXT NOT NULL,
   password      TEXT NOT NULL,
--   notify        TEXT NOT NULL
--                 CHECK (notify IN ('never','immediately','hourly','daily',
--                        'sunday','monday','tuesday','wednesday',
--                        'thursday','friday','saturday')),
   verified      TIMESTAMPTZ,
   created       TIMESTAMPTZ NOT NULL,
   updated       TIMESTAMPTZ NOT NULL,
   authorized    TIMESTAMPTZ,
   hidden        TIMESTAMPTZ,      -- null = visible
   hideafter     INTEGER NOT NULL CHECK (hideafter IN (0, 1)),
   search_vector tsvector,
    embedding     vector(1536)
);

CREATE INDEX IF NOT EXISTS idx_conference_starts ON conference (starts);
CREATE INDEX IF NOT EXISTS idx_conference_ends ON conference (ends);
CREATE INDEX IF NOT EXISTS idx_conference_hidden ON conference (hidden) WHERE hidden IS NULL;
CREATE INDEX IF NOT EXISTS idx_conference_verified ON conference (verified) WHERE verified IS NULL;

CREATE TABLE IF NOT EXISTS poster (
    pid        SERIAL PRIMARY KEY,
    uid        INTEGER REFERENCES "user"(uid) ON DELETE SET NULL,
    logo       BYTEA,
    template   TEXT NOT NULL DEFAULT 'windsor',
    dimensions TEXT NOT NULL DEFAULT '36x48',
    content    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created    TIMESTAMPTZ NOT NULL,
    updated    TIMESTAMPTZ,
    unprinted  INTEGER DEFAULT 0,
    print      TEXT,
    printed    TIMESTAMPTZ,
    theme      TEXT NOT NULL DEFAULT 'forest',
    font       TEXT NOT NULL DEFAULT 'Modern Publishing',
    searchable INTEGER NOT NULL DEFAULT 0 CHECK (searchable IN (0, 1)),
    uploaded   INTEGER NOT NULL DEFAULT 0 CHECK (uploaded IN (0, 1)),
    downloads  INTEGER NOT NULL DEFAULT 0,
    search_vector tsvector,
    embedding  vector(1536)
);

CREATE INDEX IF NOT EXISTS idx_poster_uid ON poster (uid);
CREATE INDEX IF NOT EXISTS idx_poster_searchable ON poster (searchable) WHERE searchable = 1;
-- CREATE INDEX IF NOT EXISTS idx_poster_position ON poster ("position");
CREATE INDEX IF NOT EXISTS idx_poster_embedding_ivfflat ON poster
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

CREATE TABLE IF NOT EXISTS userposter (
    uid       INTEGER NOT NULL REFERENCES "user"(uid) ON DELETE CASCADE,
    pid       INTEGER NOT NULL REFERENCES poster(pid) ON DELETE CASCADE,
    settings  JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (uid, pid)
);

CREATE INDEX IF NOT EXISTS idx_userposter_uid ON userposter (uid);
CREATE INDEX IF NOT EXISTS idx_userposter_pid ON userposter (pid);

CREATE TABLE IF NOT EXISTS purchase (
    pid       INTEGER NOT NULL REFERENCES poster(pid) ON DELETE CASCADE,
    uid       INTEGER NOT NULL REFERENCES "user"(uid) ON DELETE CASCADE,
    amount    NUMERIC(10, 2) NOT NULL,
    transid   TEXT NOT NULL,
    created   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (pid, uid)
);

CREATE TABLE IF NOT EXISTS download (
    did       SERIAL PRIMARY KEY,
    pid       INTEGER NOT NULL REFERENCES poster(pid) ON DELETE CASCADE,
    uid       INTEGER NOT NULL REFERENCES "user"(uid) ON DELETE CASCADE, 
    format    TEXT NOT NULL CHECK (format IN ('pdf','zip')),
    hash      TEXT NOT NULL,
    created   TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_download_pid ON download (pid);

CREATE TABLE IF NOT EXISTS content (
    xid         SERIAL PRIMARY KEY,
    pid         INTEGER NOT NULL REFERENCES poster(pid) ON DELETE CASCADE,
    updated     TIMESTAMPTZ NOT NULL,
    embedding  vector(1536),
    UNIQUE (pid, cardinal)
);

CREATE INDEX IF NOT EXISTS idx_content_embedding_ivfflat ON content
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

CREATE TABLE IF NOT EXISTS show (
    cid         INTEGER NOT NULL REFERENCES conference(cid) ON DELETE CASCADE,
    pid         INTEGER NOT NULL REFERENCES poster(pid) ON DELETE CASCADE,
    searchable  INTEGER NOT NULL CHECK (searchable IN (0, 1)),
    approved   TIMESTAMPTZ,
    UNIQUE (cid, pid)
);

CREATE INDEX IF NOT EXISTS idx_show_pid ON show (pid);
CREATE INDEX IF NOT EXISTS idx_show_approved ON show (approved) WHERE approved IS NULL;

CREATE TABLE IF NOT EXISTS source (
    sid         SERIAL PRIMARY KEY,
    pid         INTEGER NOT NULL REFERENCES poster(pid) ON DELETE CASCADE,
    text        TEXT,
    length      INTEGER NOT NULL,
    description TEXT,
    filename    TEXT NOT NULL,
    mimetype    TEXT NOT NULL,
    added       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_source_pid ON source (pid);

CREATE TABLE IF NOT EXISTS extract (
    eid         SERIAL PRIMARY KEY,
    sid         INTEGER NOT NULL REFERENCES source(sid) ON DELETE CASCADE,
    description TEXT,
    kind        TEXT NOT NULL CHECK (kind IN ('svg','mixed','image')),
    mimetype    TEXT NOT NULL,
    added       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_extract_sid ON extract (sid);

CREA

-- DO $$
-- BEGIN
--     IF EXISTS (
--         SELECT 1
--         FROM information_schema.columns
--         WHERE table_schema = current_schema()
--           AND table_name = 'extract'
--           AND column_name = 'pid'
--     ) AND NOT EXISTS (
--         SELECT 1
--         FROM information_schema.columns
--         WHERE table_schema = current_schema()
--           AND table_name = 'extract'
--           AND column_name = 'sid'
--     ) THEN
--         ALTER TABLE extract RENAME COLUMN pid TO sid;
--     END IF;
-- END
-- $$;

-- ALTER TABLE extract DROP CONSTRAINT IF EXISTS extract_kind_check;
-- UPDATE extract SET kind = 'mixed' WHERE kind = 'figure';
-- UPDATE extract SET kind = 'mixed' WHERE kind = 'text';
-- ALTER TABLE extract
-- ADD CONSTRAINT extract_kind_check
-- CHECK (kind IN ('svg','mixed','image'));

CREATE INDEX IF NOT EXISTS idx_extract_sid ON extract (sid);

-- CREATE TABLE IF NOT EXISTS reference (
--     rid         SERIAL PRIMARY KEY,
--     pid         INTEGER REFERENCES poster(pid) ON DELETE CASCADE,
--     eid         INTEGER REFERENCES extract(eid) ON DELETE CASCADE,
--     pageno      INTEGER,
--     bounding    JSONB
-- );

-- CREATE INDEX IF NOT EXISTS idx_reference_pid ON reference (pid);
-- CREATE INDEX IF NOT EXISTS idx_reference_eid ON reference (eid);

-- CREATE TABLE IF NOT EXISTS footnote (
--     fid         SERIAL PRIMARY KEY,
--     pid         INTEGER REFERENCES poster(pid) ON DELETE CASCADE,
--     xid         INTEGER REFERENCES content(xid) ON DELETE CASCADE,
--     text        TEXT NOT NULL,
--     CHECK (pid IS NOT NULL OR xid IS NOT NULL)
-- );

-- CREATE INDEX IF NOT EXISTS idx_footnote_pid ON footnote (pid);
-- CREATE INDEX IF NOT EXISTS idx_footnote_xid ON footnote (xid);

CREATE TABLE IF NOT EXISTS keyterm (
    cid         INTEGER REFERENCES conference(cid) ON DELETE CASCADE,
    pid         INTEGER REFERENCES poster(pid) ON DELETE CASCADE,
    text        TEXT NOT NULL,
    UNIQUE (cid, text),
    CHECK (cid IS NOT NULL OR pid IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_keyterm_pid ON keyterm (pid);

CREATE TABLE IF NOT EXISTS attachment (
    tid         SERIAL PRIMARY KEY,
    pid         INTEGER NOT NULL REFERENCES poster(pid) ON DELETE CASCADE,
    name        TEXT,
    description TEXT,
    filename    TEXT NOT NULL,
    mimetype    TEXT NOT NULL,
    created     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_attachment_pid ON attachment (pid);

CREATE TABLE IF NOT EXISTS feedback (
    fid         SERIAL PRIMARY KEY,
    cid         INTEGER REFERENCES conference(cid) ON DELETE CASCADE,
    pid         INTEGER REFERENCES poster(pid) ON DELETE CASCADE,
    feedback    TEXT NOT NULL,
    created     TIMESTAMPTZ NOT NULL,
    reviewed    TIMESTAMPTZ,
    CHECK (cid IS NOT NULL OR pid IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_feedback_cid ON feedback (cid);
CREATE INDEX IF NOT EXISTS idx_feedback_pid ON feedback (pid);
CREATE INDEX IF NOT EXISTS idx_feedback_reviewed ON feedback (reviewed) WHERE reviewed IS NULL;

-- Seed administration record (password: Fr33d0m)
INSERT INTO administration (username, password, updated)
SELECT 'root', '$2b$12$Pe3/OcSm/3Jy.iuU0qbUaO2NbS1q6xbgAfaLiNpYXW2va2Ev7pZTC', NOW()
WHERE NOT EXISTS (SELECT 1 FROM administration WHERE username = 'root');

-- =========================================================================
-- Full-text search
-- =========================================================================

-- ── Conference ────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_conference_fts ON conference USING GIN (search_vector);

-- Recompute the tsvector for a single conference.
-- Weights: name=A, location=B, description=C, keyterms=A
CREATE OR REPLACE FUNCTION conference_search_vector(cid_in INTEGER)
RETURNS tsvector LANGUAGE sql STABLE AS $$
    SELECT
        setweight(to_tsvector('english', coalesce(c.name,        '')), 'A') ||
        setweight(to_tsvector('english', coalesce(c.location,    '')), 'B') ||
        setweight(to_tsvector('english', coalesce(c.description, '')), 'C') ||
        setweight(to_tsvector('english', coalesce(
            (SELECT string_agg(text, ' ') FROM keyterm WHERE cid = cid_in), ''
        )), 'A')
    FROM conference c
    WHERE c.cid = cid_in;
$$;

-- Fired before insert/update of text columns; keeps search_vector current.
CREATE OR REPLACE FUNCTION trg_conference_fts()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_vector := conference_search_vector(NEW.cid);
    RETURN NEW;
END;
$$;

SET client_min_messages TO WARNING;
DROP TRIGGER IF EXISTS conference_fts_update ON conference;
RESET client_min_messages;

CREATE TRIGGER conference_fts_update
BEFORE INSERT OR UPDATE OF name, description, location
ON conference
FOR EACH ROW EXECUTE FUNCTION trg_conference_fts();

-- ── Poster ────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_poster_fts ON poster USING GIN (search_vector);

-- Recompute the tsvector for a single poster.
-- Weights: title=A, subtitle/authors/content-titles=B,
--          poster-footnotes/footnote-rows=C, content-bodies=D, keyterms=A
CREATE OR REPLACE FUNCTION poster_search_vector(pid_in INTEGER)
RETURNS tsvector LANGUAGE sql STABLE AS $$
    SELECT
        setweight(to_tsvector('english', coalesce(p.title,     '')), 'A') ||
        setweight(to_tsvector('english', coalesce(p.subtitle,  '')), 'B') ||
        setweight(to_tsvector('english', coalesce(p.authors,   '')), 'B') ||
        setweight(to_tsvector('english', coalesce(p.footnotes, '')), 'C') ||
        -- keyterms associated with this poster
        setweight(to_tsvector('english', coalesce(
            (SELECT string_agg(text, ' ') FROM keyterm WHERE pid = pid_in), ''
        )), 'A') ||
        -- rows in the footnote table (poster-level and via content blocks)
        setweight(to_tsvector('english', coalesce(
            (SELECT string_agg(fn.text, ' ')
               FROM footnote fn
              WHERE fn.pid = pid_in
                 OR fn.xid IN (SELECT xid FROM content WHERE pid = pid_in)), ''
        )), 'C') ||
        -- content block titles
        setweight(to_tsvector('english', coalesce(
            (SELECT string_agg(coalesce(cx.title, ''), ' ')
               FROM content cx WHERE cx.pid = pid_in), ''
        )), 'B') ||
        -- content block bodies (subtitle, content text, footnotes field)
        setweight(to_tsvector('english', coalesce(
            (SELECT string_agg(
                coalesce(cx.subtitle,  '') || ' ' ||
                coalesce(cx.content,   '') || ' ' ||
                coalesce(cx.footnotes, ''),
                ' '
             )
               FROM content cx WHERE cx.pid = pid_in), ''
        )), 'D')
    FROM poster p
    WHERE p.pid = pid_in;
$$;

-- Fired before insert/update of text columns on poster itself.
CREATE OR REPLACE FUNCTION trg_poster_fts()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_vector := poster_search_vector(NEW.pid);
    RETURN NEW;
END;
$$;

SET client_min_messages TO WARNING;
DROP TRIGGER IF EXISTS poster_fts_update ON poster;
RESET client_min_messages;

CREATE TRIGGER poster_fts_update
BEFORE INSERT OR UPDATE OF title, subtitle, authors, footnotes
ON poster
FOR EACH ROW EXECUTE FUNCTION trg_poster_fts();

-- ── Cross-table triggers ──────────────────────────────────────────────────

-- Keyterms: a row may belong to a conference (cid) OR a poster (pid).
-- Refreshes whichever parent has a non-null foreign key.
CREATE OR REPLACE FUNCTION trg_keyterm_fts()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    target_cid INTEGER;
    target_pid INTEGER;
BEGIN
    IF TG_OP = 'DELETE' THEN
        target_cid := OLD.cid;  target_pid := OLD.pid;
    ELSE
        target_cid := NEW.cid;  target_pid := NEW.pid;
    END IF;
    IF target_cid IS NOT NULL THEN
        UPDATE conference
           SET search_vector = conference_search_vector(target_cid)
         WHERE cid = target_cid;
    END IF;
    IF target_pid IS NOT NULL THEN
        UPDATE poster
           SET search_vector = poster_search_vector(target_pid)
         WHERE pid = target_pid;
    END IF;
    RETURN NULL;
END;
$$;

SET client_min_messages TO WARNING;
DROP TRIGGER IF EXISTS keyterm_fts_update ON keyterm;
RESET client_min_messages;

CREATE TRIGGER keyterm_fts_update
AFTER INSERT OR UPDATE OR DELETE ON keyterm
FOR EACH ROW EXECUTE FUNCTION trg_keyterm_fts();

-- Content blocks: refresh the owning poster whenever a block changes.
CREATE OR REPLACE FUNCTION trg_content_fts()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    target_pid INTEGER;
BEGIN
    IF TG_OP = 'DELETE' THEN
        target_pid := OLD.pid;
    ELSE
        target_pid := NEW.pid;
    END IF;
    UPDATE poster
       SET search_vector = poster_search_vector(target_pid)
     WHERE pid = target_pid;
    RETURN NULL;
END;
$$;

SET client_min_messages TO WARNING;
DROP TRIGGER IF EXISTS content_fts_update ON content;
RESET client_min_messages;

CREATE TRIGGER content_fts_update
AFTER INSERT OR UPDATE OR DELETE ON content
FOR EACH ROW EXECUTE FUNCTION trg_content_fts();

-- Footnote rows: refresh via poster.pid directly, or via content.pid when
-- the footnote belongs to a content block (xid).
CREATE OR REPLACE FUNCTION trg_footnote_fts()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    target_pid INTEGER;
    target_xid INTEGER;
BEGIN
    IF TG_OP = 'DELETE' THEN
        target_pid := OLD.pid;  target_xid := OLD.xid;
    ELSE
        target_pid := NEW.pid;  target_xid := NEW.xid;
    END IF;
    IF target_pid IS NOT NULL THEN
        UPDATE poster
           SET search_vector = poster_search_vector(target_pid)
         WHERE pid = target_pid;
    ELSIF target_xid IS NOT NULL THEN
        UPDATE poster p
           SET search_vector = poster_search_vector(c.pid)
          FROM content c
         WHERE c.xid = target_xid
           AND p.pid = c.pid;
    END IF;
    RETURN NULL;
END;
$$;

SET client_min_messages TO WARNING;
DROP TRIGGER IF EXISTS footnote_fts_update ON footnote;
RESET client_min_messages;

CREATE TRIGGER footnote_fts_update
AFTER INSERT OR UPDATE OR DELETE ON footnote
FOR EACH ROW EXECUTE FUNCTION trg_footnote_fts();

-- ── Back-fill existing rows ───────────────────────────────────────────────

UPDATE conference SET search_vector = conference_search_vector(cid);
UPDATE poster     SET search_vector = poster_search_vector(pid);