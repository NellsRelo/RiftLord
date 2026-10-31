<script>
  // Rift Lord's settings: what a new mission starts with in both editors, and where
  // missions are kept.
  import Icon from './Icon.svelte'
  import { getSettings, saveSettings, setRoot, reveal, supportedMods, openModPage, fabSettings,
           saveFabSettings } from './api.js'
  import { pickFolder } from './native.js'

  let { active } = $props()

  let data = $state(null)
  let defaults = $state({ author: '', version: '', placement: 'CustomMaps', endless: false,
                          hub: 'OldFriends' })
  //: hub scenes OrcsMustUI can show (bucket.DEFAULTS, customcampaign.HUBS); '' lets the level decide
  const HUBS = [
    { value: '', label: 'Automatic (from level)' },
    { value: 'OldFriends', label: 'Old Friends' },
    { value: 'DrasticSteps', label: 'Drastic Steps' },
    { value: 'ColdAsEyes', label: 'Cold as Eyes' },
    { value: 'TippingTheScales', label: 'Tipping the Scales' },
  ]
  let message = $state(null)
  let saving = $state(false)

  async function load() {
    try {
      data = await getSettings()
      defaults = { ...data.defaults }
    } catch (e) {
      message = { tone: 'bad', text: e.message }
    }
  }

  //: Supported Mods (riftlord/core/supportedmods.py state(): the detected ones only, the
  //: section hidden without any), detected again on Refresh
  let mods = $state(null)
  let refreshing = $state(false)

  async function loadMods() {
    refreshing = true
    try {
      mods = await supportedMods()
    } catch (e) {
      message = { tone: 'bad', text: e.message }
    } finally {
      refreshing = false
    }
  }

  async function openPage(id) {
    try {
      await openModPage(id)
    } catch (e) {
      message = { tone: 'bad', text: e.message }
    }
  }

  //: the file a row shows: the pak carrying the mod, from the Paks folder
  const shownFile = (row) => row.files[0]

  //: Fab Kits (riftlord/app/fabkits.py settings_view): the vault folder and the convert limits
  let fab = $state(null)
  //: the limit fields' text: {memoryMb, workers}
  let limits = $state({ memoryMb: '', workers: '' })
  //: how often the vault search is read while it runs, in milliseconds
  const FAB_POLL_MS = 500
  let fabTimer = null
  //: the limit fields' aria-labels, by key of `limits`
  const LIMIT_LABELS = { memoryMb: 'Memory Limit', workers: 'Workers' }

  /** `poll`: the limit field that has focus keeps its text. */
  function takeFab(got, poll = false) {
    fab = got
    const d = got.defaults || {}
    const typing = poll ? document.activeElement?.getAttribute('aria-label') : null
    const next = { memoryMb: String(got.memoryMb ?? d.memory_mb ?? ''),
                   workers: String(got.workers ?? d.workers ?? '') }
    for (const key of Object.keys(next)) if (LIMIT_LABELS[key] === typing) next[key] = limits[key]
    limits = next
    clearTimeout(fabTimer)
    if (got.scanning) fabTimer = setTimeout(() => loadFab(true), FAB_POLL_MS)
  }

  async function loadFab(poll = false) {
    try {
      takeFab(await fabSettings(), poll)
    } catch (e) {
      message = { tone: 'bad', text: e.message }
    }
  }

  async function saveFab(values) {
    try {
      takeFab(await saveFabSettings(values))
    } catch (e) {
      message = { tone: 'bad', text: e.message }
      await loadFab()
    }
  }

  async function changeVault() {
    const shown = fab.vaultRoot || fab.roots?.found?.[0]
    const folder = await pickFolder('Epic Vault', shown)
    if (folder && folder !== fab.vaultRoot) await saveFab({ vaultRoot: folder })
  }

  /** A limit field left: the converter's default stores nothing. */
  function saveLimit(key, defaultKey) {
    const text = String(limits[key]).trim()
    const value = text === '' ? null : Number(text)
    const same = value === null || value === fab.defaults?.[defaultKey]
    if ((same ? null : value) === fab[key]) return
    saveFab({ [key]: same ? null : value })
  }

  let fabRoot = $derived(fab && (fab.vaultRoot || fab.roots?.found?.[0] || null))

  $effect(() => {
    if (!active) return
    load()
    loadMods()
    loadFab()
    return () => clearTimeout(fabTimer)
  })

  // A default campaign has to exist already: "New campaign" needs a name per mission.
  let choices = $derived((data?.choices ?? []).filter((c) => c.kind !== 'new'))
  let chosen = $derived(choices.find((c) => c.name === defaults.placement))
  let changed = $derived(data && JSON.stringify(defaults) !== JSON.stringify(data.defaults))

  async function save() {
    saving = true
    try {
      const got = await saveSettings(defaults)
      data = { ...data, defaults: got.defaults }
      defaults = { ...got.defaults }
      message = { tone: 'ok', text: 'Saved. New missions in both editors start with these.' }
    } catch (e) {
      message = { tone: 'bad', text: e.message }
    } finally {
      saving = false
    }
  }

  async function changeFolder() {
    const folder = await pickFolder('Where should Rift Lord keep your missions?', data.root)
    if (!folder || folder === data.root) return
    if (!confirm(`Keep missions in ${folder}?\n\nIf that folder has no missions yet, the ones here move there. If it already has some, Rift Lord switches to those and leaves these where they are.`)) return
    try {
      const got = await setRoot(folder)
      message = { tone: 'ok', text: got.moved
        ? `Missions are now kept in ${got.root}; moved what was here.`
        : `Missions are now kept in ${got.root}.` }
      await load()
    } catch (e) {
      message = { tone: 'bad', text: e.message }
    }
  }
