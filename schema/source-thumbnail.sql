ALTER TABLE source ADD COLUMN IF NOT EXISTS updated TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE source DROP CONSTRAINT IF EXISTS source_kind_check;
ALTER TABLE source ADD CONSTRAINT source_kind_check CHECK (
    kind IN ('text', 'svg', 'mixed', 'image', 'math', 'chart', 'table', 'logo',
             'moreinfo', 'qrcode', 'thumbnail')
);

DROP INDEX IF EXISTS idx_source_pid_kind_unique;
CREATE UNIQUE INDEX idx_source_pid_kind_unique ON source (pid, kind)
WHERE kind IN ('logo', 'moreinfo', 'qrcode', 'thumbnail');
