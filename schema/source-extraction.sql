-- Existing sources were processed during upload. New uploads explicitly set FALSE.
ALTER TABLE source ADD COLUMN IF NOT EXISTS extracted BOOLEAN NOT NULL DEFAULT TRUE;