</script>

<section class="settings">
  <header>
    <h1>Settings</h1>
  </header>

  {#if message}
    <div class="message {message.tone}" role="status">
      <span>{message.text}</span>
      <button class="ghost" onclick={() => (message = null)} aria-label="Dismiss">✕</button>
    </div>
  {/if}

  {#if data}
    <div class="body">
      <section class="group">
        <h2>Mission defaults</h2>
        <p class="hint">What a new mission starts with in the Map Editor and the Onslaught
          Editor. Each mission can still change them, in its editor or in its Mission
          Settings.</p>
        <label><span>Author</span>
          <input bind:value={defaults.author} placeholder="Your name" /></label>
        <label><span>Version</span>
          <input bind:value={defaults.version} placeholder="1.0" /></label>
        <label><span>Campaign</span>
          <select bind:value={defaults.placement}>
            {#each choices as c}
              <option value={c.name}>{c.kind === 'custom' ? `${c.label} (installed)` : c.label}</option>
            {/each}
          </select></label>
        {#if defaults.placement === 'CustomMaps' || chosen?.kind === 'custom'}
          <p class="hint indent">The Custom Maps and New Campaign options require OrcsMustUI to
            be installed for the mission to be reachable.</p>
        {:else if chosen?.kind === 'shipped'}
          <p class="hint indent warn">Listing a mission in a shipped campaign patches the game's
            campaign table. Only one mod that does so can be installed at a time.</p>
        {/if}
        <label><span>Hub Style</span>
          <select bind:value={defaults.hub}>
            {#each HUBS as h}<option value={h.value}>{h.label}</option>{/each}
          </select></label>
        <p class="hint indent">The hub scene behind a new mission in the Custom Maps tab. Needs
          OrcsMustUI.</p>
        <label class="check"><input type="checkbox" bind:checked={defaults.endless} />
          <span>Add to Endless</span></label>
        <div class="actions">
          <button class="primary" onclick={save} disabled={!changed || saving}>
            {saving ? 'Saving…' : 'Save defaults'}
          </button>
        </div>
      </section>

      <section class="group">
        <h2>Missions folder</h2>
        <p class="hint">Where Rift Lord keeps missions, unpacked, until they are installed.</p>
        <div class="folder">
          <code>{data.root}</code>
          <button onclick={changeFolder}>Change…</button>
          <button class="ghost icon" onclick={() => reveal()} title="Open the Missions folder"
                  aria-label="Open the Missions folder"><Icon name="folder" size={16} /></button>
        </div>
      </section>

      <section class="group">
        <h2>Game</h2>
        <p class="hint">The Orcs Must Die 3 install that Install puts missions into.</p>
        <div class="folder"><code>{data.game ?? 'Not found'}</code></div>
      </section>

      {#if fab?.available}
        <section class="group fabkits">
          <h2>Fab Kits</h2>
          <div class="field"><span>Epic Vault</span>
            <div class="folder">
              <code class="vault" title={fabRoot || ''}>{fabRoot ?? (fab.scanning ? 'Searching' : 'Not found')}</code>
              <button onclick={changeVault}>Change…</button>
              {#if fab.vaultRoot}<button class="ghost" onclick={() => saveFab({ vaultRoot: '' })}>Reset</button>{/if}
            </div>
          </div>
          {#if fab.defaults}
            <div class="pair">
              <label><span>Memory Limit</span>
                <input type="number" min={fab.minMemoryMb} step="512" bind:value={limits.memoryMb}
                       aria-label="Memory Limit" onchange={() => saveLimit('memoryMb', 'memory_mb')} />
                <span class="unit">MB</span></label>
              <label><span>Workers</span>
                <input type="number" min="1" max={fab.maxWorkers} step="1" bind:value={limits.workers}
                       aria-label="Workers" onchange={() => saveLimit('workers', 'workers')} /></label>
              {#if fab.memoryMb || fab.workers}
                <button class="ghost reset-limits" onclick={() => saveFab({ memoryMb: null, workers: null })}>Reset</button>
              {/if}
            </div>
          {/if}
        </section>
      {/if}

      {#if mods?.mods.length}
        <section class="group supported">
          <div class="head">
            <h2>Supported Mods</h2>
            <button onclick={loadMods} disabled={refreshing}>Refresh</button>
          </div>
          <ul class="mods" role="listbox" aria-label="Supported Mods">
            {#each mods.mods as row (row.id)}
              <li class="mod {row.status}" role="option" aria-selected="false" data-mod={row.id}>
                <div class="line">
                  <b class="name">{row.name}</b>
                  <span class="status">{row.label}</span>
                  {#if row.link}
                    <button class="ghost page" onclick={() => openPage(row.id)}>Open Page</button>
                  {/if}
                </div>
                <code class="file" title={shownFile(row).full}>Paks/{shownFile(row).path}</code>
                {#if row.missing.length}
                  <details class="missing">
                    <summary>Missing</summary>
                    <ul>{#each row.missing as m}<li>{m}</li>{/each}</ul>
                  </details>
                {/if}
              </li>
            {/each}
          </ul>
        </section>
      {/if}
    </div>
  {/if}
</section>

<style>
  .settings { height: 100%; display: flex; flex-direction: column; min-width: 0; }
  header { padding: 20px 28px 14px; border-bottom: 1px solid var(--edge); }
  h1 { margin: 0; font-size: 22px; font-weight: 650; color: var(--hot); }
  .body { flex: 1; overflow: auto; padding: 8px 28px 28px; max-width: 46rem; }
  .group { padding: 18px 0; border-bottom: 1px solid var(--edge); }
  .group:last-child { border-bottom: 0; }
  h2 { margin: 0 0 4px; font-size: 15px; font-weight: 650; }
  .hint { font-size: 13px; color: var(--text-dim); margin: 2px 0 10px; }
  .hint.indent { margin-left: calc(7rem + 8px); }
  .warn { color: var(--warn); }
  label { display: flex; align-items: center; gap: 8px; margin: 8px 0; font-size: 13px; }
  label > span { width: 7rem; flex: none; color: var(--text-dim); }
  label.check { margin-left: calc(7rem + 8px); }
  label.check > span { width: auto; color: var(--text); }
  input:not([type=checkbox]), select {
    flex: 1; min-width: 0; font: inherit; color: var(--text); background: var(--panel-2);
    border: 1px solid var(--edge); border-radius: 5px; padding: 6px 8px;
  }
  .actions { margin-top: 14px; display: flex; justify-content: flex-end; }
  .folder { display: flex; align-items: center; gap: 8px; }
  code { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
         font: 12px ui-monospace, Consolas, monospace; background: var(--panel-2);
         border: 1px solid var(--edge); border-radius: 5px; padding: 7px 9px; }
  .icon { padding: 5px 7px; display: inline-grid; place-items: center; }
  .message { margin: 12px 28px 0; padding: 8px 10px 8px 14px; border-radius: 6px; display: flex;
             align-items: center; gap: 10px; font-size: 13px; border: 1px solid var(--edge);
             max-width: 46rem; }
  .message span { flex: 1; }
  .message.ok { border-color: var(--ok); }
  .message.bad { border-color: var(--bad); color: var(--bad); }
  .field { display: flex; align-items: center; gap: 8px; margin: 8px 0; font-size: 13px; }
  .field > span { width: 7rem; flex: none; color: var(--text-dim); }
  .field .folder { flex: 1; min-width: 0; }
  .pair { display: flex; align-items: center; gap: 8px 20px; flex-wrap: wrap; }
  .pair label { flex: none; }
  .pair input { flex: none; width: 6.5rem; }
  .pair label:not(:first-child) > span, .pair label > span.unit { width: auto; }
  .unit { color: var(--text-dim); }
  .supported .head { display: flex; align-items: center; justify-content: space-between;
                     gap: 8px; margin-bottom: 10px; }
  .supported .head h2 { margin: 0; }
  .mods { list-style: none; margin: 0; padding: 0; border: 1px solid var(--edge);
          border-radius: 5px; background: var(--panel-2); }
  .mod { padding: 9px 11px; border-bottom: 1px solid var(--edge); font-size: 13px;
         display: flex; flex-direction: column; gap: 5px; min-width: 0; }
  .mod:last-child { border-bottom: 0; }
  .mod .line { display: flex; align-items: center; gap: 10px; min-width: 0; }
  .mod .name { font-weight: 650; }
  .mod .status { color: var(--text-dim); }
  .mod.installed .status { color: var(--ok); }
  .mod.incompatible .status { color: var(--warn); }
  .mod .page { margin-left: auto; padding: 3px 8px; }
  .mod .file { flex: none; background: none; border: 0; padding: 0; color: var(--text-dim); }
  .mod .missing { font-size: 12px; color: var(--text-dim); }
  .mod .missing summary { cursor: pointer; }
  .mod .missing ul { margin: 4px 0 0; padding-left: 18px; }
  .mod .missing li { font: 12px ui-monospace, Consolas, monospace; overflow-wrap: anywhere; }
</style>
