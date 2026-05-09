/* ──────────────────────────────────────────────────────────
   STATION · PRO shared JS — theme toggle, flap renderer,
   ticker, clock, common hotkey scaffold.
   Each page imports this; page-specific JS lives inline.
   ────────────────────────────────────────────────────────── */

/* ── Theme toggle (light ↔ dark) ─────────────────────── */
(function () {
  const root = document.documentElement;
  const stored = localStorage.getItem('station-design-theme');
  const prefersDark = matchMedia('(prefers-color-scheme: dark)').matches;
  apply(stored ?? (prefersDark ? 'dark' : 'light'));

  document.addEventListener('click', (e) => {
    if (!e.target.closest('#theme-toggle')) return;
    const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
    apply(next);
    localStorage.setItem('station-design-theme', next);
  });

  function apply(t) {
    if (t === 'dark') root.dataset.theme = 'dark'; else delete root.dataset.theme;
    const sun = document.getElementById('theme-sun');
    const moon = document.getElementById('theme-moon');
    if (sun)  sun.style.display  = t === 'dark' ? 'block' : 'none';
    if (moon) moon.style.display = t === 'dark' ? 'none'  : 'block';
  }
})();

/* ── Flap renderer ───────────────────────────────────── */
window.flap = function flap(text, baseDelay = 0, charSpacingMs = 18) {
  const wrap = document.createElement('span');
  wrap.className = 'flap';
  for (let i = 0; i < text.length; i++) {
    const s = document.createElement('span');
    s.textContent = text[i];
    s.style.animationDelay = (baseDelay + i * charSpacingMs) + 'ms';
    wrap.appendChild(s);
  }
  return wrap;
};

/* ── Ticker render (real snapshot) ───────────────────── */
window.STATION_TICKER = [
  ['ACTIVE',          '1',                 'go'],
  ['QUEUE',           '3',                 ''  ],
  ['TOK·7D',          '1.75K',             ''  ],
  ['BACKPRESSURE',    'GREEN',             'go'],
  ['DISK·FREE',       '4.9G / 87G',        'am'],
  ['MEM',             '9.7G / 13.6G · 71%','am'],
  ['LOAD',            '2.83 · 1.04 · 0.57',''  ],
  ['UPTIME',          '3D 09H',            ''  ],
  ['NEXT TRIGGER',    '—',                 ''  ],
  ['VERDICTS·7D',     '0 OK / 0 PR / 0 ✗', ''  ],
  ['MODELS',          'OPUS-4-7 · SONNET-4-6', ''],
];
window.renderTicker = function (el) {
  if (!el) return;
  const html = window.STATION_TICKER
    .map(([k,v,c]) => `<span>${k} <b class="${c}">${v}</b></span>`)
    .join('');
  el.innerHTML = html + html;
};

/* ── Clock (call setupClock(el)) ─────────────────────── */
window.pad2 = (n) => String(n).padStart(2, '0');
window.nowStr = () => {
  const d = new Date();
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
};
window.setupClock = function (el) {
  if (!el) return;
  const tick = () => el.textContent = nowStr();
  tick();
  setInterval(tick, 1000);
};

/* ── Common hotkeys (theme = 't', plus pages can extend) ── */
document.addEventListener('keydown', (e) => {
  const tag = e.target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA') return;
  if (e.key === 't') document.getElementById('theme-toggle')?.click();
});
