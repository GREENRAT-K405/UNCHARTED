"""Demo family: six weeks of believable history for Aaji, written through the real memory code.

Everything goes through memory_store (so supersede chains, audit events, embeddings and
TTL expiry are real) and briefs are produced by the real retrieval/re-ranking code, with
the clock pinned to each historical moment via clock.frozen().

Riya is kept "demo-safe": her caregiver notes (lives in Mumbai, dog Bruno) are untouched
and her last call was days ago, so the live demo script in the README still works.
"""
import asyncio
import random
import secrets
from datetime import date, datetime, time, timedelta

from bson import ObjectId

from . import clock, db, embeddings
from . import memory_store as ms
from .agent import brief as brief_mod
from .config import settings

TZ = settings.tz
HISTORY_DAYS = 42
BRIEFED_CALLS = 30  # build real briefs for the most recent N calls

FAMILY_USERS = [  # seeded caregivers (cannot sign in; they make the activity log look lived-in)
    {"key": "rahul", "name": "Rahul Patil", "email": "rahul.patil@demo.pehchaan.app", "relation": "son", "role": "owner"},
    {"key": "sunita", "name": "Sunita Kulkarni", "email": "sunita.k@demo.pehchaan.app", "relation": "daughter", "role": "family"},
    {"key": "meera", "name": "Meera Patil", "email": "meera.patil@demo.pehchaan.app", "relation": "daughter-in-law", "role": "family"},
    {"key": "riya", "name": "Riya Patil", "email": "riya.patil@demo.pehchaan.app", "relation": "granddaughter", "role": "viewer"},
]

# freq: calls per week; hours: preferred local call hours; skip_recent: days with no calls (demo safety)
CONTACTS = [
    {"key": "rahul", "name": "Rahul", "nicknames": ["beta", "my son"], "relation": "son", "color": "#2f6f4f",
     "phone": "+91 98200 00001", "primary": True, "freq": 6, "hours": [18, 19, 20],
     "notes": ["Rahul works night shifts as a nurse at Sassoon Hospital", "Rahul lives ten minutes away in Kothrud"],
     "constraints": [
         ("Rahul sleeps 9 AM - 3 PM after night shifts; no calls then", {"kind": "quiet_hours", "start": "09:00", "end": "15:00"}),
         ("Don't let Aaji call Rahul more than 3 times a day", {"kind": "max_calls_per_day", "value": 3})]},
    {"key": "sunita", "name": "Sunita", "nicknames": ["Sunu", "my daughter"], "relation": "daughter",
     "detail": "lives in Nashik", "color": "#7a4fa0", "phone": "+91 98200 00005", "freq": 3.5, "hours": [8, 13, 21],
     "notes": ["Sunita lives in Nashik with Vikram and Kavya", "Sunita works as a cashier at SBI, Nashik"]},
    {"key": "meera", "name": "Meera", "nicknames": ["sunbai", "my daughter-in-law"], "relation": "daughter-in-law",
     "detail": "Rahul's wife", "color": "#b5651d", "phone": "+91 98200 00003", "freq": 2, "hours": [16, 17],
     "notes": ["Meera teaches mathematics at a school", "Meera loves cooking puran poli with Aaji"]},
    {"key": "riya", "name": "Riya", "nicknames": ["Riyu", "my granddaughter"], "relation": "granddaughter",
     "detail": "Rahul's daughter", "color": "#9b4d8a", "phone": "+91 98200 00002", "freq": 1.5, "hours": [20, 21],
     "skip_recent": 3,
     "notes": ["Riya lives in Mumbai", "Riya has a dog named Bruno", "Riya studies engineering in college"],
     "constraints": [("Wait at least an hour between calls to Riya", {"kind": "min_gap_minutes", "value": 60})]},
    {"key": "arjun", "name": "Arjun", "nicknames": ["my grandson"], "relation": "grandson", "detail": "Rahul's son",
     "color": "#2b5d8a", "phone": "+91 98200 00004", "freq": 1.3, "hours": [17, 18],
     "notes": ["Arjun is in 8th standard", "Arjun plays football for his school team"]},
    {"key": "anil", "name": "Anil", "known_as": "Anil bhau", "nicknames": ["Anil bhau", "my brother"], "relation": "younger brother",
     "detail": "lives in Kolhapur", "color": "#8a5a2b", "phone": "+91 98200 00006", "freq": 1, "hours": [10, 11],
     "weekday": 6, "notes": ["Anil lives in Kolhapur"]},
    {"key": "lata", "name": "Lata", "known_as": "Lata tai", "nicknames": ["Lata tai", "my friend"], "relation": "friend",
     "detail": "neighbour, bhajan group", "color": "#a0405a", "phone": "+91 98200 00007", "freq": 2.5, "hours": [10, 11, 12],
     "notes": ["Lata is Aaji's friend from the bhajan group and lives next door"]},
    {"key": "kavya", "name": "Kavya", "nicknames": ["chimni", "little Kavya"], "relation": "granddaughter",
     "detail": "Sunita's daughter", "color": "#c0598a", "phone": "+91 98200 00008", "freq": 0.8, "hours": [18],
     "notes": ["Kavya is 6 years old"]},
    {"key": "vikram", "name": "Vikram", "nicknames": ["jawai", "my son-in-law"], "relation": "son-in-law",
     "detail": "Sunita's husband", "color": "#4a6a8a", "phone": "+91 98200 00009", "freq": 0.5, "hours": [21],
     "notes": ["Vikram runs a pharmacy in Nashik"]},
    {"key": "doctor", "name": "Dr. Deshpande", "nicknames": ["doctor", "the doctor"], "relation": "family doctor",
     "color": "#3d6f73", "phone": "+91 20 2543 0000", "freq": 0.3, "hours": [11],
     "notes": ["Dr. Deshpande has been Aaji's family doctor for 20 years"]},
]

