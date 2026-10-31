import { useEffect, useMemo, useRef, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { api, openSocket } from "../lib/api.js";
import { useCaregiverAuth } from "../lib/auth.jsx";
import { firebaseEnabled } from "../lib/identity.js";
import { useServerClock } from "../components/common.jsx";
import Overview from "./caregiver/Overview.jsx";
import ReviewQueue from "./caregiver/ReviewQueue.jsx";
import Inspector from "./caregiver/Inspector.jsx";
import Briefs from "./caregiver/Briefs.jsx";
import Calls from "./caregiver/Calls.jsx";
import Contacts from "./caregiver/Contacts.jsx";
import Family from "./caregiver/Family.jsx";
import Onboarding from "./caregiver/Onboarding.jsx";

const TABS = [
  ["overview", "Overview"],
  ["review", "Review queue"],
  ["memories", "Memory inspector"],
  ["briefs", "Briefs"],
  ["calls", "Calls"],
  ["contacts", "Contacts & rules"],
  ["family", "Family & devices"],
];

const store = {
  get: (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch {} },
};

export default function Caregiver() {
  const { user, me, error } = useCaregiverAuth();
  if (user === undefined || (user && !me && !error)) return <div className="cg loading">Signing you in…</div>;
  if (!user || user.anonymous) return <Navigate to="/login?next=/caregiver" replace />;
  if (error) return <div className="cg loading">Couldn't reach the server: {error}</div>;
  if (me.circles.length === 0) return <Onboarding />;
  return <Dashboard />;
}

function Dashboard() {
  const auth = useCaregiverAuth();
  const { me } = auth;
  const [circleId, setCircleId] = useState(() => {
    const saved = store.get("cg-circle");
    return me.circles.some((c) => c.patient.id === saved) ? saved : me.circles[0].patient.id;
  });
  const circle = me.circles.find((c) => c.patient.id === circleId) || me.circles[0];
  const patient = circle.patient;
  const canEdit = circle.role !== "viewer";

  const [tab, setTab] = useState(() => store.get("cg-tab", "overview"));
  const [health, setHealth] = useState(null);
  const [wsState, setWsState] = useState("closed");
  const [feed, setFeed] = useState([]); // change-stream events
  const [liveTranscript, setLiveTranscript] = useState([]);
  const [tick, setTick] = useState(0); // bumps on any memory/call change so panels refetch
  const [contacts, setContacts] = useState([]);
  const [pendingCount, setPendingCount] = useState(0);
  const [focusMemory, setFocusMemory] = useState(null);
  const [menu, setMenu] = useState(false);
  const [offsetMs, setOffsetMs] = useServerClock();
  const titles = useRef(new Map()); // id -> content, so delete events stay readable

  useEffect(() => { api.get("/health").then(setHealth); }, []);
  useEffect(() => store.set("cg-tab", tab), [tab]);
  useEffect(() => store.set("cg-circle", circle.patient.id), [circle.patient.id]);

  useEffect(() => {
    setFeed([]);
    setLiveTranscript([]);
    let timer = null;
    const bump = () => {
      clearTimeout(timer);
      timer = setTimeout(() => setTick((t) => t + 1), 400);
    };
    const s = openSocket(`/ws/caregiver/${patient.id}`, (msg) => {
      if (msg.type === "memory") {
        if (msg.memory) titles.current.set(msg.id, msg.memory.content);
        const content = msg.memory?.content || titles.current.get(msg.id) || "(memory)";
        setFeed((f) => [{ ...msg, content, at: Date.now() }, ...f].slice(0, 80));
        bump();
      } else if (msg.type === "call") {
        setFeed((f) => [{ ...msg, at: Date.now() }, ...f].slice(0, 80));
        bump();
      } else if (msg.type === "transcript") {
        setLiveTranscript((t) => [...t.slice(-40), msg]);
      } else if (msg.type === "contact_request") {
        setFeed((f) => [{ ...msg, at: Date.now() }, ...f]);
        bump();
      }
    }, setWsState, async () => ({ type: "auth", token: await auth.identity.getToken() }));
    return () => s.close();
  }, [patient.id]);

  useEffect(() => {
    api.get(`/patients/${patient.id}/contacts?status=all`).then(setContacts);
    api.get(`/memories?patient_id=${patient.id}&status=pending_review`).then((m) => setPendingCount(m.length));
  }, [patient.id, tick]);

  const ctx = useMemo(
    () => ({ patient, role: circle.role, canEdit, me, contacts, tick, refresh: () => setTick((t) => t + 1), offsetMs,
      titles: titles.current, openMemory: (id) => { setFocusMemory(id); setTab("memories"); }, goTo: setTab }),
    [patient, circle.role, contacts, tick, offsetMs]
  );

  async function shiftClock(days) {
    const c = await api.post("/clock", days === 0 ? { reset: true } : { shift_days: days });
    setOffsetMs(new Date(c.now).getTime() - Date.now());
    setTick((t) => t + 1);
  }

  async function resetDemo() {
    if (!confirm("Reset the demo family to its seeded six weeks of history? Changes made during the demo are lost.")) return;
    await api.post("/demo/reset");
    setFeed([]);
    setLiveTranscript([]);
    setOffsetMs(0);
    setTick((t) => t + 1);
  }

  const demoNow = new Date(Date.now() + offsetMs);
  const initials = (me.name || me.email || "?").split(/[\s@]/).map((p) => p[0]).join("").slice(0, 2).toUpperCase();

  return (
    <div className="cg">
      <aside className="cg-nav">
        <Link to="/" className="cg-brand">Pehchaan</Link>
        <div className="cg-patient">
          {me.circles.length > 1 ? (
            <select className="circle-select" value={circle.patient.id} onChange={(e) => setCircleId(e.target.value)}
              aria-label="Whose care circle">
              {me.circles.map((c) => <option key={c.patient.id} value={c.patient.id}>{c.patient.full_name || c.patient.name}</option>)}
            </select>
          ) : <div className="cg-pname">{patient.full_name || patient.name}</div>}
          <div className="cg-psub">Caring for “{patient.name}” · you are {circle.role}</div>
        </div>
        <nav>
          {TABS.map(([id, label]) => (
            <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>
              {label}
              {id === "review" && pendingCount > 0 && <span className="badge">{pendingCount}</span>}
            </button>
          ))}
        </nav>
        <div className="cg-sys">
          <div><span className={`dot ${wsState === "open" ? "ok" : "bad"}`} /> Live updates {wsState === "open" ? "on" : "off"}</div>
          {health && (
            <>
              <div><span className={`dot ${health.capabilities.change_streams ? "ok" : "warn"}`} /> Change streams</div>
              <div><span className={`dot ${health.capabilities.vector_search ? "ok" : "warn"}`} /> Vector search</div>
              <div><span className={`dot ${health.llm.mode !== "offline" ? "ok" : "warn"}`} /> Agent: {health.llm.mode}</div>
              <div><span className={`dot ${health.embeddings === "voyage" ? "ok" : "warn"}`} /> Embeddings: {health.embeddings}</div>
              <div><span className={`dot ${health.auth === "firebase" ? "ok" : "warn"}`} /> Auth: {health.auth === "firebase" ? "Firebase" : "dev mode"}</div>
            </>
          )}
          <Link to="/patient" className="btn-link">Open patient screen ↗</Link>
        </div>
      </aside>

      <section className="cg-main">
        <header className="cg-top">
          <h1>{TABS.find((t) => t[0] === tab)?.[1]}</h1>
          <div className="clock">
            {patient.demo && (
              <>
                <span className="clock-label">Demo clock</span>
                <span className="clock-now">
                  {demoNow.toLocaleString("en-IN", { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })}
                </span>
                {Math.abs(offsetMs) > 60000 && <span className="chip chip-warn">+{(offsetMs / 86400000).toFixed(1)}d</span>}
                <button className="btn-sm" onClick={() => shiftClock(1)}>+1 day</button>
                <button className="btn-sm" onClick={() => shiftClock(2)}>+2 days</button>
                <button className="btn-sm" onClick={() => shiftClock(0)}>Real time</button>
                {circle.role === "owner" && <button className="btn-sm danger" onClick={resetDemo}>Reset demo</button>}
              </>
            )}
            <div className="user-menu">
              <button className="avatar-btn" onClick={() => setMenu((m) => !m)} aria-expanded={menu} aria-label="Account">
                {me.picture ? <img src={me.picture} alt="" referrerPolicy="no-referrer" /> : initials}
              </button>
              {menu && (
                <div className="menu-pop" role="menu">
                  <div className="menu-who">
                    <b>{me.name || "Caregiver"}</b>
                    <span>{me.email}</span>
                    <span className="muted small">
                      Signed in with {me.provider === "google.com" ? "Google" : me.provider === "password" ? "email" : firebaseEnabled ? me.provider : "dev account"}
                    </span>
                  </div>
                  <button role="menuitem" onClick={() => { setMenu(false); setTab("family"); }}>Family &amp; devices</button>
                  <button role="menuitem" className="danger" onClick={auth.signOut}>Sign out</button>
                </div>
              )}
            </div>
          </div>
        </header>
        {!canEdit && <div className="viewer-banner">You have view-only access to {patient.name}'s circle. Ask an owner to change your role.</div>}
        <div className="cg-body">
          {tab === "overview" && <Overview ctx={ctx} feed={feed} liveTranscript={liveTranscript} />}
          {tab === "review" && <ReviewQueue ctx={ctx} />}
          {tab === "memories" && <Inspector ctx={ctx} focus={focusMemory} onFocus={setFocusMemory} />}
          {tab === "briefs" && <Briefs ctx={ctx} />}
          {tab === "calls" && <Calls ctx={ctx} />}
          {tab === "contacts" && <Contacts ctx={ctx} />}
          {tab === "family" && <Family ctx={ctx} refreshMe={auth.refreshMe} />}
        </div>
      </section>
    </div>
  );
}
