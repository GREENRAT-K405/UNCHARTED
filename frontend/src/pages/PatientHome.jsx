import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api.js";
import { speak } from "../lib/tts.js";
import { Avatar } from "../components/common.jsx";
import { usePatientSession } from "./PatientGate.jsx";

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

function whenText(iso, startsIn) {
  const d = new Date(iso);
  const time = d.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });
  if (startsIn <= 0) return `Now · started ${time}`;
  if (startsIn < 3600) return `In ${Math.max(1, Math.round(startsIn / 60))} minutes · ${time}`;
  const today = new Date();
  const tomorrow = new Date(Date.now() + 86400000);
  if (d.toDateString() === today.toDateString()) return `Today at ${time}`;
  if (d.toDateString() === tomorrow.toDateString()) return `Tomorrow at ${time}`;
  return `${d.toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" })} at ${time}`;
}

function withWhom(meet) {
  if (meet.everyone || meet.people.length === 0) return "Everyone in the family";
  const names = meet.people.map((p) => p.name);
  return names.length <= 2 ? names.join(" and ") : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
}

export default function PatientHome({ patient }) {
  const nav = useNavigate();
  const { viaGoogle, identity } = usePatientSession();
  const [home, setHome] = useState(null);
  const [choose, setChoose] = useState(false);
  const [guard, setGuard] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let stop = false;
    const load = () => api.get("/patient/home").then((h) => !stop && setHome(h)).catch((e) => !stop && setError(e.message));
    load();
    const t = setInterval(load, 20000); // the Join buttons switch on as meets come close
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, []);

  async function start(body) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.post("/calls", body);
      if (res.allowed) {
        nav(`/call/${res.call_id}`);
      } else {
        setChoose(false);
        setGuard({ ...res, body });
        speak(res.message);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const upcoming = home?.upcoming || [];
  return (
    <div className="patient-shell">
      <header className="patient-header">
        <div>
          <div className="hello">{greeting()}, {patient.name}</div>
          <div className="today">{new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" })}</div>
        </div>
        {viaGoogle && <button className="btn-ghost" onClick={() => identity.signOut().then(() => location.reload())}>Sign out</button>}
      </header>

      {home?.live_call && (
        <button className="rejoin" onClick={() => nav(`/call/${home.live_call.id}`)}>
          You are in a meet right now. <b>Go back to it</b>
        </button>
      )}

      <button className="join-big" onClick={() => setChoose(true)} disabled={busy}>
        <svg viewBox="0 0 24 24" width="56" height="56" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
          <rect x="2.5" y="6" width="13" height="12" rx="2.5" /><path d="M15.5 10.5l6-3.5v10l-6-3.5" />
        </svg>
        Join meet
      </button>

      {error && <div className="patient-notice">{error}</div>}

      <section className="upcoming-meets">
        <h2>Coming up</h2>
        {!home && <p className="muted big">Loading…</p>}
        {home && upcoming.length === 0 && <p className="muted big">Nothing planned yet. Your family will add meets here.</p>}
        {upcoming.map((m) => (
          <div key={m.id} className={`meet-card ${m.can_join ? "ready" : ""}`}>
            <div className="meet-faces">
              {(m.everyone ? [] : m.people.slice(0, 3)).map((p) => <Avatar key={p.id} contact={p} size={72} />)}
            </div>
            <div className="meet-body">
              <div className="meet-when">{whenText(m.scheduled_at, m.starts_in_sec)}</div>
              <div className="meet-title">{m.title}</div>
              <div className="meet-with">with {withWhom(m)}</div>
            </div>
            {m.can_join ? (
              <button className="btn-big btn-primary" disabled={busy} onClick={() => start({ meet_id: m.id })}>Join now</button>
            ) : (
              <div className="meet-later">Later</div>
            )}
          </div>
        ))}
      </section>

      {choose && (
        <div className="modal-backdrop" onClick={() => setChoose(false)}>
          <div className="modal patient-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <p className="guard-msg">Who would you like to see?</p>
            <button className="choice" disabled={busy} onClick={() => start({ invite_all: true })}>
              <b>Invite all relatives</b>
              <span>Everyone in your family gets a message. The first one free will join you.</span>
            </button>
            <button className="choice" disabled={busy} onClick={() => start({})}>
              <b>New meet</b>
              <span>Start a meet and share the code or link with someone.</span>
            </button>
            <button className="btn-big btn-calm" onClick={() => setChoose(false)}>Back</button>
          </div>
        </div>
      )}

      {guard && (
        <div className="modal-backdrop">
          <div className="modal patient-modal" role="dialog" aria-modal="true">
            <p className="guard-msg">{guard.message}</p>
            <div className="modal-actions">
              <button className="btn-big btn-calm" onClick={() => setGuard(null)}>Okay, later</button>
              {guard.can_override && (
                <button className="btn-big btn-primary" onClick={() => { const b = guard.body; setGuard(null); start({ ...b, force: true }); }}>
                  Start anyway
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
