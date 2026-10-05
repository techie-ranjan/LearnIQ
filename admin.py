"""Admin pages: users, topics and the question bank."""
import csv
import io

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from helpers import admin_required, get_db
from seed import DIFFICULTIES, load_rows

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")
PAGE_SIZE = 20


@admin_bp.route("/")
@admin_required
def home():
    db = get_db()
    count = lambda sql: db.execute(sql).fetchone()[0]
    stats = [("Users", count("SELECT COUNT(*) FROM users")),
             ("Active questions", count("SELECT COUNT(*) FROM questions WHERE active = 1")),
             ("Quiz attempts", count("SELECT COUNT(*) FROM attempts"))]
    topics = db.execute(
        "SELECT t.id, t.name, COALESCE(SUM(q.active), 0) AS total FROM topics t "
        "LEFT JOIN questions q ON q.topic_id = t.id GROUP BY t.id ORDER BY t.name").fetchall()
    users = db.execute(
        "SELECT u.id, u.name, u.email, u.is_admin, date(u.created_at) AS joined, "
        "(SELECT COUNT(*) FROM attempts a WHERE a.user_id = u.id) AS attempts "
        "FROM users u ORDER BY u.id").fetchall()
    return render_template("admin_home.html", stats=stats, topics=topics, users=users)


# ---------- topics ----------
@admin_bp.route("/topics/add", methods=["POST"])
@admin_required
def add_topic():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Type a topic name.")
    else:
        db = get_db()
        added = db.execute("INSERT OR IGNORE INTO topics (name) VALUES (?)", (name,)).rowcount
        db.commit()
        flash(f"Topic '{name}' added." if added else f"Topic '{name}' already exists.")
    return redirect(url_for("admin.home"))


# ---------- users ----------
@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    db = get_db()
    user = db.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None or user["is_admin"] or user_id == session["user_id"]:
        flash("That account cannot be deleted. Remove its admin rights first.")
    else:
        db.execute("DELETE FROM answers WHERE attempt_id IN (SELECT id FROM attempts WHERE user_id = ?)", (user_id,))
        db.execute("DELETE FROM attempts WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        db.commit()
        flash("User and their quiz history deleted.")
    return redirect(url_for("admin.home"))


@admin_bp.route("/users/<int:user_id>/toggle-admin", methods=["POST"])
@admin_required
def toggle_admin(user_id):
    if user_id == session["user_id"]:
        flash("You cannot change your own admin rights.")
    else:
        db = get_db()
        db.execute("UPDATE users SET is_admin = 1 - is_admin WHERE id = ?", (user_id,))
        db.commit()
    return redirect(url_for("admin.home"))


# ---------- question bank ----------
@admin_bp.route("/import", methods=["POST"])
@admin_required
def import_csv():
    upload = request.files.get("file")
    if not upload or not upload.filename.lower().endswith(".csv"):
        flash("Choose a .csv file.")
        return redirect(url_for("admin.home"))
    try:
        reader = csv.DictReader(io.TextIOWrapper(upload.stream, encoding="utf-8-sig", newline=""))
        flash(f"{load_rows(get_db(), reader)} questions imported.")
    except (ValueError, UnicodeDecodeError, csv.Error) as error:
        flash(f"Nothing was imported. {error}")
    return redirect(url_for("admin.home"))


@admin_bp.route("/questions")
@admin_required
def questions():
    db = get_db()
    topic_id = request.args.get("topic", type=int)
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "all")
    page = max(1, request.args.get("page", 1, type=int))

    where, params = ["1 = 1"], []
    if topic_id:
        where.append("q.topic_id = ?"); params.append(topic_id)
    if search:
        where.append("q.text LIKE ?"); params.append(f"%{search}%")
    if status in ("active", "hidden"):
        where.append("q.active = ?"); params.append(1 if status == "active" else 0)
    clause = " AND ".join(where)

    total = db.execute(f"SELECT COUNT(*) FROM questions q WHERE {clause}", params).fetchone()[0]
    rows = db.execute(
        f"SELECT q.id, q.text, q.category, q.difficulty, q.active, t.name AS topic FROM questions q "
        f"JOIN topics t ON t.id = q.topic_id WHERE {clause} ORDER BY q.id DESC LIMIT ? OFFSET ?",
        (*params, PAGE_SIZE, (page - 1) * PAGE_SIZE)).fetchall()
    topics = db.execute("SELECT id, name FROM topics ORDER BY name").fetchall()
    filters = {"topic": topic_id, "search": search, "status": status}
    return render_template("admin_questions.html", rows=rows, topics=topics, filters=filters,
                           page=page, pages=max(1, -(-total // PAGE_SIZE)), total=total)


@admin_bp.route("/questions/<int:question_id>/toggle", methods=["POST"])
@admin_required
def toggle_question(question_id):
    """Hide or restore a question. Hidden questions are never asked, but history stays intact."""
    db = get_db()
    db.execute("UPDATE questions SET active = 1 - active WHERE id = ?", (question_id,))
    db.commit()
    return redirect(request.referrer or url_for("admin.questions"))


@admin_bp.route("/questions/new", methods=["GET", "POST"])
@admin_bp.route("/questions/<int:question_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_question(question_id=None):
    db = get_db()
    topics = db.execute("SELECT id, name FROM topics ORDER BY name").fetchall()
    question = {"difficulty": "easy", "correct": "a"}
    if question_id:
        question = db.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
        if question is None:
            abort(404)

    if request.method == "POST":
        data = {k: request.form.get(k, "").strip() for k in (
            "text", "option_a", "option_b", "option_c", "option_d", "category",
            "difficulty", "correct", "topic_id", "new_topic")}
        error = check_question(data)
        if error:
            flash(error)
            return render_template("admin_question_form.html", q=data, topics=topics,
                                   difficulties=DIFFICULTIES, editing=question_id)
        if data["new_topic"]:
            db.execute("INSERT OR IGNORE INTO topics (name) VALUES (?)", (data["new_topic"],))
            data["topic_id"] = db.execute("SELECT id FROM topics WHERE name = ?",
                                          (data["new_topic"],)).fetchone()[0]
        values = (int(data["topic_id"]), data["text"], data["option_a"], data["option_b"], data["option_c"],
                  data["option_d"], data["correct"], data["difficulty"], data["category"] or "General")
        if question_id:
            db.execute("UPDATE questions SET topic_id=?, text=?, option_a=?, option_b=?, option_c=?, "
                       "option_d=?, correct=?, difficulty=?, category=? WHERE id=?", (*values, question_id))
        else:
            db.execute("INSERT INTO questions (topic_id, text, option_a, option_b, option_c, option_d, "
                       "correct, difficulty, category) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
        db.commit()
        flash("Question saved.")
        return redirect(url_for("admin.questions"))

    return render_template("admin_question_form.html", q=question, topics=topics,
                           difficulties=DIFFICULTIES, editing=question_id)


def check_question(data):
    """Return an error message, or None if the form is valid."""
    options = [data[f"option_{k}"] for k in "abcd"]
    if not data["text"] or not all(options):
        return "The question and all four options are required."
    if len({o.lower() for o in options}) < 4:
        return "The four options must all be different."
    if data["correct"] not in ("a", "b", "c", "d"):
        return "Choose the correct option."
    if data["difficulty"] not in DIFFICULTIES:
        return "Choose a difficulty."
    if not data["new_topic"] and not data["topic_id"].isdigit():
        return "Choose a topic, or type a new one."
    return None