# (days_ago, contact, type, content, quote, options)
# options: speaker, supersedes (content), event_in (days from today), confidence, status, sensitive
BEATS = [
    (38, "arjun", "fact", "Arjun switched from football to cricket", "I don't play football any more Aaji, only cricket now!",
     {"supersedes": "Arjun plays football for his school team"}),
    (33, "riya", "preference", "Riya loves singing old Kishore Kumar songs with Aaji", "Aaji, let's sing 'Pal pal dil ke paas' again!", {}),
    (31, "lata", "fact", "Lata's grandson Ishaan lives in Bengaluru", "Ishaan is still in Bengaluru, working very hard.", {"speaker_about": "other"}),
    (30, "sunita", "fact", "Sunita was promoted to branch manager at SBI Nashik", "Aai, I got promoted! I'm branch manager now.",
     {"supersedes": "Sunita works as a cashier at SBI, Nashik"}),
    (29, "anil", "fact", "Anil is recovering from a knee operation", "The knee is still stiff, but the doctor says it went well.", {}),
    (27, "vikram", "fact", "Vikram's pharmacy is closed for renovation", "We've shut the shop for a few weeks to redo the shelves.", {}),
    (26, "anil", "fact", "Anil lives in Mumbai", "Anil, you are still in Mumbai, no?",
     {"speaker": "patient", "conflict": "Anil lives in Kolhapur", "status": "rejected"}),
    (25, "kavya", "fact", "Kavya started Bharatanatyam classes", "Aaji, I went to dance class! We did namaskar first.", {}),
    (24, "anil", "sensitive", "Do not mention to Aaji that Anil's wife Shobha is in hospital",
     "Please don't tell Tai about Shobha being in hospital, she will worry.", {"speaker_about": "other"}),
    (22, "meera", "fact", "Meera was made class teacher of 9th standard", "They've made me class teacher for 9th this year.", {}),
    (21, "anil", "preference", "Anil enjoys remembering the wrestling akhada in Kolhapur from their childhood",
     "Remember Baba taking us to the akhada every morning?", {}),
    (20, "arjun", "fact", "Arjun was selected as wicket-keeper for the school cricket team", "I'm the wicket-keeper now, Aaji!", {}),
    (19, "riya", "fact", "Riya's dog Bruno learned to shake hands", "Bruno can shake hands now, I'll show you on video!", {}),
    (18, "sunita", "sensitive", "Do not mention Sunita's health check-up results to Aaji",
     "Don't tell Aai about my reports, it's nothing serious.", {}),
    (17, "anil", "fact", "Anil lives in Mumbai", "Anil is in Mumbai these days, isn't he?",
     {"speaker": "patient", "conflict": "Anil lives in Kolhapur", "status": "rejected"}),
    (16, "rahul", "plan", "Rahul will take Aaji to Dr. Deshpande on {date} at 11 AM", "I've booked Dr. Deshpande for you, I'll take you.",
     {"event_in": 2}),
    (14, "kavya", "preference", "Kavya loves drawing elephants for Aaji", "I drew you an elephant with a crown!", {}),
    (13, "anil", "fact", "Anil's knee has healed and he walks to the market again", "I walked to the market today, no stick!",
     {"supersedes": "Anil is recovering from a knee operation"}),
    (12, "lata", "fact", "Lata's grandson Ishaan moved to Germany for work", "Ishaan has gone to Germany, Munich, for his new job.",
     {"supersedes": "Lata's grandson Ishaan lives in Bengaluru", "speaker_about": "other"}),
    (12, "sunita", "event", "Sunita and Vikram's 20th wedding anniversary is on {date}", "Our twentieth anniversary is coming, Aai!",
     {"event_in": 5}),
    (11, "rahul", "fact", "Rahul bought a new scooter", "I finally bought the new scooter, it's grey.", {}),
    (10, "vikram", "fact", "Vikram's pharmacy reopened after the renovation", "The shop is open again, it looks very nice now.",
     {"supersedes": "Vikram's pharmacy is closed for renovation"}),
    (9, "arjun", "event", "Arjun has a maths unit test on {date}", "I have a maths test, Aaji, pray for me!", {"event_in": -1}),
    (8, "meera", "event", "Meera's school annual day is on {date}", "Our school annual day is next week, I'm organising the play.",
     {"event_in": 3}),
    (7, "kavya", "event", "Kavya's first Bharatanatyam recital is on {date}", "My dance show is coming, you have to watch the video!",
     {"event_in": 9}),
    (6, "sunita", "preference", "Sunita loves hearing Aaji's old recipes, especially ukadiche modak",
     "Tell me again how you make the modak, Aai.", {}),
    (5, "vikram", "fact", "Vikram may open a second pharmacy in Satara", "We're thinking about Satara, maybe, let's see.",
     {"confidence": 0.5}),
    (5, "riya", "topic", "Riya talked about the food in her college hostel", "The hostel food is so bland, I miss your varan bhaat!", {}),
    (4, "arjun", "event", "Arjun's inter-school cricket final is on {date}", "We reached the final! It's on {weekday}.", {"event_in": 4}),
    (4, "anil", "fact", "Anil lives in Mumbai", "How is Mumbai, Anil?",
     {"speaker": "patient", "conflict": "Anil lives in Kolhapur", "status": "pending_review"}),
    (3, "arjun", "fact", "Arjun is in 5th standard", "Arjun, you are in 5th now, right?",
     {"speaker": "patient", "conflict": "Arjun is in 8th standard", "status": "pending_review"}),
    (3, "meera", "preference", "Meera wants to learn Aaji's puran poli recipe before Diwali", "Teach me your puran poli before Diwali, please!", {}),
    (2, "lata", "plan", "Lata will visit with homemade chakli on {date}", "I'll bring chakli on {weekday}, keep the tea ready!", {"event_in": 1}),
    (2, "rahul", "topic", "Aaji and Rahul talked about the tulsi plant on the balcony", "Is the tulsi getting enough sun on the balcony?", {}),
    (1, "anil", "plan", "Anil will come to Pune for Diwali on {date}", "I'm coming for Diwali, Tai, I've booked the bus.", {"event_in": 8}),
]

