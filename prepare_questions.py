"""Turns programming_language_quiz_1000.csv (question + answer) into MCQs.

Wrong options are taken from other answers of the same language and category,
so they look believable. Run once:  python prepare_questions.py
"""
import csv
import random
from collections import defaultdict

SOURCE = "programming_language_quiz_1000.csv"
OUTPUT = "questions.csv"
random.seed(42)  # same output every run

with open(SOURCE, newline="", encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))

by_category = defaultdict(list)
by_language = defaultdict(list)
for r in rows:
    by_category[(r["language"], r["question_type"], r["category"])].append(r["answer"])
    by_language[(r["language"], r["question_type"])].append(r["answer"])


def pick_wrong_answers(row, count=3):
    correct = row["answer"]
    chosen = []
    pools = [by_category[(row["language"], row["question_type"], row["category"])],
             by_language[(row["language"], row["question_type"])]]
    for pool in pools:
        options = sorted({a for a in pool if a != correct and a not in chosen})
        random.shuffle(options)
        options.sort(key=lambda a: abs(len(a) - len(correct)))  # similar length looks fairer
        nearest = options[:8]
        random.shuffle(nearest)
        chosen += nearest[:count - len(chosen)]
        if len(chosen) == count:
            break
    return chosen


with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
    out = csv.writer(f)
    out.writerow(["topic", "question", "option_a", "option_b", "option_c",
                  "option_d", "correct", "difficulty", "category"])
    for r in rows:
        options = pick_wrong_answers(r) + [r["answer"]]
        random.shuffle(options)
        letter = "abcd"[options.index(r["answer"])]
        out.writerow([r["language"], r["question"], *options, letter,
                      r["difficulty"].lower(), r["category"]])

print(f"{len(rows)} MCQs written to {OUTPUT}")
