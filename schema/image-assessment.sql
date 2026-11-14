ALTER TABLE assessment
    ALTER COLUMN cid DROP NOT NULL;

ALTER TABLE assessment
    ADD COLUMN IF NOT EXISTS sid INTEGER REFERENCES source(sid) ON DELETE CASCADE;

ALTER TABLE assessment
    ADD COLUMN IF NOT EXISTS criteria JSONB,
    ADD COLUMN IF NOT EXISTS supported_elements TEXT[],
    ADD COLUMN IF NOT EXISTS limitations TEXT[],
    ADD COLUMN IF NOT EXISTS suggested_improvements TEXT[];

CREATE UNIQUE INDEX IF NOT EXISTS idx_assessment_sid_section
    ON assessment (sid, section);
