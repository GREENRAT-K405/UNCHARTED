import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, openSocket } from "../lib/api.js";
import { deviceIdentity } from "../lib/identity.js";
import { phrases, talkedWhen } from "../lib/i18n.js";
import { listenOnce, speechSupported } from "../lib/speech.js";
import { speak, voiceStatus, whenVoicesReady } from "../lib/tts.js";
import { Avatar } from "../components/common.jsx";

const HOLD_MS = 3000; // long-press on the corner icon opens the family menu

// Space-padded matching works for any script (\b in JS regexes is ASCII-only).
const norm = (s) => ` ${(s || "").toLowerCase().replace(/[^\p{L}\p{M}\p{N}\s-]/gu, " ").replace(/\s+/g, " ").trim()} `;

/** "Call my son" -> Rahul. Name and nickname first, then relation as a whole word. */
export function matchContact(utterance, contacts) {
  const said = norm(utterance);
  const has = (phrase) => phrase && norm(phrase).trim() && said.includes(norm(phrase));
  return (
    contacts.find((c) => has(c.name)) ||
    contacts.find((c) => has(c.known_as)) ||
    contacts.find((c) => (c.nicknames || []).some(has)) ||
    contacts.find((c) => has(c.relation))
  );
}

/** A small corner icon; holding it for 3 seconds opens the family menu (not for the patient). */
function HoldButton({ onHold }) {
  const timer = useRef(null);
  const [holding, setHolding] = useState(false);
  const start = (e) => {
    e.preventDefault();
    setHolding(true);
    timer.current = setTimeout(() => { setHolding(false); onHold(); }, HOLD_MS);
  };
  const cancel = () => {
    clearTimeout(timer.current);
    setHolding(false);
  };
  return (
    <button className={`family-corner ${holding ? "holding" : ""}`} aria-label="Family menu (press and hold)"
      onPointerDown={start} onPointerUp={cancel} onPointerLeave={cancel} onPointerCancel={cancel}
      onContextMenu={(e) => e.preventDefault()}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && !e.repeat && start(e)} onKeyUp={cancel}>
      <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
        <circle cx="9" cy="8" r="3" /><circle cx="17" cy="9" r="2.4" /><path d="M3.5 19c.6-3 2.8-4.6 5.5-4.6s4.9 1.6 5.5 4.6M14.5 15.2c.8-.5 1.6-.7 2.5-.7 2 0 3.6 1.2 4 3.5" />
      </svg>
      <svg className="hold-ring" viewBox="0 0 40 40" aria-hidden><circle cx="20" cy="20" r="18" /></svg>
    </button>
  );
}

