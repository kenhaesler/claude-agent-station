<script lang="ts">
  interface Props {
    enabled?: boolean;
    phase?: string;
  }

  let { enabled = true, phase = 'idle' }: Props = $props();

  let canvas = $state<HTMLCanvasElement>(undefined!);
  let animFrame = 0;

  const PARTICLE_COUNT = 50;
  const LAYERS = 3;

  interface Particle {
    x: number;
    y: number;
    r: number;
    layer: number;
    alpha: number;
    hue: number;
    speed: number;
  }

  function phaseHue(p: string): number {
    switch (p) {
      case 'employee': return 230;
      case 'manager_review': return 45;
      case 'executing_verdict': return 145;
      case 'coordinating': return 280;
      default: return 220;
    }
  }

  function createParticles(w: number, h: number): Particle[] {
    const particles: Particle[] = [];
    for (let i = 0; i < PARTICLE_COUNT; i++) {
      const layer = i % LAYERS;
      particles.push({
        x: Math.random() * w,
        y: Math.random() * h,
        r: 2 + Math.random() * 4 + layer * 2,
        layer,
        alpha: 0.08 + Math.random() * 0.15,
        hue: phaseHue(phase) + (Math.random() - 0.5) * 40,
        speed: 0.05 + Math.random() * 0.15,
      });
    }
    return particles;
  }

  let mouseX = 0;
  let mouseY = 0;

  function handleMouseMove(e: MouseEvent) {
    mouseX = (e.clientX / window.innerWidth - 0.5) * 2;
    mouseY = (e.clientY / window.innerHeight - 0.5) * 2;
  }

  $effect(() => {
    if (!canvas || !enabled) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let w = canvas.width = canvas.offsetWidth;
    let h = canvas.height = canvas.offsetHeight;
    let particles = createParticles(w, h);

    const onResize = () => {
      w = canvas.width = canvas.offsetWidth;
      h = canvas.height = canvas.offsetHeight;
      particles = createParticles(w, h);
    };
    window.addEventListener('resize', onResize);
    window.addEventListener('mousemove', handleMouseMove);

    let t = 0;
    function draw() {
      t += 0.016;
      ctx!.clearRect(0, 0, w, h);

      for (const p of particles) {
        // Parallax offset based on layer depth
        const parallaxFactor = (p.layer + 1) / LAYERS;
        const px = p.x + mouseX * 15 * parallaxFactor;
        const py = p.y + mouseY * 10 * parallaxFactor;

        // Slow drift
        p.y -= p.speed;
        if (p.y + p.r < 0) {
          p.y = h + p.r;
          p.x = Math.random() * w;
        }

        // Pulsing alpha
        const pulseAlpha = p.alpha * (0.7 + 0.3 * Math.sin(t * 0.5 + p.x * 0.01));

        // Draw radial gradient circle
        const grad = ctx!.createRadialGradient(px, py, 0, px, py, p.r);
        grad.addColorStop(0, `hsla(${p.hue}, 60%, 70%, ${pulseAlpha})`);
        grad.addColorStop(1, `hsla(${p.hue}, 60%, 70%, 0)`);
        ctx!.fillStyle = grad;
        ctx!.beginPath();
        ctx!.arc(px, py, p.r, 0, Math.PI * 2);
        ctx!.fill();
      }

      animFrame = requestAnimationFrame(draw);
    }

    animFrame = requestAnimationFrame(draw);

    return () => {
      cancelAnimationFrame(animFrame);
      window.removeEventListener('resize', onResize);
      window.removeEventListener('mousemove', handleMouseMove);
    };
  });
</script>

{#if enabled}
  <canvas bind:this={canvas} class="w-full h-full block"></canvas>
{/if}
