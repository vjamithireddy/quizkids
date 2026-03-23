import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quizkid import services
from quizkid.db import init_db
from quizkid.services import (
    choose_questions_for_attempt,
    create_initial_admin,
    create_kid_profile,
    create_material,
    delete_material,
    get_attempt_progress,
    get_material,
    has_admin_account,
    list_topics_for_kid,
    maybe_complete_attempt,
    record_answer,
    register_parent_account,
    regenerate_material,
    recommend_next_skill,
    set_question_review_status,
    set_topic_review_status,
    seed_demo_data,
    start_quiz_attempt,
)


class QuizKidServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)
        seed_demo_data(self.conn)
        self.kid_id = self.conn.execute("SELECT id FROM kid_profiles LIMIT 1").fetchone()[0]
        self.topic_id = self.conn.execute("SELECT id FROM topics LIMIT 1").fetchone()[0]

    def tearDown(self) -> None:
        self.conn.close()

    def test_recommend_next_skill_increases_for_strong_performance(self) -> None:
        self.assertEqual(recommend_next_skill(2, 0.9, 0.0), 3)

    def test_recommend_next_skill_decreases_for_low_accuracy(self) -> None:
        self.assertEqual(recommend_next_skill(3, 0.4, 0.2), 2)

    def test_attempt_completion_updates_progress(self) -> None:
        attempt_id = start_quiz_attempt(self.conn, self.kid_id, self.topic_id)
        questions = choose_questions_for_attempt(self.conn, self.kid_id, self.topic_id, 2)
        for question in questions:
            record_answer(self.conn, attempt_id, question["id"], question["correct_choice"], used_hint=False)
        attempt = self.conn.execute("SELECT * FROM quiz_attempts WHERE id = ?", (attempt_id,)).fetchone()
        kid = self.conn.execute("SELECT * FROM kid_profiles WHERE id = ?", (self.kid_id,)).fetchone()
        mastery = self.conn.execute("SELECT * FROM mastery_scores WHERE kid_profile_id = ?", (self.kid_id,)).fetchone()
        self.assertIsNotNone(attempt["completed_at"])
        self.assertEqual(attempt["score"], 100.0)
        self.assertGreaterEqual(kid["current_skill_level"], 3)
        self.assertIsNotNone(mastery)

    def test_retake_prefers_new_questions(self) -> None:
        first_attempt = start_quiz_attempt(self.conn, self.kid_id, self.topic_id)
        first_batch = choose_questions_for_attempt(self.conn, self.kid_id, self.topic_id, 2)
        for question in first_batch:
            record_answer(self.conn, first_attempt, question["id"], question["correct_choice"], used_hint=False)
        second_batch = choose_questions_for_attempt(self.conn, self.kid_id, self.topic_id, 2)
        first_ids = {question["id"] for question in first_batch}
        second_ids = {question["id"] for question in second_batch}
        self.assertTrue(second_ids - first_ids)

    def test_material_upload_generates_topic(self) -> None:
        ok, notes = create_material(
            self.conn,
            1,
            "Plants Intro",
            "plants.txt",
            "text/plain",
            b"Plants need sunlight.\nRoots help absorb water.\nLeaves make food.\nStems support the plant.",
        )
        topic_count = self.conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
        self.assertTrue(ok)
        self.assertTrue(notes)
        self.assertGreaterEqual(topic_count, 2)


class QuizKidProductionSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_upload_dir = services.UPLOAD_DIR
        services.UPLOAD_DIR = Path(self.tempdir.name)
        admin_user, _ = create_initial_admin(self.conn, "owner@example.com", "supersecure1", "Owner")
        parent_user, _ = register_parent_account(self.conn, "parent@example.com", "parentpass1", "Parent One")
        create_kid_profile(self.conn, parent_user["id"], "Maya", "Ages 8-10", 2)
        self.admin_id = admin_user["id"]
        self.kid_id = self.conn.execute("SELECT id FROM kid_profiles LIMIT 1").fetchone()[0]

    def tearDown(self) -> None:
        services.UPLOAD_DIR = self.original_upload_dir
        self.tempdir.cleanup()
        self.conn.close()

    def test_initial_admin_can_only_be_created_once(self) -> None:
        second_admin, second_errors = create_initial_admin(self.conn, "again@example.com", "supersecure1", "Again")
        self.assertIsNone(second_admin)
        self.assertIn("already complete", second_errors[0])

    def test_parent_registration_creates_real_account(self) -> None:
        parent_user, errors = register_parent_account(self.conn, "parent2@example.com", "parentpass1", "Parent Two")
        self.assertIsNotNone(parent_user)
        self.assertEqual(errors, [])
        stored = self.conn.execute("SELECT * FROM users WHERE email = 'parent2@example.com'").fetchone()
        self.assertEqual(stored["role"], "parent")

    def test_material_upload_is_persisted_to_disk(self) -> None:
        ok, _ = create_material(
            self.conn,
            self.admin_id,
            "Animals Intro",
            "animals.txt",
            "text/plain",
            b"Mammals feed milk.\nBirds have feathers.\nFish live in water.",
        )
        material = self.conn.execute("SELECT * FROM course_materials ORDER BY id DESC LIMIT 1").fetchone()
        self.assertTrue(ok)
        self.assertTrue(Path(material["stored_file_path"]).exists())
        self.assertEqual(material["stored_file_size"], len(b"Mammals feed milk.\nBirds have feathers.\nFish live in water."))

    def test_pdf_text_extraction_uses_pdf_reader_when_available(self) -> None:
        class FakePage:
            def __init__(self, text: str) -> None:
                self._text = text

            def extract_text(self) -> str:
                return self._text

        class FakeReader:
            def __init__(self, stream) -> None:
                self.pages = [FakePage("Fractions are parts of a whole."), FakePage("Equivalent fractions have the same value.")]

        with patch("quizkid.services.load_pdf_reader_class", return_value=FakeReader):
            extracted, status = services.extract_source_text("fractions.pdf", "application/pdf", b"%PDF-fake")

        self.assertEqual(status, "extracted")
        self.assertIn("Fractions are parts of a whole.", extracted)
        self.assertIn("Equivalent fractions have the same value.", extracted)

    def test_pdf_without_reader_dependency_falls_back_to_stored_status(self) -> None:
        with patch("quizkid.services.load_pdf_reader_class", side_effect=ModuleNotFoundError):
            extracted, status = services.extract_source_text("fractions.pdf", "application/pdf", b"%PDF-fake")

        self.assertEqual(status, "stored")
        self.assertIn("dependency is not installed", extracted)

    def test_delete_material_removes_file_and_row(self) -> None:
        create_material(
            self.conn,
            self.admin_id,
            "Animals Intro",
            "animals.txt",
            "text/plain",
            b"Mammals feed milk.\nBirds have feathers.\nFish live in water.",
        )
        material = self.conn.execute("SELECT * FROM course_materials ORDER BY id DESC LIMIT 1").fetchone()
        stored_path = Path(material["stored_file_path"])
        self.assertTrue(stored_path.exists())
        ok, _ = delete_material(self.conn, material["id"], self.admin_id)
        self.assertTrue(ok)
        self.assertFalse(stored_path.exists())
        self.assertIsNone(get_material(self.conn, material["id"]))

    def test_regenerate_material_rebuilds_topics(self) -> None:
        create_material(
            self.conn,
            self.admin_id,
            "Plants Intro",
            "plants.txt",
            "text/plain",
            b"Plants need sunlight.\nRoots absorb water.\nLeaves make food.\nStems support the plant.",
        )
        material = self.conn.execute("SELECT * FROM course_materials ORDER BY id DESC LIMIT 1").fetchone()
        self.conn.execute("DELETE FROM topics WHERE material_id = ?", (material["id"],))
        self.conn.commit()
        ok, note = regenerate_material(self.conn, material["id"], self.admin_id)
        topic_count = self.conn.execute("SELECT COUNT(*) FROM topics WHERE material_id = ?", (material["id"],)).fetchone()[0]
        self.assertTrue(ok)
        self.assertIn("Generated automatically", note)
        self.assertGreater(topic_count, 0)

    def test_approved_topics_are_only_ones_shown_to_kids(self) -> None:
        create_material(
            self.conn,
            self.admin_id,
            "Plants Intro",
            "plants.txt",
            "text/plain",
            b"Plants need sunlight.\nRoots absorb water.\nLeaves make food.\nStems support the plant.",
        )
        topic = self.conn.execute("SELECT * FROM topics ORDER BY id DESC LIMIT 1").fetchone()
        topics_before = list_topics_for_kid(self.conn, self.kid_id)
        self.assertFalse(any(row["id"] == topic["id"] for row in topics_before))
        ok, _ = set_topic_review_status(self.conn, topic["id"], "approved", self.admin_id)
        self.assertTrue(ok)
        topics_after = list_topics_for_kid(self.conn, self.kid_id)
        self.assertTrue(any(row["id"] == topic["id"] for row in topics_after))

    def test_question_must_be_approved_for_quiz_selection(self) -> None:
        create_material(
            self.conn,
            self.admin_id,
            "Shapes Intro",
            "shapes.txt",
            "text/plain",
            b"Triangles have three sides.\nSquares have four equal sides.\nCircles have no corners.\nRectangles have four sides.",
        )
        topic_id = self.conn.execute("SELECT id FROM topics ORDER BY id DESC LIMIT 1").fetchone()[0]
        attempt_id = start_quiz_attempt(self.conn, self.kid_id, topic_id)
        self.conn.execute("UPDATE topics SET review_status = 'approved' WHERE id = ?", (topic_id,))
        self.conn.execute(
            """
            UPDATE questions
            SET review_status = 'rejected', active = 0
            WHERE concept_id IN (SELECT id FROM concepts WHERE topic_id = ?)
            """,
            (topic_id,),
        )
        self.conn.commit()
        self.assertEqual(choose_questions_for_attempt(self.conn, self.kid_id, topic_id, 2), [])
        question_id = self.conn.execute(
            """
            SELECT questions.id
            FROM questions
            JOIN concepts ON concepts.id = questions.concept_id
            WHERE concepts.topic_id = ?
            LIMIT 1
            """,
            (topic_id,),
        ).fetchone()[0]
        ok, _ = set_question_review_status(self.conn, question_id, "approved", self.admin_id)
        self.assertTrue(ok)
        selected = choose_questions_for_attempt(self.conn, self.kid_id, topic_id, 2)
        self.assertEqual(len(selected), 1)


if __name__ == "__main__":
    unittest.main()
