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

/** Represents an active employee/agent working on a project. */
export interface ActiveEmployee {
  runId: string;
  projectId: number;
  mode: string;        // 'employee' | 'analyst' | 'manager' | 'planner'
  status: string;      // 'running' | 'success' | 'failed'
  issueNumber: number | null;
  turns: number | null;
}

/** A rendered satellite hexagon orbiting a project node. */
interface EmployeeSatellite {
  employee: ActiveEmployee;
  x: number;
  y: number;
  angle: number;
  pulsePhase: number;
  spawnProgress: number; // 0..1 for spawn animation
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
}

export type RunPhase = 'idle' | 'employee' | 'manager_review' | 'executing_verdict';

export interface WorkspaceData {
  projects: { id: number; repo: string; priority: string; enabled: boolean }[];
  activeRunProjectIds: Set<number>;
  activeProjectModes: Map<number, string>; // project_id -> 'employee' | 'analyst' | 'manager'
  activeEmployees: ActiveEmployee[];
  runPhase: RunPhase;
  serviceActive: boolean;
  usagePercent: number;
}

/** Style config per agent type */
const AGENT_STYLES: Record<string, {
  color: [number, number, number];
  label: string;
  dashPattern: number[];
  fillAlpha: number;
}> = {
  employee: {
    color: [59, 130, 246],    // blue
    label: 'EMP',
    dashPattern: [],           // solid fill
    fillAlpha: 0.2,
  },
  analyst: {
    color: [168, 85, 247],    // purple
    label: 'ANL',
    dashPattern: [4, 4],       // dashed
    fillAlpha: 0.15,
  },
  manager: {
    color: [245, 158, 11],    // amber
    label: 'MGR',
    dashPattern: [],
    fillAlpha: 0.12,
  },
  planner: {
    color: [16, 185, 129],    // green
    label: 'PLN',
    dashPattern: [2, 3],       // dotted
    fillAlpha: 0.12,
  },
};

const STATUS_COLORS: Record<string, [number, number, number]> = {
  running: [59, 130, 246],   // blue
  success: [16, 185, 129],   // green
  failed:  [239, 68, 68],    // red
};

export class WorkspaceRenderer {
  private ctx: CanvasRenderingContext2D;
  private w = 0;
  private h = 0;
  private dpr: number;
  private running = false;
  private rafId = 0;
  private lastTime = 0;

  private nodes: ProjectNode[] = [];
  private satellites: Map<number, EmployeeSatellite[]> = new Map(); // projectId -> satellites
  private ambientParticles: Particle[] = [];
  private flowParticles: FlowParticle[] = [];

  private hubX = 0;
  private hubY = 0;
  private hubRadius = 30;
  private orbitRadius = 0;
  private hubPulse = 0;
  private usagePercent = 0;
  private serviceActive = false;
  private activeRunProjectIds = new Set<number>();
  private activeProjectModes = new Map<number, string>();
  private activeEmployees: ActiveEmployee[] = [];
  private runPhase: RunPhase = 'idle';

  constructor(private canvas: HTMLCanvasElement, dpr: number = window.devicePixelRatio || 1) {
    this.ctx = canvas.getContext('2d')!;
    this.dpr = dpr;
  }

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

