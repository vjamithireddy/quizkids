from __future__ import annotations

import html
import os
import sqlite3
from http import cookies
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from .config import COOKIE_SECURE, SEED_DEMO_DATA
from .db import get_connection, init_db
from .security import SESSION_COOKIE, make_session_token, read_session_token
from .services import (
    authenticate_user,
    create_initial_admin,
    create_kid_profile,
    create_material,
    create_session,
    destroy_session,
    get_attempt,
    get_attempt_progress,
    get_kid_profile,
    get_mastery_rows_for_parent,
    get_recent_attempts,
    get_session_user,
    get_topic,
    get_topic_concepts,
    has_admin_account,
    list_materials,
    list_parent_kids,
    list_topics_for_kid,
    next_question_for_attempt,
    record_answer,
    register_parent_account,
    seed_demo_data,
    start_quiz_attempt,
    summarize_generation_risk,
)


STYLE = """
:root {
  --bg: #f7f2e8;
  --panel: rgba(255,255,255,0.9);
  --ink: #20252a;
  --muted: #55616d;
  --accent: #d86c40;
  --accent-2: #1f7a8c;
  --line: #d7cab4;
  --success: #2f855a;
  --warn: #c05621;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: Georgia, "Avenir Next", serif;
  color: var(--ink);
  background:
    radial-gradient(circle at top left, rgba(216,108,64,0.25), transparent 32%),
    radial-gradient(circle at bottom right, rgba(31,122,140,0.25), transparent 30%),
    linear-gradient(180deg, #fcf8ef, #f1ead9);
}
a { color: var(--accent-2); text-decoration: none; }
a:hover { text-decoration: underline; }
.shell { max-width: 1100px; margin: 0 auto; padding: 24px; }
.hero {
  display: grid;
  gap: 18px;
  padding: 28px;
  border-radius: 28px;
  background: linear-gradient(135deg, rgba(255,255,255,0.96), rgba(255,246,230,0.82));
  border: 1px solid rgba(215,202,180,0.9);
  box-shadow: 0 18px 50px rgba(52, 45, 28, 0.08);
}
.grid {
  display: grid;
  gap: 18px;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  margin-top: 18px;
}
.panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 20px;
  padding: 20px;
  box-shadow: 0 10px 30px rgba(40, 29, 12, 0.06);
}
.tag, .badge {
  display: inline-block;
  border-radius: 999px;
  padding: 6px 12px;
  font-size: 0.84rem;
  background: rgba(31,122,140,0.12);
  color: var(--accent-2);
}
.badge.warn { background: rgba(192,86,33,0.12); color: var(--warn); }
.badge.success { background: rgba(47,133,90,0.12); color: var(--success); }
h1, h2, h3 { margin: 0 0 12px; }
h1 { font-size: clamp(2rem, 4vw, 3.2rem); line-height: 1.05; }
p { color: var(--muted); line-height: 1.55; }
form { display: grid; gap: 12px; }
label { display: grid; gap: 6px; font-weight: 600; }
input, select, button, textarea {
  font: inherit;
  padding: 11px 12px;
  border-radius: 12px;
  border: 1px solid var(--line);
}
button, .button {
  background: var(--accent);
  color: white;
  border: none;
  cursor: pointer;
  display: inline-block;
}
button.secondary, .button.secondary {
  background: var(--accent-2);
}
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 10px 6px; border-bottom: 1px solid var(--line); }
.row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.flash { padding: 12px 14px; border-radius: 14px; background: rgba(31,122,140,0.1); color: var(--accent-2); }
.flash.error { background: rgba(192,86,33,0.1); color: var(--warn); }
.choice-list { display: grid; gap: 10px; margin-top: 14px; }
.choice {
  padding: 12px;
  border-radius: 14px;
  border: 1px solid var(--line);
  background: rgba(255,255,255,0.92);
}
.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}
.muted { color: var(--muted); }
details {
  border: 1px dashed var(--line);
  border-radius: 14px;
  padding: 12px 14px;
}
@media (max-width: 720px) {
  .shell { padding: 14px; }
  .hero, .panel { padding: 16px; border-radius: 18px; }
}
"""


