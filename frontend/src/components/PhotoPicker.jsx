import { useEffect, useRef, useState } from "react";
import { enrolFrame, enrolPhoto, loadFaceModels } from "../lib/face.js";

const dist = (a, b) => Math.hypot(...a.map((x, i) => x - b[i]));

/**
 * Pick face photos for a person: from files or the webcam. Each photo is checked in the browser
 * (one clear face, big enough) and turned into a face fingerprint before it is accepted.
 * value: [{id, photo, descriptor}]
 */
export default function PhotoPicker({ value, onChange, max = 6, existing = 0 }) {
  const [items, setItems] = useState([]); // in-progress and rejected uploads: {id, name, status, error}
  const [modelState, setModelState] = useState("idle");
  const [cam, setCam] = useState(false);
  const fileRef = useRef(null);
  const room = max - existing - value.length;

  useEffect(() => {
    setModelState("loading");
    loadFaceModels().then(() => setModelState("ready"), () => setModelState("error"));
  }, []);

  async function addFiles(files) {
    const list = Array.from(files).slice(0, Math.max(0, room));
    for (const file of list) {
      const id = Math.random().toString(36).slice(2);
      setItems((xs) => [...xs, { id, name: file.name, status: "checking" }]);
      const r = await enrolPhoto(file).catch((e) => ({ error: e.message }));
      if (r.error) setItems((xs) => xs.map((x) => (x.id === id ? { ...x, status: "rejected", error: r.error } : x)));
      else {
        setItems((xs) => xs.filter((x) => x.id !== id));
        onChange((v) => [...v, { id, photo: r.photo, descriptor: r.descriptor, warning: r.warning }]);
      }
    }
  }

  // Photos of one person should sit close together; a big spread usually means a wrong photo slipped in.
  let spreadWarning = null;
  for (let i = 0; i < value.length && !spreadWarning; i++)
    for (let j = i + 1; j < value.length; j++)
      if (dist(value[i].descriptor, value[j].descriptor) > 0.62) {
        spreadWarning = `Photos ${i + 1} and ${j + 1} may not be the same person. Remove the one that's wrong.`;
        break;
      }

  return (
    <div className="photo-picker">
      <div className="pp-grid">
        {value.map((p, i) => (
          <div key={p.id} className="pp-item ok">
            <img src={p.photo} alt={`Face photo ${i + 1}`} />
            <button type="button" className="pp-x" aria-label="Remove photo" onClick={() => onChange((v) => v.filter((x) => x.id !== p.id))}>×</button>
            <span className="pp-tag">✓ face {i + 1}</span>
          </div>
        ))}
        {items.map((x) => (
          <div key={x.id} className={`pp-item ${x.status}`}>
            <span className="pp-msg">{x.status === "checking" ? "Checking face…" : x.error}</span>
            {x.status === "rejected" && (
              <button type="button" className="pp-x" aria-label="Dismiss" onClick={() => setItems((xs) => xs.filter((y) => y.id !== x.id))}>×</button>
            )}
          </div>
        ))}
        {room > 0 && (
          <div className="pp-add">
            <button type="button" className="btn-ghost" disabled={modelState !== "ready"} onClick={() => fileRef.current?.click()}>
              + Upload photos
            </button>
            <button type="button" className="btn-ghost" disabled={modelState !== "ready"} onClick={() => setCam(true)}>
              Use camera
            </button>
          </div>
        )}
      </div>
      <input ref={fileRef} type="file" accept="image/*" multiple hidden onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
      <p className="muted small">
        {modelState === "loading" && "Loading face recognition (first time only, about 13 MB)… "}
        {modelState === "error" && "Face recognition couldn't load. Check your connection and reload. "}
        For the best recognition add 3 clear, front-facing photos in different light. Each photo must show one face.
      </p>
      {spreadWarning && <div className="auth-error">{spreadWarning}</div>}
      {cam && <CameraCapture onClose={() => setCam(false)} onPhoto={(r) => onChange((v) => [...v, { id: Math.random().toString(36).slice(2), ...r }])} />}
    </div>
  );
}

function CameraCapture({ onClose, onPhoto }) {
  const video = useRef(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  useEffect(() => {
    let stream = null;
    navigator.mediaDevices?.getUserMedia({ video: { width: 640, height: 480 } })
      .then((s) => { stream = s; if (video.current) video.current.srcObject = s; })
      .catch(() => setError("The camera isn't available. Allow camera access, or upload photos instead."));
    return () => stream?.getTracks().forEach((t) => t.stop());
  }, []);

  async function snap() {
    setBusy(true);
    setMsg(null);
    const r = await enrolFrame(video.current).catch((e) => ({ error: e.message }));
    setBusy(false);
    if (r.error) setMsg(r.error);
    else {
      onPhoto(r);
      setMsg("Saved. Turn your head slightly and take another, or close.");
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal cam-modal" onClick={(e) => e.stopPropagation()}>
        {error ? <p className="auth-error">{error}</p> : <video ref={video} autoPlay playsInline muted className="cam-video" />}
        {msg && <p className="muted">{msg}</p>}
        <div className="row">
          <button type="button" className="btn" disabled={busy || !!error} onClick={snap}>{busy ? "Checking…" : "Take photo"}</button>
          <button type="button" className="btn-ghost" onClick={onClose}>Done</button>
        </div>
      </div>
    </div>
  );
}
