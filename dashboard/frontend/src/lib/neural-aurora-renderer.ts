/**
 * Neural Aurora Mesh — Living intelligence background renderer.
 * Canvas 2D: layered aurora color fields, flow field neural pathways,
 * organic breathing rhythm, bloom post-processing.
 */

// ─── Simplex Noise ──────────────────────────────────────────

const P = new Uint8Array([
  151,160,137,91,90,15,131,13,201,95,96,53,194,233,7,225,140,36,103,30,69,142,
  8,99,37,240,21,10,23,190,6,148,247,120,234,75,0,26,197,62,94,252,219,203,117,
  35,11,32,57,177,33,88,237,149,56,87,174,20,125,136,171,168,68,175,74,165,71,
  134,139,48,27,166,77,146,158,231,83,111,229,122,60,211,133,230,220,105,92,41,
  55,46,245,40,244,102,143,54,65,25,63,161,1,216,80,73,209,76,132,187,208,89,
  18,169,200,196,135,130,116,188,159,86,164,100,109,198,173,186,3,64,52,217,226,
  250,124,123,5,202,38,147,118,126,255,82,85,212,207,206,59,227,47,16,58,17,182,
  189,28,42,223,183,170,213,119,248,152,2,44,154,163,70,221,153,101,155,167,43,
  172,9,129,22,39,253,19,98,108,110,79,113,224,232,178,185,112,104,218,246,97,
  228,251,34,242,193,238,210,144,12,191,179,162,241,81,51,145,235,249,14,239,
  107,49,192,214,31,181,199,106,157,184,84,204,176,115,121,50,45,127,4,150,254,
  138,236,205,93,222,114,67,29,24,72,243,141,128,195,78,66,215,61,156,180,
]);

const PERM = new Uint8Array(512);
const PERM12 = new Uint8Array(512);
for (let i = 0; i < 512; i++) {
  PERM[i] = P[i & 255];
  PERM12[i] = PERM[i] % 12;
}

const G3 = [
  [1,1,0],[-1,1,0],[1,-1,0],[-1,-1,0],
  [1,0,1],[-1,0,1],[1,0,-1],[-1,0,-1],
  [0,1,1],[0,-1,1],[0,1,-1],[0,-1,-1],
];

const F2 = 0.5 * (Math.sqrt(3) - 1);
const G2 = (3 - Math.sqrt(3)) / 6;

function noise2D(xin: number, yin: number): number {
  const s = (xin + yin) * F2;
  const i = Math.floor(xin + s), j = Math.floor(yin + s);
  const t = (i + j) * G2;
  const x0 = xin - i + t, y0 = yin - j + t;
  const i1 = x0 > y0 ? 1 : 0, j1 = x0 > y0 ? 0 : 1;
  const x1 = x0 - i1 + G2, y1 = y0 - j1 + G2;
  const x2 = x0 - 1 + 2 * G2, y2 = y0 - 1 + 2 * G2;
  const ii = i & 255, jj = j & 255;
  let n0 = 0, n1 = 0, n2 = 0;
  let t0 = 0.5 - x0 * x0 - y0 * y0;
  if (t0 > 0) { t0 *= t0; const g = G3[PERM12[ii + PERM[jj]]]; n0 = t0 * t0 * (g[0] * x0 + g[1] * y0); }
  let t1 = 0.5 - x1 * x1 - y1 * y1;
  if (t1 > 0) { t1 *= t1; const g = G3[PERM12[ii + i1 + PERM[jj + j1]]]; n1 = t1 * t1 * (g[0] * x1 + g[1] * y1); }
  let t2 = 0.5 - x2 * x2 - y2 * y2;
  if (t2 > 0) { t2 *= t2; const g = G3[PERM12[ii + 1 + PERM[jj + 1]]]; n2 = t2 * t2 * (g[0] * x2 + g[1] * y2); }
  return 70 * (n0 + n1 + n2);
}

