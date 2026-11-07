ALTER TABLE poster
    ALTER COLUMN width TYPE DOUBLE PRECISION USING width::double precision,
    ALTER COLUMN height TYPE DOUBLE PRECISION USING height::double precision;