FILLER = {
    "rahul": [("contact", "Did you eat on time, Aai?"), ("patient", "Yes, I had poha."), ("contact", "Good. Take your evening tablet after dinner."),
              ("patient", "The tulsi has new leaves."), ("contact", "I'll come by after my shift tomorrow.")],
    "sunita": [("contact", "Aai, how was your morning walk?"), ("patient", "Lata and I walked to the temple."),
               ("contact", "Kavya keeps asking about you."), ("patient", "Tell her Aaji is waiting for her drawing.")],
    "meera": [("contact", "I made usal today, thinking of you."), ("patient", "Add a little more goda masala next time."),
              ("contact", "Arjun is studying, he'll call you later.")],
    "riya": [("contact", "Aaji! College is so busy this week."), ("patient", "Are you eating properly?"),
             ("contact", "Bruno says hello, he misses you."), ("patient", "Give him a biscuit from me.")],
    "arjun": [("contact", "Aaji, we practised for two hours today!"), ("patient", "Wear your cap in the sun."),
              ("contact", "Coach says my catching is better now.")],
    "anil": [("contact", "Tai, how is your health?"), ("patient", "Fine, fine. How is Kolhapur?"),
             ("contact", "Same as always, the mandai is very crowded.")],
    "lata": [("contact", "Sunanda, are you coming for bhajan on Thursday?"), ("patient", "Of course, I'll wear the green saree."),
             ("contact", "I'll bring the harmonium book.")],
    "kavya": [("contact", "Aaji, I lost a tooth!"), ("patient", "Oh! Put it under your pillow."), ("contact", "I'm drawing you a peacock.")],
    "vikram": [("contact", "Namaskar Aai, all well?"), ("patient", "Yes, how is the shop?"), ("contact", "Busy, but good.")],
    "doctor": [("contact", "Namaskar Sunandatai, how is the sleep these days?"), ("patient", "Better, doctor."),
               ("contact", "Keep walking every morning, that helps.")],
}

TOPICS = {
    "rahul": ["Aaji told Rahul about her morning walk with Lata", "Rahul reminded Aaji about her evening tablet"],
    "sunita": ["Sunita and Aaji talked about Kavya's school", "Aaji told Sunita about the temple visit"],
    "meera": ["Meera and Aaji compared usal recipes"],
    "riya": ["Riya told Aaji about her college fest"],
    "arjun": ["Arjun told Aaji about cricket practice"],
    "anil": ["Anil and Aaji talked about Kolhapur's mandai"],
    "lata": ["Aaji and Lata planned Thursday's bhajan"],
    "kavya": ["Kavya told Aaji she lost a tooth"],
    "vikram": ["Vikram told Aaji the shop is busy"],
    "doctor": ["Dr. Deshpande asked Aaji about her sleep"],
}

GLOBAL_MEMORIES = [
    ("sensitive", "Do not mention that the old Nashik house was sold", "never_surface"),
    ("sensitive", "Do not bring up Minu, Aaji's cat who died last year", "never_surface"),
    ("preference", "Aaji enjoys talking about her balcony garden and old Marathi films", "patient"),
]

