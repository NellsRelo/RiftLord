<script>
  // Every mission the editors have saved, grouped by the campaign that lists it. They
  // are kept editable; Build cooks them, Install packs built ones into the game's mods
  // folder and Package into a zip to share -- one at a time, a selection, or all of them.
  import Icon from './Icon.svelte'
  import { listBucket, install, remove, reveal, zipUrl, packageTo, setRoot,
           startBuild, buildStatus, renameMission, campaignSettings, saveCampaign,
           setCampaignIndex } from './api.js'
  import { pickFolder, saveFile, isNative } from './native.js'
  import MissionSettings from './MissionSettings.svelte'
  import CampaignEditor, { campaignSendable } from '../../../shared/CampaignEditor.svelte'

  let { onedit, active } = $props()

  let data = $state({ items: [], campaigns: [], root: '' })
  let picked = $state(new Set())
  let busy = $state('')
  let message = $state(null)          // {tone, text}
  let loaded = $state(false)
  //: the mission whose settings are open
  let settingsFor = $state(null)
  //: {id: 'queued' | 'building'} while a build runs
  let progress = $state({})
  //: {id: stage} while a build runs (bucket._STAGE)
  let stages = $state({})
  //: how often a running build's progress is read, in milliseconds
  const BUILD_POLL_MS = 700

  async function refresh() {
    try {
      data = await listBucket()
      const known = new Set(data.items.map((i) => i.id))
      picked = new Set([...picked].filter((id) => known.has(id)))
      loaded = true
    } catch (e) {
      message = { tone: 'bad', text: e.message }
    }
  }

  // Builds land here from the tools while this view is hidden, so it is read again
  // whenever it is shown, and every few seconds while it is.
  $effect(() => {
    if (!active) return
    refresh()
    const timer = setInterval(refresh, 4000)
    return () => clearInterval(timer)
  })

  /** Missions in the order shown: each custom campaign, then shipped placements,
   *  then Custom Maps, then the missions placed in Endless (listed there alone). */
  let groups = $derived.by(() => {
    const out = []
    for (const c of data.campaigns) {
      //: in the campaign's own order (its MissionList)
      const rows = data.items.filter((i) => i.campaign === c.key)
        .sort((a, b) => (a.campaignIndex ?? 1e9) - (b.campaignIndex ?? 1e9))
      if (rows.length) out.push({ key: c.key, title: c.title, kind: 'campaign', campaign: c, rows })
    }
    const shipped = new Map()
    for (const i of data.items) {
      if (!i.campaign && i.placementLabel) {
        if (!shipped.has(i.placementLabel)) shipped.set(i.placementLabel, [])
        shipped.get(i.placementLabel).push(i)
      }
    }
    for (const [title, rows] of shipped) out.push({ key: 'shipped:' + title, title, kind: 'shipped', rows })
    const loose = data.items.filter((i) => !i.campaign && !i.placementLabel && !i.endlessOnly)
    if (loose.length) out.push({ key: 'custom-maps', title: 'Custom Maps', kind: 'none', rows: loose })
    const endless = data.items.filter((i) => !i.campaign && !i.placementLabel && i.endlessOnly)
    if (endless.length) out.push({ key: 'endless', title: 'Endless', kind: 'endless', rows: endless })
    return out
  })

  let allIds = $derived(data.items.map((i) => i.id))
  let chosen = $derived([...picked])
  //: what Install and Package act on: the selection, or every mission when none is selected
  let targetIds = $derived(chosen.length ? chosen : allIds)
  let targetLabel = $derived(chosen.length ? count(chosen.length, 'mission')
    : `all ${count(allIds.length, 'mission')}`)
  let byId = $derived(new Map(data.items.map((i) => [i.id, i])))

  /** A row's build state: a running build's word for it, else the list's. */
  const rowState = (item) => progress[item.id] || item.state
  const needsBuild = (item) => ['unbuilt', 'stale', 'failed'].includes(rowState(item))
  const blocksPacking = (item) => rowState(item) !== 'built'

  /** Build missions one after another, showing each row's progress as it goes. */
  async function doBuild(ids) {
    busy = 'build'
    message = null
    try {
      let st = await startBuild(ids)
      progress = st.progress
      stages = st.stages ?? {}
      while (st.job && st.job.running) {
        await new Promise((r) => setTimeout(r, BUILD_POLL_MS))
        st = await buildStatus()
        progress = st.progress
        stages = st.stages ?? {}
      }
      await refresh()
      const job = st.job || { built: [], failed: [], skipped: [] }
      const title = (id) => byId.get(id)?.title || id
      //: one line per mission that could not be built from what Missions keeps
      const skipped = job.skipped.map((s) => ` Skipped ${s.title}: ${s.reason}.`).join('')
      if (job.failed.length) {
        message = { tone: 'bad', text: `Built ${job.built.length} of ${job.built.length + job.failed.length}. `
          + job.failed.map((f) => f.error).join(' ') + skipped }
      } else if (job.built.length) {
        message = { tone: skipped ? 'warn' : 'ok', text: `Built ${job.built.length === 1 ? title(job.built[0])
          : count(job.built.length, 'mission')}. Install or Package can take ${job.built.length === 1 ? 'it' : 'them'} now.` + skipped }
      } else {
        message = { tone: 'warn', text: `Nothing was built.${skipped}` }
      }
    } catch (e) {
      message = { tone: 'bad', text: e.message }
    } finally {
      progress = {}
      stages = {}
      busy = ''
    }
  }

  /** The refusal Install and Package give when a chosen mission is not built, or null. */
  function unbuiltRefusal(ids, verb) {
    const names = ids.map((id) => byId.get(id)).filter((i) => i && blocksPacking(i)).map((i) => i.title)
    if (!names.length) return null
    return names.length === 1
      ? `Build ${names[0]} first: it is not built or is out of date. Nothing was ${verb}.`
      : `Build these missions first; they are not built or are out of date: ${names.join(', ')}. Nothing was ${verb}.`
  }

  function toggle(id) {
    const next = new Set(picked)
    next.has(id) ? next.delete(id) : next.add(id)
    picked = next
  }
  function toggleGroup(rows) {
    const next = new Set(picked)
    const every = rows.every((r) => next.has(r.id))
    for (const r of rows) every ? next.delete(r.id) : next.add(r.id)
    picked = next
  }
  function toggleAll() {
    picked = picked.size === allIds.length ? new Set() : new Set(allIds)
  }

  async function doInstall(ids, label) {
    const refused = unbuiltRefusal(ids, 'installed')
    if (refused) return (message = { tone: 'bad', text: refused })
    busy = 'install'
    message = null
    try {
      const got = await install(ids)
      //: the game is running: it completes by itself once the game closes
      if (got.waiting) {
        message = { tone: 'warn', text: got.message }
        await refresh()
        return
      }
      const camps = got.campaigns.map((c) => c.left_out
        ? `${c.campaign} (${c.missions} of ${c.missions + c.left_out} missions — the rest are not installed)`
        : c.campaign)
      message = { tone: 'ok', text: `Installed ${label} into the game's mods folder`
        + (camps.length ? `, with campaign ${camps.join(', ')}` : '') + '. Restart the game to see it.' }
      await refresh()
    } catch (e) {
      message = { tone: 'bad', text: e.message }
    } finally {
      busy = ''
    }
  }

  /** Pack missions into a zip to share. The app asks where to save it; a plain browser
   *  downloads it instead. */
  async function doPackage(ids, label, zipName = null) {
    const refused = unbuiltRefusal(ids, 'packaged')
    if (refused) return (message = { tone: 'bad', text: refused })
    const name = zipName || (ids.length === 1 ? `${ids[0]}.zip` : 'RiftLord_Missions.zip')
    if (!isNative()) {
      const a = document.createElement('a')
      a.href = zipUrl(ids)
      a.click()
      return
    }
    const path = await saveFile(`Package ${label}`, name, 'zip')
    if (!path) return
    busy = 'package'
    message = null
    try {
      const got = await packageTo(ids, path)
      message = { tone: 'ok', text: `Packaged ${label} into ${got.path}.` }
    } catch (e) {
      message = { tone: 'bad', text: e.message }
    } finally {
      busy = ''
    }
  }

  /** Keep the missions somewhere else. */
  async function changeFolder() {
    const folder = await pickFolder('Where should Rift Lord keep your missions?', data.root)
    if (!folder || folder === data.root) return
    if (!confirm(`Keep missions in ${folder}?\n\nIf that folder has no missions yet, the ones here move there. If it already has some, Rift Lord switches to those and leaves these where they are.`)) return
    busy = 'move'
    try {
      const got = await setRoot(folder)
      message = { tone: 'ok', text: got.moved
        ? `Missions are now kept in ${got.root}; moved what was here.`
        : `Missions are now kept in ${got.root}.` }
      await refresh()
    } catch (e) {
      message = { tone: 'bad', text: e.message }
    } finally {
      busy = ''
    }
  }

  //: the campaign open in its settings: {key, values}, or null
  let campaignFor = $state(null)

  /** A custom campaign's name, description, hub style and banner, in its own dialog. */
  async function openCampaign(c) {
    try {
      const got = await campaignSettings(c.key)
      campaignFor = { key: c.key, values: { name: got.label, description: got.description ?? '',
                                            hub: got.hub ?? '', banner: got.bannerSource ?? null } }
    } catch (e) {
      message = { tone: 'bad', text: e.message }
    }
  }

  async function saveCampaignSettings(got) {
    await saveCampaign(campaignFor.key, campaignSendable(got))
    campaignFor = null
    await refresh()
  }

  //: the row being renamed: {id, title}, or null
  let renaming = $state(null)

  /** Put a row's name into an input; Enter keeps it, Escape or leaving drops it. */
  function startRename(item) {
    if (busy) return
    renaming = { id: item.id, title: item.title, was: item.title }
  }

  async function finishRename() {
    const r = renaming
    if (!r) return
    renaming = null
    const title = r.title.trim()
    if (!title || title === r.was) return
    busy = 'rename'
    message = null
    try {
      const got = await renameMission(r.id, title)
      message = { tone: 'ok', text: `Renamed to ${got.title}.`
        + (got.stale ? ' Build it again for the game to use the new name.' : '') }
      await refresh()
    } catch (e) {
      message = { tone: 'bad', text: e.message }
    } finally {
      busy = ''
    }
  }

  //: {id: Campaign Index as typed} while a row's differs from its place
  let indexTyped = $state({})
  //: {id: why the typed Campaign Index is refused}
  let indexErrors = $state({})

  /** What is wrong with a Campaign Index for a campaign of `size` missions, or ''. */
  function indexProblem(text, size) {
    const t = String(text).trim()
    if (!/^\d+$/.test(t) || Number(t) < 1 || Number(t) > size) {
      return size <= 1 ? 'Campaign Index is 1.' : `Campaign Index is a whole number from 1 to ${size}.`
    }
    return ''
  }

  function typeIndex(item, size, text) {
    indexTyped = { ...indexTyped, [item.id]: text }
    indexErrors = { ...indexErrors, [item.id]: indexProblem(text, size) }
  }

  /** Move a mission to the typed place in its campaign; the others move along. */
  async function takeIndex(item, campaign) {
    const text = indexTyped[item.id]
    if (text === undefined || indexErrors[item.id]) return
    const index = Number(String(text).trim())
    if (index === item.campaignIndex) {
      indexTyped = { ...indexTyped, [item.id]: undefined }
      return
    }
    busy = 'index'
    message = null
    try {
      await setCampaignIndex(campaign.key, item.id, index)
      indexTyped = { ...indexTyped, [item.id]: undefined }
      message = { tone: 'ok', text: `${item.title} is mission ${index} of ${campaign.title}.` }
      await refresh()
    } catch (e) {
      indexErrors = { ...indexErrors, [item.id]: e.message }
    } finally {
      busy = ''
    }
  }

  /** Focus and select a rename input as it appears. */
  function focusSelect(node) {
    node.focus()
    node.select()
  }

  async function doRemove(ids, label) {
    if (!confirm(`Remove ${label} from Missions?\n\nIts files and notes are deleted from the Missions folder. Anything already installed in the game stays installed.`)) return
    busy = 'remove'
    message = null
    try {
      await remove(ids)
      message = { tone: 'ok', text: `Removed ${label} from Missions.` }
      await refresh()
    } catch (e) {
      message = { tone: 'bad', text: e.message }
    } finally {
      busy = ''
    }
  }

  const count = (n, one, many = one + 's') => `${n} ${n === 1 ? one : many}`
  const STATE = {
    installed: { label: 'Installed', tone: 'ok', tip: 'The game has this exact pak' },
    changed: { label: 'Out of date', tone: 'warn', tip: 'An older build of this pak is installed' },
    no: { label: 'Not installed', tone: 'dim', tip: 'The game does not have this pak' },
    waiting: { label: 'Waiting for the game to close', tone: 'busy', tip: 'Installs when the game closes' },
  }
  const BUILD = {
    built: { label: 'Built', tone: 'ok', tip: 'Built from its latest save; ready to install or package' },
    stale: { label: 'Out of date', tone: 'warn', tip: 'Edited since it was last built; build it again' },
    unbuilt: { label: 'Not built', tone: 'warn', tip: 'Saved but never built; build it before installing' },
    failed: { label: 'Build failed', tone: 'bad', tip: 'Its last build failed' },
    queued: { label: 'Queued', tone: 'dim', tip: 'Waiting for the build in front of it' },
    building: { label: 'Building…', tone: 'busy', tip: 'Being built now' },
  }
  const buildOf = (item) => {
    const b = BUILD[rowState(item)] || BUILD.built
    //: the stage alone (Lightmap, Level, Navmesh, Waves): the column is narrow
    return rowState(item) === 'building' && stages[item.id]
      ? { ...b, label: stages[item.id] } : b
  }

  function when(stamp) {
    if (!stamp) return ''
    const s = (Date.now() / 1000) - stamp
    if (s < 60) return 'just now'
    if (s < 3600) return `${Math.floor(s / 60)} min ago`
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`
    return new Date(stamp * 1000).toLocaleDateString()
  }
</script>

<section class="bucket">
  <header>
    <div class="heading">
      <h1>Missions</h1>
      <p>
        <span>{count(data.items.length, 'mission')}{data.campaigns.length ? ` · ${count(data.campaigns.length, 'campaign')}` : ''}</span>
        <button class="ghost path" onclick={changeFolder} disabled={!!busy}
                title="Choose where Rift Lord keeps your missions">{data.root}</button>
        <button class="ghost icon" onclick={() => reveal()} title="Open the Missions folder"
                aria-label="Open the Missions folder"><Icon name="folder" size={16} /></button>
      </p>
    </div>
    <div class="actions">
      <span class="group">
        <button disabled={!targetIds.length || !!busy}
                onclick={() => doBuild(targetIds)}
                title={`Build ${targetLabel} from ${targetIds.length === 1 ? 'its' : 'their'} saved records: unbuilt ones are built, built ones rebuilt`}>
          <Icon name="build" size={16} /> {busy === 'build' ? 'Building…'
            : chosen.length ? `Build Selected (${chosen.length})` : 'Build All'}
        </button>
        <button class="primary" disabled={!targetIds.length || !!busy}
                onclick={() => doInstall(targetIds, targetLabel)}
                title={chosen.length ? 'Install the selected missions into the game' : 'Install every mission into the game'}>
          <Icon name="install" size={16} /> {busy === 'install' ? 'Installing…'
            : chosen.length ? `Install Selected (${chosen.length})` : 'Install All'}
        </button>
        <button disabled={!targetIds.length || !!busy}
                onclick={() => doPackage(targetIds, targetLabel)}
                title={chosen.length ? 'Package the selected missions into a zip to share' : 'Package every mission into a zip to share'}>
          <Icon name="package" size={16} /> {busy === 'package' ? 'Packaging…'
            : chosen.length ? 'Package Selected…' : 'Package All…'}
        </button>
        <button class="danger" disabled={!chosen.length || !!busy}
                onclick={() => doRemove(chosen, count(chosen.length, 'mission'))}>
          <Icon name="trash" size={16} /> Remove
        </button>
      </span>
    </div>
  </header>

  {#if message}
    <div class="message {message.tone}" role="status">
      <span>{message.text}</span>
      <button class="ghost" onclick={() => (message = null)} aria-label="Dismiss">✕</button>
    </div>
  {/if}

  <div class="list">
    {#if loaded && !data.items.length}
      <div class="empty">
        <Icon name="missions" size={40} />
        <h2>No missions yet</h2>
        <p>Build a map in the <b>Map Editor</b>, or save an onslaught in the
          <b>Onslaught Editor</b>. Missions are kept here, editable, until you install
          them into the game or package them to share. Build a mission after
          each change; only built missions install or package.</p>
      </div>
    {:else if data.items.length}
      <div class="row head">
        <input type="checkbox" aria-label="Select all"
               checked={picked.size === allIds.length && allIds.length > 0}
               indeterminate={picked.size > 0 && picked.size < allIds.length}
               onchange={toggleAll} />
        <span>Name</span><span>Made in</span><span>Working Title</span><span>Build</span><span>Game</span><span>Updated</span><span></span>
      </div>
      {#each groups as g (g.key)}
        <div class="group-head">
          <input type="checkbox" aria-label="Select {g.title}"
                 checked={g.rows.every((r) => picked.has(r.id))}
                 indeterminate={g.rows.some((r) => picked.has(r.id)) && !g.rows.every((r) => picked.has(r.id))}
                 onchange={() => toggleGroup(g.rows)} />
          <h3>{g.title}</h3>
          <span class="chip {g.kind}">{g.kind === 'campaign' ? 'Custom campaign'
            : g.kind === 'shipped' ? 'Shipped campaign'
            : g.kind === 'endless' ? 'Endless hub' : 'No campaign'}</span>
          {#if g.kind === 'campaign'}
            <span class="dim">{g.campaign.source} · {count(g.campaign.missions.length, 'mission')}</span>
            {#if g.campaign.own}
              <button class="ghost icon" title="Campaign Settings" aria-label="Campaign Settings for {g.title}"
                      disabled={!!busy} onclick={() => openCampaign(g.campaign)}><Icon name="settings" size={15} /></button>
              <!-- one pak with its missions: installed and packaged as one -->
              <span class="state {STATE[g.campaign.installed].tone}" title={STATE[g.campaign.installed].tip}
                    data-campaign-state>{STATE[g.campaign.installed].label}</span>
              <button class="ghost icon" title="Install Campaign" aria-label="Install Campaign {g.title}"
                      disabled={!!busy || g.rows.some(blocksPacking)}
                      onclick={() => doInstall(g.campaign.members, g.title)}><Icon name="install" size={15} /></button>
              <button class="ghost icon" title="Package Campaign" aria-label="Package Campaign {g.title}"
                      disabled={!!busy}
                      onclick={() => doPackage(g.campaign.members, g.title,
                                               g.campaign.source.replace(/\.pak$/, '') + '.zip')}><Icon name="package" size={15} /></button>
            {/if}
          {/if}
        </div>
        {#each g.rows as item (item.id)}
          <div class="row" class:on={picked.has(item.id)}>
            <input type="checkbox" checked={picked.has(item.id)} onchange={() => toggle(item.id)}
                   aria-label="Select {item.title}" />
            <span class="name">
              {#if renaming?.id === item.id}
                <input class="rename" bind:value={renaming.title} use:focusSelect maxlength="120"
                       aria-label="New name for {item.title}"
                       onkeydown={(e) => { if (e.key === 'Enter') finishRename()
                                           if (e.key === 'Escape') renaming = null }}
                       onblur={finishRename} />
              {:else}
                <span class="title">
                  <b ondblclick={() => startRename(item)} title="Double-click to rename">{item.title}</b>
                  <button class="ghost icon pen" disabled={!!busy} title="Rename"
                          aria-label="Rename {item.title}"
                          onclick={() => startRename(item)}><Icon name="pencil" size={13} /></button>
                </span>
              {/if}
              {#if g.kind === 'campaign' && item.campaignIndex}
                <label class="cindex"><span>Campaign Index</span>
                  <input value={indexTyped[item.id] ?? String(item.campaignIndex)} inputmode="numeric"
                         spellcheck="false" disabled={!!busy && busy !== 'index'}
                         aria-label="Campaign Index of {item.title}"
                         oninput={(e) => typeIndex(item, g.campaign.missions.length, e.currentTarget.value)}
                         onkeydown={(e) => { if (e.key === 'Enter') takeIndex(item, g.campaign) }}
                         onblur={() => takeIndex(item, g.campaign)} /></label>
                {#if indexErrors[item.id] && indexTyped[item.id] !== undefined}
                  <small class="bad" role="alert">{indexErrors[item.id]}</small>
                {/if}
              {/if}
              {#if rowState(item) === 'failed'}
                <small class="bad" title={item.error}>{item.error}</small>
              {:else}
                <small>{item.source || item.id}</small>
              {/if}
            </span>
            <span class="made">
              {#if item.kind === 'map'}<Icon name="map" size={15} /> Map{:else}<Icon name="onslaught" size={15} /> Onslaught{/if}
            </span>
            <span class="dim wtitle" title={item.workingTitle || ''}>{item.workingTitle || '—'}</span>
            <span><span class="state {buildOf(item).tone}"
                        title={rowState(item) === 'failed' ? item.error : buildOf(item).tip}>
              {#if rowState(item) === 'building'}<span class="spin" aria-hidden="true"></span>{/if}{buildOf(item).label}</span></span>
            <span><span class="state {STATE[item.installed].tone}" title={STATE[item.installed].tip}>{STATE[item.installed].label}</span></span>
            <span class="dim">{when(item.updated)}</span>
            <span class="tools">
              <button class="ghost" disabled={!item.editable.riftlord}
                      title={item.editable.riftlord ? 'Edit the map in the Map Editor' : 'Not built in the Map Editor'}
                      onclick={() => onedit('riftlord', item)}><Icon name="map" size={16} /> Map</button>
              <button class="ghost" disabled={!item.editable.onslaught}
                      title="Edit the waves in the Onslaught Editor"
                      onclick={() => onedit('onslaught', item)}><Icon name="onslaught" size={16} /> Waves</button>
              <button class="ghost icon" title="Mission Settings: details, campaign and Endless"
                      onclick={() => (settingsFor = item.id)}><Icon name="settings" size={16} /></button>
              <!-- Build on every row (Rebuild once built); Install only on a built one,
                   which otherwise keeps its place so the icons stay in columns -->
              <button class="ghost rowbuild" disabled={!!busy}
                      title={needsBuild(item) ? 'Build the mission from its saved record'
                        : 'Rebuild the mission from its saved record'}
                      onclick={() => doBuild([item.id])}><Icon name="build" size={16} />
                {needsBuild(item) ? 'Build' : 'Rebuild'}</button>
              <!-- a campaign's mission installs into its campaign's pak once that is installed,
                   else alone (bucket._units); Package zips it alone -->
              <button class="ghost icon" class:gone={blocksPacking(item)}
                      disabled={!!busy || blocksPacking(item)} aria-hidden={blocksPacking(item)}
                      tabindex={blocksPacking(item) ? -1 : 0} title="Install Mission"
                      onclick={() => doInstall([item.id], item.title)}><Icon name="install" size={16} /></button>
              <button class="ghost icon" title="Package Mission" disabled={!!busy}
                      onclick={() => doPackage([item.id], item.title)}><Icon name="package" size={16} /></button>
              <button class="ghost icon danger" title="Remove Mission" disabled={!!busy}
                      onclick={() => doRemove([item.id], item.title)}><Icon name="trash" size={16} /></button>
            </span>
          </div>
        {/each}
      {/each}
    {/if}
  </div>
</section>

{#if settingsFor}
  <MissionSettings id={settingsFor} onclose={() => (settingsFor = null)}
                   onsaved={() => { message = { tone: 'ok', text: 'Settings saved.' }; refresh() }} />
{/if}
{#if campaignFor}
  <CampaignEditor values={campaignFor.values} onsave={saveCampaignSettings}
                  onclose={() => (campaignFor = null)} />
{/if}

<style>
  .bucket { height: 100%; display: flex; flex-direction: column; min-width: 0; }
  header {
    display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between;
    gap: 12px 24px; padding: 20px 28px 14px; border-bottom: 1px solid var(--edge);
  }
  /* the shared title purple, as the editors' titles use */
  h1 { margin: 0; font-size: 22px; font-weight: 650; color: var(--hot); }
  .heading p { margin: 2px 0 0; color: var(--text-dim); font-size: 13px; display: flex;
               align-items: center; gap: 6px; flex-wrap: wrap; }
  .path { font-size: 12px; color: var(--text-dim); padding: 2px 6px; max-width: 52ch;
          overflow: hidden; text-overflow: ellipsis; text-decoration: underline dotted;
          text-underline-offset: 3px; }
  .path:hover:not(:disabled) { color: var(--accent-ink); }
  .heading .icon { padding: 2px 5px; display: inline-grid; place-items: center; color: var(--text-dim); }
  .actions { display: flex; gap: 14px; flex-wrap: wrap; }
  .actions .group { display: flex; gap: 6px; }
  .actions button { display: inline-flex; align-items: center; gap: 6px; }

  .message { margin: 12px 28px 0; padding: 8px 10px 8px 14px; border-radius: 6px; display: flex;
             align-items: center; gap: 10px; font-size: 13px; border: 1px solid var(--edge); }
  .message span { flex: 1; }
  .message.ok { border-color: var(--ok); }
  .message.bad { border-color: var(--bad); color: var(--bad); }
  .message.warn { border-color: var(--warn); }

  .list { flex: 1; overflow: auto; padding: 8px 28px 28px; }
  .row {
    display: grid; align-items: center; gap: 14px;
    grid-template-columns: 18px minmax(200px, 2.2fr) 100px minmax(110px, 1fr) 110px 110px 80px 362px;
    padding: 8px 10px; border-radius: 6px;
  }
  .row:not(.head):hover { background: var(--panel-2); }
  .row.on { background: color-mix(in srgb, var(--accent) 10%, transparent); }
  .row.head { color: var(--text-dim); font-size: 12px; text-transform: uppercase;
              letter-spacing: 0.05em; position: sticky; top: 0; background: var(--bg); z-index: 1; }
  .name { display: flex; flex-direction: column; min-width: 0; }
  .name b { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .name .title { display: flex; align-items: center; gap: 4px; min-width: 0; }
  .name .pen { padding: 1px 3px; color: var(--text-dim); opacity: 0; }
  .row:hover .name .pen, .name .pen:focus-visible { opacity: 1; }
  .name input.rename { font: inherit; font-weight: 600; padding: 1px 5px; margin: 0;
                       min-width: 0; }
  .name small { color: var(--text-dim); font: 11px ui-monospace, Consolas, monospace;
                overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .name small.bad { color: var(--bad); font-family: inherit; }
  .name .cindex { display: flex; align-items: center; gap: 6px; font-size: 11px;
                  color: var(--text-dim); margin: 2px 0; }
  .name .cindex input { width: 3.5em; font-size: 12px; padding: 1px 5px; margin: 0; }
  .made { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; }
  .dim { color: var(--text-dim); font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tools { display: flex; gap: 2px; justify-content: flex-end; }
  .tools button { display: inline-flex; align-items: center; gap: 5px; font-size: 13px; padding: 4px 8px; }
  .tools button.icon { padding: 4px 6px; }
  /* wide enough for "Rebuild", so Build and Rebuild rows keep the icons in columns */
  .tools button.rowbuild { min-width: 6.2em; justify-content: flex-start; }
  .tools button.gone { visibility: hidden; }

  .group-head { display: flex; align-items: center; gap: 10px; padding: 18px 10px 6px;
                border-bottom: 1px solid var(--edge); margin-bottom: 4px; }
  .group-head h3 { margin: 0; font-size: 15px; font-weight: 650; }
  .chip { font-size: 11px; padding: 1px 8px; border-radius: 99px; border: 1px solid var(--edge);
          color: var(--text-dim); }
  .chip.campaign { border-color: var(--accent); color: var(--accent-ink); }

  .state { font-size: 12px; padding: 2px 8px; border-radius: 99px; white-space: nowrap; }
  .state.ok { color: var(--ok); border: 1px solid var(--ok); }
  .state.warn { color: var(--warn); border: 1px solid var(--warn); }
  .state.dim { color: var(--text-dim); border: 1px solid var(--edge); }
  .state.bad { color: var(--bad); border: 1px solid var(--bad); }
  .state.busy { color: var(--accent-ink); border: 1px solid var(--accent);
                display: inline-flex; align-items: center; gap: 6px; }
  .spin { width: 10px; height: 10px; border-radius: 50%; border: 2px solid var(--accent-soft);
          border-top-color: var(--accent-ink); animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .empty { max-width: 34rem; margin: 12vh auto 0; text-align: center; color: var(--text-dim); }
  .empty h2 { color: var(--text); font-size: 18px; margin: 12px 0 6px; }
  .empty p { line-height: 1.6; }
</style>
