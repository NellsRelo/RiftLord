<script>
  // Copies one door's waves from a door on another map (or another door here): every
  // wave, or one wave into a wave of this door. Only doors of the same type are offered:
  // a flyer route takes Hellbats, a walking route takes everything else. The pickers
  // fold away once chosen; maps are read ahead (api.prefetchMaps), so they open at once.
  import { mapDetailKept, onslaughtKept, prefetchMaps } from './api.js'
  import { doorType, doorsMatch } from './spec.js'

  // waves: how many waves the onslaught being edited has
  let { groups, detail, door, waves = 1, onpick, onclose } = $props()

  //: the chosen map: {name, title, doors, waves (its onslaught's wave count)}
  let source = $state(null)
  //: the chosen door of that map
  let from = $state(null)
  //: the wave copied: 'all', or a 0-based wave of the source
  let wave = $state('all')
  //: the wave of this door a single wave goes into (0-based)
  let into = $state(0)
  //: which picker is open: 'map', 'door', 'wave' or null
  let open = $state('map')
  let busy = $state(false)
  let error = $state(null)

  const TYPE_LABEL = { fly: 'flyer', ground: 'ground', both: 'flyer and ground' }

  $effect(() => { prefetchMaps(groups.flatMap(([, entries]) => entries.map((m) => m.name))) })

  async function chooseMap(name, title) {
    busy = true
    error = null
    from = null
    wave = 'all'
    try {
      const [d, got] = await Promise.all([
        name === detail.map ? detail : mapDetailKept(name), onslaughtKept(name)])
      source = { name, title: d.title ?? title, waves: got.waves.length,
                 doors: d.doors.filter((x) =>
                   doorsMatch(x, door) && !(name === detail.map && x.key === door.key)) }
      open = 'door'
    } catch (e) {
      error = e.message
    } finally {
      busy = false
    }
  }

  function chooseDoor(d) {
    from = d
    open = 'wave'
  }

  function chooseWave(w) {
    wave = w
    if (w !== 'all') into = Math.min(w, waves)
    open = null
  }

  async function copy() {
    busy = true
    error = null
    try {
      const got = await onslaughtKept(source.name)
      const rowsOf = (w) => from.spawners.flatMap((i) => w.spawns?.[String(i)] ?? [])
      const label = `${from.label} of ${source.title}`
      if (wave === 'all') onpick(got.waves.map(rowsOf), label, null)
      else onpick(rowsOf(got.waves[wave] ?? {}), label, { wave, into })
    } catch (e) {
      error = e.message
    } finally {
      busy = false
    }
  }

  const toggle = (which) => { open = open === which ? null : which }
</script>

