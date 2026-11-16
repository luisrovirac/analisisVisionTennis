-- Los análisis sin golpe detectado no tienen un valor válido que restaurar: se eliminan antes.
DELETE FROM analyses WHERE stroke_type IS NULL;
ALTER TABLE analyses ALTER COLUMN stroke_type SET NOT NULL;
