<script lang="ts">
  /**
   * Global LiveTicker — dense KPI info-bar.
   *
   * Renders eleven scrolling cells (ACTIVE, QUEUE, TOK·7D, BACKPRESSURE,
   * DISK·FREE, MEM, LOAD, UPTIME, NEXT TRIGGER, VERDICTS·7D, MODELS) that
   * mirror `dashboard/frontend/design-drafts/dispatch-board.html` and the
   * `STATION_TICKER` array in `pro.js`. Each cell renders as
   * `<span>LABEL <b class="<color>">VALUE</b></span>` where ``color`` is one
   * of ``''`` (default ink), ``go`` (green), ``am`` (amber), ``rd`` (red).
   *
   * Polls four endpoints on a 10s cadence (paused while the tab is hidden):
   *   - GET /api/runs/telemetry-summary  → active, queue, tokens_7d, verdicts_7d
   *   - GET /api/queue/pressure          → backpressure level
   *   - GET /api/system/status           → disk, mem, load, uptime, next_trigger
   *   - GET /api/config                  → models map
   *
   * The ticker track HTML is duplicated (matching `pro.js`'s `html + html`
   * trick) so the CSS keyframe scroll loops seamlessly. The CSS rules for
   * `.ticker`, `.ticker-tag`, `.ticker-track` and the `b.go/.am/.rd` color
   * tokens already live in `lib/design/pro.css` and are not changed here.
   */
  import {
    getTelemetrySummary,
    getBackpressure,
    getSystemStatus,
    getConfig,
  } from '../../lib/api';
  import type {
    TelemetrySummary,
    BackpressureStatus,
    SystemStatus,
    StationConfig,
  } from '../../lib/types';

  type Color = '' | 'go' | 'am' | 'rd';
  type Cell = readonly [string, string, Color];

  let telemetry = $state<TelemetrySummary | null>(null);
  let pressure = $state<BackpressureStatus | null>(null);
  let system = $state<SystemStatus | null>(null);
  let config = $state<StationConfig | null>(null);

  // ── Formatters ──────────────────────────────────────────
  function fmtTok(n: number | null | undefined): string {
    if (n == null) return '—';
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(2)}K`;
    return String(n);
  }

  function fmtUptime(seconds: number | null | undefined): string {
    if (seconds == null || !Number.isFinite(seconds)) return '—';
    const days = Math.floor(seconds / 86_400);
    const hours = Math.floor((seconds % 86_400) / 3_600);
    return `${days}d ${String(hours).padStart(2, '0')}h`;
  }

  function fmtModel(name: string | undefined): string {
    if (!name) return '—';
    // ``claude-opus-4-7`` → ``OPUS-4-7``; strip a leading vendor prefix and
    // upper-case the rest. Hyphens are preserved by design.
    return name.replace(/^claude-/i, '').toUpperCase();
  }

  function pressureColor(level: string | undefined): Color {
    switch ((level ?? '').toUpperCase()) {
      case 'GREEN': return 'go';
      case 'YELLOW': return 'am';
      case 'RED':
      case 'BLACK': return 'rd';
      default: return '';
    }
  }

  function diskColor(free: number | null | undefined): Color {
    if (free == null) return '';
    if (free < 1) return 'rd';
    if (free < 5) return 'am';
    return '';
  }

  function memColor(pct: number | null | undefined): Color {
    if (pct == null) return '';
    if (pct > 90) return 'rd';
    if (pct > 70) return 'am';
    return '';
  }

  // ── Cells ──────────────────────────────────────────────
  let cells = $derived.by<Cell[]>(() => {
    // 1. ACTIVE
    const activeCount = telemetry?.active.count ?? 0;
    const activeCell: Cell = ['ACTIVE', String(activeCount), activeCount > 0 ? 'go' : ''];

    // 2. QUEUE
    const queueCell: Cell = ['QUEUE', String(telemetry?.queue.total ?? 0), ''];

    // 3. TOK·7D
    const tokCell: Cell = ['TOK·7D', fmtTok(telemetry?.tokens_7d.total ?? null), ''];

    // 4. BACKPRESSURE
    const lvl = pressure?.level ?? '—';
    const bpCell: Cell = ['BACKPRESSURE', String(lvl).toUpperCase(), pressureColor(lvl)];

    // 5. DISK·FREE
    const diskFree = system?.resources.disk_free_gb;
    const diskTotal = system?.resources.disk_total_gb;
    const diskValue = diskFree != null && diskTotal != null
      ? `${Math.round(diskFree)}G / ${Math.round(diskTotal)}G`
      : '—';
    const diskCell: Cell = ['DISK·FREE', diskValue, diskColor(diskFree)];

    // 6. MEM
    const memUsedMb = system?.resources.memory_used_mb;
    const memTotalMb = system?.resources.memory_total_mb;
    const memPct = telemetry?.system.memory_used_pct
      ?? (memUsedMb != null && memTotalMb ? Math.round((100 * memUsedMb) / memTotalMb) : null);
    const memValue = memUsedMb != null && memTotalMb != null && memPct != null
      ? `${(memUsedMb / 1024).toFixed(1)}G / ${(memTotalMb / 1024).toFixed(1)}G · ${memPct}%`
      : '—';
    const memCell: Cell = ['MEM', memValue, memColor(memPct)];

    // 7. LOAD — system.resources.load_avg is a [1m, 5m, 15m] tuple from
    //   /proc/loadavg (see services/systemd.py). If the field is absent
    //   (e.g. non-Linux dev box without /proc), fall back to ``—``.
    //   TODO: cross-platform fallback via ``os.getloadavg()`` if we ever
    //   ship to a non-Linux runtime.
    const load = system?.resources.load_avg;
    const loadValue = Array.isArray(load) && load.length >= 3
      ? `${load[0].toFixed(2)} · ${load[1].toFixed(2)} · ${load[2].toFixed(2)}`
      : '—';
    const loadCell: Cell = ['LOAD', loadValue, ''];

    // 8. UPTIME
    const uptimeCell: Cell = [
      'UPTIME',
      fmtUptime(system?.resources.uptime_seconds ?? telemetry?.system.uptime_secs ?? null),
      '',
    ];

    // 9. NEXT TRIGGER
    //   ``/api/system/status`` exposes ``timer.next_trigger`` as a free-form
    //   string from systemd (e.g. ``Sat 2026-05-10 18:45:00 UTC``). When the
    //   timer is inactive or the deploy mode doesn't expose it, render ``—``.
    //   TODO: parse cron from /api/config when running under compose mode so
    //   this cell stays useful outside systemd. Skipped for now to keep the
    //   ticker dependency-free.
    const nextTrigger = system?.timer?.next_trigger?.trim();
    const nextCell: Cell = [
      'NEXT TRIGGER',
      nextTrigger && nextTrigger !== '' ? nextTrigger : '—',
      '',
    ];

    // 10. VERDICTS·7D
    const v = telemetry?.verdicts_7d;
    const verdictsValue = v
      ? `${v.ok} OK / ${v.pr} PR / ${v.x} ✗`
      : '0 OK / 0 PR / 0 ✗';
    const verdictsCell: Cell = ['VERDICTS·7D', verdictsValue, ''];

    // 11. MODELS
    const modelsValue = config?.models
      ? `${fmtModel(config.models.employee)} · ${fmtModel(config.models.manager)}`
      : '—';
    const modelsCell: Cell = ['MODELS', modelsValue, ''];

    return [
      activeCell, queueCell, tokCell, bpCell, diskCell, memCell, loadCell,
      uptimeCell, nextCell, verdictsCell, modelsCell,
    ];
  });

  // The ticker animation depends on the track being twice as long as one
  // copy (the `@keyframes tick-scroll` translates by -50%). Duplicating the
  // cells matches `renderTicker()`'s ``html + html`` trick.
  let loopedCells = $derived([...cells, ...cells]);

  // ── Polling ────────────────────────────────────────────
  async function load() {
    // Run all four fetches in parallel; tolerate individual failures so the
    // ticker keeps showing last-good values for the cells that still respond.
    const [tel, prs, sys, cfg] = await Promise.allSettled([
      getTelemetrySummary(),
      getBackpressure(),
      getSystemStatus(),
      getConfig(),
    ]);
    if (tel.status === 'fulfilled') telemetry = tel.value;
    if (prs.status === 'fulfilled') pressure = prs.value;
    if (sys.status === 'fulfilled') system = sys.value;
    if (cfg.status === 'fulfilled') config = cfg.value;
  }

  $effect(() => {
    load();
    const isHidden = () =>
      typeof document !== 'undefined' && document.visibilityState === 'hidden';
    const t = setInterval(() => { if (!isHidden()) load(); }, 10_000);
    const onVis = () => { if (!isHidden()) load(); };
    document.addEventListener('visibilitychange', onVis);
    return () => {
      clearInterval(t);
      document.removeEventListener('visibilitychange', onVis);
    };
  });
</script>

<div class="ticker" aria-label="Station KPIs">
  <span class="ticker-tag">// Live</span>
  <div class="ticker-track">
    {#each loopedCells as [label, value, color]}
      <span>{label} <b class={color}>{value}</b></span>
    {/each}
  </div>
</div>