PENDING_CONTACT = {"name": "Ramesh", "days_ago": 1}

# Aaji's late husband: archived, so his tile is hidden, calls are refused gently, his memories are
# caregiver_only, and the family's note on how to answer reaches relatives' screens.
ARCHIVED = {
    "name": "Madhav", "nicknames": ["Baba", "my husband", "Aho"], "relation": "husband",
    "detail": "passed away in March 2021", "color": "#6b5a49",
    "note": "Madhav (Baba) passed away in March 2021. If Aaji asks for him, don't correct her or break the news "
            "again. Say \"Tell me about him\" and let her talk about the abhangs he sang every morning, then gently "
            "move on to today.",
    "memories": ["Madhav sang abhangs every morning before breakfast", "Madhav and Aaji were married for 48 years"],
    "asked_days_ago": [9, 3],  # Aaji asked by voice to call him; the guard refused gently
}


def _local(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=TZ)


def _label(d: date) -> str:
    return f"{d.strftime('%A')} {d.day} {d.strftime('%b')}"


async def _wipe(patient_id: ObjectId):
    call_ids = [c["_id"] async for c in db.calls.find({"patient_id": patient_id}, {"_id": 1})]
    await db.transcript_chunks.delete_many({"call_id": {"$in": call_ids}})
    for coll in (db.calls, db.contacts, db.devices, db.auth_events, db.contact_updates):
        await coll.delete_many({"patient_id": patient_id})
    for coll in (db.memories, db.briefs, db.memory_events):
        await coll.delete_many({"owner_id": patient_id})
    seeded = [u["_id"] async for u in db.users.find({"seeded": True, "role": "caregiver"})]
    await db.memberships.delete_many({"patient_id": patient_id, "user_id": {"$in": seeded}})
    await db.users.delete_many({"_id": {"$in": seeded}})


_seed_lock = asyncio.Lock()


async def seed_demo() -> dict:
    """Serialised: two overlapping runs (a double-clicked Reset demo, or a reset during the startup seed)
    would each insert a full set of contacts and leave every person on the patient's screen twice."""
    async with _seed_lock:
        return await _seed_demo()


