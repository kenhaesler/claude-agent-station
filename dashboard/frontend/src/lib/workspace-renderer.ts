/**
 * workspace-renderer.ts — Type-only stub.
 *
 * The canvas-based "Mission Cortex" renderer (2171 lines) has been removed.
 * AgentWorkspace now renders a functional Team Operations Board via Svelte components.
 *
 * Type exports are kept for backward compatibility with files that import them:
 * - audio-engine.ts (SoundEvent)
 * - replay-controller.ts (WorkspaceRenderer, EventType)
 * - RunDetailPage.svelte, RunCard.svelte, PhaseTimeline.svelte (RunPhase)
 */

export type RunPhase =
  | 'idle'
  | 'coordinating'
  | 'employee'
  | 'plan_review'
  | 'manager_review'
  | 'executing_verdict'
  // Agent Teams phases
  | 'team_creation'
  | 'planning'
  | 'implementing'
  | 'synthesis';

export type EventType =
  | 'tool_use'
  | 'thinking_start'
  | 'thinking_end'
  | 'guidance_sent'
  | 'phase_change'
  | 'run_start'
  | 'run_complete'
  | 'conflict'
  | 'verdict'
  | 'reaper_sweep'
  | 'employee_reaped'
  // Agent Teams events
  | 'team_created'
  | 'teammate_spawned'
  | 'task_claimed'
  | 'teammate_completed';

export interface SoundEvent {
  type: string;
  intensity: number;
  data?: Record<string, unknown>;
}

/**
 * Stub class — no-op implementation.
 * replay-controller.ts imports this type but the canvas renderer is removed.
 */
export class WorkspaceRenderer {
  constructor(_canvas: HTMLCanvasElement) {}
  start() {}
  stop() {}
  resize(_w: number, _h: number) {}
  setData(_data: unknown) {}
  setActivity(_activity: unknown) {}
  setRenderQuality(_quality: string) {}
  setMousePosition(_x: number, _y: number) {}
  triggerEvent(_type: string, _data?: unknown) {}
  getNodeAt(_x: number, _y: number): null { return null; }
  getEmployeeAt(_x: number, _y: number): null { return null; }
  getEmployeeTooltip(_x: number, _y: number): null { return null; }
  isHubAt(_x: number, _y: number): boolean { return false; }
  onSoundEvent(_cb: (e: SoundEvent) => void): () => void { return () => {}; }
}
