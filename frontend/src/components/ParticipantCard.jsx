import { Avatar } from "./common.jsx";

function relationText(person) {
  if (!person) return "";
  if (person.kind === "guest") return person.first_meeting ? "First time meeting" : "Someone you have met before";
  const r = (person.relation || "").trim();
  return r ? `your ${r}` : "";
}

function lastMetText(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  const days = Math.floor((Date.now() - d.getTime()) / 86400000);
  const when = d.toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" });
  const ago = days <= 0 ? "today" : days === 1 ? "yesterday" : `${days} days ago`;
  return `${when} (${ago})`;
}

/** What the patient reads aloud to themselves: one or two short sentences. */
export function speechFor(person) {
  if (!person) return "";
  if (person.kind === "guest" && person.first_meeting) {
    return `This is someone new${person.name && person.name !== "New person" ? `, ${person.name}` : ""}. It is your first time meeting.`;
  }
  let s = `This is ${person.name}, ${relationText(person)}.`;
  if (person.last_meet_at) {
    const day = new Date(person.last_meet_at).toLocaleDateString("en-IN", { weekday: "long" });
    s += ` You last met on ${day}.`;
    if (person.last_summary) s += ` You talked about: ${person.last_summary}`;
  }
  return s;
}

/**
 * The popup at the top of the patient's call screen.
 * view: {phase, person, candidate, agrees, claimed, confidence}
 */
export default function ParticipantCard({ view, suggestion, collapsed, onToggle, onRead }) {
  const { phase, person } = view;

  if (!person) {
    let title = "Looking at who joined…";
    let sub = "Their name will appear here.";
    if (phase === "waiting") {
      title = "Waiting for someone to join";
      sub = "Share the code or link below.";
    } else if (phase === "noface") {
      title = "I can't see their face yet";
      sub = "Ask them to look at the camera.";
    } else if (phase === "unsure" && view.candidate) {
      title = "I'm not sure who this is";
      sub = `They may be ${view.candidate.name}${view.candidate.relation ? `, your ${view.candidate.relation}` : ""}.`;
    } else if (phase === "unknown") {
      title = "Someone I don't know yet";
      sub = "Your family will check who this is.";
    }
    return (
      <div className={`pcard pcard-waiting ${phase === "unsure" ? "pcard-unsure" : ""}`} role="status" aria-live="polite">
        {phase === "unsure" && view.candidate?.photo ? (
          <img className="pcard-photo small" src={view.candidate.photo} alt="" />
        ) : (
          <span className={`pcard-dot ${phase === "waiting" ? "" : "pulse"}`} aria-hidden />
        )}
        <div>
          <div className="pcard-title">{title}</div>
          <div className="pcard-sub">{sub}</div>
        </div>
      </div>
    );
  }

  const guest = person.kind === "guest";
  const last = lastMetText(person.last_meet_at);
  if (collapsed) {
    return (
      <button className="pcard pcard-mini" onClick={onToggle} aria-label="Show who this is">
        <Avatar contact={{ name: person.name, photo: person.photo, color: guest ? "#8a8f98" : "#1f5c4f" }} size={44} />
        <span><b>{person.name}</b> {relationText(person)}</span>
      </button>
    );
  }
  return (
    <div className={`pcard ${guest ? "pcard-guest" : ""}`} role="status" aria-live="polite">
      <div className="pcard-head">
        <Avatar contact={{ name: person.name, photo: person.photo, color: guest ? "#8a8f98" : "#1f5c4f" }} size={96} />
        <div className="pcard-who">
          <div className="pcard-name">{person.name}</div>
          <div className="pcard-rel">{relationText(person)}</div>
          {phase === "matched" && view.confidence != null && (
            <div className="pcard-match">Recognised by face · {view.confidence}% sure</div>
          )}
        </div>
        <button className="pcard-hide" onClick={onToggle} aria-label="Make smaller">−</button>
      </div>

      {view.agrees === false && (
        <div className="pcard-warn">
          {view.claimed ? `Signed in as ${view.claimed.name}, but the face looks like ${person.name}.` : `The face doesn't match ${person.name}'s photo.`}
        </div>
      )}

      {guest && person.first_meeting ? (
        <div className="pcard-row">This is your first time meeting. Their photo is saved so your family can check who they are.</div>
      ) : last ? (
        <>
          <div className="pcard-row"><span className="pcard-label">Last met</span>{last}{person.last_title ? ` · ${person.last_title}` : ""}</div>
          {person.last_summary && <div className="pcard-row"><span className="pcard-label">You talked about</span>{person.last_summary}</div>}
          {person.last_note && <div className="pcard-row note"><span className="pcard-label">Family note</span>{person.last_note}</div>}
        </>
      ) : (
        <div className="pcard-row">You haven't had a video meet with {person.name} in this app yet.</div>
      )}
      {suggestion && <div className="pcard-row ask"><span className="pcard-label">You could ask</span>“{suggestion}”</div>}
      {onRead && <button className="btn-ghost pcard-read" onClick={onRead}>🔊 Read it to me</button>}
    </div>
  );
}
