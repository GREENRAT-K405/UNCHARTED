import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../lib/api.js";
import { useCaregiverAuth } from "../../lib/auth.jsx";

export default function Onboarding() {
  const { me, refreshMe, signOut } = useCaregiverAuth();
  const [form, setForm] = useState({ name: "", full_name: "", my_relation: "" });
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  async function run(which, fn) {
    setBusy(which);
    setError(null);
    try {
      await fn();
      await refreshMe();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="onboard">
      <div className="onboard-head">
        <h1>Welcome{me.name ? `, ${me.name.split(" ")[0]}` : ""}</h1>
        <p>Pehchaan works around one person living with memory loss and the family who care for them. How would you like to start?</p>
      </div>
      {error && <div className="auth-error onboard-error" role="alert">{error}</div>}
      <div className="onboard-grid">
        <div className="panel onboard-card featured">
          <div className="panel-title">Explore the demo family</div>
          <p>Aaji (Sunanda Patil, 74) and her ten family members and friends, with six weeks of calls, memories, briefs and insights already in place.</p>
          <button className="btn" disabled={!!busy} onClick={() => run("demo", () => api.post("/circles/demo"))}>
            {busy === "demo" ? "Opening…" : "Open Aaji's dashboard"}
          </button>
        </div>
        <form className="panel onboard-card form" onSubmit={(e) => { e.preventDefault(); run("create", () => api.post("/circles", form)); }}>
          <div className="panel-title">Set up care for someone</div>
          <input required placeholder="What they like to be called (e.g. Aai, Nana)" value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input placeholder="Full name (optional)" value={form.full_name}
            onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
          <input placeholder="You are their… (e.g. son, daughter, carer)" value={form.my_relation}
            onChange={(e) => setForm({ ...form, my_relation: e.target.value })} />
          <button className="btn" disabled={!!busy}>{busy === "create" ? "Creating…" : "Create care circle"}</button>
        </form>
        <form className="panel onboard-card form" onSubmit={(e) => { e.preventDefault(); run("join", () => api.post("/invites/accept", { code })); }}>
          <div className="panel-title">Join your family's circle</div>
          <p className="muted">Ask the person who set it up for an 8-letter invite code.</p>
          <input required className="code-input" placeholder="ABCD2345" value={code} maxLength={9}
            onChange={(e) => setCode(e.target.value.toUpperCase())} />
          <button className="btn" disabled={!!busy}>{busy === "join" ? "Joining…" : "Join circle"}</button>
        </form>
      </div>
      <p className="muted center">Just a family member, not the one setting things up? <Link to="/family">Go to your page</Link> to get their calls.</p>
      <p className="muted center">Signed in as {me.email || me.name}. <button className="btn-link" onClick={signOut}>Sign out</button></p>
    </div>
  );
}
