import { useEffect, useState } from "react";
import { api } from "../../lib/api.js";
import { useCaregiverAuth } from "../../lib/auth.jsx";
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
  const { refreshMe } = useCaregiverAuth();
  const [rules, setRules] = useState([]);
  const [form, setForm] = useState({ name: "", known_as: "", relation: "", relation_detail: "", nicknames: "", phone: "", notes: "", photo: null });
  const [memForm, setMemForm] = useState({ contact_id: "", type: "fact", content: "", event_date: "" });
  const [ruleForm, setRuleForm] = useState({ contact_id: "", kind: "quiet_hours", start: "21:00", end: "07:00", value: 3, content: "" });
  const [approve, setApprove] = useState({});
  const [archiving, setArchiving] = useState(null); // {contact, note}
  const [copied, setCopied] = useState(null);

  useEffect(() => {
    api.get(`/memories?patient_id=${patient.id}&type=constraint&status=active`).then(setRules);
  }, [patient, tick]);

  const active = contacts.filter((c) => c.status === "active");
  const pending = contacts.filter((c) => c.status === "pending");
  const archived = contacts.filter((c) => c.status === "archived");
  const name = (id) => (id ? contacts.find((c) => c.id === id)?.name : "Everyone");

  async function addContact(e) {
    e.preventDefault();
    await api.post("/contacts", {
      patient_id: patient.id, name: form.name, known_as: form.known_as || null, relation: form.relation,
      relation_detail: form.relation_detail || null,
      nicknames: form.nicknames.split(",").map((s) => s.trim()).filter(Boolean), phone: form.phone || null,
      photo: form.photo, notes: form.notes.split("\n"),
    });
    setForm({ name: "", known_as: "", relation: "", relation_detail: "", nicknames: "", phone: "", notes: "", photo: null });
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
    if (!confirm(`Delete ${c.name} permanently? Every memory about ${c.name} will be forgotten. This cannot be undone.`)) return;
    await api.del(`/contacts/${c.id}`);
    setArchiving(null);
    refresh();
  }

  async function archive(e) {
    e.preventDefault();
    await api.post(`/contacts/${archiving.contact.id}/archive`, { note: archiving.note });
    setArchiving(null);
    refresh();
  }

  async function unarchive(c) {
    await api.post(`/contacts/${c.id}/unarchive`);
    refresh();
  }

  async function saveNote(c, note) {
    await api.patch(`/contacts/${c.id}/archive-note`, { note });
    refresh();
  }

  async function copyLink(c) {
    await navigator.clipboard?.writeText(c.link_url);
    setCopied(c.id);
    setTimeout(() => setCopied(null), 2000);
  }

  async function newLink(c) {
    if (!confirm(`Make a new link for ${c.name}? The old link stops working at once.`)) return;
    await api.post(`/contacts/${c.id}/link`);
    refresh();
  }

  async function linkMyself(c) {
    try {
      await api.post("/relative/link", { token: c.link_url });
      await refreshMe();
      refresh();
      alert(`Done: you're now ${c.name} on your own account. Open /family to be rung when ${patient.name} calls.`);
    } catch (e) {
      alert(e.message);
    }
  }

  async function unlinkAccount(c) {
    if (!confirm(`Detach ${c.account.email || "their account"} from ${c.name}? They can add the link to an account again.`)) return;
    await api.del(`/contacts/${c.id}/account`);
    refresh();
  }

  async function setKnownAs(c) {
    const v = prompt(`What does ${patient.name} call ${c.name}? (shown on the tile and the call card)`, c.known_as || c.name);
    if (v === null) return;
    await api.patch(`/contacts/${c.id}`, { known_as: v.trim() === c.name ? "" : v.trim() });
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
              <div className="muted small">
                {patient.name} calls them <b>{c.known_as || c.name}</b>{" "}
                {ctx.canEdit && <button className="btn-link" onClick={() => setKnownAs(c)}>change</button>}
              </div>
              {c.nicknames?.length > 0 && <div className="muted small">Voice also matches: {c.nicknames.join(", ")}</div>}
              <div className="cc-rules">
                {rules.filter((r) => r.contact_id === c.id).map((r) => <Chip key={r.id} tone="warn" title={JSON.stringify(r.rule)}>{r.content}</Chip>)}
              </div>
              <div className="muted small">
                {c.account
                  ? <>Has their own account: <b>{c.account.email || c.account.name}</b>{" "}
                      {ctx.canEdit && <button className="btn-link" onClick={() => unlinkAccount(c)}>unlink</button>}</>
                  : <>No account yet (they use the link){" "}
                      <button className="btn-link" onClick={() => linkMyself(c)}>this is me</button></>}
              </div>
              {c.link_url && (
                <div className="cc-link">
                  <span className="muted small">Their link (rings when {patient.name} calls):</span>
                  <div className="row">
                    <button className="btn-sm" onClick={() => copyLink(c)}>{copied === c.id ? "Copied" : "Copy link"}</button>
                    {ctx.canEdit && <button className="btn-sm" onClick={() => newLink(c)}>New link</button>}
                    <a className="btn-link small" href={c.link_url} target="_blank" rel="noreferrer">open</a>
                    {ctx.canEdit && <button className="btn-sm cc-remove" onClick={() => setArchiving({ contact: c, note: "" })}>Remove…</button>}
                  </div>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {archiving && (
        <div className="modal-backdrop" onClick={() => setArchiving(null)}>
          <form className="modal archive-modal" onSubmit={archive} onClick={(e) => e.stopPropagation()}>
            <h2>Remove {archiving.contact.name}</h2>
            <div className="archive-choice">
              <div>
                <b>Archive (passed away or no longer in contact)</b>
                <p className="muted small">
                  Their photo disappears from {patient.name}'s screen and calls to them are gently refused (by tap or by
                  voice), without saying why. Memories about them are kept but hidden from {patient.name}; unarchiving
                  restores them.
                </p>
                <label className="archive-label">How does the family answer when {patient.name} asks for {archiving.contact.name}?
                  <textarea rows={3} value={archiving.note} onChange={(e) => setArchiving({ ...archiving, note: e.target.value })}
                    placeholder={`e.g. Don't correct ${patient.name}. Say "Tell me about them" and talk about the good times.`} />
                </label>
                <p className="muted small">Shown to other relatives before and during their calls, never to {patient.name}.</p>
                <button className="btn">Archive {archiving.contact.name}</button>
              </div>
              <div>
                <b>Delete permanently</b>
                <p className="muted small">Forgets {archiving.contact.name} and every memory about them. Cannot be undone.</p>
                <button type="button" className="btn-ghost danger" onClick={() => removeContact(archiving.contact)}>Delete permanently</button>
              </div>
            </div>
            <button type="button" className="btn-link" onClick={() => setArchiving(null)}>Cancel</button>
          </form>
        </div>
      )}

      {archived.length > 0 && (
        <div className="panel">
          <div className="panel-title">Archived <span className="panel-sub">hidden from {patient.name}; calls are refused gently</span></div>
          {archived.map((c) => (
            <ArchivedRow key={c.id} c={c} canEdit={ctx.canEdit} patientName={patient.name}
              onSave={(n) => saveNote(c, n)} onUnarchive={() => unarchive(c)} onDelete={() => removeContact(c)} />
          ))}
        </div>
      )}

      <div className="grid-3">
        <form className="panel form" onSubmit={addContact}>
          <div className="panel-title">Add a person</div>
          <input required placeholder="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input required placeholder="Relation to the patient (e.g. niece)" value={form.relation} onChange={(e) => setForm({ ...form, relation: e.target.value })} />
          <input placeholder="Detail (e.g. Meera's daughter)" value={form.relation_detail} onChange={(e) => setForm({ ...form, relation_detail: e.target.value })} />
          <input placeholder={`What ${patient.name} calls them (optional, e.g. Lata tai)`} value={form.known_as} onChange={(e) => setForm({ ...form, known_as: e.target.value })} />
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

function ArchivedRow({ c, canEdit, patientName, onSave, onUnarchive, onDelete }) {
  const [note, setNote] = useState(c.archive_note || "");
  const dirty = note !== (c.archive_note || "");
  return (
    <div className="archived-row">
      <Avatar contact={c} size={44} />
      <div className="cc-body">
        <div><b>{c.name}</b> <span className="muted">{c.relation}{c.relation_detail && ` · ${c.relation_detail}`}</span></div>
        <textarea rows={2} value={note} disabled={!canEdit} onChange={(e) => setNote(e.target.value)}
          placeholder={`How the family answers when ${patientName} asks for ${c.name}`} />
        {canEdit && (
          <div className="row">
            {dirty && <button className="btn-sm" onClick={() => onSave(note)}>Save note</button>}
            <button className="btn-sm" onClick={onUnarchive}>Unarchive</button>
            <button className="btn-link danger small" onClick={onDelete}>Delete permanently</button>
          </div>
        )}
      </div>
    </div>
  );
}
