/**
 * Pro flap renderer — the single-character split-flap intro animation
 * shared across the Pro design drafts. Mounts a `.flap` span with one
 * child `<span>` per character, each with a staggered animation-delay.
 *
 * Use as a Svelte action:
 *
 *     <span use:flap={{ text: 'HELLO', baseDelay: 80 }}></span>
 *
 * Or as a plain helper that returns a Node, mirroring the drafts API:
 *
 *     el.appendChild(flapNode('HELLO', 80));
 *
 * Disabled when the user prefers reduced motion: characters render
 * immediately with no transform animation.
 */

const REDUCED_MOTION = typeof window !== 'undefined'
  && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

export interface FlapOptions {
  text: string;
  baseDelay?: number;
  charSpacingMs?: number;
}

/** Build the inner DOM (children of a `.flap` host) for a flap animation. */
function buildChars(host: HTMLElement, text: string, baseDelay: number, charSpacingMs: number) {
  host.classList.add('flap');
  host.textContent = '';
  for (let i = 0; i < text.length; i++) {
    const s = document.createElement('span');
    s.textContent = text[i] === ' ' ? ' ' : text[i];
    if (!REDUCED_MOTION) {
      s.style.animationDelay = `${baseDelay + i * charSpacingMs}ms`;
    }
    host.appendChild(s);
  }
}

/**
 * Svelte action: re-renders flap chars whenever the parameter changes.
 *
 *     <span use:flap={{ text: '03', baseDelay: 60 }}></span>
 */
export function flap(node: HTMLElement, params: FlapOptions) {
  let { text, baseDelay = 0, charSpacingMs = 18 } = params;
  buildChars(node, text, baseDelay, charSpacingMs);
  return {
    update(next: FlapOptions) {
      if (next.text === text && (next.baseDelay ?? 0) === baseDelay) return;
      text = next.text;
      baseDelay = next.baseDelay ?? 0;
      charSpacingMs = next.charSpacingMs ?? 18;
      buildChars(node, text, baseDelay, charSpacingMs);
    },
  };
}

/** Imperative helper mirroring the drafts' `flap()` — returns a new element. */
export function flapNode(text: string, baseDelay = 0, charSpacingMs = 18): HTMLElement {
  const wrap = document.createElement('span');
  buildChars(wrap, text, baseDelay, charSpacingMs);
  return wrap;
}
