/**
 * AudioEngine — procedural Web Audio synthesis for ambient sound design.
 *
 * All 8 sound types from the WorkspaceRenderer SoundEvent system are
 * synthesized using OscillatorNode + GainNode — zero audio files.
 *
 * Default: muted on first visit. User must opt-in via volume control.
 */

import type { SoundEvent } from './workspace-renderer';

const STORAGE_KEY = 'station-audio-volume';
const STORAGE_MUTED_KEY = 'station-audio-muted';

class AudioEngine {
  private ctx: AudioContext | null = null;
  private masterGain: GainNode | null = null;
  private volume = 0.3;
  private muted = true;
  private initialized = false;

  constructor() {
    // Restore preferences from localStorage
    const savedVol = localStorage.getItem(STORAGE_KEY);
    if (savedVol != null) this.volume = parseFloat(savedVol);
    const savedMuted = localStorage.getItem(STORAGE_MUTED_KEY);
    this.muted = savedMuted === null ? true : savedMuted === 'true';
  }

  /** Lazily initialize AudioContext on first user gesture */
  private init() {
    if (this.initialized) return;
    try {
      this.ctx = new AudioContext();
      this.masterGain = this.ctx.createGain();
      this.masterGain.gain.value = this.muted ? 0 : this.volume;
      this.masterGain.connect(this.ctx.destination);
      this.initialized = true;
    } catch {
      // Web Audio not available
    }
  }

  /** Ensure context is running (after user gesture) */
  private async ensureRunning() {
    if (!this.ctx) this.init();
    if (this.ctx?.state === 'suspended') {
      await this.ctx.resume();
    }
  }

  // --- Public API ---

  async play(event: SoundEvent) {
    if (this.muted) return;
    await this.ensureRunning();
    if (!this.ctx || !this.masterGain) return;

    const t = this.ctx.currentTime;
    const intensity = event.intensity;

    switch (event.type) {
      case 'tool_tick':
        this.playToolTick(t, intensity);
        break;
      case 'connect_chirp':
        this.playConnectChirp(t, intensity);
        break;
      case 'employee_spawn':
        this.playEmployeeSpawn(t, intensity);
        break;
      case 'employee_complete':
        this.playEmployeeComplete(t, intensity);
        break;
      case 'guidance_ping':
        this.playGuidancePing(t, intensity);
        break;
      case 'approve_chime':
        this.playApproveChime(t, intensity);
        break;
      case 'reject_shatter':
        this.playRejectShatter(t, intensity);
        break;
      case 'reaper_bass_drop':
        this.playReaperBassDrop(t, intensity);
        break;
    }
  }

  setVolume(v: number) {
    this.volume = Math.max(0, Math.min(1, v));
    localStorage.setItem(STORAGE_KEY, String(this.volume));
    if (this.masterGain && !this.muted) {
      this.masterGain.gain.setValueAtTime(this.volume, this.ctx!.currentTime);
    }
  }

  getVolume(): number {
    return this.volume;
  }

  setMuted(m: boolean) {
    this.muted = m;
    localStorage.setItem(STORAGE_MUTED_KEY, String(m));
    if (this.masterGain) {
      this.masterGain.gain.setValueAtTime(m ? 0 : this.volume, this.ctx!.currentTime);
    }
  }

  isMuted(): boolean {
    return this.muted;
  }

  toggleMute() {
    this.setMuted(!this.muted);
    // Initialize on first unmute (user gesture)
    if (!this.muted && !this.initialized) {
      this.init();
    }
  }

  // --- Sound Synthesis ---

  /** 2ms noise burst with quick exponential decay — many blend into "rain on glass" */
  private playToolTick(t: number, intensity: number) {
    const ctx = this.ctx!;
    const gain = ctx.createGain();
    gain.connect(this.masterGain!);
    gain.gain.setValueAtTime(0.08 * intensity, t);
    gain.gain.exponentialRampToValueAtTime(0.001, t + 0.04);

    // White noise via buffer
    const buffer = ctx.createBuffer(1, ctx.sampleRate * 0.004, ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < data.length; i++) {
      data[i] = (Math.random() * 2 - 1) * 0.5;
    }
    const noise = ctx.createBufferSource();
    noise.buffer = buffer;
    noise.connect(gain);
    noise.start(t);
    noise.stop(t + 0.04);
  }

