<script lang="ts">
  import { lerpColor } from '../../lib/chart-utils';

  let {
    rows = [],
    columns = [],
    data = [],
    format = (v: number) => `${Math.round(v * 100)}%`,
    lowColor = '#1e293b',
    highColor = '#22c55e',
  }: {
    rows: string[];
    columns: string[];
    data: number[][];  // rows x columns, values 0-1
    format?: (v: number) => string;
    lowColor?: string;
    highColor?: string;
  } = $props();

  function cellColor(v: number): string {
    return lerpColor(lowColor, highColor, Math.max(0, Math.min(1, v)));
  }
</script>

<div class="overflow-x-auto">
  <table class="text-xs">
    <thead>
      <tr>
        <th class="px-2 py-1.5 text-left text-text-muted font-normal"></th>
        {#each columns as col}
          <th class="px-2 py-1.5 text-center text-text-muted font-normal whitespace-nowrap">{col}</th>
        {/each}
      </tr>
    </thead>
    <tbody>
      {#each rows as row, ri}
        <tr>
          <td class="px-2 py-1 text-text-dim whitespace-nowrap">{row}</td>
          {#each columns as _, ci}
            {@const val = data[ri]?.[ci] ?? 0}
            <td class="px-1 py-1">
              <div
                class="w-10 h-7 rounded flex items-center justify-center data-readout text-[10px]"
                style="background:{cellColor(val)}; color: {val > 0.5 ? '#000' : 'var(--color-text-dim)'}"
                title="{row} / {columns[ci]}: {format(val)}"
              >
                {format(val)}
              </div>
            </td>
          {/each}
        </tr>
      {/each}
    </tbody>
  </table>
</div>
