from __future__ import annotations

import sqlite3

from .config import DB_PATH


def get_connection(db_path=None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'parent')),
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kid_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    display_name TEXT NOT NULL,
    age_band TEXT NOT NULL,
    start_skill_level INTEGER NOT NULL,
    current_skill_level INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS course_materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    filename TEXT NOT NULL,
    stored_filename TEXT,
    stored_file_path TEXT,
    stored_file_size INTEGER NOT NULL DEFAULT 0,
    mime_type TEXT NOT NULL,
    source_text TEXT NOT NULL,
    extraction_status TEXT NOT NULL,
    validation_notes TEXT NOT NULL,
    generation_status TEXT NOT NULL,
    quality_score REAL NOT NULL DEFAULT 0,
    uploaded_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER NOT NULL REFERENCES course_materials(id) ON DELETE CASCADE,
    subject_name TEXT NOT NULL,
    chapter_name TEXT NOT NULL,
    topic_name TEXT NOT NULL,
    summary TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS concepts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    concept_title TEXT NOT NULL,
    explanation TEXT NOT NULL,
    example_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id INTEGER NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    prompt TEXT NOT NULL,
    choice_a TEXT NOT NULL,
    choice_b TEXT NOT NULL,
    choice_c TEXT NOT NULL,
    choice_d TEXT NOT NULL,
    correct_choice TEXT NOT NULL CHECK(correct_choice IN ('A', 'B', 'C', 'D')),
    explanation TEXT NOT NULL,
    hint_text TEXT NOT NULL,
    difficulty_level INTEGER NOT NULL,
    question_variant_group TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'pending',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quiz_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kid_profile_id INTEGER NOT NULL REFERENCES kid_profiles(id) ON DELETE CASCADE,
    topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    requested_skill_level INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    score REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS answer_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id INTEGER NOT NULL REFERENCES quiz_attempts(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    selected_choice TEXT NOT NULL CHECK(selected_choice IN ('A', 'B', 'C', 'D')),
    is_correct INTEGER NOT NULL,
    used_hint INTEGER NOT NULL DEFAULT 0,
    feedback_text TEXT NOT NULL,
    answered_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mastery_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kid_profile_id INTEGER NOT NULL REFERENCES kid_profiles(id) ON DELETE CASCADE,
    topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    chapter_name TEXT NOT NULL,
    mastery_percent REAL NOT NULL,
    attempts_count INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(kid_profile_id, topic_id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    details TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kid_course_assignments (
    kid_profile_id INTEGER NOT NULL REFERENCES kid_profiles(id) ON DELETE CASCADE,
    material_id INTEGER NOT NULL REFERENCES course_materials(id) ON DELETE CASCADE,
    assigned_at TEXT NOT NULL,
    PRIMARY KEY (kid_profile_id, material_id)
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    ensure_column(conn, "course_materials", "stored_filename", "TEXT")
    ensure_column(conn, "course_materials", "stored_file_path", "TEXT")
    ensure_column(conn, "course_materials", "stored_file_size", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "topics", "review_status", "TEXT NOT NULL DEFAULT 'pending'")
    ensure_column(conn, "questions", "review_status", "TEXT NOT NULL DEFAULT 'pending'")
    conn.commit()


def ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_type: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