class Request:
    def __init__(self, environ: dict):
        self.environ = environ
        self.method = environ["REQUEST_METHOD"].upper()
        self.path = environ.get("PATH_INFO", "/")
        self.query = {k: v[0] for k, v in parse_qs(environ.get("QUERY_STRING", "")).items()}
        self.cookies = cookies.SimpleCookie(environ.get("HTTP_COOKIE", ""))
        self._form: dict[str, str] | None = None
        self._files: dict[str, dict] | None = None

    def form(self) -> tuple[dict[str, str], dict[str, dict]]:
        if self._form is not None and self._files is not None:
            return self._form, self._files

        content_type = self.environ.get("CONTENT_TYPE", "")
        body = self.body_bytes()
        if content_type.startswith("multipart/form-data"):
            form, files = parse_multipart_form(content_type, body)
        else:
            parsed = parse_qs(body.decode("utf-8", errors="ignore"))
            form = {k: v[0] for k, v in parsed.items()}
            files = {}
        self._form = form
        self._files = files
        return form, files

    def body_bytes(self) -> bytes:
        length = int(self.environ.get("CONTENT_LENGTH") or 0)
        if length <= 0:
            return b""
        return self.environ["wsgi.input"].read(length)


def parse_multipart_form(content_type: str, body: bytes) -> tuple[dict[str, str], dict[str, dict]]:
    boundary_marker = "boundary="
    if boundary_marker not in content_type:
        return {}, {}
    boundary = content_type.split(boundary_marker, 1)[1].encode("utf-8")
    parts = body.split(b"--" + boundary)
    form: dict[str, str] = {}
    files: dict[str, dict] = {}
    for part in parts:
        part = part.strip()
        if not part or part == b"--":
            continue
        headers_blob, _, payload = part.partition(b"\r\n\r\n")
        if not payload:
            continue
        payload = payload.rstrip(b"\r\n")
        header_lines = headers_blob.decode("utf-8", errors="ignore").split("\r\n")
        headers = {}
        for line in header_lines:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        disposition = headers.get("content-disposition", "")
        attrs: dict[str, str] = {}
        for token in disposition.split(";"):
            if "=" in token:
                key, value = token.strip().split("=", 1)
                attrs[key] = value.strip('"')
        name = attrs.get("name")
        if not name:
            continue
        filename = attrs.get("filename")
        if filename:
            files[name] = {
                "filename": Path(filename).name,
                "content_type": headers.get("content-type", "application/octet-stream"),
                "content": payload,
            }
        else:
            form[name] = payload.decode("utf-8", errors="ignore")
    return form, files


def html_escape(value: object) -> str:
    return html.escape("" if value is None else str(value))


def page(title: str, body: str, user: sqlite3.Row | None = None) -> bytes:
    auth_block = ""
    if user:
        auth_block = (
            f"<div class='row'><span class='tag'>{html_escape(user['display_name'])}</span>"
            "<form method='post' action='/logout'><button class='secondary' type='submit'>Log Out</button></form></div>"
        )
    markup = f"""
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>{html_escape(title)}</title>
      <style>{STYLE}</style>
    </head>
    <body>
      <div class="shell">
        <div class="topbar">
          <a href="/"><strong>QuizKid</strong></a>
          {auth_block}
        </div>
        {body}
      </div>
    </body>
    </html>
    """
    return markup.encode("utf-8")


def session_cookie_header(token: str | None = None, *, clear: bool = False) -> tuple[str, str]:
    parts = [f"{SESSION_COOKIE}={'' if clear else token or ''}", "HttpOnly", "Path=/", "SameSite=Lax"]
    if COOKIE_SECURE:
        parts.append("Secure")
    if clear:
        parts.append("Max-Age=0")
    return ("Set-Cookie", "; ".join(parts))


def redirect(start_response: Callable, location: str, headers: list[tuple[str, str]] | None = None) -> list[bytes]:
    response_headers = [("Location", location)]
    if headers:
        response_headers.extend(headers)
    start_response("302 Found", response_headers)
    return [b""]


def response(start_response: Callable, title: str, body: str, user: sqlite3.Row | None = None, status: str = "200 OK", headers: list[tuple[str, str]] | None = None) -> list[bytes]:
    response_headers = [("Content-Type", "text/html; charset=utf-8")]
    if headers:
        response_headers.extend(headers)
    start_response(status, response_headers)
    return [page(title, body, user)]


def current_user(conn: sqlite3.Connection, request: Request) -> sqlite3.Row | None:
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return None
    session_id = read_session_token(cookie.value)
    if not session_id:
        return None
    return get_session_user(conn, session_id)


