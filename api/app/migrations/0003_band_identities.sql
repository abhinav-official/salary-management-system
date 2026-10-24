CREATE TABLE band_identities (
    country TEXT NOT NULL,
    level TEXT NOT NULL,
    generation TEXT NOT NULL UNIQUE,
    PRIMARY KEY (country, level),
    FOREIGN KEY (country, level) REFERENCES salary_bands (country, level) ON DELETE CASCADE
) STRICT;

INSERT INTO band_identities (country, level, generation)
SELECT country, level, lower(hex(randomblob(16))) FROM salary_bands;

CREATE TRIGGER salary_bands_identity
AFTER INSERT ON salary_bands
BEGIN
    INSERT INTO band_identities (country, level, generation)
    VALUES (NEW.country, NEW.level, lower(hex(randomblob(16))));
END;
