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

/** Read prefers-reduced-motion at call time so the flap respects OS-level
 * preference changes within a session. Reading on each `buildChars` call is
 * cheap (single matchMedia lookup) and avoids the complexity of subscribing
 * to a `change` event and tearing it down. The previous module-level
 * constant was captured once at import and never updated. */
function reducedMotion(): boolean {
  return typeof window !== 'undefined'
    && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
}

export interface FlapOptions {
  text: string;
  baseDelay?: number;
  charSpacingMs?: number;
}

/** Build the inner DOM (children of a `.flap` host) for a flap animation.
 *
 * When `animate` is false, the staggered per-character animation is
 * suppressed entirely — used on Svelte action `update()` calls so cell
 * value changes during polling pop in cleanly without re-flapping the
 * full board on every refresh.
 */
function buildChars(
  host: HTMLElement,
  text: string,
  baseDelay: number,
  charSpacingMs: number,
  animate: boolean = true,
) {
  host.classList.add('flap');
  host.textContent = '';
  const skipAnim = !animate || reducedMotion();
  for (let i = 0; i < text.length; i++) {
    const s = document.createElement('span');
    // Non-breaking space — regular spaces collapse to 0 width inside the
    // inline-flex .flap container.
    s.textContent = text[i] === ' ' ? ' ' : text[i];
    if (skipAnim) {
      // Disable the CSS @keyframes flap so polling-driven updates don't
      // re-trigger the intro animation across a 50-row board.
      s.style.animation = 'none';
    } else {
      s.style.animationDelay = `${baseDelay + i * charSpacingMs}ms`;
    }
    host.appendChild(s);
  }
}

/**
 * Svelte action: re-renders flap chars whenever the parameter changes.
 *
 *     <span use:flap={{ text: '03', baseDelay: 60 }}></span>
 *
 * Initial mount runs the staggered flap intro. Subsequent `update()`
 * calls (driven by reactive param changes during polling) rebuild the
 * chars without animation, so values change visibly but don't churn.
 */
export function flap(node: HTMLElement, params: FlapOptions) {
  let { text, baseDelay = 0, charSpacingMs = 18 } = params;
  buildChars(node, text, baseDelay, charSpacingMs, true);
  return {
    update(next: FlapOptions) {
      if (next.text === text && (next.baseDelay ?? 0) === baseDelay) return;
      text = next.text;
      baseDelay = next.baseDelay ?? 0;
      charSpacingMs = next.charSpacingMs ?? 18;
      buildChars(node, text, baseDelay, charSpacingMs, false);
    },
  };
}

/** Imperative helper mirroring the drafts' `flap()` — returns a new element. */
export function flapNode(text: string, baseDelay = 0, charSpacingMs = 18): HTMLElement {
  const wrap = document.createElement('span');
  buildChars(wrap, text, baseDelay, charSpacingMs, true);
  return wrap;
}
