from __future__ import annotations

import importlib
import io
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from .config import MAX_UPLOAD_BYTES, UPLOAD_DIR
from .security import hash_password, new_session_id


SUPPORTED_MIME_TYPES = {
    "text/plain",
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

TEXT_MIME_TYPES = {"text/plain"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def utcnow() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def has_admin_account(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone() is not None


def count_users_by_role(conn: sqlite3.Connection, role: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM users WHERE role = ?", (role,)).fetchone()[0]


def validate_account_input(email: str, password: str, display_name: str) -> list[str]:
    errors: list[str] = []
    if not EMAIL_RE.match(normalize_email(email)):
        errors.append("Enter a valid email address.")
    if len(password) < 10:
        errors.append("Password must be at least 10 characters long.")
    if display_name.strip() == "":
        errors.append("Display name is required.")
    return errors


def create_user(conn: sqlite3.Connection, email: str, password: str, role: str, display_name: str) -> tuple[sqlite3.Row | None, list[str]]:
    errors = validate_account_input(email, password, display_name)
    if role not in {"admin", "parent"}:
        errors.append("Unsupported role.")
    normalized = normalize_email(email)
    if conn.execute("SELECT 1 FROM users WHERE email = ?", (normalized,)).fetchone():
        errors.append("An account with that email already exists.")
    if errors:
        return None, errors
    now = utcnow()
    user_id = conn.execute(
        """
        INSERT INTO users (email, password_hash, role, display_name, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (normalized, hash_password(password), role, display_name.strip(), now),
    ).lastrowid
    conn.commit()
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone(), []


def create_initial_admin(conn: sqlite3.Connection, email: str, password: str, display_name: str) -> tuple[sqlite3.Row | None, list[str]]:
    if has_admin_account(conn):
        return None, ["Initial admin setup is already complete."]
    return create_user(conn, email, password, "admin", display_name)


def register_parent_account(conn: sqlite3.Connection, email: str, password: str, display_name: str) -> tuple[sqlite3.Row | None, list[str]]:
    return create_user(conn, email, password, "parent", display_name)


def seed_demo_data(conn: sqlite3.Connection) -> None:
    user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if user_count:
        return

    now = utcnow()
    conn.execute(
        """
        INSERT INTO users (email, password_hash, role, display_name, created_at)
        VALUES (?, ?, 'admin', 'Admin User', ?)
        """,
        ("admin@quizkid.local", hash_password("admin123"), now),
    )
    conn.execute(
        """
        INSERT INTO users (email, password_hash, role, display_name, created_at)
        VALUES (?, ?, 'parent', 'Parent User', ?)
        """,
        ("parent@quizkid.local", hash_password("parent123"), now),
    )
    parent_id = conn.execute("SELECT id FROM users WHERE email = ?", ("parent@quizkid.local",)).fetchone()[0]
    conn.execute(
        """
        INSERT INTO kid_profiles (parent_user_id, display_name, age_band, start_skill_level, current_skill_level, created_at)
        VALUES (?, 'Maya', 'Ages 8-10', 2, 2, ?)
        """,
        (parent_id, now),
    )
    material_id = conn.execute(
        """
        INSERT INTO course_materials
            (title, filename, mime_type, source_text, extraction_status, validation_notes, generation_status, quality_score, uploaded_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "Starter Fractions",
            "fractions.txt",
            "text/plain",
            "\n".join(
                [
                    "Fractions show equal parts of a whole.",
                    "A numerator tells how many parts are selected.",
                    "A denominator tells how many equal parts make the whole.",
                    "Equivalent fractions name the same amount in different ways.",
                ]
            ),
            "extracted",
            "Seeded demo material.",
            "generated",
            0.92,
            1,
            now,
        ),
    ).lastrowid
    topic_id = conn.execute(
        """
        INSERT INTO topics (material_id, subject_name, chapter_name, topic_name, summary, review_status, created_at)
        VALUES (?, ?, ?, ?, ?, 'approved', ?)
        """,
        (
            material_id,
            "Math",
            "Fractions Basics",
            "Understanding Fractions",
            "Kids learn the parts of a fraction and how equivalent fractions work.",
            now,
        ),
    ).lastrowid
    concepts = [
        (
            "Fractions as Parts",
            "Fractions describe equal parts of a whole object or group.",
            "If a pizza is cut into 4 equal pieces and you eat 1 piece, you ate 1/4 of the pizza.",
        ),
        (
            "Numerator",
            "The numerator is the top number and counts selected parts.",
            "In 3/5, the numerator is 3 because 3 parts are counted.",
        ),
        (
            "Denominator",
            "The denominator is the bottom number and shows the total equal parts.",
            "In 3/5, the denominator is 5 because the whole is split into 5 equal parts.",
        ),
        (
            "Equivalent Fractions",
            "Equivalent fractions have different numbers but the same value.",
            "1/2 and 2/4 are equivalent because they cover the same amount.",
        ),
    ]
    concept_ids = []
    for title, explanation, example in concepts:
        concept_id = conn.execute(
            """
            INSERT INTO concepts (topic_id, concept_title, explanation, example_text, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (topic_id, title, explanation, example, now),
        ).lastrowid
        concept_ids.append((concept_id, title, explanation, example))

    question_rows = [
        (
            concept_ids[0][0],
            "What does a fraction usually show?",
            "Equal parts of a whole",
            "The color of an object",
            "How fast something moves",
            "A random guess",
            "A",
            "Fractions are used to describe equal parts of a whole.",
            "Think about how a pizza can be split into equal slices.",
            1,
            "fractions-part-1",
        ),
        (
            concept_ids[1][0],
            "In the fraction 3/7, what does the numerator tell you?",
            "How many equal parts make the whole",
            "How many parts are selected",
            "The shape of the fraction",
            "The chapter number",
            "B",
            "The numerator is the top number and tells how many parts are selected.",
            "Look at the top number in the fraction.",
            2,
            "fractions-num-1",
        ),
        (
            concept_ids[2][0],
            "In the fraction 3/7, what does the denominator tell you?",
            "How many parts are selected",
            "How many answers are correct",
            "How many equal parts make the whole",
            "Which fraction is largest",
            "C",
            "The denominator is the bottom number and tells how many equal parts make the whole.",
            "Look at the bottom number in the fraction.",
            2,
            "fractions-den-1",
        ),
        (
            concept_ids[3][0],
            "Which pair shows equivalent fractions?",
            "1/2 and 2/4",
            "1/2 and 3/4",
            "2/3 and 1/5",
            "3/4 and 1/8",
            "A",
            "Equivalent fractions name the same amount in different ways.",
            "Think about fractions that cover the same amount.",
            3,
            "fractions-eq-1",
        ),
        (
            concept_ids[1][0],
            "Which number is the numerator in 5/8?",
            "8",
            "5",
            "13",
            "1",
            "B",
            "The numerator is the top number, so in 5/8 it is 5.",
            "The numerator sits on top.",
            1,
            "fractions-num-2",
        ),
        (
            concept_ids[2][0],
            "Which number is the denominator in 5/8?",
            "5",
            "13",
            "1",
            "8",
            "D",
            "The denominator is the bottom number, so in 5/8 it is 8.",
            "The denominator sits on the bottom.",
            1,
            "fractions-den-2",
        ),
    ]
    for row in question_rows:
        conn.execute(
            """
            INSERT INTO questions
                (concept_id, prompt, choice_a, choice_b, choice_c, choice_d, correct_choice, explanation, hint_text, difficulty_level, question_variant_group, review_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?)
            """,
            (*row, now),
        )
    conn.commit()


def authenticate_user(conn: sqlite3.Connection, email: str, password: str) -> sqlite3.Row | None:
    from .security import verify_password

    user = conn.execute("SELECT * FROM users WHERE email = ?", (normalize_email(email),)).fetchone()
    if not user:
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    return user


def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    session_id = new_session_id()
    now = datetime.now(UTC)
    expires = now + timedelta(days=7)
    conn.execute(
        "INSERT INTO sessions (id, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (session_id, user_id, now.isoformat(), expires.isoformat()),
    )
    conn.commit()
    return session_id


def get_session_user(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    row = conn.execute(
        """
        SELECT users.*
        FROM sessions
        JOIN users ON users.id = sessions.user_id
        WHERE sessions.id = ? AND sessions.expires_at > ?
        """,
        (session_id, datetime.now(UTC).isoformat()),
    ).fetchone()
    return row


def destroy_session(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()


def create_kid_profile(conn: sqlite3.Connection, parent_user_id: int, display_name: str, age_band: str, start_skill_level: int) -> None:
    now = utcnow()
    conn.execute(
        """
        INSERT INTO kid_profiles (parent_user_id, display_name, age_band, start_skill_level, current_skill_level, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (parent_user_id, display_name.strip(), age_band.strip(), start_skill_level, start_skill_level, now),
    )
    conn.commit()


def list_parent_kids(conn: sqlite3.Connection, parent_user_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM kid_profiles
        WHERE parent_user_id = ?
        ORDER BY display_name
        """,
        (parent_user_id,),
    ).fetchall()


def list_materials(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT course_materials.*, users.display_name AS uploader_name
        FROM course_materials
        JOIN users ON users.id = course_materials.uploaded_by
        ORDER BY course_materials.created_at DESC
        """
    ).fetchall()


def get_material(conn: sqlite3.Connection, material_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT course_materials.*, users.display_name AS uploader_name
        FROM course_materials
        JOIN users ON users.id = course_materials.uploaded_by
        WHERE course_materials.id = ?
        """,
        (material_id,),
    ).fetchone()


def get_material_topics(conn: sqlite3.Connection, material_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM topics WHERE material_id = ? ORDER BY topic_name",
        (material_id,),
    ).fetchall()


def validate_material_upload(filename: str, mime_type: str, payload: bytes) -> list[str]:
    notes: list[str] = []
    if not filename:
        notes.append("A file is required.")
    if mime_type not in SUPPORTED_MIME_TYPES:
        notes.append(f"Unsupported file type: {mime_type}.")
    if not payload:
        notes.append("The uploaded file is empty.")
    if len(payload) > MAX_UPLOAD_BYTES:
        notes.append(f"The uploaded file is too large. Limit is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    return notes


def load_pdf_reader_class():
    module = importlib.import_module("pypdf")
    return module.PdfReader


def normalize_extracted_text(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.replace("\r", "\n").split("\n")]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def extract_pdf_text(payload: bytes) -> tuple[str, str]:
    try:
        reader_class = load_pdf_reader_class()
    except ModuleNotFoundError:
        return (
            "PDF parsing dependency is not installed. Install requirements.txt so QuizKid can extract real PDF content.",
            "stored",
        )

    try:
        reader = reader_class(io.BytesIO(payload))
        page_text = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                page_text.append(text)
        extracted = normalize_extracted_text("\n".join(page_text))
        if not extracted:
            return (
                "PDF was stored, but no readable text was extracted. The file may be image-only or scanned.",
                "stored",
            )
        return extracted, "extracted"
    except Exception as exc:
        return (f"PDF was stored, but parsing failed: {exc}", "stored")


def extract_source_text(filename: str, mime_type: str, payload: bytes) -> tuple[str, str]:
    if mime_type == "application/pdf":
        return extract_pdf_text(payload)
    if mime_type in TEXT_MIME_TYPES:
        return normalize_extracted_text(payload.decode("utf-8", errors="ignore")), "extracted"
    note = (
        f"Stored {filename} successfully, but deep parsing for {mime_type} requires an external extractor. "
        "A lightweight placeholder summary will be generated."
    )
    return note, "stored"


def store_material_upload(filename: str, payload: bytes) -> tuple[str, str, int]:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix
    stored_filename = f"{uuid4().hex}{suffix}"
    stored_path = UPLOAD_DIR / stored_filename
    stored_path.write_bytes(payload)
    return stored_filename, str(stored_path), len(payload)


def _normalize_chunks(source_text: str) -> list[str]:
    chunks = [chunk.strip() for chunk in normalize_extracted_text(source_text).split("\n") if chunk.strip()]
    unique: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        key = chunk.lower()
        if key not in seen:
            seen.add(key)
            unique.append(chunk)
    return unique[:8]


def _title_from_chunk(chunk: str) -> str:
    words = chunk.replace(".", "").split()
    return " ".join(words[:4]) if words else "Concept"


def generate_content_from_material(conn: sqlite3.Connection, material_id: int, title: str, source_text: str) -> tuple[str, float]:
    now = utcnow()
    chunks = _normalize_chunks(source_text)
    if not chunks:
        return "quarantined", 0.1

    subject_name = "General Studies"
    chapter_name = title.strip() or "Uploaded Chapter"
    topic_name = title.strip() or "New Topic"
    summary = chunks[0][:220]
    topic_id = conn.execute(
        """
        INSERT INTO topics (material_id, subject_name, chapter_name, topic_name, summary, review_status, created_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
        """,
        (material_id, subject_name, chapter_name, topic_name, summary, now),
    ).lastrowid

    concept_ids: list[int] = []
    explanations: list[str] = []
    for idx, chunk in enumerate(chunks[:4], start=1):
        concept_id = conn.execute(
            """
            INSERT INTO concepts (topic_id, concept_title, explanation, example_text, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                topic_id,
                _title_from_chunk(chunk),
                chunk,
                f"Example {idx}: {chunk}",
                now,
            ),
        ).lastrowid
        concept_ids.append(concept_id)
        explanations.append(chunk)

    if len(concept_ids) < 2:
        return "quarantined", 0.25

    for idx, concept_id in enumerate(concept_ids):
        concept = explanations[idx]
        distractors = [text for pos, text in enumerate(explanations) if pos != idx] or [concept]
        choices = [concept, *distractors[:3]]
        while len(choices) < 4:
            choices.append("This does not match the concept.")
        prompt = f"Which explanation best matches the concept '{_title_from_chunk(concept)}'?"
        difficulty = min(3, 1 + idx)
        conn.execute(
            """
            INSERT INTO questions
                (concept_id, prompt, choice_a, choice_b, choice_c, choice_d, correct_choice, explanation, hint_text, difficulty_level, question_variant_group, review_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                concept_id,
                prompt,
                choices[0],
                choices[1],
                choices[2],
                choices[3],
                "A",
                f"The concept '{_title_from_chunk(concept)}' matches the first description because it comes from the source material for this concept.",
                f"Review the concept card for '{_title_from_chunk(concept)}' before answering.",
                difficulty,
                f"material-{material_id}-concept-{concept_id}",
                now,
            ),
        )
    return "generated", min(0.95, 0.55 + len(concept_ids) * 0.1)


def create_material(conn: sqlite3.Connection, user_id: int, title: str, filename: str, mime_type: str, payload: bytes) -> tuple[bool, list[str]]:
    notes = validate_material_upload(filename, mime_type, payload)
    now = utcnow()
    if notes:
        return False, notes

    source_text, extraction_status = extract_source_text(filename, mime_type, payload)
    stored_filename, stored_file_path, stored_file_size = store_material_upload(filename, payload)
    material_id = conn.execute(
        """
        INSERT INTO course_materials
            (title, filename, stored_filename, stored_file_path, stored_file_size, mime_type, source_text, extraction_status, validation_notes, generation_status, quality_score, uploaded_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'processing', 0, ?, ?)
        """,
        (
            title.strip() or filename,
            filename,
            stored_filename,
            stored_file_path,
            stored_file_size,
            mime_type,
            source_text,
            extraction_status,
            "Pending checks.",
            user_id,
            now,
        ),
    ).lastrowid
    generation_status, quality_score = generate_content_from_material(conn, material_id, title.strip() or filename, source_text)
    validation_notes = (
        "Generated automatically. Review quality score and generated topics."
        if generation_status == "generated"
        else "Material stored but generated content was quarantined because source structure was too weak."
    )
    conn.execute(
        """
        UPDATE course_materials
        SET generation_status = ?, quality_score = ?, validation_notes = ?
        WHERE id = ?
        """,
        (generation_status, quality_score, validation_notes, material_id),
    )
    conn.execute(
        """
        INSERT INTO audit_logs (actor_user_id, action, entity_type, entity_id, details, created_at)
        VALUES (?, 'upload_material', 'course_material', ?, ?, ?)
        """,
        (user_id, str(material_id), f"Uploaded {filename} with status {generation_status}.", now),
    )
    conn.commit()
    return True, [validation_notes]


def list_material_questions(conn: sqlite3.Connection, material_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT questions.*, concepts.concept_title, topics.topic_name
        FROM questions
        JOIN concepts ON concepts.id = questions.concept_id
        JOIN topics ON topics.id = concepts.topic_id
        WHERE topics.material_id = ?
        ORDER BY topics.topic_name, concepts.concept_title, questions.id
        """,
        (material_id,),
    ).fetchall()


def set_topic_review_status(conn: sqlite3.Connection, topic_id: int, review_status: str, actor_user_id: int) -> tuple[bool, str]:
    if review_status not in {"approved", "rejected", "pending"}:
        return False, "Unsupported review status."
    topic = conn.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()
    if not topic:
        return False, "Topic not found."
    now = utcnow()
    conn.execute("UPDATE topics SET review_status = ? WHERE id = ?", (review_status, topic_id))
    if review_status != "approved":
        conn.execute(
            """
            UPDATE questions
            SET review_status = 'rejected', active = 0
            WHERE concept_id IN (SELECT id FROM concepts WHERE topic_id = ?)
            """,
            (topic_id,),
        )
    conn.execute(
        """
        INSERT INTO audit_logs (actor_user_id, action, entity_type, entity_id, details, created_at)
        VALUES (?, 'review_topic', 'topic', ?, ?, ?)
        """,
        (actor_user_id, str(topic_id), f"Marked topic as {review_status}.", now),
    )
    conn.commit()
    return True, f"Topic marked as {review_status}."


def set_question_review_status(conn: sqlite3.Connection, question_id: int, review_status: str, actor_user_id: int) -> tuple[bool, str]:
    if review_status not in {"approved", "rejected", "pending"}:
        return False, "Unsupported review status."
    question = conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
    if not question:
        return False, "Question not found."
    now = utcnow()
    active = 1 if review_status == "approved" else 0
    conn.execute("UPDATE questions SET review_status = ?, active = ? WHERE id = ?", (review_status, active, question_id))
    conn.execute(
        """
        INSERT INTO audit_logs (actor_user_id, action, entity_type, entity_id, details, created_at)
        VALUES (?, 'review_question', 'question', ?, ?, ?)
        """,
        (actor_user_id, str(question_id), f"Marked question as {review_status}.", now),
    )
    conn.commit()
    return True, f"Question marked as {review_status}."


def clear_generated_content(conn: sqlite3.Connection, material_id: int) -> None:
    conn.execute("DELETE FROM topics WHERE material_id = ?", (material_id,))
    conn.commit()


def regenerate_material(conn: sqlite3.Connection, material_id: int, actor_user_id: int) -> tuple[bool, str]:
    material = get_material(conn, material_id)
    if not material:
        return False, "Material not found."

    clear_generated_content(conn, material_id)
    generation_status, quality_score = generate_content_from_material(conn, material_id, material["title"], material["source_text"])
    validation_notes = (
        "Generated automatically. Review quality score and generated topics."
        if generation_status == "generated"
        else "Material stored but generated content was quarantined because source structure was too weak."
    )
    now = utcnow()
    conn.execute(
        """
        UPDATE course_materials
        SET generation_status = ?, quality_score = ?, validation_notes = ?
        WHERE id = ?
        """,
        (generation_status, quality_score, validation_notes, material_id),
    )
    conn.execute(
        """
        INSERT INTO audit_logs (actor_user_id, action, entity_type, entity_id, details, created_at)
        VALUES (?, 'regenerate_material', 'course_material', ?, ?, ?)
        """,
        (actor_user_id, str(material_id), f"Regenerated {material['filename']} with status {generation_status}.", now),
    )
    conn.commit()
    return True, validation_notes


def delete_material(conn: sqlite3.Connection, material_id: int, actor_user_id: int) -> tuple[bool, str]:
    material = get_material(conn, material_id)
    if not material:
        return False, "Material not found."

    stored_file_path = material["stored_file_path"]
    if stored_file_path:
        try:
            path = Path(stored_file_path)
            if path.exists():
                path.unlink()
        except OSError:
            pass

    now = utcnow()
    conn.execute(
        """
        INSERT INTO audit_logs (actor_user_id, action, entity_type, entity_id, details, created_at)
        VALUES (?, 'delete_material', 'course_material', ?, ?, ?)
        """,
        (actor_user_id, str(material_id), f"Deleted {material['filename']}.", now),
    )
    conn.execute("DELETE FROM course_materials WHERE id = ?", (material_id,))
    conn.commit()
    return True, "Material deleted."


def list_topics_for_kid(conn: sqlite3.Connection, kid_profile_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT topics.*,
               COALESCE(mastery_scores.mastery_percent, 0) AS mastery_percent,
               COALESCE(mastery_scores.attempts_count, 0) AS attempts_count
        FROM topics
        LEFT JOIN mastery_scores
          ON mastery_scores.topic_id = topics.id AND mastery_scores.kid_profile_id = ?
        WHERE topics.review_status = 'approved'
        ORDER BY topics.subject_name, topics.chapter_name, topics.topic_name
        """,
        (kid_profile_id,),
    ).fetchall()


def get_kid_profile(conn: sqlite3.Connection, kid_profile_id: int, parent_user_id: int | None = None) -> sqlite3.Row | None:
    if parent_user_id is None:
        return conn.execute("SELECT * FROM kid_profiles WHERE id = ?", (kid_profile_id,)).fetchone()
    return conn.execute(
        "SELECT * FROM kid_profiles WHERE id = ? AND parent_user_id = ?",
        (kid_profile_id, parent_user_id),
    ).fetchone()


def get_topic(conn: sqlite3.Connection, topic_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()


def get_topic_concepts(conn: sqlite3.Connection, topic_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM concepts WHERE topic_id = ? ORDER BY id", (topic_id,)).fetchall()


def get_recent_attempts(conn: sqlite3.Connection, kid_profile_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT quiz_attempts.*, topics.topic_name, topics.chapter_name
        FROM quiz_attempts
        JOIN topics ON topics.id = quiz_attempts.topic_id
        WHERE quiz_attempts.kid_profile_id = ?
        ORDER BY quiz_attempts.started_at DESC
        LIMIT 10
        """,
        (kid_profile_id,),
    ).fetchall()


def recommend_next_skill(current_skill: int, accuracy: float, hint_rate: float) -> int:
    if accuracy >= 0.8 and hint_rate < 0.5:
        return min(5, current_skill + 1)
    if accuracy < 0.5 or hint_rate > 0.75:
        return max(1, current_skill - 1)
    return current_skill


def _recent_question_ids(conn: sqlite3.Connection, kid_profile_id: int, topic_id: int) -> set[int]:
    rows = conn.execute(
        """
        SELECT answer_records.question_id
        FROM answer_records
        JOIN quiz_attempts ON quiz_attempts.id = answer_records.attempt_id
        WHERE quiz_attempts.kid_profile_id = ? AND quiz_attempts.topic_id = ?
        ORDER BY answer_records.answered_at DESC
        LIMIT 12
        """,
        (kid_profile_id, topic_id),
    ).fetchall()
    return {row["question_id"] for row in rows}


def choose_questions_for_attempt(conn: sqlite3.Connection, kid_profile_id: int, topic_id: int, skill_level: int, batch_size: int = 3) -> list[sqlite3.Row]:
    recent_ids = _recent_question_ids(conn, kid_profile_id, topic_id)
    rows = conn.execute(
        """
        SELECT questions.*, concepts.concept_title, concepts.explanation AS concept_explanation, concepts.example_text
        FROM questions
        JOIN concepts ON concepts.id = questions.concept_id
        JOIN topics ON topics.id = concepts.topic_id
        WHERE concepts.topic_id = ? AND topics.review_status = 'approved' AND questions.active = 1 AND questions.review_status = 'approved'
        ORDER BY ABS(questions.difficulty_level - ?) ASC, questions.id ASC
        """,
        (topic_id, skill_level),
    ).fetchall()
    fresh = [row for row in rows if row["id"] not in recent_ids]
    selected = fresh[:batch_size]
    if len(selected) < batch_size:
        already = {row["id"] for row in selected}
        selected.extend([row for row in rows if row["id"] not in already][: batch_size - len(selected)])
    return selected


def start_quiz_attempt(conn: sqlite3.Connection, kid_profile_id: int, topic_id: int) -> int:
    kid = get_kid_profile(conn, kid_profile_id)
    if not kid:
        raise ValueError("Kid profile not found.")
    skill_level = kid["current_skill_level"]
    now = utcnow()
    attempt_id = conn.execute(
        """
        INSERT INTO quiz_attempts (kid_profile_id, topic_id, requested_skill_level, started_at, completed_at, score)
        VALUES (?, ?, ?, ?, NULL, 0)
        """,
        (kid_profile_id, topic_id, skill_level, now),
    ).lastrowid
    conn.commit()
    return attempt_id


def get_attempt(conn: sqlite3.Connection, attempt_id: int, kid_profile_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM quiz_attempts WHERE id = ? AND kid_profile_id = ?",
        (attempt_id, kid_profile_id),
    ).fetchone()


def get_attempt_progress(conn: sqlite3.Connection, attempt_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT answer_records.*, questions.prompt, questions.correct_choice
        FROM answer_records
        JOIN questions ON questions.id = answer_records.question_id
        WHERE answer_records.attempt_id = ?
        ORDER BY answer_records.id
        """,
        (attempt_id,),
    ).fetchall()


def next_question_for_attempt(conn: sqlite3.Connection, attempt_id: int) -> sqlite3.Row | None:
    attempt = conn.execute("SELECT * FROM quiz_attempts WHERE id = ?", (attempt_id,)).fetchone()
    if not attempt:
        return None
    all_questions = choose_questions_for_attempt(conn, attempt["kid_profile_id"], attempt["topic_id"], attempt["requested_skill_level"])
    answered = {row["question_id"] for row in get_attempt_progress(conn, attempt_id)}
    for question in all_questions:
        if question["id"] not in answered:
            return question
    return None


@dataclass
class AnswerResult:
    is_correct: bool
    feedback_text: str
    correct_choice: str
    explanation: str


def grade_answer(question: sqlite3.Row, selected_choice: str) -> AnswerResult:
    selected_choice = selected_choice.upper().strip()
    is_correct = selected_choice == question["correct_choice"]
    if is_correct:
        feedback = "Correct. You matched the concept accurately."
    else:
        correct_text = question[f"choice_{question['correct_choice'].lower()}"]
        if selected_choice in {"A", "B", "C", "D"}:
            chosen_text = question[f"choice_{selected_choice.lower()}"]
        else:
            chosen_text = "an invalid choice"
        feedback = (
            f"Not quite. You chose '{chosen_text}', but the best answer is '{correct_text}'. "
            f"{question['explanation']}"
        )
    return AnswerResult(is_correct, feedback, question["correct_choice"], question["explanation"])


def record_answer(conn: sqlite3.Connection, attempt_id: int, question_id: int, selected_choice: str, used_hint: bool) -> AnswerResult:
    question = conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
    if not question:
        raise ValueError("Question not found.")
    result = grade_answer(question, selected_choice)
    conn.execute(
        """
        INSERT INTO answer_records (attempt_id, question_id, selected_choice, is_correct, used_hint, feedback_text, answered_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (attempt_id, question_id, selected_choice.upper(), int(result.is_correct), int(used_hint), result.feedback_text, utcnow()),
    )
    conn.commit()
    maybe_complete_attempt(conn, attempt_id)
    return result


def maybe_complete_attempt(conn: sqlite3.Connection, attempt_id: int) -> None:
    attempt = conn.execute("SELECT * FROM quiz_attempts WHERE id = ?", (attempt_id,)).fetchone()
    if not attempt or attempt["completed_at"]:
        return
    answers = get_attempt_progress(conn, attempt_id)
    target_count = 3
    if len(answers) < target_count:
        return
    correct_count = sum(row["is_correct"] for row in answers)
    hint_rate = sum(row["used_hint"] for row in answers) / max(1, len(answers))
    accuracy = correct_count / max(1, len(answers))
    score = round(accuracy * 100, 1)
    completed_at = utcnow()
    conn.execute(
        """
        UPDATE quiz_attempts
        SET completed_at = ?, score = ?
        WHERE id = ?
        """,
        (completed_at, score, attempt_id),
    )
    next_skill = recommend_next_skill(attempt["requested_skill_level"], accuracy, hint_rate)
    conn.execute(
        "UPDATE kid_profiles SET current_skill_level = ? WHERE id = ?",
        (next_skill, attempt["kid_profile_id"]),
    )
    update_mastery(conn, attempt["kid_profile_id"], attempt["topic_id"])
    conn.commit()


def update_mastery(conn: sqlite3.Connection, kid_profile_id: int, topic_id: int) -> None:
    attempts = conn.execute(
        """
        SELECT score
        FROM quiz_attempts
        WHERE kid_profile_id = ? AND topic_id = ? AND completed_at IS NOT NULL
        ORDER BY started_at DESC
        LIMIT 5
        """,
        (kid_profile_id, topic_id),
    ).fetchall()
    if not attempts:
        return
    mastery = round(sum(row["score"] for row in attempts) / len(attempts), 1)
    topic = get_topic(conn, topic_id)
    now = utcnow()
    conn.execute(
        """
        INSERT INTO mastery_scores (kid_profile_id, topic_id, chapter_name, mastery_percent, attempts_count, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(kid_profile_id, topic_id)
        DO UPDATE SET
            chapter_name = excluded.chapter_name,
            mastery_percent = excluded.mastery_percent,
            attempts_count = excluded.attempts_count,
            updated_at = excluded.updated_at
        """,
        (kid_profile_id, topic_id, topic["chapter_name"], mastery, len(attempts), now),
    )


def get_mastery_rows_for_parent(conn: sqlite3.Connection, parent_user_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT kid_profiles.display_name AS kid_name,
               topics.chapter_name,
               topics.topic_name,
               COALESCE(mastery_scores.mastery_percent, 0) AS mastery_percent,
               COALESCE(mastery_scores.attempts_count, 0) AS attempts_count
        FROM kid_profiles
        JOIN topics
        LEFT JOIN mastery_scores
          ON mastery_scores.kid_profile_id = kid_profiles.id
         AND mastery_scores.topic_id = topics.id
        WHERE kid_profiles.parent_user_id = ?
        ORDER BY kid_profiles.display_name, topics.chapter_name, topics.topic_name
        """,
        (parent_user_id,),
    ).fetchall()


def summarize_generation_risk(material_row: sqlite3.Row) -> str:
    if material_row["generation_status"] == "generated" and material_row["quality_score"] >= 0.75:
        return "Low"
    if material_row["generation_status"] == "generated":
        return "Medium"
    return "High"
