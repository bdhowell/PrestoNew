ALTER TABLE source
    DROP COLUMN IF EXISTS summary,
    DROP COLUMN IF EXISTS evaluation,
    DROP COLUMN IF EXISTS evaluation_hash;
