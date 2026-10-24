CREATE TABLE import_previews (
    preview_id TEXT PRIMARY KEY NOT NULL,
    owner TEXT NOT NULL,
    worksheet TEXT NOT NULL,
    source_digest TEXT NOT NULL CHECK(length(source_digest)=64),
    proposed_digest TEXT NOT NULL CHECK(length(proposed_digest)=64),
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL CHECK(expires_at=created_at+1800),
    status TEXT NOT NULL CHECK(status IN ('clean','invalid')),
    total_rows INTEGER NOT NULL CHECK(total_rows>=0),
    valid_count INTEGER NOT NULL CHECK(valid_count>=0),
    invalid_count INTEGER NOT NULL CHECK(invalid_count>=0 AND valid_count+invalid_count=total_rows),
    error_count INTEGER NOT NULL CHECK(error_count>=0),
    unbanded_count INTEGER NOT NULL CHECK(unbanded_count BETWEEN 0 AND total_rows),
    valid_unbanded_count INTEGER NOT NULL CHECK(valid_unbanded_count BETWEEN 0 AND valid_count),
    sealed INTEGER NOT NULL DEFAULT 0 CHECK(sealed IN (0,1)),
    CHECK((status='clean' AND error_count=0 AND total_rows>0) OR (status='invalid' AND error_count>0))
) STRICT;

CREATE TABLE import_preview_rows (
    preview_id TEXT NOT NULL REFERENCES import_previews(preview_id),
    row_number INTEGER NOT NULL CHECK(row_number BETWEEN 1 AND 1048576),
    employee_id TEXT, name TEXT, email TEXT, department TEXT, level TEXT, country TEXT,
    salary_minor INTEGER CHECK(salary_minor IS NULL OR salary_minor>=0),
    salary_input TEXT,
    rounding_applied INTEGER CHECK(rounding_applied IS NULL OR rounding_applied IN (0,1)),
    valid INTEGER NOT NULL CHECK(valid IN (0,1)),
    raw_cells TEXT NOT NULL CHECK(json_valid(raw_cells)),
    band_status TEXT CHECK(band_status IS NULL OR band_status IN ('Band-matched','Unbanded')),
    band_revision INTEGER CHECK(band_revision IS NULL OR band_revision>0),
    band_generation TEXT,
    band_minimum_minor INTEGER,
    band_midpoint_minor INTEGER,
    band_maximum_minor INTEGER,
    PRIMARY KEY(preview_id,row_number),
    CHECK((band_status='Band-matched' AND band_revision IS NOT NULL AND band_generation IS NOT NULL
        AND band_minimum_minor IS NOT NULL AND band_midpoint_minor IS NOT NULL AND band_maximum_minor IS NOT NULL
        AND band_minimum_minor>=0 AND band_midpoint_minor>0
        AND band_minimum_minor<=band_midpoint_minor AND band_midpoint_minor<=band_maximum_minor)
        OR ((band_status IS NULL OR band_status='Unbanded') AND band_revision IS NULL AND band_generation IS NULL
        AND band_minimum_minor IS NULL AND band_midpoint_minor IS NULL AND band_maximum_minor IS NULL))
) STRICT;

CREATE TABLE import_preview_errors (
    preview_id TEXT NOT NULL REFERENCES import_previews(preview_id),
    error_index INTEGER NOT NULL CHECK(error_index>0),
    row_number INTEGER,
    field TEXT NOT NULL,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    context TEXT NOT NULL CHECK(json_valid(context)),
    PRIMARY KEY(preview_id,error_index)
) STRICT;
CREATE INDEX import_preview_error_rows ON import_preview_errors(preview_id,row_number,error_index);
CREATE INDEX import_preview_owner ON import_previews(owner,preview_id);

CREATE TRIGGER import_preview_seal BEFORE UPDATE ON import_previews
BEGIN
    SELECT CASE WHEN OLD.sealed=1 OR NEW.sealed<>1
        OR NEW.preview_id<>OLD.preview_id OR NEW.owner<>OLD.owner OR NEW.worksheet<>OLD.worksheet
        OR NEW.source_digest<>OLD.source_digest OR NEW.proposed_digest<>OLD.proposed_digest
        OR NEW.created_at<>OLD.created_at OR NEW.expires_at<>OLD.expires_at OR NEW.status<>OLD.status
        OR NEW.total_rows<>OLD.total_rows OR NEW.valid_count<>OLD.valid_count OR NEW.invalid_count<>OLD.invalid_count
        OR NEW.error_count<>OLD.error_count OR NEW.unbanded_count<>OLD.unbanded_count
        OR NEW.valid_unbanded_count<>OLD.valid_unbanded_count
        THEN RAISE(ABORT,'Import previews are immutable') END;
    SELECT CASE WHEN NEW.total_rows<>(SELECT count(*) FROM import_preview_rows WHERE preview_id=OLD.preview_id)
        OR NEW.valid_count<>(SELECT count(*) FROM import_preview_rows WHERE preview_id=OLD.preview_id AND valid=1)
        OR NEW.error_count<>(SELECT count(*) FROM import_preview_errors WHERE preview_id=OLD.preview_id)
        OR NEW.unbanded_count<>(SELECT count(*) FROM import_preview_rows WHERE preview_id=OLD.preview_id AND band_status='Unbanded')
        OR NEW.valid_unbanded_count<>(SELECT count(*) FROM import_preview_rows WHERE preview_id=OLD.preview_id AND band_status='Unbanded' AND valid=1)
        THEN RAISE(ABORT,'Import preview totals do not reconcile') END;
END;
CREATE TRIGGER import_preview_delete BEFORE DELETE ON import_previews
BEGIN SELECT RAISE(ABORT,'Import previews are immutable'); END;
CREATE TRIGGER import_preview_row_insert BEFORE INSERT ON import_preview_rows
WHEN (SELECT sealed FROM import_previews WHERE preview_id=NEW.preview_id)=1
BEGIN SELECT RAISE(ABORT,'Import previews are immutable'); END;
CREATE TRIGGER import_preview_row_update BEFORE UPDATE ON import_preview_rows
BEGIN SELECT RAISE(ABORT,'Import previews are immutable'); END;
CREATE TRIGGER import_preview_row_delete BEFORE DELETE ON import_preview_rows
BEGIN SELECT RAISE(ABORT,'Import previews are immutable'); END;
CREATE TRIGGER import_preview_error_insert BEFORE INSERT ON import_preview_errors
WHEN (SELECT sealed FROM import_previews WHERE preview_id=NEW.preview_id)=1
BEGIN SELECT RAISE(ABORT,'Import previews are immutable'); END;
CREATE TRIGGER import_preview_error_update BEFORE UPDATE ON import_preview_errors
BEGIN SELECT RAISE(ABORT,'Import previews are immutable'); END;
CREATE TRIGGER import_preview_error_delete BEFORE DELETE ON import_preview_errors
BEGIN SELECT RAISE(ABORT,'Import previews are immutable'); END;
