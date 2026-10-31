"""Rule-based fallback for the memory agent, used when Claude is unavailable.

Much narrower than the LLM path: it recognises common phrasings (moves, exams,
visits, pets, "don't tell", relationship claims) so the full memory loop can be
demoed without network access. Output shapes match the LLM schemas exactly.
"""
import re
from datetime import datetime, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
RELATIONS = ("daughter|son|granddaughter|grandson|sister|brother|niece|nephew|wife|husband|"
             "mother|father|cousin|friend|daughter-in-law|son-in-law")
_DAY = r"(today|tomorrow|(?:this |next |on )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))"


def _resolve_day(phrase: str, call_date: datetime) -> datetime | None:
    phrase = phrase.lower().strip()
    if phrase == "today":
        return call_date
    if phrase == "tomorrow":
        return call_date + timedelta(days=1)
    for i, name in enumerate(WEEKDAYS):
        if name in phrase:
            delta = (i - call_date.weekday()) % 7
            if delta == 0:
                delta = 7
            if phrase.startswith("next") and delta < 7:
                delta += 7 if delta <= 2 else 0
            return call_date + timedelta(days=delta)
    return None


def _date_label(d: datetime) -> str:
    return d.strftime("%A %d %b").replace(" 0", " ")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def _cand(type_, content, speaker, about, quote, event_date=None, days=None, importance=0.6, confidence=0.85,
          sensitive=False):
    return {
        "type": type_, "content": content, "speaker": speaker, "about": about, "quote": quote,
        "event_date": event_date.strftime("%Y-%m-%d") if event_date else None,
        "expires_after_event_days": days, "importance": importance, "confidence": confidence, "sensitive": sensitive,
    }


def extract(lines: list[dict], contact: dict, patient_name: str, call_date: datetime) -> dict:
    name = contact["name"]
    cands: list[dict] = []
    recap_bits: list[str] = []
    said: list[str] = []

    for line in lines:
        speaker = line["speaker"]
        for s in _sentences(line["text"]):
            low = s.lower()
            if speaker == "contact":
                said.append(s)
                if m := re.search(r"don'?t tell (?:\w+ )?(?:about )?(.+?)[.!?]?$", s, re.I):
                    topic = m.group(1).strip().rstrip(".!")
                    cands.append(_cand("sensitive", f"Do not mention {topic} to {patient_name}", "contact", "other", s,
                                       importance=0.9, confidence=0.9, sensitive=True))
                    continue
                if m := re.search(r"\b(?:moved|shifted|relocated) to ([A-Z][\w]+)", s) or \
                        re.search(r"\b(?:i|we) (?:now )?live in ([A-Z][\w]+)", s, re.I):
                    city = m.group(1).capitalize()
                    cands.append(_cand("fact", f"{name} lives in {city}", "contact", "contact_self", s, importance=0.7))
                if m := re.search(r"\bmy ([\w ]{0,20}?)\s?exam (?:is )?" + _DAY, s, re.I):
                    when = _resolve_day(m.group(2), call_date)
                    subject = m.group(1).strip().title()
                    label = f"{subject} exam" if subject else "an exam"
                    content = f"{name} has {'a ' if subject else ''}{label} on {_date_label(when)}" if when else f"{name} has {label} soon"
                    cands.append(_cand("event", content, "contact", "contact_self", s, when, 2, 0.8))
                # Prefer the day named after "visit" ("exam on Friday and I'll visit on Sunday" -> Sunday).
                if re.search(r"\bvisit", low) and (m := re.search(r"visit\w*(?:\s+[\w,]+){0,3}?\s+" + _DAY, s, re.I)
                                                    or re.search(_DAY, s, re.I)):
                    when = _resolve_day(m.group(1), call_date)
                    if when:
                        cands.append(_cand("plan", f"{name} will visit {patient_name} on {_date_label(when)}",
                                           "contact", "contact_self", s, when, 1, 0.85))
                        recap_bits.append(f"{name} is coming to visit on {when.strftime('%A')}.")
                if m := re.search(r"\bmy (dog|cat|puppy|kitten)(?:'s name is| is called| named)? ([A-Z]\w+)", s):
                    cands.append(_cand("fact", f"{name} has a {m.group(1)} named {m.group(2)}", "contact", "contact_self", s))
                if m := re.search(r"\b(?:new job|started working|joined) (?:at|with) ([A-Z][\w ]+)", s):
                    cands.append(_cand("fact", f"{name} works at {m.group(1).strip()}", "contact", "contact_self", s, importance=0.7))
                if m := re.search(r"\bi (?:really )?(?:love|like|enjoy) ([\w ]+)", s, re.I):
                    cands.append(_cand("preference", f"{name} enjoys {m.group(1).strip()}", "contact", "contact_self", s,
                                       importance=0.5, confidence=0.7))
            else:
                if m := re.search(rf"\b(?:you are|you're|{re.escape(name)} is) my ({RELATIONS})\b", s, re.I):
                    cands.append(_cand("relationship", f"{name} is {patient_name}'s {m.group(1).lower()}", "patient",
                                       "other", s, importance=0.9, confidence=0.5))
                if m := re.search(r"\b(?:you|she|he) (?:still )?lives? in ([A-Z]\w+)", s):
                    cands.append(_cand("fact", f"{name} lives in {m.group(1)}", "patient", "other", s, confidence=0.4))

    shareable = [c["content"] for c in cands if not c["sensitive"] and c["speaker"] == "contact"]
    summary = (f"{name} shared: " + "; ".join(shareable) + ".") if shareable else (" ".join(said[:2]) or f"Short call with {name}.")
    recap = f"You just talked to {name}. " + (" ".join(recap_bits) if recap_bits else "It was lovely to hear from them.")
    return {"summary": summary[:400], "patient_recap": recap, "candidates": cands}


