import { useEffect, useState } from "react";
import { api } from "../../lib/api.js";
import { BriefCard, Chip, fmtDateTime } from "../../components/common.jsx";

export default function Briefs({ ctx }) {
  const { patient, contacts, tick, openMemory } = ctx;
  const [contact, setContact] = useState("");
  const [list, setList] = useState([]);
  const [sel, setSel] = useState(null);
  const [previewing, setPreviewing] = useState(false);

  useEffect(() => {
    const p = new URLSearchParams({ patient_id: patient.id });
    if (contact) p.set("contact_id", contact);
    api.get(`/briefs?${p}`).then((bs) => {
      setList(bs);
      setSel((s) => (s && bs.find((b) => b.id === s.id)) || bs[0] || null);
    });
  }, [patient, tick, contact]);

  const name = (id) => contacts.find((c) => c.id === id)?.name || "?";

  async function preview() {
    if (!contact) return;
    setPreviewing(true);
    try {
      const b = await api.post(`/contacts/${contact}/preview-brief`);
      setList((l) => [b, ...l]);
      setSel(b);
    } finally {
      setPreviewing(false);
    }
  }

  return (
    <div className="briefs">
      <div className="panel brief-list">
        <div className="row">
          <select value={contact} onChange={(e) => setContact(e.target.value)}>
            <option value="">All people</option>
            {contacts.filter((c) => c.status === "active").map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <button className="btn" disabled={!contact || previewing} onClick={preview}>
            {previewing ? "Building…" : "Preview brief now"}
          </button>
        </div>
        {list.map((b) => (
          <button key={b.id} className={`brief-row ${sel?.id === b.id ? "sel" : ""}`} onClick={() => setSel(b)}>
            <b>{name(b.contact_id)}</b> <span className="muted">{fmtDateTime(b.created_at)}</span>
            <span className="muted small">{b.call_id ? "call" : "preview"} · {b.mode}</span>
          </button>
        ))}
        {list.length === 0 && <div className="empty">No briefs yet.</div>}
      </div>

      {sel && (
        <div className="brief-detail">
          <div className="panel">
            <div className="panel-title">What {patient.name} saw</div>
            <div className="card-preview"><BriefCard card={sel.card} compact /></div>
            <div className="muted small">Query: “{sel.query}”</div>
          </div>
          <div className="panel">
            <div className="panel-title">
              Retrieval &amp; re-ranking
              <span className="panel-sub">
                final = {sel.weights.similarity}·sim + {sel.weights.recency}·recency + {sel.weights.importance}·importance + {sel.weights.trust}·trust (+{sel.weights.open_loop_boost} open loop)
              </span>
            </div>
            <table className="score-table">
              <thead>
                <tr><th></th><th>Memory</th><th>sim</th><th>rec</th><th>imp</th><th>trust</th><th>boost</th><th>final</th><th>Why retrieved</th></tr>
              </thead>
              <tbody>
                {sel.items.map((i) => (
                  <tr key={i.memory_id} className={i.selected ? "selected" : ""} onClick={() => openMemory(i.memory_id)}>
                    <td>{i.selected ? <Chip tone="good">shown</Chip> : ""}</td>
                    <td><Chip>{i.type}</Chip> {i.content}</td>
                    <td>{i.similarity.toFixed(2)}</td>
                    <td>{i.recency.toFixed(2)}</td>
                    <td>{i.importance.toFixed(2)}</td>
                    <td>{i.trust.toFixed(2)}</td>
                    <td>{i.boost ? `+${i.boost}` : ""}</td>
                    <td><b>{i.final.toFixed(3)}</b></td>
                    <td className="muted small">{i.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {sel.guardrail_ids?.length > 0 && (
              <div className="muted small">{sel.guardrail_ids.length} private/constraint memories were passed as guardrails and never shown.</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
