import { useEffect, useState } from "react";
import { api } from "../../lib/api.js";
import { Chip, fmtDateTime, tone } from "../../components/common.jsx";

export default function Calls({ ctx }) {
  const { patient, tick, openMemory } = ctx;
  const [calls, setCalls] = useState([]);
  const [sel, setSel] = useState(null);
  const [transcript, setTranscript] = useState([]);

  useEffect(() => {
    api.get(`/calls?patient_id=${patient.id}&limit=150`).then((cs) => {
      setCalls(cs);
      setSel((s) => (s && cs.find((c) => c.id === s.id)) || cs[0] || null);
    });
  }, [patient, tick]);

  useEffect(() => {
    if (sel) api.get(`/calls/${sel.id}/transcript`).then(setTranscript);
  }, [sel?.id, tick]);

  useEffect(() => {
    if (ctx.focusCall) {
      const c = calls.find((x) => x.id === ctx.focusCall);
      if (c) setSel(c);
    }
  }, [ctx.focusCall, calls]);

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
            <b>{c.contact_name}</b> <Chip tone={c.status === "processed" ? "good" : "warn"}>{c.status === "blocked" ? "paused" : c.status}</Chip>
            {c.emergency && <Chip tone="bad">help</Chip>}
            {c.guard && c.status !== "blocked" && <Chip tone="warn">called anyway</Chip>}
            <span className="muted small">{fmtDateTime(c.started_at)}{c.duration_sec != null && ` · ${Math.round(c.duration_sec / 60)} min`}</span>
          </button>
        ))}
      </div>
      {sel && (
        <div className="call-detail">
          <div className="panel">
            <div className="panel-title">{sel.title || "Meet"} with {sel.contact_name} <span className="panel-sub">{fmtDateTime(sel.started_at)}</span></div>
            {sel.status !== "blocked" && <NoteEditor key={sel.id} call={sel} canEdit={ctx.canEdit} onSaved={ctx.refresh} />}
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
              {transcript.length === 0 && (
                <div className="empty">
                  {sel.status === "blocked" ? `The repeat-call guard paused this call: ${sel.guard?.reason || ""}`
                    : (Date.now() - new Date(sel.started_at).getTime()) > 30 * 86400000
                      ? "Raw transcripts are deleted after 30 days by a MongoDB TTL index. The memories learned from this call remain."
                      : sel.off_record ? "This call was off the record, so nothing was saved." : "Nothing was transcribed."}
                </div>
              )}
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

/** The caretaker's note on a meet, and a star for important discussions. */
function NoteEditor({ call, canEdit, onSaved }) {
  const [note, setNote] = useState(call.caretaker_note || "");
  const [important, setImportant] = useState(!!call.important);
  const [saved, setSaved] = useState(false);
  async function save(next = {}) {
    await api.patch(`/calls/${call.id}/note`, { note, important, ...next });
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
    onSaved?.();
  }
  return (
    <div className="note-editor">
      <div className="row space">
        <span className="sub-title">Your note</span>
        <button className={`btn-sm ${important ? "star-on" : ""}`} disabled={!canEdit}
          onClick={() => { setImportant(!important); save({ important: !important }); }}>
          {important ? "★ Important" : "☆ Mark important"}
        </button>
      </div>
      <textarea rows={2} value={note} disabled={!canEdit} onChange={(e) => setNote(e.target.value)}
        placeholder="What mattered in this meet? This note is shown to the patient next time they meet this person." />
      {canEdit && <div className="row"><button className="btn-sm" onClick={() => save()}>Save note</button>{saved && <span className="muted small">Saved</span>}</div>}
    </div>
  );
}
