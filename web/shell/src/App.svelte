<script>
  // An icon rail on the left; the bucket or one of the tools beside it. Each tool is
  // its own page, served by its own server, shown in a frame that stays loaded once
  // opened, so switching back and forth keeps what was on screen.
  import Icon from './lib/Icon.svelte'
  import Missions from './lib/Missions.svelte'
  import FabKits from './lib/FabKits.svelte'
  import Titlebar from './lib/Titlebar.svelte'
  import Console from './lib/Console.svelte'
  import Settings from './lib/Settings.svelte'
  import Setup from './lib/Setup.svelte'
  import { tools as getTools, quit } from './lib/api.js'
  import { isNative } from './lib/native.js'

  const VIEWS = [
    { key: 'bucket', label: 'Missions', icon: 'missions' },
    { key: 'riftlord', label: 'Map Editor', icon: 'map' },
    { key: 'onslaught', label: 'Onslaught Editor', icon: 'onslaught' },
    { key: 'fab', label: 'Fab Kits', icon: 'kits' },
  ]
  //: views drawn by this page rather than shown in a frame
  const OWN_VIEWS = ['bucket', 'fab', 'settings']

  let view = $state('bucket')
  let info = $state(null)
  let error = $state(null)
  let stopped = $state(false)
  let theme = $state(localStorage.getItem('workshop-theme') || 'dark')
  //: Each tool's frame address. Unset until the tool is first opened, so a tool
  //: nobody visits costs nothing; changed to reopen it on a bucket item.
  let frames = $state({ riftlord: null, onslaught: null })
  let opens = 0
  //: the console panel, and whether it has news since it was last open: 'new', 'error'
  let consoleOpen = $state(false)
  let unseen = $state(null)
  //: the app window draws its own titlebar; a plain browser (development) has its own
  const native = isNative()

  // An editor's page asks for the console when a save fails, and the Map Editor's
  // music preview is played by the app window (workshop/app, main.rs `music`): its
  // requests go to the window's message channel and the window's answers back to the
  // editors' frames.
  $effect(() => {
    const take = (e) => {
      if (e.data?.type === 'riftlord-console') { consoleOpen = true; unseen = null }
      if (e.data?.type === 'riftlord-music' && native) {
        window.ipc.postMessage(JSON.stringify({ id: 0, music: e.data.action, url: e.data.url || '' }))
      }
    }
    window.__riftlordMusic = (state, url) => {
      for (const frame of document.querySelectorAll('iframe')) {
        frame.contentWindow?.postMessage({ type: 'riftlord-music-state', state, url }, '*')
      }
    }
    window.addEventListener('message', take)
    return () => window.removeEventListener('message', take)
  })

  $effect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem('workshop-theme', theme) } catch { /* private window */ }
    // the one theme switch for the whole app: each editor's page follows it
    for (const frame of document.querySelectorAll('iframe')) tellTheme(frame)
  })

  //: the editors' pages ask the app window, through this page, for what it does natively
  function tellNative(frame) {
    frame.contentWindow?.postMessage({ type: 'riftlord-native', music: native }, '*')
  }

  function tellTheme(frame) {
    frame.contentWindow?.postMessage({ type: 'riftlord-theme', theme }, '*')
  }

  //: /api/tools: the tools' addresses; the editors' tabs are disabled until it answers
  const toolsAnswer = getTools()
  $effect(() => {
    toolsAnswer.then((got) => { info = got }).catch((e) => { error = e.message })
  })

  function address(tool, extra = {}) {
    const q = new URLSearchParams({ embedded: '1', theme, ...extra })
    return info.tools[tool] + '?' + q
  }

  function show(key) {
    view = key
    if (!OWN_VIEWS.includes(key) && info && !frames[key]) frames[key] = address(key)
  }

  /** A Fab Kit was converted or deleted: the editors' pages read their Fab libraries again. */
  function fabChanged(pack) {
    for (const frame of document.querySelectorAll('iframe')) {
      frame.contentWindow?.postMessage({ type: 'riftlord-fab-changed', pack: pack || null }, '*')
    }
  }

  //: How long an editor's page has to answer whether it has a level open, in milliseconds.
  const OPEN_STATE_MS = 600
  let asks = 0
  //: true while an editor is being asked, so a second click waits for the first
  let asking = false

  /** Whether `tool`'s page has a level open, and whether that level has changes not
   *  saved (null when the page does not say), asked by message (riftlord-open-state).
   *  A page that does not answer counts as open when its frame was opened on a Missions
   *  item. */
  function openState(tool) {
    const frame = document.querySelector(`iframe[data-tool="${tool}"]`)
    const fallback = { open: new URL(frames[tool], location.href).searchParams.has('bucket'), unsaved: null }
    if (!frame?.contentWindow) return Promise.resolve(fallback)
    const id = ++asks
    return new Promise((resolve) => {
      const done = (got) => {
        window.removeEventListener('message', take)
        clearTimeout(timer)
        resolve(got)
      }
      const take = (e) => {
        if (e.source !== frame.contentWindow || e.data?.type !== 'riftlord-open-state' || e.data.id !== id) return
        done({ open: !!e.data.open, unsaved: typeof e.data.unsaved === 'boolean' ? e.data.unsaved : null })
      }
      const timer = setTimeout(() => done(fallback), OPEN_STATE_MS)
      window.addEventListener('message', take)
      frame.contentWindow.postMessage({ type: 'riftlord-open-state', id }, '*')
    })
  }

  /** Open a bucket item in a tool, replacing whatever that tool has open: asked first
   *  when the tool has a level open with changes not saved (or open, where the tool does
   *  not say). */
  async function edit(tool, item) {
    if (asking) return
    const name = VIEWS.find((v) => v.key === tool).label
    asking = true
    let state = { open: false }
    try {
      // a Missions item's buttons show before the tools' addresses: they wait for them
      info = info || await toolsAnswer.catch(() => null)
      if (frames[tool]) state = await openState(tool)
    } finally {
      asking = false
    }
    if (!info) return
    if (state.open && state.unsaved !== false &&
        !confirm(`Open “${item.title}” in ${name}?\n\nWhatever is open in ${name} now is replaced; anything not built is lost.`)) return
    opens += 1
    frames[tool] = address(tool, { bucket: item.id, open: String(opens) })
    view = tool
  }

  async function stop() {
    if (!confirm('Quit Rift Lord?\n\nAnything not built in the Map Editor or the Onslaught Editor is lost.')) return
    try { await quit() } catch { /* the server may close before it answers */ }
    stopped = true
  }
