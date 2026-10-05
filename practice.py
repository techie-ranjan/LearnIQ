"""Decides WHICH questions to ask: adaptive difficulty and weak-area revision."""
from scoring import build_stats, difficulty_mix

RECENT_ANSWERS = 20
NORMAL_LEVELS = ["easy", "medium", "hard"]


def category_rows(db, user_id):
    """Answered / correct counts for every topic + category the user has tried."""
    return db.execute(
        "SELECT t.id AS topic_id, t.name AS topic, q.category, "
        "COUNT(*) AS answered, SUM(an.is_correct) AS correct "
        "FROM answers an JOIN attempts a ON a.id = an.attempt_id "
        "JOIN questions q ON q.id = an.question_id JOIN topics t ON t.id = q.topic_id "
        "WHERE a.user_id = ? GROUP BY t.id, q.category",
        (user_id,),
    ).fetchall()


def recent_accuracy(db, user_id, topic_id):
    """Accuracy over the last 20 answers in this topic, or None if there is too little history."""
    rows = db.execute(
        "SELECT an.is_correct FROM answers an JOIN attempts a ON a.id = an.attempt_id "
        "JOIN questions q ON q.id = an.question_id WHERE a.user_id = ? AND q.topic_id = ? "
        "ORDER BY an.id DESC LIMIT ?",
        (user_id, topic_id, RECENT_ANSWERS),
    ).fetchall()
    if len(rows) < 5:
        return None
    return round(100 * sum(r[0] for r in rows) / len(rows))


def pick(db, user_id, topic_id, limit, difficulties=None, categories=None,
         exclude=(), missed_first=False):
    """Random questions, preferring ones this user has not seen (or has missed, for revision)."""
    history = ("(SELECT COUNT(*) FROM answers an JOIN attempts a ON a.id = an.attempt_id "
               "WHERE an.question_id = q.id AND a.user_id = ?{})")
    sql = (f"SELECT q.*, {history.format('')} AS seen, {history.format(' AND an.is_correct = 0')} AS missed "
           "FROM questions q WHERE q.topic_id = ? AND q.active = 1")
    params = [user_id, user_id, topic_id]
    for column, values in (("difficulty", difficulties), ("category", categories)):
        if values:
            sql += f" AND q.{column} IN ({','.join('?' * len(values))})"
            params += list(values)
    if exclude:
        sql += f" AND q.id NOT IN ({','.join('?' * len(exclude))})"
        params += list(exclude)
    order = "missed DESC, seen, RANDOM()" if missed_first else "seen, RANDOM()"
    return db.execute(f"{sql} ORDER BY {order} LIMIT ?", (*params, limit)).fetchall()


def adaptive(db, user_id, topic_id, total):
    """Mix of easy/medium/hard chosen from the user's recent accuracy in this topic."""
    accuracy = recent_accuracy(db, user_id, topic_id)
    chosen = []
    for level, count in zip(NORMAL_LEVELS, difficulty_mix(accuracy, total)):
        if count:
            chosen += pick(db, user_id, topic_id, count, [level], exclude=[q["id"] for q in chosen])
    if len(chosen) < total:  # a level ran short, fill up from the others
        chosen += pick(db, user_id, topic_id, total - len(chosen), NORMAL_LEVELS,
                       exclude=[q["id"] for q in chosen])
    return chosen, accuracy


def revision(db, user_id, topic_id, total):
    """Questions from the user's weak categories (missed ones first). Empty list if none are weak."""
    stats = build_stats(category_rows(db, user_id))
    weak = [s["category"] for s in stats if s["topic_id"] == topic_id and s["status"] == "Weak"]
    if not weak:
        return []
    chosen = pick(db, user_id, topic_id, total, categories=weak, missed_first=True)
    if len(chosen) < total:  # small categories: top up with other questions
        chosen += pick(db, user_id, topic_id, total - len(chosen), NORMAL_LEVELS,
                       exclude=[q["id"] for q in chosen])
    return chosen


def build_quiz(db, user_id, topic_id, level, category, total):
    """Returns (questions, label, note) for the quiz page."""
    if category:
        return pick(db, user_id, topic_id, total, categories=[category]), category, "Practising one category."
    note = ""
    if level == "weak":
        questions = revision(db, user_id, topic_id, total)
        if questions:
            return questions, "Revision", "Built from your weak categories. Questions you missed come first."
        level, note = "adaptive", "No weak areas found yet, so this is an adaptive quiz. "
    if level == "adaptive":
        questions, accuracy = adaptive(db, user_id, topic_id, total)
        note += ("Not enough history yet, so you get a balanced mix." if accuracy is None
                 else f"Difficulty adjusted to your recent accuracy ({accuracy}%).")
        return questions, "Adaptive", note
    difficulties = None if level == "all" else [level]
    return pick(db, user_id, topic_id, total, difficulties=difficulties), level.capitalize(), ""
