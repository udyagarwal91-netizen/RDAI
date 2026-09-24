// Thin wrapper over the browser Web Speech API (Chrome / Edge / Android,
// Safari 14.5+). Keeps listening through pauses by restarting the engine
// whenever the browser stops it, until stop() is called.

const Recognition = typeof window !== 'undefined' && (window.SpeechRecognition || window.webkitSpeechRecognition);

export const speechSupported = !!Recognition;

export class Listener {
  constructor({ lang = 'en-IN', onFinal, onInterim, onState, onError }) {
    this.lang = lang;
    this.onFinal = onFinal;
    this.onInterim = onInterim;
    this.onState = onState;
    this.onError = onError;
    this.active = false;
    this.rec = null;
  }

  start() {
    if (!Recognition) throw new Error('Speech recognition is not available in this browser. Use Chrome on Android or desktop.');
    this.active = true;
    this._spawn();
    this._keepAwake();
    this.onState?.('listening');
  }

  // Chrome stops listening when the screen locks, so hold a screen wake
  // lock for the whole shop visit (re-taken if the tab comes back).
  async _keepAwake() {
    if (!('wakeLock' in navigator) || !this.active) return;
    try {
      this.wake = await navigator.wakeLock.request('screen');
    } catch { /* battery saver or unsupported: screen may still sleep */ }
    if (!this._onVisible) {
      this._onVisible = () => {
        if (document.visibilityState === 'visible' && this.active) {
          this._keepAwake();
          if (!this.rec) this._spawn();
        }
      };
      document.addEventListener('visibilitychange', this._onVisible);
    }
  }

  stop() {
    this.active = false;
    try { this.wake?.release(); } catch { /* already released */ }
    this.wake = null;
    try { this.rec?.stop(); } catch { /* already stopped */ }
    this.rec = null;
    this.onInterim?.('');
    this.onState?.('idle');
  }

  _spawn() {
    const rec = new Recognition();
    rec.lang = this.lang;
    rec.continuous = true;
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    rec.onresult = (e) => {
      let interim = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) this.onFinal?.(r[0].transcript.trim());
        else interim += r[0].transcript;
      }
      this.onInterim?.(interim);
    };
    rec.onerror = (e) => {
      if (e.error === 'no-speech' || e.error === 'aborted') return;
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
        this.active = false;
        this.onError?.('Microphone permission was denied. Allow the microphone for this site and try again.');
        this.onState?.('idle');
        return;
      }
      this.onError?.(`Speech error: ${e.error}`);
    };
    rec.onend = () => {
      this.rec = null;
      if (this.active && document.visibilityState === 'visible') {
        // Browsers end recognition after silence or ~60s; carry on.
        setTimeout(() => { if (this.active) this._spawn(); }, 150);
      }
    };
    this.rec = rec;
    rec.start();
  }
}
