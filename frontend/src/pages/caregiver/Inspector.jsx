import { useEffect, useState } from "react";
import { api } from "../../lib/api.js";
import { Chip, fmtDate, fmtDateTime, relTime, tone } from "../../components/common.jsx";

const TYPES = ["relationship", "fact", "event", "plan", "preference", "constraint", "topic", "sensitive", "feedback"];
const STATUSES = ["active", "pending_review", "superseded", "rejected"];

export default function Inspector({ ctx, focus, onFocus }) {
  const { patient, contacts, tick } = ctx;
  const [filters, setFilters] = useState({ contact: "", type: "", status: "active,pending_review,superseded" });
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [list, setList] = useState([]);

  useEffect(() => {
    const p = new URLSearchParams({ patient_id: patient.id });
    if (filters.contact) p.set("contact_id", filters.contact);
    if (filters.status) p.set("status", filters.status);
    if (filters.type && !query) p.set("type", filters.type);
    if (query) p.set("q", query);
    api.get(`/memories?${p}`).then(setList);
  }, [patient, tick, filters, query]);

  const name = (id) => (id ? contacts.find((c) => c.id === id)?.name || "?" : "Everyone");
  const set = (k) => (e) => setFilters((f) => ({ ...f, [k]: e.target.value }));

  return (
    <div className="inspector">
      <div className="insp-list panel">
        <form className="search" onSubmit={(e) => { e.preventDefault(); setQuery(q.trim()); }}>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Semantic search, e.g. “pets” or “where she lives”" />
          {query && <button type="button" className="btn-ghost" onClick={() => { setQ(""); setQuery(""); }}>Clear</button>}
        </form>
        <div className="filters">
          <select value={filters.contact} onChange={set("contact")}>
            <option value="">All people</option>
            <option value="none">Everyone (global)</option>
            {contacts.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <select value={filters.type} onChange={set("type")} disabled={!!query}>
            <option value="">All types</option>
            {TYPES.map((t) => <option key={t}>{t}</option>)}
          </select>
          <select value={filters.status} onChange={set("status")}>
            <option value="active,pending_review,superseded">Active, pending &amp; superseded</option>
            <option value="">Any status</option>
            {STATUSES.map((s) => <option key={s}>{s}</option>)}
          </select>
        </div>
        <div className="mem-rows">
          {list.length === 0 && <div className="empty">No memories match.</div>}
          {list.map((m) => (
            <button key={m.id} className={`mem-row ${focus === m.id ? "sel" : ""} ${m.status}`} onClick={() => onFocus(m.id)}>
              <div className="mem-row-top">
                <Chip>{m.type}</Chip>
                <span className="mem-who">{name(m.contact_id)}</span>
                {m.score != null && <span className="score">sim {m.score.toFixed(2)}</span>}
                {m.status !== "active" && <Chip tone={tone(m.status)}>{m.status.replace("_", " ")}</Chip>}
                {m.visibility !== "patient" && <Chip tone={tone(m.visibility)}>{m.visibility.replace("_", " ")}</Chip>}
                {m.expires_at && <Chip tone="warn" title={m.expires_at}>expires {relTime(m.expires_at, ctx.offsetMs)}</Chip>}
              </div>
              <div className="mem-content">{m.content}</div>
            </button>
          ))}
        </div>
      </div>
      <div className="insp-detail">
        {focus ? <MemoryDetail key={focus + tick} id={focus} ctx={ctx} onFocus={onFocus} name={name} /> :
          <div className="panel empty-state">Select a memory to see where it came from, how it changed, and why it was picked for a brief.</div>}
      </div>
    </div>
  );
}

function Bar({ label, value, weight }) {
  return (
    <div className="bar">
      <span className="bar-label">{label}</span>
      <span className="bar-track"><span className="bar-fill" style={{ width: `${Math.round(value * 100)}%` }} /></span>
      <span className="bar-val">{value.toFixed(2)}{weight != null && <span className="muted"> ×{weight}</span>}</span>
    </div>
  );
}

function MemoryDetail({ id, ctx, onFocus, name }) {
  const [d, setD] = useState(null);
  const [gone, setGone] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    api.get(`/memories/${id}`).then(setD).catch(() => setGone(true));
  }, [id]);

  if (gone) return <div className="panel empty-state">This memory no longer exists (forgotten or expired).</div>;
  if (!d) return <div className="panel">Loading…</div>;
  const m = d.memory;

  async function patch(body) {
    await api.patch(`/memories/${m.id}`, body);
    setEditing(false);
    ctx.refresh();
  }
  async function forget() {
    if (!confirm("Forget this memory? It is deleted permanently; the audit log keeps only its id.")) return;
    await api.del(`/memories/${m.id}`);
    ctx.refresh();
    onFocus(null);
  }

  const lastUse = d.used_in_briefs[0]?.item;
  return (
    <div className="panel detail">
      <div className="detail-head">
        <Chip>{m.type}</Chip>
        <Chip tone={tone(m.status)}>{m.status.replace("_", " ")}</Chip>
        <Chip tone={tone(m.visibility)}>{m.visibility.replace("_", " ")}</Chip>
        <span className="muted">v{m.version} · about {name(m.contact_id)}</span>
      </div>
      {editing ? (
        <div className="edit">
          <textarea rows={3} value={draft} onChange={(e) => setDraft(e.target.value)} />
          <div className="row">
            <button className="btn" onClick={() => patch({ content: draft })}>Save correction</button>
            <button className="btn-ghost" onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </div>
      ) : (
        <h2 className="detail-content">{m.content}</h2>
      )}
      {d.expired_on_demo_clock && <div className="warnbox">Expired on the demo clock; MongoDB TTL will delete it once real time passes {fmtDateTime(m.expires_at)}.</div>}

      <div className="kv">
        <div><span>Source</span>{m.source?.kind === "call" ? `Call · said by ${m.source.speaker} (${m.source.origin})`
          : m.source?.kind === "contact_update" ? `Update from ${name(m.contact_id) || "the contact"}'s link (${m.source.origin})` : m.source?.kind}</div>
        {m.visibility_before_archive && <div><span>Archived</span>hidden while the person is archived (was {m.visibility_before_archive})</div>}
        {m.identity_asks > 0 && <div><span>“Who is this?”</span>asked more than once on {m.identity_asks} {m.identity_asks === 1 ? "call" : "calls"}</div>}
        {m.source?.quote && <div><span>Quote</span>“{m.source.quote}”</div>}
        {d.source_call && <div><span>From call</span>{fmtDateTime(d.source_call.started_at)} — {d.source_call.summary}</div>}
        <div><span>Trust / confidence</span>{m.trust} / {m.confidence}</div>
        <div><span>Importance</span>{m.importance} {m.feedback && `(👍 ${m.feedback.helpful} · 👎 ${m.feedback.not_helpful})`}</div>
        {m.event_date && <div><span>Event date</span>{fmtDate(m.event_date)}</div>}
        <div><span>Expires</span>{m.expires_at ? `${fmtDateTime(m.expires_at)} (TTL index)` : "never (permanent)"}</div>
        <div><span>Created</span>{fmtDateTime(m.created_at)} · retrieved {m.retrieval_count}×</div>
      </div>

      {(d.supersedes_chain.length > 0 || d.superseded_by_chain.length > 0) && (
        <div className="chain">
          <div className="sub-title">Version chain</div>
          {[...d.superseded_by_chain].reverse().map((c) => (
            <div key={c.id} className="chain-item newer" onClick={() => onFocus(c.id)}>↑ newer: {c.content}</div>
          ))}
          <div className="chain-item current">● {m.content}</div>
          {d.supersedes_chain.map((c) => (
            <div key={c.id} className="chain-item older" onClick={() => onFocus(c.id)}>↓ replaced: {c.content} <Chip tone="muted">{c.status}</Chip></div>
          ))}
        </div>
      )}
      {d.conflicts_with && (
        <div className="chain">
          <div className="sub-title">Conflicts with</div>
          <div className="chain-item older" onClick={() => onFocus(d.conflicts_with.id)}>
            {d.conflicts_with.content} <Chip tone="info">trust {d.conflicts_with.trust}</Chip>
          </div>
        </div>
      )}

      {lastUse && (
        <div className="why">
          <div className="sub-title">Why it was {lastUse.selected ? "selected" : "considered"} in the last brief</div>
          <div className="muted small">{lastUse.reason} · {fmtDateTime(d.used_in_briefs[0].created_at)}</div>
          <Bar label="similarity" value={lastUse.similarity} weight={0.5} />
          <Bar label="recency" value={lastUse.recency} weight={0.2} />
          <Bar label="importance" value={lastUse.importance} weight={0.15} />
          <Bar label="trust" value={lastUse.trust} weight={0.15} />
          {lastUse.boost > 0 && <Bar label="open-loop boost" value={lastUse.boost} />}
          <div className="final">final score <b>{lastUse.final.toFixed(3)}</b> {lastUse.selected ? <Chip tone="good">shown</Chip> : <Chip tone="muted">not shown</Chip>}</div>
        </div>
      )}

      <div className="sub-title">Audit log <span className="muted small">(ids &amp; actions only)</span></div>
      <ul className="events">
        {d.events.map((e) => (
          <li key={e.id}><code>{e.action}</code> by {e.actor} · {fmtDateTime(e.ts)}</li>
        ))}
      </ul>

      <div className="row wrap">
        <button className="btn-ghost" onClick={() => { setDraft(m.content); setEditing(true); }}>Correct</button>
        <select value={m.visibility} onChange={(e) => patch({ visibility: e.target.value })}>
          <option value="patient">Show to patient</option>
          <option value="caregiver_only">Caregiver only</option>
          <option value="never_surface">Private — never surface</option>
        </select>
        {m.status === "pending_review" && <button className="btn" onClick={() => patch({ action: "approve" })}>Approve</button>}
        {m.status === "pending_review" && <button className="btn-ghost" onClick={() => patch({ action: "reject" })}>Reject</button>}
        <button className="btn-ghost danger" onClick={forget}>Forget</button>
      </div>
    </div>
  );
}
