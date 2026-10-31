import { useEffect, useState } from "react";
import { api } from "../../lib/api.js";
import { Avatar, Chip } from "../../components/common.jsx";

const RULE_KINDS = {
  quiet_hours: "No calls between",
  max_calls_per_day: "At most N calls a day",
  min_gap_minutes: "Minimum minutes between calls",
};

function resizeToDataUrl(file, size = 256) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const c = document.createElement("canvas");
      const s = Math.min(img.width, img.height);
      c.width = c.height = size;
      c.getContext("2d").drawImage(img, (img.width - s) / 2, (img.height - s) / 2, s, s, 0, 0, size, size);
      resolve(c.toDataURL("image/jpeg", 0.82));
    };
    img.src = URL.createObjectURL(file);
  });
}

export default function Contacts({ ctx }) {
  const { patient, contacts, tick, refresh } = ctx;
  const [rules, setRules] = useState([]);
  const [form, setForm] = useState({ name: "", relation: "", relation_detail: "", nicknames: "", phone: "", notes: "", photo: null });
  const [memForm, setMemForm] = useState({ contact_id: "", type: "fact", content: "", event_date: "" });
  const [ruleForm, setRuleForm] = useState({ contact_id: "", kind: "quiet_hours", start: "21:00", end: "07:00", value: 3, content: "" });
  const [approve, setApprove] = useState({});

  useEffect(() => {
    api.get(`/memories?patient_id=${patient.id}&type=constraint&status=active`).then(setRules);
  }, [patient, tick]);

  const active = contacts.filter((c) => c.status === "active");
  const pending = contacts.filter((c) => c.status === "pending");
  const name = (id) => (id ? contacts.find((c) => c.id === id)?.name : "Everyone");

  async function addContact(e) {
    e.preventDefault();
    await api.post("/contacts", {
      patient_id: patient.id, name: form.name, relation: form.relation, relation_detail: form.relation_detail || null,
      nicknames: form.nicknames.split(",").map((s) => s.trim()).filter(Boolean), phone: form.phone || null,
      photo: form.photo, notes: form.notes.split("\n"),
    });
    setForm({ name: "", relation: "", relation_detail: "", nicknames: "", phone: "", notes: "", photo: null });
    refresh();
  }

  async function addMemory(e) {
    e.preventDefault();
    await api.post("/memories", {
      patient_id: patient.id, contact_id: memForm.contact_id || null, type: memForm.type, content: memForm.content,
      event_date: memForm.event_date || null,
    });
    setMemForm((f) => ({ ...f, content: "", event_date: "" }));
    refresh();
  }

  async function addRule(e) {
    e.preventDefault();
    const f = ruleForm;
    const rule = f.kind === "quiet_hours" ? { kind: f.kind, start: f.start, end: f.end } : { kind: f.kind, value: Number(f.value) };
    const who = name(f.contact_id) || "anyone";
    const content = f.content || (f.kind === "quiet_hours" ? `No calls to ${who} between ${f.start} and ${f.end}`
      : f.kind === "max_calls_per_day" ? `At most ${f.value} calls a day to ${who}` : `Wait ${f.value} minutes between calls to ${who}`);
    await api.post("/memories", { patient_id: patient.id, contact_id: f.contact_id || null, type: "constraint", content, rule });
    setRuleForm((r) => ({ ...r, content: "" }));
    refresh();
  }

  async function removeContact(c) {
    if (!confirm(`Remove ${c.name}? Every memory about ${c.name} will be forgotten.`)) return;
    await api.del(`/contacts/${c.id}`);
    refresh();
  }

  async function approveContact(c) {
    if (!approve[c.id]) return;
    await api.patch(`/contacts/${c.id}`, { status: "active", relation: approve[c.id] });
    refresh();
  }

  async function setPhoto(c, file) {
    await api.patch(`/contacts/${c.id}`, { photo: await resizeToDataUrl(file) });
    refresh();
  }

  return (
    <div className="contacts-page">
      {pending.length > 0 && (
        <div className="panel">
          <div className="panel-title">Waiting for your approval <span className="panel-sub">{patient.name} can't call unknown people until you approve them</span></div>
          {pending.map((c) => (
            <div key={c.id} className="row">
              <b>{c.name}</b>
              <input placeholder="Relation, e.g. neighbour" value={approve[c.id] || ""}
                onChange={(e) => setApprove((a) => ({ ...a, [c.id]: e.target.value }))} />
              <button className="btn" onClick={() => approveContact(c)}>Approve</button>
              <button className="btn-ghost danger" onClick={() => removeContact(c)}>Decline</button>
            </div>
          ))}
        </div>
      )}

      <div className="contact-cards">
        {active.map((c) => (
          <div key={c.id} className="panel contact-card">
            <label className="photo-pick" title="Change photo">
              <Avatar contact={c} size={72} />
              <input type="file" accept="image/*" hidden onChange={(e) => e.target.files[0] && setPhoto(c, e.target.files[0])} />
            </label>
            <div className="cc-body">
              <div className="cc-name">{c.name} {c.primary_caregiver && <Chip tone="info">primary</Chip>}</div>
              <div className="muted">{c.relation}{c.relation_detail && ` · ${c.relation_detail}`}</div>
              {c.nicknames?.length > 0 && <div className="muted small">Also: {c.nicknames.join(", ")}</div>}
              <div className="cc-rules">
                {rules.filter((r) => r.contact_id === c.id).map((r) => <Chip key={r.id} tone="warn" title={JSON.stringify(r.rule)}>{r.content}</Chip>)}
              </div>
            </div>
            <button className="btn-ghost danger small" onClick={() => removeContact(c)}>Remove</button>
          </div>
        ))}
      </div>

      <div className="grid-3">
        <form className="panel form" onSubmit={addContact}>
          <div className="panel-title">Add a person</div>
          <input required placeholder="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input required placeholder="Relation to the patient (e.g. niece)" value={form.relation} onChange={(e) => setForm({ ...form, relation: e.target.value })} />
          <input placeholder="Detail (e.g. Meera's daughter)" value={form.relation_detail} onChange={(e) => setForm({ ...form, relation_detail: e.target.value })} />
          <input placeholder="Nicknames, comma separated" value={form.nicknames} onChange={(e) => setForm({ ...form, nicknames: e.target.value })} />
          <input placeholder="Phone" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
          <textarea rows={3} placeholder={"Things to remember, one per line\n(e.g. Has a cat named Mau)"} value={form.notes}
            onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          <label className="file">Photo <input type="file" accept="image/*" onChange={async (e) => e.target.files[0] && setForm({ ...form, photo: await resizeToDataUrl(e.target.files[0]) })} /></label>
          <button className="btn">Add person</button>
          <p className="muted small">Your notes are stored as caregiver memories with the highest trust.</p>
        </form>

        <form className="panel form" onSubmit={addRule}>
          <div className="panel-title">Calling rules</div>
          <select value={ruleForm.contact_id} onChange={(e) => setRuleForm({ ...ruleForm, contact_id: e.target.value })}>
            <option value="">Everyone</option>
            {active.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
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
          <ul className="rule-list">
            {rules.map((r) => (
              <li key={r.id}>
                <span>{r.content} <span className="muted small">({name(r.contact_id)})</span></span>
                <button type="button" className="btn-link danger" onClick={async () => { await api.del(`/memories/${r.id}`); refresh(); }}>remove</button>
              </li>
            ))}
          </ul>
        </form>

        <form className="panel form" onSubmit={addMemory}>
          <div className="panel-title">Add a memory</div>
          <select value={memForm.contact_id} onChange={(e) => setMemForm({ ...memForm, contact_id: e.target.value })}>
            <option value="">Everyone (global)</option>
            {active.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <select value={memForm.type} onChange={(e) => setMemForm({ ...memForm, type: e.target.value })}>
            <option value="fact">Fact</option>
            <option value="event">Event (expires)</option>
            <option value="plan">Plan (expires)</option>
            <option value="preference">Preference</option>
            <option value="sensitive">Sensitive — never show {patient.name}</option>
          </select>
          <textarea required rows={3} placeholder="e.g. Riya's birthday party is on Saturday" value={memForm.content}
            onChange={(e) => setMemForm({ ...memForm, content: e.target.value })} />
          {["event", "plan"].includes(memForm.type) && (
            <input type="date" value={memForm.event_date} onChange={(e) => setMemForm({ ...memForm, event_date: e.target.value })} />
          )}
          <button className="btn">Save memory</button>
        </form>
      </div>
    </div>
  );
}
