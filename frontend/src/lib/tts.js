// Read-aloud with the browser's speechSynthesis, in the patient's language. Prefers an exact voice
// for the language (hi-IN), then any voice of that language; without one it falls back to an Indian
// English voice, and voiceStatus() tells the caregiver dashboard why.
//
// Listeners hear when speech starts and stops, so the call screen can pause speech recognition:
// nothing the app reads aloud may reach the transcript.
const listeners = new Set();
let current = 0; // id of the utterance that owns the "speaking" state

function voices() {
  return window.speechSynthesis?.getVoices() || [];
}

function englishVoice(vs) {
  return vs.find((v) => v.lang === "en-IN") || vs.find((v) => v.lang.startsWith("en") && /india/i.test(v.name)) ||
    vs.find((v) => v.lang.startsWith("en")) || null;
}

function voiceFor(lang) {
  const vs = voices();
  const base = (lang || "en-IN").split("-")[0];
  const exact = vs.find((v) => v.lang.replace("_", "-") === lang) || vs.find((v) => v.lang.toLowerCase().startsWith(base));
  return base === "en" ? englishVoice(vs) || exact || null : exact || null;
}

/** Resolves once the browser has loaded its voice list (Chrome loads it asynchronously). */
export function whenVoicesReady(timeoutMs = 2500) {
  return new Promise((resolve) => {
    if (!window.speechSynthesis || voices().length) return resolve();
    const done = () => resolve();
    window.speechSynthesis.addEventListener?.("voiceschanged", done, { once: true });
    setTimeout(done, timeoutMs);
  });
}

export function voiceStatus(lang = "en-IN") {
  const v = voiceFor(lang);
  const used = v || englishVoice(voices());
  return { language: lang, voice_found: !!v, voice_name: used?.name || null, voice_lang: used?.lang || "en-IN",
    recognition: !!(window.SpeechRecognition || window.webkitSpeechRecognition) };
}

export function onSpeaking(cb) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

function emit(speaking, text = "") {
  listeners.forEach((cb) => cb(speaking, text));
}

export function speak(text, { rate = 0.88, lang = "en-IN" } = {}) {
  if (!window.speechSynthesis || !text) return;
  window.speechSynthesis.cancel();
  const id = ++current;
  const u = new SpeechSynthesisUtterance(text);
  const v = voiceFor(lang) || voiceFor("en-IN");
  if (v) u.voice = v;
  u.lang = v?.lang || "en-IN";
  u.rate = rate; // a little slower than default
  const end = () => id === current && emit(false);
  u.onend = end;
  u.onerror = end;
  emit(true, text); // before the audio starts, so recognition is already paused
  window.speechSynthesis.speak(u);
}

export function stopSpeaking() {
  current++;
  window.speechSynthesis?.cancel();
  emit(false);
}
