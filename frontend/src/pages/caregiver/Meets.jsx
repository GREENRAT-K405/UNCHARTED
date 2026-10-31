import { useEffect, useState } from "react";
import { api } from "../../lib/api.js";
import { Avatar, Chip, fmtDateTime } from "../../components/common.jsx";

function localInputValue(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const blank = () => {
  const d = new Date(Date.now() + 3600000);
  d.setMinutes(0, 0, 0);
  return { title: "", when: localInputValue(d), duration_min: 30, contact_ids: [], everyone: false, agenda: "" };
};

export default function Meets({ ctx }) {
  const { patient, contacts, tick, refresh, canEdit, openCall } = ctx;
  const [form, setForm] = useState(blank);
  const [editId, setEditId] = useState(null);
  const [upcoming, setUpcoming] = useState([]);
  const [past, setPast] = useState([]);
  const [error, setError] = useState(null);
  const relatives = contacts.filter((c) => c.status === "active");

  useEffect(() => {
    api.get(`/meets?patient_id=${patient.id}&scope=upcoming`).then(setUpcoming);
    api.get(`/meets?patient_id=${patient.id}&scope=past`).then(setPast);
  }, [patient.id, tick]);

  function toggle(id) {
    setForm((f) => ({ ...f, everyone: false, contact_ids: f.contact_ids.includes(id) ? f.contact_ids.filter((x) => x !== id) : [...f.contact_ids, id] }));
  }

  async function save(e) {
    e.preventDefault();
    setError(null);
    const body = {
      title: form.title, scheduled_at: new Date(form.when).toISOString(), duration_min: Number(form.duration_min),
      contact_ids: form.everyone ? [] : form.contact_ids, agenda: form.agenda,
    };
    if (!form.everyone && body.contact_ids.length === 0) {
      setError("Choose who the meet is with, or pick everyone.");
      return;
    }
    try {
      if (editId) await api.patch(`/meets/${editId}`, body);
      else await api.post("/meets", { patient_id: patient.id, ...body });
      setForm(blank());
      setEditId(null);
      refresh();
    } catch (err) {
      setError(err.message);
    }
  }

  function edit(m) {
    setEditId(m.id);
    setForm({ title: m.title, when: localInputValue(new Date(m.scheduled_at)), duration_min: m.duration_min,
      contact_ids: m.everyone ? [] : m.people.map((p) => p.id), everyone: m.everyone, agenda: m.agenda || "" });
  }

  async function cancel(m) {
    if (!confirm(`Cancel "${m.title}"?`)) return;
    await api.patch(`/meets/${m.id}`, { status: "cancelled" });
    refresh();
  }

  return (
    <div className="meets">
      <div className="grid-2">
        {canEdit && (
          <form className="panel form" onSubmit={save}>
            <div className="panel-title">{editId ? "Edit meet" : "Schedule a meet"}</div>
            <label>Title<input required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="e.g. Sunday chat with Riya" /></label>
            <div className="row">
              <label className="grow">When<input required type="datetime-local" value={form.when} onChange={(e) => setForm({ ...form, when: e.target.value })} /></label>
              <label>Minutes<input type="number" min={5} max={240} value={form.duration_min} onChange={(e) => setForm({ ...form, duration_min: e.target.value })} /></label>
            </div>
            <div className="sub-title">With</div>
            <div className="who-pick">
              <label className={`who-chip ${form.everyone ? "on" : ""}`}>
                <input type="checkbox" checked={form.everyone} onChange={(e) => setForm({ ...form, everyone: e.target.checked, contact_ids: [] })} /> Everyone
              </label>
              {relatives.map((c) => (
                <label key={c.id} className={`who-chip ${form.contact_ids.includes(c.id) ? "on" : ""}`}>
                  <input type="checkbox" checked={form.contact_ids.includes(c.id)} onChange={() => toggle(c.id)} />
                  <Avatar contact={c} size={24} /> {c.name}
                </label>
              ))}
            </div>
            <label>Agenda for {patient.name} (optional)<textarea rows={2} value={form.agenda} onChange={(e) => setForm({ ...form, agenda: e.target.value })} placeholder="e.g. Ask about the cricket final" /></label>
            {error && <div className="auth-error">{error}</div>}
            <div className="row">
              <button className="btn">{editId ? "Save changes" : "Schedule"}</button>
              {editId && <button type="button" className="btn-ghost" onClick={() => { setEditId(null); setForm(blank()); }}>Cancel edit</button>}
            </div>
            <p className="muted small">{patient.name}'s Join button turns on 30 minutes before the start. Invited relatives see it on their dashboard.</p>
          </form>
        )}

        <div className="panel">
          <div className="panel-title">Upcoming <span className="panel-sub">{upcoming.length} scheduled</span></div>
          {upcoming.length === 0 && <div className="empty">Nothing scheduled.</div>}
          <ul className="meet-list">
            {upcoming.map((m) => (
              <li key={m.id}>
                <div className="ml-when">{fmtDateTime(m.scheduled_at)}</div>
                <div className="ml-body">
                  <b>{m.title}</b>
                  <span className="muted small">{m.everyone ? "Everyone" : m.people.map((p) => p.name).join(", ")} · {m.duration_min} min</span>
                  {m.agenda && <span className="small">{m.agenda}</span>}
                </div>
                {canEdit && (
                  <div className="row">
                    <button className="btn-link" onClick={() => edit(m)}>Edit</button>
                    <button className="btn-link danger" onClick={() => cancel(m)}>Cancel</button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="panel">
        <div className="panel-title">Past meets <span className="panel-sub">click one to read the transcript and add a note</span></div>
        {past.length === 0 && <div className="empty">No past meets yet.</div>}
        <table className="past-table">
          <tbody>
            {past.map((m) => (
              <tr key={m.id} className={m.call ? "clickable" : ""} onClick={() => m.call && openCall(m.call.id)}>
                <td className="nowrap">{fmtDateTime(m.scheduled_at)}</td>
                <td><b>{m.title}</b><div className="muted small">{m.everyone ? "Everyone" : m.people.map((p) => p.name).join(", ")}</div></td>
                <td>
                  {m.status === "cancelled" ? <Chip tone="muted">cancelled</Chip> : !m.call ? <Chip tone="warn">missed</Chip> : (
                    <>
                      {m.call.important && <Chip tone="warn">★ important</Chip>}
                      <div className="small">{m.call.summary}</div>
                      {m.call.caretaker_note && <div className="small note-line">Note: {m.call.caretaker_note}</div>}
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
