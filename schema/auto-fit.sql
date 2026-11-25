CREATE TABLE IF NOT EXISTS fitting_runs (
    run_id UUID PRIMARY KEY,
    pid INTEGER NOT NULL REFERENCES poster(pid) ON DELETE CASCADE,
    uid INTEGER NOT NULL,
    cancelled BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS sizings (
    sizing_id   BIGSERIAL PRIMARY KEY,
    pid         INTEGER NOT NULL REFERENCES poster(pid) ON DELETE CASCADE,
    section     section_which NOT NULL,
    length      INTEGER NOT NULL CHECK (length > 0),
    text        TEXT NOT NULL,
    source_text TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    run_id UUID REFERENCES fitting_runs(run_id) ON DELETE CASCADE,
    expected_text TEXT,
    version     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE sizings
    ADD COLUMN IF NOT EXISTS sizing_id BIGSERIAL,
    ADD COLUMN IF NOT EXISTS run_id UUID REFERENCES fitting_runs(run_id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS expected_text TEXT,
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
DECLARE
    existing_primary_key TEXT;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'sizings'::regclass AND conname = 'sizings_length_check'
    ) THEN
        ALTER TABLE sizings ADD CONSTRAINT sizings_length_check CHECK (length > 0);
    END IF;
    SELECT conname INTO existing_primary_key FROM pg_constraint
        WHERE conrelid = 'sizings'::regclass AND contype = 'p'
        AND pg_get_constraintdef(oid) <> 'PRIMARY KEY (sizing_id)';
    IF existing_primary_key IS NOT NULL THEN
        EXECUTE format('ALTER TABLE sizings DROP CONSTRAINT %I', existing_primary_key);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid = 'sizings'::regclass AND contype = 'p') THEN
        ALTER TABLE sizings ADD PRIMARY KEY (sizing_id);
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS fitting_applications (
    application_id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES fitting_runs(run_id) ON DELETE CASCADE,
    sizing_id BIGINT NOT NULL REFERENCES sizings(sizing_id) ON DELETE CASCADE,
    section section_which NOT NULL,
    prior_text TEXT NOT NULL,
    applied_text TEXT NOT NULL,
    rolled_back BOOLEAN NOT NULL DEFAULT FALSE
);

ALTER TABLE fitting_applications
    DROP CONSTRAINT IF EXISTS fitting_applications_sizing_id_fkey,
    ADD CONSTRAINT fitting_applications_sizing_id_fkey
        FOREIGN KEY (sizing_id) REFERENCES sizings(sizing_id) ON DELETE CASCADE;