async def _seed_demo() -> dict:
    rng = random.Random(42)
    now = clock.now()
    today = now.astimezone(TZ).date()
    start = today - timedelta(days=HISTORY_DAYS)
    setup_at = _local(start, 9, 30)

    patient = await db.users.find_one({"role": "patient", "demo": True})
    # Contact links survive a demo reset, so a link already open on a relative's phone keeps working.
    old_tokens = {c["name"]: c["link_token"] async for c in db.contacts.find(
        {"patient_id": patient["_id"], "link_token": {"$exists": True}})} if patient else {}
    fields = {"name": "Aaji", "full_name": "Sunanda Patil", "role": "patient", "demo": True, "language": "en-IN",
              "age": 74, "city": "Pune", "created_at": setup_at}
    if patient:
        await _wipe(patient["_id"])
        await db.users.update_one({"_id": patient["_id"]}, {"$set": fields})
        pid = patient["_id"]
    else:
        pid = (await db.users.insert_one(fields)).inserted_id

    # Embed every string we will store in one batched pass (one request per 128 with Voyage).
    texts = set()
    for c in CONTACTS:
        texts.update(c["notes"])
        texts.update(t for t, _ in c.get("constraints", []))
        texts.update(TOPICS.get(c["key"], []))
    for _, _, _, content, _, opts in BEATS:
        texts.add(_fill(content, opts, today))
    texts.update(t for _, t, _ in GLOBAL_MEMORIES)
    texts.update(ARCHIVED["memories"])
    texts.add(f"{ARCHIVED['name']} was Aaji's {ARCHIVED['relation']}")
    rel_texts = {c["key"]: f"{c['name']} is Aaji's {c['relation']}" + (f" ({c['detail']})" if c.get("detail") else "")
                 for c in CONTACTS}
    texts.update(rel_texts.values())
    texts = sorted(texts)
    vectors: dict[str, list[float]] = {}
    for i in range(0, len(texts), 128):
        chunk = texts[i:i + 128]
        vectors.update(zip(chunk, await embeddings.embed(chunk)))

    # ---- caregivers, devices, contacts and caregiver-entered memories (six weeks ago)
    users = {}
    with clock.frozen(setup_at):
        for i, u in enumerate(FAMILY_USERS):
            doc = {"name": u["name"], "email": u["email"], "role": "caregiver", "provider": "google.com",
                   "seeded": True, "created_at": setup_at + timedelta(minutes=i * 7),
                   "last_seen": now - timedelta(hours=rng.randint(1, 60))}
            users[u["key"]] = (await db.users.insert_one(doc)).inserted_id
            await db.memberships.insert_one({"patient_id": pid, "user_id": users[u["key"]], "role": u["role"],
                                             "relation": u["relation"], "created_at": doc["created_at"]})
        await db.devices.insert_one({"firebase_uid": f"seed-device-{pid}", "patient_id": pid, "label": "Aaji's tablet",
                                     "paired_by": users["rahul"], "paired_by_name": "Rahul Patil", "paired_at": setup_at,
                                     "last_seen": now - timedelta(minutes=rng.randint(5, 50)), "revoked": False})
        await db.auth_events.insert_one({"kind": "circle_created", "patient_id": pid, "user_id": users["rahul"],
                                         "who": "Rahul Patil", "provider": "google.com", "ts": setup_at})
        await db.auth_events.insert_one({"kind": "device_paired", "patient_id": pid, "user_id": users["rahul"],
                                         "who": "Rahul Patil", "provider": "google.com", "label": "Aaji's tablet",
                                         "ts": setup_at + timedelta(minutes=20)})

        contacts: dict[str, dict] = {}
        by_content: dict[str, dict] = {}
        caregiver_src = {"kind": "caregiver", "origin": "caregiver", "by": "Rahul Patil"}
        for order, c in enumerate(CONTACTS):
            doc = {"patient_id": pid, "name": c["name"], "known_as": c.get("known_as"), "nicknames": c["nicknames"],
                   "relation": c["relation"], "relation_detail": c.get("detail"), "phone": c["phone"], "photo": None,
                   "color": c["color"], "status": "active", "primary_caregiver": c.get("primary", False),
                   "link_token": old_tokens.get(c["name"]) or secrets.token_urlsafe(12),
                   "created_at": setup_at + timedelta(seconds=order), "added_by": "Rahul Patil"}
            doc["_id"] = (await db.contacts.insert_one(doc)).inserted_id
            contacts[c["key"]] = doc
            m = await ms.insert_memory(owner_id=pid, contact_id=doc["_id"], type="relationship",
                                       content=rel_texts[c["key"]], source=caregiver_src, trust=1.0, importance=1.0,
                                       actor="Rahul Patil", embedding=vectors[rel_texts[c["key"]]])
            by_content[m["content"]] = m
            for note in c["notes"]:
                m = await ms.insert_memory(owner_id=pid, contact_id=doc["_id"], type="fact", content=note,
                                           source=caregiver_src, trust=1.0, importance=0.6, actor="Rahul Patil",
                                           embedding=vectors[note])
                by_content[note] = m
            for text, rule in c.get("constraints", []):
                await ms.insert_memory(owner_id=pid, contact_id=doc["_id"], type="constraint", content=text,
                                       source=caregiver_src, trust=1.0, importance=1.0, visibility="caregiver_only",
                                       rule=rule, actor="Rahul Patil", embedding=vectors[text])
        a = ARCHIVED
        late = {"patient_id": pid, "name": a["name"], "known_as": None, "nicknames": a["nicknames"],
                "relation": a["relation"], "relation_detail": a["detail"], "phone": None, "photo": None,
                "color": a["color"], "status": "archived", "primary_caregiver": False,
                "archive_note": a["note"], "archived_at": setup_at, "archived_by": "Rahul Patil",
                "created_at": setup_at + timedelta(seconds=len(CONTACTS)), "added_by": "Rahul Patil"}
        late["_id"] = (await db.contacts.insert_one(late)).inserted_id
        rel_text = f"{a['name']} was Aaji's {a['relation']}"
        for mtype, text in [("relationship", rel_text)] + [("fact", t) for t in a["memories"]]:
            await ms.insert_memory(owner_id=pid, contact_id=late["_id"], type=mtype, content=text, source=caregiver_src,
                                   trust=1.0, importance=1.0 if mtype == "relationship" else 0.6,
                                   visibility="caregiver_only", actor="Rahul Patil", embedding=vectors[text])
        await db.memories.update_many({"contact_id": late["_id"]}, {"$set": {"visibility_before_archive": "patient"}})
        for mtype, text, vis in GLOBAL_MEMORIES:
            await ms.insert_memory(owner_id=pid, contact_id=None, type=mtype, content=text, source=caregiver_src,
                                   trust=1.0, importance=1.0 if mtype == "sensitive" else 0.6, visibility=vis,
                                   actor="Sunita Kulkarni" if "Minu" in text else "Rahul Patil", embedding=vectors[text])

    # ---- call schedule
    keys = {c["key"]: c for c in CONTACTS}
    schedule: list[tuple[datetime, str]] = []
    beat_days = {(d, k) for d, k, *_ in BEATS}
    rahul_today = 0
    for days_ago in range(HISTORY_DAYS - 1, -1, -1):
        day = today - timedelta(days=days_ago)
        for c in CONTACTS:
            if days_ago < c.get("skip_recent", 0):
                continue
            wanted = (days_ago, c["key"]) in beat_days
            if "weekday" in c:
                wanted = wanted or day.weekday() == c["weekday"]
            else:
                wanted = wanted or rng.random() < c["freq"] / 7
            if not wanted:
                continue
            at = _local(day, rng.choice(c["hours"]), rng.randint(0, 55))
            if days_ago == 0:
                if at > now - timedelta(minutes=90) or (c["key"] == "rahul" and rahul_today >= 1):
                    continue
                rahul_today += c["key"] == "rahul"
            schedule.append((at, c["key"]))
            if c["key"] == "rahul" and days_ago > 0 and rng.random() < 0.35:  # second evening call
                schedule.append((at + timedelta(minutes=rng.randint(70, 110)), "rahul"))
    schedule.sort()

    # Blocked attempts (repeat-call guard): cluster in the late afternoon / evening (sundowning).
    blocked = []
    for days_ago in range(HISTORY_DAYS - 1, 0, -1):
        day = today - timedelta(days=days_ago)
        for _ in range(rng.choices([0, 1, 2, 3], weights=[3, 4, 2, 1])[0]):
            hour = rng.choices([10, 12, 17, 18, 19, 20], weights=[2, 1, 4, 5, 4, 2])[0]
            key = rng.choices(["rahul", "riya", "sunita"], weights=[6, 3, 1])[0]
            blocked.append((_local(day, hour, rng.randint(0, 59)), key))

    beats_by = {}
    for b in BEATS:
        beats_by.setdefault((b[0], b[1]), []).append(b)

    # ---- replay history call by call
    first_briefed = max(0, len(schedule) - BRIEFED_CALLS)
    call_count = 0
    for idx, (at, key) in enumerate(schedule):
        c = keys[key]
        contact = contacts[key]
        days_ago = (today - at.astimezone(TZ).date()).days
        duration = max(90, int(rng.gauss(560, 260)))
        ended = at + timedelta(seconds=duration)
        overrode = rng.random() < 0.07
        call = {"patient_id": pid, "contact_id": contact["_id"], "status": "processed", "started_at": at,
                "answered_at": at + timedelta(seconds=rng.randint(4, 25)), "ended_at": ended, "duration_sec": duration,
                "processed_at": ended + timedelta(seconds=rng.randint(6, 20)), "initiated_by": "patient",
                "emergency": False, "off_record": rng.random() < 0.05, "seeded": True,
                "guard": {"allowed": False, "can_override": True, "reason": "Default: 30 minutes between calls",
                          "message": f"You spoke to {c['name']} a little while ago. Do you still want to call?"} if overrode else None}
        call["_id"] = (await db.calls.insert_one(call)).inserted_id
        call_count += 1

        if idx >= first_briefed:
            with clock.frozen(at):
                await brief_mod.build_brief(pid, contact, call["_id"], compose=False)

        lines = [("patient", f"Hello? {c['name']}?"), ("contact", f"Namaskar Aaji, it's {c['name']}!" if key != "doctor"
                                                       else "Namaskar Sunandatai, Dr. Deshpande here.")]
        filler = FILLER[key][:]
        rng.shuffle(filler)
        lines += filler[:rng.randint(2, len(filler))]
        actions, shared = [], []
        with clock.frozen(ended + timedelta(seconds=8)):
            for (_, _, mtype, content, quote, opts) in beats_by.get((days_ago, key), []):
                text = _fill(content, opts, today)
                speaker = opts.get("speaker", "contact")
                lines.insert(rng.randint(2, len(lines)), (speaker, _fill(quote, opts, today)))
                actions.append(await _apply_beat(pid, contact, call["_id"], mtype, text, _fill(quote, opts, today),
                                                 opts, today, by_content, vectors, users))
                if speaker == "contact" and mtype != "sensitive":
                    shared.append(text)
            if not actions and days_ago <= 13 and rng.random() < 0.45:
                topic = rng.choice(TOPICS[key])
                m = await ms.insert_memory(
                    owner_id=pid, contact_id=contact["_id"], type="topic", content=topic,
                    source={"kind": "call", "origin": "contact_self", "call_id": call["_id"], "speaker": "contact",
                            "quote": lines[-1][1]}, trust=0.85, confidence=0.8, importance=0.35,
                    expires_at=ms.expiry_for(clock.now(), 14), embedding=vectors[topic])
                actions.append({"action": "ADD", "content": topic, "reason": "something they enjoyed talking about",
                                "memory_id": str(m["_id"])})
            elif not actions and rng.random() < 0.5:
                facts = [m for m in by_content.values() if m["contact_id"] == contact["_id"] and m["type"] == "fact"
                         and m["status"] == "active"]
                if facts:
                    f = rng.choice(facts)
                    await db.memories.update_one({"_id": f["_id"]}, {"$inc": {"reinforced": 1}})
                    await ms.log_event(f["_id"], "reinforced", pid, "agent", call["_id"])
                    actions.append({"action": "IGNORE", "content": f["content"], "reason": "already known; reinforced",
                                    "memory_id": str(f["_id"]), "target_id": str(f["_id"]), "target_content": f["content"]})
        lines.append(("contact", "Okay Aaji, take care. I'll call again soon." if key != "doctor" else "Take care, Sunandatai."))

        if not call["off_record"]:
            for n, (speaker, text) in enumerate(lines):
                ts = at + timedelta(seconds=20 + n * duration / (len(lines) + 1))
                await db.transcript_chunks.insert_one({"call_id": call["_id"], "speaker": speaker, "text": text,
                                                       "ts": ts, "created_at": ts})
        name = c["name"]
        summary = (f"{name} shared: " + "; ".join(shared) + ".") if shared else \
            f"A {max(1, duration // 60)}-minute catch-up with {name}. " + (actions[0]["content"] + "." if actions else "")
        recap = f"You talked to {name}." + (f" {shared[0]}." if shared else "")
        await db.calls.update_one({"_id": call["_id"]}, {"$set": {
            "summary": summary.strip(), "recap": recap,
            "agent": {"mode": "seeded", "candidates": len(actions), "actions": actions}}})

    for at, key in blocked:
        c = keys[key]
        reason = {"rahul": "Rahul sleeps 9 AM - 3 PM after night shifts; no calls then" if at.astimezone(TZ).hour < 15
                  else "Don't let Aaji call Rahul more than 3 times a day",
                  "riya": "Wait at least an hour between calls to Riya",
                  "sunita": "Default: 30 minutes between calls"}[key]
        await db.calls.insert_one({
            "patient_id": pid, "contact_id": contacts[key]["_id"], "status": "blocked", "started_at": at,
            "initiated_by": "patient", "seeded": True,
            "guard": {"allowed": False, "can_override": key != "rahul", "reason": reason,
                      "message": f"You spoke to {c['name']} a little while ago."}})

    for days_ago in ARCHIVED["asked_days_ago"]:
        await db.calls.insert_one({
            "patient_id": pid, "contact_id": late["_id"], "status": "blocked", "seeded": True, "initiated_by": "patient",
            "started_at": _local(today - timedelta(days=days_ago), 19, rng.randint(0, 59)),
            "guard": {"allowed": False, "can_override": False, "archived": True,
                      "reason": "Archived contact: calls are not placed", "message": "Let's call someone else today."}})

    # ---- the "Who is this?" overlay: Aaji asked who Arjun was more than once on two recent calls
    asks = {"arjun": [(2, 1), (1, 1)], "kavya": [(0, 1)]}  # per recent call: (button, voice)
    for key, per_call in asks.items():
        recent = [c async for c in db.calls.find({"contact_id": contacts[key]["_id"], "status": "processed"})
                  .sort("started_at", -1).limit(len(per_call))]
        for call, (button, voice) in zip(recent, per_call):
            t0 = call.get("answered_at") or call["started_at"]
            events = [{"kind": "shown", "ts": t0 + timedelta(seconds=3)}] + \
                [{"kind": "button", "ts": t0 + timedelta(seconds=60 * (i + 1))} for i in range(button)] + \
                [{"kind": "voice", "ts": t0 + timedelta(seconds=90 * (i + 1))} for i in range(voice)]
            await db.calls.update_one({"_id": call["_id"]}, {"$set": {"overlay": {
                "shown": 1, "button": button, "voice": voice, "also_here_changes": 0,
                "events": sorted(events, key=lambda e: e["ts"])}}})
            if button + voice >= 2:
                rel = await db.memories.find_one({"contact_id": contacts[key]["_id"], "type": "relationship"})
                with clock.frozen(call["ended_at"]):
                    await ms.set_fields(rel, {"identity_asks": rel.get("identity_asks", 0) + 1,
                                              "last_identity_ask_at": clock.now()}, "identity_asked", "patient")

    # ---- recent caregiver activity, so the family activity log looks lived-in
    async def touch(content, days_ago, fn):
        m = await db.memories.find_one({"owner_id": pid, "content": content})
        if m:
            with clock.frozen(now - timedelta(days=days_ago, hours=rng.randint(1, 8))):
                await fn(m)

    await touch("Do not mention Sunita's health check-up results to Aaji", 17,
                lambda m: ms.log_event(m["_id"], "visibility:never_surface", pid, "Sunita Kulkarni"))
    await touch("Vikram's pharmacy reopened after the renovation", 9,
                lambda m: ms.set_fields(m, {"importance": 0.75}, "reweighted", "Sunita Kulkarni"))
    await touch("Meera teaches mathematics at a school", 6,
                lambda m: ms.update_content(m, "Meera teaches mathematics at Vidya Bhavan school", "Meera Patil", {"trust": 1.0}))
    await touch("Riya loves singing old Kishore Kumar songs with Aaji", 4,
                lambda m: ms.log_event(m["_id"], "feedback:helpful", pid, "Riya Patil"))
    await touch("Aaji and Rahul talked about the tulsi plant on the balcony", 1,
                lambda m: ms.set_fields(m, {"importance": 0.6}, "reweighted", "Rahul Patil"))
    with clock.frozen(now - timedelta(days=2, hours=3)):
        await ms.insert_memory(owner_id=pid, contact_id=None, type="fact",
                               content="Aaji's new reading glasses are in the blue case on the bedside table",
                               source={"kind": "caregiver", "origin": "caregiver", "by": "Meera Patil"}, trust=1.0,
                               importance=0.5, actor="Meera Patil")

    # ---- retrieval stats & feedback so the inspector has something to show
    async for m in db.memories.find({"owner_id": pid, "status": "active", "type": {"$in": ["fact", "preference", "event"]}}):
        if rng.random() < 0.3:
            helpful = rng.randint(1, 4)
            await db.memories.update_one({"_id": m["_id"]}, {"$set": {"feedback": {"helpful": helpful,
                                                                                  "not_helpful": rng.randint(0, 1)}}})

    # ---- a patient-requested contact waiting for approval, and recent sign-ins
    await db.contacts.insert_one({"patient_id": pid, "name": PENDING_CONTACT["name"], "nicknames": [], "relation": "unknown",
                                  "status": "pending", "photo": None, "color": "#8a8f98", "primary_caregiver": False,
                                  "requested_by": "patient", "created_at": now - timedelta(days=PENDING_CONTACT["days_ago"])})
    for u in FAMILY_USERS:
        for _ in range(rng.randint(3, 9)):
            await db.auth_events.insert_one({"kind": "sign_in", "patient_id": pid, "user_id": users[u["key"]],
                                             "who": u["name"], "provider": "google.com",
                                             "ts": now - timedelta(days=rng.uniform(0, HISTORY_DAYS - 1))})

    counts = {name: await db.db[name].count_documents(q) for name, q in [
        ("contacts", {"patient_id": pid}), ("calls", {"patient_id": pid}),
        ("memories", {"owner_id": pid}), ("briefs", {"owner_id": pid}), ("memory_events", {"owner_id": pid})]}
    return {"patient_id": str(pid), **counts}


