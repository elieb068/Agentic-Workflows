-- Runs once, when the dev PostgreSQL volume is first created (compose.dev.yaml).
-- Separate database for pytest, so tests never touch the dev data.
CREATE DATABASE raioxia_test OWNER raioxia;
