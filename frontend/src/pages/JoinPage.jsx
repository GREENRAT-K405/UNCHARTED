import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api, setIdentity } from "../lib/api.js";
import { speechSupported } from "../lib/speech.js";
import CallPage from "./CallPage.jsx";

/** Receiver side: zero install, explicit consent before anything is transcribed. */
export default function JoinPage() {
  const { callId } = useParams();
  const [params] = useSearchParams();
  setIdentity(null); // the family member joining has no account; the link's token is their pass
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);
  const [joined, setJoined] = useState(false);

  useEffect(() => {
    api.get(`/calls/${callId}/receiver?t=${encodeURIComponent(params.get("t") || "")}`)
      .then(setInfo).catch((e) => setError(e.message));
  }, [callId]);

  if (joined) return <CallPage role="contact" />;
  if (error) return <div className="join"><div className="join-card"><h2>This call link isn't valid</h2><p>{error}</p></div></div>;
  if (!info) return <div className="join"><div className="join-card">Loading…</div></div>;

  const { patient, contact, call, notes } = info;
  const over = ["ended", "processing", "processed"].includes(call.status);
  return (
    <div className="join">
      <div className="join-card">
        <div className="join-kicker">Hi {contact.name}</div>
        <h1>{patient.name} is calling you</h1>
        <div className="consent">
          <strong>This call is transcribed to help {patient.name} remember.</strong>
          <ul>
            <li>Only text is kept. Audio and video are never stored, and raw transcripts are deleted after 30 days.</li>
            <li>After the call, a memory assistant saves a few short notes (plans, news) that {patient.name} sees next time.</li>
            <li>You can go <b>off the record</b> at any time; nothing said then is saved.</li>
            <li>To keep something from {patient.name}, say "don't tell {patient.name} about …" and it stays with the family only.</li>
          </ul>
        </div>
        {notes.length > 0 && (
          <div className="receiver-notes">
            <div className="rn-title">Good to know before you talk</div>
            {notes.map((n, i) => <div key={i}>{n}</div>)}
          </div>
        )}
        {!speechSupported && (
          <p className="fine">Live transcription needs Chrome. You can still type what you said during the call.</p>
        )}
        <button className="btn-big btn-primary" disabled={over} onClick={() => setJoined(true)}>
          {over ? "This call has ended" : "I agree, join the call"}
        </button>
      </div>
    </div>
  );
}
