<script>
  // A Rift Group's tab (riftlord/mapeditor/rifttear.py): when it opens (its lock and its
  // triggers, each a sentence: Trigger.svelte), its Unstable Rifts with their locks, then its
  // enemy rows wave by wave with a spawn row's controls and Copy Previous Wave (WaveSection
  // on groupKey(gi)). Each rift's pick chance per wave is on the Timeline. Each opened rift
  // sends the wave's rows; a wave with none is one the group does not open in.
  import WaveSection from './WaveSection.svelte'
  import Trigger from './Trigger.svelte'
  import { DIFFICULTY, groupKey } from './spec.js'

  //: `riftName(spawner)`: an Unstable Rift's name as the map shows it
  let { group, gi, spec, units, riftName, onremovewave } = $props()

  //: rifttear.PLAYERS
  const PLAYERS = { 0: 'Any', 1: 'Solo', 2: 'Co-op' }

  //: the door the wave sections show: the group's rows, which walk
  let door = $derived({ key: `group-${gi}`, label: group.name, spawners: [groupKey(gi)],
                        pathing: 'walk', kind: 'group' })

  function addTrigger() {
    group.triggers = [...(group.triggers ?? []), { at: 75, wave: null, alive: 0, both: false,
                                                   open: 1, launchAfter: 10, launchAlive: 0,
                                                   repeat: 0 }]
  }

  let opensOpen = $state(true)
  let riftsOpen = $state(true)
</script>

<div class="group">
  <section>
    <header onclick={() => (opensOpen = !opensOpen)} role="button" tabindex="0"
            onkeydown={(e) => e.key === 'Enter' && (opensOpen = !opensOpen)}>
      <span class="twist" class:open={opensOpen} aria-hidden="true">▸</span>
      <h3>Opens</h3>
    </header>
    {#if opensOpen}
      <div class="row lock">
        <label><span>Difficulty</span>
          <select bind:value={group.difficulty} aria-label="Group difficulty">
            {#each Object.entries(DIFFICULTY) as [value, text]}
              <option value={Number(value)}>{text}</option>
            {/each}
          </select></label>
        <label><span>Players</span>
          <select bind:value={group.players} aria-label="Group players">
            {#each Object.entries(PLAYERS) as [value, text]}
              <option value={Number(value)}>{text}</option>
            {/each}
          </select></label>
      </div>
      <div class="rows">
        {#each group.triggers ?? [] as t, ti (ti)}
          <Trigger {t} rifts={(group.rifts ?? []).length} waves={spec.waves.length}
                   onremove={() => (group.triggers = group.triggers.filter((_x, k) => k !== ti))} />
        {/each}
      </div>
      <button class="ghost add" onclick={addTrigger}>+ Add trigger</button>
    {/if}
  </section>

  <section>
    <header onclick={() => (riftsOpen = !riftsOpen)} role="button" tabindex="0"
            onkeydown={(e) => e.key === 'Enter' && (riftsOpen = !riftsOpen)}>
      <span class="twist" class:open={riftsOpen} aria-hidden="true">▸</span>
      <h3>Unstable Rifts</h3>
      <span class="count">{(group.rifts ?? []).length}</span>
    </header>
    {#if riftsOpen}
      <div class="rows">
        {#each group.rifts ?? [] as r (r.spawner)}
          <div class="row rift">
            <b>{riftName(r.spawner)}</b>
            <label><span>Difficulty</span>
              <select bind:value={r.difficulty} aria-label="Rift difficulty">
                {#each Object.entries(DIFFICULTY) as [value, text]}
                  <option value={Number(value)}>{text}</option>
                {/each}
              </select></label>
            <label><span>Players</span>
              <select bind:value={r.players} aria-label="Rift players">
                {#each Object.entries(PLAYERS) as [value, text]}
                  <option value={Number(value)}>{text}</option>
                {/each}
              </select></label>
          </div>
        {/each}
      </div>
    {/if}
  </section>

  {#each spec.waves as wave, i (wave.id)}
    <WaveSection {wave} index={i} {door} {units} {spec} emptyLabel="Doesn't open" copyPrevious
                 onremovewave={() => onremovewave(wave.id)} />
  {/each}
</div>

<style>
  .group { padding: 2px 0; }
  section {
    border: 1px solid var(--edge);
    border-radius: 6px;
    background: var(--panel);
    margin-bottom: 12px;
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
  .twist { color: var(--text-dim); font-size: 11px; transition: transform 120ms; display: inline-block; }
  .twist.open { transform: rotate(90deg); }
  .count { color: var(--text-dim); font-size: 12px; }
  .rows { padding: 4px; }
  .add { margin: 0 4px 8px 7px; font-size: 12px; }
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 10px;
    align-items: center;
    padding: 5px 7px;
    min-width: 0;
    border-radius: 4px;
  }
  .row.lock { padding: 8px 11px 2px; }
  .row.rift b { min-width: 9rem; font-weight: 500; }
  label { display: flex; align-items: center; gap: 5px; }
  span { color: var(--text-dim); font-size: 12px; white-space: nowrap; }
  label > select { width: 5.9rem; }
</style>
