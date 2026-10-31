import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, getPatient, openSocket, resetPatientCache } from "../lib/api.js";
import { useServerClock } from "../components/common.jsx";
import Overview from "./caregiver/Overview.jsx";
import ReviewQueue from "./caregiver/ReviewQueue.jsx";
import Inspector from "./caregiver/Inspector.jsx";
import Briefs from "./caregiver/Briefs.jsx";
import Calls from "./caregiver/Calls.jsx";
import Contacts from "./caregiver/Contacts.jsx";

const TABS = [
  ["overview", "Live"],
  ["review", "Review queue"],
  ["memories", "Memory inspector"],
  ["briefs", "Briefs"],
  ["calls", "Calls"],
  ["contacts", "Contacts & rules"],
];

export default function Caregiver() {
  const [patient, setPatient] = useState(null);
  const [tab, setTab] = useState(() => {
    try { return localStorage.getItem("cg-tab") || "overview"; } catch { return "overview"; }
  });
  const [health, setHealth] = useState(null);
  const [wsState, setWsState] = useState("closed");
  const [feed, setFeed] = useState([]); // change-stream events
  const [liveTranscript, setLiveTranscript] = useState([]);
  const [tick, setTick] = useState(0); // bumps on any memory/call change so panels refetch
  const [contacts, setContacts] = useState([]);
  const [pendingCount, setPendingCount] = useState(0);
  const [focusMemory, setFocusMemory] = useState(null);
  const [offsetMs, setOffsetMs] = useServerClock();
  const titles = useRef(new Map()); // id -> content, so delete events stay readable

  useEffect(() => {
    getPatient().then(setPatient);
    api.get("/health").then(setHealth);
  }, []);

  useEffect(() => {
    try { localStorage.setItem("cg-tab", tab); } catch {}
  }, [tab]);

  useEffect(() => {
    if (!patient) return;
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
    }, setWsState);
    return () => s.close();
  }, [patient]);

  useEffect(() => {
    if (!patient) return;
    api.get(`/patients/${patient.id}/contacts?status=all`).then(setContacts);
    api.get(`/memories?patient_id=${patient.id}&status=pending_review`).then((m) => setPendingCount(m.length));
  }, [patient, tick]);

  const ctx = useMemo(
    () => ({ patient, contacts, tick, refresh: () => setTick((t) => t + 1), offsetMs, titles: titles.current,
      openMemory: (id) => { setFocusMemory(id); setTab("memories"); } }),
    [patient, contacts, tick, offsetMs]
  );

  async function shiftClock(days) {
    const c = await api.post("/clock", days === 0 ? { reset: true } : { shift_days: days });
    setOffsetMs(new Date(c.now).getTime() - Date.now());
    setTick((t) => t + 1);
  }

  async function resetDemo() {
    if (!confirm("Reset all demo data to the seeded state?")) return;
    await api.post("/demo/reset");
    resetPatientCache();
    setFeed([]);
    setLiveTranscript([]);
    const p = await getPatient();
    setPatient(p);
    setOffsetMs(0);
    setTick((t) => t + 1);
  }

  if (!patient) return <div className="cg loading">Loading…</div>;
  const demoNow = new Date(Date.now() + offsetMs);

  return (
    <div className="cg">
      <aside className="cg-nav">
        <Link to="/" className="cg-brand">Pehchaan</Link>
        <div className="cg-patient">
          <div className="cg-pname">{patient.full_name || patient.name}</div>
          <div className="cg-psub">Caring for “{patient.name}”</div>
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
              <div><span className={`dot ${health.llm.mode === "claude" ? "ok" : "warn"}`} /> Agent: {health.llm.mode}</div>
              <div><span className={`dot ${health.embeddings === "voyage" ? "ok" : "warn"}`} /> Embeddings: {health.embeddings}</div>
            </>
          )}
          <Link to="/patient" className="btn-link">Open patient screen ↗</Link>
        </div>
      </aside>

      <section className="cg-main">
        <header className="cg-top">
          <h1>{TABS.find((t) => t[0] === tab)[1]}</h1>
          <div className="clock">
            <span className="clock-label">Demo clock</span>
            <span className="clock-now">
              {demoNow.toLocaleString("en-IN", { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })}
            </span>
            {Math.abs(offsetMs) > 60000 && <span className="chip chip-warn">+{(offsetMs / 86400000).toFixed(1)}d</span>}
            <button className="btn-sm" onClick={() => shiftClock(1)}>+1 day</button>
            <button className="btn-sm" onClick={() => shiftClock(2)}>+2 days</button>
            <button className="btn-sm" onClick={() => shiftClock(0)}>Real time</button>
            <button className="btn-sm danger" onClick={resetDemo}>Reset demo</button>
          </div>
        </header>
        <div className="cg-body">
          {tab === "overview" && <Overview ctx={ctx} feed={feed} liveTranscript={liveTranscript} />}
          {tab === "review" && <ReviewQueue ctx={ctx} />}
          {tab === "memories" && <Inspector ctx={ctx} focus={focusMemory} onFocus={setFocusMemory} />}
          {tab === "briefs" && <Briefs ctx={ctx} />}
          {tab === "calls" && <Calls ctx={ctx} />}
          {tab === "contacts" && <Contacts ctx={ctx} />}
        </div>
      </section>
    </div>
  );
}
