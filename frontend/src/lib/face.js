// Face recognition in the browser (face-api). A face becomes 128 numbers (a "descriptor"); the same
// person's photos land close together. Descriptors are sent to the server and stored in MongoDB;
// the server decides who someone is (see backend/app/faces.py). Models live in /public/models and
// are cached by the browser after the first load (about 13 MB).

let loading = null;

export function loadFaceModels() {
  loading ??= (async () => {
    const faceapi = await import("@vladmandic/face-api");
    const ok = await faceapi.tf.setBackend("webgl").catch(() => false);
    if (!ok) await faceapi.tf.setBackend("cpu");
    await faceapi.tf.ready();
    await Promise.all([
      faceapi.nets.ssdMobilenetv1.loadFromUri("/models"),
      faceapi.nets.faceLandmark68Net.loadFromUri("/models"),
      faceapi.nets.faceRecognitionNet.loadFromUri("/models"),
    ]);
    return faceapi;
  })().catch((e) => {
    loading = null; // allow a retry
    throw e;
  });
  return loading;
}

const MIN_SCORE = 0.6; // detector confidence for a usable face
export const MIN_FACE_PX = 70; // narrower than this in the source image and the fingerprint gets unreliable

function sourceSize(src) {
  return { w: src.videoWidth || src.naturalWidth || src.width, h: src.videoHeight || src.naturalHeight || src.height };
}

/** All faces in an image or video frame, biggest first: [{descriptor, box, score}]. */
export async function describeFaces(src, { maxResults = 5 } = {}) {
  const faceapi = await loadFaceModels();
  const opts = new faceapi.SsdMobilenetv1Options({ minConfidence: MIN_SCORE, maxResults });
  const found = await faceapi.detectAllFaces(src, opts).withFaceLandmarks().withFaceDescriptors();
  return found
    .map((f) => ({
      descriptor: Array.from(f.descriptor),
      box: { x: f.detection.box.x, y: f.detection.box.y, width: f.detection.box.width, height: f.detection.box.height },
      score: f.detection.score,
    }))
    .sort((a, b) => b.box.width * b.box.height - a.box.width * a.box.height);
}

/** Why a detected face might not be good enough to enrol, in plain words (null = good). */
export function faceProblem(face, count) {
  if (!face) return "No face found. Use a clear, front-facing photo with the face well lit.";
  if (face.box.width < MIN_FACE_PX) return "The face is too small. Move closer or crop the photo.";
  if (face.score < 0.75) return "The face isn't clear enough. Try better lighting and look at the camera.";
  if (count > 1) return "More than one face found; the biggest one will be used.";
  return null;
}

/** Square JPEG (data URL) around a face with some margin: used as the person's profile picture. */
export function cropFace(src, box, size = 256, margin = 0.5) {
  const { w, h } = sourceSize(src);
  const side = Math.max(box.width, box.height) * (1 + margin);
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  const sx = Math.max(0, Math.min(w - side, cx - side / 2));
  const sy = Math.max(0, Math.min(h - side, cy - side / 2));
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  canvas.getContext("2d").drawImage(src, sx, sy, Math.min(side, w), Math.min(side, h), 0, 0, size, size);
  return canvas.toDataURL("image/jpeg", 0.85);
}

/** Load an image file, shrinking very large photos so detection stays quick. */
export function loadImageFile(file, maxSide = 1400) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      const scale = Math.min(1, maxSide / Math.max(img.naturalWidth, img.naturalHeight));
      if (scale === 1) return resolve(img);
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.naturalWidth * scale);
      canvas.height = Math.round(img.naturalHeight * scale);
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      resolve(canvas);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("That file isn't an image we can read."));
    };
    img.src = url;
  });
}

/** Turn a photo into what we store: {descriptor, photo} or {error}. */
export async function enrolPhoto(file) {
  let img;
  try {
    img = await loadImageFile(file);
  } catch (e) {
    return { error: e.message };
  }
  const faces = await describeFaces(img);
  const best = faces[0];
  const problem = faceProblem(best, faces.length);
  if (!best || best.box.width < MIN_FACE_PX || best.score < 0.75) return { error: problem };
  return { descriptor: best.descriptor, photo: cropFace(img, best.box), warning: problem, score: best.score };
}

/** Same, from a canvas or video frame (the webcam "take a photo" button). */
export async function enrolFrame(src) {
  const faces = await describeFaces(src);
  const best = faces[0];
  const problem = faceProblem(best, faces.length);
  if (!best || best.box.width < MIN_FACE_PX || best.score < 0.75) return { error: problem };
  return { descriptor: best.descriptor, photo: cropFace(src, best.box), warning: problem, score: best.score };
}
