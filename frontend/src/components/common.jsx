import { useEffect, useState } from "react";
import { api } from "../lib/api.js";

export function GoogleMark({ size = 20 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden>
      <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9 3.5l6.7-6.7C35.6 2.4 30.2 0 24 0 14.6 0 6.6 5.4 2.7 13.3l7.8 6C12.4 13.7 17.7 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.1 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.4c-.5 2.9-2.2 5.3-4.6 6.9l7.4 5.7c4.3-4 6.9-9.9 6.9-17.1z" />
      <path fill="#FBBC05" d="M10.5 28.7c-.5-1.4-.8-3-.8-4.7s.3-3.2.8-4.7l-7.8-6C1 16.6 0 20.2 0 24s1 7.4 2.7 10.7l7.8-6z" />
      <path fill="#34A853" d="M24 48c6.2 0 11.4-2 15.2-5.5l-7.4-5.7c-2.1 1.4-4.7 2.2-7.8 2.2-6.3 0-11.6-4.2-13.5-9.9l-7.8 6C6.6 42.6 14.6 48 24 48z" />
    </svg>
  );
}

export function Avatar({ contact, size = 160, className = "" }) {
  const initials = (contact?.name || "?")
    .split(" ")
    .map((p) => p[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
  const style = { width: size, height: size, fontSize: size * 0.38, background: contact?.color || "#55606e" };
  if (contact?.photo) {
    return <img className={`avatar ${className}`} style={style} src={contact.photo} alt="" />;
  }
  return (
    <div className={`avatar ${className}`} style={style} aria-hidden>
      {initials}
    </div>
  );
}

const ICONS = {
  person: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-7 8c.8-3.6 3.6-5.5 7-5.5s6.2 1.9 7 5.5",
  place: "M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0C18.5 15.4 12 21 12 21Zm0-8.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z",
  calendar: "M4.5 6.5h15v13h-15zM4.5 10.5h15M8.5 4v4M15.5 4v4",
  chat: "M4.5 5.5h15v10h-8l-4.5 3.5v-3.5h-2.5z",
  heart: "M12 19.5s-7.5-4.6-7.5-9.7A4.3 4.3 0 0 1 12 7.4a4.3 4.3 0 0 1 7.5 2.4c0 5.1-7.5 9.7-7.5 9.7Z",
  star: "m12 4 2.4 5 5.4.6-4 3.7 1.1 5.3L12 16l-4.9 2.6 1.1-5.3-4-3.7 5.4-.6z",
};

export function Icon({ name, size = 28 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={ICONS[name] || ICONS.star} />
    </svg>
  );
}

/** The "Who is this?" card, shared by the patient overlay and the caregiver preview. */
export function BriefCard({ card, compact = false, onFeedback, onRead }) {
  if (!card) return null;
  return (
    <div className={`brief-card ${compact ? "compact" : ""}`}>
      <div className="brief-headline">{card.headline}</div>
      <ul className="brief-lines">
        {card.lines.map((l, i) => (
          <li key={i}>
            <span className="brief-icon">
              <Icon name={l.icon} size={compact ? 18 : 30} />
            </span>
            <span className="brief-text">{l.text}</span>
            {onFeedback && l.memory_ids?.length > 0 && (
              <span className="thumbs">
                <button aria-label="Helpful" onClick={() => onFeedback(l.memory_ids, true)}>👍</button>
                <button aria-label="Not helpful" onClick={() => onFeedback(l.memory_ids, false)}>👎</button>
              </span>
            )}
          </li>
        ))}
      </ul>
      {card.suggestions?.length > 0 && (
        <div className="brief-suggest">
          <div className="brief-suggest-label">You could ask</div>
          {card.suggestions.map((s, i) => (
            <div key={i} className="brief-suggest-item">“{s.replace(/^["“]|["”]$/g, "")}”</div>
          ))}
        </div>
      )}
      {onRead && (
        <button className="btn-ghost read-again" onClick={onRead}>
          🔊 Read it to me again
        </button>
      )}
    </div>
  );
}

export function Chip({ children, tone = "neutral", title }) {
  return (
    <span className={`chip chip-${tone}`} title={title}>
      {children}
    </span>
  );
}

const TONES = {
  active: "good", pending_review: "warn", superseded: "muted", rejected: "bad",
  patient: "good", caregiver_only: "info", never_surface: "bad",
  ADD: "good", UPDATE: "info", SUPERSEDE: "accent", IGNORE: "muted", CONFLICT: "warn", ERROR: "bad",
  insert: "good", update: "info", delete: "bad", replace: "info",
};
export const tone = (v) => TONES[v] || "neutral";

export function useServerClock() {
  const [offsetMs, setOffsetMs] = useState(0);
  useEffect(() => {
    api.get("/clock").then((c) => setOffsetMs(new Date(c.now).getTime() - Date.now()));
  }, []);
  return [offsetMs, setOffsetMs];
}

export function relTime(iso, offsetMs = 0) {
  if (!iso) return "";
  const diff = (Date.now() + offsetMs - new Date(iso).getTime()) / 1000;
  const future = diff < 0;
  const s = Math.abs(diff);
  const fmt =
    s < 45 ? `${Math.round(s)}s` : s < 3600 ? `${Math.round(s / 60)}m` : s < 86400 ? `${Math.round(s / 3600)}h` : `${Math.round(s / 86400)}d`;
  return future ? `in ${fmt}` : `${fmt} ago`;
}

export function fmtDateTime(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleString("en-IN", { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

export function fmtDate(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
}
