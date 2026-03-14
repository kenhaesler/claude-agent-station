/**
 * ReplayController — DVR for AI work.
 * Fetches structured log events, replays them through the Cortex renderer
 * at configurable speed with temporal scrubbing and gap compression.
 */

import { getRunLogs } from './api';
import { parseLogLine } from './log-parser';
import type { ParsedLogEvent } from './log-parser';
import type { WorkspaceRenderer, EventType } from './workspace-renderer';

export type ReplayState = 'idle' | 'loading' | 'playing' | 'paused' | 'scrubbing' | 'complete';

export interface ReplayEvent {
  timestamp: number;
  relativeTime: number; // ms from start
  compressedTime: number; // ms from start with gap compression
  parsed: ParsedLogEvent;
  phase: 'employee' | 'review' | 'verdict' | 'idle';
}

export interface ReplayStatus {
  state: ReplayState;
  currentTime: number; // compressed time in ms
  totalDuration: number; // total compressed duration in ms
  speed: number;
  eventIndex: number;
  totalEvents: number;
  progress: number; // 0-1
}

const GAP_THRESHOLD = 5000; // 5s gap = compress
const GAP_COMPRESSED = 500; // Compressed gap = 500ms
const SPEEDS = [10, 25, 50, 100];

export class ReplayController {
  private events: ReplayEvent[] = [];
  private state: ReplayState = 'idle';
  private speed = 25;
  private currentTime = 0;
  private totalDuration = 0;
  private eventIndex = 0;
  private rafId = 0;
  private lastFrameTime = 0;
  private renderer: WorkspaceRenderer | null = null;
  private onStatusChange: ((status: ReplayStatus) => void) | null = null;
  private onLogEvent: ((event: ParsedLogEvent) => void) | null = null;

  constructor(
    options: {
      onStatusChange?: (status: ReplayStatus) => void;
      onLogEvent?: (event: ParsedLogEvent) => void;
    } = {}
  ) {
    this.onStatusChange = options.onStatusChange ?? null;
    this.onLogEvent = options.onLogEvent ?? null;
  }

  setRenderer(renderer: WorkspaceRenderer) {
    this.renderer = renderer;
  }

  getStatus(): ReplayStatus {
    return {
      state: this.state,
      currentTime: this.currentTime,
      totalDuration: this.totalDuration,
      speed: this.speed,
      eventIndex: this.eventIndex,
      totalEvents: this.events.length,
      progress: this.totalDuration > 0 ? this.currentTime / this.totalDuration : 0,
    };
  }

  getEvents(): ReplayEvent[] {
    return this.events;
  }

  getAvailableSpeeds(): number[] {
    return SPEEDS;
  }

  async load(runId: string) {
    this.state = 'loading';
    this.emitStatus();

    try {
      // Fetch all log lines
      const res = await getRunLogs(runId, 2000);
      const rawLines = res.lines;

      // Parse and extract timestamps
      const parsed: { timestamp: number; event: ParsedLogEvent }[] = [];
      for (const line of rawLines) {
        const lineStr = JSON.stringify(line);
        const result = parseLogLine(lineStr);
        if (!result) continue;

        const events = Array.isArray(result) ? result : [result];
        for (const evt of events) {
          if (evt.type === 'unknown') continue;
          // Extract timestamp from raw JSON or use index-based ordering
          let ts = 0;
          try {
            const raw = JSON.parse(evt.raw);
            ts = raw.timestamp ? new Date(raw.timestamp).getTime() : 0;
          } catch { /* ignore */ }
          parsed.push({ timestamp: ts, event: evt });
        }
      }

      if (parsed.length === 0) {
        this.state = 'idle';
        this.emitStatus();
        return;
      }

      // If no real timestamps, assign sequential fake timestamps (100ms apart)
      const hasTimestamps = parsed.some(p => p.timestamp > 0);
      if (!hasTimestamps) {
        parsed.forEach((p, i) => { p.timestamp = i * 100; });
      }

      // Sort by timestamp
      parsed.sort((a, b) => a.timestamp - b.timestamp);

      const startTime = parsed[0].timestamp;

      // Build replay events with gap compression
      this.events = [];
      let compressedAccum = 0;
      let lastRealTime = 0;

      for (let i = 0; i < parsed.length; i++) {
        const p = parsed[i];
        const realTime = p.timestamp - startTime;
        const gap = realTime - lastRealTime;

        if (i > 0 && gap > GAP_THRESHOLD) {
          compressedAccum += GAP_COMPRESSED;
        } else {
          compressedAccum += gap;
        }

        // Detect phase from event sequence
        let phase: ReplayEvent['phase'] = 'employee';
        if (p.event.type === 'result') phase = 'verdict';

        this.events.push({
          timestamp: p.timestamp,
          relativeTime: realTime,
          compressedTime: compressedAccum,
          parsed: p.event,
          phase,
        });

        lastRealTime = realTime;
      }

      this.totalDuration = this.events.length > 0 ? this.events[this.events.length - 1].compressedTime : 0;
      this.currentTime = 0;
      this.eventIndex = 0;
      this.state = 'paused';
      this.emitStatus();
    } catch {
      this.state = 'idle';
      this.emitStatus();
    }
  }

