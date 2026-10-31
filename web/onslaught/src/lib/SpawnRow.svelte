<script>
  // One spawn call. The real signature is
  // OnslaughtSpawnEntities(proto, count, bool SpawnWithShield,
  //   int32 EndlessAddCount, float EndlessPromotePercent, EOnslaughtDifficulty)
  // so the float is an endless promotion chance, not a rate or a delay.
  import { DIFFICULTY, sends } from './spec.js'
  import { pick, release, isDragging } from './drag.svelte.js'
  import Pause from './Pause.svelte'

  let { spawn, units, pathing, waveId, spawner, first = false, early = false,
        onremove, oninsert } = $props()

  // The row only becomes draggable once the pointer is on the grip, so a drag
  // cannot start from inside a select or a number field.
  let grabbable = $state(false)

  // A route only flyers can use offers only flyers, and a walking route hides
  // them: no shipped wave mixes the two, and the build refuses it.
  let allowed = $derived(
    units.filter((u) =>
      pathing === 'fly' ? u.flyer : pathing === 'walk' ? !u.flyer : true)
  )

  // shipped families first (a mod's units of a shipped family join it, after the shipped
  // ones), then one group per mod-only family, then Scramble mode
  let groups = $derived.by(() => {
    const live = allowed.filter((u) => !u.scramble)
    const shipped = new Set(live.filter((u) => !u.mod).map((u) => u.family))
    const modOnly = new Set(live.filter((u) => u.mod && !shipped.has(u.family)).map((u) => u.family))
    const out = [...[...shipped].sort(), ...[...modOnly].sort()].map((f) => ({
      family: f, units: [...live.filter((u) => u.family === f && !u.mod),
                         ...live.filter((u) => u.family === f && u.mod)],
    }))
    const scramble = allowed.filter((u) => u.scramble)
    if (scramble.length) out.push({ family: 'Scramble mode', units: scramble })
    return out
  })

  const addAfter = () => (spawn.after = { kind: 'wait', value: 5 })

  let noUnit = $derived(!spawn.unit)
  let badCount = $derived(!sends(spawn))
  let grows = $derived(Number(spawn.addCount) || 0)
</script>

{#if first}
  <Pause bind:pause={spawn.before} label="Insert Delay" />
{/if}

<div class="row" draggable={grabbable} class:dragging={isDragging(spawn.id)} class:early
     ondragstart={(e) => {
       e.dataTransfer.effectAllowed = 'move'
       e.dataTransfer.setData('text/plain', String(spawn.id))
       pick(waveId, spawner, spawn.id)
     }}
     ondragend={() => { grabbable = false; release() }}>
  <span class="grip" title="Drag to reorder, or into another wave"
        onpointerdown={() => (grabbable = true)}
        onpointerup={() => (grabbable = false)}
        aria-hidden="true">
    <svg viewBox="0 0 16 16" width="13" height="13">
      <rect x="2" y="3.2" width="12" height="1.6" rx="0.8" />
      <rect x="2" y="7.2" width="12" height="1.6" rx="0.8" />
      <rect x="2" y="11.2" width="12" height="1.6" rx="0.8" />
    </svg>
  </span>

  <select bind:value={spawn.unit} class:bad={noUnit} aria-label="Minion">
    <option value="">— pick a minion —</option>
    {#each groups as group}
      <optgroup label={group.family}>
        {#each group.units as unit}
          <option value={unit.name}>{unit.label}{unit.boss ? ' ★' : ''}</option>
        {/each}
      </optgroup>
    {/each}
  </select>

  <label title={grows ? `In Endless, ${grows} more each loop` : null}><span>Count</span>
    <input type="number" min={grows ? 0 : 1} max="200" class:bad={badCount}
           bind:value={spawn.count} /></label>

  <span class="gap"></span>

  <label title="EndlessPromotePercent: the chance a spawn is promoted to a tougher unit in Endless. Shipped waves use 0.5 almost everywhere.">
    <span>Promotion %</span>
    <input type="number" min="0" max="1" step="0.05" bind:value={spawn.promote} />
  </label>

  <label title="EOnslaughtDifficulty. A spawn set to one difficulty only appears on that one.">
    <span>Difficulty</span>
    <select bind:value={spawn.difficulty} aria-label="Difficulty">
      {#each Object.entries(DIFFICULTY) as [value, label]}
        <option value={Number(value)}>{label}</option>
      {/each}
    </select>
  </label>

  <!-- kept in the layout when it is not offered, so every row's columns
       line up whether or not it carries a pause -->
  <button class="insert" class:hidden={!!spawn.after} onclick={addAfter}
          tabindex={spawn.after ? -1 : 0} aria-hidden={!!spawn.after}
          title="Insert a delay after this spawn">
      <svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true">
        <path d="M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zm0 2a7 7 0 1 1 0 14 7 7 0 0 1 0-14z" />
        <path d="M11 7h2v5.4l3.3 2-1 1.7L11 13.5z" />
      </svg>
    Insert Delay
  </button>

  <button class="ghost below" onclick={oninsert} title="Insert a spawn below this one"
          aria-label="Insert a spawn below">+</button>

  <button class="ghost del" onclick={onremove} title="Remove this spawn"
          aria-label="Remove this spawn">✕</button>
</div>

<Pause bind:pause={spawn.after} label="Insert Delay" hideAdd />

<style>
  .row {
    display: grid;
    /* minion and count to the left, everything else pushed right by the gap */
    /* fixed tracks, so every row lines up with every other */
    grid-template-columns:
      18px 11rem auto minmax(6px, 1fr) auto auto auto auto auto;
    gap: 6px;
    align-items: center;
    padding: 5px 7px;
    min-width: 0;
    border-radius: 4px;
  }
  .row:hover { background: var(--panel-2); }
  .row.dragging { opacity: 0.4; }
  /* an Endless row that opens its door early (WaveSection explains it) */
  .row.early { box-shadow: inset 3px 0 0 var(--warn); }
  .grip {
    display: flex;
    align-items: center;
    cursor: grab;
    color: var(--text-dim);
    padding: 2px;
  }
  .grip:active { cursor: grabbing; }
  .grip svg { fill: currentColor; }
  .row:hover .grip { color: var(--text); }

  label { display: flex; align-items: center; gap: 5px; }
  span { color: var(--text-dim); font-size: 12px; white-space: nowrap; }
  .row > label > select { width: 5.9rem; }
  input[type='number'] { width: 52px; }
  select { min-width: 0; }
  .del { padding: 2px 7px; color: var(--text-dim); }
  .del:hover { color: var(--bad); }
  .below { padding: 2px 7px; color: var(--text-dim); font-weight: 600; }
  .below:hover { color: var(--accent-ink, var(--text)); }
  .gap { min-width: 0; }
  .insert {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 3px 7px;
    font-size: 11px;
    color: var(--text-dim);
    background: var(--raised);
    border: 1px solid var(--edge);
    border-radius: 4px;
    white-space: nowrap;
  }
  .insert svg { fill: currentColor; }
  .insert.hidden { visibility: hidden; }
  .insert:hover { color: var(--text); border-color: var(--text-dim); }
</style>
