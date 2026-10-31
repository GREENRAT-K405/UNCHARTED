import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, getPatient } from "../lib/api.js";
import { listenOnce, speechSupported } from "../lib/speech.js";
import { speak } from "../lib/tts.js";
import { Avatar } from "../components/common.jsx";

const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** "Call my son" -> Rahul. Name and nickname first, then relation as a whole word. */
export function matchContact(utterance, contacts) {
  const said = ` ${utterance.toLowerCase().replace(/[^\p{L}\s-]/gu, " ")} `;
  const has = (phrase) => phrase && new RegExp(`\\b${escape(phrase.toLowerCase())}\\b`).test(said);
  return (
    contacts.find((c) => has(c.name)) ||
    contacts.find((c) => (c.nicknames || []).some(has)) ||
    contacts.find((c) => has(c.relation))
  );
}

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

export default function PatientHome() {
  const nav = useNavigate();
  const [patient, setPatient] = useState(null);
  const [contacts, setContacts] = useState([]);
  const [guard, setGuard] = useState(null); // {contact, check}
  const [listening, setListening] = useState(false);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [askNew, setAskNew] = useState(false);
  const [newName, setNewName] = useState("");

  useEffect(() => {
    getPatient().then(async (p) => {
      setPatient(p);
      setContacts(await api.get(`/patients/${p.id}/contacts`));
    });
  }, []);

  const primary = contacts.find((c) => c.primary_caregiver);

  async function placeCall(contact, { force = false, emergency = false } = {}) {
    if (busy) return;
    setBusy(true);
    try {
      const res = await api.post("/calls", { patient_id: patient.id, contact_id: contact.id, force, emergency });
      if (res.allowed) {
        nav(`/call/${res.call_id}`);
      } else {
        setGuard({ contact, check: res });
        speak(res.message);
      }
    } finally {
      setBusy(false);
    }
  }

  async function voiceCall() {
    setListening(true);
    setNotice(null);
    speak("Who do you want to talk to?");
    await new Promise((r) => setTimeout(r, 1400));
    const said = await listenOnce();
    setListening(false);
    if (!said) {
      setNotice("I didn't hear anything. You can tap a photo instead.");
      return;
    }
    const match = matchContact(said, contacts);
    if (match) {
      speak(`Calling ${match.name}, your ${match.relation}.`);
      placeCall(match);
    } else {
      setNotice(`I heard “${said}”. I couldn't find that person. Tap a photo instead.`);
      speak("I couldn't find that person. You can tap a photo instead.");
    }
  }

  async function requestNew() {
    if (!newName.trim()) return;
    await api.post("/contacts/request", { patient_id: patient.id, name: newName });
    setAskNew(false);
    setNewName("");
    const msg = `Thank you. ${primary?.name || "Your family"} will check and add ${newName.trim()} for you.`;
    setNotice(msg);
    speak(msg);
  }

  if (!patient) return <div className="patient-shell loading">Loading…</div>;

  return (
    <div className="patient-shell">
      <header className="patient-header">
        <div>
          <div className="hello">
            {greeting()}, {patient.name}
          </div>
          <div className="today">
            {new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" })}
          </div>
        </div>
        {speechSupported && (
          <button className={`mic-btn ${listening ? "listening" : ""}`} onClick={voiceCall} disabled={listening}>
            <svg viewBox="0 0 24 24" width="40" height="40" fill="none" stroke="currentColor" strokeWidth="2"
              strokeLinecap="round" aria-hidden>
              <rect x="9" y="3" width="6" height="11" rx="3" />
              <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" />
            </svg>
            <span>{listening ? "Listening…" : "Who do you want to talk to?"}</span>
          </button>
        )}
      </header>

      {notice && <div className="patient-notice">{notice}</div>}

      <main className="contact-grid">
        {contacts.map((c) => (
          <button key={c.id} className="contact-tile" onClick={() => placeCall(c)} disabled={busy}>
            <Avatar contact={c} size={168} />
            <span className="tile-name">{c.name}</span>
            <span className="tile-rel">your {c.relation}</span>
          </button>
        ))}
        <button className="contact-tile tile-new" onClick={() => setAskNew(true)}>
          <div className="avatar plus" style={{ width: 168, height: 168 }}>+</div>
          <span className="tile-name">Someone new</span>
          <span className="tile-rel">ask {primary?.name || "family"} to add</span>
        </button>
      </main>

      {primary && (
        <button className="emergency" onClick={() => placeCall(primary, { emergency: true })}>
          <span className="em-dot" /> Help — call {primary.name} now
        </button>
      )}

      {guard && (
        <div className="modal-backdrop">
          <div className="modal patient-modal" role="dialog" aria-modal="true">
            <Avatar contact={guard.contact} size={120} />
            <p className="guard-msg">{guard.check.message}</p>
            <div className="modal-actions">
              <button className="btn-big btn-calm" onClick={() => setGuard(null)}>
                Okay, later
              </button>
              {guard.check.can_override && (
                <button className="btn-big btn-primary"
                  onClick={() => { const c = guard.contact; setGuard(null); placeCall(c, { force: true }); }}>
                  Call anyway
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {askNew && (
        <div className="modal-backdrop">
          <div className="modal patient-modal" role="dialog" aria-modal="true">
            <p className="guard-msg">Who would you like to add? {primary?.name || "Your family"} will check first.</p>
            <input className="big-input" autoFocus value={newName} onChange={(e) => setNewName(e.target.value)}
              placeholder="Their name" onKeyDown={(e) => e.key === "Enter" && requestNew()} />
            <div className="modal-actions">
              <button className="btn-big btn-calm" onClick={() => setAskNew(false)}>Cancel</button>
              <button className="btn-big btn-primary" onClick={requestNew}>Ask {primary?.name || "family"}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
