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
 *  6. Connection base strands (3-strand bezier cables)
 *  7. Active connection glow + data diamonds
 *  8. Hub rings + phase core + energy arcs
 *  9. Agent nodes (holographic multi-layer)
 * 10. Ripple effects (expanding rings)
 * 11. Thinking dots (orbiting)
 * 12. Labels + tooltips
 * 13. Bloom post-process
 */

// ── Interfaces ──────────────────────────────────────────────

export type RunPhase = 'idle' | 'coordinating' | 'employee' | 'manager_review' | 'executing_verdict';

export type EventType = 'tool_use' | 'thinking_start' | 'thinking_end' | 'guidance_sent'
  | 'phase_change' | 'run_start' | 'run_complete' | 'conflict' | 'verdict';

export interface AgentNode {
  id: string;
  role: 'manager' | 'employee' | 'coordinator' | 'analyst';
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

export interface WorkspaceData {
  agents: {
    id: string;
    role: 'manager' | 'employee' | 'coordinator' | 'analyst';
    name: string;
    color: string;
    isActive: boolean;
    isThinking: boolean;
    currentAction: string | null;
    turnCount: number;
    tokenCount: number;
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
}

// ── Role colors ──────────────────────────────────────────────

const ROLE_COLORS: Record<string, [number, number, number]> = {
  manager: [245, 158, 11],
  employee: [59, 130, 246],
  coordinator: [168, 85, 247],
  analyst: [139, 92, 246],
};

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

  // Event queue
  private eventQueue: { type: EventType; data: EventData; time: number }[] = [];

