import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../lib/api.js";
import { useCaregiverAuth } from "../lib/auth.jsx";
import { firebaseEnabled, friendlyError } from "../lib/identity.js";

function GoogleMark() {
  return (
    <svg width="20" height="20" viewBox="0 0 48 48" aria-hidden>
      <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9 3.5l6.7-6.7C35.6 2.4 30.2 0 24 0 14.6 0 6.6 5.4 2.7 13.3l7.8 6C12.4 13.7 17.7 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.1 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.4c-.5 2.9-2.2 5.3-4.6 6.9l7.4 5.7c4.3-4 6.9-9.9 6.9-17.1z" />
      <path fill="#FBBC05" d="M10.5 28.7c-.5-1.4-.8-3-.8-4.7s.3-3.2.8-4.7l-7.8-6C1 16.6 0 20.2 0 24s1 7.4 2.7 10.7l7.8-6z" />
      <path fill="#34A853" d="M24 48c6.2 0 11.4-2 15.2-5.5l-7.4-5.7c-2.1 1.4-4.7 2.2-7.8 2.2-6.3 0-11.6-4.2-13.5-9.9l-7.8 6C6.6 42.6 14.6 48 24 48z" />
    </svg>
  );
}

export default function Login() {
  const { user, me, identity } = useCaregiverAuth();
  const [params] = useSearchParams();
  const nav = useNavigate();
  const invite = params.get("invite");
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
      nav(params.get("next") || "/caregiver", { replace: true });
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
        <h1>Help the person you care for recognise every caller.</h1>
        <ul className="auth-points">
          <li>See who called, what was said, and what's coming up.</li>
          <li>Correct or hide anything before it reaches them.</li>
          <li>Share care with family; everyone sees the same picture.</li>
        </ul>
        <p className="auth-fine">Their tablet never needs a password. You pair it once with a 6-digit code.</p>
      </div>
      <div className="auth-main">
        <div className="auth-card">
          {mismatch && <div className="auth-error" role="alert">{mismatch}</div>}
          {invite && <div className="auth-invite">You've been invited to join a family care circle. Sign in to accept.</div>}
          <h2>{mode === "signup" ? "Create your caregiver account" : mode === "reset" ? "Reset your password" : "Sign in to Pehchaan"}</h2>

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
        <p className="auth-fine center">Setting up the patient's tablet? Open <Link to="/patient">/patient</Link> on it.</p>
      </div>
    </div>
  );
}
