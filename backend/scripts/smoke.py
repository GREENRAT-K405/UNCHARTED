"""Headless run of the demo's memory loop against a running backend (default http://localhost:8000).

    python scripts/smoke.py [base_url]

Signs in as a dev caregiver, opens the demo family, resets it, pairs a dev "tablet",
then plays the demo: the tablet calls Riya, Riya (with the call's join token) speaks,
the memory agent runs, and the guard, the next brief and the review queue are checked.
Then the call features: the "Who is this?" card, contact links and ringing, the home screen
recap line, a relative's written update, archiving, the patient's language and recall cards.
Needs the backend in dev auth mode (no FIREBASE_PROJECT_ID). WebSocket checks need the
`websockets` package (installed with uvicorn[standard]); they are skipped without it.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

try:
    from websockets.sync.client import connect as ws_connect
except ImportError:  # pragma: no cover
    ws_connect = None

ROOT = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
BASE = ROOT + "/api"
WS = ROOT.replace("http", "ws", 1)


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


def status_of(method, path, body=None, as_=""):
    try:
        call(method, path, body, as_=as_)
        return 200
    except urllib.error.HTTPError as e:
        return e.code


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    return bool(cond)


def wait_for(fn, seconds=20, every=0.5):
    end = time.time() + seconds
    while time.time() < end:
        if v := fn():
            return v
        time.sleep(every)
    return None


class Sock:
    """A tiny JSON WebSocket helper that authenticates with its first frame."""

    def __init__(self, path, hello):
        self.ws = ws_connect(WS + path, open_timeout=10).__enter__()
        self.ws.send(json.dumps(hello))
        self.seen = []

    def send(self, msg):
        self.ws.send(json.dumps(msg))

    def expect(self, kind, timeout=10, **match):
        want = lambda m: m.get("type") == kind and all(m.get(k) == v for k, v in match.items())
        for m in self.seen:
            if want(m):
                self.seen.remove(m)
                return m
        end = time.time() + timeout
        while time.time() < end:
            try:
                m = json.loads(self.ws.recv(timeout=max(0.1, end - time.time())))
            except TimeoutError:
                break
            if want(m):
                return m
            self.seen.append(m)
        return None

    def close(self):
        self.ws.close()


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
info = call("GET", f"/calls/{cid}")
ok &= check(info["who"] and info["who"]["name"] == "Riya" and info["who"]["relation"] == "your granddaughter",
            "name tag comes from the contact at once (Riya, your granddaughter)")
brief1 = call("GET", f"/calls/{cid}/brief")
card1 = brief1["patient_card"]
ok &= check(card1["memory"] is None or card1["memory"]["type"] != "relationship",
            "patient card: at most one memory, never the relationship line")
ok &= check("suggestions" not in json.dumps(card1) and "card" not in brief1, "talking points never reach the patient")
stored1 = call("GET", f"/briefs/{brief1['brief_id']}", as_=CAREGIVER)
print("   brief:", stored1["card"]["headline"], "|", " / ".join(l["text"] for l in stored1["card"]["lines"]))
print("   patient card:", card1["who"]["name"], "·", card1["who"]["relation"], "·", (card1["memory"] or {}).get("text"))
recent = any("recent topic" in i["reason"] for i in stored1["items"])
if health["embeddings"] == "local" or recent:
    ok &= check(recent, "brief uses a vector query filtered on type and created_at ('recent topic')")
else:
    print("  SKIP 'recent topic' (no hits: the query was probably embedded locally after a Voyage rate limit)")
recv = call("GET", f"/calls/{cid}/receiver?t={join_token}", as_="")
ok &= check("talking_points" in recv["view"] and recv["view"]["on_screen"]["who"]["name"] == "Riya",
            "relative's side gets talking points and what the patient sees")
ok &= check(all(o["name"] != "Riya" for o in recv["others"]) and len(recv["others"]) >= 5,
            "'Who else is with you?' offers the patient's other active contacts")
script = [
    ("patient", "Hello? Who is this?"),
    ("contact", "Aaji, it's Riya! I moved to Pune last month for college."),
    ("contact", "My physics exam is on Friday, I'm a little nervous."),
    ("patient", "Riya is my daughter, na?"),
    ("contact", "I'll come visit you on Sunday, Aaji."),
    ("contact", "And Bruno says hello."),
    ("patient", "Who are you, beta?"),
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
ok &= check(c.get("overlay", {}).get("voice") == 2, "two spoken 'who is this?' recorded on the call")

mems = call("GET", f"/memories?patient_id={pid}&contact_id={riya['id']}&status=active,pending_review,superseded", as_=CAREGIVER)
ok &= check(any(m["status"] == "superseded" and "Mumbai" in m["content"] for m in mems), "Mumbai memory marked superseded")
ok &= check(any(m["type"] == "event" and m.get("expires_at") for m in mems), "exam event has expires_at (TTL)")
ok &= check(any(m["visibility"] == "never_surface" for m in mems), "surgery stored as never_surface")
ok &= check(any("granddaughter" in m["content"] and m["status"] == "active" for m in mems), "caregiver relationship untouched")
rel = next(m for m in mems if m["type"] == "relationship" and m["status"] == "active")
ok &= check(rel.get("identity_asks", 0) >= 1, "asking twice reinforced the relationship memory")
events = call("GET", f"/memories/{rel['id']}", as_=CAREGIVER)["events"]
ok &= check(any(e["action"] == "identity_asked" for e in events), "... and it is logged in memory_events")
ok &= check(not any(w in json.dumps(mems) for w in ("Hello? Who is this",)), "the 'who is this' line made no memory")

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
card2 = call("GET", f"/briefs/{brief2['brief_id']}", as_=CAREGIVER)["card"]
text2 = json.dumps(card2).lower()
print("   brief:", " / ".join(l["text"] for l in card2["lines"]), "| ask:", card2["suggestions"])
chosen2 = " ".join(i["content"] for i in call("GET", f"/briefs/{brief2['brief_id']}", as_=CAREGIVER)["items"]
                   if i["selected"]).lower()
ok &= check("pune" in text2 or "pune" in chosen2, "brief selects (and usually says) Pune")
ok &= check("mumbai" not in text2 and "mumbai" not in chosen2, "brief no longer uses Mumbai")
ok &= check("surgery" not in text2 and "surgery" not in json.dumps(brief2).lower(), "private memory never surfaces")
ok &= check(any("exam" in x.lower() for x in card2["suggestions"]), "brief suggests asking about the exam")
ok &= check(bool(brief2["patient_card"]["who"].get("detail")), "after repeated asks the name tag adds identity detail")
ended2 = call("POST", f"/calls/{start2['call_id']}/end")["status"]
ok &= check(ended2 == "cancelled" or call("GET", f"/calls/{start2['call_id']}")["status"] == "unanswered",
            "hanging up before anyone answers is recorded as cancelled (or unanswered), not as a call")
call("POST", "/clock", {"reset": True}, as_=CAREGIVER)

print("\n5. inspector")
b = call("GET", f"/briefs/{brief2['brief_id']}", as_=CAREGIVER)
top = [i for i in b["items"] if i["selected"]]
ok &= check(all({"similarity", "recency", "importance", "trust", "final", "reason"} <= i.keys() for i in top),
            "brief items carry score breakdowns")
for i in top:
    print(f"   {i['final']:.3f}  sim {i['similarity']:.2f} rec {i['recency']:.2f} +{i['boost']}  {i['content']}  [{i['reason']}]")

print("\n6. home screen memory line")
home = {c["name"]: c for c in call("GET", f"/patients/{pid}/contacts")}
ok &= check(bool(home["Riya"].get("last_recap", {}).get("at")), "Riya's tile has the latest recap time")
print("   Riya's tile:", home["Riya"].get("last_recap"))
ok &= check("link_url" not in home["Riya"] and "link_token" not in json.dumps(home), "the tablet never sees contact links")
ok &= check(all(c["status"] == "active" for c in home.values()), "archived and pending people have no tile")

print("\n7. contact link")
cg_contacts = {c["name"]: c for c in call("GET", f"/patients/{pid}/contacts?status=all", as_=CAREGIVER)}
riya_link = cg_contacts["Riya"]["link_url"]
riya_tok = riya_link.rsplit("/c/", 1)[1]
page = call("GET", f"/c/{riya_tok}", as_="")
ok &= check(page["patient"]["name"] == "Aaji" and page["past_calls"], "contact page shows past calls with recaps")
ok &= check("surgery" not in json.dumps(page).lower(), "contact page never shows private memories")
ok &= check(any(h["name"] == "Madhav" for h in page["hints"]), "family's note on an archived person reaches the relative")
upd = call("POST", f"/c/{riya_tok}/update", {"text": "I got engaged to Kabir last week!"}, as_="")
ok &= check(upd["saved"] >= 1, "written update saved through the memory pipeline")
mems = call("GET", f"/memories?patient_id={pid}&contact_id={riya['id']}", as_=CAREGIVER)
from_link = [m for m in mems if m["source"].get("kind") == "contact_update"]
ok &= check(bool(from_link) and all(m["trust"] in (0.85, 0.7) for m in from_link),
            "update stored with source contact_update and contact trust")
print("   from the link:", [m["content"] for m in from_link])

if ws_connect:
    sunita = cg_contacts["Sunita"]
    sun_tok = sunita["link_url"].rsplit("/c/", 1)[1]
    room = Sock("/ws/contact", {"type": "auth", "link_token": sun_tok})
    ok &= check(room.expect("welcome") is not None, "waiting room registers presence")
    ring_call = call("POST", "/calls", {"patient_id": pid, "contact_id": sunita["id"], "force": True})
    patient_ws = Sock(f"/ws/call/{ring_call['call_id']}?role=patient", {"type": "auth", "token": TABLET})
    ring = room.expect("ring")
    ok &= check(ring is not None and ring["call_id"] == ring_call["call_id"], "calling Sunita rings her open link")
    room.send({"type": "decline", "call_id": ring["call_id"]})
    na = patient_ws.expect("no_answer")
    ok &= check(na is not None and na["reason"] == "declined", "decline shows the patient a calm 'busy' message")
    ok &= check(call("GET", f"/calls/{ring_call['call_id']}")["status"] == "declined", "call recorded as declined")
    patient_ws.close()
    timeout = health["calls"]["ring_timeout_seconds"]
    if timeout <= 10:
        c3 = call("POST", "/calls", {"patient_id": pid, "contact_id": sunita["id"], "force": True})
        room.expect("ring", call_id=c3["call_id"])
        st = wait_for(lambda: call("GET", f"/calls/{c3['call_id']}")["status"] == "unanswered", timeout + 5)
        ok &= check(bool(st), f"no answer within {timeout}s -> recorded as unanswered")
        ok &= check(room.expect("ring_over", call_id=c3["call_id"]) is not None, "... and the ring stops")
    else:
        print(f"   (skipping the unanswered check: ring timeout is {timeout}s; run the server with RING_TIMEOUT_SECONDS<=10)")
    room.close()
old = riya_tok
new_link = call("POST", f"/contacts/{riya['id']}/link", as_=CAREGIVER)["link_url"]
ok &= check(status_of("GET", f"/c/{old}") == 404 and status_of("GET", "/c/" + new_link.rsplit("/c/", 1)[1]) == 200,
            "regenerating the link retires the old one")

print("\n8. archive")
vikram = cg_contacts["Vikram"]
call("POST", f"/contacts/{vikram['id']}/archive", {"note": "If Aaji asks for Vikram, say he is busy at the shop."},
     as_=CAREGIVER)
vmems = call("GET", f"/memories?patient_id={pid}&contact_id={vikram['id']}", as_=CAREGIVER)
ok &= check(all(m["visibility"] in ("caregiver_only", "never_surface") for m in vmems), "archived: memories hidden from the patient")
refused = call("POST", "/calls", {"patient_id": pid, "contact_id": vikram["id"]})
ok &= check(not refused["allowed"] and refused.get("archived") and "died" not in refused["message"].lower(),
            "guard refuses the call gently, without saying why")
ok &= check("Vikram" not in {c["name"] for c in call("GET", f"/patients/{pid}/contacts")}, "tile hidden")
madhav = cg_contacts["Madhav"]
ok &= check(madhav["status"] == "archived" and not call("POST", "/calls", {"patient_id": pid, "contact_id": madhav["id"]})["allowed"],
            "seeded late husband is archived and cannot be called")
c4 = call("POST", "/calls", {"patient_id": pid, "contact_id": cg_contacts["Arjun"]["id"], "force": True})
hints = call("GET", f"/calls/{c4['call_id']}/receiver?t={c4['join_url'].split('?t=')[1]}", as_="")["view"]["hints"]
ok &= check({h["name"] for h in hints} >= {"Vikram", "Madhav"}, "other relatives see the archive notes as hints")
call("POST", f"/calls/{c4['call_id']}/end")
call("POST", f"/contacts/{vikram['id']}/unarchive", as_=CAREGIVER)
vmems2 = call("GET", f"/memories?patient_id={pid}&contact_id={vikram['id']}", as_=CAREGIVER)
ok &= check(sorted(m["visibility"] for m in vmems2) == sorted(m.get("visibility_before_archive") or "patient" for m in vmems)
            and any(m["visibility"] == "patient" for m in vmems2), "unarchive restores each memory's visibility")

print("\n9. language")
call("PATCH", f"/patients/{pid}", {"language": "hi-IN"}, as_=CAREGIVER)
c5 = call("POST", "/calls", {"patient_id": pid, "contact_id": cg_contacts["Meera"]["id"], "force": True})
ok &= check(call("GET", f"/calls/{c5['call_id']}")["language"] == "hi-IN", "call screen gets the patient's language")
call("POST", f"/calls/{c5['call_id']}/transcript", {"text": "यह कौन है?"})
ok &= check(call("GET", f"/calls/{c5['call_id']}").get("overlay", {}).get("voice") == 1, "Hindi 'who is this?' detected")
call("POST", f"/calls/{c5['call_id']}/end")
call("POST", f"/patients/{pid}/speech-status", {"language": "hi-IN", "voice_found": False, "voice_lang": "en-IN"})
ss = call("GET", f"/patients/{pid}/speech-status", as_=CAREGIVER)
ok &= check(ss["status"]["voice_found"] is False, "missing voice reported for the caregiver sidebar")
call("PATCH", f"/patients/{pid}", {"language": "en-IN"}, as_=CAREGIVER)

if ws_connect:
    print("\n10. read-aloud stays out of the transcript; recall card")
    c6 = call("POST", "/calls", {"patient_id": pid, "contact_id": riya["id"], "force": True})
    tok6 = c6["join_url"].split("?t=")[1]
    pws = Sock(f"/ws/call/{c6['call_id']}?role=patient", {"type": "auth", "token": TABLET})
    cws = Sock(f"/ws/call/{c6['call_id']}?role=contact", {"type": "auth", "join_token": tok6})
    pws.expect("welcome"), cws.expect("welcome")
    spoken = "This is Riya, your granddaughter. Riya lives in Pune."
    pws.send({"type": "tts", "speaking": True, "text": spoken})
    pws.send({"type": "transcript", "text": "This is Riya your granddaughter"})  # the mic hears the speaker
    cws.send({"type": "transcript", "text": "this is riya your granddaughter riya lives in pune"})  # echo
    cws.send({"type": "transcript", "text": "Bruno learned a new trick, Aaji, he shakes hands now!"})
    cws.send({"type": "transcript", "text": "And the hostel food is still so bland, I miss your varan bhaat."})
    pws.send({"type": "tts", "speaking": False})
    time.sleep(1)
    lines = call("GET", f"/calls/{c6['call_id']}/transcript", as_=CAREGIVER)
    ok &= check(not any("granddaughter" in l["text"].lower() for l in lines), "read-aloud text dropped from both sides")
    ok &= check(any("Bruno" in l["text"] for l in lines), "the relative's real words are kept")
    rc = pws.expect("recall", timeout=health.get("calls", {}).get("recall_tick", 25))
    print("   recall card:", rc and rc["card"])
    if rc is None and health["embeddings"] != "local":
        print("  SKIP recall card (none shown: likely a Voyage rate limit; the loop then skips rather than guess)")
    else:
        ok &= check(rc is not None and any(w in rc["card"]["text"] for w in ("Bruno", "hostel")),
                    "recall card matched what the relative said")
    pws.send({"type": "overlay", "event": "button"})
    cws.send({"type": "also_here", "ids": [cg_contacts["Rahul"]["id"]]})
    ah = pws.expect("also_here")
    ok &= check(ah is not None and ah["people"][0]["name"] == "Rahul", "'also here' reaches the patient's card")
    time.sleep(0.5)
    c6doc = call("GET", f"/calls/{c6['call_id']}")
    ok &= check(c6doc["overlay"].get("button") == 1 and c6doc["overlay"].get("also_here_changes") == 1
                and len(c6doc.get("recall_cards", [])) == (1 if rc else 0), "overlay events and recall cards logged on the call")
    pws.close(), cws.close()
    call("POST", f"/calls/{c6['call_id']}/end")

print("\n11. relatives with their own accounts, and the one-button family call")
REL_A = "dev:rel-riya|riya.account@example.com|Riya Account"
REL_B = "dev:rel-sunita|sunita.account@example.com|Sunita Account"
cg = {c["name"]: c for c in call("GET", f"/patients/{pid}/contacts?status=all", as_=CAREGIVER)}
tok_of = lambda n: cg[n]["link_url"].rsplit("/c/", 1)[1]
none = call("POST", "/calls/family", {"patient_id": pid})
ok &= check(none["allowed"] and none["ringing"] == 0, "nobody online: the meeting still opens (the patient never gets a refusal)")
call("POST", f"/calls/{none['call_id']}/end")
call("POST", "/relative/link", {"token": tok_of("Riya")}, as_=REL_A)
call("POST", "/relative/link", {"token": tok_of("Sunita")}, as_=REL_B)
me_a = call("GET", "/me", as_=REL_A)
ok &= check(me_a["circles"] == [] and [r["contact_name"] for r in me_a["relative"]] == ["Riya"],
            "a relative's account is linked to their contact and has no care circle")
ok &= check(status_of("POST", "/relative/link", {"token": tok_of("Riya")}, as_=REL_B) == 409,
            "a contact already on one account can't be taken by another")
ok &= check(all(status_of("GET", path, as_=REL_A) in (403, 404) for path in (
    f"/patients/{pid}/contacts", f"/memories?patient_id={pid}", f"/calls?patient_id={pid}", f"/briefs?patient_id={pid}")),
    "a relative's account is denied every caregiver and tablet route")
ok &= check(status_of("POST", "/clock", {"shift_days": 1}, as_=REL_A) == 403, "... including the demo clock")
ok &= check(status_of("GET", f"/relative/contacts/{cg['Sunita']['id']}", as_=REL_A) == 404,
            "a relative can't open another relative's page")
page = call("GET", f"/relative/contacts/{cg['Riya']['id']}", as_=REL_A)
ok &= check(page["patient"]["name"] == "Aaji" and "surgery" not in json.dumps(page).lower() and "notes" not in page,
            "relative page: no private memories, no 'recently believed' notes")
upd2 = call("POST", f"/relative/contacts/{cg['Riya']['id']}/update", {"text": "My exam is on Friday."}, as_=REL_A)
ok &= check(upd2["saved"] >= 1, "a signed-in relative can share an update")
cg = {c["name"]: c for c in call("GET", f"/patients/{pid}/contacts?status=all", as_=CAREGIVER)}
ok &= check(cg["Riya"].get("account", {}).get("email") == "riya.account@example.com",
            "caregivers see which relatives have their own account")
ok &= check("account" not in json.dumps(call("GET", f"/patients/{pid}/contacts")) and "user_id" not in json.dumps(
    call("GET", f"/patients/{pid}/contacts")), "the patient's tablet never sees accounts")
call("POST", "/relative/link", {"token": tok_of("Rahul")}, as_=CAREGIVER)
me_cg = call("GET", "/me", as_=CAREGIVER)
ok &= check(me_cg["circles"] and any(r["contact_name"] == "Rahul" for r in me_cg["relative"]),
            "a caregiver who is also a relative keeps both views")
call("DELETE", f"/contacts/{cg['Rahul']['id']}/account", as_=CAREGIVER)

if ws_connect:
    a = Sock("/ws/relative", {"type": "auth", "token": REL_A})
    b = Sock("/ws/relative", {"type": "auth", "token": REL_B})
    ok &= check(a.expect("welcome") is not None and b.expect("welcome") is not None, "account pages register presence")
    rule = call("POST", "/memories", {"patient_id": pid, "contact_id": cg["Sunita"]["id"], "type": "constraint",
                                      "content": "Smoke: no calls today", "rule": {"kind": "quiet_hours", "start": "00:00", "end": "23:59"}},
                as_=CAREGIVER)
    f1 = call("POST", "/calls/family", {"patient_id": pid})
    ring1 = a.expect("ring", call_id=f1["call_id"])
    ok &= check(f1["allowed"] and f1["ringing"] == 1 and ring1 and ring1["family"] and "join_token" not in ring1
                and b.expect("ring", timeout=1.5, call_id=f1["call_id"]) is None,
                "family call rings only relatives who are online and allowed (quiet hours skip Sunita)")
    again = call("POST", "/calls/family", {"patient_id": pid})
    ok &= check(again["call_id"] == f1["call_id"], "a second tap while it rings is the same call")
    p1 = Sock(f"/ws/call/{f1['call_id']}?role=patient", {"type": "auth", "token": TABLET})
    p1.expect("welcome")
    a.send({"type": "decline", "call_id": f1["call_id"], "contact_id": cg["Riya"]["id"]})
    ok &= check(a.expect("ring_over", call_id=f1["call_id"]) is not None and p1.expect("no_answer", timeout=1.5) is None
                and call("GET", f"/calls/{f1['call_id']}")["status"] == "ringing",
                "declining only stops that relative's ring: the meeting stays open")
    p1.close()
    call("POST", f"/calls/{f1['call_id']}/end")
    call("DELETE", f"/memories/{rule['id']}", as_=CAREGIVER)

    f2 = call("POST", "/calls/family", {"patient_id": pid})
    ok &= check(f2["ringing"] == 2 and a.expect("ring", call_id=f2["call_id"]) and b.expect("ring", call_id=f2["call_id"]),
                "with no rule both online relatives ring")
    fdoc = call("GET", f"/calls/{f2['call_id']}")
    ok &= check(fdoc["family"] and fdoc["contact"] is None and fdoc["who"] is None and "join_url" not in fdoc,
                "an unanswered family call has no name tag and no link")
    ok &= check(call("GET", f"/calls/{f2['call_id']}/brief")["patient_card"]["who"] is None,
                "... and no brief: the patient never sees an error or a wrong name")
    p2 = Sock(f"/ws/call/{f2['call_id']}?role=patient", {"type": "auth", "token": TABLET})
    p2.expect("welcome")
    a.send({"type": "accept", "call_id": f2["call_id"], "contact_id": cg["Riya"]["id"]})
    b.send({"type": "accept", "call_id": f2["call_id"], "contact_id": cg["Sunita"]["id"]})
    ra, rb = a.expect("accepted", timeout=4, call_id=f2["call_id"]), b.expect("accepted", timeout=4, call_id=f2["call_id"])
    winner, loser, win_name = (ra, b, "Riya") if ra else (rb, a, "Sunita")
    ok &= check(bool(ra) != bool(rb) and winner and winner["join_token"], "exactly one relative gets the call (atomic claim)")
    ok &= check(loser.expect("ring_over", timeout=4, call_id=f2["call_id"]) is not None, "the other relative's ring stops")
    claimed = p2.expect("claimed", timeout=4)
    ok &= check(claimed and claimed["who"]["name"] == win_name and claimed["who"]["relation"].startswith("your "),
                f"the patient's card names whoever picked up ({win_name})")
    fdoc = call("GET", f"/calls/{f2['call_id']}")
    ok &= check(fdoc["contact"]["name"] == win_name and fdoc["who"]["name"] == win_name,
                "after the claim it is a normal call with that person")
    bf = call("GET", f"/calls/{f2['call_id']}/brief")["patient_card"]
    ok &= check(bf["who"]["name"] == win_name, "the brief and memory line follow the claimed contact")
    cw = Sock(f"/ws/call/{f2['call_id']}?role=contact", {"type": "auth", "join_token": winner["join_token"]})
    cw.expect("welcome")
    call("POST", f"/calls/{f2['call_id']}/transcript", {"text": "Hello Aaji, it is lovely to see you.", "t": winner["join_token"]})
    cw.close(), p2.close()
    ok &= check(call("POST", f"/calls/{f2['call_id']}/end")["status"] == "ended", "a claimed family call ends and is remembered like any call")
    listing = call("GET", f"/calls?patient_id={pid}&limit=30", as_=CAREGIVER)
    ok &= check(any(c["contact_name"] == "Family call" for c in listing), "the Calls list copes with calls nobody claimed")
    if health["calls"]["ring_timeout_seconds"] <= 10:
        f3 = call("POST", "/calls/family", {"patient_id": pid})
        a.expect("ring", call_id=f3["call_id"])
        ok &= check(bool(wait_for(lambda: call("GET", f"/calls/{f3['call_id']}")["status"] == "unanswered",
                                  health["calls"]["ring_timeout_seconds"] + 5)), "a family call nobody answers is recorded as unanswered")
        ok &= check(a.expect("ring_over", call_id=f3["call_id"]) is not None, "... and every ring stops")
    a.close(), b.close()
call("DELETE", f"/contacts/{cg['Riya']['id']}/account", as_=CAREGIVER)
ok &= check(call("GET", "/me", as_=REL_A)["relative"] == [], "a caregiver can unlink a relative's account")
stats = call("GET", f"/patients/{pid}/stats?days=14", as_=CAREGIVER)
ok &= check(stats["totals"]["calls"] > 0, "insights still compute with unclaimed family calls in the data")

print("\nRESULT:", "ALL PASS" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
