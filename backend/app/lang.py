"""The patient's language: names for prompts, and "who is this?" phrases for the voice trigger.

Detection is simple rules on the patient's own transcript chunks (no LLM): a chunk that
contains one of these phrases re-opens the "Who is this?" card.
"""
import re

LANGUAGES = {"en-IN": "English", "hi-IN": "Hindi", "mr-IN": "Marathi", "gu-IN": "Gujarati"}
DEFAULT = "en-IN"

_ENGLISH = [
    "who is this", "who is that", "who are you", "who am i talking to", "who am i speaking to",
    "what is your name", "do i know you",
    # Romanised Hindi / Marathi, in case recognition writes them in Latin script
    "kaun hai", "kaun ho", "aap kaun", "tum kaun", "kon ahe", "kon aahe", "tumhi kon",
]
WHO_PHRASES = {
    "en-IN": _ENGLISH,
    "hi-IN": ["कौन है", "कौन हैं", "कौन हो", "किससे बात", "नाम क्या है", "आपको जानती", "आपको जानता", "तुम्हें जानती"],
    "mr-IN": ["कोण आहे", "कोण आहेस", "कोण आहात", "तुम्ही कोण", "तू कोण", "कोण बोल", "कोणाशी बोल", "नाव काय",
              "ओळखते का", "ओळखतो का"],
    "gu-IN": ["કોણ છે", "કોણ છો", "કોણ બોલ", "કોની સાથે વાત", "નામ શું", "તમને ઓળખ"],
}


def normalise(code: str | None) -> str:
    return code if code in LANGUAGES else DEFAULT


def name(code: str | None) -> str:
    return LANGUAGES[normalise(code)]


def _clean(text: str) -> str:
    text = text.lower().replace("’", "'").replace("who's", "who is").replace("what's", "what is")
    text = re.sub(r"[.,!?;:\"“”()\[\]।॥\-]", " ", text)  # punctuation only: Indic vowel signs must survive
    return f" {re.sub(r'\s+', ' ', text).strip()} "


def asks_who(text: str, code: str | None = None) -> bool:
    """True if the patient is asking who they are talking to. English phrases always count."""
    said = _clean(text)
    phrases = _ENGLISH + (WHO_PHRASES.get(normalise(code), []) if normalise(code) != DEFAULT else [])
    return any(f" {_clean(p).strip()} " in said if p.isascii() else _clean(p).strip() in said for p in phrases)
