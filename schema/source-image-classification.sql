ALTER TABLE source DROP CONSTRAINT IF EXISTS source_kind_check;
ALTER TABLE source ADD CONSTRAINT source_kind_check CHECK (
    kind IN ('text', 'svg', 'mixed', 'image', 'math', 'math expression', 'chart',
             'table', 'diagram', 'logo', 'moreinfo', 'qrcode', 'thumbnail')
);
