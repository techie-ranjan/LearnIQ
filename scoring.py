"""Plain Python scoring logic. No database and no AI here, so it is easy to test."""


def score_quiz(questions, selected):
    """questions: rows with 'id' and 'correct'. selected: {question_id: 'a'..'d'}."""
    results = []
    for q in questions:
        choice = selected.get(q["id"])
        results.append({
            "question_id": q["id"],
            "selected": choice,
            "is_correct": choice == q["correct"],
        })
    score = sum(r["is_correct"] for r in results)
    return score, results


def status_for(accuracy):
    """Label a topic by accuracy percentage."""
    if accuracy >= 75:
        return "Strong"
    if accuracy >= 50:
        return "Improving"
    return "Weak"


MIN_ANSWERS = 3  # need at least this many answers before judging a category


def build_stats(rows):
    """rows: topic_id, topic, category, answered, correct. Returns stats, weakest first."""
    stats = []
    for r in rows:
        accuracy = round(100 * r["correct"] / r["answered"])
        status = status_for(accuracy) if r["answered"] >= MIN_ANSWERS else "New"
        stats.append({**dict(r), "accuracy": accuracy, "status": status})
    stats.sort(key=lambda s: s["accuracy"])
    return stats


def difficulty_mix(accuracy, total):
    """How many easy / medium / hard questions to ask, from recent accuracy (None = new user)."""
    if accuracy is None:
        weights = (3, 5, 2)    # balanced start
    elif accuracy < 50:
        weights = (6, 3, 1)    # struggling: mostly easy
    elif accuracy < 80:
        weights = (2, 5, 3)    # doing fine: mostly medium
    else:
        weights = (1, 3, 6)    # doing great: mostly hard
    easy = round(total * weights[0] / 10)
    medium = round(total * weights[1] / 10)
    return easy, medium, max(0, total - easy - medium)
