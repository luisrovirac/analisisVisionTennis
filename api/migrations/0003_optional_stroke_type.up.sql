-- stroke_type NULL = el usuario no lo declaró: el worker lo detecta y lo rellena al completar.
-- El CHECK existente sigue siendo válido (CHECK sobre NULL no falla).
ALTER TABLE analyses ALTER COLUMN stroke_type DROP NOT NULL;
