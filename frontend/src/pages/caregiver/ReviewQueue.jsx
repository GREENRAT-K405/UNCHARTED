import { useEffect, useState } from "react";
import { api } from "../../lib/api.js";
import { Chip, relTime } from "../../components/common.jsx";

export default function ReviewQueue({ ctx }) {
  const { patient, contacts, tick, refresh, openMemory } = ctx;
  const [items, setItems] = useState([]);
  const [conflicts, setConflicts] = useState({});
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    api.get(`/memories?patient_id=${patient.id}&status=pending_review`).then(async (ms) => {
      setItems(ms);
      const entries = await Promise.all(
        ms.filter((m) => m.conflicts_with).map(async (m) => [m.id, (await api.get(`/memories/${m.conflicts_with}`)).memory])
      );
      setConflicts(Object.fromEntries(entries));
    });
  }, [patient, tick]);

  const name = (id) => contacts.find((c) => c.id === id)?.name || "Everyone";

  async function act(m, body) {
    await api.patch(`/memories/${m.id}`, body);
    setEditing(null);
    refresh();
  }

  if (items.length === 0) return <div className="panel empty-state">Nothing to review. New low-confidence or conflicting memories appear here as soon as a call ends.</div>;

  return (
    <div className="review">
      {items.map((m) => {
        const target = conflicts[m.id];
        return (
          <div key={m.id} className="panel review-item">
            <div className="ri-head">
              <Chip tone="warn">{target ? "Conflict" : "Needs review"}</Chip>
              <Chip>{m.type}</Chip>
              <span className="muted">about {name(m.contact_id)} · {relTime(m.created_at, ctx.offsetMs)}</span>
            </div>
            <div className={target ? "conflict-grid" : ""}>
              <div className="claim">
                <div className="claim-label">
                  New — said by {m.source?.speaker === "patient" ? patient.name : name(m.contact_id)} (trust {m.trust})
                </div>
                {editing === m.id ? (
                  <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={2} />
                ) : (
                  <div className="claim-text">{m.content}</div>
                )}
                {m.source?.quote && <div className="quote">“{m.source.quote}”</div>}
              </div>
              {target && (
                <div className="claim existing">
                  <div className="claim-label">Existing — {target.source?.kind === "caregiver" ? "entered by caregiver" : target.source?.origin} (trust {target.trust})</div>
                  <div className="claim-text">{target.content}</div>
                  <button className="btn-link" onClick={() => openMemory(target.id)}>Inspect</button>
                </div>
              )}
            </div>
            {target && (
              <p className="muted small">
                The agent did not overwrite the existing memory because the new statement comes from a less trusted source.
              </p>
            )}
            <div className="row">
              {editing === m.id ? (
                <>
                  <button className="btn" onClick={() => act(m, { content: draft, action: "approve" })}>Save &amp; approve</button>
                  <button className="btn-ghost" onClick={() => setEditing(null)}>Cancel</button>
                </>
              ) : (
                <>
                  <button className="btn" onClick={() => act(m, { action: "reject" })}>
                    {target ? "Keep existing (reject)" : "Reject"}
                  </button>
                  <button className="btn-ghost" onClick={() => act(m, { action: "approve" })}>
                    {target ? "New one is right (replace)" : "Approve"}
                  </button>
                  <button className="btn-ghost" onClick={() => { setEditing(m.id); setDraft(m.content); }}>Edit</button>
                  <button className="btn-ghost" onClick={() => act(m, { action: "approve", visibility: "never_surface" })}>
                    Approve as private
                  </button>
                </>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