<div class="scrim" onclick={onclose} role="presentation"></div>
<div class="menu" role="dialog" aria-label="Copy door waves">
  <p>Copy into {door.label}</p>

  <button class="fold" class:on={open === 'map'} onclick={() => toggle('map')}
          aria-expanded={open === 'map'} data-pick="map">
    <span class="k">Map</span><span class="v">{source?.title ?? 'Choose'}</span>
    <span class="twist" aria-hidden="true">▸</span>
  </button>
  {#if open === 'map'}
    <div class="maps" role="listbox" tabindex="-1">
      {#each groups as [campaign, entries]}
        <div class="group">{campaign}</div>
        {#each entries as m}
          <button class="pick" role="option" aria-selected={source?.name === m.name}
                  disabled={busy} onclick={() => chooseMap(m.name, m.title)}>{m.title}</button>
        {/each}
      {/each}
    </div>
  {/if}

  {#if source}
    <button class="fold" class:on={open === 'door'} onclick={() => toggle('door')}
            aria-expanded={open === 'door'} data-pick="door">
      <span class="k">Door</span><span class="v">{from?.label ?? 'Choose'}</span>
      <span class="twist" aria-hidden="true">▸</span>
    </button>
    {#if open === 'door'}
      <div class="maps" role="listbox" tabindex="-1">
        {#each source.doors as d (d.key)}
          <button class="pick" role="option" aria-selected={from?.key === d.key}
                  onclick={() => chooseDoor(d)}>{d.label}</button>
        {:else}
          <div class="none">No {TYPE_LABEL[doorType(door)]} door on this map.</div>
        {/each}
      </div>
    {/if}
  {/if}

  {#if from}
    <button class="fold" class:on={open === 'wave'} onclick={() => toggle('wave')}
            aria-expanded={open === 'wave'} data-pick="wave">
      <span class="k">Wave</span>
      <span class="v">{wave === 'all' ? 'All waves' : `Wave ${wave + 1}`}</span>
      <span class="twist" aria-hidden="true">▸</span>
    </button>
    {#if open === 'wave'}
      <div class="maps" role="listbox" tabindex="-1">
        <button class="pick" role="option" aria-selected={wave === 'all'}
                onclick={() => chooseWave('all')}>All waves</button>
        {#each Array.from({ length: source.waves }, (_x, i) => i) as i}
          <button class="pick" role="option" aria-selected={wave === i}
                  onclick={() => chooseWave(i)}>Wave {i + 1}</button>
        {/each}
      </div>
    {/if}
    {#if wave !== 'all'}
      <label class="into"><span class="k">Into</span>
        <select bind:value={into} aria-label="Into wave">
          {#each Array.from({ length: waves + 1 }, (_x, i) => i) as i}
            <option value={i}>Wave {i + 1}{i === waves ? ' (new)' : ''}</option>
          {/each}
        </select></label>
    {/if}
    <button class="go" onclick={copy} disabled={busy}>Copy</button>
  {/if}
  {#if busy}<p class="note">Reading…</p>{/if}
  {#if error}<p class="note err">{error}</p>{/if}
</div>

<style>
  .scrim { position: fixed; inset: 0; z-index: 8; }
  .menu {
    position: absolute; top: calc(100% + 5px); right: 0; z-index: 9; width: 17rem;
    padding: 9px; background: var(--panel); border: 1px solid var(--edge);
    border-radius: 6px; box-shadow: 0 10px 28px rgba(0, 0, 0, 0.35);
    display: flex; flex-direction: column; gap: 5px;
  }
  p {
    margin: 0 0 2px; font-size: 11px; color: var(--text-dim);
    text-transform: uppercase; letter-spacing: 0.06em;
  }
  .note { margin: 2px 0 0; text-transform: none; letter-spacing: 0; }
  .err { color: var(--bad); }
  .fold {
    display: flex; align-items: center; gap: 8px; width: 100%; text-align: left;
    padding: 5px 8px; font-size: 12.5px; background: var(--bg); border: 1px solid var(--edge);
    border-radius: 4px; color: var(--text);
  }
  .fold .k, .into .k { width: 3rem; color: var(--text-dim); font-size: 11px;
                       text-transform: uppercase; letter-spacing: 0.06em; }
  .fold .v { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .twist { color: var(--text-dim); font-size: 10px; transition: transform 120ms; }
  .fold.on .twist { transform: rotate(90deg); }
  .maps {
    max-height: 15rem; overflow-y: auto; border: 1px solid var(--edge);
    border-radius: 4px; background: var(--bg);
  }
  .group {
    padding: 4px 9px; background: var(--raised); color: var(--text-dim);
    font-size: 11px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.06em; position: sticky; top: 0;
  }
  .pick {
    display: block; width: 100%; text-align: left; padding: 4px 9px 4px 16px;
    font-size: 12.5px; background: none; border: 0; border-radius: 0; color: var(--text);
  }
  .pick:hover { background: #2f6fd0; color: #fff; }
  .pick[aria-selected='true'] { color: var(--accent-ink, var(--accent)); font-weight: 600; }
  .none { padding: 6px 9px; font-size: 12px; color: var(--text-dim); }
  .into { display: flex; align-items: center; gap: 8px; padding: 0 8px; font-size: 12.5px; }
  .into select { flex: 1; min-width: 0; }
  .go { align-self: flex-end; font-size: 12px; padding: 3px 12px; background: var(--accent);
        border-color: var(--accent); color: var(--on-accent, #fff); }
  .go:disabled { opacity: 0.5; }
</style>
