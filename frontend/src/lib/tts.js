// Read-aloud with the browser's speechSynthesis; prefers an Indian English voice.
let voice = null;

function pickVoice() {
  const voices = window.speechSynthesis?.getVoices() || [];
  voice =
    voices.find((v) => v.lang === "en-IN") ||
    voices.find((v) => v.lang.startsWith("en") && /india/i.test(v.name)) ||
    voices.find((v) => v.lang.startsWith("en")) ||
    null;
}
if (typeof window !== "undefined" && window.speechSynthesis) {
  pickVoice();
  window.speechSynthesis.onvoiceschanged = pickVoice;
}

export function speak(text, { rate = 0.88 } = {}) {
  if (!window.speechSynthesis || !text) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  if (voice) u.voice = voice;
  u.lang = voice?.lang || "en-IN";
  u.rate = rate; // a little slower than default
  window.speechSynthesis.speak(u);
}

export function stopSpeaking() {
  window.speechSynthesis?.cancel();
}