  play() {
    if (this.events.length === 0) return;
    if (this.state === 'complete') {
      this.currentTime = 0;
      this.eventIndex = 0;
    }
    this.state = 'playing';
    this.lastFrameTime = performance.now();
    this.tick();
    this.emitStatus();
  }

  pause() {
    this.state = 'paused';
    if (this.rafId) {
      cancelAnimationFrame(this.rafId);
      this.rafId = 0;
    }
    this.emitStatus();
  }

  togglePlayPause() {
    if (this.state === 'playing') {
      this.pause();
    } else {
      this.play();
    }
  }

  /** Scrub to a specific progress (0-1) */
  scrubTo(progress: number) {
    const wasPlaying = this.state === 'playing';
    if (wasPlaying) this.pause();

    this.currentTime = Math.max(0, Math.min(this.totalDuration, progress * this.totalDuration));

    // Find event index at this time
    this.eventIndex = 0;
    for (let i = 0; i < this.events.length; i++) {
      if (this.events[i].compressedTime <= this.currentTime) {
        this.eventIndex = i + 1;
      } else {
        break;
      }
    }

    this.state = wasPlaying ? 'playing' : 'paused';
    if (wasPlaying) {
      this.lastFrameTime = performance.now();
      this.tick();
    }
    this.emitStatus();
  }

  /** Step forward one event */
  stepForward() {
    if (this.eventIndex < this.events.length) {
      this.dispatchEvent(this.events[this.eventIndex]);
      this.eventIndex++;
      if (this.eventIndex < this.events.length) {
        this.currentTime = this.events[this.eventIndex].compressedTime;
      } else {
        this.currentTime = this.totalDuration;
        this.state = 'complete';
      }
      this.emitStatus();
    }
  }

  /** Step backward one event */
  stepBackward() {
    if (this.eventIndex > 0) {
      this.eventIndex--;
      this.currentTime = this.events[this.eventIndex].compressedTime;
      this.emitStatus();
    }
  }

  setSpeed(speed: number) {
    this.speed = speed;
    this.emitStatus();
  }

  cycleSpeed() {
    const idx = SPEEDS.indexOf(this.speed);
    this.speed = SPEEDS[(idx + 1) % SPEEDS.length];
    this.emitStatus();
  }

  stop() {
    this.state = 'idle';
    if (this.rafId) {
      cancelAnimationFrame(this.rafId);
      this.rafId = 0;
    }
    this.currentTime = 0;
    this.eventIndex = 0;
    this.emitStatus();
  }

  destroy() {
    this.stop();
    this.events = [];
    this.renderer = null;
    this.onStatusChange = null;
    this.onLogEvent = null;
  }

  // --- Internal ---

  private tick() {
    if (this.state !== 'playing') return;

    const now = performance.now();
    const dt = (now - this.lastFrameTime) * this.speed; // real ms * speed
    this.lastFrameTime = now;
    this.currentTime += dt;

    // Dispatch events that the virtual clock has passed
    while (this.eventIndex < this.events.length && this.events[this.eventIndex].compressedTime <= this.currentTime) {
      this.dispatchEvent(this.events[this.eventIndex]);
      this.eventIndex++;
    }

    // Check completion
    if (this.eventIndex >= this.events.length) {
      this.state = 'complete';
      this.emitStatus();
      return;
    }

    this.emitStatus();
    this.rafId = requestAnimationFrame(() => this.tick());
  }

  private dispatchEvent(event: ReplayEvent) {
    const parsed = event.parsed;

    // Notify log consumers
    this.onLogEvent?.(parsed);

    // Drive Cortex renderer
    if (!this.renderer) return;

    if (parsed.type === 'assistant_tool_use') {
      this.renderer.triggerEvent('tool_use', { toolName: parsed.toolName });
    } else if (parsed.type === 'assistant_thinking') {
      this.renderer.triggerEvent('thinking_start', {});
    } else if (parsed.type === 'result') {
      this.renderer.triggerEvent('run_complete', {});
    }
  }

  private emitStatus() {
    this.onStatusChange?.(this.getStatus());
  }
}
