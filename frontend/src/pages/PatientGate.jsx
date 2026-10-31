import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { api, requestAs, setIdentity } from "../lib/api.js";
import { caregiverIdentity, deviceIdentity, firebaseEnabled, friendlyError } from "../lib/identity.js";
import { GoogleMark } from "../components/common.jsx";

/**
 * The patient screen. Patients sign in with their own Google or email account, or family pairs
 * the tablet by typing the number shown here into their dashboard (no password at all). Either
 * way the screen then stays signed in.
 *
 * Both paths use the device identity: it starts anonymous (which is what gets paired), and a
 * real sign-in replaces the anonymous user on this browser.
 */
export default function PatientGate({ children }) {
  setIdentity(deviceIdentity);
  const [state, setState] = useState({ status: "loading" });
  const poll = useRef(null);

  useEffect(() => {
    const off = deviceIdentity.onChange(async (u) => {
      if (!u) {
        try {
          await deviceIdentity.signInDevice();
        } catch (e) {
          setState({ status: "signin", offline: friendlyError(e) });
        }
        return;
      }
      check();
    });
    return () => {
      off();
      clearTimeout(poll.current);
    };
  }, []);

  async function check() {
    clearTimeout(poll.current);
    try {
      const me = await api.get("/devices/me");
      if (me.paired) {
        setState({ status: "paired", patient: me.patient, account: me.account || null, device: me.device || null });
        poll.current = setTimeout(check, 60000); // notice an unpairing within a minute
        return;
      }
      if (me.account) {
        setState({ status: "profile", account: me.account });
        return;
      }
      const code = await api.post("/devices/pair-code");
      setState({ status: "signin", code: code.code });
      poll.current = setTimeout(check, 3000); // notice a pairing from the family's phone
    } catch (e) {
      setState((s) => ({ ...s, status: s.status === "loading" ? "signin" : s.status, offline: e.message }));
      poll.current = setTimeout(check, 5000);
    }
  }

  async function signOut() {
    clearTimeout(poll.current);
    setState({ status: "loading" });
    await deviceIdentity.signOut(); // onChange signs back in anonymously and shows sign-in again
  }

  if (state.status === "paired") return children(state.patient, { account: state.account, device: state.device, signOut });
  if (state.status === "loading") return <div className="patient-shell loading">Getting ready…</div>;
  return (
    <div className="pair-screen">
      {state.status === "profile"
        ? <ProfileStep account={state.account} onDone={check} signOut={signOut} />
        : <SignInStep state={state} onPaired={check} />}
    </div>
  );
}

function useBusy() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  async function run(fn, { keepBusy = false } = {}) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      if (!keepBusy) setBusy(false);
    } catch (e) {
      setError(friendlyError(e));
      setBusy(false);
    }
  }
  return { busy, error, run };
}

