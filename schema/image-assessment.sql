ALTER TABLE assessment
    ALTER COLUMN cid DROP NOT NULL;

ALTER TABLE assessment
    ADD COLUMN IF NOT EXISTS sid INTEGER REFERENCES source(sid) ON DELETE CASCADE;

CREATE UNIQUE INDEX IF NOT EXISTS idx_assessment_sid_section
    ON assessment (sid, section);
