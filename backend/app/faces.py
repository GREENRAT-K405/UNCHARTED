"""Face matching on 128-number descriptors (computed in the browser by face-api).

The browser turns a face into 128 numbers; two photos of the same person land close together
(small Euclidean distance), different people land far apart. We keep the descriptors in MongoDB
and decide here, so thresholds are tuned in one place and the stored data is the source of truth.

Accuracy rules (a wrong name is worse than "not sure" for someone with Alzheimer's):
  * several frames from the live video are compared at once, not a single frame
  * the average of those frames must be close to a stored photo of exactly one person
  * it must beat the second-closest person by a clear margin
  * most individual frames must agree with the average
"""
import math

import numpy as np

from .config import settings

DIM = 128
MAX_DESCRIPTORS = 8


def clean_descriptor(d) -> list[float]:
    if not isinstance(d, (list, tuple)) or len(d) != DIM:
        raise ValueError(f"a face descriptor has {DIM} numbers")
    out = [float(x) for x in d]
    if any(not math.isfinite(x) or abs(x) > 3 for x in out):
        raise ValueError("invalid face descriptor")
    return [round(x, 5) for x in out]


def clean_descriptors(items) -> list[list[float]]:
    if not isinstance(items, (list, tuple)) or not (1 <= len(items) <= MAX_DESCRIPTORS):
        raise ValueError(f"send between 1 and {MAX_DESCRIPTORS} face descriptors")
    return [clean_descriptor(d) for d in items]


def confidence(distance: float) -> int:
    """Distance -> a 0-100 score. 50 is the match threshold; closer is higher."""
    return int(round(100 / (1 + math.exp((distance - settings.face_match_threshold) * 14))))


def ranked(vector: np.ndarray, gallery: list[dict]) -> list[tuple[str, float]]:
    """Closest stored photo per person, nearest first. gallery: [{id, faces: [{descriptor}]}]."""
    out = []
    for person in gallery:
        dists = [float(np.linalg.norm(vector - np.asarray(f["descriptor"], dtype=np.float32)))
                 for f in person.get("faces", [])]
        if dists:
            out.append((str(person["id"]), min(dists)))
    out.sort(key=lambda x: x[1])
    return out


def decide(descriptors: list[list[float]], gallery: list[dict]) -> dict:
    """Returns {status, contact_id?, distance?, confidence?, runner_up?, frames_agree?}.

    status: matched | unsure | unknown | no_gallery
    """
    if not gallery or not any(p.get("faces") for p in gallery):
        return {"status": "no_gallery"}
    frames = np.asarray(descriptors, dtype=np.float32)
    mean = frames.mean(axis=0)
    ranking = ranked(mean, gallery)
    best_id, best = ranking[0]
    runner_up = ranking[1] if len(ranking) > 1 else None

    per_frame = [ranked(f, gallery)[0] for f in frames]
    agree = sum(1 for cid, d in per_frame if cid == best_id and d <= settings.face_unsure_threshold) / len(per_frame)

    result = {"contact_id": best_id, "distance": round(best, 4), "confidence": confidence(best),
              "frames_agree": round(agree, 2),
              "runner_up": {"contact_id": runner_up[0], "distance": round(runner_up[1], 4)} if runner_up else None}
    clear_margin = runner_up is None or (runner_up[1] - best) >= settings.face_match_margin
    if best <= settings.face_match_threshold and clear_margin and agree >= 0.6:
        return {"status": "matched", **result}
    if best <= settings.face_unsure_threshold and agree >= 0.5:
        return {"status": "unsure", **result}
    return {"status": "unknown", **result}


def short(text: str | None, limit: int = 170) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "…"
