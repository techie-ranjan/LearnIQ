"""Asks an AI service to explain a wrong answer.

Supports Google Gemini (free tier) and Anthropic Claude. Choose with AI_PROVIDER in
the .env file. Uses only the standard library, so nothing extra to install.
"""
import json
import os
import urllib.error
import urllib.request

SYSTEM_PROMPT = (
    "You are a friendly programming tutor for beginner BCA students in India. "
    "Explain in simple English, in at most 4 short sentences. "
    "Say why the correct answer is right and why the student's choice is wrong. "
    "End with one short tip to remember it. Plain text only, no markdown."
)


class AIError(Exception):
    """Problem with the AI service. The message is safe to show to the user."""


def load_env():
    """Read KEY=VALUE lines from the .env file next to this script."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"'))


load_env()


def _post(url, headers, payload):
    request = urllib.request.Request(url, json.dumps(payload).encode(), headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        messages = {
            400: "The AI service rejected the request. Check the API key and model name in .env.",
            401: "The AI key was rejected. Check the key in your .env file.",
            403: "The AI key was rejected. Check the key in your .env file.",
            404: "AI model not found. Change the model name in your .env file.",
            429: "Too many AI requests right now. Wait a minute and try again.",
        }
        raise AIError(messages.get(e.code, f"The AI service returned an error ({e.code}).")) from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise AIError("Could not reach the AI service. Check your internet connection.") from e


def _gemini(prompt, key):
    model = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
    data = _post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {"Content-Type": "application/json", "x-goog-api-key": key},
        {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 800, "temperature": 0.4},
        },
    )
    return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])


def _anthropic(prompt, key):
    model = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    data = _post(
        "https://api.anthropic.com/v1/messages",
        {"Content-Type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"},
        {"model": model, "max_tokens": 400, "system": SYSTEM_PROMPT,
         "messages": [{"role": "user", "content": prompt}]},
    )
    return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")


def explain(language, category, question, chosen, correct):
    """Return a short beginner-friendly explanation of why `chosen` is wrong."""
    provider = os.environ.get("AI_PROVIDER", "gemini").lower()
    key_name = "ANTHROPIC_API_KEY" if provider == "anthropic" else "GEMINI_API_KEY"
    key = os.environ.get(key_name, "").strip()
    if not key or key.startswith("paste"):
        raise AIError(f"AI is not set up yet. Add {key_name} to the .env file and restart the app.")

    prompt = (f"Language: {language}\nTopic: {category}\nQuestion: {question}\n"
              f"Student's answer: {chosen or 'Not answered'}\nCorrect answer: {correct}")
    try:
        text = (_anthropic if provider == "anthropic" else _gemini)(prompt, key).strip()
    except (KeyError, IndexError, TypeError) as e:
        raise AIError("The AI sent an unexpected reply. Please try again.") from e
    if not text:
        raise AIError("The AI sent an empty reply. Please try again.")
    return text