function noise3D(xin: number, yin: number, zin: number): number {
  const F = 1 / 3, G = 1 / 6;
  const s = (xin + yin + zin) * F;
  const i = Math.floor(xin + s), j = Math.floor(yin + s), k = Math.floor(zin + s);
  const t = (i + j + k) * G;
  const x0 = xin - i + t, y0 = yin - j + t, z0 = zin - k + t;
  let i1: number, j1: number, k1: number, i2: number, j2: number, k2: number;
  if (x0 >= y0) {
    if (y0 >= z0) { i1=1;j1=0;k1=0;i2=1;j2=1;k2=0; }
    else if (x0 >= z0) { i1=1;j1=0;k1=0;i2=1;j2=0;k2=1; }
    else { i1=0;j1=0;k1=1;i2=1;j2=0;k2=1; }
  } else {
    if (y0 < z0) { i1=0;j1=0;k1=1;i2=0;j2=1;k2=1; }
    else if (x0 < z0) { i1=0;j1=1;k1=0;i2=0;j2=1;k2=1; }
    else { i1=0;j1=1;k1=0;i2=1;j2=1;k2=0; }
  }
  const x1 = x0-i1+G, y1 = y0-j1+G, z1 = z0-k1+G;
  const x2 = x0-i2+2*G, y2 = y0-j2+2*G, z2 = z0-k2+2*G;
  const x3 = x0-1+3*G, y3 = y0-1+3*G, z3 = z0-1+3*G;
  const ii = i & 255, jj = j & 255, kk = k & 255;
  let n0 = 0, n1 = 0, n2 = 0, n3 = 0;
  let t0 = 0.6 - x0*x0 - y0*y0 - z0*z0;
  if (t0 > 0) { t0 *= t0; const g = G3[PERM12[ii+PERM[jj+PERM[kk]]]]; n0 = t0*t0*(g[0]*x0+g[1]*y0+g[2]*z0); }
  let t1 = 0.6 - x1*x1 - y1*y1 - z1*z1;
  if (t1 > 0) { t1 *= t1; const g = G3[PERM12[ii+i1+PERM[jj+j1+PERM[kk+k1]]]]; n1 = t1*t1*(g[0]*x1+g[1]*y1+g[2]*z1); }
  let t2 = 0.6 - x2*x2 - y2*y2 - z2*z2;
  if (t2 > 0) { t2 *= t2; const g = G3[PERM12[ii+i2+PERM[jj+j2+PERM[kk+k2]]]]; n2 = t2*t2*(g[0]*x2+g[1]*y2+g[2]*z2); }
  let t3 = 0.6 - x3*x3 - y3*y3 - z3*z3;
  if (t3 > 0) { t3 *= t3; const g = G3[PERM12[ii+1+PERM[jj+1+PERM[kk+1]]]]; n3 = t3*t3*(g[0]*x3+g[1]*y3+g[2]*z3); }
  return 32 * (n0 + n1 + n2 + n3);
}

// ─── Color Utilities ────────────────────────────────────────

type RGB = [number, number, number];

export function parseCSSColor(str: string): RGB {
  str = str.trim();
  if (str.startsWith('#')) {
    const h = str.length === 4
      ? str[1]+str[1]+str[2]+str[2]+str[3]+str[3]
      : str.slice(1, 7);
    return [parseInt(h.slice(0,2),16), parseInt(h.slice(2,4),16), parseInt(h.slice(4,6),16)];
  }
  const m = str.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
  if (m) return [+m[1], +m[2], +m[3]];
  try {
    const ctx = document.createElement('canvas').getContext('2d')!;
    ctx.fillStyle = '#010203';
    ctx.fillStyle = str;
    const n = ctx.fillStyle;
    if (n !== '#010203' && n.startsWith('#')) {
      return [parseInt(n.slice(1,3),16), parseInt(n.slice(3,5),16), parseInt(n.slice(5,7),16)];
    }
  } catch { /* noop */ }
  return [18, 20, 30];
}

function rgbToHue(r: number, g: number, b: number): number {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
  if (d < 0.001) return 260;
  let h: number;
  if (max === r) h = 60 * (((g - b) / d) % 6);
  else if (max === g) h = 60 * ((b - r) / d + 2);
  else h = 60 * ((r - g) / d + 4);
  return ((h % 360) + 360) % 360;
}

