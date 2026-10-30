-- PostgreSQL schema for the Poster conference system
-- Converted from schema.sql (MySQL) to PostgreSQL

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE chatbot_session (
    session_id TEXT PRIMARY KEY,
    data       BYTEA NOT NULL,
    version    BIGINT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX chatbot_session_expires_at ON chatbot_session (expires_at);

CREATE TABLE administration (
    username    TEXT,
    password    TEXT,
    updated     TIMESTAMPTZ NOT NULL
);

CREATE TABLE "user" (
    uid         SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    username    TEXT NOT NULL UNIQUE,
    email       TEXT NOT NULL UNIQUE,
    password    TEXT NOT NULL,
    superuser   BOOLEAN NOT NULL DEFAULT FALSE,
    pwd_updated TIMESTAMPTZ NOT NULL,    -- track quarterly change requirement
    recovery    TIMESTAMPTZ,
    created     TIMESTAMPTZ NOT NULL,
    updated     TIMESTAMPTZ NOT NULL,
    verified    TIMESTAMPTZ              -- null = unverified
);

CREATE INDEX idx_user_verified ON "user" (verified) WHERE verified IS NULL;

CREATE TABLE conference (
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
   notify        TEXT NOT NULL -- when new poster aded to conference
                 CHECK (notify IN ('never','immediately','hourly','daily',
                       'sunday','monday','tuesday','wednesday',
                       'thursday','friday','saturday')),
   details       JSONB,
   verified      TIMESTAMPTZ,
   created       TIMESTAMPTZ NOT NULL,
   updated       TIMESTAMPTZ NOT NULL,
   authorized    TIMESTAMPTZ,
   hidden        TIMESTAMPTZ,      -- null = visible
   hideafter     INTEGER NOT NULL CHECK (hideafter IN (0, 1)),
   search_vector tsvector,
   embedding     vector(1536)
);

CREATE INDEX idx_conference_starts ON conference (starts);
CREATE INDEX idx_conference_ends ON conference (ends);
CREATE INDEX idx_conference_hidden ON conference (hidden) WHERE hidden IS NULL;
CREATE INDEX idx_conference_verified ON conference (verified) WHERE verified IS NULL;

CREATE TABLE poster (
    pid          SERIAL PRIMARY KEY,
    cid          INTEGER REFERENCES conference(cid),
    uid          INTEGER REFERENCES "user"(uid) ON DELETE SET NULL,
    resumption   TEXT,
    logo         BYTEA,
    logotype     TEXT,        -- type of the logo (e.g., 'png', 'jpg', 'svg')
    width        INTEGER DEFAULT NULL,
    height       INTEGER DEFAULT NULL,
    units        TEXT NOT NULL DEFAULT 'cm' CHECK (units IN ('cm', 'mm', 'in')),
    description  TEXT,
    moreinfo     TEXT,
    moreinfopng  BYTEA,
    template     TEXT,
    theme        TEXT,
    fonts        TEXT,         -- name of the font set used in the poster
    searchable   INTEGER NOT NULL DEFAULT 0 CHECK (searchable IN (0, 1)),
    downloads    INTEGER NOT NULL DEFAULT 0,
    created      TIMESTAMPTZ NOT NULL,
    updated      TIMESTAMPTZ,
    search_vector tsvector,
    embedding    vector(1536)
);

CREATE INDEX idx_poster_uid ON poster (uid);
CREATE INDEX idx_poster_searchable ON poster (searchable) WHERE searchable = 1;
CREATE INDEX idx_poster_embedding_ivfflat ON poster
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- permissions to view or edit poster and user-specific configuration options, such as display layout

CREATE TABLE userposter (
    uid       INTEGER NOT NULL REFERENCES "user"(uid) ON DELETE CASCADE,
    pid       INTEGER NOT NULL REFERENCES poster(pid) ON DELETE CASCADE,
    editor     INTEGER NOT NULL CHECK (editor IN (0, 1)),
    settings  JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (uid, pid)
);

CREATE INDEX idx_userposter_uid ON userposter (uid);
CREATE INDEX idx_userposter_pid ON userposter (pid);

-- records the purchase of a poster so that it can be downloaded

CREATE TABLE purchase (
    pid       INTEGER NOT NULL REFERENCES poster(pid) ON DELETE CASCADE,
    uid       INTEGER NOT NULL REFERENCES "user"(uid) ON DELETE CASCADE,
    amount    NUMERIC(10, 2) NOT NULL,
    transid   TEXT NOT NULL,
    created   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (pid, uid)
);

CREATE TABLE download (
    did       SERIAL PRIMARY KEY,
    pid       INTEGER REFERENCES poster(pid) ON DELETE CASCADE,
    uid       INTEGER NOT NULL REFERENCES "user"(uid) ON DELETE CASCADE, 
    hash      TEXT NOT NULL,
    created   TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_download_pid ON download (pid);

CREATE TABLE show (
    cid         INTEGER NOT NULL REFERENCES conference(cid) ON DELETE CASCADE,
    pid         INTEGER NOT NULL REFERENCES poster(pid) ON DELETE CASCADE,
    searchable  INTEGER NOT NULL CHECK (searchable IN (0, 1)),
    approved    TIMESTAMPTZ,
    UNIQUE (cid, pid)
);

CREATE INDEX idx_show_pid ON show (pid);
CREATE INDEX idx_show_approved ON show (approved) WHERE approved IS NULL;

CREATE TABLE source (
    sid         SERIAL PRIMARY KEY,
    pid         INTEGER NOT NULL REFERENCES poster(pid) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('text', 'svg','mixed','image','chart','table','logo','moreinfo', 'qrcode')),
    parent      INTEGER REFERENCES source(sid) ON DELETE CASCADE,
    text        TEXT,
    description TEXT,
    summary     TEXT,
    size        INTEGER NOT NULL,
    height      INTEGER,
    width       INTEGER,
    fileid      UUID,
    storage_uri TEXT, -- absolute local path or gs://bucket/key for an original upload
    hash        TEXT, -- prevents repeatedly extracted the same item
    filename    TEXT, -- original filename of the uploaded file without the path
    mimetype    TEXT NOT NULL,
    added       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    usable      BOOLEAN NOT NULL DEFAULT TRUE,
    search_vector tsvector,
    embedding   vector(1536)
);

CREATE INDEX idx_source_pid ON source (pid);
CREATE INDEX idx_source_fileid ON source (fileid);
CREATE INDEX idx_source_parent ON source (parent);

-- Allow at most one source of each special kind per poster.
CREATE UNIQUE INDEX idx_source_pid_kind_unique ON source (pid, kind)
WHERE kind IN ('logo', 'moreinfo', 'qrcode');

CREATE INDEX idx_source_embedding_ivfflat ON source
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);


