<script lang="ts">
  // Soft Sunrise Vapor background — neumorphic ripples, warm glows, film grain
  // Renders as fixed layers behind all content (z-index: 0)
</script>

<!-- Base color -->
<div class="fixed inset-0 z-0" style="background: var(--color-void);"></div>

<!-- Neumorphic ripple field -->
<div class="fixed inset-0 z-0 pointer-events-none overflow-hidden">
  <div class="neu-ripple" style="width:500px;height:500px;top:5%;left:8%;--duration:7s;--delay:0s;"></div>
  <div class="neu-ripple-inner" style="width:350px;height:350px;top:8.5%;left:11.5%;--duration:7s;--delay:0.4s;"></div>
  <div class="neu-ripple" style="width:500px;height:500px;top:5%;left:8%;--duration:7s;--delay:3.5s;"></div>
  <div class="neu-ripple" style="width:450px;height:450px;top:15%;left:28%;--duration:8s;--delay:1.5s;"></div>
  <div class="neu-ripple-inner" style="width:300px;height:300px;top:18.5%;left:31.5%;--duration:8s;--delay:1.9s;"></div>
  <div class="neu-ripple" style="width:450px;height:450px;top:15%;left:28%;--duration:8s;--delay:5.5s;"></div>
  <div class="neu-ripple" style="width:600px;height:600px;top:30%;left:35%;--duration:9s;--delay:0.5s;"></div>
  <div class="neu-ripple-inner" style="width:420px;height:420px;top:34%;left:39%;--duration:9s;--delay:0.9s;"></div>
  <div class="neu-ripple" style="width:600px;height:600px;top:30%;left:35%;--duration:9s;--delay:4.5s;"></div>
  <div class="neu-ripple" style="width:480px;height:480px;top:20%;left:65%;--duration:6.5s;--delay:2s;"></div>
  <div class="neu-ripple-inner" style="width:320px;height:320px;top:23.5%;left:68.5%;--duration:6.5s;--delay:2.4s;"></div>
  <div class="neu-ripple" style="width:480px;height:480px;top:20%;left:65%;--duration:6.5s;--delay:5.2s;"></div>
  <div class="neu-ripple" style="width:520px;height:520px;top:60%;left:5%;--duration:8s;--delay:1s;"></div>
  <div class="neu-ripple-inner" style="width:360px;height:360px;top:63.5%;left:8.5%;--duration:8s;--delay:1.4s;"></div>
  <div class="neu-ripple" style="width:520px;height:520px;top:60%;left:5%;--duration:8s;--delay:5s;"></div>
  <div class="neu-ripple" style="width:470px;height:470px;top:65%;left:38%;--duration:7s;--delay:3s;"></div>
  <div class="neu-ripple-inner" style="width:320px;height:320px;top:68.5%;left:41.5%;--duration:7s;--delay:3.4s;"></div>
  <div class="neu-ripple" style="width:500px;height:500px;top:55%;left:70%;--duration:7.5s;--delay:0s;"></div>
  <div class="neu-ripple-inner" style="width:350px;height:350px;top:58.5%;left:73.5%;--duration:7.5s;--delay:0.4s;"></div>
  <div class="neu-ripple" style="width:500px;height:500px;top:55%;left:70%;--duration:7.5s;--delay:3.8s;"></div>
</div>

<!-- Warm glows -->
<div class="warm-glow warm-glow-1"></div>
<div class="warm-glow warm-glow-2"></div>
<div class="warm-glow warm-glow-3"></div>

<!-- Film grain -->
<div class="grain"></div>
