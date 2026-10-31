"""Demo seed: Aaji (patient), Rahul (caregiver) and her family circle.

Only caregiver-entered basics are seeded, so the first call with Riya starts with a
minimal brief and the memory agent's work is visible from there.
"""
from datetime import timedelta

from . import clock, db
from . import memory_store as ms

CONTACTS = [
    {
        "name": "Rahul", "nicknames": ["beta", "my son"], "relation": "son", "color": "#2f6f4f",
        "phone": "+91 98200 00001", "primary": True,
        "notes": ["Rahul works night shifts as a nurse at Sassoon Hospital"],
        "constraints": [
            ("Rahul sleeps 9 AM - 3 PM after night shifts; no calls then", {"kind": "quiet_hours", "start": "09:00", "end": "15:00"}),
            ("Don't let Aaji call Rahul more than 3 times a day", {"kind": "max_calls_per_day", "value": 3}),
        ],
    },
    {
        "name": "Riya", "nicknames": ["Riyu", "my granddaughter"], "relation": "granddaughter",
        "relation_detail": "Rahul's daughter", "color": "#9b4d8a", "phone": "+91 98200 00002",
        "notes": ["Riya lives in Mumbai", "Riya has a dog named Bruno", "Riya studies engineering in college"],
        "constraints": [("Wait at least an hour between calls to Riya", {"kind": "min_gap_minutes", "value": 60})],
    },
    {
        "name": "Meera", "nicknames": ["sunbai", "my daughter-in-law"], "relation": "daughter-in-law",
        "relation_detail": "Rahul's wife", "color": "#b5651d", "phone": "+91 98200 00003",
        "notes": ["Meera teaches mathematics at a school", "Meera loves cooking puran poli with Aaji"],
    },
    {
        "name": "Arjun", "nicknames": ["my grandson"], "relation": "grandson", "relation_detail": "Rahul's son",
        "color": "#2b5d8a", "phone": "+91 98200 00004",
        "notes": ["Arjun is in 8th standard", "Arjun plays cricket for his school team"],
    },
]


async def seed(reset: bool = True) -> dict:
    if reset:
        for name in db.COLLECTIONS:
            await db.db[name].delete_many({})
        clock.reset()

    now = clock.now()
    seeded_at = now - timedelta(days=20)
    patient = {"name": "Aaji", "full_name": "Sunanda Patil", "role": "patient", "language": "en-IN", "created_at": now}
    patient["_id"] = (await db.users.insert_one(patient)).inserted_id
    caregiver = {"name": "Rahul", "role": "caregiver", "patient_id": patient["_id"], "created_at": now}
    caregiver["_id"] = (await db.users.insert_one(caregiver)).inserted_id

    source = {"kind": "caregiver", "origin": "caregiver", "user_id": caregiver["_id"]}
    for c in CONTACTS:
        contact = {
            "patient_id": patient["_id"], "name": c["name"], "nicknames": c["nicknames"], "relation": c["relation"],
            "relation_detail": c.get("relation_detail"), "phone": c["phone"], "photo": None, "color": c["color"],
            "status": "active", "primary_caregiver": c.get("primary", False), "created_at": now,
        }
        contact["_id"] = (await db.contacts.insert_one(contact)).inserted_id
        rel = f"{c['name']} is Aaji's {c['relation']}" + (f" ({c['relation_detail']})" if c.get("relation_detail") else "")
        await ms.insert_memory(owner_id=patient["_id"], contact_id=contact["_id"], type="relationship", content=rel,
                               source=source, trust=1.0, importance=1.0, actor="caregiver")
        for note in c["notes"]:
            await ms.insert_memory(owner_id=patient["_id"], contact_id=contact["_id"], type="fact", content=note,
                                   source=source, trust=1.0, importance=0.6, actor="caregiver")
        for text, rule in c.get("constraints", []):
            await ms.insert_memory(owner_id=patient["_id"], contact_id=contact["_id"], type="constraint", content=text,
                                   source=source, trust=1.0, importance=1.0, visibility="caregiver_only", rule=rule,
                                   actor="caregiver")

    await ms.insert_memory(owner_id=patient["_id"], contact_id=None, type="sensitive",
                           content="Do not mention that the old Nashik house was sold", source=source, trust=1.0,
                           importance=1.0, visibility="never_surface", actor="caregiver")

    # Backdate seeded memories so recency scores differ from fresh call memories.
    await db.memories.update_many({"owner_id": patient["_id"]}, {"$set": {"created_at": seeded_at, "updated_at": seeded_at}})
    return {"patient_id": str(patient["_id"]), "caregiver_id": str(caregiver["_id"])}