def _subject_key(content: str) -> str:
    low = content.lower()
    for key in ("lives in", "exam", "visit", "works at", "named", "'s "):
        if key in low:
            return key
    return ""


def reconcile(items: list[dict]) -> dict:
    """items: [{index, candidate, trust, neighbours: [{id, content, type, trust, similarity}]}]"""
    decisions = []
    for it in items:
        cand = it["candidate"]
        key = _subject_key(cand["content"])
        decision = {"candidate_index": it["index"], "action": "ADD", "target_id": None, "merged_content": None,
                    "reason": "new information"}
        for n in it["neighbours"]:
            if n["content"].strip().lower() == cand["content"].strip().lower() or n["similarity"] > 0.97:
                decision.update(action="IGNORE", target_id=n["id"], reason="duplicate of an existing memory")
                break
            if n["type"] == cand["type"] and key and key == _subject_key(n["content"]) and cand["type"] in ("fact", "relationship"):
                # Proposed as SUPERSEDE; the pipeline's trust guard turns it into a CONFLICT when the source may not override.
                decision.update(action="SUPERSEDE", target_id=n["id"], reason="newer information about the same thing")
                break
            if n["type"] == cand["type"] and key and key == _subject_key(n["content"]) and cand["type"] in ("event", "plan"):
                decision.update(action="UPDATE", target_id=n["id"], merged_content=cand["content"],
                                reason="same event, refreshed details")
                break
        decisions.append(decision)
    return {"decisions": decisions}


def _event_noun(content: str, name: str) -> str:
    m = re.search(r"has (?:a |an )?(.+?) on ", content)
    return m.group(1) if m else content.replace(f"{name} has ", "")


def brief(contact: dict, items: list[dict], last_calls: list[dict], now: datetime, patient_name: str = "") -> dict:
    name = contact["name"]
    you = lambda text: text.replace(f" {patient_name}", " you") if patient_name else text
    rel = next((i for i in items if i["type"] == "relationship"), None)
    lines = [{"icon": "person", "text": f"{name} is your {contact.get('relation', 'family')}.",
              "memory_ids": [rel["id"]] if rel else []}]
    suggestions = []

    # Open loops first: they are what makes this call different from the last one.
    for it in sorted((i for i in items if i["type"] in ("event", "plan") and i.get("event_date")),
                     key=lambda i: i["event_date"]):
        if it["event_date"] <= now:
            if it["type"] == "event":
                suggestions.append(f"How did your {_event_noun(it['content'], name)} go?")
        else:
            lines.append({"icon": "calendar", "text": you(it["content"]) + ".", "memory_ids": [it["id"]]})
            if it["type"] == "event" and len(suggestions) < 2:
                suggestions.append(f"All the best for your {_event_noun(it['content'], name)}!")
    if last_calls and last_calls[0].get("recap"):
        # The recap is written for the patient and never contains private items (the raw summary might).
        c = last_calls[0]
        recap = c["recap"].replace("You just talked to", "You talked to")
        lines.append({"icon": "chat", "text": f"Last call, {c['started_at'].strftime('%A')}: {recap}"[:160], "memory_ids": []})
    for it in sorted((i for i in items if i["type"] == "fact"), key=lambda i: "lives" not in i["content"]):
        if len(lines) >= 5:
            break
        lines.append({"icon": "place" if "lives" in it["content"] else "star", "text": you(it["content"]) + ".",
                      "memory_ids": [it["id"]]})
    for it in items:
        if it["type"] in ("preference", "topic") and len(suggestions) < 2:
            suggestions.append(f"Tell me about {it['content'].replace(name + ' enjoys ', '')}")
    lines = lines[:5]
    suggestions = suggestions[:2]
    speech = " ".join(l["text"] for l in lines[:3])
    if suggestions:
        speech += f" You could ask: {suggestions[0]}"
    return {"headline": f"{name} — your {contact.get('relation', 'family')}", "lines": lines,
            "suggestions": suggestions, "speech": speech}
