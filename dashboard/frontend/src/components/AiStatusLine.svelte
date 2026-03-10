<script lang="ts">
  interface Props {
    messages: string[];
    speed?: number;
  }

  let { messages, speed = 50 }: Props = $props();

  let displayed = $state('');
  let cursorVisible = $state(true);

  $effect(() => {
    if (messages.length === 0) return;
    let msgIndex = 0;
    let charIndex = 0;
    let phase: 'typing' | 'pause' | 'clearing' = 'typing';
    let timeout: ReturnType<typeof setTimeout>;

    const isLastMsg = () => msgIndex === messages.length - 1;

    function tick() {
      if (phase === 'typing') {
        const msg = messages[msgIndex];
        if (charIndex <= msg.length) {
          displayed = msg.slice(0, charIndex);
          charIndex++;
          timeout = setTimeout(tick, speed);
        } else if (isLastMsg()) {
          // Last message — stop here, just blink cursor
          return;
        } else {
          phase = 'pause';
          timeout = setTimeout(tick, 2000);
        }
      } else if (phase === 'pause') {
        phase = 'clearing';
        timeout = setTimeout(tick, speed * 0.5);
      } else {
        if (displayed.length > 0) {
          displayed = displayed.slice(0, -1);
          timeout = setTimeout(tick, speed * 0.3);
        } else {
          msgIndex++;
          charIndex = 0;
          phase = 'typing';
          timeout = setTimeout(tick, 400);
        }
      }
    }

    tick();

    const cursorInterval = setInterval(() => {
      cursorVisible = !cursorVisible;
    }, 530);

    return () => {
      clearTimeout(timeout);
      clearInterval(cursorInterval);
    };
  });
</script>

<span class="ai-text inline-flex items-center gap-0.5">
  <span>{displayed}</span>
  <span class="inline-block w-[1px] h-3 bg-accent-cyan" style:opacity={cursorVisible ? 1 : 0}></span>
</span>
