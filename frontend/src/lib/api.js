// Where the backend lives. Empty (local dev): same origin, through Vite's proxy. Deployed with the
// frontend on another host (Vercel), VITE_API_URL points at the backend so WebSockets reach it directly.
const API_ORIGIN = (import.meta.env.VITE_API_URL || "").replace(/\/+$/, "");

// Every request carries the ID token of whichever identity the current page uses
// (caregiver dashboard -> caregiver; patient screens -> paired device; join page -> none).
let tokenGetter = null;
export function setIdentity(identity) {
  tokenGetter = identity ? () => identity.getToken() : null;
}

/** One request as a specific identity, regardless of the page's identity. */
export async function requestAs(identity, method, path, body) {
  return request(method, path, body, await identity.getToken());
}

async function request(method, path, body, tokenOverride) {
  const headers = {};
  if (body) headers["Content-Type"] = "application/json";
  const token = tokenOverride ?? (tokenGetter ? await tokenGetter() : null);
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${API_ORIGIN}/api${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {}
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

export const api = {
  get: (p) => request("GET", p),
  post: (p, b = {}) => request("POST", p, b),
  patch: (p, b) => request("PATCH", p, b),
  del: (p) => request("DELETE", p),
};

export function wsUrl(path) {
  if (API_ORIGIN) return API_ORIGIN.replace(/^http/, "ws") + path;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}${path}`;
}

/**
 * WebSocket that authenticates with its first frame (tokens never go in URLs) and
 * reconnects with backoff. `hello` is an async function returning the auth frame.
 */
export function openSocket(path, onMessage, onState, hello) {
  let ws;
  let closed = false;
  let delay = 500;
  let ready = false; // true once the auth frame has gone out on the current connection
  const queue = [];
  function connect() {
    ws = new WebSocket(wsUrl(path));
    ready = false;
    ws.onopen = async () => {
      delay = 500;
      if (hello) ws.send(JSON.stringify(await hello()));
      ready = true;
      onState?.("open");
      while (queue.length) ws.send(queue.shift());
    };
    ws.onmessage = (e) => onMessage(JSON.parse(e.data));
    ws.onclose = (e) => {
      onState?.("closed");
      if (!closed && ![4000, 4403, 4404].includes(e.code)) {
        setTimeout(connect, delay);
        delay = Math.min(delay * 2, 8000);
      }
    };
  }
  connect();
  return {
    send(msg) {
      const s = JSON.stringify(msg);
      if (ready && ws.readyState === WebSocket.OPEN) ws.send(s);
      else queue.push(s);
    },
    close() {
      closed = true;
      ws.close();
    },
  };
}
