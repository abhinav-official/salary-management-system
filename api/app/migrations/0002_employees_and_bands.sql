CREATE TABLE employees (
    employee_id TEXT PRIMARY KEY NOT NULL
        CHECK (length(employee_id) > 0 AND employee_id = trim_text(employee_id)),
    employee_id_key TEXT GENERATED ALWAYS AS (normalized_key(employee_id)) STORED NOT NULL UNIQUE,
    name TEXT NOT NULL CHECK (length(name) > 0 AND name = trim_text(name)),
    name_key TEXT GENERATED ALWAYS AS (normalized_key(name)) STORED NOT NULL,
    email TEXT NOT NULL CHECK (length(email) > 0 AND email = trim_text(email)),
    email_key TEXT GENERATED ALWAYS AS (normalized_key(email)) STORED NOT NULL UNIQUE,
    department TEXT NOT NULL CHECK (length(department) > 0 AND department = trim_text(department)),
    department_key TEXT GENERATED ALWAYS AS (normalized_key(department)) STORED NOT NULL,
    level TEXT NOT NULL CHECK (level IN ('L1', 'L2', 'L3', 'L4', 'L5', 'L6', 'L7', 'L8')),
    country TEXT NOT NULL CHECK (country IN ('India', 'US', 'UK', 'Germany', 'Singapore', 'UAE')),
    salary_minor INTEGER NOT NULL CHECK (typeof(salary_minor) = 'integer' AND salary_minor >= 0),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (typeof(revision) = 'integer' AND revision >= 1)
) STRICT;

CREATE TABLE salary_bands (
    country TEXT NOT NULL CHECK (country IN ('India', 'US', 'UK', 'Germany', 'Singapore', 'UAE')),
    level TEXT NOT NULL CHECK (level IN ('L1', 'L2', 'L3', 'L4', 'L5', 'L6', 'L7', 'L8')),
    minimum_minor INTEGER NOT NULL CHECK (typeof(minimum_minor) = 'integer' AND minimum_minor >= 0),
    midpoint_minor INTEGER NOT NULL CHECK (typeof(midpoint_minor) = 'integer' AND midpoint_minor > 0),
    maximum_minor INTEGER NOT NULL CHECK (typeof(maximum_minor) = 'integer' AND maximum_minor >= 0),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (typeof(revision) = 'integer' AND revision >= 1),
    PRIMARY KEY (country, level),
    CHECK (minimum_minor <= midpoint_minor AND midpoint_minor <= maximum_minor)
) STRICT;

CREATE INDEX employees_country_level ON employees (country, level, employee_id_key);
CREATE INDEX employees_department ON employees (department_key, employee_id_key);
CREATE INDEX employees_level ON employees (level, employee_id_key);
CREATE INDEX employees_name ON employees (name_key, employee_id_key);
CREATE INDEX employees_country_salary ON employees (country, salary_minor, employee_id_key);

CREATE TRIGGER employees_immutable_id
BEFORE UPDATE OF employee_id ON employees
WHEN NEW.employee_id != OLD.employee_id
BEGIN
    SELECT RAISE(ABORT, 'Employee ID is immutable');
END;

CREATE TRIGGER employees_revision
BEFORE UPDATE ON employees
WHEN NEW.revision != OLD.revision + 1
BEGIN
    SELECT RAISE(ABORT, 'Employee revision must increment by one');
END;

CREATE TRIGGER salary_bands_revision
BEFORE UPDATE ON salary_bands
WHEN NEW.revision != OLD.revision + 1
BEGIN
    SELECT RAISE(ABORT, 'Band revision must increment by one');
END;
