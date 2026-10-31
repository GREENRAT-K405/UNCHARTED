import { useState } from "react";
import { Avatar, Icon } from "./common.jsx";
import { cardSentences, phrases, talkedWhen } from "../lib/i18n.js";

/** Patient side. Expanded: the full "Who is this?" card. Collapsed: a small name tag at the bottom
 * edge of the video. Both say exactly what is read aloud: who, relation, at most one memory. */
export function WhoCard({ view, lang, expanded, fresh, onCollapse }) {
  if (!view?.name) return null;
  const p = phrases(lang);
  const photo = { name: view.fullName || view.name, photo: view.photo, color: view.color };
  if (!expanded) {
    return (
      <div className="who-tag" aria-live="polite">
        <Avatar contact={photo} size={52} />
        <span className="who-tag-text">
          <b>{view.name}</b> <span>{view.relation}</span>
        </span>
        {fresh && <span className="who-fresh small-fresh">✓</span>}
      </div>
    );
  }
  const also = view.alsoHere?.length ? cardSentences({ ...view, memory: null }, lang).at(-1) : null;
  return (
    <div className="who-card" role="dialog" aria-label={p.whoBtn} onClick={onCollapse}>
      <Avatar contact={photo} size={96} />
      <div className="who-body">
        <div className="who-name">{view.name}</div>
        <div className="who-rel">{view.detail ? `${view.relation}, ${view.detail}` : view.relation}</div>
        {view.memory && <div className="who-memory">{view.memory}</div>}
        {also && <div className="who-also">{also}</div>}
        {fresh && <div className="who-fresh">Updated just now</div>}
      </div>
    </div>
  );
}

/** Patient side: a calm side panel about the person on the call. Who they are, what you talked about
 * recently, what is always true. Visual only: nothing here is read aloud, and it never shows
 * talking points, guardrails or the family's private notes. */
export function AboutPanel({ about, lang, onClose }) {
  if (!about?.who) return null;
  const p = phrases(lang);
  const { who } = about;
  const photo = { name: who.full_name || who.name, photo: who.photo, color: who.color };
  const empty = !about.always.length && !about.recent.length && !about.upcoming.length;
  return (
    <aside className="about-panel" aria-label={p.aboutBtn(who.name)}>
      <button className="about-hide" onClick={onClose}>{p.aboutHide}</button>
      <div className="about-who">
        <Avatar contact={photo} size={96} />
        <div className="about-name">{who.name}</div>
        <div className="about-rel">{who.detail ? `${who.relation}, ${who.detail}` : who.relation}</div>
      </div>

      {about.also_here?.length > 0 && (
        <section>
          <h3>{p.aboutAlso}</h3>
          <ul>{about.also_here.map((a) => <li key={a.id}><b>{a.name}</b>, {a.relation}</li>)}</ul>
        </section>
      )}
      {about.recent.length > 0 && (
        <section>
          <h3>{p.aboutRecent}</h3>
          <ul>{about.recent.map((r) => (
            <li key={r.id}><span className="about-when">{talkedWhen(r.at, lang)}</span>{r.text}</li>
          ))}</ul>
        </section>
      )}
      {about.upcoming.length > 0 && (
        <section>
          <h3>{p.aboutUpcoming}</h3>
          <ul>{about.upcoming.map((u) => <li key={u.id}>{u.text}</li>)}</ul>
        </section>
      )}
      {about.always.length > 0 && (
        <section>
          <h3>{p.aboutAlways}</h3>
          <ul>{about.always.map((a) => <li key={a.id}>{a.text}</li>)}</ul>
        </section>
      )}
      {empty && <p className="about-empty">{p.aboutNothing}</p>}
    </aside>
  );
}

export function RecallCard({ card }) {
  if (!card) return null;
  return (
    <div className="recall-card" role="status">
      <Icon name="heart" size={26} />
      <span>{card.text}</span>
    </div>
  );
}

/** Relative side: everything that must not crowd the patient's card. */
export function RelativePanel({ view, patientName, others, alsoHere, onAlsoHere, onClose, onFeedback }) {
  const [picking, setPicking] = useState(false);
  const [rated, setRated] = useState({});
  const chosen = new Set(alsoHere);
  const toggle = (id) => onAlsoHere(chosen.has(id) ? [...chosen].filter((x) => x !== id) : [...chosen, id]);
  const onScreen = view?.on_screen;
  return (
    <aside className="rel-panel" aria-label="Notes for you">
      <div className="rel-head">
        <b>For you</b>
        <button className="btn-link" onClick={onClose}>Hide</button>
      </div>

      {onScreen?.who && (
        <section>
          <div className="rel-title">{patientName} sees</div>
          <div className="rel-onscreen">
            “{onScreen.who.name}, {onScreen.who.relation}{onScreen.who.detail ? `, ${onScreen.who.detail}` : ""}.”
            {onScreen.memory && <> “{onScreen.memory.text}”</>}
          </div>
        </section>
      )}

      <section>
        <div className="rel-title">Who else is with you?</div>
        <div className="rel-chips">
          {others.filter((o) => chosen.has(o.id)).map((o) => (
            <span key={o.id} className="chip chip-info">
              {o.name} <button aria-label={`Remove ${o.name}`} onClick={() => toggle(o.id)}>×</button>
            </span>
          ))}
          <button className="btn-sm" onClick={() => setPicking((v) => !v)}>{picking ? "Done" : "+ Add someone"}</button>
        </div>
        {picking && (
          <div className="rel-pick">
            {others.map((o) => (
              <label key={o.id}>
                <input type="checkbox" checked={chosen.has(o.id)} onChange={() => toggle(o.id)} /> {o.name}
                <span className="muted small"> · {patientName}'s {o.relation}</span>
              </label>
            ))}
          </div>
        )}
        <p className="rel-fine">{patientName}'s card then says who else is in the room, so nobody has to be guessed.</p>
      </section>

      {view?.talking_points?.length > 0 && (
        <section>
          <div className="rel-title">You could ask</div>
          <ul>{view.talking_points.map((t, i) => <li key={i}>{t.replace(/^["“]|["”]$/g, "")}</li>)}</ul>
        </section>
      )}
      {view?.hints?.length > 0 && (
        <section className="rel-hints">
          <div className="rel-title">If {patientName} asks</div>
          {view.hints.map((h, i) => <p key={i}><b>About {h.name}:</b> {h.note}</p>)}
        </section>
      )}
      {view?.notes?.length > 0 && (
        <section>
          <div className="rel-title">Good to know</div>
          {view.notes.map((n, i) => <p key={i}>{n}</p>)}
        </section>
      )}
      {view?.known?.length > 0 && (
        <section>
          <div className="rel-title">What {patientName}'s memory has about you</div>
          <ul className="rel-known">
            {view.known.map((k) => (
              <li key={k.id}>
                <span>{k.text}</span>
                {onFeedback && (
                  <span className="thumbs">
                    {rated[k.id] ? <span className="muted small">{rated[k.id]}</span> : <>
                      <button aria-label="Useful to bring up" onClick={() => { onFeedback(k.id, true); setRated({ ...rated, [k.id]: "thanks" }); }}>👍</button>
                      <button aria-label="Not useful" onClick={() => { onFeedback(k.id, false); setRated({ ...rated, [k.id]: "noted" }); }}>👎</button>
                    </>}
                  </span>
                )}
              </li>
            ))}
          </ul>
          {onFeedback && <p className="rel-fine">👍 / 👎 changes how often {patientName}'s card brings this up.</p>}
        </section>
      )}
    </aside>
  );
}