</script>

{#if stopped}
  <div class="stopped">
    <h1>Rift Lord has stopped</h1>
    <p>Close this tab. What you built is still in the bucket for next time.</p>
  </div>
{:else}
  <div class="frame">
  {#if native}<Titlebar section={view === 'settings' ? 'Settings' : VIEWS.find((v) => v.key === view)?.label} />{/if}
  <div class="app">
    <nav class="rail" aria-label="Rift Lord">
      {#if !native}
        <div class="mark" title="Rift Lord{info ? ' ' + info.version : ''}">
          <img src="./favicon.png" alt="" width="30" height="30" />
        </div>
      {/if}
      {#each VIEWS as v}
        <button class="tab" class:on={view === v.key} onclick={() => show(v.key)}
                aria-label={v.label} aria-current={view === v.key ? 'page' : undefined}
                disabled={v.key !== 'bucket' && !info}>
          <Icon name={v.icon} size={24} />
          <span class="tip">{v.label}</span>
        </button>
      {/each}
      <span class="grow"></span>
      <button class="tab small" class:on={consoleOpen}
              onclick={() => { consoleOpen = !consoleOpen; unseen = null }} aria-label="Console">
        <Icon name="console" size={20} />
        {#if unseen}<span class="dot {unseen}"></span>{/if}
        <span class="tip">Console</span>
      </button>
      <button class="tab small" class:on={view === 'settings'} onclick={() => show('settings')}
              aria-label="Settings" aria-current={view === 'settings' ? 'page' : undefined}>
        <Icon name="gear" size={20} />
        <span class="tip">Settings</span>
      </button>
      <button class="tab small" onclick={() => (theme = theme === 'dark' ? 'light' : 'dark')}
              aria-label="Switch theme">
        <Icon name={theme === 'dark' ? 'moon' : 'sun'} size={20} />
        <span class="tip">{theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}</span>
      </button>
      <!-- the app window closes from its titlebar; a plain browser needs a way to stop -->
      {#if !native}
        <button class="tab small quit" onclick={stop} aria-label="Quit">
          <Icon name="power" size={20} />
          <span class="tip">Quit Rift Lord</span>
        </button>
      {/if}
    </nav>

    <main>
      {#if error}<div class="error" role="alert">{error}</div>{/if}
      {#if info && !info.game}
        <div class="error" role="alert">Orcs Must Die 3 was not found. Open the
          Onslaught Editor to point Rift Lord at the game.</div>
      {/if}
      {#if info && info.game}<Setup />{/if}
      <div class="pane" class:hidden={view !== 'settings'}>
        <Settings active={view === 'settings'} />
      </div>
      <div class="pane" class:hidden={view !== 'bucket'}>
        <Missions active={view === 'bucket'} onedit={edit} />
      </div>
      <div class="pane" class:hidden={view !== 'fab'}>
        <FabKits active={view === 'fab'} {info} onchanged={fabChanged}
                 onlog={() => { consoleOpen = true; unseen = null }} />
      </div>
      {#each ['riftlord', 'onslaught'] as tool}
        {#if frames[tool]}
          <!-- keyed on the address, so reopening on an item reloads the frame -->
          {#key frames[tool]}
            <iframe class="pane" class:hidden={view !== tool} src={frames[tool]} data-tool={tool}
                    title={VIEWS.find((v) => v.key === tool).label}
                    onload={(e) => { tellTheme(e.currentTarget); tellNative(e.currentTarget) }}></iframe>
          {/key}
        {/if}
      {/each}
      <Console open={consoleOpen} onclose={() => (consoleOpen = false)}
               onunseen={(kind) => { if (unseen !== 'error') unseen = kind }} />
    </main>
  </div>
</div>
{/if}

<style>
  .frame { display: flex; flex-direction: column; height: 100%; }
  .app { display: flex; flex: 1; min-height: 0; }
  .rail {
    width: 64px; flex: none; display: flex; flex-direction: column; align-items: center;
    gap: 6px; padding: 12px 0; background: var(--rail); border-right: 1px solid var(--edge);
  }
  .mark { height: 40px; display: grid; place-items: center; margin-bottom: 10px; }
  .tab {
    position: relative; width: 46px; height: 46px; display: grid; place-items: center;
    background: none; border: 1px solid transparent; border-radius: 10px; color: var(--text-dim);
    padding: 0;
  }
  .tab.small { width: 40px; height: 40px; }
  .tab:hover:not(:disabled) { color: var(--text); background: var(--raised); border-color: transparent; }
  .tab.on { color: var(--accent-ink); background: var(--panel-2); }
  .tab.on::before {
    content: ''; position: absolute; left: -9px; top: 9px; bottom: 9px; width: 3px;
    border-radius: 0 3px 3px 0; background: var(--accent);
  }
  .tab.quit:hover { color: var(--bad); }
  /* news in the console since it was last open */
  .dot { position: absolute; top: 7px; right: 7px; width: 8px; height: 8px; border-radius: 50%;
         background: var(--accent-ink); border: 2px solid var(--rail); }
  .dot.error { background: var(--bad); }
  .tip {
    position: absolute; left: calc(100% + 10px); top: 50%; transform: translateY(-50%);
    background: var(--panel); color: var(--text); border: 1px solid var(--edge);
    border-radius: 6px; padding: 3px 9px; font-size: 13px; white-space: nowrap;
    pointer-events: none; opacity: 0; transition: opacity 0.12s; z-index: 10;
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.3);
  }
  .tab:hover .tip, .tab:focus-visible .tip { opacity: 1; }
  .grow { flex: 1; }

  main { flex: 1; min-width: 0; position: relative; display: flex; flex-direction: column; }
  .pane { flex: 1; min-height: 0; width: 100%; border: 0; display: block; }
  .pane.hidden { display: none; }
  iframe.pane { background: var(--bg); }
  .error { margin: 10px 16px 0; padding: 8px 12px; border: 1px solid var(--bad);
           border-radius: 6px; color: var(--bad); font-size: 13px; }

  .stopped { max-width: 32rem; margin: 20vh auto; text-align: center; }
  .stopped p { color: var(--text-dim); }
</style>
