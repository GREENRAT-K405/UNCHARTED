import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, setIdentity } from "./api.js";
import { caregiverIdentity } from "./identity.js";

const AuthCtx = createContext(null);

/** Caregiver session: the Firebase user plus the server's view of them (circles and roles from MongoDB). */
export function CaregiverAuthProvider({ children }) {
  const [user, setUser] = useState(undefined); // undefined = still loading
  const [me, setMe] = useState(null);
  const [error, setError] = useState(null);

  setIdentity(caregiverIdentity);

  const refreshMe = useCallback(async () => {
    const m = await api.get("/me");
    setMe(m);
    return m;
  }, []);

  useEffect(() => caregiverIdentity.onChange(async (u) => {
    setUser(u);
    setMe(null);
    setError(null);
    if (u && !u.anonymous) {
      try {
        setMe(await api.post("/auth/session"));
      } catch (e) {
        setError(e.message);
      }
    }
  }), []);

  const signOut = useCallback(async () => {
    await caregiverIdentity.signOut();
    try { localStorage.removeItem("cg-circle"); } catch {}
  }, []);

  return (
    <AuthCtx.Provider value={{ user, me, error, refreshMe, signOut, identity: caregiverIdentity }}>
      {children}
    </AuthCtx.Provider>
  );
}

export function useCaregiverAuth() {
  return useContext(AuthCtx);
}