  constructor(private canvas: HTMLCanvasElement, dpr?: number) {
    this.ctx = canvas.getContext('2d')!;
    this.dpr = dpr ?? (window.devicePixelRatio || 1);
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
        newNodes.push(existing);
      } else {
        const color = hexToRgb(agent.color);
        newNodes.push({
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
        });
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

  triggerEvent(type: EventType, data: EventData = {}) {
    this.eventQueue.push({ type, data, time: this.time });
    this.processEvent(type, data);
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

  isHubAt(clientX: number, clientY: number): boolean {
    const rect = this.canvas.getBoundingClientRect();
    const dx = clientX - rect.left - this.hubX;
    const dy = clientY - rect.top - this.hubY;
    return dx * dx + dy * dy < this.hubRadius * this.hubRadius * 2;
  }

  // ── Layout ──────────────────────────────────────────────

  private layoutNodes() {
    const count = this.nodes.length;
    if (count === 0) return;

    const phase = this.phase;
    const r = this.orbitRadius;

    for (let i = 0; i < count; i++) {
      const node = this.nodes[i];
      const angle = (i / count) * Math.PI * 2 - Math.PI / 2;

      let targetR = r;
      if (phase === 'idle') {
        targetR = r;
        node.opacity = Math.max(node.opacity, 0.5);
      } else if (phase === 'manager_review') {
        if (node.role === 'manager') {
          targetR = r * 0.15;
        } else if (node.isActive) {
          targetR = r * 0.25;
        } else {
          targetR = r * 0.45;
        }
      } else if (phase === 'employee') {
        if (node.isActive) {
          targetR = r * 0.28;
        } else if (node.role === 'manager') {
          targetR = r;
        } else {
          targetR = r * 0.4;
        }
      } else if (phase === 'coordinating') {
        if (node.role === 'coordinator') {
          targetR = r * 0.15;
        } else if (node.role === 'manager') {
          targetR = r;
        } else {
          targetR = r * 0.4;
        }
      } else if (phase === 'executing_verdict') {
        targetR = r * 0.15;
      }

      node.targetX = this.hubX + Math.cos(angle) * targetR;
      node.targetY = this.hubY + Math.sin(angle) * targetR;
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
        }
        break;
      }
      case 'run_start':
        this.ripples.push({
          x: this.hubX, y: this.hubY,
          radius: 5, maxRadius: 300,
          alpha: 0.5, color: this.getPhaseColor(),
        });
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
        break;
      }
      case 'run_complete':
        // Fade all nodes
        for (const node of this.nodes) {
          node.isActive = false;
          node.isThinking = false;
        }
        break;
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

    // Ripples
    for (let i = this.ripples.length - 1; i >= 0; i--) {
      const r = this.ripples[i];
      r.radius += dt * 0.15;
      r.alpha -= dt * 0.0005;
      if (r.alpha <= 0 || r.radius > r.maxRadius) this.ripples.splice(i, 1);
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
    this.drawDiamonds();
    this.drawHub();
    this.drawNodes();
    this.drawRipples();
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

    const nebulae = [
      { cx: 0.3 + Math.sin(t * 0.7) * 0.08, cy: 0.35 + Math.cos(t * 0.5) * 0.06, r: 0.35, color: [cr * 0.3, cg * 0.3, cb * 0.6] },
      { cx: 0.7 + Math.cos(t * 0.6) * 0.07, cy: 0.65 + Math.sin(t * 0.8) * 0.05, r: 0.3, color: [cr * 0.5, cg * 0.15, cb * 0.5] },
    ];

    for (const n of nebulae) {
      const grad = ctx.createRadialGradient(n.cx * w, n.cy * h, 0, n.cx * w, n.cy * h, n.r * Math.min(w, h));
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

    const maxR = Math.max(w, h) * 0.8;
    const ringSpacing = this.orbitRadius * 0.4;

    ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${alpha})`;
    ctx.lineWidth = 0.5;
    for (let r = ringSpacing; r < maxR; r += ringSpacing) {
      ctx.beginPath();
      ctx.arc(this.hubX, this.hubY, r, 0, Math.PI * 2);
      ctx.stroke();
    }

    // 8 radial lines
    ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${alpha * 0.7})`;
    for (let i = 0; i < 8; i++) {
      const angle = (i / 8) * Math.PI * 2;
      ctx.beginPath();
      ctx.moveTo(this.hubX, this.hubY);
      ctx.lineTo(this.hubX + Math.cos(angle) * maxR, this.hubY + Math.sin(angle) * maxR);
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

    for (const p of this.particles) {
      const a = p.alpha * 0.2;
      if (a < 0.01) continue;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, ${a})`;
      ctx.fill();
    }
  }

  // ── Layer 6: Connections ────────────────────────────────

  private drawConnections() {
    const { ctx } = this;

    for (let i = 0; i < this.nodes.length; i++) {
      const node = this.nodes[i];
      const cpx = (node.x + this.hubX) / 2 + (node.y - this.hubY) * 0.15;
      const cpy = (node.y + this.hubY) / 2 - (node.x - this.hubX) * 0.15;

      if (node.isActive) {
        const [cr, cg, cb] = node.color;
        const pulse = 0.5 + 0.5 * Math.sin(this.hubPulse * 2);

        // Glow beam
        ctx.beginPath();
        ctx.moveTo(this.hubX, this.hubY);
        ctx.quadraticCurveTo(cpx, cpy, node.x, node.y);
        ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${0.04 + this.intensity * 0.06})`;
        ctx.lineWidth = 6;
        ctx.stroke();

        // Core beam
        ctx.beginPath();
        ctx.moveTo(this.hubX, this.hubY);
        ctx.quadraticCurveTo(cpx, cpy, node.x, node.y);
        ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${0.2 + this.intensity * 0.2 + pulse * 0.1})`;
        ctx.lineWidth = 1.5;
        ctx.stroke();

        // Side strands (wave offset)
        for (const offset of [-3, 3]) {
          ctx.beginPath();
          const wave = Math.sin(this.time * 0.3 + offset) * 2;
          const nx = -(node.y - this.hubY);
          const ny = (node.x - this.hubX);
          const len = Math.sqrt(nx * nx + ny * ny) || 1;
          const ox = (nx / len) * (offset + wave);
          const oy = (ny / len) * (offset + wave);
          ctx.moveTo(this.hubX + ox, this.hubY + oy);
          ctx.quadraticCurveTo(cpx + ox, cpy + oy, node.x + ox, node.y + oy);
          ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, 0.06)`;
          ctx.lineWidth = 0.5;
          ctx.stroke();
        }

        // Directional chevron at 60% along curve
        const t = 0.6;
        const mx = (1 - t) * (1 - t) * this.hubX + 2 * (1 - t) * t * cpx + t * t * node.x;
        const my = (1 - t) * (1 - t) * this.hubY + 2 * (1 - t) * t * cpy + t * t * node.y;
        const t2 = t + 0.02;
        const mx2 = (1 - t2) * (1 - t2) * this.hubX + 2 * (1 - t2) * t2 * cpx + t2 * t2 * node.x;
        const my2 = (1 - t2) * (1 - t2) * this.hubY + 2 * (1 - t2) * t2 * cpy + t2 * t2 * node.y;
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
    const [cr, cg, cb] = this.getPhaseColor();

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

    // Concentric rings: 3 rings
    const rings = [
      { r: r * 1.8, dash: [0, 0], speed: 0, alpha: 0.15 },
      { r: r * 1.5, dash: [3, 8], speed: 0.2, alpha: 0.1 },
      { r: r * 1.2, dash: [3, 8], speed: -0.3, alpha: 0.06 },
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
