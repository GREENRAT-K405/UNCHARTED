// The patient's language: what the patient's screens say and read aloud. Caregiver and relative
// screens stay in English. Relation words ("your granddaughter") come from the server: in English
// from the contact, or in the patient's language from the brief once it is ready.

export const LANGUAGES = {
  "en-IN": "English",
  "hi-IN": "हिन्दी (Hindi)",
  "mr-IN": "मराठी (Marathi)",
  "gu-IN": "ગુજરાતી (Gujarati)",
};

const EN = {
  greeting: (h) => (h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening"),
  thisIs: (name, relation) => `This is ${name}, ${relation}.`,
  alsoWith: (name, people) => `Also with ${name}: ${people}.`,
  whoBtn: "Who is this?",
  whoAsk: "Who do you want to talk to?",
  calling: (name, relation) => `Calling ${name}, ${relation}.`,
  notHeard: "I didn't hear anything. You can tap a photo instead.",
  notFound: "I couldn't find that person. You can tap a photo instead.",
  busy: (name) => `${name} is busy right now. Let's try again later.`,
  someoneElse: "Let's call someone else today.",
  wouldLove: (name) => `${name} would love to hear from you.`,
  backHome: "Back home",
  joining: (name) => `${name} is coming…`,
  joinFamily: "Join the family call",
  callingFamily: "Waiting for your family…",
  familyBusy: "Nobody is free right now. Let's try again later.",
  talked: { today: "Talked today", yesterday: "Talked yesterday", weekday: (w) => `Talked on ${w}`, days: (d) => `Talked ${d} days ago` },
};

const PHRASES = {
  "en-IN": EN,
  "hi-IN": {
    greeting: (h) => (h < 12 ? "सुप्रभात" : h < 17 ? "नमस्ते" : "शुभ संध्या"),
    thisIs: (name, relation) => `यह ${name} हैं, ${relation}।`,
    alsoWith: (name, people) => `${name} के साथ ${people} भी हैं।`,
    whoBtn: "यह कौन है?",
    whoAsk: "आप किससे बात करेंगे?",
    calling: (name, relation) => `${name} को फ़ोन लगा रहे हैं, ${relation}।`,
    notHeard: "मुझे कुछ सुनाई नहीं दिया। आप फ़ोटो दबा सकते हैं।",
    notFound: "वह व्यक्ति नहीं मिला। आप फ़ोटो दबा सकते हैं।",
    busy: (name) => `${name} अभी व्यस्त हैं। थोड़ी देर बाद फिर कोशिश करेंगे।`,
    someoneElse: "चलिए, आज किसी और से बात करते हैं।",
    wouldLove: (name) => `${name} को आपसे बात करके बहुत खुशी होगी।`,
    backHome: "वापस चलें",
    joining: (name) => `${name} आ रहे हैं…`,
    joinFamily: "परिवार से बात करें",
    callingFamily: "परिवार को फ़ोन लगा रहे हैं…",
    familyBusy: "अभी कोई खाली नहीं है। थोड़ी देर बाद फिर कोशिश करेंगे।",
    talked: { today: "आज बात हुई", yesterday: "कल बात हुई", weekday: (w) => `${w} को बात हुई`, days: (d) => `${d} दिन पहले बात हुई` },
  },
  "mr-IN": {
    greeting: (h) => (h < 12 ? "सुप्रभात" : h < 17 ? "नमस्कार" : "शुभ संध्याकाळ"),
    thisIs: (name, relation) => `हे ${name} आहेत, ${relation}.`,
    alsoWith: (name, people) => `${name} सोबत ${people} पण आहेत.`,
    whoBtn: "हे कोण आहे?",
    whoAsk: "तुम्हाला कोणाशी बोलायचं आहे?",
    calling: (name, relation) => `${name} ला फोन लावत आहे, ${relation}.`,
    notHeard: "मला काही ऐकू आलं नाही. तुम्ही फोटोवर टॅप करू शकता.",
    notFound: "ती व्यक्ती सापडली नाही. तुम्ही फोटोवर टॅप करू शकता.",
    busy: (name) => `${name} आत्ता कामात आहेत. आपण थोड्या वेळाने पुन्हा प्रयत्न करू.`,
    someoneElse: "चला, आज दुसऱ्या कुणाशी तरी बोलूया.",
    wouldLove: (name) => `${name} ला तुमच्याशी बोलून खूप आनंद होईल.`,
    backHome: "परत जा",
    joining: (name) => `${name} येत आहेत…`,
    joinFamily: "कुटुंबाशी बोला",
    callingFamily: "कुटुंबाला फोन लावत आहे…",
    familyBusy: "आत्ता कोणीही मोकळं नाही. आपण थोड्या वेळाने पुन्हा प्रयत्न करू.",
    talked: { today: "आज बोलणं झालं", yesterday: "काल बोलणं झालं", weekday: (w) => `${w} ला बोलणं झालं`, days: (d) => `${d} दिवसांपूर्वी बोलणं झालं` },
  },
  "gu-IN": {
    greeting: (h) => (h < 12 ? "સુપ્રભાત" : h < 17 ? "નમસ્તે" : "શુભ સાંજ"),
    thisIs: (name, relation) => `આ ${name} છે, ${relation}.`,
    alsoWith: (name, people) => `${name} સાથે ${people} પણ છે.`,
    whoBtn: "આ કોણ છે?",
    whoAsk: "તમે કોની સાથે વાત કરવા માંગો છો?",
    calling: (name, relation) => `${name} ને ફોન કરી રહ્યા છીએ, ${relation}.`,
    notHeard: "મને કંઈ સંભળાયું નહીં. તમે ફોટો પર ટેપ કરી શકો છો.",
    notFound: "એ વ્યક્તિ મળી નહીં. તમે ફોટો પર ટેપ કરી શકો છો.",
    busy: (name) => `${name} અત્યારે વ્યસ્ત છે. થોડી વાર પછી ફરી પ્રયત્ન કરીશું.`,
    someoneElse: "ચાલો, આજે બીજા કોઈ સાથે વાત કરીએ.",
    wouldLove: (name) => `${name} ને તમારી સાથે વાત કરીને ખૂબ આનંદ થશે.`,
    backHome: "પાછા જાઓ",
    joining: (name) => `${name} આવી રહ્યા છે…`,
    joinFamily: "પરિવાર સાથે વાત કરો",
    callingFamily: "પરિવારને ફોન કરી રહ્યા છીએ…",
    familyBusy: "અત્યારે કોઈ ફ્રી નથી. થોડી વાર પછી ફરી પ્રયત્ન કરીશું.",
    talked: { today: "આજે વાત થઈ", yesterday: "ગઈકાલે વાત થઈ", weekday: (w) => `${w} એ વાત થઈ`, days: (d) => `${d} દિવસ પહેલાં વાત થઈ` },
  },
};

export function phrases(lang) {
  return PHRASES[lang] || EN;
}

/** "Talked yesterday", "Talked on Monday", from the patient's own point of view (local days). */
export function talkedWhen(iso, lang, now = new Date()) {
  const t = phrases(lang).talked;
  const day = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const then = new Date(iso);
  const days = Math.round((day(now) - day(then)) / 86400000);
  if (days <= 0) return t.today;
  if (days === 1) return t.yesterday;
  if (days < 7) return t.weekday(then.toLocaleDateString(lang, { weekday: "long" }));
  return t.days(days);
}

/** The patient's card as one sentence list: exactly what is shown is what is read aloud. */
export function cardSentences(view, lang) {
  if (!view?.name) return [];
  const p = phrases(lang);
  const relation = view.detail ? `${view.relation}, ${view.detail}` : view.relation;
  const out = [p.thisIs(view.name, relation)];
  if (view.memory) out.push(view.memory);
  if (view.alsoHere?.length) out.push(p.alsoWith(view.name, view.alsoHere.map((a) => `${a.name}, ${a.relation}`).join("; ")));
  return out;
}