def require_user(conn: sqlite3.Connection, request: Request, role: str | None = None) -> sqlite3.Row | None:
    user = current_user(conn, request)
    if not user:
        return None
    if role and user["role"] != role:
        return None
    return user


def landing_view(request: Request, admin_ready: bool, flash: str = "", errors: list[str] | None = None, signup_errors: list[str] | None = None) -> tuple[str, str]:
    flash_html = f"<div class='flash'>{html_escape(flash)}</div>" if flash else ""
    error_html = "".join(f"<div class='flash error'>{html_escape(err)}</div>" for err in (errors or []))
    signup_error_html = "".join(f"<div class='flash error'>{html_escape(err)}</div>" for err in (signup_errors or []))
    if not admin_ready:
        body = f"""
        <section class="hero">
          <span class="tag">First-run setup</span>
          <h1>Create the first QuizKid admin account.</h1>
          <p>QuizKid no longer boots with demo credentials in production mode. Create the initial admin account to unlock the admin console and invite real parents.</p>
          {flash_html}
          {error_html}
          <div class="grid">
            <div class="panel">
              <h2>Admin Setup</h2>
              <form method="post" action="/setup/admin">
                <label>Display Name <input name="display_name" required></label>
                <label>Email <input name="email" type="email" required></label>
                <label>Password <input name="password" type="password" minlength="10" required></label>
                <button type="submit">Create Admin</button>
              </form>
            </div>
            <div class="panel">
              <h2>What happens next</h2>
              <p>The first admin can sign in immediately, upload course material, and onboard real parent accounts.</p>
            </div>
          </div>
        </section>
        """
        return "QuizKid Setup", body
    body = f"""
    <section class="hero">
      <span class="tag">Adaptive learning on a single VPS</span>
      <h1>QuizKid turns study material into guided quiz adventures.</h1>
      <p>Admins upload source material, parents manage child profiles, and kids learn through hints, explanations, retries, and topic mastery.</p>
      {flash_html}
      {error_html}
      <div class="grid">
        <div class="panel">
          <h2>Sign In</h2>
          <form method="post" action="/login">
            <label>Email <input name="email" type="email" required></label>
            <label>Password <input name="password" type="password" required></label>
            <button type="submit">Continue</button>
          </form>
        </div>
        <div class="panel">
          <h2>Parent Registration</h2>
          {signup_error_html}
          <form method="post" action="/register/parent">
            <label>Display Name <input name="display_name" required></label>
            <label>Email <input name="email" type="email" required></label>
            <label>Password <input name="password" type="password" minlength="10" required></label>
            <button type="submit">Create Parent Account</button>
          </form>
          <p class="muted">Parents can register themselves, then create kid profiles and track topic mastery.</p>
        </div>
      </div>
    </section>
    """
    return "QuizKid", body


def admin_dashboard(conn: sqlite3.Connection, user: sqlite3.Row, flash: str = "", errors: list[str] | None = None) -> tuple[str, str]:
    materials = list_materials(conn)
    flash_html = f"<div class='flash'>{html_escape(flash)}</div>" if flash else ""
    error_html = "".join(f"<div class='flash error'>{html_escape(err)}</div>" for err in (errors or []))
    rows = []
    for material in materials:
        rows.append(
            "<tr>"
            f"<td>{html_escape(material['title'])}</td>"
            f"<td>{html_escape(material['filename'])}</td>"
            f"<td>{html_escape(material['stored_filename'] or '-')}</td>"
            f"<td>{html_escape(material['generation_status'])}</td>"
            f"<td>{material['quality_score']:.2f}</td>"
            f"<td>{html_escape(summarize_generation_risk(material))}</td>"
            f"<td>{html_escape(material['validation_notes'])}</td>"
            "</tr>"
        )
    body = f"""
    <section class="hero">
      <span class="tag">Admin Console</span>
      <h1>Content pipeline and generation overview</h1>
      <p>Upload materials, let QuizKid generate topics and concepts, and keep an eye on content quality and generation risk.</p>
      {flash_html}
      {error_html}
    </section>
    <div class="grid">
      <section class="panel">
        <h2>Upload Material</h2>
        <form method="post" action="/admin/upload" enctype="multipart/form-data">
          <label>Title <input name="title" required></label>
          <label>Course File <input name="material" type="file" required></label>
          <button type="submit">Upload and Generate</button>
        </form>
        <p class="muted">The starter app extracts plain text directly. PDF/DOC files are stored and given placeholder extraction notes until external parsers are added.</p>
      </section>
      <section class="panel">
        <h2>Generated Materials</h2>
        <table>
          <thead><tr><th>Title</th><th>Original File</th><th>Stored File</th><th>Status</th><th>Quality</th><th>Risk</th><th>Notes</th></tr></thead>
          <tbody>{''.join(rows) or '<tr><td colspan=\"7\">No materials uploaded yet.</td></tr>'}</tbody>
        </table>
      </section>
    </div>
    """
    return "Admin Dashboard", body


