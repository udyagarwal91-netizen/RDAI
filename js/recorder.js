// Records the whole shop visit as one audio file (no live speech-to-text).
// Keeps the screen awake while recording, and keeps the last recording in
// IndexedDB so a network failure or a reload never loses a conversation.

const MIME_CHOICES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus', 'audio/aac'];

export const recordingSupported = typeof window !== 'undefined'
  && !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);

export class Recorder {
  constructor({ onTick, onState } = {}) {
    this.onTick = onTick;
    this.onState = onState;
    this.rec = null;
    this.chunks = [];
    this.started = 0;
    this.pausedMs = 0;
  }

  get active() { return !!this.rec && this.rec.state !== 'inactive'; }

  async start() {
    if (!recordingSupported) throw new Error('This browser cannot record audio. Use Chrome or Safari on a phone.');
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    const mimeType = MIME_CHOICES.find((m) => MediaRecorder.isTypeSupported?.(m));
    // ~24 kbps opus: a 30-minute visit is about 5 MB
    this.rec = new MediaRecorder(this.stream, { ...(mimeType ? { mimeType } : {}), audioBitsPerSecond: 24000 });
    this.chunks = [];
    this.rec.ondataavailable = (e) => { if (e.data && e.data.size) this.chunks.push(e.data); };
    this.rec.start(1000);
    this.started = Date.now();
    this.timer = setInterval(() => this.onTick?.(Date.now() - this.started), 500);
    this._keepAwake();
    this.onState?.('recording');
  }

  /** @returns {Promise<Blob>} the whole conversation */
  stop() {
    return new Promise((resolve) => {
      if (!this.rec) { resolve(null); return; }
      this.rec.onstop = () => {
        const type = (this.rec.mimeType || this.chunks[0]?.type || 'audio/webm').split(';')[0];
        const blob = new Blob(this.chunks, { type });
        this._cleanup();
        this.onState?.('idle');
        resolve(blob);
      };
      try { this.rec.stop(); } catch { this._cleanup(); resolve(null); }
    });
  }

  async _keepAwake() {
    try { this.wake = await navigator.wakeLock?.request('screen'); } catch { /* not allowed: screen may sleep */ }
    this._onVisible = () => {
      if (document.visibilityState === 'visible' && this.active && !this.wake) this._keepAwake();
    };
    document.addEventListener('visibilitychange', this._onVisible);
  }

  _cleanup() {
    clearInterval(this.timer);
    this.stream?.getTracks().forEach((t) => t.stop());
    try { this.wake?.release(); } catch { /* already released */ }
    this.wake = null;
    if (this._onVisible) document.removeEventListener('visibilitychange', this._onVisible);
    this.rec = null;
  }
}

export function formatDuration(ms) {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

// ---------------------------------------------------------------------------
// Keep the last recording on the phone (IndexedDB)
// ---------------------------------------------------------------------------
function db() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open('vob-audio', 1);
    req.onupgradeneeded = () => req.result.createObjectStore('rec');
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function saveRecording(blob) {
  try {
    const d = await db();
    await new Promise((res, rej) => {
      const tx = d.transaction('rec', 'readwrite');
      tx.objectStore('rec').put({ blob, at: Date.now() }, 'last');
      tx.oncomplete = res;
      tx.onerror = () => rej(tx.error);
    });
  } catch { /* storage blocked: recording still in memory */ }
}

export async function loadRecording() {
  try {
    const d = await db();
    return await new Promise((res) => {
      const req = d.transaction('rec').objectStore('rec').get('last');
      req.onsuccess = () => res(req.result || null);
      req.onerror = () => res(null);
    });
  } catch { return null; }
}
