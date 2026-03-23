import sqlite3
import unittest

from quizkid.db import init_db
from quizkid.services import (
    choose_questions_for_attempt,
    create_material,
    get_attempt_progress,
    list_topics_for_kid,
    maybe_complete_attempt,
    record_answer,
    recommend_next_skill,
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
        topics = list_topics_for_kid(self.conn, self.kid_id)
        self.assertTrue(ok)
        self.assertTrue(notes)
        self.assertGreaterEqual(len(topics), 2)


if __name__ == "__main__":
    unittest.main()
