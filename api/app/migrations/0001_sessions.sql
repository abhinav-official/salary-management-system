CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY NOT NULL,
    csrf_token TEXT NOT NULL,
    expires_at INTEGER NOT NULL CHECK (typeof(expires_at) = 'integer')
) STRICT;
