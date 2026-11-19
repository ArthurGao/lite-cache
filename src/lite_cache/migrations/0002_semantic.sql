-- L2 (design §4, S3): the partition a caller computed, the embedder that made the question's
-- vector, and the vector. Filled only by `semantic=True` writes with a question and a partition.
-- No ANN index (design §0): a partition holds few entries, so the lookup filters with the btree
-- below and compares exactly. `vector` without a dimension: one column for every embedder.

ALTER TABLE entries
    ADD COLUMN partition text,
    ADD COLUMN embedder text,
    ADD COLUMN question_embedding vector;

CREATE INDEX entries_semantic ON entries (scope COLLATE "C", partition, embedder)
    WHERE question_embedding IS NOT NULL;
