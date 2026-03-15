/**
 * WorkspaceRenderer — "Mission Cortex"
 *
 * Agent-centric canvas visualization. Holographic agent nodes with neural fiber
 * connections, phase-aware spatial layout, gravity vortexes, cinematic event
 * effects, spring physics, and bloom post-processing.
 *
 * Render layers (back to front):
 *  1. Background gradient (radial, phase-tinted)
 *  2. Nebula clouds (drifting, phase-colored)
 *  3. Depth grid (concentric rings + radial lines)
 *  4. Radar sweep (conic gradient rotation)
 *  5. Ambient particles (150, gravity-attracted)
 *  6. Connection base strands — dual-rail energy conduits
 *  7. Active connection glow + data diamonds + data stream text
 *  8. Hub rings + phase core + energy arcs
 *  9. Agent nodes (holographic multi-layer)
 *  9b. Employee orbitals (sub-nodes per employee)
 *  9c. Shield rings (manager review phase)
 * 10. Ripple effects (expanding rings)
 * 10b. Reaper animation (sweep + targeted shatter)
 * 11. Thinking dots (orbiting)
 * 12. Labels + tooltips
 * 13. Bloom post-process
 */

// ── Interfaces ──────────────────────────────────────────────

export type RunPhase = 'idle' | 'coordinating' | 'employee' | 'manager_review' | 'executing_verdict';

export type EventType = 'tool_use' | 'thinking_start' | 'thinking_end' | 'guidance_sent'
  | 'phase_change' | 'run_start' | 'run_complete' | 'conflict' | 'verdict'
  | 'reaper_sweep' | 'employee_reaped';

/** Sound event emitted for Web Audio API integration */
export interface SoundEvent {
  type: 'connect_chirp' | 'reaper_bass_drop' | 'approve_chime' | 'reject_shatter'
    | 'employee_spawn' | 'employee_complete' | 'tool_tick' | 'guidance_ping';
  intensity: number; // 0-1
  data?: Record<string, unknown>;
}

export interface EmployeeNode {
  index: number;
  runId: string;
  status: 'working' | 'waiting' | 'completed' | 'failed' | 'reaped';
  tool: string | null;
  issueNumber: number | null;
  orbitAngle: number;
  glow: number;
  inWorktree: boolean;
  tokensUsed: number;
  turnsUsed: number;
  // Reaper shatter state
  shatterProgress: number; // 0 = no shatter, >0 = shattering, 1 = fully dissolved
  shatterParticles: { x: number; y: number; vx: number; vy: number; life: number; size: number }[];
}

export interface AgentNode {
  id: string;
  role: 'manager' | 'employee' | 'coordinator' | 'analyst' | 'planner' | 'assigner';
  name: string;
  color: [number, number, number];
  x: number;
  y: number;
  targetX: number;
  targetY: number;
  velX: number;
  velY: number;
  opacity: number;
  scale: number;
  glowIntensity: number;
  arcRotation: number;
  heartbeatPhase: number;
  isActive: boolean;
  isThinking: boolean;
  currentAction: string | null;
  turnCount: number;
  tokenCount: number;
  employees: EmployeeNode[];
  // Shield ring state (for manager_review phase)
  shieldAngle: number;
  shieldAlpha: number;
}

interface DataDiamond {
  progress: number;
  speed: number;
  connectionFrom: number; // index into nodes
  connectionTo: number;
  trail: { x: number; y: number }[];
  color: [number, number, number];
}

interface Ripple {
  x: number;
  y: number;
  radius: number;
  maxRadius: number;
  alpha: number;
  color: [number, number, number];
}

interface ThinkingDot {
  nodeIndex: number;
  angle: number;
  speed: number;
}

interface AmbientParticle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  life: number;
  maxLife: number;
  size: number;
  alpha: number;
}

interface ReaperState {
  active: boolean;
  sweepAngle: number; // current sweep angle (0 to 2*PI)
  sweepProgress: number; // 0 to 1
  alpha: number;
  hudText: string | null;
  hudAlpha: number;
  targetRunIds: string[]; // runs being reaped
}

/** Data stream text flowing along connection paths */
interface DataStreamText {
  text: string;
  progress: number; // 0-1 along path
  speed: number;
  connectionTo: number;
  alpha: number;
}

export interface WorkspaceData {
  agents: {
    id: string;
    role: 'manager' | 'employee' | 'coordinator' | 'analyst' | 'planner' | 'assigner';
    name: string;
    color: string;
    isActive: boolean;
    isThinking: boolean;
    currentAction: string | null;
    turnCount: number;
    tokenCount: number;
    employees?: {
      index: number;
      runId: string;
      status: 'working' | 'waiting' | 'completed' | 'failed' | 'reaped';
      tool: string | null;
      issueNumber: number | null;
      inWorktree: boolean;
      tokensUsed: number;
      turnsUsed: number;
    }[];
  }[];
  phase: RunPhase;
  serviceActive: boolean;
  usagePercent: number;
  activityIntensity: number;
}

export interface ActivityData {
  intensity: number;
  currentTool: string | null;
  activeAgent: string | null;
}

interface EventData {
  agentId?: string;
  toolName?: string;
  verdict?: string;
  message?: string;
  runId?: string;
  employeeIndex?: number;
  reaperCount?: number;
  targetRunIds?: string[];
}

// ── Role colors ──────────────────────────────────────────────

const ROLE_COLORS: Record<string, [number, number, number]> = {
  manager: [245, 158, 11],
  employee: [59, 130, 246],
  coordinator: [168, 85, 247],
  analyst: [139, 92, 246],
};

/** Employee status colors */
const EMPLOYEE_STATUS_COLORS: Record<string, [number, number, number]> = {
  working: [59, 130, 246],    // blue
  waiting: [245, 158, 11],    // amber
  completed: [34, 197, 94],   // green
  failed: [239, 68, 68],      // red
  reaped: [127, 29, 29],      // dark red
};

/** Sample tool/file names for data stream text effect */
const STREAM_TEXT_SAMPLES = [
  'Edit', 'Read', 'Bash', 'Write', 'Grep', 'git commit',
  'npm test', 'src/', '.ts', 'api/', 'fix #', 'async',
  'deploy', 'build', 'lint', 'merge', 'fetch', 'push',
];

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  return [
    parseInt(h.substring(0, 2), 16),
    parseInt(h.substring(2, 4), 16),
    parseInt(h.substring(4, 6), 16),
  ];
}

// ── Renderer ─────────────────────────────────────────────────

export class WorkspaceRenderer {
  private ctx: CanvasRenderingContext2D;
  private w = 0;
  private h = 0;
  private dpr: number;
  private running = false;
  private rafId = 0;
  private lastTime = 0;

  // Bloom
  private bloomCanvas: HTMLCanvasElement | null = null;
  private bloomCtx: CanvasRenderingContext2D | null = null;

  // Scene
  private nodes: AgentNode[] = [];
  private particles: AmbientParticle[] = [];
  private diamonds: DataDiamond[] = [];
  private ripples: Ripple[] = [];
  private thinkingDots: ThinkingDot[] = [];
  private dataStreamTexts: DataStreamText[] = [];

  // Hub
  private hubX = 0;
  private hubY = 0;
  private hubRadius = 35;
  private orbitRadius = 0;

  // Clocks
  private time = 0;
  private hubPulse = 0;
  private hubArcAngle = 0;
  private radarAngle = 0;
  private nebulaClock = 0;

  // State
  private phase: RunPhase = 'idle';
  private serviceActive = false;
  private usagePercent = 0;
  private intensity = 0;
  private currentToolText = '';
  private toolTextAlpha = 0;

  // Parallax (holographic depth)
  private mouseX = 0;
  private mouseY = 0;
  private parallaxX = 0;
  private parallaxY = 0;

  // Reaper
  private reaper: ReaperState = {
    active: false,
    sweepAngle: 0,
    sweepProgress: 0,
    alpha: 0,
    hudText: null,
    hudAlpha: 0,
    targetRunIds: [],
  };

  // HUD tool cycling
  private hudToolCycleIndex = 0;
  private hudToolCycleTimer = 0;

  // Event queue
  private eventQueue: { type: EventType; data: EventData; time: number }[] = [];

  // Sound event callbacks
  private soundListeners: ((event: SoundEvent) => void)[] = [];

  constructor(private canvas: HTMLCanvasElement, dpr?: number) {
    this.ctx = canvas.getContext('2d')!;
    this.dpr = dpr ?? (window.devicePixelRatio || 1);
    this.mouseX = 0;
    this.mouseY = 0;
  }

  // ── Public API ────────────────────────────────────────────

  resize(w: number, h: number) {
    this.w = w;
    this.h = h;
    this.canvas.width = w * this.dpr;
    this.canvas.height = h * this.dpr;
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);

    this.hubX = w / 2;
    this.hubY = h / 2;
    this.orbitRadius = Math.min(w, h) * 0.32;
    this.hubRadius = Math.max(25, Math.min(w, h) * 0.045);

    this.bloomCanvas = document.createElement('canvas');
    this.bloomCanvas.width = Math.floor(w * this.dpr * 0.25);
    this.bloomCanvas.height = Math.floor(h * this.dpr * 0.25);
    this.bloomCtx = this.bloomCanvas.getContext('2d')!;

