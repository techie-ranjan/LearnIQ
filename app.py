import os
import sqlite3

import click
from flask import (Flask, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from admin import admin_bp
from ai_helper import AIError, explain
from helpers import (check_csrf, close_db, csrf_field, get_db, login_required,
                     upgrade_schema)
from practice import build_quiz, category_rows
from scoring import build_stats, score_quiz, status_for
from seed import load_questions

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-change-me")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
if app.config["SECRET_KEY"] == "dev-change-me":
    print("WARNING: SECRET_KEY is not set in .env. Fine for local testing, never for a live site.")
app.teardown_appcontext(close_db)
app.before_request(check_csrf)
app.register_blueprint(admin_bp)
QUIZ_LENGTH = 10
LEVELS = ["adaptive", "weak", "all", "easy", "medium", "hard", "interview"]


@app.context_processor
def template_globals():
    return {"csrf_field": csrf_field, "is_admin": g.get("is_admin", False)}


# ---------- command line tools ----------
@app.cli.command("init-db")
def init_db():
    """Create tables and load questions.csv (this wipes existing data)."""
    db = get_db()
    with open(os.path.join(app.root_path, "schema.sql")) as f:
        db.executescript(f.read())
    upgrade_schema(db)
    csv_path = os.path.join(app.root_path, "questions.csv")
    count = load_questions(db, csv_path) if os.path.exists(csv_path) else 0
    print(f"Database ready. {count} questions loaded.")


@app.cli.command("import-questions")
@click.argument("csv_file")
def import_questions(csv_file):
    """Add more questions from a CSV file without deleting anything."""
    try:
        print(f"{load_questions(get_db(), csv_file)} questions added.")
    except ValueError as error:
        print(f"Nothing was added. {error}")


@app.cli.command("make-admin")
@click.argument("email")
def make_admin(email):
    """Give an existing account admin rights: flask --app app make-admin you@example.com"""
    db = get_db()
    changed = db.execute("UPDATE users SET is_admin = 1 WHERE email = ?", (email.strip().lower(),)).rowcount
    db.commit()
    print("Done. Log in again to see the Admin link." if changed else "No user with that email. Register first.")


# ---------- auth ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        if not (name and email and len(password) >= 6):
            flash("Fill all fields. Password needs at least 6 characters.")
        else:
            try:
                get_db().execute(
                    "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                    (name, email, generate_password_hash(password)),
                )
                get_db().commit()
                flash("Account created. Please log in.")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("That email is already registered.")
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form["password"]):
            session.clear()
            session["user_id"] = user["id"]
            session["name"] = user["name"]
            return redirect(url_for("topics"))
        flash("Wrong email or password.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- quiz ----------
@app.route("/")
@login_required
def topics():
    db = get_db()
    rows = db.execute(
        "SELECT t.id, t.name, COUNT(q.id) AS total FROM topics t "
        "LEFT JOIN questions q ON q.topic_id = t.id AND q.active = 1 GROUP BY t.id ORDER BY t.name"
    ).fetchall()
    recent = db.execute(
        "SELECT a.id, a.score, a.total, t.name AS topic, "
        "datetime(a.taken_at, 'localtime') AS taken FROM attempts a "
        "JOIN topics t ON t.id = a.topic_id WHERE a.user_id = ? "
        "ORDER BY a.id DESC LIMIT 5",
        (session["user_id"],),
    ).fetchall()
    return render_template("topics.html", topics=rows, recent=recent)


@app.route("/quiz/<int:topic_id>", methods=["GET", "POST"])
@login_required
def quiz(topic_id):
    db = get_db()
    topic = db.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()
    if topic is None:
        return redirect(url_for("topics"))

    if request.method == "POST":
        ids = [int(i) for i in request.form["qids"].split(",") if i.isdigit()]
        marks = ",".join("?" * len(ids))
        questions = db.execute(
            f"SELECT id, correct FROM questions WHERE topic_id = ? AND id IN ({marks})",
            (topic_id, *ids),
        ).fetchall()
        selected = {q["id"]: request.form.get(f"q{q['id']}") for q in questions}
        score, results = score_quiz(questions, selected)

        cur = db.execute(
            "INSERT INTO attempts (user_id, topic_id, score, total) VALUES (?, ?, ?, ?)",
            (session["user_id"], topic_id, score, len(questions)),
        )
        db.executemany(
            "INSERT INTO answers (attempt_id, question_id, selected, is_correct) VALUES (?, ?, ?, ?)",
            [(cur.lastrowid, r["question_id"], r["selected"], int(r["is_correct"])) for r in results],
        )
        db.commit()
        return redirect(url_for("result", attempt_id=cur.lastrowid))

    level = request.args.get("level", "adaptive")
    if level not in LEVELS:
        level = "adaptive"
    questions, label, note = build_quiz(db, session["user_id"], topic_id, level,
                                        request.args.get("category", ""), QUIZ_LENGTH)
    return render_template("quiz.html", topic=topic, questions=questions, label=label, note=note)


@app.route("/result/<int:attempt_id>")
@login_required
def result(attempt_id):
    db = get_db()
    attempt = db.execute(
        "SELECT a.*, t.name AS topic FROM attempts a JOIN topics t ON t.id = a.topic_id "
        "WHERE a.id = ? AND a.user_id = ?",
        (attempt_id, session["user_id"]),
    ).fetchone()
    if attempt is None:
        return redirect(url_for("topics"))

    answers = db.execute(
        "SELECT q.*, an.selected, an.is_correct, e.text AS explanation FROM answers an "
        "JOIN questions q ON q.id = an.question_id "
        "LEFT JOIN explanations e ON e.question_id = q.id AND e.selected = COALESCE(an.selected, '') "
        "WHERE an.attempt_id = ?",
        (attempt_id,),
    ).fetchall()
    accuracy = round(100 * attempt["score"] / attempt["total"]) if attempt["total"] else 0
    return render_template("result.html", attempt=attempt, answers=answers,
                           accuracy=accuracy, status=status_for(accuracy))


@app.route("/explain/<int:attempt_id>/<int:question_id>", methods=["POST"])
@login_required
def explain_answer(attempt_id, question_id):
    """Ask the AI to explain one wrong answer. Saved so each explanation is paid for only once."""
    db = get_db()
    row = db.execute(
        "SELECT q.*, t.name AS topic, an.selected FROM answers an "
        "JOIN attempts a ON a.id = an.attempt_id JOIN questions q ON q.id = an.question_id "
        "JOIN topics t ON t.id = q.topic_id "
        "WHERE an.attempt_id = ? AND an.question_id = ? AND a.user_id = ? AND an.is_correct = 0",
        (attempt_id, question_id, session["user_id"]),
    ).fetchone()
    if row is None:
        return redirect(url_for("topics"))

    selected = row["selected"] or ""
    saved = db.execute("SELECT 1 FROM explanations WHERE question_id = ? AND selected = ?",
                       (question_id, selected)).fetchone()
    if not saved:
        try:
            text = explain(row["topic"], row["category"], row["text"],
                           row["option_" + selected] if selected else "",
                           row["option_" + row["correct"]])
            db.execute("INSERT OR IGNORE INTO explanations (question_id, selected, text) VALUES (?, ?, ?)",
                       (question_id, selected, text))
            db.commit()
        except AIError as error:
            flash(str(error))
    return redirect(url_for("result", attempt_id=attempt_id) + f"#q{question_id}")


@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    user_id = session["user_id"]
    stats = build_stats(category_rows(db, user_id))

    per_topic = {}  # topic name -> [answered, correct]
    for s in stats:
        totals = per_topic.setdefault(s["topic"], [0, 0])
        totals[0] += s["answered"]
        totals[1] += s["correct"]
    languages = sorted(
        ({"name": n, "answered": a, "accuracy": round(100 * c / a)} for n, (a, c) in per_topic.items()),
        key=lambda x: -x["accuracy"],
    )
    answered = sum(a for a, _ in per_topic.values())
    correct = sum(c for _, c in per_topic.values())

    attempts = db.execute(
        "SELECT a.score, a.total, t.name AS topic FROM attempts a "
        "JOIN topics t ON t.id = a.topic_id WHERE a.user_id = ? ORDER BY a.id DESC LIMIT 10",
        (user_id,),
    ).fetchall()[::-1]
    trend = [{"topic": a["topic"], "accuracy": round(100 * a["score"] / a["total"]) if a["total"] else 0}
             for a in attempts]

    return render_template(
        "dashboard.html", stats=stats, languages=languages, trend=trend,
        weak=[s for s in stats if s["status"] == "Weak"][:3],
        revise={s["topic_id"]: s["topic"] for s in stats if s["status"] == "Weak"},
        quizzes=db.execute("SELECT COUNT(*) FROM attempts WHERE user_id = ?", (user_id,)).fetchone()[0],
        answered=answered, accuracy=round(100 * correct / answered) if answered else 0,
    )


if __name__ == "__main__":
    app.run(debug=True)
