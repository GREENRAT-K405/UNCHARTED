import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, setIdentity } from "../lib/api.js";
import { speechSupported } from "../lib/speech.js";
import CallPage from "./CallPage.jsx";

/**
 * Joining without an account: open the invite link (/join/<id>?t=…) or type the 6-character code (/join).
 * The guest gives their name and agrees before anything is recorded. Relatives who have an account can
 * use their dashboard instead, which needs no code.
 */
export default function JoinPage() {
  const { callId } = useParams();
  const [params] = useSearchParams();
  const nav = useNavigate();
  setIdentity(null); // a guest has no account; the link's token is their pass
  const [code, setCode] = useState(params.get("code") || "");
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);
  const [name, setName] = useState(() => {
    try { return localStorage.getItem("pehchaan.guestName") || ""; } catch { return ""; }
  });
  const [agree, setAgree] = useState(false);
  const [joined, setJoined] = useState(false);
  const [busy, setBusy] = useState(false);
  const token = params.get("t") || "";

  useEffect(() => {
    if (!callId) return;
    setError(null);
    api.get(`/calls/${callId}/receiver?t=${encodeURIComponent(token)}`).then(setInfo).catch((e) => setError(e.message));
  }, [callId, token]);

  async function findCode(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.post("/join/code", { code });
      nav(`/join/${r.call_id}?t=${encodeURIComponent(r.t)}`, { replace: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  function join(e) {
    e.preventDefault();
    try { localStorage.setItem("pehchaan.guestName", name.trim()); } catch {}
    setJoined(true);
  }

  if (joined) return <CallPage role="contact" joinToken={token} guestName={name.trim()} homePath="/" />;

  if (!callId) {
    return (
      <div className="join">
        <form className="join-card" onSubmit={findCode}>
          <div className="join-kicker">Join a video meet</div>
          <h1>Enter the meeting code</h1>
          <p className="muted">It's the 6 letters and numbers shown on the bottom of the patient's screen.</p>
          <input className="big-input code-input" autoFocus value={code} maxLength={8} placeholder="K7P3QX"
            onChange={(e) => setCode(e.target.value.toUpperCase())} aria-label="Meeting code" />
          {error && <div className="auth-error" role="alert">{error}</div>}
          <button className="btn-big btn-primary" disabled={busy || code.replace(/\W/g, "").length !== 6}>
            {busy ? "Finding the meet…" : "Continue"}
          </button>
          <p className="fine">Family member with an account? <Link to="/login">Sign in</Link> and join from your dashboard.</p>
        </form>
      </div>
    );
  }

  if (error) {
    return (
      <div className="join"><div className="join-card"><h2>This meet link isn't valid</h2><p>{error}</p>
        <Link className="btn" to="/join">Enter a code instead</Link></div></div>
    );
  }
  if (!info) return <div className="join"><div className="join-card">Loading…</div></div>;

  const over = ["ended", "processing", "processed"].includes(info.call.status);
  const patient = info.patient.name;
  return (
    <div className="join">
      <form className="join-card" onSubmit={join}>
        <div className="join-kicker">{info.call.title || "Video meet"}</div>
        <h1>{patient} would like to see you</h1>
        <label className="field">Your name
          <input required value={name} onChange={(e) => setName(e.target.value)} placeholder="So they know who you are" autoComplete="name" />
        </label>
        <div className="consent">
          <strong>Before you join</strong>
          <ul>
            <li>{patient} lives with memory loss. This app shows them who they are talking to.</li>
            <li>Your face is compared with the photos their family saved. If you are new, your photo and name are saved for the family to check.</li>
            <li>The conversation is transcribed so a short note can remind {patient} later. Audio and video are never stored.</li>
            <li>Press <b>Go off the record</b> at any time and nothing said then is saved.</li>
          </ul>
        </div>
        <label className="check">
          <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} /> I agree
        </label>
        {!speechSupported && <p className="fine">Live captions need Chrome. You can still type what you say.</p>}
        <button className="btn-big btn-primary" disabled={over || !agree || !name.trim()}>
          {over ? "This meet has ended" : "Join the meet"}
        </button>
      </form>
    </div>
  );
}
