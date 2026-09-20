-- Runs once, on first initialisation of the pgvector container.
-- pgvector is part of the contract: correlation and similarity search need it.
CREATE EXTENSION IF NOT EXISTS vector;
