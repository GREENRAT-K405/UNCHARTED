import { useEffect, useState } from "react";
import { api } from "../../lib/api.js";
import { Chip, fmtDateTime, tone } from "../../components/common.jsx";

export default function Calls({ ctx }) {
  const { patient, tick, openMemory } = ctx;
  const [calls, setCalls] = useState([]);
  const [sel, setSel] = useState(null);
  const [transcript, setTranscript] = useState([]);

  useEffect(() => {
    api.get(`/calls?patient_id=${patient.id}`).then((cs) => {
      setCalls(cs);
      setSel((s) => (s && cs.find((c) => c.id === s.id)) || cs[0] || null);
    });
  }, [patient, tick]);

  useEffect(() => {
    if (sel) api.get(`/calls/${sel.id}/transcript`).then(setTranscript);
  }, [sel?.id, tick]);

  async function reprocess() {
    await api.post(`/calls/${sel.id}/reprocess`);
    ctx.refresh();
  }

  return (
    <div className="calls">
      <div className="panel call-list">
        {calls.length === 0 && <div className="empty">No calls yet.</div>}
        {calls.map((c) => (
          <button key={c.id} className={`brief-row ${sel?.id === c.id ? "sel" : ""}`} onClick={() => setSel(c)}>
            <b>{c.contact_name}</b> <Chip tone={c.status === "processed" ? "good" : "warn"}>{c.status}</Chip>
            {c.emergency && <Chip tone="bad">help</Chip>}
            {c.guard && <Chip tone="warn">called anyway</Chip>}
            <span className="muted small">{fmtDateTime(c.started_at)}{c.duration_sec != null && ` · ${Math.round(c.duration_sec / 60)} min`}</span>
          </button>
        ))}
      </div>
      {sel && (
        <div className="call-detail">
          <div className="panel">
            <div className="panel-title">Call with {sel.contact_name} <span className="panel-sub">{fmtDateTime(sel.started_at)}</span></div>
            {sel.summary && <p className="summary">{sel.summary}</p>}
            {sel.recap && <p className="muted">Read to {patient.name} after the call: “{sel.recap}”</p>}
            {sel.guard && <p className="muted">Repeat-call guard fired: {sel.guard.message} ({sel.guard.reason})</p>}
            {sel.agent?.actions && (
              <ul className="actions">
                {sel.agent.actions.map((a, i) => (
                  <li key={i} className={a.memory_id ? "clickable" : ""} onClick={() => a.memory_id && openMemory(a.memory_id)}>
                    <Chip tone={tone(a.action)}>{a.action}</Chip> <span className="act-content">{a.content}</span>
                    <span className="act-reason">{a.reason}</span>
                  </li>
                ))}
              </ul>
            )}
            {sel.status === "processed" && <button className="btn-ghost" onClick={reprocess}>Run memory agent again</button>}
          </div>
          <div className="panel">
            <div className="panel-title">Transcript <span className="panel-sub">auto-deleted after 30 days (TTL)</span></div>
            <div className="transcript">
              {transcript.length === 0 && <div className="empty">Nothing was transcribed.</div>}
              {transcript.map((t) => (
                <div key={t.id} className={`tline ${t.speaker}`}>
                  <span className="tspk">{t.speaker === "patient" ? patient.name : sel.contact_name}</span> {t.text}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
