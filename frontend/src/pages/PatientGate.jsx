import { useEffect, useRef, useState } from "react";
import { api, requestAs, setIdentity } from "../lib/api.js";
import { caregiverIdentity, deviceIdentity, friendlyError } from "../lib/identity.js";

/**
 * The patient's tablet signs in anonymously and stays signed in. Until a caregiver pairs it
 * (by typing the number shown here into their dashboard), it shows a calm, reassuring
 * screen instead of a login form: the patient never has to remember a password.
 */
export default function PatientGate({ children }) {
  setIdentity(deviceIdentity);
  const [state, setState] = useState({ status: "loading" });
  const [helper, setHelper] = useState(null); // a caregiver signed in on this same browser
  const [pairing, setPairing] = useState(false);
  const poll = useRef(null);

  useEffect(() => {
    const off = deviceIdentity.onChange(async (u) => {
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
        setState({ status: "paired", patient: me.patient, device: me.device });
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

  // Offer one-tap pairing when a caregiver is already signed in on this browser.
  useEffect(() => {
    if (state.status !== "pairing" || helper) return;
    const cg = caregiverIdentity.current;
    if (!cg || cg.anonymous) return;
    requestAs(caregiverIdentity, "GET", "/me").then((m) => {
      const circle = m.circles.find((c) => c.role !== "viewer");
      if (circle) setHelper({ name: m.name, patient: circle.patient });
    }).catch(() => {});
  }, [state.status]);

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

  if (state.status === "paired") return children(state.patient);
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
        {helper && (
          <button className="btn-big btn-primary" onClick={pairHere} disabled={pairing}>
            {pairing ? "Setting up…" : `I'm ${helper.name || "family"}: set this up for ${helper.patient.name}`}
          </button>
        )}
      </div>
      <p className="pair-help">
        For family: sign in to Pehchaan on your phone, open <b>Family &amp; devices</b>, choose <b>Pair a tablet</b> and
        enter the number above. This screen will then stay signed in. You can unpair it any time.
      </p>
    </div>
  );
}
