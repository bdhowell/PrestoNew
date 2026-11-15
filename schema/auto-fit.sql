CREATE TABLE IF NOT EXISTS sizings (
    pid         INTEGER NOT NULL REFERENCES poster(pid) ON DELETE CASCADE,
    section     section_which NOT NULL,
    length      INTEGER NOT NULL CHECK (length > 0),
    text        TEXT NOT NULL,
    source_text TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    version     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (pid, section, source_hash, length)
);

ALTER TABLE sizings
    ADD COLUMN IF NOT EXISTS text TEXT,
    ADD COLUMN IF NOT EXISTS source_text TEXT,
    ADD COLUMN IF NOT EXISTS source_hash TEXT;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM sizings
        WHERE text IS NULL OR source_text IS NULL OR source_hash IS NULL
    ) THEN
        RAISE EXCEPTION 'Legacy sizings rows lack revision/source text. Backfill or archive those rows before applying auto-fit.sql.';
    END IF;
END
$$;

ALTER TABLE sizings
    ALTER COLUMN length TYPE INTEGER,
    ALTER COLUMN text SET NOT NULL,
    ALTER COLUMN source_text SET NOT NULL,
    ALTER COLUMN source_hash SET NOT NULL,
    ALTER COLUMN version SET DEFAULT NOW();

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'sizings'::regclass AND conname = 'sizings_length_check'
    ) THEN
        ALTER TABLE sizings ADD CONSTRAINT sizings_length_check CHECK (length > 0);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'sizings'::regclass AND contype = 'p'
    ) THEN
        ALTER TABLE sizings ADD PRIMARY KEY (pid, section, source_hash, length);
    END IF;
END
$$;
