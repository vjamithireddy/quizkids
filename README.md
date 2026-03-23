# QuizKid

QuizKid is a Python-first implementation of the product plan for a kids quiz and learning application. It includes:

- real first-run admin setup and parent registration
- role-based web experience for `admin` and `parent`
- parent-managed `kid` profiles
- guided topic learning paths with adaptive difficulty
- quiz attempts, answer review, retakes, hints, and mastery tracking
- admin course-material upload pipeline scaffolding with automatic generation
- SQLite persistence using only the Python standard library

This repository is intentionally dependency-light so it can run on a simple Hostinger VPS without assuming Node.js or external Python packages.

## What is implemented

- `Admin console`
  - first-run admin creation instead of hardcoded production credentials
  - sign in as seeded admin
  - upload course material through a simple file form
  - validate uploads
  - store source material and generate basic subject/chapter/topic/concept/question data
  - inspect generated materials and generation status
- `Parent dashboard`
  - sign in as seeded parent
  - create kid profiles
  - review topic mastery and recent quiz attempts
  - launch kid mode for a selected child
- `Kid experience`
  - enter guided topic paths
  - receive small quiz batches
  - see hints before answering
  - get explanation on wrong answers
  - review past attempts
  - retake a topic with different questions when available
- `Security baseline`
  - password hashing with PBKDF2
  - signed session cookies
  - role checks
  - admin audit logging

## Startup modes

- Production/default: no demo users are created automatically. The first request shows an admin setup screen.
- Demo mode: set `QUIZKID_SEED_DEMO=1` to seed sample admin and parent accounts for local walkthroughs.

## What is intentionally simplified

- PDF and DOC ingestion is scaffolded, but deep parsing is limited without external libraries.
- Generated questions are rule-based placeholders designed to prove the workflow, not final pedagogical quality.
- The app runs as a single-process server backed by SQLite for local development and early VPS deployment.

## Demo credentials

Available only when `QUIZKID_SEED_DEMO=1`:
- Admin: `admin@quizkid.local` / `admin123`
- Parent: `parent@quizkid.local` / `parent123`

## Run locally

```bash
python3 run.py
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000).

The database is created automatically at `data/quizkid.sqlite3`.

## Run tests

```bash
python3 -m unittest discover -s tests
```

## Suggested next steps

- replace rule-based question generation with an LLM-backed pipeline and moderation layer
- add stronger PDF/DOC extraction with external libraries or an async content worker
- move from SQLite to Postgres for production growth
- separate admin and learner UIs if the product expands beyond a single VPS
