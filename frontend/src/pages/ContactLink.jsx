import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, openSocket, requestAs, setIdentity } from "../lib/api.js";
import { caregiverIdentity } from "../lib/identity.js";
import { RelativeNotes, RingScreen, ShareUpdate, useRinger } from "../components/Relative.jsx";

/** The relative's own link (/c/:token): waiting room, incoming-call ring, share an update, notes, past calls.
 * No account needed. Signed in, they can keep the link on their account and use /family instead. */
export default function ContactLink() {
  const { token } = useParams();
  const [params, setParams] = useSearchParams();
  const nav = useNavigate();
  setIdentity(null); // the link is the pass
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);
  const [ready, setReady] = useState(false);
  const [online, setOnline] = useState(false);
  const [ring, setRing] = useState(null);
  const [missed, setMissed] = useState(null);
  const [account, setAccount] = useState(undefined); // the signed-in account, if any
  const [kept, setKept] = useState(null);
  const sock = useRef(null);
  const ringer = useRinger();
  const back = `/c/${token}`;

  const load = () => api.get(`/c/${token}`).then((d) => {
    setInfo(d);
    if (d.ringing) setRing(d.ringing);
  }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [token]);

  useEffect(() => caregiverIdentity.onChange((u) => setAccount(u && !u.anonymous ? u : null)), []);

  async function keepLink() {
    try {
      const r = await requestAs(caregiverIdentity, "POST", "/relative/link", { token });
      setKept(r);
      setParams({}, { replace: true });
      load();
    } catch (e) {
      setKept({ error: e.message });
    }
  }
  // Coming back from sign-in with ?keep=1: link it straight away.
  useEffect(() => {
    if (account && params.get("keep") && info && !info.linked) keepLink();
  }, [account, info?.linked]);

  useEffect(() => {
    if (!ready) return;
    const s = openSocket("/ws/contact", (msg) => {
      if (msg.type === "ring") {
        setMissed(null);
        setRing(msg);
      } else if (msg.type === "ring_over") {
        setRing((r) => (r && r.call_id === msg.call_id ? null : r));
        if (msg.reason === "unanswered") setMissed(new Date());
      } else if (msg.type === "accepted") {
        nav(`/join/${msg.call_id}?t=${encodeURIComponent(msg.join_token)}&back=${encodeURIComponent(back)}`);
      } else if (msg.type === "link_revoked") {
        setError("This link was replaced by a new one. Ask the family for the new link.");
      }
    }, (st) => setOnline(st === "open"), async () => ({ type: "auth", link_token: token }));
    sock.current = s;
    return () => s.close();
  }, [ready, token]);

  useEffect(() => {
    if (ring && ready) ringer.start();
    else ringer.stop();
    document.title = ring ? `📞 ${info?.patient.name || ""} is calling` : "Pehchaan";
  }, [ring, ready]);

  function getReady() {
    ringer.unlock();
    setReady(true);
  }

  function accept() {
    ringer.unlock();
    ringer.stop();
    if (sock.current && ready) sock.current.send({ type: "accept", call_id: ring.call_id, contact_id: ring.contact_id });
    else if (ring.join_token) nav(`/join/${ring.call_id}?t=${encodeURIComponent(ring.join_token)}&back=${encodeURIComponent(back)}`);
  }

  function decline() {
    ringer.stop();
    sock.current?.send({ type: "decline", call_id: ring.call_id, contact_id: ring.contact_id });
    setRing(null);
  }

  if (error) return <div className="join"><div className="join-card"><h2>This link isn't working</h2><p>{error}</p></div></div>;
  if (!info) return <div className="join"><div className="join-card">Loading…</div></div>;
  const { patient, contact } = info;

  if (ring) return <RingScreen ring={ring} onAccept={accept} onDecline={decline} />;

  return (
    <div className="contact-page">
      <div className="join-card">
        <div className="join-kicker">Hi {contact.name}</div>
        <h1>Waiting for {patient.name}'s call</h1>
        {!ready ? (
          <>
            <p>Keep this page open and you'll hear it ring when {patient.name} calls.</p>
            <button className="btn-big btn-primary" onClick={getReady}>I'm ready</button>
          </>
        ) : (
          <p className="cl-status">
            <span className={`dot ${online ? "ok" : "warn"}`} />
            {online ? `Ready. It will ring here when ${patient.name} calls.` : "Connecting…"}
          </p>
        )}
        {missed && <p className="cl-missed">You missed a call from {patient.name} at {missed.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" })}. They'll try again later.</p>}
      </div>

      <div className="join-card keep-link">
        {info.linked || kept?.contact_name ? (
          <>
            <b>This link is on your account.</b>
            <p className="muted small">Next time, just sign in at <Link to="/family">/family</Link>. No link needed.</p>
          </>
        ) : account === undefined ? null : account ? (
          <>
            <b>Keep this on your account</b>
            <p className="muted small">Signed in as {account.email || account.name}. Then you can sign in later instead of keeping this link.</p>
            <button className="btn" onClick={keepLink}>Add to my account</button>
          </>
        ) : (
          <>
            <b>Don't want to keep this link?</b>
            <p className="muted small">Create an account (or sign in) and {patient.name}'s calls will find you from any device.</p>
            <Link className="btn" to={`/login?as=relative&next=${encodeURIComponent(`${back}?keep=1`)}`}>Sign in or create an account</Link>
          </>
        )}
        {kept?.error && <div className="auth-error">{kept.error}</div>}
      </div>

      <ShareUpdate patientName={patient.name} updates={info.updates} onSaved={load}
        send={(text) => api.post(`/c/${token}/update`, { text })} />
      <RelativeNotes info={info} />
    </div>
  );
}
