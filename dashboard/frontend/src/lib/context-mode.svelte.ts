/**
 * Context Panel (Zone C) mode state management.
 * Controls what the right panel shows, independent of URL routing.
 */

export type ContextMode = 'conversation' | 'code' | 'intelligence' | 'diff' | 'run-detail';

export interface ContextTarget {
  /** Which agent to focus on (e.g., 'Employee-1') */
  agentName?: string;
  /** Which run to show context for */
  runId?: string;
  /** Which task to show */
  taskId?: string;
  /** Which plan to review */
  planId?: number;
  /** Which file to show in code mode */
  filePath?: string;
}

interface ContextState {
  mode: ContextMode;
  target: ContextTarget;
  /** Whether the panel is visible (always true on desktop, togglable on mobile) */
  visible: boolean;
}

export const contextState = $state<ContextState>({
  mode: 'conversation',
  target: {},
  visible: true,
});

/** Switch context panel mode and optionally set a target */
export function setContext(mode: ContextMode, target?: ContextTarget): void {
  contextState.mode = mode;
  if (target) {
    contextState.target = target;
  }
}

/** Update just the target without changing mode */
export function setTarget(target: ContextTarget): void {
  contextState.target = { ...contextState.target, ...target };
}

/** Toggle panel visibility (for mobile) */
export function toggleContextPanel(): void {
  contextState.visible = !contextState.visible;
}

/** Show conversation for a specific agent */
export function focusAgent(agentName: string): void {
  setContext('conversation', { agentName });
}

/** Show diff for a specific run */
export function showRunDiff(runId: string): void {
  setContext('diff', { runId });
}

/** Show code activity for an agent */
export function showCodeActivity(agentName: string): void {
  setContext('code', { agentName });
}

/** Show full run detail */
export function showRunDetail(runId: string): void {
  setContext('run-detail', { runId });
}