  /** Ascending sine sweep 200→600Hz over 150ms */
  private playConnectChirp(t: number, intensity: number) {
    const ctx = this.ctx!;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(this.masterGain!);

    osc.type = 'sine';
    osc.frequency.setValueAtTime(200, t);
    osc.frequency.exponentialRampToValueAtTime(600, t + 0.15);

    gain.gain.setValueAtTime(0.12 * intensity, t);
    gain.gain.exponentialRampToValueAtTime(0.001, t + 0.2);

    osc.start(t);
    osc.stop(t + 0.2);
  }

  /** Three quick ascending sine tones — new worker arriving */
  private playEmployeeSpawn(t: number, intensity: number) {
    const ctx = this.ctx!;
    const freqs = [330, 440, 550];
    for (let i = 0; i < 3; i++) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(this.masterGain!);

      osc.type = 'sine';
      osc.frequency.value = freqs[i];

      const start = t + i * 0.08;
      gain.gain.setValueAtTime(0, start);
      gain.gain.linearRampToValueAtTime(0.1 * intensity, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, start + 0.1);

      osc.start(start);
      osc.stop(start + 0.1);
    }
  }

  /** Resolved two-tone harmony — completion */
  private playEmployeeComplete(t: number, intensity: number) {
    const ctx = this.ctx!;
    const freqs = [440, 554]; // A4 + C#5 (major third)
    for (const freq of freqs) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(this.masterGain!);

      osc.type = 'sine';
      osc.frequency.value = freq;

      gain.gain.setValueAtTime(0.1 * intensity, t);
      gain.gain.exponentialRampToValueAtTime(0.001, t + 0.4);

      osc.start(t);
      osc.stop(t + 0.4);
    }
  }

  /** Soft 880Hz bell with reverb tail */
  private playGuidancePing(t: number, intensity: number) {
    const ctx = this.ctx!;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(this.masterGain!);

    osc.type = 'sine';
    osc.frequency.value = 880;

    gain.gain.setValueAtTime(0.08 * intensity, t);
    gain.gain.exponentialRampToValueAtTime(0.001, t + 0.6);

    osc.start(t);
    osc.stop(t + 0.6);
  }

  /** Major triad with shimmer — approval */
  private playApproveChime(t: number, intensity: number) {
    const ctx = this.ctx!;
    const freqs = [523, 659, 784]; // C5 + E5 + G5
    for (let i = 0; i < 3; i++) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(this.masterGain!);

      osc.type = 'sine';
      osc.frequency.value = freqs[i];

      const start = t + i * 0.05;
      gain.gain.setValueAtTime(0.1 * intensity, start);
      gain.gain.exponentialRampToValueAtTime(0.001, start + 0.6);

      osc.start(start);
      osc.stop(start + 0.6);
    }
  }

  /** Dissonant cluster with noise burst — rejection */
  private playRejectShatter(t: number, intensity: number) {
    const ctx = this.ctx!;

    // Dissonant cluster
    const freqs = [220, 233, 277]; // A3 + Bb3 + C#4
    for (const freq of freqs) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(this.masterGain!);

      osc.type = 'sawtooth';
      osc.frequency.value = freq;

      gain.gain.setValueAtTime(0.06 * intensity, t);
      gain.gain.exponentialRampToValueAtTime(0.001, t + 0.3);

      osc.start(t);
      osc.stop(t + 0.3);
    }

    // Noise burst
    const buffer = ctx.createBuffer(1, ctx.sampleRate * 0.05, ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < data.length; i++) {
      data[i] = (Math.random() * 2 - 1);
    }
    const noise = ctx.createBufferSource();
    const noiseGain = ctx.createGain();
    noise.buffer = buffer;
    noise.connect(noiseGain);
    noiseGain.connect(this.masterGain!);
    noiseGain.gain.setValueAtTime(0.15 * intensity, t);
    noiseGain.gain.exponentialRampToValueAtTime(0.001, t + 0.08);
    noise.start(t);
    noise.stop(t + 0.08);
  }

  /** Deep 40Hz sine, 500ms, dramatic — reaper event */
  private playReaperBassDrop(t: number, intensity: number) {
    const ctx = this.ctx!;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(this.masterGain!);

    osc.type = 'sine';
    osc.frequency.setValueAtTime(40, t);
    osc.frequency.exponentialRampToValueAtTime(20, t + 0.5);

    gain.gain.setValueAtTime(0.2 * intensity, t);
    gain.gain.exponentialRampToValueAtTime(0.001, t + 0.5);

    osc.start(t);
    osc.stop(t + 0.5);
  }
}

/** Singleton audio engine instance */
export const audioEngine = new AudioEngine();
