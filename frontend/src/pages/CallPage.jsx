import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, openSocket } from "../lib/api.js";
import { Peer } from "../lib/rtc.js";
import { createTranscriber, speechSupported } from "../lib/speech.js";
import { speak, stopSpeaking } from "../lib/tts.js";
import { useFaceIdentify } from "../lib/useFaceIdentify.js";
import ParticipantCard, { speechFor } from "../components/ParticipantCard.jsx";

// One id per browser tab, so the same person can reconnect but a second person can't take the seat.
const SID = (() => {
  try {
    let s = sessionStorage.getItem("pehchaan.sid");
    if (!s) sessionStorage.setItem("pehchaan.sid", (s = Math.random().toString(36).slice(2, 12)));
    return s;
  } catch {
    return Math.random().toString(36).slice(2, 12);
  }
})();

/**
 * A 1:1 video meet.
 *  role "patient": the patient's screen, with the "who is this" popup driven by face matching
 *  role "contact": the other person, either a relative signed in (identity) or a guest (joinToken + guestName)
 */
export default function CallPage({ role, identity, joinToken, guestName, homePath }) {
  const { callId } = useParams();
  const nav = useNavigate();
  const isPatient = role === "patient";

  const [call, setCall] = useState(null);
  const [patientName, setPatientName] = useState(null);
  const [peerHere, setPeerHere] = useState(false);
  const [peerKey, setPeerKey] = useState(0); // bumps when a (possibly different) person joins
  const [hasRemote, setHasRemote] = useState(false);
  const [hasLocalVideo, setHasLocalVideo] = useState(false);
  const [connState, setConnState] = useState("new");
  const [captions, setCaptions] = useState([]);
  const [interim, setInterim] = useState("");
  const [offRecord, setOffRecord] = useState(false);
  const [muted, setMuted] = useState(false);
  const [camOn, setCamOn] = useState(true);
  const [ended, setEnded] = useState(false);
  const [full, setFull] = useState(false);
  const [recap, setRecap] = useState(null);
  const [typeOpen, setTypeOpen] = useState(!speechSupported);
  const [typed, setTyped] = useState("");
  const [sttError, setSttError] = useState(null);
  const [accountPerson, setAccountPerson] = useState(null);
  const [suggestion, setSuggestion] = useState(null);
  const [collapsed, setCollapsed] = useState(false);
  const [toast, setToast] = useState(null);

  const localVideo = useRef(null);
  const remoteVideo = useRef(null);
  const peer = useRef(null);
  const sock = useRef(null);
  const stt = useRef(null);
  const offRef = useRef(false);
  const spokenFor = useRef(null);

  const face = useFaceIdentify({ videoRef: remoteVideo, callId, enabled: isPatient && hasRemote && !ended, resetKey: peerKey });

  async function loadParticipant() {
    if (!isPatient) return;
    try {
      const r = await api.get(`/calls/${callId}/participant`);
      setAccountPerson(r.person);
    } catch {}
  }

  // ---- setup: call info, media, signalling, transcription
  useEffect(() => {
    let disposed = false;
    if (isPatient) api.get(`/calls/${callId}`).then((c) => !disposed && setCall(c)).catch(() => {});

    const p = new Peer({
      role,
      send: (data) => sock.current?.send({ type: "signal", data }),
      onRemoteStream: (stream) => {
        if (remoteVideo.current) remoteVideo.current.srcObject = stream;
        setHasRemote(true);
      },
      onState: setConnState,
    });
    peer.current = p;

    const hello = async () => {
      if (joinToken) return { type: "auth", join_token: joinToken, name: guestName, sid: SID };
      return { type: "auth", token: await identity.getToken(), sid: SID };
    };

    (async () => {
      const stream = await p.startMedia();
      if (disposed) return p.close();
      if (stream && localVideo.current) localVideo.current.srcObject = stream;
      setHasLocalVideo(!!stream?.getVideoTracks().length);

      sock.current = openSocket(`/ws/call/${callId}?role=${role}`, async (msg) => {
        switch (msg.type) {
          case "welcome":
            setOffRecord(msg.off_record);
            offRef.current = msg.off_record;
            if (msg.patient_name) setPatientName(msg.patient_name);
            if (["ended", "processing", "processed"].includes(msg.status)) setEnded(true);
            if (msg.peers.length) {
              setPeerHere(true);
              if (isPatient) {
                p.call();
                loadParticipant();
              }
            }
            break;
          case "peer_joined":
            setPeerHere(true);
            setPeerKey((k) => k + 1);
            if (isPatient) p.call();
            break;
          case "participant":
            loadParticipant();
            break;
          case "peer_left":
            setPeerHere(false);
            setHasRemote(false);
            setAccountPerson(null);
            setSuggestion(null);
            spokenFor.current = null;
            p.dropRemote();
            break;
          case "signal":
            await p.handle(msg.data);
            break;
          case "transcript":
            setCaptions((cs) => [...cs.slice(-5), msg]);
            if (msg.speaker !== role) setInterim("");
            break;
          case "interim":
            setInterim(msg.text);
            break;
          case "off_record":
            setOffRecord(msg.value);
            offRef.current = msg.value;
            break;
          case "full":
            setFull(true);
            break;
          case "hangup":
            setEnded(true);
            break;
          case "processed":
            if (msg.recap) setRecap(msg.recap);
            break;
        }
      }, undefined, hello);

      const t = createTranscriber({
        onFinal: (text) => text && sock.current?.send({ type: "transcript", text }),
        onInterim: (text) => text && sock.current?.send({ type: "interim", text }),
        onError: setSttError,
      });
      stt.current = t;
      t?.start();
    })();

    return () => {
      disposed = true;
      stt.current?.stop();
      sock.current?.close();
      p.close();
      stopSpeaking();
    };
  }, [callId, role]);

  // ---- who is on the other end (the face match wins; the relative's account fills in while we look)
  const person = face.person || accountPerson;
  let view;
  if (!peerHere) view = { phase: "waiting" };
  else if (person) view = { ...face, phase: face.person ? face.phase : "account", person };
  else if (face.phase === "searching") view = { phase: "noface" };
  else view = { ...face, person: null };

  useEffect(() => {
    if (!isPatient || !person || spokenFor.current === person.contact_id) return;
    spokenFor.current = person.contact_id;
    setCollapsed(false);
    speak(speechFor(person));
    setSuggestion(null);
    if (person.kind === "relative") {
      api.get(`/calls/${callId}/brief`).then((b) => setSuggestion(b?.card?.suggestions?.[0] || null)).catch(() => {});
    }
  }, [person?.contact_id]);

  // ---- after the call: stop media, show the recap (patient) or a thank-you (others)
  useEffect(() => {
    if (!ended) return;
    stt.current?.stop();
    peer.current?.close();
    if (!isPatient) return;
    let stop = false;
    (async () => {
      for (let i = 0; i < 60 && !stop; i++) {
        try {
          const c = await api.get(`/calls/${callId}`);
          if (c.status === "processed") {
            const text = c.recap || "Your meet has ended.";
            setRecap(text);
            speak(text);
            return;
          }
        } catch {}
        await new Promise((r) => setTimeout(r, 1500));
      }
    })();
    return () => { stop = true; };
  }, [ended]);

  function flash(text) {
    setToast(text);
    setTimeout(() => setToast(null), 3500);
  }

  function toggleMute() {
    const m = !muted;
    setMuted(m);
    peer.current?.setMuted(m);
    if (m) stt.current?.stop();
    else stt.current?.start();
  }

  function toggleCam() {
    const on = !camOn;
    setCamOn(on);
    peer.current?.setCamera(on);
  }

  async function hangUp() {
    try {
      await api.post(`/calls/${callId}/end`, joinToken ? { t: joinToken } : {});
    } catch {}
    setEnded(true);
  }

  async function inviteAll() {
    try {
      const r = await api.post(`/calls/${callId}/invite-all`);
      flash(r.invited ? `Invited ${r.invited} relatives. The first one free will join.` : "No relatives to invite yet.");
    } catch (e) {
      flash(e.message);
    }
  }

  async function shareLink() {
    if (!call?.join_url) return;
    const text = `Join ${call.patient?.name || "the"} video meet on Pehchaan: ${call.join_url}\nOr open ${location.origin}/join and enter code ${call.code}`;
    if (navigator.share) {
      try {
        await navigator.share({ title: "Join the video meet", text });
        return;
      } catch {}
    }
    window.open(`https://wa.me/?text=${encodeURIComponent(text)}`, "_blank", "noopener");
  }

  function copy(text, what) {
    navigator.clipboard?.writeText(text).then(() => flash(`${what} copied`), () => flash(text));
  }

  function sendTyped(e) {
    e.preventDefault();
    if (!typed.trim()) return;
    sock.current?.send({ type: "transcript", text: typed.trim() });
    setTyped("");
  }

  // ---------------------------------------------------------------- end screens
  if (full) {
    return (
      <div className="call-ended">
        <h2>This meet already has someone in it</h2>
        <p>Meets are one to one. Wait for them to finish, or ask {patientName || "the patient"} to start a new meet.</p>
        {homePath && <Link className="btn" to={homePath}>Back</Link>}
      </div>
    );
  }
  if (ended) {
    if (!isPatient) {
      return (
        <div className="call-ended">
          <h2>The meet has ended</h2>
          <p>Thank you for talking with {patientName || "them"}. What you shared will help them remember next time.</p>
          {homePath && <Link className="btn" to={homePath}>Back to your dashboard</Link>}
        </div>
      );
    }
    return (
      <div className="call-ended patient">
        {recap ? <p className="recap">{recap}</p> : <p className="recap muted">Saving what you talked about…</p>}
        <button className="btn-big btn-primary" onClick={() => { stopSpeaking(); nav("/patient"); }}>Back home</button>
      </div>
    );
  }

  const otherName = isPatient ? person?.name || "Guest" : patientName || "…";
  return (
    <div className={`call ${isPatient ? "as-patient" : "as-contact"}`}>
      <div className="stage">
        <video ref={remoteVideo} className={`remote ${hasRemote ? "" : "hidden"}`} autoPlay playsInline />
        {!hasRemote && (
          <div className="waiting">
            <div className="waiting-text">
              {peerHere ? "Connecting…" : isPatient ? "Waiting for someone to join…" : `Waiting for ${patientName || "the patient"}…`}
            </div>
          </div>
        )}
        <video ref={localVideo} className={`local ${hasLocalVideo && camOn ? "" : "hidden"}`} autoPlay playsInline muted />

        {isPatient ? (
          <div className="pcard-wrap">
            <ParticipantCard view={view} suggestion={suggestion} collapsed={collapsed}
              onToggle={() => setCollapsed((c) => !c)} onRead={person ? () => speak(speechFor(person)) : null} />
          </div>
        ) : (
          <div className="name-tag">
            <span className="nt-name">{patientName || "…"}</span>
            <span className="nt-rel">{offRecord ? "Off the record: nothing is saved" : "Transcribed to help them remember"}</span>
          </div>
        )}

        <div className="captions" aria-live="polite">
          {offRecord && <div className="off-banner">Off the record: nothing is being saved</div>}
          {captions.slice(-3).map((c, i) => (
            <div key={i} className={`cap ${c.speaker === role ? "me" : ""} ${c.off_record ? "off" : ""}`}>
              <b>{c.speaker === role ? "You" : otherName}:</b> {c.text}
            </div>
          ))}
          {interim && <div className="cap interim"><b>{otherName}:</b> {interim}…</div>}
        </div>
        {toast && <div className="call-toast" role="status">{toast}</div>}
      </div>

      <div className="controls">
        <button className={`ctl ${muted ? "on" : ""}`} onClick={toggleMute}>{muted ? "Unmute" : "Mute"}</button>
        <button className={`ctl ${camOn ? "" : "on"}`} onClick={toggleCam}>{camOn ? "Camera off" : "Camera on"}</button>
        {!isPatient && (
          <button className={`ctl ${offRecord ? "on warn" : ""}`}
            onClick={() => sock.current?.send({ type: "off_record", value: !offRef.current })}>
            {offRecord ? "Back on the record" : "Go off the record"}
          </button>
        )}
        <button className="ctl" onClick={() => setTypeOpen((v) => !v)}>Type instead</button>
        <button className="ctl hang" onClick={hangUp}>End meet</button>
      </div>

      {isPatient && call && (
        <div className="invite-bar">
          <div className="invite-code">
            <span className="invite-label">Meeting code</span>
            <button className="code-chip" onClick={() => copy(call.code, "Code")}>{call.code}</button>
          </div>
          <button className="ctl" onClick={shareLink}>Share invite link</button>
          <button className="ctl" onClick={() => copy(call.join_url, "Link")}>Copy link</button>
          <button className="ctl" onClick={inviteAll}>Invite all relatives</button>
        </div>
      )}

      {typeOpen && (
        <form className="type-box" onSubmit={sendTyped}>
          <input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="Type what you said" />
          <button className="btn">Send</button>
          {sttError && <span className="stt-err">Speech recognition: {sttError}</span>}
        </form>
      )}
      <div className="conn-state">{connState !== "new" ? connState : ""}</div>
    </div>
  );
}