CREATE TABLE authors (
    aid         SERIAL PRIMARY KEY,
    pid         INTEGER NOT NULL REFERENCES poster(pid) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    affiliation TEXT,
    email       TEXT,
    errata      TEXT,
    UNIQUE (pid, name)
);

CREATE TYPE section_which AS ENUM(
    'title', 'abstract', 'introduction', 'materials and methods', 'results',
    'conclusion', 'future plans', 'literature cited', 'additional information',
    'acknowledgements'
);

CREATE TABLE section (
    xid         SERIAL PRIMARY KEY,
    pid         INTEGER NOT NULL REFERENCES poster(pid) ON DELETE CASCADE,
    which       section_which NOT NULL,
    content     TEXT NOT NULL,
    updated     TIMESTAMPTZ NOT NULL,
    search_vector tsvector,
    embedding   vector(1536),
    UNIQUE (pid, which)
);


CREATE TABLE keyterm (
    cid         INTEGER REFERENCES conference(cid) ON DELETE CASCADE,
    pid         INTEGER REFERENCES poster(pid) ON DELETE CASCADE,
    text        TEXT NOT NULL,
    UNIQUE (cid, text),
    CHECK (cid IS NOT NULL OR pid IS NOT NULL)
);

CREATE INDEX idx_keyterm_pid ON keyterm (pid);