export default function PatientHome({ patient, session = {} }) {
  const nav = useNavigate();
  const lang = patient.language || "en-IN";
  const p = phrases(lang);
  const [contacts, setContacts] = useState([]);
  const [archived, setArchived] = useState([]); // never shown; only so "call Baba" can be answered gently
  const [guard, setGuard] = useState(null); // {contact, check}
  const [elsewhere, setElsewhere] = useState(false); // asked for an archived person
  const [listening, setListening] = useState(false);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [askNew, setAskNew] = useState(false);
  const [newName, setNewName] = useState("");
  const [invite, setInvite] = useState(null);
  const [menu, setMenu] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [, setNow] = useState(0);

  function load() {
    return api.get(`/patients/${patient.id}/contacts?status=active,archived`).then((cs) => {
      setContacts(cs.filter((c) => c.status === "active"));
      setArchived(cs.filter((c) => c.status === "archived"));
      setLoaded(true);
    });
  }

  useEffect(() => {
    load();
    // Live: a finished call's recap, or a caregiver's change, refreshes the tiles.
    const s = openSocket(`/ws/patient/${patient.id}`, (msg) => msg.type === "home_refresh" && load(),
      undefined, async () => ({ type: "auth", token: await deviceIdentity.getToken() }));
    const tick = setInterval(() => setNow((n) => n + 1), 60000); // "Talked today" rolls over at midnight
    return () => { s.close(); clearInterval(tick); };
  }, [patient.id]);

  // Tell the caregivers whether this device has a voice for the patient's language.
  useEffect(() => {
    whenVoicesReady().then(() => api.post(`/patients/${patient.id}/speech-status`, voiceStatus(lang)).catch(() => {}));
  }, [patient.id, lang]);

  const primary = contacts.find((c) => c.primary_caregiver);
  const say = (text) => speak(text, { lang });

  async function placeCall(contact, { force = false, emergency = false } = {}) {
    if (busy) return;
    setBusy(true);
    try {
      const res = await api.post("/calls", { patient_id: patient.id, contact_id: contact.id, force, emergency });
      if (res.allowed) {
        nav(`/call/${res.call_id}`);
      } else if (res.archived) {
        // Never call, never say why: offer someone else, gently.
        setElsewhere(true);
        say(`${p.someoneElse}${primary ? " " + p.wouldLove(primary.known_as || primary.name) : ""}`);
      } else {
        setGuard({ contact, check: res });
        say(res.message);
      }
    } finally {
      setBusy(false);
    }
  }

  /** The one button: ring the family, and whoever picks up first is the call. */
  async function joinFamily() {
    if (busy) return;
    setBusy(true);
    setNotice(null);
    try {
      const res = await api.post("/calls/family", { patient_id: patient.id });
      if (res.allowed) nav(`/call/${res.call_id}`);
      else {
        setNotice(p.familyBusy);
        say(p.familyBusy);
      }
    } finally {
      setBusy(false);
    }
  }

  async function voiceCall() {
    setListening(true);
    setNotice(null);
    say(p.whoAsk);
    await new Promise((r) => setTimeout(r, 1400));
    const said = await listenOnce(lang);
    setListening(false);
    if (!said) {
      setNotice(p.notHeard);
      return;
    }
    const match = matchContact(said, contacts) || matchContact(said, archived);
    if (match?.status === "archived") {
      placeCall(match); // the guard refuses; the gentle screen follows
    } else if (match) {
      say(p.calling(match.known_as || match.name, `your ${match.relation}`));
      placeCall(match);
    } else {
      setNotice(`“${said}” — ${p.notFound}`);
      say(p.notFound);
    }
  }

  async function requestNew() {
    if (!newName.trim()) return;
    await api.post("/contacts/request", { patient_id: patient.id, name: newName });
    setAskNew(false);
    setNewName("");
    const msg = `Thank you. ${primary?.name || "Your family"} will check and add ${newName.trim()} for you.`;
    setNotice(msg);
    say(msg);
  }

  // Only the patient's own account can invite (a shared, paired tablet cannot).
  async function inviteFamily() {
    setMenu(false);
    setInvite(await api.post(`/circles/${patient.id}/invites`, { role: "owner" }));
  }

  async function shareInvite() {
    const text = `${patient.name} invited you to help on Pehchaan. Sign in with this link:`;
    if (navigator.share) {
      try { await navigator.share({ title: "Pehchaan", text, url: invite.url }); return; } catch {}
    }
    await navigator.clipboard?.writeText(invite.url);
    setInvite({ ...invite, copied: true });
  }

  // Someone else to call instead: the primary caregiver and whoever they talk to most recently.
  const suggestions = [primary, ...[...contacts].sort((a, b) => (b.last_recap?.at || "").localeCompare(a.last_recap?.at || ""))]
    .filter((c, i, all) => c && all.indexOf(c) === i).slice(0, 3);

  return (
    <div className="patient-shell">
      <header className="patient-header">
        <div>
          <div className="hello">
            {p.greeting(new Date().getHours())}, {patient.name}
          </div>
          <div className="today">
            {new Date().toLocaleDateString(lang, { weekday: "long", day: "numeric", month: "long" })}
          </div>
        </div>
        {speechSupported && (
          <button className={`mic-btn ${listening ? "listening" : ""}`} onClick={voiceCall} disabled={listening}>
            <svg viewBox="0 0 24 24" width="40" height="40" fill="none" stroke="currentColor" strokeWidth="2"
              strokeLinecap="round" aria-hidden>
              <rect x="9" y="3" width="6" height="11" rx="3" />
              <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" />
            </svg>
            <span>{listening ? "…" : p.whoAsk}</span>
          </button>
        )}
      </header>

      {notice && <div className="patient-notice">{notice}</div>}

      {loaded && contacts.length > 0 && (
        <button className="join-family" onClick={joinFamily} disabled={busy}>
          <svg viewBox="0 0 24 24" width="48" height="48" fill="none" stroke="currentColor" strokeWidth="2"
            strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <rect x="3" y="6" width="13" height="12" rx="3" /><path d="m16 10 5-3v10l-5-3" />
          </svg>
          <span>{p.joinFamily}</span>
        </button>
      )}

      {loaded && contacts.length === 0 && (
        <div className="patient-empty">
          <p>Nobody is here yet. Your family will add the people you talk to.</p>
          {session.account && <button className="btn-big btn-primary" onClick={inviteFamily}>Invite my family</button>}
        </div>
      )}

      <main className="contact-grid">
        {contacts.map((c) => (
          <button key={c.id} className="contact-tile" onClick={() => placeCall(c)} disabled={busy}>
            <Avatar contact={c} size={168} />
            <span className="tile-name">{c.known_as || c.name}</span>
            <span className="tile-rel">your {c.relation}</span>
            {c.last_recap && (
              <span className="tile-recap">
                {talkedWhen(c.last_recap.at, lang)}{c.last_recap.text ? ` · ${c.last_recap.text}` : ""}
              </span>
            )}
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
          <span className="em-dot" /> Help — call {primary.known_as || primary.name} now
        </button>
      )}

      <HoldButton onHold={() => setMenu(true)} />

      {menu && (
        <div className="modal-backdrop" onClick={() => setMenu(false)}>
          <div className="modal family-menu" role="dialog" aria-modal="true" aria-label="Family menu" onClick={(e) => e.stopPropagation()}>
            <h2>Family menu</h2>
            <p className="muted">
              {session.account ? <>Signed in as <b>{session.account.email || patient.name}</b> ({patient.name}'s own account).</>
                : <>This screen ({session.device?.label || "tablet"}) was set up by {session.device?.paired_by_name || "family"}.
                  To unpair it, use <b>Family &amp; devices</b> on the dashboard.</>}
            </p>
            <div className="family-menu-actions">
              {session.account && <button className="btn" onClick={inviteFamily}>Invite family</button>}
              <button className="btn-ghost" onClick={() => location.reload()}>Reload this screen</button>
              {session.account && (
                <button className="btn-ghost danger" onClick={() => confirm("Sign out of this screen?") && session.signOut()}>Sign out</button>
              )}
              <button className="btn-ghost" onClick={() => setMenu(false)}>Close</button>
            </div>
          </div>
        </div>
      )}

      {invite && (
        <div className="modal-backdrop">
          <div className="modal patient-modal" role="dialog" aria-modal="true">
            <p className="guard-msg">Send this link to your family. They sign in, and then they can help you.</p>
            <code className="invite-url patient-invite">{invite.url}</code>
            {invite.copied && <p className="tile-rel">Copied. Paste it into a message to them.</p>}
            <div className="modal-actions">
              <button className="btn-big btn-calm" onClick={() => setInvite(null)}>Done</button>
              <button className="btn-big btn-primary" onClick={shareInvite}>{navigator.share ? "Send link" : "Copy link"}</button>
            </div>
          </div>
        </div>
      )}

      {elsewhere && (
        <div className="modal-backdrop">
          <div className="modal patient-modal" role="dialog" aria-modal="true">
            <p className="guard-msg">{p.someoneElse}</p>
            <div className="elsewhere-grid">
              {suggestions.map((c) => (
                <button key={c.id} className="contact-tile small" onClick={() => { setElsewhere(false); placeCall(c); }}>
                  <Avatar contact={c} size={110} />
                  <span className="tile-name">{c.known_as || c.name}</span>
                  <span className="tile-rel">your {c.relation}</span>
                </button>
              ))}
            </div>
            <button className="btn-big btn-calm" onClick={() => setElsewhere(false)}>{p.backHome}</button>
          </div>
        </div>
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
