"""Loads quiz questions from a CSV file into the database."""
import csv

REQUIRED = ["topic", "question", "option_a", "option_b", "option_c", "option_d", "correct"]
DIFFICULTIES = ("easy", "medium", "hard", "interview")


def load_questions(db, csv_path):
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        return load_rows(db, csv.DictReader(f))


def load_rows(db, reader):
    """Insert every row. Nothing is saved if any row is invalid (ValueError says which)."""
    missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError("Missing column(s): " + ", ".join(missing))
    added = 0
    try:
        for number, row in enumerate(reader, start=2):  # row 1 is the header
            if any(not (row[c] or "").strip() for c in REQUIRED):
                raise ValueError(f"Row {number}: a required value is empty.")
            correct = row["correct"].strip().lower()
            difficulty = (row.get("difficulty") or "easy").strip().lower()
            if correct not in ("a", "b", "c", "d"):
                raise ValueError(f"Row {number}: 'correct' must be a, b, c or d.")
            if difficulty not in DIFFICULTIES:
                raise ValueError(f"Row {number}: difficulty must be easy, medium, hard or interview.")
            topic = row["topic"].strip()
            db.execute("INSERT OR IGNORE INTO topics (name) VALUES (?)", (topic,))
            topic_id = db.execute("SELECT id FROM topics WHERE name = ?", (topic,)).fetchone()[0]
            db.execute(
                "INSERT INTO questions (topic_id, text, option_a, option_b, option_c, option_d, "
                "correct, difficulty, category) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (topic_id, row["question"].strip(), row["option_a"], row["option_b"],
                 row["option_c"], row["option_d"], correct, difficulty,
                 (row.get("category") or "General").strip()),
            )
            added += 1
    except ValueError:
        db.rollback()
        raise
    db.commit()
    return added
