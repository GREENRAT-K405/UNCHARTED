"""Headless run of the demo's memory loop against a running backend (default http://localhost:8000).

    python scripts/smoke.py [base_url]

Signs in as a dev caregiver, opens the demo family, resets it, pairs a dev "tablet",
then plays the demo: the tablet calls Riya, Riya (with the call's join token) speaks,
the memory agent runs, and the guard, the next brief and the review queue are checked.
Needs the backend in dev auth mode (no FIREBASE_PROJECT_ID).
"""
import json
import sys
import time
import urllib.request
from datetime import datetime

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/") + "/api"


CAREGIVER = "dev:smoke-test|smoke@example.com|Smoke Test"
TABLET = f"dev-anon:smoke-tablet-{int(time.time())}"
token = CAREGIVER


def call(method, path, body=None, as_=None):
    headers = {"Content-Type": "application/json"}
    tok = as_ if as_ is not None else token
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None, headers=headers)
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    return cond


ok = True
health = call("GET", "/health")
print("health:", json.dumps({k: health[k] for k in ("auth", "llm", "embeddings", "capabilities")}))
if health["auth"] != "dev":
    sys.exit("smoke.py needs the backend in dev auth mode (unset FIREBASE_PROJECT_ID)")
pid = call("POST", "/circles/demo")["id"]
call("POST", "/demo/reset")
code = call("POST", "/devices/pair-code", as_=TABLET)["code"]
call("POST", f"/circles/{pid}/devices", {"code": code, "label": "Smoke tablet"})
token = TABLET  # from here on, act as Aaji's tablet
contacts = {c["name"]: c for c in call("GET", f"/patients/{pid}/contacts")}
riya = contacts["Riya"]

print("\n1. first call")
start = call("POST", "/calls", {"patient_id": pid, "contact_id": riya["id"]})
ok &= check(start["allowed"], "call allowed")
cid = start["call_id"]
join_token = start["join_url"].split("?t=")[1]
brief1 = call("GET", f"/calls/{cid}/brief")
print("   brief:", brief1["card"]["headline"], "|", " / ".join(l["text"] for l in brief1["card"]["lines"]))
script = [
    ("patient", "Hello? Who is this?"),
    ("contact", "Aaji, it's Riya! I moved to Pune last month for college."),
    ("contact", "My physics exam is on Friday, I'm a little nervous."),
    ("patient", "Riya is my daughter, na?"),
    ("contact", "I'll come visit you on Sunday, Aaji."),
    ("contact", "And Bruno says hello."),
    ("contact", "Papa, don't tell Aaji about Papa's surgery."),
]
for speaker, text in script:  # the tablet speaks for Aaji; Riya's lines carry her join token
    call("POST", f"/calls/{cid}/transcript", {"text": text, "t": join_token if speaker == "contact" else None})
call("POST", f"/calls/{cid}/end")

print("\n2. memory agent")
for _ in range(120):
    c = call("GET", f"/calls/{cid}")
    if c["status"] == "processed":
        break
    time.sleep(1)
ok &= check(c["status"] == "processed", "call processed")
for a in c.get("agent", {}).get("actions", []):
    print(f"   {a['action']:<9} {a['content']}" + (f"  (vs: {a['target_content']})" if a.get("target_content") else ""))
print("   recap:", c.get("recap"))
actions = {a["action"] for a in c.get("agent", {}).get("actions", [])}
ok &= check("SUPERSEDE" in actions, "Mumbai -> Pune superseded")
ok &= check("CONFLICT" in actions, "patient's 'daughter' claim became a conflict")

mems = call("GET", f"/memories?patient_id={pid}&contact_id={riya['id']}&status=active,pending_review,superseded", as_=CAREGIVER)
ok &= check(any(m["status"] == "superseded" and "Mumbai" in m["content"] for m in mems), "Mumbai memory marked superseded")
ok &= check(any(m["type"] == "event" and m.get("expires_at") for m in mems), "exam event has expires_at (TTL)")
ok &= check(any(m["visibility"] == "never_surface" for m in mems), "surgery stored as never_surface")
ok &= check(any("granddaughter" in m["content"] and m["status"] == "active" for m in mems), "caregiver relationship untouched")

print("\n3. repeat-call guard")
again = call("POST", "/calls", {"patient_id": pid, "contact_id": riya["id"]})
ok &= check(not again["allowed"] and again["can_override"], "guard fired with 'call anyway'")
print("   message:", again.get("message"))

print("\n4. later: the day after the exam")
exam = next(m for m in mems if m["type"] == "event" and m.get("event_date"))
now = datetime.fromisoformat(call("GET", "/clock")["now"])
days = (datetime.fromisoformat(exam["event_date"]) - now).total_seconds() / 86400 + 1
print(f"   shifting demo clock +{days:.1f} days")
call("POST", "/clock", {"shift_days": days}, as_=CAREGIVER)
start2 = call("POST", "/calls", {"patient_id": pid, "contact_id": riya["id"], "force": True})
brief2 = call("GET", f"/calls/{start2['call_id']}/brief")
text2 = json.dumps(brief2["card"]).lower()
print("   brief:", " / ".join(l["text"] for l in brief2["card"]["lines"]), "| ask:", brief2["card"]["suggestions"])
ok &= check("pune" in text2, "brief mentions Pune")
ok &= check("mumbai" not in text2, "brief no longer says Mumbai")
ok &= check("surgery" not in text2, "private memory never surfaces")
ok &= check(any("exam" in x.lower() for x in brief2["card"]["suggestions"]), "brief suggests asking about the exam")
call("POST", f"/calls/{start2['call_id']}/end")
call("POST", "/clock", {"reset": True}, as_=CAREGIVER)

print("\n5. inspector")
b = call("GET", f"/briefs/{brief2['brief_id']}", as_=CAREGIVER)
top = [i for i in b["items"] if i["selected"]]
ok &= check(all({"similarity", "recency", "importance", "trust", "final", "reason"} <= i.keys() for i in top),
            "brief items carry score breakdowns")
for i in top:
    print(f"   {i['final']:.3f}  sim {i['similarity']:.2f} rec {i['recency']:.2f} +{i['boost']}  {i['content']}  [{i['reason']}]")

print("\nRESULT:", "ALL PASS" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
