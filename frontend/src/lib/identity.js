// Identity: Firebase Auth when configured (VITE_FIREBASE_* in frontend/.env), otherwise a
// local "dev" identity that the backend accepts only while it runs without FIREBASE_PROJECT_ID.
//
// Two separate identities live side by side in one browser:
//   caregiver - Google or email/password sign-in
//   device    - anonymous sign-in for the patient's tablet (paired by a caregiver, never logs out)
// They use separate Firebase app instances, so signing in on /caregiver never signs the
// tablet out of /patient (or the other way round), even in the same browser.
import { initializeApp } from "firebase/app";
import {
  GoogleAuthProvider, createUserWithEmailAndPassword, getAuth, onAuthStateChanged, sendPasswordResetEmail,
  signInAnonymously, signInWithEmailAndPassword, signInWithPopup, signInWithRedirect, signOut, updateProfile,
} from "firebase/auth";

const env = import.meta.env;
const config = {
  apiKey: env.VITE_FIREBASE_API_KEY,
  authDomain: env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: env.VITE_FIREBASE_PROJECT_ID,
  appId: env.VITE_FIREBASE_APP_ID,
};
export const firebaseEnabled = !!config.apiKey;

const FRIENDLY_ERRORS = {
  "auth/invalid-credential": "That email and password don't match. Try again or reset your password.",
  "auth/wrong-password": "That email and password don't match. Try again or reset your password.",
  "auth/user-not-found": "There's no account with that email yet. Create one below.",
  "auth/email-already-in-use": "An account with this email already exists. Sign in instead.",
  "auth/weak-password": "Please use a password of at least 8 characters.",
  "auth/too-many-requests": "Too many attempts. Please wait a minute and try again.",
  "auth/popup-closed-by-user": "The Google window was closed before signing in.",
  "auth/operation-not-allowed": "This sign-in method isn't enabled in the Firebase project yet.",
  "auth/admin-restricted-operation": "Anonymous sign-in isn't enabled in the Firebase project yet.",
};
export function friendlyError(e) {
  return FRIENDLY_ERRORS[e?.code] || e?.message || "Something went wrong. Please try again.";
}

function firebaseIdentity(appName) {
  const app = initializeApp(config, appName);
  const auth = getAuth(app);
  return {
    mode: "firebase",
    get current() {
      const u = auth.currentUser;
      return u && { uid: u.uid, email: u.email, name: u.displayName, picture: u.photoURL, anonymous: u.isAnonymous };
    },
    onChange(cb) {
      return onAuthStateChanged(auth, (u) =>
        cb(u ? { uid: u.uid, email: u.email, name: u.displayName, picture: u.photoURL, anonymous: u.isAnonymous } : null));
    },
    async getToken() {
      return auth.currentUser ? auth.currentUser.getIdToken() : null;
    },
    async signInGoogle() {
      const provider = new GoogleAuthProvider();
      provider.setCustomParameters({ prompt: "select_account" });
      try {
        await signInWithPopup(auth, provider);
      } catch (e) {
        if (e.code === "auth/popup-blocked") return signInWithRedirect(auth, provider);
        throw e;
      }
    },
    signInEmail: (email, password) => signInWithEmailAndPassword(auth, email, password),
    async signUpEmail(name, email, password) {
      const cred = await createUserWithEmailAndPassword(auth, email, password);
      if (name) await updateProfile(cred.user, { displayName: name });
      await cred.user.getIdToken(true); // refresh so the token carries the name
    },
    resetPassword: (email) => sendPasswordResetEmail(auth, email),
    signInDevice: () => signInAnonymously(auth),
    signOut: () => signOut(auth),
  };
}

function devIdentity(kind) {
  const key = `pehchaan.dev.${kind}`;
  const listeners = new Set();
  const read = () => {
    try { return JSON.parse(localStorage.getItem(key)); } catch { return null; }
  };
  const write = (v) => {
    try { v ? localStorage.setItem(key, JSON.stringify(v)) : localStorage.removeItem(key); } catch {}
    listeners.forEach((cb) => cb(v));
  };
  window.addEventListener("storage", (e) => e.key === key && listeners.forEach((cb) => cb(read())));
  const rand = () => Math.random().toString(36).slice(2, 10);
  return {
    mode: "dev",
    get current() { return read(); },
    onChange(cb) {
      listeners.add(cb);
      cb(read());
      return () => listeners.delete(cb);
    },
    async getToken() {
      const u = read();
      if (!u) return null;
      return u.anonymous ? `dev-anon:${u.uid}` : `dev:${u.uid}|${u.email || ""}|${u.name || ""}`;
    },
    async signInGoogle() { throw new Error("Google sign-in needs Firebase. Add your Firebase config to frontend/.env."); },
    async signInEmail(email) {
      write({ uid: email.toLowerCase().replace(/[^a-z0-9]/g, "-"), email, name: email.split("@")[0], anonymous: false });
    },
    async signUpEmail(name, email) {
      write({ uid: email.toLowerCase().replace(/[^a-z0-9]/g, "-"), email, name, anonymous: false });
    },
    async resetPassword() {},
    async signInDevice() { write({ uid: rand(), anonymous: true }); },
    async signOut() { write(null); },
  };
}

export const caregiverIdentity = firebaseEnabled ? firebaseIdentity("caregiver") : devIdentity("caregiver");
export const deviceIdentity = firebaseEnabled ? firebaseIdentity("patient-device") : devIdentity("device");
