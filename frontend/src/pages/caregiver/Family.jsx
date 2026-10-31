import { useEffect, useState } from "react";
import { api } from "../../lib/api.js";
import { Chip, fmtDateTime, relTime } from "../../components/common.jsx";

const ROLE_HELP = {
  owner: "Everything, including inviting family and unpairing devices",
  family: "Review and correct memories, add people and rules, pair tablets",
  viewer: "Read-only: see calls, memories and insights",
};
const EVENT_LABEL = {
  sign_in: "signed in", device_paired: "paired a tablet", device_unpaired: "unpaired a tablet",
  member_joined: "joined the circle", member_removed: "removed a member", circle_created: "created the circle",
};

export default function Family({ ctx, refreshMe }) {
  const { patient, role, tick } = ctx;
  const isOwner = role === "owner";
  const canEdit = role !== "viewer";
  const [members, setMembers] = useState([]);
  const [devices, setDevices] = useState([]);
  const [log, setLog] = useState([]);
  const [invite, setInvite] = useState(null);
  const [inviteRole, setInviteRole] = useState("family");
  const [pair, setPair] = useState({ code: "", label: `${patient.name}'s tablet` });
  const [msg, setMsg] = useState(null);
  const [n, setN] = useState(0);

  useEffect(() => {
    api.get(`/circles/${patient.id}/members`).then(setMembers);
    api.get(`/circles/${patient.id}/devices`).then(setDevices);
    if (isOwner) api.get(`/circles/${patient.id}/access-log`).then(setLog);
  }, [patient.id, tick, n]);

  const reload = () => setN((x) => x + 1);

  async function makeInvite() {
    setInvite(await api.post(`/circles/${patient.id}/invites`, { role: inviteRole }));
  }

  async function pairDevice(e) {
    e.preventDefault();
    setMsg(null);
    try {
      await api.post(`/circles/${patient.id}/devices`, pair);
      setMsg({ ok: true, text: "Paired. The tablet will open the home screen within a few seconds." });
      setPair({ ...pair, code: "" });
      reload();
    } catch (err) {
      setMsg({ ok: false, text: err.message });
    }
  }

  async function unpair(d) {
    if (!confirm(`Unpair "${d.label}"? It will show the setup screen again until someone pairs it.`)) return;
    await api.del(`/devices/${d.id}`);
    reload();
  }

  async function changeRole(m, newRole) {
    await api.patch(`/circles/${patient.id}/members/${m.user_id}`, { role: newRole });
    reload();
    if (m.is_me) refreshMe();
  }

  async function remove(m) {
    if (!confirm(`Remove ${m.name || m.email} from ${patient.name}'s circle?`)) return;
    try {
      await api.del(`/circles/${patient.id}/members/${m.user_id}`);
      if (m.is_me) await refreshMe();
      reload();
    } catch (err) {
      alert(err.message);
    }
  }

  return (
    <div className="family">
      <div className="grid-2">
        <div className="col">
          <div className="panel">
            <div className="panel-title">Care circle <span className="panel-sub">{members.length} people · stored in MongoDB, identity by Firebase</span></div>
            <table className="member-table">
              <tbody>
                {members.map((m) => (
                  <tr key={m.user_id}>
                    <td className="m-avatar">
                      {m.picture ? <img src={m.picture} alt="" referrerPolicy="no-referrer" /> :
                        <span>{(m.name || m.email || "?").slice(0, 1).toUpperCase()}</span>}
                    </td>
                    <td>
                      <b>{m.name || m.email}</b>{m.is_me && <span className="muted"> (you)</span>}
                      <div className="muted small">{m.relation || m.email}{m.last_seen && ` · active ${relTime(m.last_seen)}`}</div>
                    </td>
                    <td>
                      {isOwner && !m.is_me ? (
                        <select value={m.role} onChange={(e) => changeRole(m, e.target.value)} aria-label="Role">
                          <option value="owner">owner</option><option value="family">family</option><option value="viewer">viewer</option>
                        </select>
                      ) : <Chip tone={m.role === "owner" ? "info" : m.role === "family" ? "good" : "neutral"}>{m.role}</Chip>}
                    </td>
                    <td>{(isOwner || m.is_me) && <button className="btn-link danger" onClick={() => remove(m)}>{m.is_me ? "leave" : "remove"}</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <ul className="role-help">
              {Object.entries(ROLE_HELP).map(([r, t]) => <li key={r}><b>{r}</b>: {t}</li>)}
            </ul>
          </div>

          {isOwner && (
            <div className="panel">
              <div className="panel-title">Invite family <span className="panel-sub">codes expire after 7 days (MongoDB TTL)</span></div>
              <div className="row">
                <select value={inviteRole} onChange={(e) => setInviteRole(e.target.value)}>
                  <option value="family">as family (can edit)</option>
                  <option value="viewer">as viewer (read-only)</option>
                  <option value="owner">as owner</option>
                </select>
                <button className="btn" onClick={makeInvite}>Create invite code</button>
              </div>
              {invite && (
                <div className="invite-box">
                  <div className="invite-code">{invite.code.slice(0, 4)}-{invite.code.slice(4)}</div>
                  <div className="muted small">Share this link, or ask them to enter the code after signing in:</div>
                  <div className="row">
                    <code className="invite-url">{invite.url}</code>
                    <button className="btn-ghost" onClick={() => navigator.clipboard?.writeText(invite.url)}>Copy link</button>
                  </div>
                  <div className="muted small">Valid until {fmtDateTime(invite.expires_at)}</div>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="col">
          <PatientSignIn patient={patient} canEdit={canEdit} refreshMe={refreshMe} />
          <div className="panel">
            <div className="panel-title">Patient devices <span className="panel-sub">no passwords for {patient.name}</span></div>
            <p className="muted small">
              Open <b>/patient</b> on {patient.name}'s tablet. It shows a 6-digit number in large type. Enter it here once;
              the tablet then stays signed in, and you can unpair it remotely if it's lost.
            </p>
            {canEdit && (
              <form className="pair-form" onSubmit={pairDevice}>
                <input required inputMode="numeric" pattern="[0-9 ]{6,7}" className="code-input" placeholder="123 456"
                  value={pair.code} onChange={(e) => setPair({ ...pair, code: e.target.value })} aria-label="Pairing number" />
                <input value={pair.label} onChange={(e) => setPair({ ...pair, label: e.target.value })} aria-label="Device name" />
                <button className="btn">Pair a tablet</button>
              </form>
            )}
            {msg && <div className={msg.ok ? "auth-notice" : "auth-error"}>{msg.text}</div>}
            <ul className="device-list">
              {devices.length === 0 && <li className="empty">No tablets paired yet.</li>}
              {devices.map((d) => (
                <li key={d.id} className={d.revoked ? "revoked" : ""}>
                  <div>
                    <b>{d.label}</b> {d.revoked ? <Chip tone="muted">unpaired</Chip> : <Chip tone="good">active</Chip>}
                    <div className="muted small">
                      Paired by {d.paired_by_name || "family"} {relTime(d.paired_at, ctx.offsetMs)}
                      {!d.revoked && d.last_seen && ` · last seen ${relTime(d.last_seen, ctx.offsetMs)}`}
                    </div>
                  </div>
                  {!d.revoked && canEdit && <button className="btn-ghost danger" onClick={() => unpair(d)}>Unpair</button>}
                </li>
              ))}
            </ul>
          </div>

          {isOwner && (
            <div className="panel">
              <div className="panel-title">Access log <span className="panel-sub">kept 90 days (TTL)</span></div>
              <ul className="access-log">
                {log.map((e) => (
                  <li key={e.id}>
                    <span><b>{e.who || "someone"}</b> {EVENT_LABEL[e.kind] || e.kind}{e.label ? ` (${e.label})` : ""}</span>
                    <span className="muted small">{fmtDateTime(e.ts)}{e.provider === "google.com" ? " · Google" : ""}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** The Google account the patient signs in with. Alternative to pairing a tablet with a code. */
function PatientSignIn({ patient, canEdit, refreshMe }) {
  const [email, setEmail] = useState(patient.login_email || "");
  const [msg, setMsg] = useState(null);
  async function save(e) {
    e.preventDefault();
    try {
      await api.patch(`/circles/${patient.id}`, { login_email: email });
      setMsg({ ok: true, text: email ? `${patient.name} can now sign in with Google as ${email}.` : "Google sign-in removed." });
      refreshMe?.();
    } catch (err) {
      setMsg({ ok: false, text: err.message });
    }
  }
  return (
    <form className="panel form" onSubmit={save}>
      <div className="panel-title">{patient.name}'s sign-in <span className="panel-sub">optional</span></div>
      <p className="muted small">
        If {patient.name} has a Google account, enter it here. On their screen they choose Sign in with Google and see only
        their upcoming meets. Or skip this and pair a tablet below, which needs no account at all.
      </p>
      <div className="row">
        <input type="email" value={email} disabled={!canEdit} onChange={(e) => setEmail(e.target.value)} placeholder="name@gmail.com" />
        {canEdit && <button className="btn">Save</button>}
      </div>
      {msg && <div className={msg.ok ? "auth-notice" : "auth-error"}>{msg.text}</div>}
    </form>
  );
}
