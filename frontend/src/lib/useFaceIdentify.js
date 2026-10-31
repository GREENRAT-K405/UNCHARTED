import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { cropFace, describeFaces, MIN_FACE_PX } from "./face.js";

const TICK_MS = 600; // how often we look at the remote video
const FRAMES = 4; // faces collected before asking the server who it is
const SEARCH_RETRY_MS = 4000;
const RECHECK_MS = 30000; // once identified, double-check now and then

/**
 * Watches the other person's video, collects a few good face frames and asks the server who it is.
 * The decision (and the saving of a new person) happens on the server; this only gathers evidence.
 *
 * phase: starting | searching | matched | unsure | new | unknown
 */
export function useFaceIdentify({ videoRef, callId, enabled, resetKey = 0 }) {
  const [state, setState] = useState({ phase: "starting", person: null });
  const stateRef = useRef(state);
  stateRef.current = state;

  useEffect(() => {
    if (!enabled || !callId) return;
    let stop = false;
    let frames = [];
    let lastAsk = 0;
    let busy = false;

    const wait = (ms) => new Promise((r) => setTimeout(r, ms));

    (async () => {
      setState({ phase: "starting", person: null });
      while (!stop) {
        const v = videoRef.current;
        const usable = v && v.readyState >= 2 && v.videoWidth > 0 && !v.paused;
        if (!usable) {
          await wait(TICK_MS);
          continue;
        }
        try {
          const faces = await describeFaces(v, { maxResults: 3 });
          const top = faces[0];
          if (top && top.box.width >= MIN_FACE_PX && top.score >= 0.75) {
            frames.push({ ...top, snap: null });
            if (frames.length === 1 || !frames[0].snap) frames[0].snap = cropFace(v, top.box, 200, 0.45);
            frames = frames.slice(-FRAMES);
          } else if (stateRef.current.phase === "starting") {
            setState({ phase: "searching", person: null });
          }
        } catch {
          /* a failed frame is fine, try the next */
        }

        const phase = stateRef.current.phase;
        const interval = phase === "matched" || phase === "new" ? RECHECK_MS : SEARCH_RETRY_MS;
        if (!busy && frames.length >= FRAMES && Date.now() - lastAsk > interval) {
          busy = true;
          lastAsk = Date.now();
          try {
            const best = frames.slice().sort((a, b) => b.score - a.score);
            const res = await api.post(`/calls/${callId}/identify`, {
              descriptors: best.map((f) => f.descriptor),
              snapshot: frames.find((f) => f.snap)?.snap || null,
            });
            if (!stop) setState({ phase: res.status === "no_gallery" ? "unknown" : res.status, ...res });
            frames = [];
          } catch {
            /* try again next interval */
          } finally {
            busy = false;
          }
        }
        await wait(TICK_MS);
      }
    })();

    return () => {
      stop = true;
    };
  }, [enabled, callId, resetKey]);

  return state;
}