function hslToRgb(h: number, s: number, l: number): RGB {
  h = ((h % 360) + 360) % 360;
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  let r: number, g: number, b: number;
  if (h < 60) { r=c; g=x; b=0; } else if (h < 120) { r=x; g=c; b=0; }
  else if (h < 180) { r=0; g=c; b=x; } else if (h < 240) { r=0; g=x; b=c; }
  else if (h < 300) { r=x; g=0; b=c; } else { r=c; g=0; b=x; }
  return [Math.round((r+m)*255), Math.round((g+m)*255), Math.round((b+m)*255)];
}

function lerpColor(a: RGB, b: RGB, t: number): RGB {
  return [
    Math.round(a[0] + (b[0] - a[0]) * t),
    Math.round(a[1] + (b[1] - a[1]) * t),
    Math.round(a[2] + (b[2] - a[2]) * t),
  ];
}

// ─── Types & Constants ──────────────────────────────────────

interface AuroraPoint {
  id: number;
  layer: number;
  baseX: number;
  baseY: number;
  orbitRadius: number;
  orbitSpeed: number;
  alpha: number;
  radius: number;
}

interface FlowCurve {
  points: Array<[number, number]>;
  maxPoints: number;
  headX: number;
  headY: number;
  alpha: number;
  width: number;
  pulseOffset: number;
  pulseSpeed: number;
  fadeIn: number;
}

interface PhaseConfig {
  color: RGB;
  blend: number;
  breatheSpeed: number;
}

const PHASE_CONFIGS: Record<string, PhaseConfig> = {
  idle:               { color: [15,  20,  55],  blend: 0,    breatheSpeed: 0.4  },
  employee:           { color: [59,  130, 246], blend: 0.3,  breatheSpeed: 0.8  },
  manager_review:     { color: [245, 158, 11],  blend: 0.2,  breatheSpeed: 0.8  },
  executing_verdict:  { color: [16,  185, 129], blend: 0.25, breatheSpeed: 0.8  },
  coordinating:       { color: [168, 85,  247], blend: 0.15, breatheSpeed: 0.8  },
};

const LAYER_DEFS = [
  { count: 2, speed: 0.017, aMin: 0.28, aMax: 0.38, rMin: 0.50, rMax: 0.60, hShift: 0   },
  { count: 3, speed: 0.029, aMin: 0.22, aMax: 0.32, rMin: 0.35, rMax: 0.45, hShift: 20  },
  { count: 2, speed: 0.050, aMin: 0.15, aMax: 0.25, rMin: 0.25, rMax: 0.35, hShift: -20 },
];

const CURVE_COUNT = 16;
const CURVE_SPEED = 80;

// ─── Renderer ───────────────────────────────────────────────

export class NeuralAuroraRenderer {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private bloomCanvas: HTMLCanvasElement;
  private bloomCtx: CanvasRenderingContext2D;
  private width = 0;
  private height = 0;
  private dpr = 1;
  private diagonal = 0;
  private time = 0;
  private lastTime = 0;
  private running = false;
  private animFrame = 0;

  private auroraPoints: AuroraPoint[] = [];
  private curves: FlowCurve[] = [];

  private currentPhase = 'idle';
  private breathePhase = 0;
  private breatheSpeed = 0.4;
  private targetBreatheSpeed = 0.4;
  private breathe = 1;

  private baseHue = 260;
  private isDark = true;
  private alphaScale = 1;
  private auroraColors: RGB[] = [[15,20,55],[40,50,120],[60,45,130]];
  private curveColor: RGB = [60, 65, 180];

  private waveActive = false;
  private waveTime = 0;
  private waveColor: RGB = [99, 102, 241];

