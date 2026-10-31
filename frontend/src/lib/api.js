async function request(method, path, body) {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {}
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  get: (p) => request("GET", p),
  post: (p, b = {}) => request("POST", p, b),
  patch: (p, b) => request("PATCH", p, b),
  del: (p) => request("DELETE", p),
};

let patientPromise = null;
export function getPatient() {
  patientPromise ??= api.get("/patients").then((ps) => ps[0]);
  return patientPromise;
}
export function resetPatientCache() {
  patientPromise = null;
}

export function wsUrl(path) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}${path}`;
}

/** WebSocket that reconnects with backoff. Returns { send, close }. */
export function openSocket(path, onMessage, onState) {
  let ws;
  let closed = false;
  let delay = 500;
  const queue = [];
  function connect() {
    ws = new WebSocket(wsUrl(path));
    ws.onopen = () => {
      delay = 500;
      onState?.("open");
      while (queue.length) ws.send(queue.shift());
    };
    ws.onmessage = (e) => onMessage(JSON.parse(e.data));
    ws.onclose = (e) => {
      onState?.("closed");
      if (!closed && e.code !== 4000 && e.code !== 4404) {
        setTimeout(connect, delay);
        delay = Math.min(delay * 2, 8000);
      }
    };
  }
  connect();
  return {
    send(msg) {
      const s = JSON.stringify(msg);
      if (ws.readyState === WebSocket.OPEN) ws.send(s);
      else queue.push(s);
    },
    close() {
      closed = true;
      ws.close();
    },
  };
}
