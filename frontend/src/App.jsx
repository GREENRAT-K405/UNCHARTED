import { Link, Route, Routes } from "react-router-dom";
import PatientHome from "./pages/PatientHome.jsx";
import CallPage from "./pages/CallPage.jsx";
import JoinPage from "./pages/JoinPage.jsx";
import Caregiver from "./pages/Caregiver.jsx";

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
        <div className="landing-links">
          <Link className="landing-card" to="/patient">
            <span className="lc-title">Patient screen</span>
            <span className="lc-sub">Aaji's home: big faces, one tap to call</span>
          </Link>
          <Link className="landing-card" to="/caregiver">
            <span className="lc-title">Caregiver dashboard</span>
            <span className="lc-sub">Review memories, inspect briefs, set limits</span>
          </Link>
        </div>
        <p className="fine">Family members join calls from the link the patient's call creates (/join/…).</p>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/patient" element={<PatientHome />} />
      <Route path="/call/:callId" element={<CallPage role="patient" />} />
      <Route path="/join/:callId" element={<JoinPage />} />
      <Route path="/caregiver" element={<Caregiver />} />
    </Routes>
  );
}
