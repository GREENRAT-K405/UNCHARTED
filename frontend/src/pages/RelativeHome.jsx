import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api } from "../lib/api.js";
import { useCaregiverAuth } from "../lib/auth.jsx";
import { Avatar } from "../components/common.jsx";
import CallPage from "./CallPage.jsx";

function when(iso) {
  return new Date(iso).toLocaleString("en-IN", { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

/** Signed-in relative only; anyone else is sent to their own home. */
function RelativeOnly({ children }) {
  const { user, me, error } = useCaregiverAuth();
  if (user === undefined || (user && !me && !error)) return <div className="cg loading">Signing you in…</div>;
  if (!user || user.anonymous) return <Navigate to={`/login?next=${encodeURIComponent(location.pathname)}`} replace />;
  if (error) return <div className="cg loading">Couldn't reach the server: {error}</div>;
  if (me.kind !== "relative") return <Navigate to={me.home || "/caregiver"} replace />;
  return children(me);
}

export default function RelativeHome() {
  return <RelativeOnly>{(me) => <Dashboard me={me} />}</RelativeOnly>;
}

export function RelativeCall() {
  const { identity } = useCaregiverAuth();
  return <RelativeOnly>{() => <CallPage role="contact" identity={identity} homePath="/relative" />}</RelativeOnly>;
}

function Dashboard({ me }) {
  const nav = useNavigate();
  const { signOut } = useCaregiverAuth();
  const [home, setHome] = useState(null);
  const [error, setError] = useState(null);
  const [code, setCode] = useState("");

  useEffect(() => {
    let stop = false;
    const load = () => api.get("/relative/home").then((h) => { if (!stop) { setHome(h); setError(null); } }).catch((e) => !stop && setError(e.message));
    load();
    const t = setInterval(load, 4000); // invites show up within seconds
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, []);

  async function joinCode(e) {
    e.preventDefault();
    try {
      const r = await api.post("/join/code", { code });
      nav(`/meet/${r.call_id}`);
    } catch (err) {
      setError(err.message);
    }
  }

  if (!home) return <div className="rel loading">{error || "Loading…"}</div>;
  const patient = home.patient.name;
  return (
    <div className="rel">
      <header className="rel-top">
        <div className="rel-me">
          <Avatar contact={{ name: home.contact.name, photo: home.contact.photo, color: "#1f5c4f" }} size={52} />
          <div>
            <div className="rel-name">Hi {home.contact.name}</div>
            <div className="muted">{patient}'s {home.contact.relation}</div>
          </div>
        </div>
        <button className="btn-ghost" onClick={signOut}>Sign out</button>
      </header>

      {home.live.length > 0 ? (
        home.live.map((c) => (
          <div key={c.call_id} className={`ring ${c.taken && !c.mine ? "taken" : ""}`}>
            <div>
              <div className="ring-title">{c.taken && !c.mine ? `${patient} is already talking with someone` : `${patient} is inviting you to a meet`}</div>
              <div className="muted">{c.title || "Video meet"} · started {when(c.started_at)}</div>
            </div>
            {(!c.taken || c.mine) && <button className="btn-big btn-primary" onClick={() => nav(`/meet/${c.call_id}`)}>Join now</button>}
          </div>
        ))
      ) : (
        <div className="panel rel-quiet">No invites right now. When {patient} invites you, it appears here.</div>
      )}

      <div className="grid-2 rel-grid">
        <div className="panel">
          <div className="panel-title">Scheduled meets</div>
          {home.upcoming.length === 0 && <div className="empty">Nothing scheduled with you yet.</div>}
          <ul className="rel-list">
            {home.upcoming.map((m) => (
              <li key={m.id}>
                <b>{m.title}</b>
                <span className="muted">{when(m.scheduled_at)} · {m.everyone ? "whole family" : m.people.map((p) => p.name).join(", ")}</span>
                {m.agenda && <span className="small">{m.agenda}</span>}
              </li>
            ))}
          </ul>
          <form className="row rel-code" onSubmit={joinCode}>
            <input className="code-input" value={code} maxLength={8} placeholder="Meeting code"
              onChange={(e) => setCode(e.target.value.toUpperCase())} aria-label="Meeting code" />
            <button className="btn" disabled={code.replace(/\W/g, "").length !== 6}>Join with code</button>
          </form>
          {error && <div className="auth-error">{error}</div>}
        </div>
        <div className="panel">
          <div className="panel-title">Your recent meets with {patient}</div>
          {home.past.length === 0 && <div className="empty">None yet.</div>}
          <ul className="rel-list">
            {home.past.map((c, i) => (
              <li key={i}>
                <b>{when(c.at)}</b>
                <span className="muted">{c.title || "Meet"}{c.duration_sec ? ` · ${Math.max(1, Math.round(c.duration_sec / 60))} min` : ""}</span>
                {c.summary && <span className="small">{c.summary}</span>}
              </li>
            ))}
          </ul>
        </div>
      </div>
      <p className="fine center">Signed in as {me.email}. Only the caretaker can change your details or photos.</p>
    </div>
  );
}
