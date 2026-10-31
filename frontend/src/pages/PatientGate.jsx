import { createContext, useContext, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, requestAs, setIdentity } from "../lib/api.js";
import { caregiverIdentity, deviceIdentity, friendlyError } from "../lib/identity.js";
import CallPage from "./CallPage.jsx";

const SessionCtx = createContext({ identity: deviceIdentity, patient: null, viaGoogle: false });
export const usePatientSession = () => useContext(SessionCtx);

/** The patient's side of a meet, signed in the same way as their home screen. */
export function PatientCall() {
  const { identity } = usePatientSession();
  return <CallPage role="patient" identity={identity} />;
}

function firstAuthState(identity) {
  return new Promise((resolve) => {
    let off = null;
    let done = false;
    off = identity.onChange((u) => {
      if (done) return;
      done = true;
      resolve(u);
      setTimeout(() => off?.(), 0);
    });
  });
}

/**
 * The patient's screen. Two ways in, neither needs a password typed on the tablet:
 *  - the patient signed in with Google using the email their caretaker entered, or
 *  - the tablet signed in anonymously and a caretaker paired it with the 6-digit number shown here.
 */
export default function PatientGate({ children }) {
  const [state, setState] = useState({ status: "loading" });
  const [session, setSession] = useState(null);
  const [helper, setHelper] = useState(null); // a caretaker signed in on this same browser
  const [pairing, setPairing] = useState(false);
  const poll = useRef(null);

  useEffect(() => {
    let off = null;
    (async () => {
      // 1. A patient signed in with Google on this browser?
      const google = await firstAuthState(caregiverIdentity);
      if (google && !google.anonymous) {
        try {
          const me = await requestAs(caregiverIdentity, "GET", "/me");
          if (me.kind === "patient") {
            setIdentity(caregiverIdentity);
            const patient = me.circles[0]?.patient;
            setSession({ identity: caregiverIdentity, patient, viaGoogle: true });
            setState({ status: "paired", patient });
            return;
          }
          if (me.kind === "caregiver") {
            const circle = me.circles.find((c) => c.role !== "viewer");
            if (circle) setHelper({ name: me.name, patient: circle.patient });
          }
        } catch {
          /* fall through to the tablet flow */
        }
      }
      // 2. Otherwise this screen is a tablet: anonymous sign-in + pairing.
      setIdentity(deviceIdentity);
      off = deviceIdentity.onChange(async (u) => {
        if (!u) {
          try {
            await deviceIdentity.signInDevice();
          } catch (e) {
            setState({ status: "error", error: friendlyError(e) });
          }
          return;
        }
        check();
      });
    })();
    return () => {
      off?.();
      clearTimeout(poll.current);
    };
  }, []);

  async function check() {
    clearTimeout(poll.current);
    try {
      const me = await api.get("/devices/me");
      if (me.paired) {
        setSession({ identity: deviceIdentity, patient: me.patient, viaGoogle: false });
        setState({ status: "paired", patient: me.patient });
        poll.current = setTimeout(check, 60000); // notice an unpairing within a minute
        return;
      }
      const code = await api.post("/devices/pair-code");
      setState({ status: "pairing", code: code.code, expires: code.expires_at });
      poll.current = setTimeout(check, 3000);
    } catch (e) {
      setState({ status: "error", error: e.message });
      poll.current = setTimeout(check, 5000);
    }
  }

  async function pairHere() {
    setPairing(true);
    try {
      await requestAs(caregiverIdentity, "POST", `/circles/${helper.patient.id}/devices`,
        { code: state.code, label: `${helper.patient.name}'s screen` });
      await check();
    } finally {
      setPairing(false);
    }
  }

  if (state.status === "paired") {
    return <SessionCtx.Provider value={session}>{children(state.patient)}</SessionCtx.Provider>;
  }
  if (state.status === "loading") return <div className="patient-shell loading">Getting ready…</div>;

  const code = state.code ? `${state.code.slice(0, 3)} ${state.code.slice(3)}` : "";
  return (
    <div className="pair-screen">
      <div className="pair-card">
        <div className="pair-hello">Hello!</div>
        <p className="pair-lede">This screen is almost ready. Your family will set it up for you.</p>
        {state.status === "pairing" && (
          <>
            <div className="pair-label">Ask your family to type this number:</div>
            <div className="pair-code" aria-label={`Pairing number ${state.code.split("").join(" ")}`}>{code}</div>
          </>
        )}
        {state.status === "error" && <p className="pair-error">We're having trouble connecting. We'll keep trying.</p>}
        {helper && state.status === "pairing" && (
          <button className="btn-big btn-primary" onClick={pairHere} disabled={pairing}>
            {pairing ? "Setting up…" : `I'm ${helper.name || "family"}: set this up for ${helper.patient.name}`}
          </button>
        )}
      </div>
      <p className="pair-help">
        For family: sign in to Pehchaan, open <b>Family &amp; devices</b>, choose <b>Pair a tablet</b> and enter the
        number above. This screen then stays signed in, and you can unpair it any time. Or the patient can{" "}
        <Link to="/login?next=/patient">sign in with Google</Link> using the email their caretaker added.
      </p>
    </div>
  );
}
