-- Meridian reusable prompt skills.
-- Skills are prompt objects: list views expose name/debrief only, while detail views
-- can load the full prompt content.

CREATE TABLE IF NOT EXISTS skills (
  skill_id TEXT PRIMARY KEY,
  skill_name TEXT NOT NULL UNIQUE,
  skill_debrief TEXT NOT NULL DEFAULT '',
  skill_content TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT chk_skills_debrief_len CHECK (char_length(skill_debrief) <= 500)
);