/** Not signed in: the patient's own sign-in first, family pairing underneath. */
function SignInStep({ state, onPaired }) {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const [mode, setMode] = useState(null); // null (choose) | signin | signup
  const [form, setForm] = useState({ name: "", email: "", password: "" });
  const { busy, error, run } = useBusy();
  const [helper, setHelper] = useState(null); // a caregiver signed in on this same browser
  const [pairing, setPairing] = useState(false);
  const [ownOpen, setOwnOpen] = useState(false); // the patient's own sign-in is the secondary path
  const claiming = params.get("claim");

  // Offer one-tap pairing when a caregiver is already signed in on this browser. Subscribe rather
  // than read .current: Firebase restores the caregiver session asynchronously after page load.
  useEffect(() => caregiverIdentity.onChange((cg) => {
    if (!cg || cg.anonymous) return;
    requestAs(caregiverIdentity, "GET", "/me").then((m) => {
      const circle = m.circles.find((c) => c.role !== "viewer" && c.role !== "device");
      if (circle) setHelper({ name: m.name, patient: circle.patient });
    }).catch(() => {});
  }), []);

  // Coming back from "set up this screen now": the family member just signed in for this, so pair at once.
  useEffect(() => {
    if (helper && state.code && !pairing && params.get("setup")) pairHere();
  }, [helper, state.code]);

  async function pairHere() {
    setPairing(true);
    try {
      await requestAs(caregiverIdentity, "POST", `/circles/${helper.patient.id}/devices`,
        { code: state.code, label: `${helper.patient.name}'s screen` });
      if (params.get("setup")) setParams({}, { replace: true });
      await onPaired();
    } finally {
      setPairing(false);
    }
  }

  // On success the gate re-checks and moves on, so stay busy until this screen goes away.
  const google = () => run(() => deviceIdentity.signInGoogle(), { keepBusy: true });
  function submit(e) {
    e.preventDefault();
    if (mode === "signup" && firebaseEnabled && form.password.length < 8) {
      run(() => { throw new Error("Please use a password of at least 8 characters."); });
      return;
    }
    run(() => mode === "signup"
      ? deviceIdentity.signUpEmail(form.name.trim(), form.email.trim(), form.password)
      : deviceIdentity.signInEmail(form.email.trim(), form.password), { keepBusy: true });
  }
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const code = state.code ? `${state.code.slice(0, 3)} ${state.code.slice(3)}` : "";

  const ownSignIn = (
    <>
      {mode === null ? (
        <div className="ps-options">
          {firebaseEnabled && (
            <button className="ps-google" onClick={google} disabled={busy}>
              <GoogleMark size={30} /> Continue with Google
            </button>
          )}
          <button className="btn-big btn-calm" onClick={() => setMode("signin")} disabled={busy}>Sign in with email</button>
          <button className="btn-link ps-link" onClick={() => setMode("signup")}>New here? Create an account</button>
        </div>
      ) : (
        <form className="ps-form" onSubmit={submit}>
          {mode === "signup" && (
            <label className="ps-field">Your name
              <input className="big-input" required value={form.name} onChange={set("name")} autoComplete="name" autoFocus />
            </label>
          )}
          <label className="ps-field">Email
            <input className="big-input" required type="email" value={form.email} onChange={set("email")}
              autoComplete="email" autoFocus={mode === "signin"} />
          </label>
          <label className="ps-field">Password
            <input className="big-input" required={firebaseEnabled} type="password" value={form.password} onChange={set("password")}
              autoComplete={mode === "signup" ? "new-password" : "current-password"} />
          </label>
          <button className="btn-big btn-primary" disabled={busy}>
            {busy ? "Please wait…" : mode === "signup" ? "Create my account" : "Sign in"}
          </button>
          <div className="ps-switch">
            <button type="button" className="btn-link ps-link" onClick={() => setMode(mode === "signup" ? "signin" : "signup")}>
              {mode === "signup" ? "I already have an account" : "New here? Create an account"}
            </button>
            <button type="button" className="btn-link ps-link" onClick={() => setMode(null)}>Back</button>
          </div>
        </form>
      )}
      {!firebaseEnabled && <p className="auth-dev">Dev mode: email sign-in creates a local account with no password check.</p>}
      {error && <div className="auth-error ps-error" role="alert">{error}</div>}
    </>
  );

  // Opened from the family's sign-in link: the patient's own account is the point.
  if (claiming) {
    return (
      <div className="pair-card">
        <div className="pair-hello">Hello!</div>
        <p className="pair-lede">Your family sent you a sign-in link. Sign in to finish.</p>
        {state.offline && <p className="pair-error">We're having trouble connecting. We'll keep trying.</p>}
        {ownSignIn}
      </div>
    );
  }

  // The main path: family sets this screen up (6-digit number, or one tap when signed in here).
  return (
    <>
      <div className="pair-card pair-main">
        <div className="pair-hello">Hello!</div>
        {state.offline && <p className="pair-error">We're having trouble connecting. We'll keep trying.</p>}
        <p className="pair-lede">Your family will set up this screen for you.</p>
        {state.code && (
          <>
            <div className="pair-label">They type this number:</div>
            <div className="pair-code" aria-label={`Pairing number ${state.code.split("").join(" ")}`}>{code}</div>
          </>
        )}
        {helper ? (
          <button className="btn-big btn-primary" onClick={pairHere} disabled={pairing || !state.code}>
            {pairing ? "Setting up…" : `I'm ${helper.name || "family"}: set this up for ${helper.patient.name}`}
          </button>
        ) : (
          <Link className="btn-big btn-primary pair-now" to={`/login?next=${encodeURIComponent(`${location.pathname}?setup=1`)}`}>
            Family: set up this screen now
          </Link>
        )}
        <p className="pair-help">
          On their phone: sign in to Pehchaan, open <b>Family &amp; devices</b>, choose <b>Pair a tablet</b> and enter
          the number. This screen then stays signed in, and they can unpair it any time.
        </p>
      </div>

      <div className="pair-card ps-secondary">
        {ownOpen ? (
          <>
            {ownSignIn}
            {mode === null && <button className="btn-link ps-link" onClick={() => setOwnOpen(false)}>Close</button>}
          </>
        ) : (
          <button className="btn-link ps-link" onClick={() => setOwnOpen(true)}>Have your own account? Sign in</button>
        )}
      </div>
    </>
  );
}

