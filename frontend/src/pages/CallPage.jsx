import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, openSocket } from "../lib/api.js";
import { deviceIdentity } from "../lib/identity.js";
import { cardSentences, phrases } from "../lib/i18n.js";
import { Peer } from "../lib/rtc.js";
import { createTranscriber, speechSupported } from "../lib/speech.js";
import { onSpeaking, speak, stopSpeaking } from "../lib/tts.js";
import { Avatar } from "../components/common.jsx";
import { RecallCard, RelativePanel, WhoCard } from "../components/CallCards.jsx";

const CARD_MS = 20000; // the full card stays up this long, then collapses to the name tag
const SPEAK_DELAY_MS = 2000; // don't talk over the first "hello"
const QUIET_TAIL_MS = 1000; // recognition stays off this long after read-aloud ends
const RECALL_MS = 10000;
const OVER = ["ended", "processing", "processed"];
const NOT_ANSWERED = ["unanswered", "declined"];

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
  const back = params.get("back");
  const nav = useNavigate();
  const isPatient = role === "patient";

  const [call, setCall] = useState(null);
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
  const [videoPlaying, setVideoPlaying] = useState(false);
  const [mediaError, setMediaError] = useState(null);
  // patient side: the "Who is this?" card
  const [lang, setLang] = useState("en-IN");
  const [who, setWho] = useState(undefined); // name tag from the contact alone (undefined = loading)
  const [card, setCard] = useState(null); // patient_card from the brief (adds the memory line)
  const [alsoHere, setAlsoHere] = useState([]);
  const [expanded, setExpanded] = useState(false);
  const [fresh, setFresh] = useState(false);
  const [recall, setRecall] = useState(null);
  const [noAnswer, setNoAnswer] = useState(null);
  const [accepted, setAccepted] = useState(false);
  // relative side
  const [relView, setRelView] = useState(null);
  const [others, setOthers] = useState([]);
  const [alsoIds, setAlsoIds] = useState([]);
  const [panelOpen, setPanelOpen] = useState(true);

  const localVideo = useRef(null);
  const remoteVideo = useRef(null);
  const peer = useRef(null);
  const sock = useRef(null);
  const stt = useRef(null);
  const offRef = useRef(false);
  const mutedRef = useRef(false);
  const endedRef = useRef(false);
  const shownOnce = useRef(false);
  const timers = useRef({});
  const viewRef = useRef(null);
  const langRef = useRef("en-IN");
  const quietUntil = useRef(0);
  const speakingRef = useRef(false);

  const other = isPatient ? call?.contact : call?.patient;
  const familyCall = !!call?.family; // "Join the family call": rings relatives, the first to pick up is the call
  const familyUnanswered = familyCall && !call?.contact;
  const p = phrases(lang);
  const remoteLive = hasRemote && (videoPlaying || connState === "connected");

  // What the patient sees and hears. A brief that says "no one" (contact gone) wins over the tag.
  const tag = card ? card.who : who;
  const view = useMemo(() => tag && {
    name: tag.name, fullName: tag.full_name, relation: tag.relation, detail: tag.detail, photo: tag.photo,
    color: tag.color, memory: card?.memory?.text || null, alsoHere,
  }, [tag, card, alsoHere]);
  viewRef.current = view;
  langRef.current = lang;

  function later(key, fn, ms) {
    clearTimeout(timers.current[key]);
    timers.current[key] = setTimeout(fn, ms);
  }

  function speakCard() {
    const text = cardSentences(viewRef.current, langRef.current).join(" ");
    if (text) speak(text, { lang: langRef.current });
  }

  /** Expand the card (20 s), read it, and record why it opened: shown | button | voice. */
  function openCard(kind) {
    if (!viewRef.current) return;
    setExpanded(true);
    later("collapse", () => setExpanded(false), CARD_MS);
    if (kind === "shown") later("speak", speakCard, SPEAK_DELAY_MS);
    else speakCard();
    if (kind !== "voice") sock.current?.send({ type: "overlay", event: kind }); // voice is counted by the server
  }

  // The memory line arrives with the brief; until then the name tag stands alone. Never an error.
  const alive = useRef(true);
  function loadBrief() {
    api.get(`/calls/${callId}/brief`).then((b) => {
      if (!alive.current) return;
      setCard(b.patient_card?.who ? b.patient_card : null);
      if (b.patient_card?.also_here) setAlsoHere(b.patient_card.also_here);
    }).catch(() => {});
  }

  // ---- setup: call info, media, signaling, transcription
  useEffect(() => {
    let disposed = false;
    alive.current = true;
    if (isPatient) {
      api.get(`/calls/${callId}`).then((c) => {
        if (disposed) return;
        setCall(c);
        setWho(c.who || null);
        setAlsoHere(c.also_here_people || []);
        setLang(c.language || "en-IN");
        if (NOT_ANSWERED.includes(c.status)) setNoAnswer(c.status);
        else if (c.status === "cancelled") nav("/patient", { replace: true });
      });
      loadBrief();
    } else {
      api.get(`/calls/${callId}/receiver?t=${encodeURIComponent(joinToken)}`).then((r) => {
        if (disposed) return;
        setCall({ ...r.call, contact: r.contact, patient: r.patient });
        setRelView(r.view);
        setOthers(r.others || []);
        setAlsoIds(r.also_here || []);
      });
    }

    const pc = new Peer({
      role,
      send: (data) => sock.current?.send({ type: "signal", data }),
      onRemoteStream: (stream) => {
        if (remoteVideo.current) remoteVideo.current.srcObject = stream;
        setHasRemote(true);
      },
      onState: setConnState,
    });
    peer.current = pc;

    (async () => {
      const stream = await pc.startMedia();
      if (disposed) return pc.close();
      if (stream && localVideo.current) localVideo.current.srcObject = stream;
      setHasLocalVideo(!!stream?.getVideoTracks().length);
      setMediaError(pc.mediaError);

      sock.current = openSocket(`/ws/call/${callId}?role=${role}`, async (msg) => {
        switch (msg.type) {
          case "welcome":
            setOffRecord(msg.off_record);
            offRef.current = msg.off_record;
            if (OVER.includes(msg.status) || (!isPatient && [...NOT_ANSWERED, "cancelled"].includes(msg.status))) setEnded(true);
            if (msg.peers.length) {
              setPeerHere(true);
              if (isPatient) pc.call();
            }
            break;
          case "peer_joined":
            setPeerHere(true);
            if (isPatient) pc.call();
            break;
          case "peer_left":
            setPeerHere(false);
            setHasRemote(false);
            setVideoPlaying(false);
            pc.dropRemote();
            break;
          case "signal":
            await pc.handle(msg.data);
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
          case "brief": // a caregiver edit rebuilt the brief: update silently, no re-read, no re-expand
            setCard(msg.brief.patient_card);
            if (msg.brief.patient_card?.also_here) setAlsoHere(msg.brief.patient_card.also_here);
            setFresh(true);
            later("fresh", () => setFresh(false), 6000);
            break;
          case "relative_brief":
            setRelView(msg.view);
            break;
          case "who": // the patient asked "who is this?" out loud
            openCard("voice");
            break;
          case "also_here":
            if (isPatient) setAlsoHere(msg.people);
            else setAlsoIds(msg.ids);
            break;
          case "recall":
            setRecall(msg.card);
            later("recall", () => setRecall(null), RECALL_MS);
            break;
          case "accepted":
            setAccepted(true);
            break;
          case "claimed": // a family call: a relative picked up, so now there is someone to name
            setWho(msg.who || null);
            setCall((c) => c && { ...c, contact: msg.contact, who: msg.who });
            setAccepted(true);
            loadBrief();
            break;
          case "no_answer":
            setNoAnswer(msg.reason);
            if (msg.family) setCall((c) => c && { ...c, family: true });
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
        lang: isPatient ? langRef.current : "en-IN",
        // Anything recognised while the screen reads aloud (plus a short tail) is dropped.
        onFinal: (text) => text && Date.now() >= quietUntil.current && sock.current?.send({ type: "transcript", text }),
        onInterim: (text) => text && Date.now() >= quietUntil.current && sock.current?.send({ type: "interim", text }),
        onError: setSttError,
      });
      stt.current = t;
      t?.start();
    })();

    return () => {
      disposed = true;
      alive.current = false;
      Object.values(timers.current).forEach(clearTimeout);
      stt.current?.stop();
      sock.current?.close();
      pc.close();
      stopSpeaking();
    };
  }, [callId, role]);

  // Recognition follows the patient's language once it is known.
  useEffect(() => {
    if (!isPatient || !stt.current || lang === "en-IN") return;
    stt.current.pause();
    const t = createTranscriber({
      lang,
      onFinal: (text) => text && Date.now() >= quietUntil.current && sock.current?.send({ type: "transcript", text }),
      onInterim: (text) => text && Date.now() >= quietUntil.current && sock.current?.send({ type: "interim", text }),
      onError: setSttError,
    });
    stt.current = t;
    if (!mutedRef.current && !endedRef.current && !speakingRef.current) t?.start();
  }, [lang, !!stt.current]);

  // Keep the read-aloud out of the transcript: pause recognition while speaking, resume 1 s after.
  useEffect(() => {
    if (!isPatient) return;
    return onSpeaking((on, text) => {
      if (on) {
        quietUntil.current = Infinity;
        if (!speakingRef.current) stt.current?.pause();
        speakingRef.current = true;
        sock.current?.send({ type: "tts", speaking: true, text });
      } else if (speakingRef.current) {
        speakingRef.current = false;
        quietUntil.current = Date.now() + QUIET_TAIL_MS;
        sock.current?.send({ type: "tts", speaking: false });
        later("resume", () => !mutedRef.current && !endedRef.current && stt.current?.start(), QUIET_TAIL_MS);
      }
    });
  }, []);

  // The full card shows once the caller's video is actually live, never over a black screen.
  useEffect(() => {
    if (isPatient && remoteLive && view && !shownOnce.current) {
      shownOnce.current = true;
      openCard("shown");
    }
  }, [remoteLive, !!view]);

  // ---- after the call: stop media, wait for the memory agent's recap
  useEffect(() => {
    if (!ended && !noAnswer) return;
    endedRef.current = true;
    stt.current?.stop();
    peer.current?.close();
    if (!isPatient) return;
    if (noAnswer) {
      speak(familyUnanswered ? p.familyBusy : p.busy(call?.who?.name || call?.contact?.name || ""), { lang });
      return;
    }
    let stop = false;
    (async function poll() {
      for (let i = 0; i < 60 && !stop; i++) {
        const c = await api.get(`/calls/${callId}`);
        if (c.status === "processed") {
          const text = c.recap || `You just talked to ${c.contact.name}.`;
          setRecap(text);
          speak(text, { lang });
          return;
        }
        await new Promise((r) => setTimeout(r, 1500));
      }
    })();
    return () => { stop = true; };
  }, [ended, noAnswer]);

  function toggleMute() {
    const m = !muted;
    setMuted(m);
    mutedRef.current = m;
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
    const res = await api.post(`/calls/${callId}/end`, isPatient ? {} : { t: joinToken });
    if (isPatient && res.status === "cancelled") {
      stopSpeaking();
      nav("/patient");
      return;
    }
    setEnded(true);
  }

  async function sendTyped(e) {
    e.preventDefault();
    if (!typed.trim()) return;
    sock.current?.send({ type: "transcript", text: typed.trim() });
    setTyped("");
  }

  function changeAlsoHere(ids) {
    setAlsoIds(ids);
    sock.current?.send({ type: "also_here", ids });
  }

  async function feedback(id, helpful) {
    await api.post(`/memories/${id}/feedback`, { helpful, call_id: callId, t: joinToken });
  }

  // ---------------------------------------------------------------- after-call screens
  const name = isPatient ? who?.name || other?.name || "…" : other?.name || "…";
  if (isPatient && noAnswer) {
    return (
      <div className="call-ended patient">
        {call && <Avatar contact={call.contact} size={180} />}
        <p className="recap">{familyUnanswered ? p.familyBusy : p.busy(name)}</p>
        <button className="btn-big btn-primary" onClick={() => { stopSpeaking(); nav("/patient"); }}>{p.backHome}</button>
      </div>
    );
  }
  if (ended) {
    if (!isPatient) {
      return (
        <div className="call-ended">
          <h2>Call ended</h2>
          <p>Thank you for calling {call?.patient?.name}. What you shared will help them remember you next time.</p>
          {back && <a className="btn" href={back}>Back to {call?.patient?.name}'s page</a>}
        </div>
      );
    }
    return (
      <div className="call-ended patient">
        {call && <Avatar contact={call.contact} size={180} />}
        {recap ? <p className="recap">{recap}</p> : <p className="recap muted">Saving what you talked about…</p>}
        <button className="btn-big btn-primary" onClick={() => { stopSpeaking(); nav("/patient"); }}>
          {p.backHome}
        </button>
      </div>
    );
  }

  return (
    <div className={`call ${isPatient ? "as-patient" : "as-contact"} ${!isPatient && panelOpen ? "with-panel" : ""}`}>
      <div className="call-main">
        <div className="stage">
          <video ref={remoteVideo} className={`remote ${hasRemote ? "" : "hidden"}`} autoPlay playsInline
            onPlaying={() => setVideoPlaying(true)} />
          {!remoteLive && (
            <div className="waiting">
              {other && <Avatar contact={isPatient ? call.contact : { name, color: "#6b5a49" }} size={200} />}
              {isPatient && familyCall && !other && (
                <div className="avatar family-avatar" style={{ width: 200, height: 200 }} aria-hidden>
                  <svg viewBox="0 0 24 24" width="104" height="104" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
                    <circle cx="9" cy="8" r="3" /><circle cx="17" cy="9" r="2.4" />
                    <path d="M3.5 19c.6-3 2.8-4.6 5.5-4.6s4.9 1.6 5.5 4.6M14.5 15.2c.8-.5 1.6-.7 2.5-.7 2 0 3.6 1.2 4 3.5" />
                  </svg>
                </div>
              )}
              <div className="waiting-text">
                {isPatient && familyCall && !other ? p.callingFamily
                  : peerHere ? `Connecting to ${name}…` : isPatient ? (accepted ? p.joining(name) : `Calling ${name}…`) : `Waiting for ${name}…`}
              </div>
              {isPatient && call?.contact && call.join_url && !peerHere && !accepted && (
                <div className="invite">
                  Or send {call.contact.name} this link: <code>{call.join_url}</code>
                  <button className="btn-link" onClick={() => navigator.clipboard?.writeText(call.join_url)}>Copy</button>
                </div>
              )}
            </div>
          )}
          <video ref={localVideo} className={`local ${hasLocalVideo && camOn ? "" : "hidden"}`} autoPlay playsInline muted />

          {!isPatient && call && (
            <div className="name-tag">
              <span className="nt-name">{call.patient.name}</span>
              <span className="nt-rel">{offRecord ? "Off the record — not transcribed" : "Transcribed to help them remember"}</span>
            </div>
          )}
          {isPatient && remoteLive && <RecallCard card={recall} />}

          {mediaError && <div className="media-error" role="alert">{mediaMessage(mediaError)}</div>}

          <div className="dock">
            <div className="captions" aria-live="polite">
              {offRecord && <div className="off-banner">Off the record — nothing is being saved</div>}
              {captions.slice(-3).map((c, i) => (
                <div key={i} className={`cap ${c.speaker === role ? "me" : ""} ${c.off_record ? "off" : ""}`}>
                  <b>{c.speaker === role ? "You" : name}:</b> {c.text}
                </div>
              ))}
              {interim && <div className="cap interim"><b>{name}:</b> {interim}…</div>}
            </div>
            {isPatient && remoteLive && view && (
              <WhoCard view={view} lang={lang} expanded={expanded} fresh={fresh} onCollapse={() => setExpanded(false)} />
            )}
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
          {!isPatient && !panelOpen && <button className="ctl" onClick={() => setPanelOpen(true)}>Notes for you</button>}
          <button className="ctl" onClick={() => setTypeOpen((v) => !v)}>Type instead</button>
          <button className="ctl hang" onClick={hangUp}>End call</button>
          {isPatient && remoteLive && view && (
            <button className="who-btn" onClick={() => openCard("button")}>{p.whoBtn}</button>
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

      {!isPatient && panelOpen && call && (
        <RelativePanel view={relView} patientName={call.patient.name}
          others={others} alsoHere={alsoIds} onAlsoHere={changeAlsoHere} onClose={() => setPanelOpen(false)}
          onFeedback={feedback} />
      )}
    </div>
  );
}