def parent_dashboard(conn: sqlite3.Connection, user: sqlite3.Row, flash: str = "", errors: list[str] | None = None) -> tuple[str, str]:
    kids = list_parent_kids(conn, user["id"])
    mastery_rows = get_mastery_rows_for_parent(conn, user["id"])
    flash_html = f"<div class='flash'>{html_escape(flash)}</div>" if flash else ""
    error_html = "".join(f"<div class='flash error'>{html_escape(err)}</div>" for err in (errors or []))
    kid_cards = []
    for kid in kids:
        attempts = get_recent_attempts(conn, kid["id"])
        attempt_list = "".join(
            f"<li>{html_escape(row['topic_name'])}: {row['score']:.0f}% on {html_escape(row['started_at'][:10])}</li>"
            for row in attempts[:3]
        ) or "<li>No attempts yet.</li>"
        kid_cards.append(
            "<div class='panel'>"
            f"<h2>{html_escape(kid['display_name'])}</h2>"
            f"<p>Age band: {html_escape(kid['age_band'])}<br>Current skill level: {kid['current_skill_level']}</p>"
            f"<div class='row'><a class='button secondary' href='/kid/{kid['id']}'>Launch Kid Mode</a></div>"
            f"<h3>Recent Attempts</h3><ul>{attempt_list}</ul>"
            "</div>"
        )
    mastery_html = "".join(
        "<tr>"
        f"<td>{html_escape(row['kid_name'])}</td>"
        f"<td>{html_escape(row['chapter_name'])}</td>"
        f"<td>{html_escape(row['topic_name'])}</td>"
        f"<td>{row['mastery_percent']:.1f}%</td>"
        f"<td>{row['attempts_count']}</td>"
        "</tr>"
        for row in mastery_rows
    )
    body = f"""
    <section class="hero">
      <span class="tag">Parent Dashboard</span>
      <h1>Manage learner profiles and monitor mastery.</h1>
      <p>Pick a child’s starting level, watch topic progress, and send them back through retakes when a concept needs reinforcement.</p>
      {flash_html}
      {error_html}
    </section>
    <div class="grid">
      <section class="panel">
        <h2>Add Kid Profile</h2>
        <form method="post" action="/parent/create-kid">
          <label>Name <input name="display_name" required></label>
          <label>Age Band
            <select name="age_band">
              <option>Ages 5-7</option>
              <option selected>Ages 8-10</option>
              <option>Ages 11-13</option>
              <option>Ages 14+</option>
            </select>
          </label>
          <label>Starting Skill Level
            <select name="start_skill_level">
              <option value="1">1 - beginner</option>
              <option value="2" selected>2 - early learner</option>
              <option value="3">3 - steady</option>
              <option value="4">4 - advanced</option>
              <option value="5">5 - challenge</option>
            </select>
          </label>
          <button type="submit">Create Profile</button>
        </form>
      </section>
      <section class="panel">
        <h2>Topic Mastery</h2>
        <table>
          <thead><tr><th>Kid</th><th>Chapter</th><th>Topic</th><th>Mastery</th><th>Attempts</th></tr></thead>
          <tbody>{mastery_html or '<tr><td colspan=\"5\">Mastery data will appear after the first quiz attempt.</td></tr>'}</tbody>
        </table>
      </section>
    </div>
    <div class="grid">{''.join(kid_cards) or '<section class=\"panel\"><p>No kid profiles yet.</p></section>'}</div>
    """
    return "Parent Dashboard", body


