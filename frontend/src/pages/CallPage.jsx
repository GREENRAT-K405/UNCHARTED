import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, openSocket } from "../lib/api.js";
import { deviceIdentity } from "../lib/identity.js";
import { Peer } from "../lib/rtc.js";
import { createTranscriber, speechSupported } from "../lib/speech.js";
import { speak, stopSpeaking } from "../lib/tts.js";
import { Avatar, BriefCard } from "../components/common.jsx";

function mediaMessage(err) {
  if (err === "insecure")
    return `Camera and microphone are blocked: ${location.origin} is not a secure page. ` +
      "Open the https:// link instead (the host runs npm run dev:https).";
  const camOnly = err.startsWith("camera:");
  const name = camOnly ? err.slice(7) : err;
  const what = camOnly ? "Camera" : "Camera and microphone";
  const why = {
    NotAllowedError: "permission was denied. Click the camera icon in the address bar and allow access.",
    NotFoundError: "no device was found.",
    NotReadableError: "the device is in use by another app or tab.",
  }[name] || `could not be opened (${name}).`;
  return `${what} unavailable: ${why}${camOnly ? " The other person can still hear you." : " The other person can't see or hear you."}`;
}

export default function CallPage({ role }) {
  const { callId } = useParams();
  const [params] = useSearchParams();
  const joinToken = params.get("t") || "";
  const nav = useNavigate();
  const [call, setCall] = useState(null);
  const [brief, setBrief] = useState(null);
  const [briefFresh, setBriefFresh] = useState(false);
  const [showBrief, setShowBrief] = useState(role === "patient");
  const [peerHere, setPeerHere] = useState(false);
  const [connState, setConnState] = useState("new");
  const [captions, setCaptions] = useState([]);
  const [interim, setInterim] = useState("");
  const [offRecord, setOffRecord] = useState(false);
  const [muted, setMuted] = useState(false);
  const [camOn, setCamOn] = useState(true);
  const [ended, setEnded] = useState(false);
  const [recap, setRecap] = useState(null);
  const [typeOpen, setTypeOpen] = useState(!speechSupported);
  const [typed, setTyped] = useState("");
  const [sttError, setSttError] = useState(null);
  const [hasLocalVideo, setHasLocalVideo] = useState(false);
  const [hasRemote, setHasRemote] = useState(false);
  const [mediaError, setMediaError] = useState(null);

  const localVideo = useRef(null);
  const remoteVideo = useRef(null);
  const peer = useRef(null);
  const sock = useRef(null);
  const stt = useRef(null);
  const offRef = useRef(false);
  const spokeBrief = useRef(false);

  const other = role === "patient" ? call?.contact : call?.patient;
  const isPatient = role === "patient";

  // ---- setup: call info, media, signaling, transcription
  useEffect(() => {
    let disposed = false;
    if (isPatient) api.get(`/calls/${callId}`).then((c) => !disposed && setCall(c));
    else api.get(`/calls/${callId}/receiver?t=${encodeURIComponent(joinToken)}`)
      .then((r) => !disposed && setCall({ ...r.call, contact: r.contact, patient: r.patient }));
    if (isPatient) {
      api.get(`/calls/${callId}/brief`).then((b) => {
        if (disposed) return;
        setBrief(b);
        if (!spokeBrief.current) {
          spokeBrief.current = true;
          speak(b.card.speech);
        }
      });
    }

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

    (async () => {
      const stream = await p.startMedia();
      if (disposed) return p.close();
      if (stream && localVideo.current) localVideo.current.srcObject = stream;
      setHasLocalVideo(!!stream?.getVideoTracks().length);
      setMediaError(p.mediaError);

      sock.current = openSocket(`/ws/call/${callId}?role=${role}`, async (msg) => {
        switch (msg.type) {
          case "welcome":
            setOffRecord(msg.off_record);
            offRef.current = msg.off_record;
            if (msg.status === "ended" || msg.status === "processing" || msg.status === "processed") setEnded(true);
            if (msg.peers.length) {
              setPeerHere(true);
              if (isPatient) p.call();
            }
            break;
          case "peer_joined":
            setPeerHere(true);
            if (isPatient) p.call();
            break;
          case "peer_left":
            setPeerHere(false);
            setHasRemote(false);
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
          case "brief":
            setBrief(msg.brief);
            setBriefFresh(true);
            setTimeout(() => setBriefFresh(false), 6000);
            break;
          case "hangup":
            setEnded(true);
            break;
          case "processed":
            if (msg.recap) setRecap(msg.recap);
            break;
        }
      }, undefined, async () => (isPatient
        ? { type: "auth", token: await deviceIdentity.getToken() }
        : { type: "auth", join_token: joinToken }));

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

  // ---- after the call: stop media, wait for the memory agent's recap
  useEffect(() => {
    if (!ended) return;
    stt.current?.stop();
    peer.current?.close();
    if (!isPatient) return;
    let stop = false;
    (async function poll() {
      for (let i = 0; i < 60 && !stop; i++) {
        const c = await api.get(`/calls/${callId}`);
        if (c.status === "processed") {
          const text = c.recap || `You just talked to ${c.contact.name}.`;
          setRecap(text);
          speak(text);
          return;
        }
        await new Promise((r) => setTimeout(r, 1500));
      }
    })();
    return () => { stop = true; };
  }, [ended]);

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

  function toggleOffRecord() {
    sock.current?.send({ type: "off_record", value: !offRef.current });
  }

  async function hangUp() {
    await api.post(`/calls/${callId}/end`, isPatient ? {} : { t: joinToken });
    setEnded(true);
  }

  async function sendTyped(e) {
    e.preventDefault();
    if (!typed.trim()) return;
    sock.current?.send({ type: "transcript", text: typed.trim() });
    setTyped("");
  }

  async function feedback(ids, helpful) {
    await Promise.all(ids.map((id) => api.post(`/memories/${id}/feedback`, { helpful, by: role })));
  }

  // ---------------------------------------------------------------- after-call screens
  if (ended) {
    if (!isPatient) {
      return (
        <div className="call-ended">
          <h2>Call ended</h2>
          <p>Thank you for calling {call?.patient?.name}. What you shared will help them remember you next time.</p>
        </div>
      );
    }
    return (
      <div className="call-ended patient">
        {call && <Avatar contact={call.contact} size={180} />}
        {recap ? <p className="recap">{recap}</p> : <p className="recap muted">Saving what you talked about…</p>}
        <button className="btn-big btn-primary" onClick={() => { stopSpeaking(); nav("/patient"); }}>
          Back home
        </button>
      </div>
    );
  }

  const name = other?.name || "…";
  return (
    <div className={`call ${isPatient ? "as-patient" : "as-contact"}`}>
      <div className="stage">
        <video ref={remoteVideo} className={`remote ${hasRemote ? "" : "hidden"}`} autoPlay playsInline />
        {!hasRemote && (
          <div className="waiting">
            {other && <Avatar contact={isPatient ? call.contact : { name, color: "#6b5a49" }} size={200} />}
            <div className="waiting-text">
              {peerHere ? `Connecting to ${name}…` : isPatient ? `Calling ${name}…` : `Waiting for ${name}…`}
            </div>
            {isPatient && call && !peerHere && (
              <div className="invite">
                Invite link for {call.contact.name}: <code>{call.join_url}</code>
                <button className="btn-link" onClick={() => navigator.clipboard?.writeText(call.join_url)}>Copy</button>
              </div>
            )}
          </div>
        )}
        <video ref={localVideo} className={`local ${hasLocalVideo && camOn ? "" : "hidden"}`} autoPlay playsInline muted />

        {isPatient && call && (
          <div className="name-tag">
            <span className="nt-name">{call.contact.name}</span>
            <span className="nt-rel">your {call.contact.relation}</span>
          </div>
        )}
        {!isPatient && call && (
          <div className="name-tag">
            <span className="nt-name">{call.patient.name}</span>
            <span className="nt-rel">{offRecord ? "Off the record — not transcribed" : "Transcribed to help them remember"}</span>
          </div>
        )}

        {isPatient && showBrief && brief && (
          <div className="overlay" role="dialog" aria-label="Who is this?">
            {briefFresh && <div className="fresh">Updated just now</div>}
            <BriefCard card={brief.card} onFeedback={feedback} onRead={() => speak(brief.card.speech)} />
            <button className="btn-big btn-calm close-overlay" onClick={() => { stopSpeaking(); setShowBrief(false); }}>
              Close
            </button>
          </div>
        )}

        {mediaError && <div className="media-error" role="alert">{mediaMessage(mediaError)}</div>}

        <div className="captions" aria-live="polite">
          {offRecord && <div className="off-banner">Off the record — nothing is being saved</div>}
          {captions.slice(-3).map((c, i) => (
            <div key={i} className={`cap ${c.speaker === role ? "me" : ""} ${c.off_record ? "off" : ""}`}>
              <b>{c.speaker === role ? "You" : name}:</b> {c.text}
            </div>
          ))}
          {interim && <div className="cap interim"><b>{name}:</b> {interim}…</div>}
        </div>
      </div>

      <div className="controls">
        <button className={`ctl ${muted ? "on" : ""}`} onClick={toggleMute}>{muted ? "Unmute" : "Mute"}</button>
        <button className={`ctl ${camOn ? "" : "on"}`} onClick={toggleCam}>{camOn ? "Camera off" : "Camera on"}</button>
        {!isPatient && (
          <button className={`ctl ${offRecord ? "on warn" : ""}`} onClick={toggleOffRecord}>
            {offRecord ? "Back on the record" : "Go off the record"}
          </button>
        )}
        <button className="ctl" onClick={() => setTypeOpen((v) => !v)}>Type instead</button>
        <button className="ctl hang" onClick={hangUp}>End call</button>
        {isPatient && (
          <button className="who-btn" onClick={() => { setShowBrief(true); brief && speak(brief.card.speech); }}>
            Who is this?
          </button>
        )}
      </div>

      {typeOpen && (
        <form className="type-box" onSubmit={sendTyped}>
          <input value={typed} onChange={(e) => setTyped(e.target.value)}
            placeholder={`Type what you said (as ${isPatient ? call?.patient?.name || "patient" : name === "…" ? "contact" : "yourself"})`} />
          <button className="btn">Send</button>
          {sttError && <span className="stt-err">Speech recognition: {sttError}</span>}
        </form>
      )}
      <div className="conn-state">{connState !== "new" ? connState : ""}</div>
    </div>
  );
}
