/**
 * WorkspaceRenderer — Canvas 2D mission control visualization.
 *
 * Visual layers (back to front):
 *   1. Starfield background with parallax twinkle
 *   2. Nebula gradient clouds (animated)
 *   3. Orbital ring path with animated dashes
 *   4. Curved bezier connections with glow
 *   5. Flow particles with comet trails
 *   6. Pulse rings on active nodes
 *   7. Hub energy core (multi-ring, rotating arcs, holographic center)
 *   8. Project nodes (double hexagon, scan line, inner glow)
 *   9. Tool text overlay
 *  10. Bloom post-process pass
 */

// ── Interfaces ──────────────────────────────────────────────

interface ProjectNode {
  id: number;
  repo: string;
  priority: string;
  enabled: boolean;
  x: number;
  y: number;
  angle: number;
  glow: number;
  targetGlow: number;
}

interface Star {
  x: number;         // 0-1 normalized
  y: number;         // 0-1 normalized
  size: number;
  baseAlpha: number;
  twinkleSpeed: number;
  twinkleOffset: number;
  layer: number;     // 0=far, 1=mid, 2=near
}

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  life: number;
  maxLife: number;
  size: number;
  alpha: number;
}

interface FlowParticle {
  progress: number;
  speed: number;
  nodeIndex: number;
  size: number;
  trail: { x: number; y: number }[];
}

interface PulseRing {
  nodeIndex: number;
  radius: number;
  alpha: number;
}

export interface ActivityData {
  intensity: number;
  currentTool: string | null;
  activeAgent: 'employee' | 'manager' | null;
}

export type RunPhase = 'idle' | 'coordinating' | 'employee' | 'manager_review' | 'executing_verdict';

export interface WorkspaceData {
  projects: { id: number; repo: string; priority: string; enabled: boolean }[];
  activeRunProjectIds: Set<number>;
  activeProjectModes: Map<number, string>;
  runPhase: RunPhase;
  serviceActive: boolean;
  usagePercent: number;
}

// ── Renderer ────────────────────────────────────────────────

export class WorkspaceRenderer {
  private ctx: CanvasRenderingContext2D;
  private w = 0;
  private h = 0;
  private dpr: number;
  private running = false;
  private rafId = 0;
  private lastTime = 0;

  // Offscreen canvas for bloom
  private bloomCanvas: HTMLCanvasElement | null = null;
  private bloomCtx: CanvasRenderingContext2D | null = null;

  // Scene objects
  private stars: Star[] = [];
  private nodes: ProjectNode[] = [];
  private ambientParticles: Particle[] = [];
  private flowParticles: FlowParticle[] = [];
  private pulseRings: PulseRing[] = [];
  private pulseTimer = 0;

  // Layout
  private hubX = 0;
  private hubY = 0;
  private hubRadius = 30;
  private orbitRadius = 0;

  // Animation clocks
  private time = 0;          // total elapsed seconds
  private hubPulse = 0;
  private hubArcAngle = 0;
  private orbitDashOffset = 0;
  private nebulaClock = 0;
  private radarAngle = 0;

  // State
  private usagePercent = 0;
  private serviceActive = false;
  private activeRunProjectIds = new Set<number>();
  private activeProjectModes = new Map<number, string>();
  private runPhase: RunPhase = 'idle';
  private activity: ActivityData = { intensity: 0, currentTool: null, activeAgent: null };
  private currentToolText = '';
  private toolTextAlpha = 0;

  constructor(private canvas: HTMLCanvasElement, dpr: number = window.devicePixelRatio || 1) {
    this.ctx = canvas.getContext('2d')!;
    this.dpr = dpr;
  }

  // ── Public API ──────────────────────────────────────────

  resize(w: number, h: number) {
    this.w = w;
    this.h = h;
    this.canvas.width = w * this.dpr;
    this.canvas.height = h * this.dpr;
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);

    this.hubX = w / 2;
    this.hubY = h / 2;
    this.orbitRadius = Math.min(w, h) * 0.32;
    this.hubRadius = Math.min(w, h) * 0.045;

    // Bloom canvas
    this.bloomCanvas = document.createElement('canvas');
    this.bloomCanvas.width = Math.floor(w * this.dpr * 0.25);
    this.bloomCanvas.height = Math.floor(h * this.dpr * 0.25);
    this.bloomCtx = this.bloomCanvas.getContext('2d')!;