def kid_dashboard(conn: sqlite3.Connection, kid: sqlite3.Row, flash: str = "") -> tuple[str, str]:
    topics = list_topics_for_kid(conn, kid["id"])
    topic_cards = []
    for topic in topics:
        topic_cards.append(
            "<div class='panel'>"
            f"<span class='badge'>{html_escape(topic['subject_name'])}</span>"
            f"<h2>{html_escape(topic['topic_name'])}</h2>"
            f"<p>{html_escape(topic['summary'])}</p>"
            f"<p>Mastery: {topic['mastery_percent']:.1f}%<br>Attempts: {topic['attempts_count']}</p>"
            f"<a class='button' href='/kid/{kid['id']}/topic/{topic['id']}'>Start Topic Run</a>"
            "</div>"
        )
    flash_html = f"<div class='flash'>{html_escape(flash)}</div>" if flash else ""
    body = f"""
    <section class="hero">
      <span class="tag">Kid Mode</span>
      <h1>{html_escape(kid['display_name'])}'s learning paths</h1>
      <p>Pick a topic, use hints when needed, and build mastery by working through short puzzle runs.</p>
      {flash_html}
    </section>
    <div class="grid">{''.join(topic_cards)}</div>
    """
    return f"{kid['display_name']} Topics", body


def kid_topic_view(conn: sqlite3.Connection, kid: sqlite3.Row, topic_id: int, flash: str = "") -> tuple[str, str]:
    topic = get_topic(conn, topic_id)
    attempt_id_raw = flash and ""
    attempt_id = None
    concepts = get_topic_concepts(conn, topic_id)
    if not topic:
        return "Topic Not Found", "<section class='panel'><p>Topic not found.</p></section>"
    attempt_id_query = None
    flash_html = f"<div class='flash'>{html_escape(flash)}</div>" if flash else ""
    topic_body = "".join(
        "<details>"
        f"<summary>{html_escape(concept['concept_title'])}</summary>"
        f"<p>{html_escape(concept['explanation'])}</p>"
        f"<p><strong>Example:</strong> {html_escape(concept['example_text'])}</p>"
        "</details>"
        for concept in concepts
    )
    body = f"""
    <section class="hero">
      <span class="tag">{html_escape(topic['chapter_name'])}</span>
      <h1>{html_escape(topic['topic_name'])}</h1>
      <p>{html_escape(topic['summary'])}</p>
      {flash_html}
      <div class="row">
        <a class="button" href="/kid/{kid['id']}/topic/{topic_id}/begin">Begin Quiz Run</a>
        <a class="button secondary" href="/kid/{kid['id']}">Back to Topics</a>
      </div>
    </section>
    <div class="grid">
      <section class="panel">
        <h2>Concept Review</h2>
        {topic_body}
      </section>
    </div>
    """
    return f"{topic['topic_name']}", body


def quiz_run_view(conn: sqlite3.Connection, kid: sqlite3.Row, attempt: sqlite3.Row, flash: str = "", feedback: str = "") -> tuple[str, str]:
    topic = get_topic(conn, attempt["topic_id"])
    question = next_question_for_attempt(conn, attempt["id"])
    flash_html = f"<div class='flash'>{html_escape(flash)}</div>" if flash else ""
    feedback_html = f"<div class='flash'>{html_escape(feedback)}</div>" if feedback else ""
    if not question:
        progress = get_attempt_progress(conn, attempt["id"])
        correct = sum(row["is_correct"] for row in progress)
        body = f"""
        <section class="hero">
          <span class="tag">Run Complete</span>
          <h1>{html_escape(topic['topic_name'])}</h1>
          <p>{html_escape(kid['display_name'])} answered {correct} of {len(progress)} correctly.</p>
          {flash_html}
          <div class="row">
            <a class="button" href="/kid/{kid['id']}/topic/{topic['id']}/begin">Retake with New Questions</a>
            <a class="button secondary" href="/kid/{kid['id']}">Back to Topics</a>
          </div>
        </section>
        <section class="panel">
          <h2>Answer Review</h2>
          <ul>
            {''.join(f"<li>{html_escape(row['prompt'])}: {'correct' if row['is_correct'] else 'review needed'}</li>" for row in progress)}
          </ul>
        </section>
        """
        return "Quiz Complete", body

    choices = []
    for key in ("A", "B", "C", "D"):
        choices.append(
            f"""
            <label class="choice">
              <input type="radio" name="selected_choice" value="{key}" required>
              <strong>{key}.</strong> {html_escape(question[f'choice_{key.lower()}'])}
            </label>
            """
        )
    body = f"""
    <section class="hero">
      <span class="tag">Puzzle Run</span>
      <h1>{html_escape(topic['topic_name'])}</h1>
      <p>Skill target: {attempt['requested_skill_level']}</p>
      {flash_html}
      {feedback_html}
    </section>
    <div class="grid">
      <section class="panel">
        <h2>{html_escape(question['prompt'])}</h2>
        <details>
          <summary>Need a hint?</summary>
          <p>{html_escape(question['hint_text'])}</p>
          <p><strong>Concept:</strong> {html_escape(question['concept_title'])}</p>
          <p>{html_escape(question['concept_explanation'])}</p>
          <p><strong>Example:</strong> {html_escape(question['example_text'])}</p>
        </details>
        <form method="post" action="/kid/{kid['id']}/attempt/{attempt['id']}/answer">
          <input type="hidden" name="question_id" value="{question['id']}">
          <label><input type="checkbox" name="used_hint" value="1"> I used the hint for this question</label>
          <div class="choice-list">{''.join(choices)}</div>
          <button type="submit">Submit Answer</button>
        </form>
      </section>
    </div>
    """
    return "Quiz Run", body


