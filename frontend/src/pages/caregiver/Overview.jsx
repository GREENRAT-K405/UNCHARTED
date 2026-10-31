import { useEffect, useState } from "react";
import { api } from "../../lib/api.js";
import { Avatar, Chip, fmtDate, relTime, tone } from "../../components/common.jsx";
import { BarList, Legend, StackedColumns, StatTile } from "../../components/charts.jsx";

const SERIES = [
  { key: "calls", label: "calls", color: "var(--series-1)" },
  { key: "blocked", label: "paused by the repeat-call guard", color: "var(--series-2)" },
];
const ACTION_LABEL = {
  created: "added", corrected: "corrected", approved: "approved", rejected: "rejected", deleted: "forgot",
  superseded: "replaced", reweighted: "re-weighted", "visibility:never_surface": "made private",
  "visibility:caregiver_only": "hid from the patient", "visibility:patient": "showed to the patient",
  identity_asked: "asked “who is this?” more than once about", archived: "archived", unarchived: "unarchived",
};

function hourLabel(h) {
  return h === 0 ? "12a" : h < 12 ? `${h}a` : h === 12 ? "12p" : `${h - 12}p`;
}

export default function Overview({ ctx, feed, liveTranscript }) {
  const { patient, contacts, tick, openMemory, goTo, canEdit } = ctx;
  const [days, setDays] = useState(14);
  const [stats, setStats] = useState(null);
  const [calls, setCalls] = useState([]);
  const [ttlContact, setTtlContact] = useState("");

  useEffect(() => {
    api.get(`/patients/${patient.id}/stats?days=${days}`).then(setStats);
  }, [patient.id, tick, days]);
  useEffect(() => {
    api.get(`/calls?patient_id=${patient.id}&limit=10&include_blocked=false`).then(setCalls);
  }, [patient.id, tick]);

  const live = calls.filter((c) => ["ringing", "active"].includes(c.status));
  const lastProcessed = calls.find((c) => c.status === "processed" && c.agent?.actions?.length);
  const name = (id) => contacts.find((c) => c.id === id)?.name || "—";
  const contactOf = (id) => contacts.find((c) => c.id === id);

  async function seedExpiring() {
    await api.post("/demo/expiring", { patient_id: patient.id, contact_id: ttlContact || null, seconds: 90 });
  }

  const t = stats?.totals;
  // Sundowning: are paused attempts concentrated late in the day?
  let peak = null;
  if (stats) {
    const blocked = stats.by_hour.reduce((s, h) => s + h.blocked, 0);
    const evening = stats.by_hour.filter((h) => h.hour >= 16 && h.hour <= 20).reduce((s, h) => s + h.blocked, 0);
    if (blocked >= 4) peak = { share: Math.round((evening / blocked) * 100), blocked };
  }

  return (
    <div className="overview">
      <div className="range-row">
        <span className="muted">Showing the last</span>
        {[7, 14, 30].map((d) => (
          <button key={d} className={`seg ${days === d ? "on" : ""}`} onClick={() => setDays(d)}>{d} days</button>
        ))}
      </div>

      {t && (
        <div className="stat-row">
          <StatTile label="Calls" value={t.calls} delta={{ value: t.calls - t.prev_calls, label: `vs previous ${days} days` }} />
          <StatTile label="Time talking" value={`${Math.floor(t.minutes / 60)}h ${t.minutes % 60}m`} note={`${t.people} different people`} />
          <StatTile label="Repeat calls paused" value={t.blocked} goodWhen="down"
            delta={{ value: t.blocked - t.prev_blocked, label: `vs previous ${days} days` }}
            note={t.overrides ? `${t.overrides} ${t.overrides === 1 ? "time" : "times"} ${patient.name} chose “call anyway”` : null} />
          <StatTile label="Memories learned from calls" value={t.memories_added} note={`${t.active_memories} active in total`} />
          <button className={`stat-tile stat-action ${t.pending_review ? "attn" : ""}`} onClick={() => goTo("review")}>
            <div className="stat-label">Waiting for your review</div>
            <div className="stat-value">{t.pending_review}</div>
            <div className="stat-note">{t.pending_review ? "Open the review queue →" : "All caught up"}</div>
          </button>
        </div>
      )}

      {stats && (
        <div className="grid-2">
          <div className="panel">
            <div className="panel-title">Calls per day</div>
            <Legend items={SERIES} />
            <StackedColumns data={stats.per_day} series={SERIES} caption="Calls per day"
              labelEvery={days > 14 ? 3 : days > 7 ? 2 : 1}
              xLabel={{ header: "Day", tick: (d) => new Date(d.day).toLocaleDateString("en-IN", { day: "numeric", month: "short" }) }}
              tooltipTitle={(d) => new Date(d.day).toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "short" })} />
          </div>

          <div className="panel">
            <div className="panel-title">Who {patient.name} talks to <span className="panel-sub">calls in the last {days} days</span></div>
            <BarList color="var(--series-1)"
              rows={stats.by_contact.filter((c) => c.calls > 0).map((c) => ({
                key: c.contact_id, value: c.calls, contact: c,
                title: `${c.minutes} min · last call ${relTime(c.last_call, ctx.offsetMs)}${c.blocked ? ` · ${c.blocked} paused` : ""}`,
              }))}
              render={(r) => (
                <span className="who">
                  <Avatar contact={contactOf(r.contact.contact_id) || { name: r.contact.name, color: r.contact.color }} size={26} />
                  <span><b>{r.contact.name}</b> <span className="muted small">{r.contact.relation}</span></span>
                </span>
              )} />
            {(() => {
              const quiet = contacts.filter((c) => c.status === "active" && !stats.by_contact.some((b) => b.contact_id === c.id && b.calls > 0));
              return quiet.length > 0 && (
                <p className="muted small">No calls with {quiet.map((c) => c.name).join(", ")} in this period. Maybe suggest one?</p>
              );
            })()}
          </div>

          <div className="panel">
            <div className="panel-title">Time of day</div>
            <Legend items={SERIES} />
            <StackedColumns data={stats.by_hour.slice(6, 23)} series={SERIES} caption="Calls by hour of day" height={130}
              labelEvery={2} xLabel={{ header: "Hour", tick: (d) => hourLabel(d.hour) }}
              tooltipTitle={(d) => `${hourLabel(d.hour)}–${hourLabel((d.hour + 1) % 24)}`} />
            {peak && peak.share >= 50 && (
              <div className="insight">
                <b>{peak.share}%</b> of paused repeat calls happen between 4 and 9 PM. Late-day restlessness
                (“sundowning”) is common; a planned evening call from family may help.
              </div>
            )}
          </div>

          <div className="panel">
            <div className="panel-title">Coming up <span className="panel-sub">plans and events {patient.name} may ask about</span></div>
            <ul className="upcoming">
              {stats.upcoming.length === 0 && <li className="empty">Nothing scheduled.</li>}
              {stats.upcoming.map((u) => (
                <li key={u.id} className="clickable" onClick={() => openMemory(u.id)}>
                  <div className="up-date">
                    <span>{new Date(u.event_date).toLocaleDateString("en-IN", { weekday: "short" })}</span>
                    <b>{new Date(u.event_date).getDate()}</b>
                  </div>
                  <div>
                    <div>{u.content}</div>
                    <div className="muted small">{u.contact?.name || "Everyone"} · {u.type} · fades {relTime(u.expires_at, ctx.offsetMs)}</div>
                  </div>
                </li>
              ))}
            </ul>
          </div>

          <div className="panel">
            <div className="panel-title">Recurring confusions <span className="panel-sub">things {patient.name} believed that didn't match your records</span></div>
            {stats.confusions.length === 0 && !stats.who_asks?.length && <div className="empty">None so far.</div>}
            <ul className="confusions">
              {stats.who_asks?.filter((w) => w.asks > 1).map((w) => (
                <li key={w.contact_id}>
                  <span className="conf-times">{w.asks}×</span>
                  <span>
                    Asked who {w.name} was{w.repeated_calls ? ` (more than once on ${w.repeated_calls} ${w.repeated_calls === 1 ? "call" : "calls"})` : ""}
                    <span className="muted small"> · {w.calls} {w.calls === 1 ? "call" : "calls"} · last {relTime(w.last, ctx.offsetMs)} · “Who is this?” card</span>
                  </span>
                </li>
              ))}
              {stats.confusions.map((c) => (
                <li key={c.content}>
                  <span className="conf-times">{c.times}×</span>
                  <span>
                    “{c.content}”
                    <span className="muted small"> · last {relTime(c.last, ctx.offsetMs)} · {c.statuses.map((s) => s.replace("_", " ")).join(", ")}</span>
                  </span>
                </li>
              ))}
            </ul>
            {stats.confusions.some((c) => c.times > 1) && (
              <p className="muted small">When the same belief keeps coming back, let callers know so everyone answers the same way.</p>
            )}
            <div className="sub-title" style={{ marginTop: 16 }}>Memory bank</div>
            <BarList color="var(--series-1)" valueLabel=""
              rows={stats.memories_by_type.map((m) => ({ key: m.type, label: m.type, value: m.count }))} />
          </div>

          <div className="panel">
            <div className="panel-title">Family activity</div>
            <ul className="activity">
              {stats.activity.length === 0 && <li className="empty">No edits yet.</li>}
              {stats.activity.map((a) => (
                <li key={a.id}>
                  <b>{a.actor === "patient" ? patient.name : a.actor}</b> {ACTION_LABEL[a.action] || a.action.replace("feedback:", "marked ")}
                  {a.memory ? <> “<span className="clickable link-like" onClick={() => openMemory(a.memory.id)}>{a.memory.content}</span>”</> : " a memory that has since been forgotten"}
                  <span className="muted small"> · {relTime(a.ts, ctx.offsetMs)}</span>
                </li>
              ))}
            </ul>
            {stats.devices.length > 0 && (
              <p className="muted small">
                {stats.devices.map((d) => `${d.label}: last seen ${relTime(d.last_seen, ctx.offsetMs)}`).join(" · ")}
              </p>
            )}
          </div>
        </div>
      )}

      <div className="grid-2 section-gap">
        <div className="col">
          <div className="panel">
            <div className="panel-title">Calls in progress</div>
            {live.length === 0 && <div className="empty">No active calls. Start one from the patient screen.</div>}
            {live.map((c) => (
              <div key={c.id} className="live-call">
                <div>
                  <b>{patient.name} ↔ {c.contact_name}</b> <Chip tone={c.status === "active" ? "good" : "warn"}>{c.status}</Chip>
                  {c.off_record && <Chip tone="bad">off the record</Chip>}
                </div>
                {c.join_url && (
                  <div className="join-line">Join link: <a href={c.join_url} target="_blank" rel="noreferrer">{c.join_url}</a></div>
                )}
              </div>
            ))}
            {liveTranscript.length > 0 && (
              <div className="transcript live">
                {liveTranscript.slice(-10).map((tl, i) => (
                  <div key={i} className={`tline ${tl.speaker}`}>
                    <span className="tspk">{tl.speaker === "patient" ? patient.name : "Contact"}</span> {tl.text}
                  </div>
                ))}
              </div>
            )}
          </div>

          {lastProcessed && (
            <div className="panel">
              <div className="panel-title">
                Memory agent: last call with {lastProcessed.contact_name}
                <span className="panel-sub">{fmtDate(lastProcessed.started_at)} · {lastProcessed.agent.mode}</span>
              </div>
              {lastProcessed.summary && <p className="summary">{lastProcessed.summary}</p>}
              <ul className="actions">
                {lastProcessed.agent.actions.map((a, i) => (
                  <li key={i} onClick={() => a.memory_id && openMemory(a.memory_id)} className={a.memory_id ? "clickable" : ""}>
                    <Chip tone={tone(a.action)}>{a.action}</Chip>
                    <span className="act-content">{a.content}</span>
                    {a.target_content && <span className="act-target">↳ {a.action === "CONFLICT" ? "conflicts with" : "was"}: “{a.target_content}”</span>}
                    <span className="act-reason">{a.reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {canEdit && (
            <div className="panel">
              <div className="panel-title">Demo: TTL expiry</div>
              <p className="muted">
                Adds a plan that expires 90 seconds from now. MongoDB's TTL monitor runs about once a minute, so it disappears
                within ~1–2 minutes and shows up in the feed as a delete event.
              </p>
              <div className="row">
                <select value={ttlContact} onChange={(e) => setTtlContact(e.target.value)}>
                  <option value="">Rahul (default)</option>
                  {contacts.filter((c) => c.status === "active").map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
                <button className="btn" onClick={seedExpiring}>Add expiring memory</button>
              </div>
            </div>
          )}
        </div>

        <div className="panel feed">
          <div className="panel-title">Live memory feed <span className="panel-sub">MongoDB change streams</span></div>
          {feed.length === 0 && <div className="empty">Waiting for changes. Make a call or edit a memory and it appears here instantly.</div>}
          {feed.map((e, i) => (
            <div key={i} className={`feed-item ${e.expired ? "expired" : ""}`}>
              {e.type === "memory" && (
                <>
                  <Chip tone={e.expired ? "warn" : tone(e.op)}>{e.expired ? "TTL expired" : e.op}</Chip>
                  <span className="feed-text clickable" onClick={() => e.op !== "delete" && openMemory(e.id)}>{e.content}</span>
                  {e.memory && (
                    <span className="feed-meta">
                      {e.memory.type} · {e.memory.status}
                      {e.fields?.length > 0 && ` · changed ${e.fields.filter((f) => f !== "updated_at").join(", ")}`}
                      {e.memory.contact_id && ` · ${name(e.memory.contact_id)}`}
                    </span>
                  )}
                </>
              )}
              {e.type === "call" && (
                <>
                  <Chip tone={e.call.status === "blocked" ? "warn" : "info"}>{e.call.status === "blocked" ? "paused" : "call"}</Chip>
                  <span className="feed-text">
                    {e.call.status === "blocked" ? `Repeat-call guard paused a call to ${name(e.call.contact_id)}` : `Call with ${name(e.call.contact_id)} is ${e.call.status}`}
                  </span>
                </>
              )}
              {e.type === "contact_update" && (
                <>
                  <Chip tone="info">update</Chip>
                  <span className="feed-text">{e.contact_name} shared an update from their link{e.summary ? `: ${e.summary}` : ""}</span>
                </>
              )}
              {e.type === "contact_request" && (
                <>
                  <Chip tone="warn">request</Chip>
                  <span className="feed-text">{patient.name} asked to add “{e.contact.name}”. Review in Contacts.</span>
                </>
              )}
              <span className="feed-time">{new Date(e.at).toLocaleTimeString("en-IN")}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
