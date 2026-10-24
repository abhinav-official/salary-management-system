CREATE TABLE employee_identities (
    employee_id TEXT PRIMARY KEY NOT NULL REFERENCES employees (employee_id) ON DELETE CASCADE,
    generation TEXT NOT NULL UNIQUE
) STRICT;

INSERT INTO employee_identities (employee_id, generation)
SELECT employee_id, lower(hex(randomblob(16))) FROM employees;

CREATE TRIGGER employees_identity
AFTER INSERT ON employees
BEGIN
    INSERT INTO employee_identities (employee_id, generation)
    VALUES (NEW.employee_id, lower(hex(randomblob(16))));
END;
