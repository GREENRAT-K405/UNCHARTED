import { Link, Route, Routes } from "react-router-dom";
import PatientHome from "./pages/PatientHome.jsx";
import JoinPage from "./pages/JoinPage.jsx";
import Caregiver from "./pages/Caregiver.jsx";
import Login from "./pages/Login.jsx";
import PatientGate, { PatientCall } from "./pages/PatientGate.jsx";
import RelativeHome, { RelativeCall } from "./pages/RelativeHome.jsx";
import { CaregiverAuthProvider } from "./lib/auth.jsx";

function Landing() {
  return (
    <div className="landing">
      <div className="landing-inner">
        <div className="brand-mark" aria-hidden>
          <svg viewBox="0 0 64 64" width="56" height="56">
            <rect width="64" height="64" rx="16" fill="currentColor" />
            <circle cx="32" cy="25" r="10" fill="var(--paper)" />
            <path d="M14 52c2-10 9-15 18-15s16 5 18 15" fill="var(--paper)" />
          </svg>
        </div>
        <h1>Pehchaan</h1>
        <p className="lede">
          A video-calling companion for people living with Alzheimer's. It remembers who is calling and what you talked
          about, so every call starts with recognition.
        </p>
        <div className="landing-links three">
          <Link className="landing-card" to="/caregiver">
            <span className="lc-title">Caretaker</span>
            <span className="lc-sub">Add relatives with their photos, schedule meets, read past meets and notes</span>
          </Link>
          <Link className="landing-card" to="/patient">
            <span className="lc-title">Patient</span>
            <span className="lc-sub">Upcoming meets and one big Join button. The screen recognises who joins.</span>
          </Link>
          <Link className="landing-card" to="/relative">
            <span className="lc-title">Relative</span>
            <span className="lc-sub">See the patient's invites and scheduled meets, and join with one tap</span>
          </Link>
        </div>
        <p className="fine">Have a meeting code? <Link to="/join">Join as a guest</Link>.</p>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/patient" element={<PatientGate>{(patient) => <PatientHome patient={patient} />}</PatientGate>} />
      <Route path="/call/:callId" element={<PatientGate>{() => <PatientCall />}</PatientGate>} />
      <Route path="/join" element={<JoinPage />} />
      <Route path="/join/:callId" element={<JoinPage />} />
      <Route path="/relative" element={<CaregiverAuthProvider><RelativeHome /></CaregiverAuthProvider>} />
      <Route path="/meet/:callId" element={<CaregiverAuthProvider><RelativeCall /></CaregiverAuthProvider>} />
      <Route path="/login" element={<CaregiverAuthProvider><Login /></CaregiverAuthProvider>} />
      <Route path="/caregiver" element={<CaregiverAuthProvider><Caregiver /></CaregiverAuthProvider>} />
    </Routes>
  );
}
