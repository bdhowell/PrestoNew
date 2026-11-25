-- Allow multiple summary lengths and exclude legacy summaries of unknown content.
ALTER TABLE summarization DROP CONSTRAINT IF EXISTS summarization_pid_which_key;
ALTER TABLE summarization ADD COLUMN IF NOT EXISTS source_hash TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS idx_summarization_pid_which_words
    ON summarization (pid, which, words);
