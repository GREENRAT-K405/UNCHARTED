import { Link, Route, Routes } from "react-router-dom";
import PatientHome from "./pages/PatientHome.jsx";
import CallPage from "./pages/CallPage.jsx";
import JoinPage from "./pages/JoinPage.jsx";
import ContactLink from "./pages/ContactLink.jsx";
import FamilyHome from "./pages/FamilyHome.jsx";
import Caregiver from "./pages/Caregiver.jsx";
import Login from "./pages/Login.jsx";
import PatientGate from "./pages/PatientGate.jsx";
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
        <div className="landing-links">
          <Link className="landing-card" to="/patient">
            <span className="lc-title">Patient screen</span>
            <span className="lc-sub">Big faces, one tap to call. Sign in, or let family set it up</span>
          </Link>
          <Link className="landing-card" to="/family">
            <span className="lc-title">Family member</span>
            <span className="lc-sub">Sign in to get calls from your relative, share updates, and see what's coming up</span>
          </Link>
          <Link className="landing-card" to="/caregiver">
            <span className="lc-title">Caregiver dashboard</span>
            <span className="lc-sub">Sign in with Google to review memories and insights</span>
          </Link>
        </div>
        <p className="fine">Each family member has their own link (/c/…, under Contacts &amp; rules). It rings when the patient calls.</p>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/patient" element={<PatientGate>{(patient, session) => <PatientHome patient={patient} session={session} />}</PatientGate>} />
      <Route path="/call/:callId" element={<PatientGate>{() => <CallPage role="patient" />}</PatientGate>} />
      <Route path="/join/:callId" element={<JoinPage />} />
      <Route path="/c/:token" element={<ContactLink />} />
      <Route path="/family" element={<CaregiverAuthProvider><FamilyHome /></CaregiverAuthProvider>} />
      <Route path="/login" element={<CaregiverAuthProvider><Login /></CaregiverAuthProvider>} />
      <Route path="/caregiver" element={<CaregiverAuthProvider><Caregiver /></CaregiverAuthProvider>} />
    </Routes>
  );
}
