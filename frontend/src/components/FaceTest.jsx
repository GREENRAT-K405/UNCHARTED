import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api.js";
import { describeFaces, MIN_FACE_PX } from "../lib/face.js";

/**
 * Caretaker tool: point the webcam at someone and see who the app thinks they are, exactly as it
 * would decide on a call (same server rule, 4 frames). Nothing is stored.
 */
export default function FaceTest({ patientId, onClose }) {
  const video = useRef(null);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [status, setStatus] = useState("Starting the camera…");

  useEffect(() => {
    let stream = null;
    let stop = false;
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 } });
      } catch {
        setError("The camera isn't available. Allow camera access and try again.");
        return;
      }
      if (video.current) video.current.srcObject = stream;
      let frames = [];
      while (!stop) {
        const v = video.current;
        if (v && v.readyState >= 2) {
          try {
            const f = (await describeFaces(v, { maxResults: 2 }))[0];
            if (f && f.box.width >= MIN_FACE_PX && f.score >= 0.75) {
              frames.push(f.descriptor);
              setStatus(`Collecting face frames… ${frames.length}/4`);
            } else setStatus("Look at the camera, closer and well lit.");
            if (frames.length >= 4) {
              const r = await api.post(`/patients/${patientId}/faces/test`, { descriptors: frames });
              if (!stop) setResult(r);
              frames = [];
            }
          } catch (e) {
            setStatus(e.message);
          }
        }
        await new Promise((r) => setTimeout(r, 500));
      }
    })();
    return () => {
      stop = true;
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, [patientId]);

  const label = {
    matched: "Recognised", unsure: "Not sure", unknown: "Not recognised", no_gallery: "No faces saved yet",
  }[result?.status];
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal cam-modal" onClick={(e) => e.stopPropagation()}>
        <div className="panel-title">Test face recognition</div>
        {error ? <p className="auth-error">{error}</p> : <video ref={video} autoPlay playsInline muted className="cam-video" />}
        <p className="muted small">{status}</p>
        {result && (
          <div className={`test-result ${result.status}`}>
            <b>{label}</b>
            {result.person && <span>{result.status === "matched" ? "" : "Closest: "}{result.person.name} ({result.person.relation})</span>}
            {result.distance != null && (
              <span className="muted small">
                distance {result.distance} · {result.confidence}% · {Math.round((result.frames_agree || 0) * 100)}% of frames agree
                {result.runner_up && ` · next closest ${result.runner_up.name} at ${result.runner_up.distance}`}
              </span>
            )}
          </div>
        )}
        <p className="muted small">A name is given at distance 0.45 or less and a clear lead over the next person. Up to 0.60 shows "not sure".</p>
        <button className="btn-ghost" onClick={onClose}>Close</button>
      </div>
    </div>
  );
}