def _fill(text: str, opts: dict, today: date) -> str:
    if "event_in" not in opts:
        return text
    d = today + timedelta(days=opts["event_in"])
    return text.replace("{date}", _label(d)).replace("{weekday}", d.strftime("%A"))


async def _apply_beat(pid, contact, call_id, mtype, text, quote, opts, today, by_content, vectors, users) -> dict:
    speaker = opts.get("speaker", "contact")
    origin = "patient" if speaker == "patient" else ("contact_other" if opts.get("speaker_about") == "other" else "contact_self")
    trust = ms.TRUST[origin]
    source = {"kind": "call", "origin": origin, "call_id": call_id, "speaker": speaker, "quote": quote}
    event_date = expires_at = None
    if "event_in" in opts:
        event_date = datetime.combine(today + timedelta(days=opts["event_in"]), time(0), tzinfo=TZ)
        expires_at = ms.expiry_for(event_date, 1 if mtype == "plan" else 2)
    common = dict(owner_id=pid, contact_id=contact["_id"], type=mtype, content=text, source=source, trust=trust,
                  embedding=vectors[text], event_date=event_date, expires_at=expires_at)

    if "conflict" in opts:
        target = by_content[opts["conflict"]]
        m = await ms.insert_memory(**common, confidence=0.4, importance=0.5, status="pending_review",
                                   visibility="caregiver_only", conflicts_with=target["_id"])
        if opts.get("status") == "rejected":
            reviewer = "Sunita Kulkarni" if "Anil" in text else "Rahul Patil"
            with clock.frozen(clock.now() + timedelta(hours=random.Random(text).randint(2, 20))):
                await ms.set_fields(m, {"status": "rejected"}, "rejected", reviewer)
        return {"action": "CONFLICT", "content": text, "memory_id": str(m["_id"]), "target_id": str(target["_id"]),
                "target_content": target["content"],
                "reason": f"contradicts a caregiver-entered memory (patient trust {trust} < 1.0)"}

    if mtype == "sensitive":
        m = await ms.insert_memory(**common, confidence=0.9, importance=1.0, visibility="never_surface")
        return {"action": "ADD", "content": text, "memory_id": str(m["_id"]),
                "reason": "a speaker asked to keep this from Aaji; stored as never_surface"}

    confidence = opts.get("confidence", 0.9)
    status = "pending_review" if confidence < 0.6 else "active"
    importance = {"event": 0.8, "plan": 0.8, "fact": 0.65, "preference": 0.55, "topic": 0.35}.get(mtype, 0.5)
    supersedes = by_content.get(opts["supersedes"]) if opts.get("supersedes") else None
    m = await ms.insert_memory(**common, confidence=confidence, importance=importance, status=status,
                               supersedes=supersedes["_id"] if supersedes else None)
    by_content[text] = m
    if supersedes:
        await ms.supersede(supersedes, m["_id"])
        supersedes["status"] = "superseded"
        return {"action": "SUPERSEDE", "content": text, "memory_id": str(m["_id"]), "target_id": str(supersedes["_id"]),
                "target_content": supersedes["content"], "reason": "newer information from the person themselves"}
    return {"action": "ADD", "content": text, "memory_id": str(m["_id"]),
            "reason": "low confidence; waiting for review" if status == "pending_review" else "new information"}
