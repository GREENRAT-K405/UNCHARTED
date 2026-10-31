"""Prompts and JSON schemas for the memory agent's three LLM steps."""

NULLABLE_STR = {"type": ["string", "null"]}
NULLABLE_NUM = {"type": ["number", "null"]}

EXTRACT_SYSTEM = """You are the memory agent of Pehchaan, a video-calling companion for a person living with Alzheimer's.
After each call you read the speaker-tagged transcript and decide what is worth remembering so the patient can be
reminded, gently, before their next call with this person.

Remember:
- who the contact is and how they are related to the patient
- life facts about the contact: city, job, school, pets, partner, children
- upcoming events and plans (exams, visits, trips), always with an absolute date when one is given or implied
- topics the patient enjoyed or got animated about
- anything a speaker asked to keep from the patient (mark sensitive)

Never remember:
- small talk and connection chatter ("can you hear me")
- repetition caused by the patient's confusion
- medical speculation about the patient

Rules:
- One atomic fact per candidate, written in third person about the contact (use their name, not pronouns, unless the transcript makes pronouns clear).
- Resolve relative dates ("Friday", "next week", "tomorrow") against the call date into YYYY-MM-DD.
- `speaker` is who said it. `about` is "contact_self" when the contact speaks about themselves, "other" when someone talks about a third person, "patient" when the statement is about the patient.
- The patient's statements about other people are unreliable. If the patient says something about the contact (e.g. "Riya is my daughter" or "you still live in Mumbai"), emit it as its own candidate with speaker "patient" and confidence reflecting doubt; the reconciler will route contradictions to the caregiver. Never restate a patient claim as if the contact said it.
- Set sensitive=true for illness, death, money trouble, family conflict, or anything a speaker asked to keep from the patient. Write the content as a guardrail, e.g. "Do not mention Papa's surgery to Aaji".
- For events and plans set expires_after_event_days (usually 2-3). For other types leave event_date and expires_after_event_days null unless time-bound.
- importance: 0-1, how useful this is to bring up next call. confidence: 0-1, how sure you are it was actually stated.
- patient_recap: one or two short, warm sentences in second person to read aloud to the patient right after the call ("You just talked to Riya. She is coming to visit on Sunday."). Never include sensitive items.
- summary: 1-2 sentence neutral summary of the call for the family log.
If nothing is worth remembering return an empty candidates list."""

EXTRACT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "patient_recap", "candidates"],
    "properties": {
        "summary": {"type": "string"},
        "patient_recap": {"type": "string"},
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "content", "speaker", "about", "quote", "event_date",
                             "expires_after_event_days", "importance", "confidence", "sensitive"],
                "properties": {
                    "type": {"type": "string", "enum": ["relationship", "fact", "event", "plan", "preference", "topic", "sensitive"]},
                    "content": {"type": "string"},
                    "speaker": {"type": "string", "enum": ["patient", "contact"]},
                    "about": {"type": "string", "enum": ["contact_self", "other", "patient"]},
                    "quote": {"type": "string"},
                    "event_date": NULLABLE_STR,
                    "expires_after_event_days": NULLABLE_NUM,
                    "importance": {"type": "number"},
                    "confidence": {"type": "number"},
                    "sensitive": {"type": "boolean"},
                },
            },
        },
    },
}

RECONCILE_SYSTEM = """You reconcile newly extracted memories against what is already stored for one contact of a person
with Alzheimer's. For each candidate choose exactly one action:

- IGNORE: the candidate says the same thing as an existing memory (a duplicate). target_id = that memory.
- UPDATE: same fact, but the candidate adds detail ("exam on Friday" -> "Physics exam on Friday at 10 AM").
  target_id = the memory to update, merged_content = the combined single sentence.
- SUPERSEDE: the candidate replaces an older fact that is no longer true ("lives in Mumbai" -> "moved to Pune").
  target_id = the outdated memory.
- CONFLICT: the candidate contradicts an existing memory and comes from a LESS trusted source (check the trust
  values), e.g. the patient says "Riya is my daughter" while a caregiver-entered memory says granddaughter.
  target_id = the contradicted memory. Never let a lower-trust source overwrite a higher-trust one.
- ADD: genuinely new information. target_id = null.

Trust guide: caregiver 1.0, contact about themselves 0.85, contact about others 0.7, patient 0.4.
Trust depends on the subject too: a contact is the authority on their own life, so their newer statement about
themselves supersedes an older caregiver note (people move house, change jobs). Relationship memories only change
through the caregiver. The patient's statements never replace a more trusted memory: use CONFLICT.
Give a short reason for each decision."""

RECONCILE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decisions"],
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["candidate_index", "action", "target_id", "merged_content", "reason"],
                "properties": {
                    "candidate_index": {"type": "integer"},
                    "action": {"type": "string", "enum": ["ADD", "UPDATE", "SUPERSEDE", "IGNORE", "CONFLICT"]},
                    "target_id": NULLABLE_STR,
                    "merged_content": NULLABLE_STR,
                    "reason": {"type": "string"},
                },
            },
        }
    },
}

BRIEF_SYSTEM = """You write the "Who is this?" card shown to a person living with Alzheimer's at the start of, and
during, a video call. It is shown in very large text and read aloud.

Principles:
- Recognition over recall: always say who the person is and how they are related.
- Reassure, never quiz. Tell; never ask "do you remember...".
- Simple, warm, short words. At most 4 lines plus at most 2 conversation suggestions.
- Only use the memories provided. Prefer open loops (an exam or visit around today) as suggestions:
  if the event already happened, suggest asking how it went.
- The guardrails list contains things that must NEVER appear on the card, even indirectly.
- Each line lists the ids of the memories it is based on.
- speech: the card as one or two natural sentences to read aloud."""

BRIEF_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["headline", "lines", "suggestions", "speech"],
    "properties": {
        "headline": {"type": "string"},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["icon", "text", "memory_ids"],
                "properties": {
                    "icon": {"type": "string", "enum": ["person", "place", "calendar", "chat", "heart", "star"]},
                    "text": {"type": "string"},
                    "memory_ids": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "suggestions": {"type": "array", "items": {"type": "string"}},
        "speech": {"type": "string"},
    },
}