  private reducedMotion = false;
  private staticDrawn = false;

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d')!;
    this.bloomCanvas = document.createElement('canvas');
    this.bloomCtx = this.bloomCanvas.getContext('2d')!;
    this.initAurora();
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.lastTime = performance.now() / 1000;
    this.animFrame = requestAnimationFrame(this.loop);
  }

  stop(): void {
    this.running = false;
    cancelAnimationFrame(this.animFrame);
  }

  destroy(): void {
    this.stop();
  }

  resize(width: number, height: number): void {
    if (width < 1 || height < 1) return;
    this.width = width;
    this.height = height;
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.diagonal = Math.sqrt(width * width + height * height);
    this.canvas.width = Math.round(width * this.dpr);
    this.canvas.height = Math.round(height * this.dpr);
    this.bloomCanvas.width = Math.ceil(this.canvas.width / 4);
    this.bloomCanvas.height = Math.ceil(this.canvas.height / 4);
    this.initCurves();
    if (this.reducedMotion) { this.staticDrawn = false; this.renderStaticFrame(); }
  }

  setPhase(phase: string): void {
    if (phase === this.currentPhase) return;
    if (this.currentPhase === 'idle' && phase !== 'idle') {
      this.waveActive = true;
      this.waveTime = 0;
      const cfg = PHASE_CONFIGS[phase] || PHASE_CONFIGS.idle;
      this.waveColor = [...cfg.color] as RGB;
    }
    this.currentPhase = phase;
    this.targetBreatheSpeed = (PHASE_CONFIGS[phase] || PHASE_CONFIGS.idle).breatheSpeed;
  }

  setThemeColors(bg: RGB, scheme: 'dark' | 'light'): void {
    this.isDark = scheme === 'dark';
    this.alphaScale = this.isDark ? 1 : 0.35;
    this.baseHue = rgbToHue(bg[0], bg[1], bg[2]);
    this.computeColors();
    if (this.reducedMotion) { this.staticDrawn = false; this.renderStaticFrame(); }
  }

  setReducedMotion(reduced: boolean): void {
    this.reducedMotion = reduced;
    if (reduced) {
      this.stop();
      this.staticDrawn = false;
      this.renderStaticFrame();
    } else {
      this.staticDrawn = false;
      this.start();
    }
  }

  // ── Initialization ──────────────────────────────────────

  private initAurora(): void {
    this.auroraPoints = [];
    let id = 0;
    for (let li = 0; li < LAYER_DEFS.length; li++) {
      const L = LAYER_DEFS[li];
      for (let i = 0; i < L.count; i++) {
        const spread = L.count > 1 ? i / (L.count - 1) : 0.5;
        this.auroraPoints.push({
          id: id++,
          layer: li,
          baseX: 0.25 + spread * 0.5,
          baseY: 0.25 + (id % 3) * 0.25,
          orbitRadius: 0.12 + (id * 0.031 % 0.1),
          orbitSpeed: L.speed,
          alpha: L.aMin + (id * 0.037 % 1) * (L.aMax - L.aMin),
          radius: L.rMin + (id * 0.043 % 1) * (L.rMax - L.rMin),
        });
      }
    }
    this.computeColors();
  }

  private initCurves(): void {
    this.curves = [];
    if (this.width === 0) return;
    for (let i = 0; i < CURVE_COUNT; i++) {
      const c: FlowCurve = {
        points: [],
        maxPoints: 200 + ((i * 73) % 300),
        headX: Math.random() * this.width,
        headY: Math.random() * this.height,
        alpha: 0.12 + (i * 0.0037 % 0.13),
        width: 0.8 + (i * 0.027 % 0.4),
        pulseOffset: i / CURVE_COUNT,
        pulseSpeed: 0.3 + (i * 0.019 % 0.3),
        fadeIn: 0,
      };
      c.points.push([c.headX, c.headY]);
      this.curves.push(c);
    }
  }

  private computeColors(): void {
    const h = this.baseHue;
    if (this.isDark) {
      this.auroraColors = [
        hslToRgb(h, 0.65, 0.35),
        hslToRgb(h + 20, 0.60, 0.42),
        hslToRgb(h - 20, 0.55, 0.48),
      ];
      this.curveColor = hslToRgb(h + 10, 0.60, 0.55);
    } else {
      this.auroraColors = [
        hslToRgb(h, 0.35, 0.45),
        hslToRgb(h + 20, 0.30, 0.50),
        hslToRgb(h - 20, 0.25, 0.55),
      ];
      this.curveColor = hslToRgb(h + 10, 0.40, 0.40);
    }
  }

  // ── Animation Loop ──────────────────────────────────────

  private loop = (): void => {
    if (!this.running) return;
    const now = performance.now() / 1000;
    const dt = Math.min(now - this.lastTime, 0.05);
    this.lastTime = now;
    this.tick(dt);
    this.render();
    this.animFrame = requestAnimationFrame(this.loop);
  };

  private tick(dt: number): void {
    this.time += dt;

    // Breathing
    this.breatheSpeed += (this.targetBreatheSpeed - this.breatheSpeed) * 2 * dt;
    this.breathePhase += this.breatheSpeed * dt;
    this.breathe = 0.85 + 0.15 * Math.sin(this.breathePhase);

    // Activation wave
    if (this.waveActive) {
      this.waveTime += dt;
      if (this.waveTime >= 1.5) this.waveActive = false;
    }

    // Advance curves
    if (this.width > 0) {
      const step = CURVE_SPEED * dt;
      for (const c of this.curves) {
        if (c.fadeIn < 1) c.fadeIn = Math.min(1, c.fadeIn + dt * 0.5);
        this.advanceCurve(c, step);
      }
    }
  }

  private advanceCurve(c: FlowCurve, step: number): void {
    const angle = this.getFlowAngle(c.headX, c.headY);
    c.headX += Math.cos(angle) * step;
    c.headY += Math.sin(angle) * step;
    c.points.push([c.headX, c.headY]);
    while (c.points.length > c.maxPoints) c.points.shift();

    const margin = 50;
    if (c.headX < -margin || c.headX > this.width + margin ||
        c.headY < -margin || c.headY > this.height + margin) {
      this.respawnCurve(c);
    }
  }

  private respawnCurve(c: FlowCurve): void {
    c.points = [];
    c.headX = Math.random() * this.width;
    c.headY = Math.random() * this.height;
    c.points.push([c.headX, c.headY]);
    c.fadeIn = 0;
    c.pulseOffset = Math.random();
  }

  private getFlowAngle(x: number, y: number): number {
    const t = this.time * 0.08;
    return noise3D(x * 0.003, y * 0.003, t) * Math.PI * 2
         + noise3D(x * 0.006, y * 0.006, t) * Math.PI * 0.5;
  }

  // ── Rendering ───────────────────────────────────────────

  private render(): void {
    if (this.width === 0 || this.height === 0) return;
    const ctx = this.ctx;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.width, this.height);
    this.renderAurora();
    this.renderVignette();
    this.renderCurves();
    this.renderActivationWave();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    this.renderBloom();
  }

  private renderStaticFrame(): void {
    if (this.staticDrawn || this.width === 0) return;
    this.staticDrawn = true;
    this.breathe = 1;
    this.time = 0;
    const ctx = this.ctx;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.width, this.height);
    this.renderAurora();
    this.renderVignette();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    this.renderBloom();
  }

  private renderAurora(): void {
    const ctx = this.ctx;
    const phase = PHASE_CONFIGS[this.currentPhase] || PHASE_CONFIGS.idle;
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    for (const pt of this.auroraPoints) {
      const px = pt.baseX * this.width
        + noise2D(pt.id * 7.3, this.time * pt.orbitSpeed) * pt.orbitRadius * this.width;
      const py = pt.baseY * this.height
        + noise2D(pt.id * 7.3 + 100, this.time * pt.orbitSpeed) * pt.orbitRadius * this.height;
      const r = pt.radius * this.diagonal;
      const base = this.auroraColors[pt.layer] || this.auroraColors[0];
      const col = lerpColor(base, phase.color, phase.blend);
      const a = pt.alpha * this.breathe * this.alphaScale;
      const grad = ctx.createRadialGradient(px, py, 0, px, py, r);
      grad.addColorStop(0, `rgba(${col[0]},${col[1]},${col[2]},${a})`);
      grad.addColorStop(1, `rgba(${col[0]},${col[1]},${col[2]},0)`);
      ctx.fillStyle = grad;
      ctx.fillRect(px - r, py - r, r * 2, r * 2);
    }
    ctx.restore();
  }

  private renderVignette(): void {
    const ctx = this.ctx;
    const cx = this.width / 2, cy = this.height / 2;
    const r = this.diagonal * 0.7;
    const grad = ctx.createRadialGradient(cx, cy, r * 0.3, cx, cy, r);
    grad.addColorStop(0, 'transparent');
    grad.addColorStop(1, this.isDark ? 'rgba(0,0,0,0.15)' : 'rgba(255,255,255,0.15)');
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, this.width, this.height);
  }

  private renderCurves(): void {
    const ctx = this.ctx;
    const [cr, cg, cb] = this.curveColor;
    const phase = PHASE_CONFIGS[this.currentPhase] || PHASE_CONFIGS.idle;
    const tinted = lerpColor(this.curveColor, phase.color, phase.blend * 0.5);
    const curveBreath = this.breathe * 0.5 + 0.5;

    ctx.save();
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    for (const c of this.curves) {
      const pts = c.points;
      if (pts.length < 3) continue;
      const fade = c.fadeIn * this.alphaScale * curveBreath;
      const baseA = c.alpha * fade;

      // Base curve with tail-to-head gradient
      const tail = pts[0], head = pts[pts.length - 1];
      const grad = ctx.createLinearGradient(tail[0], tail[1], head[0], head[1]);
      grad.addColorStop(0, `rgba(${tinted[0]},${tinted[1]},${tinted[2]},0)`);
      grad.addColorStop(0.4, `rgba(${tinted[0]},${tinted[1]},${tinted[2]},${baseA * 0.4})`);
      grad.addColorStop(1, `rgba(${tinted[0]},${tinted[1]},${tinted[2]},${baseA})`);
      ctx.beginPath();
      ctx.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
      ctx.strokeStyle = grad;
      ctx.lineWidth = c.width;
      ctx.stroke();

      // Traveling pulse
      const pulseT = ((c.pulseOffset + this.time * c.pulseSpeed) % 1);
      const pi = Math.floor(pulseT * pts.length);
      const hw = Math.max(3, Math.floor(pts.length * 0.06));
      const ps = Math.max(1, pi - hw), pe = Math.min(pts.length - 1, pi + hw);
      if (pe > ps) {
        ctx.beginPath();
        ctx.moveTo(pts[ps][0], pts[ps][1]);
        for (let i = ps + 1; i <= pe; i++) ctx.lineTo(pts[i][0], pts[i][1]);
        ctx.strokeStyle = `rgba(${tinted[0]},${tinted[1]},${tinted[2]},${Math.min(baseA * 4, 0.5)})`;
        ctx.lineWidth = c.width + 0.5;
        ctx.stroke();
      }
    }
    ctx.restore();
  }

  private renderActivationWave(): void {
    if (!this.waveActive) return;
    const p = this.waveTime / 1.5;
    if (p >= 1) return;
    const ctx = this.ctx;
    const [r, g, b] = this.waveColor;
    ctx.beginPath();
    ctx.arc(this.width / 2, this.height / 2, p * this.diagonal, 0, Math.PI * 2);
    ctx.strokeStyle = `rgba(${r},${g},${b},${0.08 * (1 - p)})`;
    ctx.lineWidth = 3 + (1 - p) * 5;
    ctx.stroke();
  }

  private renderBloom(): void {
    const bw = this.bloomCanvas.width, bh = this.bloomCanvas.height;
    if (bw === 0 || bh === 0) return;
    const cw = this.canvas.width, ch = this.canvas.height;
    this.bloomCtx.clearRect(0, 0, bw, bh);
    this.bloomCtx.drawImage(this.canvas, 0, 0, bw, bh);
    this.ctx.save();
    this.ctx.globalCompositeOperation = 'lighter';
    this.ctx.globalAlpha = 0.35;
    this.ctx.drawImage(this.bloomCanvas, 0, 0, cw, ch);
    this.ctx.globalAlpha = 0.05;
    this.ctx.filter = 'blur(2px)';
    this.ctx.drawImage(this.bloomCanvas, -4, -4, cw + 8, ch + 8);
    this.ctx.filter = 'none';
    this.ctx.restore();
  }
}
