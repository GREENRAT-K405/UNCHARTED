import { useEffect, useState } from "react";
import { api } from "../../lib/api.js";
import { Chip, fmtDateTime, tone } from "../../components/common.jsx";

const STATUS = { blocked: "paused", unanswered: "no answer", declined: "declined", cancelled: "hung up before answer" };

function overlaySummary(ov) {
  if (!ov) return null;
  const asks = (ov.button || 0) + (ov.voice || 0);
  const parts = [];
  if (asks) parts.push(`Asked who this was ${asks} ${asks === 1 ? "time" : "times"} (${ov.voice || 0} by voice, ${ov.button || 0} with the button)`);
  if (ov.shown) parts.push(`card shown ${ov.shown}×`);
  if (ov.also_here_changes) parts.push(`"also here" changed ${ov.also_here_changes}×`);
  return parts.join(" · ");
}

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
            <b>{c.contact_name}</b> <Chip tone={c.status === "processed" ? "good" : "warn"}>{STATUS[c.status] || c.status}</Chip>
            {(c.overlay?.button || 0) + (c.overlay?.voice || 0) > 1 && <Chip tone="info">asked who {(c.overlay.button || 0) + (c.overlay.voice || 0)}×</Chip>}
            {c.emergency && <Chip tone="bad">help</Chip>}
            {c.guard && c.status !== "blocked" && <Chip tone="warn">called anyway</Chip>}
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
            {sel.guard && <p className="muted">{sel.guard.archived ? `${patient.name} asked to call an archived person; the call was gently refused.`
              : `Repeat-call guard fired: ${sel.guard.message} (${sel.guard.reason})`}</p>}
            {sel.status === "unanswered" && <p className="muted">Nobody answered within 30 seconds. {patient.name} saw “{sel.contact_name} is busy right now”.</p>}
            {sel.overlay && (
              <div className="overlay-log">
                <div className="sub-title">“Who is this?” card</div>
                <p>{overlaySummary(sel.overlay)}</p>
                {(sel.overlay.button || 0) + (sel.overlay.voice || 0) >= 2 && (
                  <p className="muted small">Asked more than once, so {sel.contact_name}'s relationship memory was reinforced and the next card adds more identity detail.</p>
                )}
                {sel.overlay.events?.length > 0 && (
                  <div className="muted small">
                    {sel.overlay.events.map((e, i) => `${new Date(e.ts).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", second: "2-digit" })} ${e.kind}`).join(" · ")}
                  </div>
                )}
              </div>
            )}
            {sel.recall_cards?.length > 0 && (
              <div className="overlay-log">
                <div className="sub-title">Recall cards shown during the call</div>
                <ul className="actions">
                  {sel.recall_cards.map((r, i) => (
                    <li key={i} className="clickable" onClick={() => openMemory(r.memory_id)}>
                      <Chip tone="accent">{Math.round(r.score * 100)}%</Chip> <span className="act-content">{r.content}</span>
                      <span className="act-reason">{new Date(r.ts).toLocaleTimeString("en-IN")} · matched what {sel.contact_name} was saying</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
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
                    : ["unanswered", "declined", "cancelled"].includes(sel.status) ? "Nobody talked on this call."
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
