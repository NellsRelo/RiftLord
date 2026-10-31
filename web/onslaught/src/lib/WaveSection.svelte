<script>
  // One wave's spawns for the door on the current tab.
  import SpawnRow from './SpawnRow.svelte'
  import { newSpawn, spawnsFor, doorTotal, insertSpawn, clearDoorWave, copyDoorWave } from './spec.js'
  import { drag, drop } from './drag.svelte.js'

  // `early`: the current door's entry from endlessDoors, or null; `emptyLabel`: the header's
  // text for a wave with no rows, in place of its minion count; `copyPrevious`: the header
  // has Copy Previous Wave (from wave 2 on)
  let { wave, index, door, units, spec, early = null, emptyLabel = null, copyPrevious = false,
        onremovewave } = $props()

  let spawners = $derived(door?.spawners ?? [])
  let total = $derived(doorTotal(wave, spawners))
  let open = $state(true)
  //: this wave's rows on this door that send only from Endless loop 2, outside loop 1's doors
  let earlyHere = $derived(early ? spawners.flatMap((s) => wave.spawns[s] ?? [])
    .filter((r) => early.early.has(r.id)).length : 0)
  //: the doors that send in this wave on loop 1, where Reset door moves those rows
  let intoHere = $derived((early?.into?.[index + 1] ?? []).join(' and '))

  const add = (spawner) => spawnsFor(wave, spawner).push(newSpawn())

  // A drop lands before the row it is over, or at the end when the gap below
  // the list is the target.
  const over = (spawner, at) => (e) => {
    if (!drag.from) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    drag.over = { waveId: wave.id, spawner, at }
  }
  const land = (spawner, at) => (e) => {
    if (!drag.from) return
    e.preventDefault()
    e.stopPropagation()
    drop(spec, wave.id, spawner, at)
  }
  const marked = (spawner, at) =>
    drag.over?.waveId === wave.id && drag.over?.spawner === spawner
    && drag.over?.at === at
  const remove = (spawner, id) => {
    wave.spawns[spawner] = wave.spawns[spawner].filter((s) => s.id !== id)
  }
  //: true when this door sends something in this wave
  let filled = $derived(spawners.some((s) => (wave.spawns[s] ?? []).length))
  //: the wave before this one, and whether this door sends something in it
  let previous = $derived(index > 0 ? spec.waves[index - 1] : null)
  let previousFilled = $derived(!!previous && spawners.some((s) => (previous.spawns[s] ?? []).length))

  /** Replace this door's rows in this wave with copies of the previous wave's. */
  function takePrevious() {
    for (const s of spawners) {
      copyDoorWave(spec, s, structuredClone($state.snapshot(previous.spawns[s] ?? [])), index)
    }
  }
</script>

<section id={`wave-${wave.id}`}>
  <header onclick={() => (open = !open)} role="button" tabindex="0"
          onkeydown={(e) => e.key === 'Enter' && (open = !open)}>
    <span class="twist" class:open aria-hidden="true">▸</span>
    <h3>Wave {index + 1}</h3>
    {#if emptyLabel && !filled}
      <span class="count empty">{emptyLabel}</span>
    {:else}
      <span class="count">{total} {total === 1 ? 'minion' : 'minions'}</span>
    {/if}
    <span class="grow"></span>
    {#if copyPrevious && index > 0}
      <button class="ghost clear" disabled={!previousFilled}
              onclick={(e) => { e.stopPropagation(); takePrevious() }}
              title="Replace wave {index + 1}'s rows with wave {index}'s"
              aria-label="Copy wave {index} into wave {index + 1}">Copy Previous Wave</button>
    {/if}
    <button class="ghost clear" disabled={!filled}
            onclick={(e) => { e.stopPropagation(); clearDoorWave(wave, spawners) }}
            title="Remove every row {door?.label ?? 'this door'} has in wave {index + 1}"
            aria-label="Clear wave {index + 1}">Clear</button>
    <button class="ghost" onclick={(e) => { e.stopPropagation(); onremovewave() }}
            aria-label="Remove wave {index + 1}">✕</button>
  </header>

  {#if open}
  {#if earlyHere}
    <p class="endless" role="note">
      {#if spec.resetDoors}
        {earlyHere === 1 ? 'This Endless row goes' : `These ${earlyHere} Endless rows go`}
        through {intoHere} instead when the mission is built (Reset door after Onslaught
        Loop): loop 1 sends nothing through {door.label} in this wave.
      {:else}
        {earlyHere === 1 ? 'This Endless row sends' : `These ${earlyHere} Endless rows send`}
        units through {door.label} in this wave from Endless loop 2 on; loop 1 sends
        nothing through it here.
      {/if}
    </p>
  {/if}

  {#each spawners as spawner}
    {#if spawners.length > 1}<div class="sub">Spawner {spawner}</div>{/if}
    <div class="rows" role="list"
         ondragover={over(spawner, -1)} ondrop={land(spawner, -1)}>
      {#each wave.spawns[spawner] ?? [] as spawn, k (spawn.id)}
        <div role="listitem" class:marked={marked(spawner, k)}
             ondragover={over(spawner, k)} ondrop={land(spawner, k)}>
          <SpawnRow {spawn} {units} pathing={door?.pathing} early={!!early?.early.has(spawn.id)}
                    waveId={wave.id} {spawner} first={k === 0}
                    onremove={() => remove(spawner, spawn.id)}
                    oninsert={() => insertSpawn(wave, spawner, k)} />
        </div>
      {/each}
      <div class="tail" class:marked={marked(spawner, -1)}
           class:live={!!drag.from}></div>
    </div>
    <button class="ghost add" onclick={() => add(spawner)}>+ Add spawn</button>
  {/each}
  {/if}
</section>

<style>
  section {
    border: 1px solid var(--edge);
    border-radius: 6px;
    background: var(--panel);
    margin-bottom: 12px;
    scroll-margin-top: 8px;
  }
  header {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 7px 10px;
    cursor: pointer;
    user-select: none;
    background: var(--panel-2);
    border-bottom: 1px solid var(--edge);
    border-radius: 6px 6px 0 0;
  }
  h3 { font-size: 14px; }
  .twist {
    color: var(--text-dim);
    font-size: 11px;
    transition: transform 120ms;
    display: inline-block;
  }
  .twist.open { transform: rotate(90deg); }
  .count { color: var(--text-dim); font-size: 12px; }
  .count.empty { color: var(--warn); }
  .grow { flex: 1; }
  .rows { padding: 4px; }
  /* where the row would land */
  [role='listitem'].marked { box-shadow: inset 0 2px 0 var(--accent); }
  .tail { height: 0; transition: height 90ms; }
  .tail.live { height: 14px; border-radius: 4px; }
  .tail.marked { background: var(--panel-2); box-shadow: inset 0 2px 0 var(--accent); }
  .sub {
    padding: 6px 10px 0;
    color: var(--text-dim);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .add { margin: 0 4px 8px 7px; font-size: 12px; }
  .clear { font-size: 12px; padding: 2px 8px; }
  .endless {
    margin: 6px 10px 0;
    padding: 4px 8px;
    font-size: 12px;
    color: var(--text);
    border-left: 3px solid var(--warn);
    background: var(--panel-2);
    border-radius: 0 4px 4px 0;
  }
</style>
