import { useEffect, useRef, useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { api, openSocket } from "../lib/api.js";
import { useCaregiverAuth } from "../lib/auth.jsx";
import { Avatar } from "../components/common.jsx";
import { RelativeNotes, RingScreen, ShareUpdate, useRinger } from "../components/Relative.jsx";

/** A relative's own page (/family): sign in once, and the patient's calls find you on any device.
 * Waiting room, ring, share an update, notes and past calls, for every person they are linked to. */
export default function FamilyHome() {
  const { user, me, error, signOut, refreshMe, identity } = useCaregiverAuth();
  const nav = useNavigate();
  const [selected, setSelected] = useState(null);
  const [info, setInfo] = useState(null);
  const [ready, setReady] = useState(false);
  const [online, setOnline] = useState(false);
  const [ring, setRing] = useState(null);
  const [missed, setMissed] = useState(null);
  const [paste, setPaste] = useState("");
  const [linkError, setLinkError] = useState(null);
  const sock = useRef(null);
  const ringer = useRinger();

  const links = me?.relative || [];
  const current = links.find((l) => l.contact_id === selected) || links[0];

  const load = () => current && api.get(`/relative/contacts/${current.contact_id}`).then(setInfo).catch(() => setInfo(null));
  useEffect(() => { setInfo(null); load(); }, [current?.contact_id]);

  useEffect(() => {
    if (!ready || links.length === 0) return;
    const s = openSocket("/ws/relative", (msg) => {
      if (msg.type === "ring") {
        setMissed(null);
        setRing(msg);
      } else if (msg.type === "ring_over") {
        setRing((r) => (r && r.call_id === msg.call_id ? null : r));
        if (msg.reason === "unanswered") setMissed(new Date());
      } else if (msg.type === "accepted") {
        nav(`/join/${msg.call_id}?t=${encodeURIComponent(msg.join_token)}&back=${encodeURIComponent("/family")}`);
      }
    }, (st) => setOnline(st === "open"), async () => ({ type: "auth", token: await identity.getToken() }));
    sock.current = s;
    return () => s.close();
  }, [ready, links.map((l) => l.contact_id).join(",")]);

  useEffect(() => {
    if (ring && ready) ringer.start();
    else ringer.stop();
    document.title = ring ? `📞 ${ring.patient_name} is calling` : "Pehchaan";
  }, [ring, ready]);

  if (user === undefined || (user && !user.anonymous && !me && !error)) return <div className="cg loading">Signing you in…</div>;
  if (!user || user.anonymous) return <Navigate to="/login?as=relative&next=/family" replace />;
  if (error) return <div className="cg loading">Couldn't reach the server: {error}</div>;

  function getReady() {
    ringer.unlock();
    setReady(true);
  }

  function accept() {
    ringer.unlock();
    ringer.stop();
    sock.current?.send({ type: "accept", call_id: ring.call_id, contact_id: ring.contact_id });
  }

  function decline() {
    ringer.stop();
    sock.current?.send({ type: "decline", call_id: ring.call_id, contact_id: ring.contact_id });
    setRing(null);
  }

  async function addLink(e) {
    e.preventDefault();
    setLinkError(null);
    try {
      const r = await api.post("/relative/link", { token: paste });
      setPaste("");
      await refreshMe();
      setSelected(r.contact_id);
    } catch (err) {
      setLinkError(err.message);
    }
  }

  async function unlink() {
    if (!confirm(`Remove ${current.patient_name}'s calls from your account? You can add the link again later.`)) return;
    await api.del(`/relative/contacts/${current.contact_id}/link`);
    setSelected(null);
    await refreshMe();
  }

  if (ring) return <RingScreen ring={ring} onAccept={accept} onDecline={decline} />;

  const addForm = (
    <form className="join-card" onSubmit={addLink}>
      <h2>{links.length ? "Add someone else" : "Connect to your family"}</h2>
      <p className="muted">
        Each family member has their own personal link, which looks like <code>…/c/AbC123</code>. Paste it here, or open
        it once and choose “Add to my account”. A caregiver copies it from <b>Contacts &amp; rules → Copy link</b>.
        (A link containing <code>/login?invite=</code> is for caregivers and won't work here.)
      </p>
      <input className="big-input link-input" required value={paste} onChange={(e) => setPaste(e.target.value)}
        placeholder="https://…/c/…" />
      <button className="btn">Add to my account</button>
      {linkError && <div className="auth-error">{linkError}</div>}
    </form>
  );

  return (
    <div className="contact-page">
      <div className="family-top">
        <b>Pehchaan</b>
        <span className="muted small">
          {me.name || me.email}
          {me.circles.length > 0 && <> · <Link to="/caregiver">Caregiver dashboard</Link></>}
          {" · "}<button className="btn-link" onClick={signOut}>Sign out</button>
        </span>
      </div>

      {links.length === 0 ? addForm : (
        <>
          {links.length > 1 && (
            <div className="join-card family-pick">
              {links.map((l) => (
                <button key={l.contact_id} className={`seg ${l.contact_id === current.contact_id ? "on" : ""}`}
                  onClick={() => setSelected(l.contact_id)}>{l.patient_name}</button>
              ))}
            </div>
          )}

          <div className="join-card">
            <div className="family-who">
              {info && <Avatar contact={info.contact} size={64} />}
              <div>
                <div className="join-kicker">Hi {current.contact_name}</div>
                <h1>Waiting for {current.patient_name}'s call</h1>
              </div>
            </div>
            {!ready ? (
              <>
                <p>Keep this page open and it rings when {current.patient_name} calls you, or presses “Join the family call”.</p>
                <button className="btn-big btn-primary" onClick={getReady}>I'm ready</button>
              </>
            ) : (
              <p className="cl-status">
                <span className={`dot ${online ? "ok" : "warn"}`} />
                {online ? `Ready. It rings here when ${current.patient_name} calls.` : "Connecting…"}
              </p>
            )}
            {missed && <p className="cl-missed">You missed a call from {current.patient_name} at {missed.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" })}.</p>}
          </div>

          {info && (
            <>
              <ShareUpdate patientName={info.patient.name} updates={info.updates} onSaved={load}
                send={(text) => api.post(`/relative/contacts/${current.contact_id}/update`, { text })} />
              <RelativeNotes info={info} />
            </>
          )}
          {addForm}
          <p className="fine">
            <button className="btn-link danger" onClick={unlink}>Remove {current.patient_name} from my account</button>
          </p>
        </>
      )}
    </div>
  );
}
