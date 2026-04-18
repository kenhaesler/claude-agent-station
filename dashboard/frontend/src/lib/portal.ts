export function portal(node: HTMLElement, target: HTMLElement | string = document.body) {
  const resolve = (t: HTMLElement | string): HTMLElement =>
    typeof t === 'string' ? ((document.querySelector(t) as HTMLElement) ?? document.body) : t;
  let host = resolve(target);
  host.appendChild(node);
  return {
    update(newTarget: HTMLElement | string) {
      host = resolve(newTarget);
      host.appendChild(node);
    },
    destroy() {
      if (node.parentNode) node.parentNode.removeChild(node);
    },
  };
}
