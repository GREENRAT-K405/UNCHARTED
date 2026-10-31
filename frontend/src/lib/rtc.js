// Minimal 1:1 WebRTC over our own WebSocket signaling. The patient side always offers;
// the contact side answers. Either side rebuilds its peer connection when the other rejoins.

const ICE = [{ urls: ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"] }];

export class Peer {
  constructor({ role, send, onRemoteStream, onState }) {
    this.role = role;
    this.send = send;
    this.onRemoteStream = onRemoteStream;
    this.onState = onState;
    this.localStream = null;
    this.pc = null;
    this.pendingIce = [];
  }

  async startMedia() {
    try {
      this.localStream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: { echoCancellation: true, noiseSuppression: true },
      });
    } catch {
      try {
        this.localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch {
        this.localStream = null; // no devices or blocked; the call UI still works with typed transcript
      }
    }
    return this.localStream;
  }

  _build() {
    this.pc?.close();
    this.pendingIce = [];
    const pc = new RTCPeerConnection({ iceServers: ICE });
    this.localStream?.getTracks().forEach((t) => pc.addTrack(t, this.localStream));
    if (!this.localStream) {
      pc.addTransceiver("video", { direction: "recvonly" });
      pc.addTransceiver("audio", { direction: "recvonly" });
    }
    pc.ontrack = (e) => this.onRemoteStream?.(e.streams[0]);
    pc.onicecandidate = (e) => e.candidate && this.send({ candidate: e.candidate });
    pc.onconnectionstatechange = () => this.onState?.(pc.connectionState);
    this.pc = pc;
    return pc;
  }

  async call() {
    const pc = this._build();
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    this.send({ sdp: pc.localDescription });
  }

  async handle(data) {
    if (data.sdp) {
      if (data.sdp.type === "offer") {
        const pc = this._build();
        await pc.setRemoteDescription(data.sdp);
        await this._flushIce();
        const answer = await pc.createAnswer();
        await pc.setLocalDescription(answer);
        this.send({ sdp: pc.localDescription });
      } else if (this.pc && this.pc.signalingState === "have-local-offer") {
        await this.pc.setRemoteDescription(data.sdp);
        await this._flushIce();
      }
    } else if (data.candidate) {
      if (this.pc?.remoteDescription) {
        try {
          await this.pc.addIceCandidate(data.candidate);
        } catch {}
      } else this.pendingIce.push(data.candidate);
    }
  }

  async _flushIce() {
    for (const c of this.pendingIce.splice(0)) {
      try {
        await this.pc.addIceCandidate(c);
      } catch {}
    }
  }

  setMuted(muted) {
    this.localStream?.getAudioTracks().forEach((t) => (t.enabled = !muted));
  }

  setCamera(on) {
    this.localStream?.getVideoTracks().forEach((t) => (t.enabled = on));
  }

  dropRemote() {
    this.pc?.close();
    this.pc = null;
  }

  close() {
    this.pc?.close();
    this.localStream?.getTracks().forEach((t) => t.stop());
  }
}