    this.layoutNodes();
    if (this.stars.length === 0) this.generateStars();
  }

  setData(data: WorkspaceData) {
    this.serviceActive = data.serviceActive;
    this.usagePercent = data.usagePercent;
    this.activeRunProjectIds = data.activeRunProjectIds;
    this.activeProjectModes = data.activeProjectModes;
    this.runPhase = data.runPhase;

    const existingIds = new Set(this.nodes.map(n => n.id));
    const newIds = new Set(data.projects.map(p => p.id));

    this.nodes = this.nodes.filter(n => newIds.has(n.id));

    for (const p of data.projects) {
      if (!existingIds.has(p.id)) {
        this.nodes.push({
          id: p.id, repo: p.repo, priority: p.priority, enabled: p.enabled,
          x: this.hubX, y: this.hubY, angle: 0, glow: 0, targetGlow: 0,
        });
      } else {
        const node = this.nodes.find(n => n.id === p.id)!;
        node.repo = p.repo;
        node.priority = p.priority;
        node.enabled = p.enabled;
      }
    }

    for (const node of this.nodes) {
      node.targetGlow = this.activeRunProjectIds.has(node.id) ? 1 : 0;
    }

    this.layoutNodes();
    this.ensureAmbientParticles();
  }

  setActivity(data: ActivityData) {
    this.activity = data;
    this.currentToolText = data.currentTool
      ? (data.currentTool.length > 30 ? data.currentTool.slice(0, 30) + '...' : data.currentTool)
      : '';
  }

  start() {
    if (this.running) return;
    this.running = true;
    this.lastTime = performance.now();
    this.ensureAmbientParticles();
    this.tick(this.lastTime);
  }

  stop() {
    this.running = false;
    if (this.rafId) { cancelAnimationFrame(this.rafId); this.rafId = 0; }
  }

  getNodeAt(clientX: number, clientY: number): ProjectNode | null {
    const rect = this.canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    const hitR = this.hubRadius * 0.8;
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
    return dx * dx + dy * dy < this.hubRadius * this.hubRadius * 1.5;
  }

  // ── Internals ───────────────────────────────────────────

  private tick = (now: number) => {
    if (!this.running) return;
    const dt = Math.min(now - this.lastTime, 50);
    this.lastTime = now;
    this.update(dt);
    this.draw();
    this.rafId = requestAnimationFrame(this.tick);
  };

  private generateStars() {
    this.stars = [];
    const count = 120;
    for (let i = 0; i < count; i++) {
      this.stars.push({
        x: Math.random(),
        y: Math.random(),
        size: 0.3 + Math.random() * 1.2,
        baseAlpha: 0.15 + Math.random() * 0.4,
        twinkleSpeed: 0.5 + Math.random() * 2,
        twinkleOffset: Math.random() * Math.PI * 2,
        layer: Math.floor(Math.random() * 3),
      });
    }
  }

  private layoutNodes() {
    const count = this.nodes.length;
    if (count === 0) return;
    for (let i = 0; i < count; i++) {
      const angle = (i / count) * Math.PI * 2 - Math.PI / 2;
      this.nodes[i].angle = angle;
      this.nodes[i].x = this.hubX + Math.cos(angle) * this.orbitRadius;
      this.nodes[i].y = this.hubY + Math.sin(angle) * this.orbitRadius;
    }
  }

  private ensureAmbientParticles() {
    const target = 50;
    while (this.ambientParticles.length < target) {
      this.ambientParticles.push(this.createAmbientParticle(true));
    }
    if (this.ambientParticles.length > 200) this.ambientParticles.length = 200;
  }

  private createAmbientParticle(randomLife: boolean): Particle {
    return {
      x: Math.random() * this.w,
      y: Math.random() * this.h,
      vx: (Math.random() - 0.5) * 0.25,
      vy: (Math.random() - 0.5) * 0.25,
      life: randomLife ? Math.random() * 8000 : 0,
      maxLife: 6000 + Math.random() * 6000,
      size: 0.8 + Math.random() * 1.2,
      alpha: 0,
    };
  }

  // ── Update ──────────────────────────────────────────────

  private update(dt: number) {
    const dtSec = dt / 1000;
    this.time += dtSec;
    this.hubPulse += dt * 0.002;
    this.nebulaClock += dtSec * 0.15;
    this.hubArcAngle += dtSec * 0.3;
    this.orbitDashOffset -= dtSec * 12;
    this.radarAngle += dtSec * 0.6;

    const intensity = this.activity.intensity;

    // Node glow lerp
    for (const node of this.nodes) {
      node.glow += (node.targetGlow - node.glow) * 0.03;
    }

    // Ambient particles
    for (const p of this.ambientParticles) {
      p.life += dt;
      p.x += p.vx * (dt / 16);
      p.y += p.vy * (dt / 16);

      // Gravity toward active nodes
      if (intensity > 0.1) {
        for (const node of this.nodes) {
          if (this.activeRunProjectIds.has(node.id)) {
            const dx = node.x - p.x, dy = node.y - p.y;
            const dist = Math.sqrt(dx * dx + dy * dy);
            if (dist > 20) {
              const pull = intensity * 0.015 * (dt / 16);
              p.vx += (dx / dist) * pull;
              p.vy += (dy / dist) * pull;
            }
          }
        }
        p.vx *= 0.995;
        p.vy *= 0.995;
      }

      const lr = p.life / p.maxLife;
      p.alpha = lr < 0.1 ? lr * 10 : lr > 0.9 ? (1 - lr) * 10 : 1;
      if (p.life > p.maxLife) Object.assign(p, this.createAmbientParticle(false));
    }

    // Flow particles — intensity-driven spawn
    const spawnRate = 0.02 + intensity * 0.13;
    for (let i = 0; i < this.nodes.length; i++) {
      const node = this.nodes[i];
      if (this.activeRunProjectIds.has(node.id) && Math.random() < spawnRate) {
        if (this.flowParticles.length < this.nodes.length * 60) {
          this.flowParticles.push({
            progress: 0,
            speed: 0.0003 + Math.random() * 0.0004,
            nodeIndex: i,
            size: 1.5 + Math.random() * 1.5,
            trail: [],
          });
        }
      }
    }

    // Update flow particles + comet trails
    const inward = this.runPhase === 'employee' || this.runPhase === 'coordinating' || this.runPhase === 'manager_review';
    for (let i = this.flowParticles.length - 1; i >= 0; i--) {
      const fp = this.flowParticles[i];
      fp.progress += fp.speed * dt;

      const node = this.nodes[fp.nodeIndex];
      if (node) {
        let x: number, y: number;
        // Curved path using quadratic offset
        const t = fp.progress;
        const cx = (node.x + this.hubX) / 2 + (node.y - this.hubY) * 0.15;
        const cy = (node.y + this.hubY) / 2 - (node.x - this.hubX) * 0.15;

        if (inward) {
          x = (1 - t) * (1 - t) * node.x + 2 * (1 - t) * t * cx + t * t * this.hubX;
          y = (1 - t) * (1 - t) * node.y + 2 * (1 - t) * t * cy + t * t * this.hubY;
        } else {
          x = (1 - t) * (1 - t) * this.hubX + 2 * (1 - t) * t * cx + t * t * node.x;
          y = (1 - t) * (1 - t) * this.hubY + 2 * (1 - t) * t * cy + t * t * node.y;
        }

        fp.trail.push({ x, y });
        if (fp.trail.length > 8) fp.trail.shift();
      }

      if (fp.progress > 1) this.flowParticles.splice(i, 1);
    }

    // Pulse rings
    this.pulseTimer += dt;
    if (intensity > 0.2 && this.pulseTimer > 1800) {
      this.pulseTimer = 0;
      for (let i = 0; i < this.nodes.length; i++) {
        if (this.activeRunProjectIds.has(this.nodes[i].id)) {
          this.pulseRings.push({ nodeIndex: i, radius: this.hubRadius * 0.5, alpha: 0.6 });
        }
      }
    }
    for (let i = this.pulseRings.length - 1; i >= 0; i--) {
      const ring = this.pulseRings[i];
      ring.radius += dt * 0.05;
      ring.alpha -= dt * 0.00025;
      if (ring.alpha <= 0) this.pulseRings.splice(i, 1);
    }

    // Tool text alpha
    const targetAlpha = this.currentToolText ? 1 : 0;
    this.toolTextAlpha += (targetAlpha - this.toolTextAlpha) * 0.06;
  }

  // ── Draw ────────────────────────────────────────────────

  private draw() {
    const { ctx, w, h } = this;
    ctx.clearRect(0, 0, w, h);

    this.drawStarfield();
    this.drawNebula();
    this.drawGrid();
    this.drawRadarSweep();
    this.drawOrbitRing();
    this.drawAmbientParticles();
    this.drawConnections();
    this.drawPulseRings();
    this.drawFlowParticles();
    this.drawHub();
    this.drawHubEnergyArcs();
    for (const node of this.nodes) this.drawProjectNode(node);
    this.drawToolTextOverlay();
    this.drawBloom();
  }

  // ── Layer 1: Starfield ────────────────────────────────

  private drawStarfield() {
    const { ctx, w, h, time } = this;
    for (const s of this.stars) {
      const twinkle = Math.sin(time * s.twinkleSpeed + s.twinkleOffset) * 0.5 + 0.5;
      const alpha = s.baseAlpha * (0.4 + twinkle * 0.6);
      const layerColors = ['100, 120, 180', '140, 160, 220', '180, 200, 255'];
      ctx.beginPath();
      ctx.arc(s.x * w, s.y * h, s.size, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${layerColors[s.layer]}, ${alpha})`;
      ctx.fill();

      // Tiny glow on brightest stars
      if (s.size > 1 && alpha > 0.35) {
        ctx.beginPath();
        ctx.arc(s.x * w, s.y * h, s.size * 3, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${layerColors[s.layer]}, ${alpha * 0.08})`;
        ctx.fill();
      }
    }
  }

  // ── Layer 2: Nebula ───────────────────────────────────

  private drawNebula() {
    const { ctx, w, h } = this;
    const t = this.nebulaClock;

    // Two drifting nebula clouds
    const nebulae = [
      { cx: 0.3 + Math.sin(t * 0.7) * 0.08, cy: 0.35 + Math.cos(t * 0.5) * 0.06, r: 0.35, color: [30, 60, 140] },
      { cx: 0.7 + Math.cos(t * 0.6) * 0.07, cy: 0.65 + Math.sin(t * 0.8) * 0.05, r: 0.3, color: [80, 20, 120] },
    ];

    for (const n of nebulae) {
      const grad = ctx.createRadialGradient(
        n.cx * w, n.cy * h, 0,
        n.cx * w, n.cy * h, n.r * Math.min(w, h),
      );
      grad.addColorStop(0, `rgba(${n.color.join(',')}, 0.06)`);
      grad.addColorStop(0.5, `rgba(${n.color.join(',')}, 0.025)`);
      grad.addColorStop(1, `rgba(${n.color.join(',')}, 0)`);
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, w, h);
    }
  }

  // ── Layer 2b: Subtle Grid ──────────────────────────────

  private drawGrid() {
    const { ctx, w, h } = this;
    const [cr, cg, cb] = this.getPhaseColor();
    const isActive = this.runPhase !== 'idle';
    const alpha = isActive ? 0.04 : 0.02;

    // Concentric circles from hub
    const maxR = Math.max(w, h) * 0.8;
    ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${alpha})`;
    ctx.lineWidth = 0.5;
    const ringSpacing = this.orbitRadius * 0.4;
    for (let r = ringSpacing; r < maxR; r += ringSpacing) {
      ctx.beginPath();
      ctx.arc(this.hubX, this.hubY, r, 0, Math.PI * 2);
      ctx.stroke();
    }

    // Radial lines (6 directions like hexagonal grid)
    ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${alpha * 0.7})`;
    for (let i = 0; i < 6; i++) {
      const angle = (i / 6) * Math.PI * 2;
      ctx.beginPath();
      ctx.moveTo(this.hubX, this.hubY);
      ctx.lineTo(
        this.hubX + Math.cos(angle) * maxR,
        this.hubY + Math.sin(angle) * maxR,
      );
      ctx.stroke();
    }
  }

  // ── Layer 2c: Radar Sweep ─────────────────────────────

  private drawRadarSweep() {
    if (this.runPhase === 'idle') return;

    const { ctx } = this;
    const [cr, cg, cb] = this.getPhaseColor();
    const sweepR = this.orbitRadius * 1.15;

    // Gradient wedge that rotates
    const grad = ctx.createConicGradient(
      this.radarAngle,
      this.hubX,
      this.hubY,
    );
    grad.addColorStop(0, `rgba(${cr}, ${cg}, ${cb}, 0.07)`);
    grad.addColorStop(0.08, `rgba(${cr}, ${cg}, ${cb}, 0.02)`);
    grad.addColorStop(0.15, `rgba(${cr}, ${cg}, ${cb}, 0)`);
    grad.addColorStop(1, `rgba(${cr}, ${cg}, ${cb}, 0)`);

    ctx.beginPath();
    ctx.arc(this.hubX, this.hubY, sweepR, 0, Math.PI * 2);
    ctx.fillStyle = grad;
    ctx.fill();

    // Sweep leading edge line
    const edgeX = this.hubX + Math.cos(this.radarAngle) * sweepR;
    const edgeY = this.hubY + Math.sin(this.radarAngle) * sweepR;
    ctx.beginPath();
    ctx.moveTo(this.hubX, this.hubY);
    ctx.lineTo(edgeX, edgeY);
    ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, 0.1)`;
    ctx.lineWidth = 0.8;
    ctx.stroke();
  }

  // ── Layer 3: Orbital Ring ─────────────────────────────

  private drawOrbitRing() {
    const { ctx } = this;
    const [cr, cg, cb] = this.getPhaseColor();
    const isActive = this.runPhase !== 'idle';
    const alpha = isActive ? 0.18 : 0.08;

    // Outer orbit ellipse
    ctx.beginPath();
    ctx.arc(this.hubX, this.hubY, this.orbitRadius, 0, Math.PI * 2);
    ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${alpha})`;
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 8]);
    ctx.lineDashOffset = this.orbitDashOffset;
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.lineDashOffset = 0;

    // Faint inner orbit
    ctx.beginPath();
    ctx.arc(this.hubX, this.hubY, this.orbitRadius * 0.6, 0, Math.PI * 2);
    ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${alpha * 0.4})`;
    ctx.lineWidth = 0.5;
    ctx.setLineDash([2, 12]);
    ctx.lineDashOffset = -this.orbitDashOffset * 0.5;
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.lineDashOffset = 0;
  }

  // ── Layer 4: Ambient Particles ────────────────────────

  private drawAmbientParticles() {
    const { ctx } = this;
    const [cr, cg, cb] = this.getPhaseColor();
    for (const p of this.ambientParticles) {
      const a = p.alpha * 0.18;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, ${a})`;
      ctx.fill();
    }
  }

  // ── Layer 5: Connections ──────────────────────────────

  private drawConnections() {
    const { ctx } = this;
    const intensity = this.activity.intensity;
    const inward = this.runPhase === 'employee' || this.runPhase === 'coordinating' || this.runPhase === 'manager_review';

    for (const node of this.nodes) {
      const isActive = this.activeRunProjectIds.has(node.id);

      // Curved connection: quadratic bezier with slight offset
      const cpx = (node.x + this.hubX) / 2 + (node.y - this.hubY) * 0.15;
      const cpy = (node.y + this.hubY) / 2 - (node.x - this.hubX) * 0.15;

      if (isActive) {
        const beamPulse = 0.5 + 0.5 * Math.sin(this.hubPulse * 2);
        const beamWidth = 1 + intensity * 2.5 * beamPulse;
        const [cr, cg, cb] = this.getPhaseColor();

        // Outer glow
        ctx.beginPath();
        ctx.moveTo(this.hubX, this.hubY);
        ctx.quadraticCurveTo(cpx, cpy, node.x, node.y);
        ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${0.06 + intensity * 0.08})`;
        ctx.lineWidth = beamWidth + 4;
        ctx.stroke();

        // Core beam
        ctx.beginPath();
        ctx.moveTo(this.hubX, this.hubY);
        ctx.quadraticCurveTo(cpx, cpy, node.x, node.y);
        ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${0.25 + intensity * 0.35})`;
        ctx.lineWidth = beamWidth;
        ctx.stroke();

        // Directional arrow at midpoint
        const t = inward ? 0.35 : 0.65;
        const mx = (1 - t) * (1 - t) * (inward ? node.x : this.hubX) + 2 * (1 - t) * t * cpx + t * t * (inward ? this.hubX : node.x);
        const my = (1 - t) * (1 - t) * (inward ? node.y : this.hubY) + 2 * (1 - t) * t * cpy + t * t * (inward ? this.hubY : node.y);
        // Tangent at t for arrow direction
        const t2 = t + 0.01;
        const mx2 = (1 - t2) * (1 - t2) * (inward ? node.x : this.hubX) + 2 * (1 - t2) * t2 * cpx + t2 * t2 * (inward ? this.hubX : node.x);
        const my2 = (1 - t2) * (1 - t2) * (inward ? node.y : this.hubY) + 2 * (1 - t2) * t2 * cpy + t2 * t2 * (inward ? this.hubY : node.y);
        const ang = Math.atan2(my2 - my, mx2 - mx);
        const arrowSize = 5 + intensity * 3;

        ctx.beginPath();
        ctx.moveTo(mx + Math.cos(ang) * arrowSize, my + Math.sin(ang) * arrowSize);
        ctx.lineTo(mx + Math.cos(ang + 2.5) * arrowSize * 0.7, my + Math.sin(ang + 2.5) * arrowSize * 0.7);
        ctx.lineTo(mx + Math.cos(ang - 2.5) * arrowSize * 0.7, my + Math.sin(ang - 2.5) * arrowSize * 0.7);
        ctx.closePath();
        ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, ${0.4 + intensity * 0.3})`;
        ctx.fill();

        // Flow direction label near midpoint
        const mode = this.activeProjectModes.get(node.id);
        const flowLabel = inward
          ? (mode === 'analyst' ? 'ANALYZING' : 'REPORTING')
          : 'DIRECTING';
        const labelOffset = 14;
        const perpX = -Math.sin(ang) * labelOffset;
        const perpY = Math.cos(ang) * labelOffset;
        const fontSize = Math.max(7, this.hubRadius * 0.22);
        ctx.font = `600 ${fontSize}px system-ui, -apple-system, sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, ${0.3 + intensity * 0.2})`;
        ctx.fillText(flowLabel, mx + perpX, my + perpY);
      } else {
        // Inactive dashed curve
        ctx.beginPath();
        ctx.moveTo(this.hubX, this.hubY);
        ctx.quadraticCurveTo(cpx, cpy, node.x, node.y);
        ctx.strokeStyle = 'rgba(71, 85, 105, 0.15)';
        ctx.lineWidth = 0.8;
        ctx.setLineDash([3, 6]);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }
  }

  // ── Layer 6: Pulse Rings ──────────────────────────────

  private drawPulseRings() {
    const { ctx } = this;
    const [cr, cg, cb] = this.getPhaseColor();
    for (const ring of this.pulseRings) {
      const node = this.nodes[ring.nodeIndex];
      if (!node) continue;
      ctx.beginPath();
      ctx.arc(node.x, node.y, ring.radius, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${ring.alpha * 0.6})`;
      ctx.lineWidth = 1.5 * ring.alpha;
      ctx.stroke();
    }
  }

  // ── Layer 7: Flow Particles (Comet Trails) ────────────

  private drawFlowParticles() {
    const { ctx } = this;
    const colorStr = this.getPhaseColorStr();

    ctx.globalCompositeOperation = 'lighter';
    for (const fp of this.flowParticles) {
      if (fp.trail.length < 2) continue;
      const head = fp.trail[fp.trail.length - 1];

      // Trail line
      ctx.beginPath();
      ctx.moveTo(fp.trail[0].x, fp.trail[0].y);
      for (let i = 1; i < fp.trail.length; i++) {
        ctx.lineTo(fp.trail[i].x, fp.trail[i].y);
      }
      const trailAlpha = 0.4 * (1 - fp.progress * 0.5);
      ctx.strokeStyle = `rgba(${colorStr}, ${trailAlpha * 0.5})`;
      ctx.lineWidth = fp.size * 0.8;
      ctx.stroke();

      // Head glow
      const headAlpha = 0.9 * (1 - fp.progress * 0.5);
      ctx.beginPath();
      ctx.arc(head.x, head.y, fp.size * 1.2, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${colorStr}, ${headAlpha})`;
      ctx.fill();

      // Bloom around head
      ctx.beginPath();
      ctx.arc(head.x, head.y, fp.size * 4, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${colorStr}, ${headAlpha * 0.12})`;
      ctx.fill();
    }
    ctx.globalCompositeOperation = 'source-over';
  }

  // ── Layer 8: Hub ──────────────────────────────────────

  private drawHub() {
    const { ctx } = this;
    const pulse = Math.sin(this.hubPulse) * 0.3 + 0.7;
    const r = this.hubRadius;
    const [cr, cg, cb] = this.getPhaseColor();

    // Large ambient glow
    if (this.serviceActive) {
      const grad = ctx.createRadialGradient(this.hubX, this.hubY, r * 0.3, this.hubX, this.hubY, r * 3.5);
      grad.addColorStop(0, `rgba(${cr}, ${cg}, ${cb}, ${0.12 * pulse})`);
      grad.addColorStop(0.6, `rgba(${cr}, ${cg}, ${cb}, ${0.04 * pulse})`);
      grad.addColorStop(1, `rgba(${cr}, ${cg}, ${cb}, 0)`);
      ctx.beginPath();
      ctx.arc(this.hubX, this.hubY, r * 3.5, 0, Math.PI * 2);
      ctx.fillStyle = grad;
      ctx.fill();
    }

    // Outer defense ring (rotating segmented)
    if (this.serviceActive) {
      const ringR = r * 1.45;
      ctx.lineWidth = 1.5;
      for (let i = 0; i < 6; i++) {
        const segStart = this.hubArcAngle * 0.8 + (i * Math.PI * 2) / 6;
        const segSweep = Math.PI / 4.5;
        ctx.beginPath();
        ctx.arc(this.hubX, this.hubY, ringR, segStart, segStart + segSweep);
        ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${0.2 + pulse * 0.15})`;
        ctx.stroke();
      }
    }

    // Inner rotating ring (opposite direction)
    if (this.serviceActive) {
      const innerRingR = r * 1.2;
      ctx.lineWidth = 1;
      for (let i = 0; i < 4; i++) {
        const segStart = -this.hubArcAngle * 1.2 + (i * Math.PI * 2) / 4;
        const segSweep = Math.PI / 5;
        ctx.beginPath();
        ctx.arc(this.hubX, this.hubY, innerRingR, segStart, segStart + segSweep);
        ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${0.15 + pulse * 0.1})`;
        ctx.stroke();
      }
    }

    // Outer hexagon (larger, fainter)
    this.drawHexagon(this.hubX, this.hubY, r * 1.08,
      `rgba(${cr}, ${cg}, ${cb}, ${this.serviceActive ? 0.05 : 0.02})`,
      `rgba(${cr}, ${cg}, ${cb}, ${this.serviceActive ? 0.15 + pulse * 0.1 : 0.08})`,
      0.8,
    );

    // Main hexagon
    this.drawHexagon(this.hubX, this.hubY, r,
      this.serviceActive
        ? `rgba(${cr}, ${cg}, ${cb}, ${0.08 + pulse * 0.06})`
        : 'rgba(30, 41, 59, 0.4)',
      this.serviceActive
        ? `rgba(${cr}, ${cg}, ${cb}, ${0.35 + pulse * 0.25})`
        : 'rgba(71, 85, 105, 0.25)',
      1.5,
    );

    // Inner hexagon (rotated 30deg for layered look)
    const innerR = r * 0.55;
    ctx.save();
    ctx.translate(this.hubX, this.hubY);
    ctx.rotate(Math.PI / 6);
    this.drawHexagon(0, 0, innerR,
      `rgba(${cr}, ${cg}, ${cb}, ${this.serviceActive ? 0.06 + pulse * 0.04 : 0.02})`,
      `rgba(${cr}, ${cg}, ${cb}, ${this.serviceActive ? 0.2 + pulse * 0.1 : 0.1})`,
      0.8,
    );
    ctx.restore();

    // Center energy dot with cross-hair
    if (this.serviceActive) {
      const dotR = 3 + pulse * 1.5;
      ctx.beginPath();
      ctx.arc(this.hubX, this.hubY, dotR, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, ${0.7 + pulse * 0.3})`;
      ctx.fill();

      // Tiny crosshair lines
      const chLen = r * 0.3;
      ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${0.15 + pulse * 0.1})`;
      ctx.lineWidth = 0.5;
      ctx.beginPath();
      ctx.moveTo(this.hubX - chLen, this.hubY); ctx.lineTo(this.hubX - dotR - 2, this.hubY);
      ctx.moveTo(this.hubX + dotR + 2, this.hubY); ctx.lineTo(this.hubX + chLen, this.hubY);
      ctx.moveTo(this.hubX, this.hubY - chLen); ctx.lineTo(this.hubX, this.hubY - dotR - 2);
      ctx.moveTo(this.hubX, this.hubY + dotR + 2); ctx.lineTo(this.hubX, this.hubY + chLen);
      ctx.stroke();
    } else {
      ctx.beginPath();
      ctx.arc(this.hubX, this.hubY, 2.5, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(71, 85, 105, 0.4)';
      ctx.fill();
    }

    // Usage ring
    if (this.usagePercent > 0) {
      const usageR = r + 8;
      const startAngle = -Math.PI / 2;
      const endAngle = startAngle + (this.usagePercent / 100) * Math.PI * 2;

      // Track
      ctx.beginPath();
      ctx.arc(this.hubX, this.hubY, usageR, 0, Math.PI * 2);
      ctx.strokeStyle = 'rgba(71, 85, 105, 0.1)';
      ctx.lineWidth = 2.5;
      ctx.stroke();

      // Fill
      ctx.beginPath();
      ctx.arc(this.hubX, this.hubY, usageR, startAngle, endAngle);
      ctx.strokeStyle = this.usagePercent > 80
        ? 'rgba(239, 68, 68, 0.6)'
        : 'rgba(16, 185, 129, 0.5)';
      ctx.lineWidth = 2.5;
      ctx.lineCap = 'round';
      ctx.stroke();
      ctx.lineCap = 'butt';
    }

    // "Manager" label
    const fontSize = Math.max(10, r * 0.38);
    ctx.font = `600 ${fontSize}px system-ui, -apple-system, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.fillStyle = this.serviceActive
      ? `rgba(${cr}, ${cg}, ${cb}, 0.75)`
      : 'rgba(148, 163, 184, 0.45)';
    ctx.fillText('Manager', this.hubX, this.hubY + r + 14);

    // Phase label
    const phaseLabel = this.getPhaseLabel();
    if (phaseLabel && this.runPhase !== 'idle') {
      ctx.font = `${Math.max(8, r * 0.28)}px system-ui, -apple-system, sans-serif`;
      ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, 0.5)`;
      ctx.fillText(phaseLabel, this.hubX, this.hubY + r + 14 + fontSize + 2);
    }
  }

  // ── Hub Energy Arcs ───────────────────────────────────

  private drawHubEnergyArcs() {
    const intensity = this.activity.intensity;
    if (intensity <= 0.3) return;

    const { ctx } = this;
    const [cr, cg, cb] = this.getPhaseColor();
    const arcR = this.hubRadius * 1.7;
    const alpha = (intensity - 0.3) * 0.7;

    ctx.lineWidth = 2.5;
    ctx.lineCap = 'round';
    for (let i = 0; i < 3; i++) {
      const startAngle = this.hubArcAngle * 1.5 + (i * Math.PI * 2) / 3;
      const sweep = Math.PI * 0.35;
      ctx.beginPath();
      ctx.arc(this.hubX, this.hubY, arcR, startAngle, startAngle + sweep);
      ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${alpha})`;
      ctx.stroke();
    }
    ctx.lineCap = 'butt';
  }

  // ── Project Nodes ─────────────────────────────────────

  private drawProjectNode(node: ProjectNode) {
    const { ctx } = this;
    const r = this.hubRadius * 0.6;
    const isActive = this.activeRunProjectIds.has(node.id);
    const mode = this.activeProjectModes.get(node.id);

    // Determine node color
    const activeColor = mode === 'analyst' ? [168, 85, 247] : [59, 130, 246];
    const priorityColor = node.priority === 'high'
      ? [239, 68, 68]
      : node.priority === 'medium'
        ? [245, 158, 11]
        : [148, 163, 184];
    const nodeColor = isActive ? activeColor : priorityColor;
    const [nr, ng, nb] = nodeColor;

    // Active glow
    if (isActive) {
      const grad = ctx.createRadialGradient(node.x, node.y, r * 0.2, node.x, node.y, r * 3);
      grad.addColorStop(0, `rgba(${nr}, ${ng}, ${nb}, ${0.15 * (0.7 + node.glow * 0.3)})`);
      grad.addColorStop(0.5, `rgba(${nr}, ${ng}, ${nb}, ${0.05 * node.glow})`);
      grad.addColorStop(1, `rgba(${nr}, ${ng}, ${nb}, 0)`);
      ctx.beginPath();
      ctx.arc(node.x, node.y, r * 3, 0, Math.PI * 2);
      ctx.fillStyle = grad;
      ctx.fill();
    }

    // Outer hexagon (slightly larger, fainter — layered depth)
    if (node.enabled) {
      this.drawHexagon(node.x, node.y, r * 1.12,
        'transparent',
        `rgba(${nr}, ${ng}, ${nb}, ${isActive ? 0.2 : 0.08})`,
        0.6,
      );
    }

    // Main hexagon
    const alpha = node.enabled ? (isActive ? 0.2 : 0.1) : 0.04;
    const borderAlpha = node.enabled ? (isActive ? 0.6 : 0.25) : 0.1;
    this.drawHexagon(node.x, node.y, r,
      `rgba(${nr}, ${ng}, ${nb}, ${alpha})`,
      `rgba(${nr}, ${ng}, ${nb}, ${borderAlpha})`,
      isActive ? 1.5 : 0.8,
    );

    // Holographic scan line (sweeps vertically through active nodes)
    if (isActive && node.enabled) {
      const scanY = node.y - r + ((this.time * 0.4 + node.angle) % 1) * r * 2;
      const scanGrad = ctx.createLinearGradient(node.x - r, scanY, node.x + r, scanY);
      scanGrad.addColorStop(0, `rgba(${nr}, ${ng}, ${nb}, 0)`);
      scanGrad.addColorStop(0.5, `rgba(${nr}, ${ng}, ${nb}, 0.12)`);
      scanGrad.addColorStop(1, `rgba(${nr}, ${ng}, ${nb}, 0)`);
      ctx.fillStyle = scanGrad;
      ctx.fillRect(node.x - r, scanY - 1, r * 2, 2);
    }

    // Corner accent dots on active nodes
    if (isActive) {
      for (let i = 0; i < 6; i++) {
        const angle = (i / 6) * Math.PI * 2 - Math.PI / 6;
        const cx = node.x + Math.cos(angle) * r;
        const cy = node.y + Math.sin(angle) * r;
        ctx.beginPath();
        ctx.arc(cx, cy, 1.5, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${nr}, ${ng}, ${nb}, 0.5)`;
        ctx.fill();
      }
    }

    // Role badge
    if (isActive && mode) {
      const roleLabel = mode === 'analyst' ? 'Analyst' : 'Employee';
      const fontSize = Math.max(7, r * 0.5);

      ctx.font = `600 ${fontSize}px system-ui, -apple-system, sans-serif`;
      const tw = ctx.measureText(roleLabel).width;
      const badgeW = tw + 10;
      const badgeH = fontSize + 6;
      const badgeX = node.x - badgeW / 2;
      const badgeY = node.y - r - badgeH - 6;

      // Badge bg
      ctx.beginPath();
      ctx.roundRect(badgeX, badgeY, badgeW, badgeH, 4);
      ctx.fillStyle = `rgba(${nr}, ${ng}, ${nb}, 0.15)`;
      ctx.fill();
      ctx.strokeStyle = `rgba(${nr}, ${ng}, ${nb}, 0.4)`;
      ctx.lineWidth = 0.7;
      ctx.stroke();

      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = `rgba(${nr}, ${ng}, ${nb}, 0.85)`;
      ctx.fillText(roleLabel, node.x, badgeY + badgeH / 2);
    }
  }

  // ── Tool Text Overlay ─────────────────────────────────

  private drawToolTextOverlay() {
    if (this.toolTextAlpha < 0.01 || !this.currentToolText) return;
    const { ctx } = this;

    for (const node of this.nodes) {
      if (!this.activeRunProjectIds.has(node.id)) continue;

      const r = this.hubRadius * 0.6;
      const fontSize = Math.max(8, r * 0.45);
      ctx.font = `${fontSize}px 'SF Mono', 'Cascadia Code', ui-monospace, monospace`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';

      // Text bg pill
      const tw = ctx.measureText(this.currentToolText).width;
      const px = 6, py = 2;
      const bgX = node.x - tw / 2 - px;
      const bgY = node.y + r + 8;
      ctx.beginPath();
      ctx.roundRect(bgX, bgY, tw + px * 2, fontSize + py * 2, 3);
      ctx.fillStyle = `rgba(5, 8, 22, ${0.7 * this.toolTextAlpha})`;
      ctx.fill();

      ctx.fillStyle = `rgba(6, 182, 212, ${0.8 * this.toolTextAlpha})`;
      ctx.fillText(this.currentToolText, node.x, bgY + py);
      break;
    }
  }

  // ── Layer 10: Bloom Post-Process ──────────────────────

  private drawBloom() {
    if (!this.bloomCanvas || !this.bloomCtx) return;
    const { ctx, w, h } = this;
    const bc = this.bloomCtx;
    const bw = this.bloomCanvas.width;
    const bh = this.bloomCanvas.height;

    // Downscale main canvas into bloom canvas
    bc.clearRect(0, 0, bw, bh);
    bc.drawImage(this.canvas, 0, 0, bw, bh);

    // Draw bloom back at full size with additive blend
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.globalAlpha = 0.12;
    ctx.setTransform(1, 0, 0, 1, 0, 0); // reset to pixel space
    ctx.drawImage(this.bloomCanvas, 0, 0, this.canvas.width, this.canvas.height);
    ctx.restore();
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0); // restore DPR transform
  }

  // ── Helpers ───────────────────────────────────────────

  private drawHexagon(cx: number, cy: number, r: number, fill: string, stroke: string, lineWidth: number) {
    const { ctx } = this;
    ctx.beginPath();
    for (let i = 0; i < 6; i++) {
      const angle = (i / 6) * Math.PI * 2 - Math.PI / 6;
      const x = cx + Math.cos(angle) * r;
      const y = cy + Math.sin(angle) * r;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.closePath();
    if (fill !== 'transparent') { ctx.fillStyle = fill; ctx.fill(); }
    ctx.strokeStyle = stroke;
    ctx.lineWidth = lineWidth;
    ctx.stroke();
  }

  private getPhaseColor(): [number, number, number] {
    switch (this.runPhase) {
      case 'coordinating': return [168, 85, 247];
      case 'employee': return [59, 130, 246];
      case 'manager_review': return [245, 158, 11];
      case 'executing_verdict': return [16, 185, 129];
      default: return [59, 130, 246];
    }
  }

  private getPhaseColorStr(): string {
    const [r, g, b] = this.getPhaseColor();
    return `${r}, ${g}, ${b}`;
  }

  private getPhaseLabel(): string {
    switch (this.runPhase) {
      case 'coordinating': return 'Coordinating';
      case 'employee': return 'Employees Working';
      case 'manager_review': return 'Reviewing';
      case 'executing_verdict': return 'Executing';
      default: return '';
    }
  }
}