/** Signed in, but the account isn't a patient yet: start a profile, or link to the one family made. */
function ProfileStep({ account, onDone, signOut }) {
  const [params, setParams] = useSearchParams();
  const firstName = (account.name || "").split(" ")[0];
  const [form, setForm] = useState({ name: firstName, full_name: account.name || "" });
  const [code, setCode] = useState(params.get("claim") || "");
  const { busy, error, run } = useBusy();

  function claim(c) {
    run(async () => {
      await api.post("/patients/claim", { code: c });
      if (params.get("claim")) setParams({}, { replace: true });
      await onDone();
    }, { keepBusy: true });
  }

  // Opened from the family's sign-in link: link straight away.
  useEffect(() => {
    if (params.get("claim") && !account.family) claim(params.get("claim"));
  }, []);

  if (account.family) {
    return (
      <div className="pair-card">
        <div className="pair-hello">This is a family account</div>
        <p className="pair-lede">
          {account.email || account.name} looks after someone on Pehchaan. On this screen, sign in with the patient's own
          account, or let family pair it.
        </p>
        <div className="ps-options">
          <button className="btn-big btn-primary" onClick={signOut}>Use a different account</button>
          <Link className="btn-link ps-link" to="/caregiver">Open the family dashboard instead</Link>
        </div>
      </div>
    );
  }

  function register(e) {
    e.preventDefault();
    run(async () => {
      await api.post("/patients/register", { ...form, language: navigator.language || "en-IN" });
      await onDone();
    }, { keepBusy: true });
  }

  return (
    <div className="pair-card">
      <div className="pair-hello">Welcome{firstName ? `, ${firstName}` : ""}!</div>
      <p className="pair-lede">Let's get your screen ready.</p>
      <form className="ps-form" onSubmit={(e) => { e.preventDefault(); claim(code); }}>
        <label className="ps-field">Did your family give you a code? Type it here.
          <input className="big-input ps-code" value={code} onChange={(e) => setCode(e.target.value)} placeholder="ABCD-2345"
            autoCapitalize="characters" autoComplete="off" />
        </label>
        <button className="btn-big btn-primary" disabled={busy || !code.trim()}>Use my family's code</button>
      </form>
      <div className="auth-or ps-or"><span>or start on my own</span></div>
      <form className="ps-form" onSubmit={register}>
        <label className="ps-field">What should we call you?
          <input className="big-input" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="Aaji, Dad, Meena…" />
        </label>
        <label className="ps-field">Your full name
          <input className="big-input" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })}
            autoComplete="name" />
        </label>
        <button className="btn-big btn-calm" disabled={busy}>Start</button>
      </form>
      {error && <div className="auth-error ps-error" role="alert">{error}</div>}
      <p className="ps-who">
        Signed in as {account.email || account.name}. <button className="btn-link ps-link" onClick={signOut}>Use a different account</button>
      </p>
    </div>
  );
}
