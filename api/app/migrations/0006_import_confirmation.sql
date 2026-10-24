CREATE TABLE import_preview_context (
    preview_id TEXT PRIMARY KEY NOT NULL REFERENCES import_previews(preview_id),
    snapshot TEXT NOT NULL CHECK(json_valid(snapshot)),
    digest TEXT NOT NULL CHECK(length(digest)=64)
) STRICT;
CREATE TRIGGER import_context_insert BEFORE INSERT ON import_preview_context
WHEN (SELECT sealed FROM import_previews WHERE preview_id=NEW.preview_id)=1
BEGIN SELECT RAISE(ABORT,'Import preview context is immutable'); END;
CREATE TRIGGER import_context_update BEFORE UPDATE ON import_preview_context
BEGIN SELECT RAISE(ABORT,'Import preview context is immutable'); END;
CREATE TRIGGER import_context_delete BEFORE DELETE ON import_preview_context
BEGIN SELECT RAISE(ABORT,'Import preview context is immutable'); END;

CREATE TABLE import_receipts (
    preview_id TEXT PRIMARY KEY NOT NULL REFERENCES import_previews(preview_id),
    actor TEXT NOT NULL,
    completed_at INTEGER NOT NULL,
    inserted_count INTEGER NOT NULL CHECK(inserted_count>0),
    employee_ids TEXT NOT NULL CHECK(json_valid(employee_ids) AND json_type(employee_ids)='array'
        AND json_array_length(employee_ids)=inserted_count)
) STRICT;
CREATE TRIGGER import_receipt_insert BEFORE INSERT ON import_receipts
WHEN NOT EXISTS (SELECT 1 FROM import_previews p JOIN import_preview_context c USING(preview_id)
    WHERE p.preview_id=NEW.preview_id AND p.owner=NEW.actor AND p.sealed=1 AND p.status='clean'
    AND p.total_rows=NEW.inserted_count)
BEGIN SELECT RAISE(ABORT,'Receipt requires an owned clean preview'); END;
CREATE TRIGGER import_receipt_update BEFORE UPDATE ON import_receipts
BEGIN SELECT RAISE(ABORT,'Import receipts are immutable'); END;
CREATE TRIGGER import_receipt_delete BEFORE DELETE ON import_receipts
BEGIN SELECT RAISE(ABORT,'Import receipts are immutable'); END;
