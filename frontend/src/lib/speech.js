// Web Speech API helpers. Each participant transcribes only their own microphone,
// so speaker attribution comes for free. Chrome-only; everything degrades to typing.

const SR = typeof window !== "undefined" ? window.SpeechRecognition || window.webkitSpeechRecognition : null;
export const speechSupported = !!SR;

/** Continuous recognizer that restarts itself (Chrome ends sessions after silence). */
export function createTranscriber({ lang = "en-IN", onFinal, onInterim, onError }) {
  if (!SR) return null;
  let rec = null;
  let active = false;

  function begin() {
    rec = new SR();
    rec.lang = lang;
    rec.continuous = true;
    rec.interimResults = true;
    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) onFinal?.(r[0].transcript.trim());
        else interim += r[0].transcript;
      }
      onInterim?.(interim.trim());
    };
    rec.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") active = false;
      if (e.error !== "no-speech" && e.error !== "aborted") onError?.(e.error);
    };
    rec.onend = () => {
      if (active) setTimeout(() => active && begin(), 200);
    };
    try {
      rec.start();
    } catch {}
  }

  return {
    start() {
      if (active) return;
      active = true;
      begin();
    },
    stop() {
      active = false;
      try {
        rec?.stop();
      } catch {}
    },
    get active() {
      return active;
    },
  };
}

/** One utterance, e.g. "Call Riya". Resolves with the transcript or "". */
export function listenOnce(lang = "en-IN", timeoutMs = 7000) {
  return new Promise((resolve) => {
    if (!SR) return resolve("");
    const rec = new SR();
    rec.lang = lang;
    rec.interimResults = false;
    rec.maxAlternatives = 3;
    let done = false;
    const finish = (t) => {
      if (done) return;
      done = true;
      try {
        rec.stop();
      } catch {}
      resolve(t);
    };
    rec.onresult = (e) => finish(e.results[0][0].transcript);
    rec.onerror = () => finish("");
    rec.onend = () => finish("");
    setTimeout(() => finish(""), timeoutMs);
    rec.start();
  });
}
