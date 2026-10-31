import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../lib/api.js";
import { useCaregiverAuth } from "../lib/auth.jsx";
import { firebaseEnabled, friendlyError } from "../lib/identity.js";
import { GoogleMark } from "../components/common.jsx";

export default function Login() {
  const { user, me, identity } = useCaregiverAuth();
  const [params] = useSearchParams();
  const nav = useNavigate();
  const invite = params.get("invite");
  const asRelative = params.get("as") === "relative"; // a family member: they get calls, not the dashboard
  const [mode, setMode] = useState("signin"); // signin | signup | reset
  const [form, setForm] = useState({ name: "", email: "", password: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [mismatch, setMismatch] = useState(null);

  // Frontend and backend must agree on the auth mode, or every request fails with 401.
  useEffect(() => {
    api.get("/health").then((h) => {
      if (h.auth === "firebase" && !firebaseEnabled) {
        setMismatch("The server expects Firebase sign-in, but this site has no Firebase web config. Add VITE_FIREBASE_* to frontend/.env and restart the dev server.");
      } else if (h.auth === "dev" && firebaseEnabled) {
        setMismatch("This site signs in with Firebase, but the server is in dev mode. Set FIREBASE_PROJECT_ID in backend/.env and restart the backend.");
      }
    }).catch(() => {});
  }, []);

  // Signed in: accept a pending invite, then go to the dashboard.
  useEffect(() => {
    if (!user || user.anonymous || !me) return;
    (async () => {
      if (invite) {
        try {
          await api.post("/invites/accept", { code: invite });
        } catch (e) {
          setError(e.message);
          return;
        }
      }
      nav(params.get("next") || (asRelative ? "/family" : "/caregiver"), { replace: true });
    })();
  }, [user, me]);

  async function run(fn) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setBusy(false);
    }
  }

  function submit(e) {
    e.preventDefault();
    if (mode === "reset") {
      return run(async () => {
        await identity.resetPassword(form.email);
        setNotice("If that email has an account, a reset link is on its way.");
        setMode("signin");
      });
    }
    if (mode === "signup" && form.password.length < 8 && firebaseEnabled) {
      setError("Please use a password of at least 8 characters.");
      return;
    }
    return run(() => mode === "signup"
      ? identity.signUpEmail(form.name.trim(), form.email.trim(), form.password)
      : identity.signInEmail(form.email.trim(), form.password));
  }

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  return (
    <div className="auth-page">
      <div className="auth-side">
        <Link to="/" className="auth-brand">Pehchaan</Link>
        {asRelative ? (
          <>
            <h1>Be there when they call.</h1>
            <ul className="auth-points">
              <li>Your phone or laptop rings when they want to talk to family.</li>
              <li>Tell them your news; it helps them remember you.</li>
              <li>See what to ask about and how past calls went.</li>
            </ul>
          </>
        ) : (
          <>
            <h1>Help the person you care for recognise every caller.</h1>
            <ul className="auth-points">
              <li>See who called, what was said, and what's coming up.</li>
              <li>Correct or hide anything before it reaches them.</li>
              <li>Share care with family; everyone sees the same picture.</li>
            </ul>
          </>
        )}
        {!asRelative && <p className="auth-fine">They can sign in with their own account, or you pair their tablet once with a 6-digit code.</p>}
      </div>
      <div className="auth-main">
        <div className="auth-card">
          {mismatch && <div className="auth-error" role="alert">{mismatch}</div>}
          {invite && <div className="auth-invite">You've been invited to join a family care circle. Sign in to accept.</div>}
          <h2>{mode === "signup" ? (asRelative ? "Create your account" : "Create your caregiver account") : mode === "reset" ? "Reset your password" : "Sign in to Pehchaan"}</h2>

          {mode !== "reset" && (
            <>
              <button className="google-btn" disabled={busy || !firebaseEnabled} onClick={() => run(() => identity.signInGoogle())}>
                <GoogleMark /> Continue with Google
              </button>
              {!firebaseEnabled && (
                <p className="auth-dev">
                  Google sign-in turns on once Firebase is configured (see README). Until then, the email form below
                  creates a <b>local dev account</b> with no password check.
                </p>
              )}
              <div className="auth-or"><span>or</span></div>
            </>
          )}

          <form onSubmit={submit} className="auth-form">
            {mode === "signup" && (
              <label>Your name<input required value={form.name} onChange={set("name")} autoComplete="name" /></label>
            )}
            <label>Email<input required type="email" value={form.email} onChange={set("email")} autoComplete="email" /></label>
            {mode !== "reset" && (
              <label>Password
                <input required={firebaseEnabled} type="password" value={form.password} onChange={set("password")}
                  autoComplete={mode === "signup" ? "new-password" : "current-password"} />
              </label>
            )}
            {error && <div className="auth-error" role="alert">{error}</div>}
            {notice && <div className="auth-notice">{notice}</div>}
            <button className="btn auth-submit" disabled={busy}>
              {busy ? "Please wait…" : mode === "signup" ? "Create account" : mode === "reset" ? "Send reset link" : "Sign in"}
            </button>
          </form>

          <div className="auth-switch">
            {mode === "signin" && (
              <>
                <button className="btn-link" onClick={() => setMode("signup")}>Create an account</button>
                {firebaseEnabled && <button className="btn-link" onClick={() => setMode("reset")}>Forgot password?</button>}
              </>
            )}
            {mode !== "signin" && <button className="btn-link" onClick={() => setMode("signin")}>Back to sign in</button>}
          </div>
        </div>
        {!asRelative && <p className="auth-fine center">Just a family member? <Link to="/login?as=relative">Sign in here</Link> to get their calls.</p>}
        <p className="auth-fine center">Setting up the patient's tablet? Open <Link to="/patient">/patient</Link> on it.</p>
      </div>
    </div>
  );
}