def app(environ: dict, start_response: Callable):
    conn = get_connection()
    init_db(conn)
    if SEED_DEMO_DATA:
        seed_demo_data(conn)
    request = Request(environ)
    admin_ready = has_admin_account(conn)
    user = current_user(conn, request)

    if request.path == "/health" and request.method == "GET":
        start_response("200 OK", [("Content-Type", "text/plain; charset=utf-8")])
        return [b"ok"]

    if request.path == "/" and request.method == "GET":
        title, body = landing_view(request, admin_ready)
        return response(start_response, title, body, user)

    if request.path == "/setup/admin" and request.method == "POST":
        form, _ = request.form()
        new_admin, errors = create_initial_admin(
            conn,
            form.get("email", ""),
            form.get("password", ""),
            form.get("display_name", ""),
        )
        if not new_admin:
            title, body = landing_view(request, has_admin_account(conn), errors=errors)
            return response(start_response, title, body, status="400 Bad Request")
        session_id = create_session(conn, new_admin["id"])
        token = make_session_token(session_id)
        return redirect(start_response, "/admin", [session_cookie_header(token)])

    if request.path == "/login" and request.method == "POST":
        if not admin_ready:
            title, body = landing_view(request, False, errors=["Create the first admin account before signing in."])
            return response(start_response, title, body, status="400 Bad Request")
        form, _ = request.form()
        auth_user = authenticate_user(conn, form.get("email", ""), form.get("password", ""))
        if not auth_user:
            title, body = landing_view(request, admin_ready, "Invalid email or password.")
            return response(start_response, title, body, status="401 Unauthorized")
        session_id = create_session(conn, auth_user["id"])
        token = make_session_token(session_id)
        headers = [session_cookie_header(token)]
        location = "/admin" if auth_user["role"] == "admin" else "/parent"
        return redirect(start_response, location, headers)

    if request.path == "/register/parent" and request.method == "POST":
        if not admin_ready:
            title, body = landing_view(request, False, errors=["Create the first admin account before registering parents."])
            return response(start_response, title, body, status="400 Bad Request")
        form, _ = request.form()
        parent_user, errors = register_parent_account(
            conn,
            form.get("email", ""),
            form.get("password", ""),
            form.get("display_name", ""),
        )
        if not parent_user:
            title, body = landing_view(request, True, signup_errors=errors)
            return response(start_response, title, body, status="400 Bad Request")
        session_id = create_session(conn, parent_user["id"])
        token = make_session_token(session_id)
        return redirect(start_response, "/parent", [session_cookie_header(token)])

    if request.path == "/logout" and request.method == "POST":
        cookie = request.cookies.get(SESSION_COOKIE)
        if cookie:
            session_id = read_session_token(cookie.value)
            if session_id:
                destroy_session(conn, session_id)
        headers = [session_cookie_header(clear=True)]
        return redirect(start_response, "/", headers)

    if request.path == "/admin" and request.method == "GET":
        user = require_user(conn, request, "admin")
        if not user:
            return redirect(start_response, "/")
        title, body = admin_dashboard(conn, user)
        return response(start_response, title, body, user)

    if request.path == "/admin/upload" and request.method == "POST":
        user = require_user(conn, request, "admin")
        if not user:
            return redirect(start_response, "/")
        form, files = request.form()
        material = files.get("material")
        if not material:
            title, body = admin_dashboard(conn, user, errors=["A material file is required."])
            return response(start_response, title, body, user, status="400 Bad Request")
        ok, notes = create_material(
            conn,
            user["id"],
            form.get("title", material["filename"]),
            material["filename"],
            material["content_type"],
            material["content"],
        )
        title, body = admin_dashboard(conn, user, flash=notes[0] if ok else "", errors=None if ok else notes)
        return response(start_response, title, body, user, status="200 OK" if ok else "400 Bad Request")

    if request.path == "/parent" and request.method == "GET":
        user = require_user(conn, request, "parent")
        if not user:
            return redirect(start_response, "/")
        title, body = parent_dashboard(conn, user)
        return response(start_response, title, body, user)

    if request.path == "/parent/create-kid" and request.method == "POST":
        user = require_user(conn, request, "parent")
        if not user:
            return redirect(start_response, "/")
        form, _ = request.form()
        errors = []
        if not form.get("display_name", "").strip():
            errors.append("Kid name is required.")
        try:
            start_skill_level = int(form.get("start_skill_level", "2"))
        except ValueError:
            start_skill_level = 2
        if not errors:
            create_kid_profile(conn, user["id"], form["display_name"], form.get("age_band", "Ages 8-10"), start_skill_level)
            title, body = parent_dashboard(conn, user, flash="Kid profile created.")
            return response(start_response, title, body, user)
        title, body = parent_dashboard(conn, user, errors=errors)
        return response(start_response, title, body, user, status="400 Bad Request")

    if request.path.startswith("/kid/"):
        parent = require_user(conn, request, "parent")
        if not parent:
            return redirect(start_response, "/")
        segments = [segment for segment in request.path.split("/") if segment]
        try:
            kid_id = int(segments[1])
        except (ValueError, IndexError):
            return response(start_response, "Not Found", "<section class='panel'><p>Unknown kid route.</p></section>", parent, status="404 Not Found")
        kid = get_kid_profile(conn, kid_id, parent["id"])
        if not kid:
            return response(start_response, "Not Found", "<section class='panel'><p>Kid profile not found.</p></section>", parent, status="404 Not Found")

        if len(segments) == 2 and request.method == "GET":
            title, body = kid_dashboard(conn, kid)
            return response(start_response, title, body, parent)

        if len(segments) >= 4 and segments[2] == "topic":
            topic_id = int(segments[3])
            if len(segments) == 4 and request.method == "GET":
                title, body = kid_topic_view(conn, kid, topic_id)
                return response(start_response, title, body, parent)
            if len(segments) == 5 and segments[4] == "begin" and request.method == "GET":
                attempt_id = start_quiz_attempt(conn, kid["id"], topic_id)
                return redirect(start_response, f"/kid/{kid['id']}/attempt/{attempt_id}")

        if len(segments) >= 4 and segments[2] == "attempt":
            attempt_id = int(segments[3])
            attempt = get_attempt(conn, attempt_id, kid["id"])
            if not attempt:
                return response(start_response, "Not Found", "<section class='panel'><p>Attempt not found.</p></section>", parent, status="404 Not Found")
            if len(segments) == 4 and request.method == "GET":
                title, body = quiz_run_view(conn, kid, attempt)
                return response(start_response, title, body, parent)
            if len(segments) == 5 and segments[4] == "answer" and request.method == "POST":
                form, _ = request.form()
                question_id = int(form.get("question_id", "0"))
                selected_choice = form.get("selected_choice", "")
                used_hint = form.get("used_hint") == "1"
                result = record_answer(conn, attempt_id, question_id, selected_choice, used_hint)
                attempt = get_attempt(conn, attempt_id, kid["id"])
                title, body = quiz_run_view(conn, kid, attempt, feedback=result.feedback_text)
                return response(start_response, title, body, parent)

    return response(
        start_response,
        "Not Found",
        "<section class='panel'><h1>Not Found</h1><p>The requested page does not exist.</p></section>",
        user,
        status="404 Not Found",
    )


def run_dev_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    with make_server(host, port, app) as server:
        print(f"QuizKid running at http://{host}:{port}")
        server.serve_forever()


def run_from_env() -> None:
    host = os.environ.get("APP_HOST", "127.0.0.1")
    port = int(os.environ.get("APP_PORT", "8000"))
    run_dev_server(host=host, port=port)
