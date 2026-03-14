<script lang="ts">
  import { T, useTask } from '@threlte/core';
  import * as THREE from 'three';

  interface Props {
    phase?: string;
  }

  let { phase = 'idle' }: Props = $props();

  // Phase-based color tinting
  function phaseColor(p: string): THREE.Color {
    switch (p) {
      case 'employee': return new THREE.Color(0.4, 0.5, 1.0);
      case 'manager_review': return new THREE.Color(0.9, 0.7, 0.3);
      case 'executing_verdict': return new THREE.Color(0.3, 0.9, 0.5);
      case 'coordinating': return new THREE.Color(0.7, 0.4, 1.0);
      default: return new THREE.Color(0.6, 0.65, 1.0);
    }
  }

  // Generate star positions
  const STAR_COUNT = 4000;
  const starPositions = new Float32Array(STAR_COUNT * 3);
  const starSizes = new Float32Array(STAR_COUNT);
  for (let i = 0; i < STAR_COUNT; i++) {
    // Random positions in a sphere
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    const r = 20 + Math.random() * 80;
    starPositions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    starPositions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
    starPositions[i * 3 + 2] = r * Math.cos(phi);
    starSizes[i] = 0.5 + Math.random() * 1.5;
  }

  const starGeometry = new THREE.BufferGeometry();
  starGeometry.setAttribute('position', new THREE.BufferAttribute(starPositions, 3));
  starGeometry.setAttribute('size', new THREE.BufferAttribute(starSizes, 1));

  // Create nebula textures (procedural radial gradients)
  function createNebulaTexture(color1: string, color2: string): THREE.Texture {
    const size = 256;
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext('2d')!;
    const grad = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    grad.addColorStop(0, color1);
    grad.addColorStop(0.5, color2);
    grad.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, size, size);
    const tex = new THREE.CanvasTexture(canvas);
    tex.needsUpdate = true;
    return tex;
  }

  const nebulaTex1 = createNebulaTexture('rgba(60,80,180,0.12)', 'rgba(40,20,100,0.04)');
  const nebulaTex2 = createNebulaTexture('rgba(120,40,160,0.08)', 'rgba(60,10,80,0.03)');
  const nebulaTex3 = createNebulaTexture('rgba(30,100,140,0.10)', 'rgba(10,50,80,0.03)');

  // Camera auto-drift
  let cameraRef: THREE.PerspectiveCamera | null = null;
  let cameraAngle = $state(0);
  let cameraTargetX = $state(0);
  let cameraTargetY = $state(0);
  let cameraX = $state(0);
  let cameraY = $state(0);

  // Mouse parallax
  function handleMouseMove(e: MouseEvent) {
    cameraTargetX = (e.clientX / window.innerWidth - 0.5) * 3;
    cameraTargetY = (e.clientY / window.innerHeight - 0.5) * 2;
  }

  $effect(() => {
    window.addEventListener('mousemove', handleMouseMove);
    return () => window.removeEventListener('mousemove', handleMouseMove);
  });

  // Nebula rotation refs
  let nebula1Rot = $state(0);
  let nebula2Rot = $state(0);
  let nebula3Rot = $state(0);

  // Star material color (reactive to phase)
  let starColor = $derived(phaseColor(phase));

  // Animation loop
  useTask((delta) => {
    cameraAngle += 0.02 * delta;
    // Lerp mouse parallax
    cameraX += (cameraTargetX - cameraX) * 0.03;
    cameraY += (cameraTargetY - cameraY) * 0.03;

    // Rotate nebulas slowly
    nebula1Rot += 0.003 * delta;
    nebula2Rot -= 0.002 * delta;
    nebula3Rot += 0.001 * delta;

    // Keep camera looking at origin
    if (cameraRef) {
      cameraRef.lookAt(0, 0, 0);
    }
  });

  // Camera position derived from angle + parallax
  let camPosX = $derived(Math.sin(cameraAngle) * 5 + cameraX);
  let camPosY = $derived(cameraY + 1);
  let camPosZ = $derived(Math.cos(cameraAngle) * 5 + 10);
</script>

<!-- Camera -->
<T.PerspectiveCamera
  makeDefault
  position.x={camPosX}
  position.y={camPosY}
  position.z={camPosZ}
  fov={60}
  near={0.1}
  far={200}
  oncreate={({ ref }: { ref: THREE.PerspectiveCamera }) => { cameraRef = ref; ref.lookAt(0, 0, 0); }}
/>

<!-- Stars -->
<T.Points geometry={starGeometry}>
  <T.PointsMaterial
    color={starColor}
    size={1.2}
    sizeAttenuation={true}
    transparent={true}
    opacity={0.8}
    blending={THREE.AdditiveBlending}
    depthWrite={false}
  />
</T.Points>

<!-- Nebula sprites -->
<T.Mesh
  position.x={-15}
  position.y={5}
  position.z={-30}
  rotation.z={nebula1Rot}
>
  <T.PlaneGeometry args={[40, 40]} />
  <T.MeshBasicMaterial
    map={nebulaTex1}
    transparent={true}
    blending={THREE.AdditiveBlending}
    depthWrite={false}
    side={THREE.DoubleSide}
  />
</T.Mesh>

<T.Mesh
  position.x={20}
  position.y={-8}
  position.z={-25}
  rotation.z={nebula2Rot}
>
  <T.PlaneGeometry args={[35, 35]} />
  <T.MeshBasicMaterial
    map={nebulaTex2}
    transparent={true}
    blending={THREE.AdditiveBlending}
    depthWrite={false}
    side={THREE.DoubleSide}
  />
</T.Mesh>

<T.Mesh
  position.x={5}
  position.y={12}
  position.z={-35}
  rotation.z={nebula3Rot}
>
  <T.PlaneGeometry args={[30, 30]} />
  <T.MeshBasicMaterial
    map={nebulaTex3}
    transparent={true}
    blending={THREE.AdditiveBlending}
    depthWrite={false}
    side={THREE.DoubleSide}
  />
</T.Mesh>
