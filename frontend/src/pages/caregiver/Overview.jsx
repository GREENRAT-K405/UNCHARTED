import { useEffect, useState } from "react";
import { api } from "../../lib/api.js";
import { Chip, relTime, tone } from "../../components/common.jsx";

export default function Overview({ ctx, feed, liveTranscript }) {
  const { patient, contacts, tick, openMemory } = ctx;
  const [calls, setCalls] = useState([]);
  const [ttlContact, setTtlContact] = useState("");

  useEffect(() => {
    api.get(`/calls?patient_id=${patient.id}&limit=8`).then(setCalls);
  }, [patient, tick]);

  const live = calls.filter((c) => ["ringing", "active"].includes(c.status));
  const lastProcessed = calls.find((c) => c.status === "processed" && c.agent?.actions);
  const name = (id) => contacts.find((c) => c.id === id)?.name || "—";

  async function seedExpiring() {
    await api.post("/demo/expiring", { patient_id: patient.id, contact_id: ttlContact || null, seconds: 90 });
  }

  return (
    <div className="grid-2">
      <div className="col">
        <div className="panel">
          <div className="panel-title">Calls in progress</div>
          {live.length === 0 && <div className="empty">No active calls. Start one from the patient screen.</div>}
          {live.map((c) => (
            <div key={c.id} className="live-call">
              <div>
                <b>{patient.name} ↔ {c.contact_name}</b> <Chip tone={c.status === "active" ? "good" : "warn"}>{c.status}</Chip>
                {c.off_record && <Chip tone="bad">off the record</Chip>}
              </div>
              <div className="join-line">
                Join link: <a href={`/join/${c.id}`} target="_blank" rel="noreferrer">{location.origin}/join/{c.id}</a>
              </div>
            </div>
          ))}
          {liveTranscript.length > 0 && (
            <div className="transcript live">
              {liveTranscript.slice(-10).map((t, i) => (
                <div key={i} className={`tline ${t.speaker}`}>
                  <span className="tspk">{t.speaker === "patient" ? patient.name : "Contact"}</span> {t.text}
                </div>
              ))}
            </div>
          )}
        </div>

        {lastProcessed && (
          <div className="panel">
            <div className="panel-title">
              Memory agent: last call with {lastProcessed.contact_name}
              <span className="panel-sub">{relTime(lastProcessed.processed_at || lastProcessed.started_at, ctx.offsetMs)} · {lastProcessed.agent.mode}</span>
            </div>
            {lastProcessed.summary && <p className="summary">{lastProcessed.summary}</p>}
            <ul className="actions">
              {lastProcessed.agent.actions.map((a, i) => (
                <li key={i} onClick={() => a.memory_id && openMemory(a.memory_id)} className={a.memory_id ? "clickable" : ""}>
                  <Chip tone={tone(a.action)}>{a.action}</Chip>
                  <span className="act-content">{a.content}</span>
                  {a.target_content && <span className="act-target">↳ {a.action === "CONFLICT" ? "conflicts with" : "was"}: “{a.target_content}”</span>}
                  <span className="act-reason">{a.reason}</span>
                </li>
              ))}
              {lastProcessed.agent.actions.length === 0 && <li className="empty">Nothing worth remembering.</li>}
            </ul>
          </div>
        )}

        <div className="panel">
          <div className="panel-title">Demo: TTL expiry</div>
          <p className="muted">
            Adds a plan that expires 90 seconds from now. MongoDB's TTL monitor runs about once a minute, so it disappears
            within ~1–2 minutes and shows up below as a delete event.
          </p>
          <div className="row">
            <select value={ttlContact} onChange={(e) => setTtlContact(e.target.value)}>
              <option value="">Rahul (default)</option>
              {contacts.filter((c) => c.status === "active").map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <button className="btn" onClick={seedExpiring}>Add expiring memory</button>
          </div>
        </div>
      </div>

      <div className="panel feed">
        <div className="panel-title">Live memory feed <span className="panel-sub">MongoDB change streams</span></div>
        {feed.length === 0 && <div className="empty">Waiting for changes…</div>}
        {feed.map((e, i) => (
          <div key={i} className={`feed-item ${e.expired ? "expired" : ""}`}>
            {e.type === "memory" && (
              <>
                <Chip tone={e.expired ? "warn" : tone(e.op)}>{e.expired ? "TTL expired" : e.op}</Chip>
                <span className="feed-text clickable" onClick={() => e.op !== "delete" && openMemory(e.id)}>
                  {e.content}
                </span>
                {e.memory && (
                  <span className="feed-meta">
                    {e.memory.type} · {e.memory.status}
                    {e.fields?.length > 0 && ` · changed ${e.fields.filter((f) => f !== "updated_at").join(", ")}`}
                    {e.memory.contact_id && ` · ${name(e.memory.contact_id)}`}
                  </span>
                )}
              </>
            )}
            {e.type === "call" && (
              <>
                <Chip tone="info">call</Chip>
                <span className="feed-text">Call with {name(e.call.contact_id)} is {e.call.status}</span>
              </>
            )}
            {e.type === "contact_request" && (
              <>
                <Chip tone="warn">request</Chip>
                <span className="feed-text">{patient.name} asked to add “{e.contact.name}”. Review in Contacts.</span>
              </>
            )}
            <span className="feed-time">{new Date(e.at).toLocaleTimeString("en-IN")}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
