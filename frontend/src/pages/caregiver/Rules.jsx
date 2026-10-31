import { useEffect, useState } from "react";
import { api } from "../../lib/api.js";

const RULE_KINDS = {
  quiet_hours: "No new meets between",
  max_calls_per_day: "At most N meets a day",
  min_gap_minutes: "Minimum minutes between meets",
};

/** Calling rules (the repeat-call guard) and memories the caretaker adds by hand. */
export default function Rules({ ctx }) {
  const { patient, contacts, tick, refresh, canEdit } = ctx;
  const [rules, setRules] = useState([]);
  const [memForm, setMemForm] = useState({ contact_id: "", type: "fact", content: "", event_date: "" });
  const [ruleForm, setRuleForm] = useState({ kind: "min_gap_minutes", start: "21:00", end: "07:00", value: 30, content: "" });
  const relatives = contacts.filter((c) => c.status === "active");

  useEffect(() => {
    api.get(`/memories?patient_id=${patient.id}&type=constraint&status=active`).then(setRules);
  }, [patient.id, tick]);

  async function addRule(e) {
    e.preventDefault();
    const f = ruleForm;
    const rule = f.kind === "quiet_hours" ? { kind: f.kind, start: f.start, end: f.end } : { kind: f.kind, value: Number(f.value) };
    const content = f.content || (f.kind === "quiet_hours" ? `No new meets between ${f.start} and ${f.end}`
      : f.kind === "max_calls_per_day" ? `At most ${f.value} meets a day` : `Wait ${f.value} minutes between meets`);
    await api.post("/memories", { patient_id: patient.id, contact_id: null, type: "constraint", content, rule });
    setRuleForm((r) => ({ ...r, content: "" }));
    refresh();
  }

  async function addMemory(e) {
    e.preventDefault();
    await api.post("/memories", { patient_id: patient.id, contact_id: memForm.contact_id || null, type: memForm.type,
      content: memForm.content, event_date: memForm.event_date || null });
    setMemForm((f) => ({ ...f, content: "", event_date: "" }));
    refresh();
  }

  return (
    <div className="grid-2">
      <form className="panel form" onSubmit={addRule}>
        <div className="panel-title">Calling rules <span className="panel-sub">apply when {patient.name} starts a new meet</span></div>
        <ul className="rule-list">
          {rules.length === 0 && <li className="empty">No rules. By default {patient.name} is gently reminded if a meet ended under 30 minutes ago.</li>}
          {rules.map((r) => (
            <li key={r.id}>
              <span>{r.content}</span>
              {canEdit && <button type="button" className="btn-link danger" onClick={async () => { await api.del(`/memories/${r.id}`); refresh(); }}>remove</button>}
            </li>
          ))}
        </ul>
        {canEdit && (
          <>
            <select value={ruleForm.kind} onChange={(e) => setRuleForm({ ...ruleForm, kind: e.target.value })}>
              {Object.entries(RULE_KINDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            {ruleForm.kind === "quiet_hours" ? (
              <div className="row">
                <input type="time" value={ruleForm.start} onChange={(e) => setRuleForm({ ...ruleForm, start: e.target.value })} />
                <span>to</span>
                <input type="time" value={ruleForm.end} onChange={(e) => setRuleForm({ ...ruleForm, end: e.target.value })} />
              </div>
            ) : (
              <input type="number" min={1} value={ruleForm.value} onChange={(e) => setRuleForm({ ...ruleForm, value: e.target.value })} />
            )}
            <input placeholder="Why (optional, shown to you only)" value={ruleForm.content} onChange={(e) => setRuleForm({ ...ruleForm, content: e.target.value })} />
            <button className="btn">Add rule</button>
          </>
        )}
      </form>

      {canEdit && (
        <form className="panel form" onSubmit={addMemory}>
          <div className="panel-title">Add a memory</div>
          <select value={memForm.contact_id} onChange={(e) => setMemForm({ ...memForm, contact_id: e.target.value })}>
            <option value="">Everyone (general)</option>
            {relatives.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <select value={memForm.type} onChange={(e) => setMemForm({ ...memForm, type: e.target.value })}>
            <option value="fact">Fact</option>
            <option value="event">Event (fades after the date)</option>
            <option value="plan">Plan (fades after the date)</option>
            <option value="preference">Something they enjoy</option>
            <option value="sensitive">Private: never show {patient.name}</option>
          </select>
          <textarea required rows={3} placeholder="e.g. Riya's birthday party is on Saturday" value={memForm.content}
            onChange={(e) => setMemForm({ ...memForm, content: e.target.value })} />
          {["event", "plan"].includes(memForm.type) && (
            <input type="date" value={memForm.event_date} onChange={(e) => setMemForm({ ...memForm, event_date: e.target.value })} />
          )}
          <button className="btn">Save memory</button>
        </form>
      )}
    </div>
  );
}