CREATE TABLE attachment (
    tid         SERIAL PRIMARY KEY,
    pid         INTEGER NOT NULL REFERENCES poster(pid) ON DELETE CASCADE,
    name        TEXT,
    description TEXT,
    filename    TEXT NOT NULL,
    mimetype    TEXT NOT NULL,
    created     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_attachment_pid ON attachment (pid);

CREATE TABLE feedback (
    fid         SERIAL PRIMARY KEY,
    cid         INTEGER REFERENCES conference(cid) ON DELETE CASCADE,
    pid         INTEGER REFERENCES poster(pid) ON DELETE CASCADE,
    feedback    TEXT NOT NULL,
    created     TIMESTAMPTZ NOT NULL,
    reviewed    TIMESTAMPTZ,
    CHECK (cid IS NOT NULL OR pid IS NOT NULL)
);

CREATE INDEX idx_feedback_cid ON feedback (cid);
CREATE INDEX idx_feedback_pid ON feedback (pid);
CREATE INDEX idx_feedback_reviewed ON feedback (reviewed) WHERE reviewed IS NULL;

-- Seed administration record (password: Fr33d0m)
INSERT INTO administration (username, password, updated)
SELECT 'root', '$2b$12$Pe3/OcSm/3Jy.iuU0qbUaO2NbS1q6xbgAfaLiNpYXW2va2Ev7pZTC', NOW()
WHERE NOT EXISTS (SELECT 1 FROM administration WHERE username = 'root');

-- =========================================================================
-- Full-text search
-- =========================================================================

-- Source and section vectors use NEW values so inserts and edits are indexed
-- before the row is visible to queries.
CREATE FUNCTION trg_source_fts()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('english', coalesce(NEW.text, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(NEW.description, '')), 'B');
    RETURN NEW;
END;
$$;

CREATE FUNCTION trg_section_fts()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_vector := to_tsvector('english', coalesce(NEW.content, ''));
    RETURN NEW;
END;
$$;

CREATE TRIGGER source_fts_update
BEFORE INSERT OR UPDATE OF text, description
ON source
FOR EACH ROW EXECUTE FUNCTION trg_source_fts();

CREATE TRIGGER section_fts_update
BEFORE INSERT OR UPDATE OF content
ON section
FOR EACH ROW EXECUTE FUNCTION trg_section_fts();

CREATE INDEX idx_source_fts ON source USING GIN (search_vector);
CREATE INDEX idx_section_fts ON section USING GIN (search_vector);

-- ── Conference ────────────────────────────────────────────────────────────

CREATE INDEX idx_conference_fts ON conference USING GIN (search_vector);

-- Recompute the tsvector for a single conference.
-- Weights: name=A, location=B, description=C, keyterms=A
CREATE FUNCTION conference_search_vector(cid_in INTEGER)
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
-- Uses NEW so inserted rows are indexed before they are visible to queries.
CREATE FUNCTION trg_conference_fts()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('english', coalesce(NEW.name,        '')), 'A') ||
        setweight(to_tsvector('english', coalesce(NEW.location,    '')), 'B') ||
        setweight(to_tsvector('english', coalesce(NEW.description, '')), 'C') ||
        setweight(to_tsvector('english', coalesce(
            (SELECT string_agg(text, ' ') FROM keyterm WHERE cid = NEW.cid), ''
        )), 'A');
    RETURN NEW;
END;
$$;

CREATE TRIGGER conference_fts_update
BEFORE INSERT OR UPDATE OF name, description, location
ON conference
FOR EACH ROW EXECUTE FUNCTION trg_conference_fts();

-- ── Poster ────────────────────────────────────────────────────────────────

CREATE INDEX idx_poster_fts ON poster USING GIN (search_vector);

CREATE FUNCTION poster_search_vector(pid_in INTEGER)
RETURNS tsvector LANGUAGE sql STABLE AS $$
    SELECT
        setweight(to_tsvector('english', coalesce(p.description, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(
            (SELECT content FROM section WHERE pid = pid_in AND which = 'title'), ''
        )), 'A') ||
        setweight(to_tsvector('english', coalesce(
            (SELECT string_agg(content, ' ' ORDER BY which)
             FROM section WHERE pid = pid_in AND which != 'title'), ''
        )), 'D') ||
        setweight(to_tsvector('english', coalesce(
            (SELECT string_agg(name || ' ' || coalesce(affiliation, ''), ' ' ORDER BY aid)
             FROM authors WHERE pid = pid_in), ''
        )), 'B') ||
        setweight(to_tsvector('english', coalesce(
            (SELECT string_agg(text, ' ') FROM keyterm WHERE pid = pid_in), ''
        )), 'A')
    FROM poster p WHERE p.pid = pid_in;
$$;

CREATE FUNCTION trg_poster_fts()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        UPDATE poster SET search_vector = poster_search_vector(NEW.pid)
        WHERE pid = NEW.pid;
        RETURN NEW;
    END IF;
    NEW.search_vector := setweight(to_tsvector('english', coalesce(NEW.description, '')), 'B');
    RETURN NEW;
END;
$$;

CREATE FUNCTION trg_poster_child_fts()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP != 'INSERT' THEN
        UPDATE poster SET search_vector = poster_search_vector(OLD.pid)
        WHERE pid = OLD.pid;
    END IF;
    IF TG_OP != 'DELETE' THEN
        UPDATE poster SET search_vector = poster_search_vector(NEW.pid)
        WHERE pid = NEW.pid;
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER poster_fts_insert BEFORE INSERT ON poster
FOR EACH ROW EXECUTE FUNCTION trg_poster_fts();
CREATE TRIGGER poster_fts_update AFTER UPDATE OF description ON poster
FOR EACH ROW EXECUTE FUNCTION trg_poster_fts();
CREATE TRIGGER section_poster_fts AFTER INSERT OR UPDATE OR DELETE ON section
FOR EACH ROW EXECUTE FUNCTION trg_poster_child_fts();
CREATE TRIGGER authors_poster_fts AFTER INSERT OR UPDATE OR DELETE ON authors
FOR EACH ROW EXECUTE FUNCTION trg_poster_child_fts();
CREATE TRIGGER keyterm_poster_fts AFTER INSERT OR UPDATE OR DELETE ON keyterm
FOR EACH ROW EXECUTE FUNCTION trg_poster_child_fts();

-- New rows and edited text are indexed by the triggers above.