    this.layoutNodes();
    this.layoutSatellites();
  }

  setData(data: WorkspaceData) {
    this.serviceActive = data.serviceActive;
    this.usagePercent = data.usagePercent;
    this.activeRunProjectIds = data.activeRunProjectIds;
    this.activeProjectModes = data.activeProjectModes;
    this.activeEmployees = data.activeEmployees;
    this.runPhase = data.runPhase;

    const existingIds = new Set(this.nodes.map(n => n.id));
    const newIds = new Set(data.projects.map(p => p.id));

    // Remove nodes for deleted projects
    this.nodes = this.nodes.filter(n => newIds.has(n.id));

    // Add new projects
    for (const p of data.projects) {
      if (!existingIds.has(p.id)) {
        this.nodes.push({
          id: p.id,
          repo: p.repo,
          priority: p.priority,
          enabled: p.enabled,
          x: this.hubX,
          y: this.hubY,
          angle: 0,
          glow: 0,
          targetGlow: 0,
        });
      } else {
        const node = this.nodes.find(n => n.id === p.id)!;
        node.repo = p.repo;
        node.priority = p.priority;
        node.enabled = p.enabled;
      }
    }

    // Update target glow based on active runs
    for (const node of this.nodes) {
      node.targetGlow = this.activeRunProjectIds.has(node.id) ? 1 : 0;
    }

    this.layoutNodes();
    this.updateSatellites();
    this.ensureAmbientParticles();
  }

  /** Create/update satellite hexagons for active employees */
  private updateSatellites() {
    // Group employees by project
    const byProject = new Map<number, ActiveEmployee[]>();
    for (const emp of this.activeEmployees) {
      const list = byProject.get(emp.projectId) ?? [];
      list.push(emp);
      byProject.set(emp.projectId, list);
    }

    // Update satellites for each project
    for (const [projectId, employees] of byProject) {
      const existing = this.satellites.get(projectId) ?? [];
      const updated: EmployeeSatellite[] = [];

      for (const emp of employees) {
        // Find existing satellite for this run
        const existingSat = existing.find(s => s.employee.runId === emp.runId);
        if (existingSat) {
          existingSat.employee = emp;
          updated.push(existingSat);
        } else {
          // New satellite - will animate in
          updated.push({
            employee: emp,
            x: 0,
            y: 0,
            angle: 0,
            pulsePhase: Math.random() * Math.PI * 2,
            spawnProgress: 0,
          });
        }
      }

      this.satellites.set(projectId, updated);
    }

    // Remove satellites for projects with no active employees
    for (const [projectId] of this.satellites) {
      if (!byProject.has(projectId)) {
        this.satellites.delete(projectId);
      }
    }

    this.layoutSatellites();
  }

  /** Position satellites around their parent project nodes */
  private layoutSatellites() {
    const satelliteRadius = this.hubRadius * 0.4; // size of satellite hex
    const orbitDist = this.hubRadius * 1.4; // distance from project center

    for (const [projectId, sats] of this.satellites) {
      const parent = this.nodes.find(n => n.id === projectId);
      if (!parent) continue;

      const count = sats.length;
      for (let i = 0; i < count; i++) {
        // Arrange satellites radially around the project node
        // Start from the side facing away from hub for better visibility
        const awayAngle = Math.atan2(parent.y - this.hubY, parent.x - this.hubX);
        let satAngle: number;

        if (count === 1) {
          satAngle = awayAngle;
        } else {
          // Spread satellites in an arc facing away from the hub
          const spread = Math.min(Math.PI * 0.8, (count - 1) * Math.PI / 4);
          satAngle = awayAngle - spread / 2 + (i / (count - 1)) * spread;
        }

        sats[i].angle = satAngle;
        sats[i].x = parent.x + Math.cos(satAngle) * orbitDist;
        sats[i].y = parent.y + Math.sin(satAngle) * orbitDist;
      }
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
    const target = 40;
    while (this.ambientParticles.length < target) {
      this.ambientParticles.push(this.createAmbientParticle(true));
    }
    if (this.ambientParticles.length > 200) {
      this.ambientParticles.length = 200;
    }
  }

  private createAmbientParticle(randomLife: boolean): Particle {
    return {
      x: Math.random() * this.w,
      y: Math.random() * this.h,
      vx: (Math.random() - 0.5) * 0.3,
      vy: (Math.random() - 0.5) * 0.3,
      life: randomLife ? Math.random() * 8000 : 0,
      maxLife: 6000 + Math.random() * 6000,
      size: 1 + Math.random() * 1.5,
      alpha: 0,
    };
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
    if (this.rafId) {
      cancelAnimationFrame(this.rafId);
      this.rafId = 0;
    }
  }

  private tick = (now: number) => {
    if (!this.running) return;
    const dt = Math.min(now - this.lastTime, 50);
    this.lastTime = now;

    this.update(dt);
    this.draw();

    this.rafId = requestAnimationFrame(this.tick);
  };

  private update(dt: number) {
    this.hubPulse += dt * 0.002;

    // Update node glow
    for (const node of this.nodes) {
      const diff = node.targetGlow - node.glow;
      node.glow += diff * 0.03;
    }

    // Update satellite spawn animations and pulse
    for (const [, sats] of this.satellites) {
      for (const sat of sats) {
        if (sat.spawnProgress < 1) {
          sat.spawnProgress = Math.min(1, sat.spawnProgress + dt * 0.003);
        }
        sat.pulsePhase += dt * 0.003;
      }
    }

    // Update ambient particles
    for (const p of this.ambientParticles) {
      p.life += dt;
      p.x += p.vx * (dt / 16);
      p.y += p.vy * (dt / 16);

      const lifeRatio = p.life / p.maxLife;
      if (lifeRatio < 0.1) p.alpha = lifeRatio * 10;
      else if (lifeRatio > 0.9) p.alpha = (1 - lifeRatio) * 10;
      else p.alpha = 1;

      if (p.life > p.maxLife) {
        Object.assign(p, this.createAmbientParticle(false));
      }
    }

    // Spawn flow particles for active connections
    for (let i = 0; i < this.nodes.length; i++) {
      const node = this.nodes[i];
      if (this.activeRunProjectIds.has(node.id) && Math.random() < 0.05) {
        if (this.flowParticles.length < this.nodes.length * 50) {
          this.flowParticles.push({
            progress: 0,
            speed: 0.0003 + Math.random() * 0.0004,
            nodeIndex: i,
            size: 1.5 + Math.random() * 1.5,
          });
        }
      }
    }

    // Update flow particles
    for (let i = this.flowParticles.length - 1; i >= 0; i--) {
      this.flowParticles[i].progress += this.flowParticles[i].speed * dt;
      if (this.flowParticles[i].progress > 1) {
        this.flowParticles.splice(i, 1);
      }
    }
  }

  private draw() {
    const { ctx, w, h } = this;
    ctx.clearRect(0, 0, w, h);

    // Draw ambient particles
    for (const p of this.ambientParticles) {
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(59, 130, 246, ${p.alpha * 0.15})`;
      ctx.fill();
    }

    // Draw connections (hub <-> project nodes)
    for (const node of this.nodes) {
      const isActive = this.activeRunProjectIds.has(node.id);
      ctx.beginPath();
      ctx.moveTo(this.hubX, this.hubY);
      ctx.lineTo(node.x, node.y);

      if (isActive) {
        ctx.strokeStyle = 'rgba(59, 130, 246, 0.3)';
        ctx.lineWidth = 1.5;
        ctx.setLineDash([]);
      } else {
        ctx.strokeStyle = 'rgba(71, 85, 105, 0.2)';
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 6]);
      }
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Draw satellite connection lines (project <-> employees)
    for (const [projectId, sats] of this.satellites) {
      const parent = this.nodes.find(n => n.id === projectId);
      if (!parent) continue;

      for (const sat of sats) {
        if (sat.spawnProgress < 0.1) continue;
        const style = AGENT_STYLES[sat.employee.mode] ?? AGENT_STYLES.employee;
        const [cr, cg, cb] = style.color;
        const alpha = 0.25 * sat.spawnProgress;

        ctx.beginPath();
        ctx.moveTo(parent.x, parent.y);
        ctx.lineTo(
          parent.x + (sat.x - parent.x) * sat.spawnProgress,
          parent.y + (sat.y - parent.y) * sat.spawnProgress,
        );
        ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${alpha})`;
        ctx.lineWidth = 1;
        ctx.setLineDash(style.dashPattern);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }

    // Draw flow particles
    // Direction depends on phase: employee phase = node->hub, verdict = hub->node
    const inward = this.runPhase === 'employee' || this.runPhase === 'manager_review';
    ctx.globalCompositeOperation = 'lighter';
    for (const fp of this.flowParticles) {
      const node = this.nodes[fp.nodeIndex];
      if (!node) continue;

      let x: number, y: number;
      if (inward) {
        // Node -> Hub (employees reporting to manager)
        x = node.x + (this.hubX - node.x) * fp.progress;
        y = node.y + (this.hubY - node.y) * fp.progress;
      } else {
        // Hub -> Node (manager directing / verdict)
        x = this.hubX + (node.x - this.hubX) * fp.progress;
        y = this.hubY + (node.y - this.hubY) * fp.progress;
      }

      // Phase-based color: employee=blue, manager_review=amber, verdict=green
      const color = this.runPhase === 'manager_review'
        ? '245, 158, 11'
        : this.runPhase === 'executing_verdict'
        ? '16, 185, 129'
        : '59, 130, 246';

      ctx.beginPath();
      ctx.arc(x, y, fp.size, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${color}, ${0.8 * (1 - fp.progress * 0.5)})`;
      ctx.fill();

      // Glow
      ctx.beginPath();
      ctx.arc(x, y, fp.size * 3, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${color}, ${0.15 * (1 - fp.progress * 0.5)})`;
      ctx.fill();
    }
    ctx.globalCompositeOperation = 'source-over';

    // Draw hub
    this.drawHub();

    // Draw project nodes
    for (const node of this.nodes) {
      this.drawProjectNode(node);
    }

    // Draw employee satellites
    for (const [, sats] of this.satellites) {
      for (const sat of sats) {
        this.drawEmployeeSatellite(sat);
      }
    }
  }

  private getPhaseColor(): [number, number, number] {
    switch (this.runPhase) {
      case 'employee': return [59, 130, 246];       // blue
      case 'manager_review': return [245, 158, 11]; // amber
      case 'executing_verdict': return [16, 185, 129]; // green
      default: return [59, 130, 246];                // blue (idle)
    }
  }

  private getPhaseLabel(): string {
    switch (this.runPhase) {
      case 'employee': return 'Reviewing';
      case 'manager_review': return 'Reviewing';
      case 'executing_verdict': return 'Executing';
      default: return '';
    }
  }

  private drawHub() {
    const { ctx } = this;
    const pulse = Math.sin(this.hubPulse) * 0.3 + 0.7;
    const r = this.hubRadius;
    const phaseColor = this.getPhaseColor();
    const [cr, cg, cb] = phaseColor;

    // Hub glow
    if (this.serviceActive) {
      const gradient = ctx.createRadialGradient(this.hubX, this.hubY, r * 0.5, this.hubX, this.hubY, r * 2.5);
      gradient.addColorStop(0, `rgba(${cr}, ${cg}, ${cb}, ${0.15 * pulse})`);
      gradient.addColorStop(1, `rgba(${cr}, ${cg}, ${cb}, 0)`);
      ctx.beginPath();
      ctx.arc(this.hubX, this.hubY, r * 2.5, 0, Math.PI * 2);
      ctx.fillStyle = gradient;
      ctx.fill();
    }

    // Hub hexagon
    ctx.beginPath();
    for (let i = 0; i < 6; i++) {
      const angle = (i / 6) * Math.PI * 2 - Math.PI / 6;
      const x = this.hubX + Math.cos(angle) * r;
      const y = this.hubY + Math.sin(angle) * r;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.closePath();

    ctx.fillStyle = this.serviceActive
      ? `rgba(${cr}, ${cg}, ${cb}, ${0.1 + pulse * 0.08})`
      : 'rgba(30, 41, 59, 0.5)';
    ctx.fill();

    ctx.strokeStyle = this.serviceActive
      ? `rgba(${cr}, ${cg}, ${cb}, ${0.4 + pulse * 0.3})`
      : 'rgba(71, 85, 105, 0.3)';
    ctx.lineWidth = 1.5;
    ctx.stroke();

    // Usage ring
    if (this.usagePercent > 0) {
      const usageR = r + 6;
      const startAngle = -Math.PI / 2;
      const endAngle = startAngle + (this.usagePercent / 100) * Math.PI * 2;

      ctx.beginPath();
      ctx.arc(this.hubX, this.hubY, usageR, startAngle, endAngle);
      ctx.strokeStyle = this.usagePercent > 80
        ? 'rgba(239, 68, 68, 0.6)'
        : 'rgba(16, 185, 129, 0.5)';
      ctx.lineWidth = 2;
      ctx.stroke();
    }

    // Center dot
    ctx.beginPath();
    ctx.arc(this.hubX, this.hubY, 3, 0, Math.PI * 2);
    ctx.fillStyle = this.serviceActive
      ? `rgba(${cr}, ${cg}, ${cb}, ${pulse})`
      : 'rgba(71, 85, 105, 0.5)';
    ctx.fill();

    // "Manager" label below hub
    ctx.font = `bold ${Math.max(10, r * 0.4)}px system-ui, -apple-system, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.fillStyle = this.serviceActive
      ? `rgba(${cr}, ${cg}, ${cb}, 0.8)`
      : 'rgba(148, 163, 184, 0.5)';
    ctx.fillText('Manager', this.hubX, this.hubY + r + 10);

    // Phase label below "Manager" when active
    const phaseLabel = this.getPhaseLabel();
    if (phaseLabel && this.runPhase !== 'idle') {
      ctx.font = `${Math.max(8, r * 0.3)}px system-ui, -apple-system, sans-serif`;
      ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, 0.6)`;
      ctx.fillText(phaseLabel, this.hubX, this.hubY + r + 10 + Math.max(12, r * 0.45));
    }
  }

  private drawProjectNode(node: ProjectNode) {
    const { ctx } = this;
    const r = this.hubRadius * 0.6;
    const isActive = this.activeRunProjectIds.has(node.id);
    const mode = this.activeProjectModes.get(node.id);
    const hasSatellites = (this.satellites.get(node.id)?.length ?? 0) > 0;

    // Glow for active nodes
    if (isActive) {
      const glowColor = mode === 'analyst' ? '168, 85, 247' : '59, 130, 246';
      const gradient = ctx.createRadialGradient(node.x, node.y, r * 0.3, node.x, node.y, r * 2);
      gradient.addColorStop(0, `rgba(${glowColor}, ${0.2 * (0.7 + node.glow * 0.3)})`);
      gradient.addColorStop(1, `rgba(${glowColor}, 0)`);
      ctx.beginPath();
      ctx.arc(node.x, node.y, r * 2, 0, Math.PI * 2);
      ctx.fillStyle = gradient;
      ctx.fill();
    }

    // Hexagon
    ctx.beginPath();
    for (let i = 0; i < 6; i++) {
      const angle = (i / 6) * Math.PI * 2 - Math.PI / 6;
      const x = node.x + Math.cos(angle) * r;
      const y = node.y + Math.sin(angle) * r;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.closePath();

    const priorityColor = node.priority === 'high'
      ? [239, 68, 68]
      : node.priority === 'medium'
      ? [245, 158, 11]
      : [148, 163, 184];

    const alpha = node.enabled ? (isActive ? 0.25 : 0.12) : 0.06;
    ctx.fillStyle = `rgba(${priorityColor.join(',')}, ${alpha})`;
    ctx.fill();

    const borderAlpha = node.enabled ? (isActive ? 0.7 : 0.3) : 0.15;
    ctx.strokeStyle = `rgba(${priorityColor.join(',')}, ${borderAlpha})`;
    ctx.lineWidth = 1;
    ctx.stroke();

    // Role badge for active nodes (only if no satellites — satellites show individual roles)
    if (isActive && mode && !hasSatellites) {
      const roleLabel = mode === 'analyst' ? 'Analyst' : 'Employee';
      const badgeColor = mode === 'analyst' ? '168, 85, 247' : '59, 130, 246';
      const fontSize = Math.max(8, r * 0.55);

      // Badge background pill
      ctx.font = `bold ${fontSize}px system-ui, -apple-system, sans-serif`;
      const textWidth = ctx.measureText(roleLabel).width;
      const badgeW = textWidth + 8;
      const badgeH = fontSize + 4;
      const badgeX = node.x - badgeW / 2;
      const badgeY = node.y - r - badgeH - 4;

      ctx.beginPath();
      ctx.roundRect(badgeX, badgeY, badgeW, badgeH, 3);
      ctx.fillStyle = `rgba(${badgeColor}, 0.2)`;
      ctx.fill();
      ctx.strokeStyle = `rgba(${badgeColor}, 0.5)`;
      ctx.lineWidth = 0.5;
      ctx.stroke();

      // Badge text
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = `rgba(${badgeColor}, 0.9)`;
      ctx.fillText(roleLabel, node.x, badgeY + badgeH / 2);
    }
  }

  /** Draw a satellite hexagon representing an active employee */
  private drawEmployeeSatellite(sat: EmployeeSatellite) {
    if (sat.spawnProgress < 0.01) return;

    const { ctx } = this;
    const baseR = this.hubRadius * 0.35;
    const r = baseR * sat.spawnProgress; // animate size on spawn
    const emp = sat.employee;
    const style = AGENT_STYLES[emp.mode] ?? AGENT_STYLES.employee;
    const statusColor = STATUS_COLORS[emp.status] ?? STATUS_COLORS.running;
    const [cr, cg, cb] = style.color;
    const [sr, sg, sb] = statusColor;

    const globalAlpha = sat.spawnProgress;

    // Pulse glow for running employees
    if (emp.status === 'running') {
      const pulse = Math.sin(sat.pulsePhase) * 0.3 + 0.7;
      const gradient = ctx.createRadialGradient(sat.x, sat.y, r * 0.3, sat.x, sat.y, r * 2);
      gradient.addColorStop(0, `rgba(${cr}, ${cg}, ${cb}, ${0.15 * pulse * globalAlpha})`);
      gradient.addColorStop(1, `rgba(${cr}, ${cg}, ${cb}, 0)`);
      ctx.beginPath();
      ctx.arc(sat.x, sat.y, r * 2, 0, Math.PI * 2);
      ctx.fillStyle = gradient;
      ctx.fill();
    }

    // Hexagon shape
    ctx.beginPath();
    for (let i = 0; i < 6; i++) {
      const angle = (i / 6) * Math.PI * 2 - Math.PI / 6;
      const x = sat.x + Math.cos(angle) * r;
      const y = sat.y + Math.sin(angle) * r;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.closePath();

    // Fill style depends on agent type
    ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, ${style.fillAlpha * globalAlpha})`;
    ctx.fill();

    // Border style depends on agent type (dash pattern)
    ctx.setLineDash(style.dashPattern);
    ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${0.6 * globalAlpha})`;
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.setLineDash([]);

    // Status indicator dot (top-right of hex)
    const dotX = sat.x + r * 0.6;
    const dotY = sat.y - r * 0.6;
    const dotR = Math.max(2, r * 0.2);

    ctx.beginPath();
    ctx.arc(dotX, dotY, dotR, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(${sr}, ${sg}, ${sb}, ${0.9 * globalAlpha})`;
    ctx.fill();

    // Pulsing ring for running status
    if (emp.status === 'running') {
      const pulseScale = 1 + Math.sin(sat.pulsePhase * 2) * 0.3;
      ctx.beginPath();
      ctx.arc(dotX, dotY, dotR * pulseScale * 1.8, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(${sr}, ${sg}, ${sb}, ${0.3 * globalAlpha})`;
      ctx.lineWidth = 1;
      ctx.stroke();
    }

    // Issue number label inside hex
    if (emp.issueNumber != null) {
      const fontSize = Math.max(7, r * 0.5);
      ctx.font = `bold ${fontSize}px system-ui, -apple-system, sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, ${0.9 * globalAlpha})`;
      ctx.fillText(`#${emp.issueNumber}`, sat.x, sat.y);
    }

    // Agent type label below hex
    const labelFontSize = Math.max(6, r * 0.35);
    ctx.font = `${labelFontSize}px system-ui, -apple-system, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, ${0.6 * globalAlpha})`;
    ctx.fillText(style.label, sat.x, sat.y + r + 2);
  }

  getNodeAt(clientX: number, clientY: number): ProjectNode | null {
    const rect = this.canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    const hitRadius = this.hubRadius * 0.8;

    for (const node of this.nodes) {
      const dx = x - node.x;
      const dy = y - node.y;
      if (dx * dx + dy * dy < hitRadius * hitRadius) {
        return node;
      }
    }
    return null;
  }

  /** Get the satellite (employee) at the given screen coordinates */
  getSatelliteAt(clientX: number, clientY: number): EmployeeSatellite | null {
    const rect = this.canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    const hitRadius = this.hubRadius * 0.45;

    for (const [, sats] of this.satellites) {
      for (const sat of sats) {
        if (sat.spawnProgress < 0.5) continue;
        const dx = x - sat.x;
        const dy = y - sat.y;
        if (dx * dx + dy * dy < hitRadius * hitRadius) {
          return sat;
        }
      }
    }
    return null;
  }

  isHubAt(clientX: number, clientY: number): boolean {
    const rect = this.canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    const dx = x - this.hubX;
    const dy = y - this.hubY;
    return dx * dx + dy * dy < this.hubRadius * this.hubRadius * 1.5;
  }
}
