import { useEffect, useRef, useState } from "react";
import { Avatar, fmtDateTime } from "./common.jsx";

/** A soft two-tone ring through WebAudio (no audio file). Needs a user gesture first: "I'm ready". */
export function useRinger() {
  const ctx = useRef(null);
  const timer = useRef(null);
  function unlock() {
    try {
      ctx.current = ctx.current || new (window.AudioContext || window.webkitAudioContext)();
      ctx.current.resume();
    } catch {}
  }
  function beep(at, freq) {
    const c = ctx.current;
    const o = c.createOscillator();
    const g = c.createGain();
    o.frequency.value = freq;
    g.gain.setValueAtTime(0, at);
    g.gain.linearRampToValueAtTime(0.18, at + 0.04);
    g.gain.linearRampToValueAtTime(0, at + 0.38);
    o.connect(g).connect(c.destination);
    o.start(at);
    o.stop(at + 0.4);
  }
  function start() {
    stop();
    const ring = () => {
      if (ctx.current?.state === "running") {
        const t = ctx.current.currentTime;
        beep(t, 660); beep(t + 0.45, 880);
      }
      navigator.vibrate?.([300, 150, 300]);
    };
    ring();
    timer.current = setInterval(ring, 2200);
  }
  function stop() {
    clearInterval(timer.current);
    timer.current = null;
  }
  useEffect(() => stop, []);
  return { unlock, start, stop };
}

/** Full-screen incoming call. `family` calls say so: the patient pressed the one big button. */
export function RingScreen({ ring, onAccept, onDecline }) {
  return (
    <div className="ringing" role="alertdialog" aria-label={`${ring.patient_name} is calling`}>
      <div className="ring-avatar"><Avatar contact={{ name: ring.patient_name, color: "#1f5c4f" }} size={180} /></div>
      <h1>{ring.patient_name} is calling{ring.emergency ? " — asking for help" : ""}</h1>
      <p>
        {ring.family ? `${ring.patient_name} would like to talk to family. The first person to pick up gets the call. ` : ""}
        You'll see a short consent note first.
      </p>
      <div className="ring-actions">
        <button className="ring-btn decline" onClick={onDecline}>Decline</button>
        <button className="ring-btn accept" onClick={onAccept}>Accept</button>
      </div>
    </div>
  );
}

/** "Share an update" box, used on the link page and on the account page. */
export function ShareUpdate({ patientName, updates, send, onSaved }) {
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(null);

  async function submit(e) {
    e.preventDefault();
    if (!text.trim()) return;
    setSaving(true);
    try {
      setSaved(await send(text.trim()));
      setText("");
      onSaved?.();
    } catch (err) {
      setSaved({ error: err.message });
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="join-card" onSubmit={submit}>
      <h2>Share an update with {patientName}</h2>
      <p className="muted">News about your own life ({patientName}'s card may mention it on your next call).</p>
      <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} className="cl-update"
        placeholder={"I got engaged!\nMy exam is on Friday."} maxLength={1000} />
      <button className="btn" disabled={saving || !text.trim()}>{saving ? "Saving…" : "Share"}</button>
      {saved?.error && <div className="auth-error">{saved.error}</div>}
      {saved?.items && (
        <div className="auth-notice">
          {saved.items.length === 0 ? "Thanks! Nothing new to remember from that." : <>
            Saved for {patientName}:
            <ul>{saved.items.map((it, i) => (
              <li key={i}>{it.text}{it.private ? " (kept private: never shown to them)" : it.review ? " (the family will check it first)" : ""}</li>
            ))}</ul>
          </>}
        </div>
      )}
      {updates?.length > 0 && (
        <div className="muted small">Recent updates: {updates.map((u) => `“${u.text}”`).join(" · ")}</div>
      )}
    </form>
  );
}

/** Notes and past calls for one relative, from `GET /c/:token` or `GET /relative/contacts/:id`. */
export function RelativeNotes({ info }) {
  const { patient } = info;
  const any = info.hints.length > 0 || info.talking_points.length > 0 || info.known.length > 0;
  return (
    <>
      {any && (
        <div className="join-card">
          <h2>Good to know before you talk</h2>
          {info.hints.map((h, i) => <p key={`h${i}`}><b>If {patient.name} asks about {h.name}:</b> {h.note}</p>)}
          {info.talking_points.length > 0 && <p><b>You could ask:</b> {info.talking_points.join(" · ")}</p>}
          {info.known.length > 0 && (
            <>
              <div className="rn-title">What {patient.name}'s memory has about you</div>
              <ul>{info.known.map((k) => <li key={k.id}>{k.text}</li>)}</ul>
            </>
          )}
        </div>
      )}
      <div className="join-card">
        <h2>Your calls with {patient.name}</h2>
        {info.past_calls.length === 0 && <p className="muted">No calls yet.</p>}
        <ul className="cl-calls">
          {info.past_calls.map((c) => (
            <li key={c.id}>
              <span className="muted small">{fmtDateTime(c.started_at)}{c.duration_sec ? ` · ${Math.max(1, Math.round(c.duration_sec / 60))} min` : ""}</span>
              <span>{c.recap || "—"}</span>
            </li>
          ))}
        </ul>
        <p className="fine">These are the short notes {patient.name} heard after each call.</p>
      </div>
    </>
  );
}