    this.layoutNodes();
  }

  setData(data: WorkspaceData) {
    this.phase = data.phase;
    this.serviceActive = data.serviceActive;
    this.usagePercent = data.usagePercent;
    this.intensity = data.activityIntensity;

    // Sync nodes
    const existingMap = new Map(this.nodes.map(n => [n.id, n]));

    const newNodes: AgentNode[] = [];
    for (const agent of data.agents) {
      const existing = existingMap.get(agent.id);
      if (existing) {
        existing.isActive = agent.isActive;
        existing.isThinking = agent.isThinking;
        existing.currentAction = agent.currentAction;
        existing.turnCount = agent.turnCount;
        existing.tokenCount = agent.tokenCount;
        existing.role = agent.role;
        existing.name = agent.name;
        existing.color = hexToRgb(agent.color);
        // Sync employee nodes
        this.syncEmployeeNodes(existing, agent.employees ?? []);
        newNodes.push(existing);
      } else {
        const color = hexToRgb(agent.color);
        const node: AgentNode = {
          id: agent.id,
          role: agent.role,
          name: agent.name,
          color,
          x: this.hubX,
          y: this.hubY,
          targetX: this.hubX,
          targetY: this.hubY,
          velX: 0,
          velY: 0,
          opacity: 0,
          scale: 0.8,
          glowIntensity: 0,
          arcRotation: Math.random() * Math.PI * 2,
          heartbeatPhase: Math.random() * Math.PI * 2,
          isActive: agent.isActive,
          isThinking: agent.isThinking,
          currentAction: agent.currentAction,
          turnCount: agent.turnCount,
          tokenCount: agent.tokenCount,
          employees: [],
          shieldAngle: 0,
          shieldAlpha: 0,
        };
        this.syncEmployeeNodes(node, agent.employees ?? []);
        newNodes.push(node);
      }
    }
    this.nodes = newNodes;
    this.layoutNodes();
    this.ensureParticles();
    this.syncThinkingDots();
  }

  setActivity(data: ActivityData) {
    this.intensity = data.intensity;
    this.currentToolText = data.currentTool
      ? (data.currentTool.length > 28 ? data.currentTool.slice(0, 28) + '...' : data.currentTool)
      : '';
  }

  /** Set mouse position for parallax effect */
  setMousePosition(x: number, y: number) {
    this.mouseX = x;
    this.mouseY = y;
  }

  triggerEvent(type: EventType, data: EventData = {}) {
    this.eventQueue.push({ type, data, time: this.time });
    this.processEvent(type, data);
  }

  /** Register a listener for sound events (Web Audio API hook) */
  onSoundEvent(listener: (event: SoundEvent) => void): () => void {
    this.soundListeners.push(listener);
    return () => {
      const idx = this.soundListeners.indexOf(listener);
      if (idx >= 0) this.soundListeners.splice(idx, 1);
    };
  }

  private emitSound(event: SoundEvent) {
    for (const listener of this.soundListeners) {
      try { listener(event); } catch { /* ignore */ }
    }
  }

  start() {
    if (this.running) return;
    this.running = true;
    this.lastTime = performance.now();
    this.ensureParticles();
    this.tick(this.lastTime);
  }

  stop() {
    this.running = false;
    if (this.rafId) { cancelAnimationFrame(this.rafId); this.rafId = 0; }
  }

  getNodeAt(clientX: number, clientY: number): AgentNode | null {
    const rect = this.canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    const hitR = 40;
    for (const node of this.nodes) {
      const dx = x - node.x, dy = y - node.y;
      if (dx * dx + dy * dy < hitR * hitR) return node;
    }
    return null;
  }

  /** Get employee orbital at click position, returns { nodeId, employeeIndex, runId } or null */
  getEmployeeAt(clientX: number, clientY: number): { nodeId: string; employeeIndex: number; runId: string } | null {
    const rect = this.canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    const hitR = 12;
    for (const node of this.nodes) {
      for (const emp of node.employees) {
        const empR = node.role === 'employee' ? 22 : 35;
        const ex = node.x + Math.cos(emp.orbitAngle) * empR;
        const ey = node.y + Math.sin(emp.orbitAngle) * empR;
        const dx = x - ex, dy = y - ey;
        if (dx * dx + dy * dy < hitR * hitR) {
          return { nodeId: node.id, employeeIndex: emp.index, runId: emp.runId };
        }
      }
    }
    return null;
  }

  isHubAt(clientX: number, clientY: number): boolean {
    const rect = this.canvas.getBoundingClientRect();
    const dx = clientX - rect.left - this.hubX;
    const dy = clientY - rect.top - this.hubY;
    return dx * dx + dy * dy < this.hubRadius * this.hubRadius * 2;
  }

  /** Get tooltip text for employee at position */
  getEmployeeTooltip(clientX: number, clientY: number): string | null {
    const emp = this.getEmployeeAt(clientX, clientY);
    if (!emp) return null;
    const node = this.nodes.find(n => n.id === emp.nodeId);
    if (!node) return null;
    const employee = node.employees.find(e => e.index === emp.employeeIndex);
    if (!employee) return null;
    const parts = [`Employee ${employee.index}`];
    if (employee.tool) parts.push(`Tool: ${employee.tool}`);
    if (employee.issueNumber) parts.push(`Issue #${employee.issueNumber}`);
    if (employee.inWorktree) parts.push('[worktree]');
    parts.push(`Status: ${employee.status}`);
    return parts.join(' | ');
  }

  /** Get all active employees across all nodes for HUD */
  getAllEmployees(): EmployeeNode[] {
    const result: EmployeeNode[] = [];
    for (const node of this.nodes) {
      result.push(...node.employees);
    }
    return result;
  }

  /** Get total active employee count */
  getActiveEmployeeCount(): number {
    let count = 0;
    for (const node of this.nodes) {
      count += node.employees.filter(e => e.status === 'working' || e.status === 'waiting').length;
    }
    return count;
  }

  // ── Employee Node Sync ────────────────────────────────────

  private syncEmployeeNodes(
    node: AgentNode,
    employees: WorkspaceData['agents'][0]['employees'],
  ) {
    if (!employees || employees.length === 0) {
      // Preserve employees that are shattering (reaper animation)
      node.employees = node.employees.filter(e => e.shatterProgress > 0 && e.shatterProgress < 1);
      return;
    }

    const existingMap = new Map(node.employees.map(e => [e.index, e]));
    const newEmployees: EmployeeNode[] = [];
    const spacing = (Math.PI * 2) / Math.max(employees.length, 1);

    for (let i = 0; i < employees.length; i++) {
      const empData = employees[i];
      const existing = existingMap.get(empData.index);
      if (existing) {
        existing.status = empData.status;
        existing.tool = empData.tool;
        existing.issueNumber = empData.issueNumber;
        existing.inWorktree = empData.inWorktree;
        existing.tokensUsed = empData.tokensUsed;
        existing.turnsUsed = empData.turnsUsed;
        existing.runId = empData.runId;
        newEmployees.push(existing);
      } else {
        newEmployees.push({
          index: empData.index,
          runId: empData.runId,
          status: empData.status,
          tool: empData.tool,
          issueNumber: empData.issueNumber,
          orbitAngle: i * spacing + Math.random() * 0.3,
          glow: 0,
          inWorktree: empData.inWorktree,
          tokensUsed: empData.tokensUsed,
          turnsUsed: empData.turnsUsed,
          shatterProgress: 0,
          shatterParticles: [],
        });
        // Sound: employee spawned
        this.emitSound({ type: 'employee_spawn', intensity: 0.5, data: { index: empData.index } });
      }
    }

    // Keep shattering employees
    for (const existing of node.employees) {
      if (existing.shatterProgress > 0 && existing.shatterProgress < 1) {
        if (!newEmployees.find(e => e.index === existing.index)) {
          newEmployees.push(existing);
        }
      }
    }

    node.employees = newEmployees;
  }

  // ── Layout ──────────────────────────────────────────────

  private layoutNodes() {
    const count = this.nodes.length;
    if (count === 0) return;

    const phase = this.phase;
    // Elliptical orbit — wide landscape spread, compact vertical.
    // hubX = w/2, hubY = h/2.
    const w = this.hubX * 2;
    const h = this.hubY * 2;
    const baseRx = w * 0.40;   // generous horizontal spread
    const baseRy = h * 0.25;   // compact vertical band

    // Overlap guard: on an ellipse, minimum adjacent-node distance =
    // 2·sin(π/n)·min(rx,ry). We only need to floor the shorter axis.
    const MIN_NODE_SPACING = 100;
    const minR = count >= 2
      ? MIN_NODE_SPACING / (2 * Math.sin(Math.PI / count))
      : MIN_NODE_SPACING * 0.5;
    // Keep nodes outside the hub's outermost visible ring + small gap
    const minHubClearance = this.hubRadius * 2 + 30;
    const floor = Math.max(minR, minHubClearance);

    for (let i = 0; i < count; i++) {
      const node = this.nodes[i];
      // Start at 0 (rightward) so 2-node layouts spread horizontally,
      // not vertically (the old -π/2 put 2 nodes at top/bottom).
      const angle = (i / count) * Math.PI * 2;

      let mult = 1.0;
      if (phase === 'idle') {
        mult = 1.0;
        node.opacity = Math.max(node.opacity, 0.5);
      } else if (phase === 'manager_review') {
        if (node.role === 'manager') mult = 0.45;
        else if (node.isActive) mult = 0.6;
        else mult = 0.75;
      } else if (phase === 'employee') {
        if (node.isActive) mult = 0.55;
        else if (node.role === 'manager') mult = 1.0;
        else mult = 0.7;
      } else if (phase === 'coordinating') {
        if (node.role === 'coordinator') mult = 0.45;
        else if (node.role === 'manager') mult = 1.0;
        else mult = 0.7;
      } else if (phase === 'executing_verdict') {
        mult = 0.5;
      }

      let targetRx = baseRx * mult;
      let targetRy = baseRy * mult;

      // Only clamp the shorter axis to the floor — keeps the ellipse wide
      if (targetRx <= targetRy) {
        targetRx = Math.max(targetRx, floor);
      } else {
        targetRy = Math.max(targetRy, floor);
      }

      node.targetX = this.hubX + Math.cos(angle) * targetRx;
      node.targetY = this.hubY + Math.sin(angle) * targetRy;
    }
  }

  // ── Particles ───────────────────────────────────────────

  private ensureParticles() {
    while (this.particles.length < 150) {
      this.particles.push(this.createParticle(true));
    }
    if (this.particles.length > 200) this.particles.length = 200;
  }

  private createParticle(randomLife: boolean): AmbientParticle {
    return {
      x: Math.random() * this.w,
      y: Math.random() * this.h,
      vx: (Math.random() - 0.5) * 0.2,
      vy: (Math.random() - 0.5) * 0.2,
      life: randomLife ? Math.random() * 8000 : 0,
      maxLife: 6000 + Math.random() * 6000,
      size: 0.5 + Math.random() * 1,
      alpha: 0,
    };
  }

  // ── Thinking dots ────────────────────────────────────────

  private syncThinkingDots() {
    // Remove dots for non-thinking agents
    this.thinkingDots = this.thinkingDots.filter(d => {
      const node = this.nodes[d.nodeIndex];
      return node && node.isThinking;
    });

    // Add dots for newly thinking agents
    for (let i = 0; i < this.nodes.length; i++) {
      if (this.nodes[i].isThinking) {
        const hasDots = this.thinkingDots.some(d => d.nodeIndex === i);
        if (!hasDots) {
          for (let j = 0; j < 3; j++) {
            this.thinkingDots.push({
              nodeIndex: i,
              angle: (j / 3) * Math.PI * 2,
              speed: 1.2,
            });
          }
        }
      }
    }
  }

  // ── Events ──────────────────────────────────────────────

  private processEvent(type: EventType, data: EventData) {
    switch (type) {
      case 'tool_use': {
        const node = this.nodes.find(n => n.id === data.agentId);
        if (node) {
          this.ripples.push({
            x: node.x, y: node.y,
            radius: 10, maxRadius: data.toolName === 'Bash' ? 80 : 60,
            alpha: 0.5,
            color: node.color,
          });
          if (data.toolName === 'Bash') {
            // Double pulse for bash
            setTimeout(() => {
              this.ripples.push({
                x: node.x, y: node.y, radius: 10, maxRadius: 70,
                alpha: 0.4, color: [245, 158, 11],
              });
            }, 200);
          }
          this.emitSound({ type: 'tool_tick', intensity: 0.2, data: { tool: data.toolName } });
        }
        break;
      }
      case 'run_start':
        this.ripples.push({
          x: this.hubX, y: this.hubY,
          radius: 5, maxRadius: 300,
          alpha: 0.5, color: this.getPhaseColor(),
        });
        this.emitSound({ type: 'connect_chirp', intensity: 0.7 });
        break;
      case 'guidance_sent': {
        const target = this.nodes.find(n => n.id === data.agentId);
        if (target) {
          // Spawn data diamonds along connection
          const idx = this.nodes.indexOf(target);
          for (let i = 0; i < 4; i++) {
            this.diamonds.push({
              progress: i * 0.15,
              speed: 0.002,
              connectionFrom: -1, // hub
              connectionTo: idx,
              trail: [],
              color: [255, 255, 255],
            });
          }
          this.emitSound({ type: 'guidance_ping', intensity: 0.5 });
        }
        break;
      }
      case 'verdict': {
        const isApprove = data.verdict === 'APPROVE' || data.verdict === 'approve';
        const color: [number, number, number] = isApprove ? [34, 197, 94] : [245, 158, 11];
        this.ripples.push({
          x: this.hubX, y: this.hubY,
          radius: 5, maxRadius: 200,
          alpha: 0.6, color,
        });
        // Shield ring event
        if (isApprove) {
          this.triggerShieldOpen();
          this.emitSound({ type: 'approve_chime', intensity: 0.8 });
        } else {
          this.triggerShieldShatter();
          this.emitSound({ type: 'reject_shatter', intensity: 0.8 });
        }
        break;
      }
      case 'run_complete':
        // Fade all nodes
        for (const node of this.nodes) {
          node.isActive = false;
          node.isThinking = false;
        }
        this.emitSound({ type: 'employee_complete', intensity: 0.6 });
        break;
      case 'reaper_sweep':
        this.startReaperSweep(data.reaperCount ?? 0, data.targetRunIds ?? []);
        this.emitSound({ type: 'reaper_bass_drop', intensity: 1.0, data: { count: data.reaperCount } });
        break;
      case 'employee_reaped': {
        // Find and mark specific employee for shatter
        if (data.runId) {
          for (const node of this.nodes) {
            const emp = node.employees.find(e => e.runId === data.runId);
            if (emp && emp.shatterProgress === 0) {
              this.startEmployeeShatter(emp, node);
            }
          }
        }
        break;
      }
    }
  }

  // ── Reaper ──────────────────────────────────────────────

  private startReaperSweep(count: number, targetRunIds: string[]) {
    this.reaper.active = true;
    this.reaper.sweepAngle = 0;
    this.reaper.sweepProgress = 0;
    this.reaper.alpha = 1;
    this.reaper.hudText = `REAPER: ${count} STALE RUN${count !== 1 ? 'S' : ''} TERMINATED`;
    this.reaper.hudAlpha = 1;
    this.reaper.targetRunIds = targetRunIds;
  }

  private startEmployeeShatter(emp: EmployeeNode, node: AgentNode) {
    emp.status = 'reaped';
    emp.shatterProgress = 0.01; // start shatter
    emp.shatterParticles = [];
    const empR = node.role === 'employee' ? 22 : 35;
    const ex = node.x + Math.cos(emp.orbitAngle) * empR;
    const ey = node.y + Math.sin(emp.orbitAngle) * empR;
    // Create shatter particles
    for (let i = 0; i < 20; i++) {
      const angle = Math.random() * Math.PI * 2;
      const speed = 1 + Math.random() * 3;
      emp.shatterParticles.push({
        x: ex,
        y: ey,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed,
        life: 1.0,
        size: 1 + Math.random() * 3,
      });
    }
  }

  // ── Shield Rings ────────────────────────────────────────

  private triggerShieldOpen() {
    // Green burst — shield opens
    for (const node of this.nodes) {
      if (node.role !== 'manager') {
        node.shieldAlpha = 0; // dissolve shield
        this.ripples.push({
          x: node.x, y: node.y,
          radius: 20, maxRadius: 100,
          alpha: 0.6, color: [34, 197, 94],
        });
      }
    }
  }

  private triggerShieldShatter() {
    // Red shards — shield shatters
    for (const node of this.nodes) {
      if (node.role !== 'manager') {
        node.shieldAlpha = 0;
        // Red shard ripples
        for (let i = 0; i < 3; i++) {
          const angle = Math.random() * Math.PI * 2;
          const dist = 20 + Math.random() * 30;
          this.ripples.push({
            x: node.x + Math.cos(angle) * dist,
            y: node.y + Math.sin(angle) * dist,
            radius: 5, maxRadius: 40 + Math.random() * 30,
            alpha: 0.5, color: [239, 68, 68],
          });
        }
      }
    }
  }

  // ── Tick ────────────────────────────────────────────────

  private tick = (now: number) => {
    if (!this.running) return;
    const dt = Math.min(now - this.lastTime, 50);
    this.lastTime = now;
    this.update(dt);
    this.draw();
    this.rafId = requestAnimationFrame(this.tick);
  };

  // ── Update ──────────────────────────────────────────────

  private update(dt: number) {
    const dtSec = dt / 1000;
    this.time += dtSec;
    this.hubPulse += dt * 0.002;
    this.hubArcAngle += dtSec * 0.3;
    this.nebulaClock += dtSec * 0.15;
    if (this.phase !== 'idle') {
      this.radarAngle += dtSec * 0.6;
    }

    // Parallax (holographic depth)
    const targetPX = (this.mouseX - this.w / 2) * 0.01;
    const targetPY = (this.mouseY - this.h / 2) * 0.01;
    this.parallaxX += (targetPX - this.parallaxX) * 0.03;
    this.parallaxY += (targetPY - this.parallaxY) * 0.03;

    // HUD tool cycling timer
    this.hudToolCycleTimer += dtSec;
    if (this.hudToolCycleTimer >= 3) {
      this.hudToolCycleTimer = 0;
      this.hudToolCycleIndex++;
    }

    // Spring physics for node positions
    const stiffness = 0.12;
    const damping = 0.82;
    for (const node of this.nodes) {
      const dx = node.targetX - node.x;
      const dy = node.targetY - node.y;
      node.velX = (node.velX + dx * stiffness) * damping;
      node.velY = (node.velY + dy * stiffness) * damping;
      node.x += node.velX;
      node.y += node.velY;

      // Opacity fade in
      const targetOpacity = node.isActive ? 1 : (this.phase === 'idle' ? 0.5 : 0.35);
      node.opacity += (targetOpacity - node.opacity) * 0.05;

      // Scale
      const targetScale = node.isActive ? 1 : 0.85;
      node.scale += (targetScale - node.scale) * 0.05;

      // Glow
      const targetGlow = node.isActive ? 0.8 + this.intensity * 0.2 : 0;
      node.glowIntensity += (targetGlow - node.glowIntensity) * 0.04;

      // Arc rotation
      node.arcRotation += dtSec * (node.isActive ? 0.4 : 0.2);

      // Heartbeat
      if (node.isActive) {
        node.heartbeatPhase += dtSec * 5.2; // ~1.2s cycle
      }

      // Shield ring
      if (this.phase === 'manager_review' && node.role !== 'manager') {
        node.shieldAngle += dtSec * 0.8;
        const targetShieldAlpha = 0.6;
        node.shieldAlpha += (targetShieldAlpha - node.shieldAlpha) * 0.05;
      } else {
        node.shieldAlpha += (0 - node.shieldAlpha) * 0.08;
      }

      // Employee orbital rotation
      for (const emp of node.employees) {
        if (emp.shatterProgress > 0) {
          // Shatter animation
          emp.shatterProgress = Math.min(emp.shatterProgress + dtSec * 0.5, 1);
          for (const p of emp.shatterParticles) {
            p.x += p.vx * dtSec * 60;
            p.y += p.vy * dtSec * 60;
            p.life -= dtSec * 0.8;
            p.vx *= 0.98;
            p.vy *= 0.98;
          }
          emp.shatterParticles = emp.shatterParticles.filter(p => p.life > 0);
        } else {
          // Normal orbital rotation
          const speed = emp.status === 'working' ? 0.5 : emp.status === 'waiting' ? 1.2 : 0.1;
          emp.orbitAngle += dtSec * speed;
          // Glow pulse
          const targetGlow = emp.status === 'working' ? 0.6 + Math.sin(this.time * 4) * 0.2 : 0.3;
          emp.glow += (targetGlow - emp.glow) * 0.05;
        }
      }

      // Remove fully dissolved employees
      node.employees = node.employees.filter(e => e.shatterProgress < 1);
    }

    // Particles
    for (const p of this.particles) {
      p.life += dt;
      p.x += p.vx * (dt / 16);
      p.y += p.vy * (dt / 16);

      // Gravity toward active nodes
      if (this.intensity > 0.05) {
        for (const node of this.nodes) {
          if (node.isActive) {
            const dx = node.x - p.x, dy = node.y - p.y;
            const dist = Math.sqrt(dx * dx + dy * dy);
            if (dist > 15 && dist < 250) {
              const pull = this.intensity * 0.012 * (dt / 16) * (1 - dist / 250);
              p.vx += (dx / dist) * pull;
              p.vy += (dy / dist) * pull;
            }
          }
        }
        p.vx *= 0.996;
        p.vy *= 0.996;
      }

      const lr = p.life / p.maxLife;
      p.alpha = lr < 0.1 ? lr * 10 : lr > 0.9 ? (1 - lr) * 10 : 1;
      if (p.life > p.maxLife) Object.assign(p, this.createParticle(false));
    }

    // Data diamonds
    for (let i = this.diamonds.length - 1; i >= 0; i--) {
      const d = this.diamonds[i];
      d.progress += d.speed * dt;

      const toNode = this.nodes[d.connectionTo];
      if (toNode) {
        const fromX = d.connectionFrom === -1 ? this.hubX : this.nodes[d.connectionFrom].x;
        const fromY = d.connectionFrom === -1 ? this.hubY : this.nodes[d.connectionFrom].y;
        const cpx = (fromX + toNode.x) / 2 + (fromY - toNode.y) * 0.15;
        const cpy = (fromY + toNode.y) / 2 - (fromX - toNode.x) * 0.15;

        const t = Math.min(d.progress, 1);
        const x = (1 - t) * (1 - t) * fromX + 2 * (1 - t) * t * cpx + t * t * toNode.x;
        const y = (1 - t) * (1 - t) * fromY + 2 * (1 - t) * t * cpy + t * t * toNode.y;
        d.trail.push({ x, y });
        if (d.trail.length > 5) d.trail.shift();
      }

      if (d.progress > 1) this.diamonds.splice(i, 1);
    }

    // Spawn flow diamonds for active connections
    if (this.intensity > 0.1 && Math.random() < this.intensity * 0.06) {
      for (let i = 0; i < this.nodes.length; i++) {
        if (this.nodes[i].isActive && this.diamonds.length < 30) {
          this.diamonds.push({
            progress: 0,
            speed: 0.0004 + Math.random() * 0.0003,
            connectionFrom: -1,
            connectionTo: i,
            trail: [],
            color: this.nodes[i].color,
          });
        }
      }
    }

    // Data stream text
    if (this.intensity > 0.2 && Math.random() < this.intensity * 0.02) {
      for (let i = 0; i < this.nodes.length; i++) {
        if (this.nodes[i].isActive && this.dataStreamTexts.length < 15) {
          const text = STREAM_TEXT_SAMPLES[Math.floor(Math.random() * STREAM_TEXT_SAMPLES.length)];
          this.dataStreamTexts.push({
            text,
            progress: 0,
            speed: 0.0003 + Math.random() * 0.0002,
            connectionTo: i,
            alpha: 0.4 + Math.random() * 0.3,
          });
        }
      }
    }
    for (let i = this.dataStreamTexts.length - 1; i >= 0; i--) {
      const st = this.dataStreamTexts[i];
      st.progress += st.speed * dt;
      if (st.progress > 1) this.dataStreamTexts.splice(i, 1);
    }

    // Ripples
    for (let i = this.ripples.length - 1; i >= 0; i--) {
      const r = this.ripples[i];
      r.radius += dt * 0.15;
      r.alpha -= dt * 0.0005;
      if (r.alpha <= 0 || r.radius > r.maxRadius) this.ripples.splice(i, 1);
    }

    // Reaper update
    if (this.reaper.active) {
      this.reaper.sweepProgress += dtSec * 0.4; // sweep takes ~2.5s
      this.reaper.sweepAngle = this.reaper.sweepProgress * Math.PI * 2;
      if (this.reaper.sweepProgress >= 1) {
        this.reaper.active = false;
        this.reaper.sweepProgress = 0;
        // Mark targeted employees for shatter
        for (const runId of this.reaper.targetRunIds) {
          for (const node of this.nodes) {
            const emp = node.employees.find(e => e.runId === runId);
            if (emp && emp.shatterProgress === 0) {
              this.startEmployeeShatter(emp, node);
            }
          }
        }
      }
      this.reaper.alpha = this.reaper.sweepProgress < 0.8 ? 1 : (1 - this.reaper.sweepProgress) * 5;
      this.reaper.hudAlpha = Math.max(0, this.reaper.hudAlpha - dtSec * 0.25);
    } else {
      this.reaper.hudAlpha = Math.max(0, this.reaper.hudAlpha - dtSec * 0.5);
    }

    // Thinking dots
    for (const dot of this.thinkingDots) {
      dot.angle += dot.speed * dtSec;
    }

    // Tool text alpha
    const targetAlpha = this.currentToolText ? 1 : 0;
    this.toolTextAlpha += (targetAlpha - this.toolTextAlpha) * 0.06;

    // Clean old events
    this.eventQueue = this.eventQueue.filter(e => this.time - e.time < 3);
  }

  // ── Draw ────────────────────────────────────────────────

  private draw() {
    const { ctx, w, h } = this;
    ctx.clearRect(0, 0, w, h);

    this.drawBackground();
    this.drawNebula();
    this.drawGrid();
    this.drawRadarSweep();
    this.drawParticles();
    this.drawConnections();
    this.drawDataStreamText();
    this.drawDiamonds();
    this.drawHub();
    this.drawNodes();
    this.drawEmployeeOrbitals();
    this.drawShieldRings();
    this.drawRipples();
    this.drawReaperSweep();
    this.drawThinkingDots();
    this.drawLabels();
    this.drawBloom();
  }

  // ── Layer 1: Background ──────────────────────────────────

  private drawBackground() {
    const { ctx, w, h } = this;
    const [cr, cg, cb] = this.getPhaseColor();

    const grad = ctx.createRadialGradient(this.hubX, this.hubY, 0, this.hubX, this.hubY, Math.max(w, h) * 0.7);
    grad.addColorStop(0, `rgba(${20 + cr * 0.03}, ${22 + cg * 0.02}, ${35 + cb * 0.02}, 1)`);
    grad.addColorStop(1, `rgba(15, 17, 25, 1)`);
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, w, h);
  }

  // ── Layer 2: Nebula ─────────────────────────────────────

  private drawNebula() {
    const { ctx, w, h } = this;
    const t = this.nebulaClock;
    const [cr, cg, cb] = this.getPhaseColor();
    // Parallax offset for nebula (deeper layer = more shift)
    const px = this.parallaxX * 3;
    const py = this.parallaxY * 3;

    const nebulae = [
      { cx: 0.3 + Math.sin(t * 0.7) * 0.08, cy: 0.35 + Math.cos(t * 0.5) * 0.06, r: 0.35, color: [cr * 0.3, cg * 0.3, cb * 0.6] },
      { cx: 0.7 + Math.cos(t * 0.6) * 0.07, cy: 0.65 + Math.sin(t * 0.8) * 0.05, r: 0.3, color: [cr * 0.5, cg * 0.15, cb * 0.5] },
    ];

    for (const n of nebulae) {
      const grad = ctx.createRadialGradient(n.cx * w + px, n.cy * h + py, 0, n.cx * w + px, n.cy * h + py, n.r * Math.min(w, h));
      grad.addColorStop(0, `rgba(${n.color[0]}, ${n.color[1]}, ${n.color[2]}, 0.05)`);
      grad.addColorStop(0.5, `rgba(${n.color[0]}, ${n.color[1]}, ${n.color[2]}, 0.02)`);
      grad.addColorStop(1, `rgba(${n.color[0]}, ${n.color[1]}, ${n.color[2]}, 0)`);
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, w, h);
    }
  }

  // ── Layer 3: Grid ───────────────────────────────────────

  private drawGrid() {
    const { ctx, w, h } = this;
    const [cr, cg, cb] = this.getPhaseColor();
    const isActive = this.phase !== 'idle';
    const alpha = isActive ? 0.04 : 0.02;
    // Parallax for grid (mid layer)
    const px = this.parallaxX * 1.5;
    const py = this.parallaxY * 1.5;

    const maxR = Math.max(w, h) * 0.8;
    const ringSpacing = this.orbitRadius * 0.4;

    ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${alpha})`;
    ctx.lineWidth = 0.5;
    for (let r = ringSpacing; r < maxR; r += ringSpacing) {
      ctx.beginPath();
      ctx.arc(this.hubX + px, this.hubY + py, r, 0, Math.PI * 2);
      ctx.stroke();
    }

    // 8 radial lines
    ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${alpha * 0.7})`;
    for (let i = 0; i < 8; i++) {
      const angle = (i / 8) * Math.PI * 2;
      ctx.beginPath();
      ctx.moveTo(this.hubX + px, this.hubY + py);
      ctx.lineTo(this.hubX + px + Math.cos(angle) * maxR, this.hubY + py + Math.sin(angle) * maxR);
      ctx.stroke();
    }
  }

  // ── Layer 4: Radar sweep ────────────────────────────────

  private drawRadarSweep() {
    if (this.phase === 'idle') return;
    const { ctx } = this;
    const [cr, cg, cb] = this.getPhaseColor();
    const sweepR = this.orbitRadius * 1.15;

    const grad = ctx.createConicGradient(this.radarAngle, this.hubX, this.hubY);
    grad.addColorStop(0, `rgba(${cr}, ${cg}, ${cb}, 0.06)`);
    grad.addColorStop(0.08, `rgba(${cr}, ${cg}, ${cb}, 0.02)`);
    grad.addColorStop(0.15, `rgba(${cr}, ${cg}, ${cb}, 0)`);
    grad.addColorStop(1, `rgba(${cr}, ${cg}, ${cb}, 0)`);

    ctx.beginPath();
    ctx.arc(this.hubX, this.hubY, sweepR, 0, Math.PI * 2);
    ctx.fillStyle = grad;
    ctx.fill();
  }

  // ── Layer 5: Particles ──────────────────────────────────

  private drawParticles() {
    const { ctx } = this;
    const [cr, cg, cb] = this.getPhaseColor();
    // Parallax for particles (shallow layer)
    const px = this.parallaxX * 0.5;
    const py = this.parallaxY * 0.5;

    for (const p of this.particles) {
      const a = p.alpha * 0.2;
      if (a < 0.01) continue;
      ctx.beginPath();
      ctx.arc(p.x + px, p.y + py, p.size, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, ${a})`;
      ctx.fill();
    }
  }

  // ── Layer 6: Connections — Dual-Rail Energy Conduits ────

  private drawConnections() {
    const { ctx } = this;

    for (let i = 0; i < this.nodes.length; i++) {
      const node = this.nodes[i];
      const cpx = (node.x + this.hubX) / 2 + (node.y - this.hubY) * 0.15;
      const cpy = (node.y + this.hubY) / 2 - (node.x - this.hubX) * 0.15;

      if (node.isActive) {
        const [cr, cg, cb] = node.color;
        const pulse = 0.5 + 0.5 * Math.sin(this.hubPulse * 2);

        // Normal vector for dual-rail offset
        const nx = -(node.y - this.hubY);
        const ny = (node.x - this.hubX);
        const len = Math.sqrt(nx * nx + ny * ny) || 1;
        const ux = nx / len;
        const uy = ny / len;
        const railGap = 3; // distance between rails

        // Dual-rail energy conduit
        for (const side of [-1, 1]) {
          const ox = ux * railGap * side;
          const oy = uy * railGap * side;

          // Rail line
          ctx.beginPath();
          ctx.moveTo(this.hubX + ox, this.hubY + oy);
          ctx.quadraticCurveTo(cpx + ox, cpy + oy, node.x + ox, node.y + oy);
          ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${0.15 + this.intensity * 0.1 + pulse * 0.05})`;
          ctx.lineWidth = 1;
          ctx.stroke();
        }

        // Center glow beam (between rails)
        ctx.beginPath();
        ctx.moveTo(this.hubX, this.hubY);
        ctx.quadraticCurveTo(cpx, cpy, node.x, node.y);
        ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${0.03 + this.intensity * 0.04})`;
        ctx.lineWidth = railGap * 2 + 2;
        ctx.stroke();

        // Energy particles flowing between rails
        const numEnergyDots = Math.floor(3 + this.intensity * 5);
        for (let j = 0; j < numEnergyDots; j++) {
          const t = ((this.time * 0.15 + j / numEnergyDots) % 1);
          const ex = (1 - t) * (1 - t) * this.hubX + 2 * (1 - t) * t * cpx + t * t * node.x;
          const ey = (1 - t) * (1 - t) * this.hubY + 2 * (1 - t) * t * cpy + t * t * node.y;
          // Oscillate between rails
          const wave = Math.sin(t * Math.PI * 6 + this.time * 3) * railGap;
          const dotX = ex + ux * wave;
          const dotY = ey + uy * wave;
          const dotAlpha = Math.sin(t * Math.PI) * (0.3 + this.intensity * 0.4);
          ctx.beginPath();
          ctx.arc(dotX, dotY, 1.5, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, ${dotAlpha})`;
          ctx.fill();
        }

        // Directional chevron at 60% along curve
        const ct = 0.6;
        const mx = (1 - ct) * (1 - ct) * this.hubX + 2 * (1 - ct) * ct * cpx + ct * ct * node.x;
        const my = (1 - ct) * (1 - ct) * this.hubY + 2 * (1 - ct) * ct * cpy + ct * ct * node.y;
        const ct2 = ct + 0.02;
        const mx2 = (1 - ct2) * (1 - ct2) * this.hubX + 2 * (1 - ct2) * ct2 * cpx + ct2 * ct2 * node.x;
        const my2 = (1 - ct2) * (1 - ct2) * this.hubY + 2 * (1 - ct2) * ct2 * cpy + ct2 * ct2 * node.y;
        const ang = Math.atan2(my2 - my, mx2 - mx);

        ctx.beginPath();
        ctx.moveTo(mx + Math.cos(ang) * 6, my + Math.sin(ang) * 6);
        ctx.lineTo(mx + Math.cos(ang + 2.5) * 4, my + Math.sin(ang + 2.5) * 4);
        ctx.moveTo(mx + Math.cos(ang) * 6, my + Math.sin(ang) * 6);
        ctx.lineTo(mx + Math.cos(ang - 2.5) * 4, my + Math.sin(ang - 2.5) * 4);
        ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, 0.4)`;
        ctx.lineWidth = 1.5;
        ctx.stroke();
      } else {
        // Inactive: faint dashed
        ctx.beginPath();
        ctx.moveTo(this.hubX, this.hubY);
        ctx.quadraticCurveTo(cpx, cpy, node.x, node.y);
        ctx.strokeStyle = `rgba(100, 110, 130, ${0.05 * node.opacity})`;
        ctx.lineWidth = 0.5;
        ctx.setLineDash([3, 8]);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }
  }

  // ── Layer 6b: Data Stream Text ──────────────────────────

  private drawDataStreamText() {
    const { ctx } = this;
    ctx.font = '8px "SF Mono", ui-monospace, monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    for (const st of this.dataStreamTexts) {
      const toNode = this.nodes[st.connectionTo];
      if (!toNode) continue;
      const [cr, cg, cb] = toNode.color;

      const cpx = (toNode.x + this.hubX) / 2 + (toNode.y - this.hubY) * 0.15;
      const cpy = (toNode.y + this.hubY) / 2 - (toNode.x - this.hubX) * 0.15;

      const t = st.progress;
      const x = (1 - t) * (1 - t) * this.hubX + 2 * (1 - t) * t * cpx + t * t * toNode.x;
      const y = (1 - t) * (1 - t) * this.hubY + 2 * (1 - t) * t * cpy + t * t * toNode.y;

      // Fade at edges
      const fadeAlpha = Math.sin(t * Math.PI) * st.alpha;

      ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, ${fadeAlpha * 0.5})`;
      ctx.fillText(st.text, x, y - 8);
    }
  }

  // ── Layer 7: Data diamonds ──────────────────────────────

  private drawDiamonds() {
    const { ctx } = this;
    ctx.globalCompositeOperation = 'lighter';

    for (const d of this.diamonds) {
      if (d.trail.length < 2) continue;
      const head = d.trail[d.trail.length - 1];
      const [cr, cg, cb] = d.color;
      const fadeAlpha = 1 - d.progress * 0.3;

      // Trail
      ctx.beginPath();
      ctx.moveTo(d.trail[0].x, d.trail[0].y);
      for (let i = 1; i < d.trail.length; i++) {
        ctx.lineTo(d.trail[i].x, d.trail[i].y);
      }
      ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${0.3 * fadeAlpha})`;
      ctx.lineWidth = 1;
      ctx.stroke();

      // Diamond head (elongated)
      const angle = d.trail.length > 1
        ? Math.atan2(head.y - d.trail[d.trail.length - 2].y, head.x - d.trail[d.trail.length - 2].x)
        : 0;
      ctx.save();
      ctx.translate(head.x, head.y);
      ctx.rotate(angle);
      ctx.beginPath();
      ctx.moveTo(5, 0);
      ctx.lineTo(0, 2);
      ctx.lineTo(-5, 0);
      ctx.lineTo(0, -2);
      ctx.closePath();
      ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, ${0.7 * fadeAlpha})`;
      ctx.fill();
      ctx.restore();

      // Glow
      ctx.beginPath();
      ctx.arc(head.x, head.y, 4, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, ${0.1 * fadeAlpha})`;
      ctx.fill();
    }

    ctx.globalCompositeOperation = 'source-over';
  }

  // ── Layer 8: Hub ────────────────────────────────────────

  private drawHub() {
    const { ctx } = this;
    const pulse = Math.sin(this.hubPulse) * 0.3 + 0.7;
    const r = this.hubRadius;
    const [cr, cg, cb] = this.getThreatLevelColor();

    // Deep glow
    if (this.serviceActive) {
      const breathe = 10 * Math.sin(this.time * 0.3 * Math.PI * 2);
      const glowR = r * 3.5 + breathe;
      const grad = ctx.createRadialGradient(this.hubX, this.hubY, r * 0.3, this.hubX, this.hubY, glowR);
      grad.addColorStop(0, `rgba(${cr}, ${cg}, ${cb}, ${0.06 * pulse})`);
      grad.addColorStop(0.5, `rgba(${cr}, ${cg}, ${cb}, ${0.03 * pulse})`);
      grad.addColorStop(1, `rgba(${cr}, ${cg}, ${cb}, 0)`);
      ctx.beginPath();
      ctx.arc(this.hubX, this.hubY, glowR, 0, Math.PI * 2);
      ctx.fillStyle = grad;
      ctx.fill();
    }

    // Concentric rings: 3 rings — pulse faster with threat level
    const threatPulseSpeed = 1 + this.intensity * 3;
    const rings = [
      { r: r * 1.8, dash: [0, 0], speed: 0, alpha: 0.15 },
      { r: r * 1.5, dash: [3, 8], speed: 0.2 * threatPulseSpeed, alpha: 0.1 },
      { r: r * 1.2, dash: [3, 8], speed: -0.3 * threatPulseSpeed, alpha: 0.06 },
    ];
    for (const ring of rings) {
      ctx.beginPath();
      ctx.arc(this.hubX, this.hubY, ring.r, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${ring.alpha * (this.serviceActive ? 1 : 0.4)})`;
      ctx.lineWidth = 1;
      if (ring.dash[0] > 0) {
        ctx.setLineDash(ring.dash);
        ctx.lineDashOffset = this.time * ring.speed * 50;
      }
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.lineDashOffset = 0;
    }

    // Phase core circle
    ctx.beginPath();
    ctx.arc(this.hubX, this.hubY, r, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(20, 22, 30, 0.9)`;
    ctx.fill();
    ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${this.serviceActive ? 0.4 + pulse * 0.2 : 0.15})`;
    ctx.lineWidth = 2;
    ctx.stroke();

    // Phase label inside hub
    const phaseText = this.getPhaseLabel() || 'IDLE';
    const fontSize = Math.max(8, r * 0.35);
    ctx.font = `600 ${fontSize}px system-ui, -apple-system, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, ${this.serviceActive ? 0.7 : 0.3})`;
    ctx.fillText(phaseText, this.hubX, this.hubY);

    // Usage arc (270° sweep)
    if (this.usagePercent > 0) {
      const usageR = r + 6;
      const startAngle = -Math.PI / 2;
      const endAngle = startAngle + (this.usagePercent / 100) * (Math.PI * 1.5);

      ctx.beginPath();
      ctx.arc(this.hubX, this.hubY, usageR, startAngle, startAngle + Math.PI * 1.5);
      ctx.strokeStyle = 'rgba(71, 85, 105, 0.1)';
      ctx.lineWidth = 2;
      ctx.stroke();

      ctx.beginPath();
      ctx.arc(this.hubX, this.hubY, usageR, startAngle, endAngle);
      ctx.strokeStyle = this.usagePercent > 80
        ? 'rgba(239, 68, 68, 0.5)'
        : `rgba(${cr}, ${cg}, ${cb}, 0.4)`;
      ctx.lineWidth = 2;
      ctx.lineCap = 'round';
      ctx.stroke();
      ctx.lineCap = 'butt';
    }

    // Energy arcs
    if (this.intensity > 0.3 && this.serviceActive) {
      const arcR = r * 1.65;
      const alpha = (this.intensity - 0.3) * 0.6;
      ctx.lineWidth = 2;
      ctx.lineCap = 'round';
      for (let i = 0; i < 3; i++) {
        const startAngle = this.hubArcAngle * 1.5 + (i * Math.PI * 2) / 3;
        ctx.beginPath();
        ctx.arc(this.hubX, this.hubY, arcR, startAngle, startAngle + Math.PI * 0.35);
        ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${alpha})`;
        ctx.stroke();
      }
      ctx.lineCap = 'butt';
    }
  }

  // ── Layer 9: Agent nodes ────────────────────────────────

  private drawNodes() {
    for (const node of this.nodes) {
      this.drawAgentNode(node);
    }
  }

  private drawAgentNode(node: AgentNode) {
    const { ctx } = this;
    const [cr, cg, cb] = node.color;
    const nodeR = 28 * node.scale;

    ctx.save();
    ctx.globalAlpha = node.opacity;

    // Layer 1: Gravity well glow
    if (node.glowIntensity > 0.05) {
      const grad = ctx.createRadialGradient(node.x, node.y, nodeR * 0.2, node.x, node.y, 120);
      grad.addColorStop(0, `rgba(${cr}, ${cg}, ${cb}, ${0.03 * node.glowIntensity})`);
      grad.addColorStop(1, `rgba(${cr}, ${cg}, ${cb}, 0)`);
      ctx.beginPath();
      ctx.arc(node.x, node.y, 120, 0, Math.PI * 2);
      ctx.fillStyle = grad;
      ctx.fill();
    }

    // Layer 2: Outer tech ring — rotating arc segments
    const outerR = nodeR + 12;
    ctx.lineWidth = 1;
    for (let i = 0; i < 3; i++) {
      const segStart = node.arcRotation + (i * Math.PI * 2) / 3;
      const segSweep = Math.PI / 3;
      ctx.beginPath();
      ctx.arc(node.x, node.y, outerR, segStart, segStart + segSweep);
      const arcAlpha = node.isActive ? 0.5 + (node.isThinking ? 0.3 * Math.sin(this.time * 9.4) : 0) : 0.2;
      ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${arcAlpha})`;
      ctx.stroke();
    }

    // Layer 3: Data arc ring (turn count as progress)
    if (node.turnCount > 0) {
      const dataR = nodeR + 6;
      const progress = Math.min(node.turnCount / 500, 1); // normalize to 500 turns
      const startAngle = -Math.PI / 2;
      const endAngle = startAngle + progress * Math.PI * 2;

      ctx.beginPath();
      ctx.arc(node.x, node.y, dataR, startAngle, endAngle);
      ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, 0.4)`;
      ctx.lineWidth = 3;
      ctx.lineCap = 'round';
      ctx.stroke();
      ctx.lineCap = 'butt';
    }

    // Layer 4: Core circle
    ctx.beginPath();
    ctx.arc(node.x, node.y, nodeR, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(20, 22, 30, 0.9)';
    ctx.fill();
    ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${node.isActive ? 0.6 : 0.25})`;
    ctx.lineWidth = 2;
    ctx.stroke();

    // Icon
    this.drawAgentIcon(node.x, node.y, nodeR * 0.5, node.role, cr, cg, cb);

    // Layer 5: Heartbeat core
    if (node.isActive) {
      const hb = node.heartbeatPhase;
      // Lub-dub: two peaks in one cycle
      const t = hb % (Math.PI * 2);
      const s = 1 + 0.4 * Math.max(0, Math.sin(t) * Math.exp(-0.5 * (t % Math.PI)));
      const hbAlpha = 0.5 + 0.5 * Math.max(0, Math.sin(t) * Math.exp(-0.5 * (t % Math.PI)));

      ctx.beginPath();
      ctx.arc(node.x, node.y, 3 * s, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(255, 255, 255, ${hbAlpha})`;
      ctx.shadowColor = `rgba(${cr}, ${cg}, ${cb}, 0.8)`;
      ctx.shadowBlur = 4;
      ctx.fill();
      ctx.shadowBlur = 0;
    } else {
      ctx.beginPath();
      ctx.arc(node.x, node.y, 3, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(255, 255, 255, 0.2)`;
      ctx.fill();
    }

    // Layer 6: Status dot
    const statusColor = node.isActive ? [34, 197, 94]
      : node.isThinking ? [245, 158, 11]
      : [107, 114, 128];
    const sdx = node.x + nodeR * 0.7;
    const sdy = node.y + nodeR * 0.7;
    ctx.beginPath();
    ctx.arc(sdx, sdy, 5, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(20, 22, 30, 1)`;
    ctx.fill();
    ctx.beginPath();
    ctx.arc(sdx, sdy, 3.5, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(${statusColor[0]}, ${statusColor[1]}, ${statusColor[2]}, 0.9)`;
    ctx.fill();

    ctx.restore();
  }

  // ── Layer 9b: Employee Orbitals ─────────────────────────

  private drawEmployeeOrbitals() {
    const { ctx } = this;

    for (const node of this.nodes) {
      for (const emp of node.employees) {
        // Determine orbit radius — worktree employees on outer ring
        const baseOrbitR = 28 * node.scale + 18;
        const empOrbitR = emp.inWorktree ? baseOrbitR + 14 : baseOrbitR;
        const ex = node.x + Math.cos(emp.orbitAngle) * empOrbitR;
        const ey = node.y + Math.sin(emp.orbitAngle) * empOrbitR;

        // Skip if fully shattered
        if (emp.shatterProgress >= 1) continue;

        const [sr, sg, sb] = EMPLOYEE_STATUS_COLORS[emp.status] ?? [100, 100, 100];
        const dotR = 6;

        ctx.save();

        // Shatter fade
        if (emp.shatterProgress > 0) {
          ctx.globalAlpha = 1 - emp.shatterProgress;
        }

        // Worktree indicator: separate orbit ring (dashed)
        if (emp.inWorktree) {
          ctx.beginPath();
          ctx.arc(node.x, node.y, empOrbitR, emp.orbitAngle - 0.3, emp.orbitAngle + 0.3);
          ctx.strokeStyle = `rgba(${sr}, ${sg}, ${sb}, 0.15)`;
          ctx.lineWidth = 1;
          ctx.setLineDash([2, 4]);
          ctx.stroke();
          ctx.setLineDash([]);
          // Small "W" badge
          ctx.font = '6px "SF Mono", ui-monospace, monospace';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillStyle = `rgba(${sr}, ${sg}, ${sb}, 0.5)`;
          ctx.fillText('W', ex, ey + dotR + 7);
        }

        // Glow
        if (emp.glow > 0.1) {
          const grad = ctx.createRadialGradient(ex, ey, dotR * 0.3, ex, ey, dotR * 3);
          grad.addColorStop(0, `rgba(${sr}, ${sg}, ${sb}, ${0.15 * emp.glow})`);
          grad.addColorStop(1, `rgba(${sr}, ${sg}, ${sb}, 0)`);
          ctx.beginPath();
          ctx.arc(ex, ey, dotR * 3, 0, Math.PI * 2);
          ctx.fillStyle = grad;
          ctx.fill();
        }

        // Mini-hexagon body
        ctx.beginPath();
        for (let v = 0; v < 6; v++) {
          const a = (v / 6) * Math.PI * 2 - Math.PI / 2;
          const hx = ex + Math.cos(a) * dotR;
          const hy = ey + Math.sin(a) * dotR;
          if (v === 0) ctx.moveTo(hx, hy);
          else ctx.lineTo(hx, hy);
        }
        ctx.closePath();
        ctx.fillStyle = 'rgba(20, 22, 30, 0.9)';
        ctx.fill();
        ctx.strokeStyle = `rgba(${sr}, ${sg}, ${sb}, 0.7)`;
        ctx.lineWidth = 1.5;
        ctx.stroke();

        // Status indicator: pulsing core
        if (emp.status === 'working') {
          // Pulsing blue dot
          const pulseSize = 2 + Math.sin(this.time * 4 + emp.index) * 0.8;
          ctx.beginPath();
          ctx.arc(ex, ey, pulseSize, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(${sr}, ${sg}, ${sb}, 0.8)`;
          ctx.fill();
        } else if (emp.status === 'waiting') {
          // Spinning amber indicator
          const spinAngle = this.time * 3 + emp.index;
          ctx.beginPath();
          ctx.arc(ex, ey, 3, spinAngle, spinAngle + Math.PI);
          ctx.strokeStyle = `rgba(${sr}, ${sg}, ${sb}, 0.8)`;
          ctx.lineWidth = 1.5;
          ctx.stroke();
        } else if (emp.status === 'completed') {
          // Green checkmark flash
          ctx.strokeStyle = `rgba(${sr}, ${sg}, ${sb}, 0.9)`;
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.moveTo(ex - 2, ey);
          ctx.lineTo(ex, ey + 2);
          ctx.lineTo(ex + 3, ey - 2);
          ctx.stroke();
        } else if (emp.status === 'failed') {
          // Red X
          ctx.strokeStyle = `rgba(${sr}, ${sg}, ${sb}, 0.9)`;
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.moveTo(ex - 2, ey - 2);
          ctx.lineTo(ex + 2, ey + 2);
          ctx.moveTo(ex + 2, ey - 2);
          ctx.lineTo(ex - 2, ey + 2);
          ctx.stroke();
        } else if (emp.status === 'reaped') {
          // Skull/cross
          ctx.strokeStyle = `rgba(${sr}, ${sg}, ${sb}, 0.7)`;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(ex - 2, ey - 2);
          ctx.lineTo(ex + 2, ey + 2);
          ctx.moveTo(ex + 2, ey - 2);
          ctx.lineTo(ex - 2, ey + 2);
          ctx.stroke();
        }

        // Employee index label
        ctx.font = '7px "SF Mono", ui-monospace, monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = `rgba(${sr}, ${sg}, ${sb}, 0.6)`;
        ctx.fillText(`${emp.index}`, ex, ey - dotR - 4);

        ctx.restore();

        // Draw shatter particles (outside save/restore for full alpha)
        if (emp.shatterProgress > 0) {
          for (const p of emp.shatterParticles) {
            if (p.life <= 0) continue;
            ctx.beginPath();
            ctx.arc(p.x, p.y, p.size * p.life, 0, Math.PI * 2);
            ctx.fillStyle = `rgba(${sr}, ${sg}, ${sb}, ${p.life * 0.8})`;
            ctx.fill();
          }
        }
      }
    }
  }

  // ── Layer 9c: Shield Rings ──────────────────────────────

  private drawShieldRings() {
    const { ctx } = this;

    for (const node of this.nodes) {
      if (node.shieldAlpha < 0.02 || node.role === 'manager') continue;

      const [cr, cg, cb] = [245, 158, 11]; // amber shield
      const nodeR = 28 * node.scale;
      const shieldR = nodeR + 20;
      const alpha = node.shieldAlpha;

      // Rotating shield segments (barrier ring)
      const numSegments = 4;
      const segSweep = Math.PI / (numSegments + 1);
      ctx.lineWidth = 2.5;
      ctx.lineCap = 'round';
      for (let i = 0; i < numSegments; i++) {
        const segStart = node.shieldAngle + (i * Math.PI * 2) / numSegments;
        ctx.beginPath();
        ctx.arc(node.x, node.y, shieldR, segStart, segStart + segSweep);
        ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${alpha * 0.6})`;
        ctx.stroke();

        // Inner glow of shield segment
        ctx.beginPath();
        ctx.arc(node.x, node.y, shieldR - 1, segStart + 0.05, segStart + segSweep - 0.05);
        ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${alpha * 0.2})`;
        ctx.lineWidth = 4;
        ctx.stroke();
        ctx.lineWidth = 2.5;
      }
      ctx.lineCap = 'butt';

      // Shield glow ring
      ctx.beginPath();
      ctx.arc(node.x, node.y, shieldR, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${alpha * 0.08})`;
      ctx.lineWidth = 8;
      ctx.stroke();
    }
  }

  // ── Layer 10: Ripples ───────────────────────────────────

  private drawRipples() {
    const { ctx } = this;
    for (const r of this.ripples) {
      ctx.beginPath();
      ctx.arc(r.x, r.y, r.radius, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(${r.color[0]}, ${r.color[1]}, ${r.color[2]}, ${r.alpha * 0.6})`;
      ctx.lineWidth = 1.5 * r.alpha;
      ctx.stroke();
    }
  }

  // ── Layer 10b: Reaper Sweep ─────────────────────────────

  private drawReaperSweep() {
    const { ctx, w, h } = this;

    if (!this.reaper.active && this.reaper.hudAlpha <= 0) return;

    // Scythe-shaped sweep arc
    if (this.reaper.active && this.reaper.alpha > 0) {
      const sweepR = Math.max(w, h) * 0.6;
      const angle = this.reaper.sweepAngle - Math.PI / 2;
      const arcWidth = Math.PI * 0.15;

      // Crimson energy wave
      const grad = ctx.createConicGradient(angle - arcWidth, this.hubX, this.hubY);
      grad.addColorStop(0, `rgba(180, 30, 30, 0)`);
      grad.addColorStop(0.3, `rgba(220, 38, 38, ${0.12 * this.reaper.alpha})`);
      grad.addColorStop(0.6, `rgba(239, 68, 68, ${0.08 * this.reaper.alpha})`);
      grad.addColorStop(1, `rgba(180, 30, 30, 0)`);

      ctx.beginPath();
      ctx.moveTo(this.hubX, this.hubY);
      ctx.arc(this.hubX, this.hubY, sweepR, angle - arcWidth, angle + arcWidth);
      ctx.closePath();
      ctx.fillStyle = grad;
      ctx.fill();

      // Leading edge — bright crimson line
      ctx.beginPath();
      ctx.moveTo(this.hubX, this.hubY);
      ctx.lineTo(
        this.hubX + Math.cos(angle) * sweepR,
        this.hubY + Math.sin(angle) * sweepR,
      );
      ctx.strokeStyle = `rgba(239, 68, 68, ${0.5 * this.reaper.alpha})`;
      ctx.lineWidth = 2;
      ctx.stroke();
    }

    // HUD callout text
    if (this.reaper.hudAlpha > 0 && this.reaper.hudText) {
      ctx.font = '600 12px "SF Mono", ui-monospace, monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = `rgba(239, 68, 68, ${this.reaper.hudAlpha * 0.9})`;
      ctx.fillText(this.reaper.hudText, w / 2, h * 0.12);

      // Underline flash
      const tw = ctx.measureText(this.reaper.hudText).width;
      ctx.beginPath();
      ctx.moveTo(w / 2 - tw / 2, h * 0.12 + 10);
      ctx.lineTo(w / 2 + tw / 2, h * 0.12 + 10);
      ctx.strokeStyle = `rgba(239, 68, 68, ${this.reaper.hudAlpha * 0.4})`;
      ctx.lineWidth = 1;
      ctx.stroke();
    }
  }

  // ── Layer 11: Thinking dots ─────────────────────────────

  private drawThinkingDots() {
    const { ctx } = this;
    for (const dot of this.thinkingDots) {
      const node = this.nodes[dot.nodeIndex];
      if (!node) continue;
      const [cr, cg, cb] = node.color;
      const orbitR = 44 * node.scale;
      const x = node.x + Math.cos(dot.angle) * orbitR;
      const y = node.y + Math.sin(dot.angle) * orbitR;
      ctx.beginPath();
      ctx.arc(x, y, 2.5, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, 0.5)`;
      ctx.fill();
    }
  }

  // ── Layer 12: Labels ────────────────────────────────────

  private drawLabels() {
    const { ctx } = this;

    for (const node of this.nodes) {
      const [cr, cg, cb] = node.color;
      const labelY = node.y + 28 * node.scale + 16;

      ctx.save();
      ctx.globalAlpha = node.opacity;

      // Name
      ctx.font = '600 12px Inter, system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, 0.85)`;
      ctx.fillText(node.name, node.x, labelY);

      // Activity text
      if (node.currentAction) {
        const action = node.currentAction.length > 24 ? node.currentAction.slice(0, 24) + '...' : node.currentAction;
        ctx.font = '10px Inter, system-ui, sans-serif';
        ctx.fillStyle = `rgba(200, 205, 215, 0.5)`;
        ctx.fillText(action, node.x, labelY + 16);
      }

      // Metric badge
      if (node.turnCount > 0) {
        const metricText = node.tokenCount > 1000
          ? `${(node.tokenCount / 1000).toFixed(0)}K tok`
          : `${node.turnCount} turns`;
        ctx.font = '9px "SF Mono", ui-monospace, monospace';
        const tw = ctx.measureText(metricText).width;
        const badgeX = node.x - tw / 2 - 4;
        const badgeY = labelY + (node.currentAction ? 30 : 16);
        ctx.beginPath();
        ctx.roundRect(badgeX, badgeY, tw + 8, 14, 3);
        ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, 0.1)`;
        ctx.fill();
        ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, 0.6)`;
        ctx.textBaseline = 'middle';
        ctx.fillText(metricText, node.x, badgeY + 7);
      }

      ctx.restore();
    }
  }

  // ── Layer 13: Bloom ─────────────────────────────────────

  private drawBloom() {
    if (!this.bloomCanvas || !this.bloomCtx) return;
    const { ctx } = this;
    const bc = this.bloomCtx;
    const bw = this.bloomCanvas.width;
    const bh = this.bloomCanvas.height;

    bc.clearRect(0, 0, bw, bh);
    bc.drawImage(this.canvas, 0, 0, bw, bh);

    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.globalAlpha = 0.1;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.drawImage(this.bloomCanvas, 0, 0, this.canvas.width, this.canvas.height);
    ctx.restore();
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
  }

  private drawAgentIcon(cx: number, cy: number, size: number, role: string, cr: number, cg: number, cb: number) {
    const { ctx } = this;
    ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, 0.8)`;
    ctx.lineWidth = 1.5;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    const s = size;

    switch (role) {
      case 'manager':
        // Shield
        ctx.beginPath();
        ctx.moveTo(cx, cy - s);
        ctx.lineTo(cx - s * 0.8, cy - s * 0.4);
        ctx.lineTo(cx - s * 0.8, cy + s * 0.2);
        ctx.quadraticCurveTo(cx, cy + s, cx, cy + s);
        ctx.quadraticCurveTo(cx, cy + s, cx + s * 0.8, cy + s * 0.2);
        ctx.lineTo(cx + s * 0.8, cy - s * 0.4);
        ctx.closePath();
        ctx.stroke();
        break;
      case 'coordinator':
        // Network nodes
        const r = s * 0.3;
        ctx.beginPath();
        ctx.arc(cx, cy - s * 0.5, r, 0, Math.PI * 2);
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(cx - s * 0.5, cy + s * 0.4, r, 0, Math.PI * 2);
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(cx + s * 0.5, cy + s * 0.4, r, 0, Math.PI * 2);
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(cx, cy - s * 0.2);
        ctx.lineTo(cx - s * 0.35, cy + s * 0.2);
        ctx.moveTo(cx, cy - s * 0.2);
        ctx.lineTo(cx + s * 0.35, cy + s * 0.2);
        ctx.stroke();
        break;
      case 'analyst':
        // Magnifying glass
        ctx.beginPath();
        ctx.arc(cx - s * 0.1, cy - s * 0.1, s * 0.5, 0, Math.PI * 2);
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(cx + s * 0.25, cy + s * 0.25);
        ctx.lineTo(cx + s * 0.7, cy + s * 0.7);
        ctx.lineWidth = 2;
        ctx.stroke();
        ctx.lineWidth = 1.5;
        break;
      default:
        // Wrench
        ctx.beginPath();
        ctx.moveTo(cx + s * 0.5, cy - s * 0.7);
        ctx.lineTo(cx + s * 0.1, cy - s * 0.1);
        ctx.lineTo(cx + s * 0.3, cy + s * 0.1);
        ctx.lineTo(cx - s * 0.3, cy + s * 0.7);
        ctx.moveTo(cx + s * 0.1, cy - s * 0.1);
        ctx.lineTo(cx - s * 0.1, cy + s * 0.1);
        ctx.stroke();
        break;
    }

    ctx.lineCap = 'butt';
    ctx.lineJoin = 'miter';
  }

  // ── Helpers ──────────────────────────────────────────────

  private getPhaseColor(): [number, number, number] {
    switch (this.phase) {
      case 'coordinating': return [168, 85, 247];
      case 'employee': return [59, 130, 246];
      case 'manager_review': return [245, 158, 11];
      case 'executing_verdict': return [16, 185, 129];
      default: return [59, 130, 246];
    }
  }

  /** Threat level color — hub shifts spectrum based on system load */
  private getThreatLevelColor(): [number, number, number] {
    const load = this.intensity;
    if (load < 0.25) {
      // Calm blue
      return [59, 130, 246];
    } else if (load < 0.5) {
      // Active cyan
      const t = (load - 0.25) / 0.25;
      return [
        Math.round(59 + (6 - 59) * t),
        Math.round(130 + (182 - 130) * t),
        Math.round(246 + (212 - 246) * t),
      ];
    } else if (load < 0.75) {
      // Intense purple
      const t = (load - 0.5) / 0.25;
      return [
        Math.round(6 + (168 - 6) * t),
        Math.round(182 + (85 - 182) * t),
        Math.round(212 + (247 - 212) * t),
      ];
    } else {
      // Critical red
      const t = (load - 0.75) / 0.25;
      return [
        Math.round(168 + (239 - 168) * t),
        Math.round(85 + (68 - 85) * t),
        Math.round(247 + (68 - 247) * t),
      ];
    }
  }

  private getPhaseLabel(): string {
    switch (this.phase) {
      case 'coordinating': return 'COORD';
      case 'employee': return 'WORKING';
      case 'manager_review': return 'REVIEW';
      case 'executing_verdict': return 'VERDICT';
      default: return '';
    }
  }
}
