CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  source_url TEXT NOT NULL,
  num_clips INTEGER NOT NULL,
  aspect_ratio TEXT NOT NULL,
  quality TEXT NOT NULL,
  status TEXT NOT NULL,
  active_stage TEXT NOT NULL,
  created_at REAL NOT NULL,
  started_at REAL,
  completed_at REAL,
  retry_count INTEGER NOT NULL DEFAULT 0,
  output_dir TEXT NOT NULL,
  error_stage TEXT,
  error_message TEXT,
  clip_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS job_stages (
  job_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at REAL,
  completed_at REAL,
  message TEXT,
  PRIMARY KEY (job_id, stage),
  FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS job_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL,
  timestamp REAL NOT NULL,
  stage TEXT NOT NULL,
  message TEXT NOT NULL,
  FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS clips (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL,
  file_path TEXT NOT NULL,
  filename TEXT NOT NULL,
  title TEXT NOT NULL,
  score INTEGER NOT NULL DEFAULT 0,
  hook_sentence TEXT NOT NULL DEFAULT '',
  start_time REAL NOT NULL,
  end_time REAL NOT NULL,
  duration REAL NOT NULL,
  created_at REAL NOT NULL,
  FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_job ON job_logs(job_id, id DESC);
