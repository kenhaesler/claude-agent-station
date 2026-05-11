<script lang="ts">
  // Cyberpunk 2077 FX background — perspective grid floor, neon ambient blobs,
  // CRT scanline veil, vignette. Renders as fixed layers behind all content
  // (z-index: 0). Honors [data-animations="off"] and prefers-reduced-motion.
</script>

<!-- Pure black backdrop -->
<div class="fixed inset-0 z-0" style="background: #05060A;"></div>

<!-- Drifting ambient neon blobs -->
<div class="fixed inset-0 z-0 pointer-events-none overflow-hidden">
  <div class="blob blob-cyan"></div>
  <div class="blob blob-yellow"></div>
  <div class="blob blob-magenta"></div>
</div>

<!-- Perspective grid floor + horizon glow -->
<div class="fixed inset-0 z-0 pointer-events-none overflow-hidden">
  <div class="grid-wrap">
    <div class="horizon"></div>
    <div class="grid-floor"></div>
  </div>
</div>

<!-- Vignette -->
<div class="fixed inset-0 z-0 pointer-events-none vignette"></div>

<!-- CRT scanline veil — sits ABOVE app content so the CRT effect blankets
     the whole UI, not just the empty backdrop. Very low opacity so text
     stays legible. -->
<div class="fixed inset-0 pointer-events-none scanlines" style="z-index: 10;"></div>

<style>
  /* ---------- Ambient drifting blobs ---------- */
  .blob {
    position: absolute;
    width: 700px;
    height: 700px;
    border-radius: 50%;
    filter: blur(80px);
    opacity: 0.22;
    pointer-events: none;
    will-change: transform;
  }

  .blob-cyan {
    top: -10%;
    left: -8%;
    background: radial-gradient(circle, #00F0FF 0%, rgba(0, 240, 255, 0) 65%);
    animation: drift-cyan 18s ease-in-out infinite;
  }

  .blob-yellow {
    top: 30%;
    right: -10%;
    left: auto;
    background: radial-gradient(circle, #FCEE0A 0%, rgba(252, 238, 10, 0) 65%);
    animation: drift-yellow 22s ease-in-out infinite;
  }

  .blob-magenta {
    bottom: -12%;
    left: -5%;
    background: radial-gradient(circle, #FF2A6D 0%, rgba(255, 42, 109, 0) 65%);
    animation: drift-magenta 14s ease-in-out infinite;
  }

  @keyframes drift-cyan {
    0%   { transform: translate(0, 0) scale(1); }
    50%  { transform: translate(120px, 80px) scale(1.1); }
    100% { transform: translate(0, 0) scale(1); }
  }

  @keyframes drift-yellow {
    0%   { transform: translate(0, 0) scale(1); }
    50%  { transform: translate(-100px, 60px) scale(1.08); }
    100% { transform: translate(0, 0) scale(1); }
  }

  @keyframes drift-magenta {
    0%   { transform: translate(0, 0) scale(1); }
    50%  { transform: translate(140px, -90px) scale(1.12); }
    100% { transform: translate(0, 0) scale(1); }
  }

  /* ---------- Perspective grid floor ---------- */
  .grid-wrap {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    height: 55%;
    overflow: hidden;
  }

  .horizon {
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 1px;
    background: rgba(0, 240, 255, 0.55);
    box-shadow:
      0 0 80px 20px rgba(0, 240, 255, 0.35),
      0 0 200px 60px rgba(252, 238, 10, 0.18);
    z-index: 1;
  }

  .grid-floor {
    position: absolute;
    top: 0;
    left: -10%;
    right: -10%;
    bottom: -20%;
    background-image:
      linear-gradient(to right, rgba(0, 240, 255, 0.18) 1px, transparent 1px),
      linear-gradient(to bottom, rgba(0, 240, 255, 0.18) 1px, transparent 1px);
    background-size: 60px 60px;
    transform: perspective(600px) rotateX(60deg);
    transform-origin: top center;
    -webkit-mask-image: linear-gradient(to top, #000 0%, #000 30%, transparent 100%);
    mask-image: linear-gradient(to top, #000 0%, #000 30%, transparent 100%);
    animation: grid-scroll 2s linear infinite;
    will-change: background-position;
  }

  @keyframes grid-scroll {
    from { background-position-y: 0; }
    to   { background-position-y: 60px; }
  }

  /* ---------- CRT scanlines (over-the-content veil) ----------
     Sits above #app so the CRT effect blankets the whole UI. Opacity
     deliberately very low so it doesn't impair text legibility. */
  .scanlines {
    background: repeating-linear-gradient(
      to bottom,
      transparent 0 2px,
      rgba(0, 240, 255, 0.025) 2px 3px
    );
    opacity: 0.35;
  }

  /* ---------- Vignette ---------- */
  .vignette {
    box-shadow: inset 0 0 200px 80px rgba(0, 0, 0, 0.55);
  }

  /* ---------- Animation kill-switch ----------
     Also clears will-change so GPU layers aren't held while animations
     are off (the hint exists only to smooth running animations). */
  :global([data-animations="off"]) .grid-floor,
  :global([data-animations="off"]) .blob-cyan,
  :global([data-animations="off"]) .blob-yellow,
  :global([data-animations="off"]) .blob-magenta {
    animation: none;
    will-change: auto;
  }

  @media (prefers-reduced-motion: reduce) {
    .grid-floor,
    .blob-cyan,
    .blob-yellow,
    .blob-magenta {
      animation: none;
      will-change: auto;
    }
  }
</style>
