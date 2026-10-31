import { useEffect, useState } from "react";
import { api } from "../../lib/api.js";
import { Avatar, Chip, fmtDateTime, relTime } from "../../components/common.jsx";
import PhotoPicker from "../../components/PhotoPicker.jsx";
import FaceTest from "../../components/FaceTest.jsx";

const EMPTY = { name: "", relation: "", relation_detail: "", email: "", phone: "", notes: "" };

export default function Relatives({ ctx }) {
  const { patient, contacts, tick, refresh, canEdit } = ctx;
  const [form, setForm] = useState(EMPTY);
  const [photos, setPhotos] = useState([]);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [lookalike, setLookalike] = useState(null);
  const [guests, setGuests] = useState([]);
  const [editing, setEditing] = useState(null);
  const [photosFor, setPhotosFor] = useState(null);
  const [testing, setTesting] = useState(false);
  const [showForm, setShowForm] = useState(false);

  useEffect(() => {
    api.get(`/patients/${patient.id}/guests`).then(setGuests).catch(() => setGuests([]));
  }, [patient.id, tick]);

  const relatives = contacts.filter((c) => c.status === "active");
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  async function add(e, ignoreLookalike = false) {
    e?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/contacts", {
        patient_id: patient.id, name: form.name, relation: form.relation, relation_detail: form.relation_detail || null,
        email: form.email, phone: form.phone, notes: form.notes.split("\n"), ignore_lookalike: ignoreLookalike,
        faces: photos.map((p) => ({ descriptor: p.descriptor, photo: p.photo })),
      });
      setForm(EMPTY);
      setPhotos([]);
      setConsent(false);
      setLookalike(null);
      setShowForm(false);
      refresh();
    } catch (err) {
      if (err.status === 409) setLookalike(err.message);
      else setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(c) {
    if (!confirm(`Remove ${c.name}? Their photos, face data and every memory about them are deleted.`)) return;
    await api.del(`/contacts/${c.id}`);
    refresh();
  }

  const ready = form.name.trim() && form.relation.trim() && form.email.trim() && form.phone.trim() && photos.length > 0 && consent;
  return (
    <div className="relatives">
      <div className="row space">
        <p className="muted">
          Relatives sign in with Google using the email you enter here. Their photos let {patient.name}'s screen recognise them on a meet.
        </p>
        <div className="row">
          <button className="btn-ghost" onClick={() => setTesting(true)}>Test recognition</button>
          {canEdit && <button className="btn" onClick={() => setShowForm((v) => !v)}>{showForm ? "Close" : "+ Add a relative"}</button>}
        </div>
      </div>

      {showForm && (
        <form className="panel form add-relative" onSubmit={add}>
          <div className="panel-title">Add a relative</div>
          <div className="grid-form">
            <label>Name<input required value={form.name} onChange={set("name")} autoComplete="off" /></label>
            <label>Relation to {patient.name}<input required value={form.relation} onChange={set("relation")} placeholder="e.g. granddaughter" /></label>
            <label>Email (for their sign-in)<input required type="email" value={form.email} onChange={set("email")} /></label>
            <label>Phone<input required type="tel" value={form.phone} onChange={set("phone")} placeholder="+91 …" /></label>
            <label>Detail (optional)<input value={form.relation_detail} onChange={set("relation_detail")} placeholder="e.g. Rahul's daughter" /></label>
          </div>
          <label>Things to remember about them (optional, one per line)
            <textarea rows={2} value={form.notes} onChange={set("notes")} placeholder={"Lives in Pune\nHas a dog named Bruno"} />
          </label>
          <div className="sub-title">Face photos</div>
          <PhotoPicker value={photos} onChange={setPhotos} />
          <label className="check">
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            This person agreed to their photos being used to recognise them on calls.
          </label>
          {error && <div className="auth-error" role="alert">{error}</div>}
          {lookalike && (
            <div className="warnbox">
              {lookalike}
              <div className="row"><button type="button" className="btn-ghost" onClick={() => add(null, true)}>They really look alike, add anyway</button></div>
            </div>
          )}
          <button className="btn" disabled={!ready || busy}>{busy ? "Saving…" : "Add relative"}</button>
        </form>
      )}

      <div className="contact-cards">
        {relatives.length === 0 && <div className="panel empty-state">No relatives yet. Add the people {patient.name} talks to.</div>}
        {relatives.map((c) => (
          <div key={c.id} className="panel contact-card">
            <Avatar contact={c} size={72} />
            <div className="cc-body">
              <div className="cc-name">{c.name} <span className="muted">· {c.relation}</span></div>
              {c.relation_detail && <div className="muted small">{c.relation_detail}</div>}
              <div className="small">{c.email || "no email"} · {c.phone || "no phone"}</div>
              <div className="cc-rules">
                {c.face_count > 0 ? <Chip tone="good">{c.face_count} face photo{c.face_count > 1 ? "s" : ""}</Chip>
                  : <Chip tone="warn">No photo: can't be recognised</Chip>}
                {c.has_account ? <Chip tone="info">signed in {c.last_login ? relTime(c.last_login, ctx.offsetMs) : ""}</Chip>
                  : <Chip>hasn't signed in yet</Chip>}
              </div>
              {canEdit && (
                <div className="row small-row">
                  <button className="btn-link" onClick={() => setPhotosFor(c)}>Photos</button>
                  <button className="btn-link" onClick={() => setEditing(c)}>Edit</button>
                  <button className="btn-link danger" onClick={() => remove(c)}>Remove</button>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="panel">
        <div className="panel-title">Unverified guests <span className="panel-sub">people the screen didn't recognise; saved for you to check</span></div>
        {guests.length === 0 && <div className="empty">No new faces to check.</div>}
        {guests.map((g) => <GuestRow key={g.id} g={g} patient={patient} canEdit={canEdit} refresh={refresh} />)}
      </div>

      {editing && <EditRelative c={editing} onClose={() => setEditing(null)} refresh={refresh} />}
      {photosFor && <PhotosModal c={photosFor} onClose={() => { setPhotosFor(null); refresh(); }} />}
      {testing && <FaceTest patientId={patient.id} onClose={() => setTesting(false)} />}
    </div>
  );
}

function GuestRow({ g, patient, canEdit, refresh }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ name: g.name === "New person" ? "" : g.name, relation: "", email: "", phone: "" });
  const [error, setError] = useState(null);

  async function approve(e) {
    e.preventDefault();
    try {
      await api.post(`/contacts/${g.id}/approve`, f);
      refresh();
    } catch (err) {
      setError(err.message);
    }
  }
  async function reject() {
    if (!confirm(`Delete ${g.name}? Their saved face and anything learned from the meet is removed.`)) return;
    await api.del(`/contacts/${g.id}`);
    refresh();
  }
  return (
    <div className="guest-row">
      <div className="row">
        {g.photo ? <img className="guest-photo" src={g.photo} alt="" /> : <div className="guest-photo blank">?</div>}
        <div className="cc-body">
          <b>{g.name}</b>
          <span className="muted small">first seen {g.first_seen ? fmtDateTime(g.first_seen) : "recently"} · {g.meets} meet{g.meets === 1 ? "" : "s"}</span>
          {g.last_summary && <span className="small">{g.last_summary}</span>}
        </div>
        {canEdit && !open && (
          <div className="row">
            <button className="btn" onClick={() => setOpen(true)}>Add to family</button>
            <button className="btn-ghost danger" onClick={reject}>Delete</button>
          </div>
        )}
      </div>
      {open && (
        <form className="grid-form guest-form" onSubmit={approve}>
          <label>Name<input required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
          <label>Relation to {patient.name}<input required value={f.relation} onChange={(e) => setF({ ...f, relation: e.target.value })} /></label>
          <label>Email (optional)<input type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></label>
          <label>Phone (optional)<input value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></label>
          {error && <div className="auth-error">{error}</div>}
          <div className="row"><button className="btn">Save as relative</button><button type="button" className="btn-ghost" onClick={() => setOpen(false)}>Cancel</button></div>
        </form>
      )}
    </div>
  );
}

function EditRelative({ c, onClose, refresh }) {
  const [f, setF] = useState({ name: c.name, relation: c.relation, relation_detail: c.relation_detail || "", email: c.email || "", phone: c.phone || "" });
  const [error, setError] = useState(null);
  async function save(e) {
    e.preventDefault();
    try {
      await api.patch(`/contacts/${c.id}`, f);
      refresh();
      onClose();
    } catch (err) {
      setError(err.message);
    }
  }
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="modal form" onClick={(e) => e.stopPropagation()} onSubmit={save}>
        <div className="panel-title">Edit {c.name}</div>
        <div className="grid-form">
          <label>Name<input required value={f.name} onChange={set("name")} /></label>
          <label>Relation<input required value={f.relation} onChange={set("relation")} /></label>
          <label>Email<input type="email" value={f.email} onChange={set("email")} /></label>
          <label>Phone<input value={f.phone} onChange={set("phone")} /></label>
          <label>Detail<input value={f.relation_detail} onChange={set("relation_detail")} /></label>
        </div>
        {error && <div className="auth-error">{error}</div>}
        <div className="row"><button className="btn">Save</button><button type="button" className="btn-ghost" onClick={onClose}>Cancel</button></div>
      </form>
    </div>
  );
}

function PhotosModal({ c, onClose }) {
  const [saved, setSaved] = useState([]);
  const [fresh, setFresh] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = () => api.get(`/contacts/${c.id}/faces`).then(setSaved);
  useEffect(() => { load(); }, [c.id]);

  async function saveNew() {
    setBusy(true);
    setError(null);
    try {
      for (const p of fresh) await api.post(`/contacts/${c.id}/faces`, { descriptor: p.descriptor, photo: p.photo });
      setFresh([]);
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function drop(fid) {
    try {
      await api.del(`/contacts/${c.id}/faces/${fid}`);
      load();
    } catch (e) {
      setError(e.message);
    }
  }
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal form" onClick={(e) => e.stopPropagation()}>
        <div className="panel-title">{c.name}'s face photos</div>
        <div className="pp-grid">
          {saved.map((f) => (
            <div key={f.id} className="pp-item ok">
              {f.photo ? <img src={f.photo} alt="" /> : <span className="pp-msg">learned on a call</span>}
              <button className="pp-x" aria-label="Delete photo" onClick={() => drop(f.id)}>×</button>
            </div>
          ))}
        </div>
        <PhotoPicker value={fresh} onChange={setFresh} existing={saved.length} max={8} />
        {error && <div className="auth-error">{error}</div>}
        <div className="row">
          <button className="btn" disabled={!fresh.length || busy} onClick={saveNew}>{busy ? "Saving…" : `Save ${fresh.length || ""} new photo${fresh.length === 1 ? "" : "s"}`}</button>
          <button className="btn-ghost" onClick={onClose}>Done</button>
        </div>
      </div>
    </div>
  );
}
