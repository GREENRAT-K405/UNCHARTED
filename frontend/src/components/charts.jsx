import { useState } from "react";

// Two validated series colours (CVD ΔE 24.7, ≥3:1 on the card surface):
// --series-1 = calls that happened, --series-2 = attempts the repeat-call guard paused.

export function Legend({ items }) {
  return (
    <div className="legend">
      {items.map((it) => (
        <span key={it.label} className="legend-item">
          <span className="legend-swatch" style={{ background: it.color }} />
          {it.label}
        </span>
      ))}
    </div>
  );
}

/**
 * Stacked columns from plain HTML: thin bars, 2px surface gap between segments, rounded data-ends,
 * a per-column hover/focus tooltip, and a table view for screen readers and anyone who prefers numbers.
 */
export function StackedColumns({ data, series, xLabel, tooltipTitle, height = 160, labelEvery = 1, caption }) {
  const [hover, setHover] = useState(null);
  const [table, setTable] = useState(false);
  const max = Math.max(1, ...data.map((d) => series.reduce((s, x) => s + (d[x.key] || 0), 0)));
  const top = niceMax(max);

  if (table) {
    return (
      <div>
        <table className="data-table">
          <caption className="sr-only">{caption}</caption>
          <thead><tr><th>{xLabel.header}</th>{series.map((s) => <th key={s.key}>{s.label}</th>)}</tr></thead>
          <tbody>
            {data.map((d, i) => (
              <tr key={i}><td>{tooltipTitle(d)}</td>{series.map((s) => <td key={s.key}>{d[s.key] || 0}</td>)}</tr>
            ))}
          </tbody>
        </table>
        <button className="btn-link small" onClick={() => setTable(false)}>Show chart</button>
      </div>
    );
  }

  return (
    <div className="colchart">
      <div className="colchart-plot" style={{ height }} onPointerLeave={() => setHover(null)}>
        <div className="colchart-grid">
          <span style={{ bottom: "100%" }}>{top}</span>
          <span style={{ bottom: "50%" }}>{top / 2}</span>
          <span style={{ bottom: 0 }}>0</span>
        </div>
        <div className="colchart-cols">
          {data.map((d, i) => {
            const total = series.reduce((s, x) => s + (d[x.key] || 0), 0);
            return (
              <div key={i} className={`colchart-col ${hover === i ? "hover" : ""}`} tabIndex={0}
                onPointerEnter={() => setHover(i)} onFocus={() => setHover(i)} onBlur={() => setHover(null)}
                aria-label={`${tooltipTitle(d)}: ${series.map((s) => `${d[s.key] || 0} ${s.label}`).join(", ")}`}>
                <div className="colchart-stack" style={{ height: `${(total / top) * 100}%` }}>
                  {series.slice().reverse().map((s) => (d[s.key] || 0) > 0 && (
                    <div key={s.key} className="colchart-seg" style={{ flexGrow: d[s.key], background: s.color }} />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
        {hover !== null && (
          <div className="viz-tip" style={{ left: `${((hover + 0.5) / data.length) * 100}%` }} role="status">
            <div className="viz-tip-title">{tooltipTitle(data[hover])}</div>
            {series.map((s) => (
              <div key={s.key} className="viz-tip-row">
                <span className="viz-tip-key" style={{ background: s.color }} />
                <b>{data[hover][s.key] || 0}</b> <span>{s.label}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="colchart-x">
        {data.map((d, i) => <span key={i}>{i % labelEvery === 0 ? xLabel.tick(d, i) : ""}</span>)}
      </div>
      <button className="btn-link small table-toggle" onClick={() => setTable(true)}>View as table</button>
    </div>
  );
}

/** Horizontal bars, one series: value label at the end of each bar, entity named on the left. */
export function BarList({ rows, color, valueLabel, max: forcedMax, render }) {
  const max = forcedMax || Math.max(1, ...rows.map((r) => r.value));
  return (
    <ul className="barlist">
      {rows.map((r) => (
        <li key={r.key} title={r.title}>
          <span className="barlist-label">{render ? render(r) : r.label}</span>
          <span className="barlist-track">
            <span className="barlist-fill" style={{ width: `${(r.value / max) * 100}%`, background: color }} />
          </span>
          <span className="barlist-value">{r.value}{valueLabel ? ` ${valueLabel}` : ""}</span>
        </li>
      ))}
    </ul>
  );
}

export function StatTile({ label, value, delta, goodWhen = "up", note }) {
  let tone = "flat";
  let arrow = "→";
  if (delta && delta.value !== 0) {
    const up = delta.value > 0;
    arrow = up ? "↑" : "↓";
    tone = (up && goodWhen === "up") || (!up && goodWhen === "down") ? "good" : goodWhen === "none" ? "flat" : "bad";
  }
  return (
    <div className="stat-tile">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {delta && (
        <div className={`stat-delta ${tone}`}>
          {arrow} {Math.abs(delta.value)} {delta.label}
        </div>
      )}
      {note && <div className="stat-note">{note}</div>}
    </div>
  );
}

function niceMax(v) {
  const steps = [2, 4, 6, 8, 10, 12, 16, 20, 30, 40, 50, 60, 80, 100];
  return steps.find((s) => s >= v) || Math.ceil(v / 50) * 50;
}
