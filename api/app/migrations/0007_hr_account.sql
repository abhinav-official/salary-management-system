CREATE TABLE hr_account (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    email TEXT NOT NULL CHECK (length(email) > 0 AND email = normalized_key(email)),
    password_hash TEXT NOT NULL CHECK (substr(password_hash, 1, 7) = 'scrypt$')
) STRICT;
