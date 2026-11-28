ALTER TABLE source ADD COLUMN IF NOT EXISTS dpi DOUBLE PRECISION
    CHECK (dpi > 0 AND dpi < 'Infinity'::double precision);
