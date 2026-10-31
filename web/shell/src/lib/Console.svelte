<script>
  // What the editors' scripts said: every build's progress, anything the server
  // printed, and every request that failed. Kept by the server (console.py); this
  // panel asks for what it has not seen yet, and filters by source and level.
  import { consoleSince, clearConsole } from './api.js'

  let { open, onclose, onunseen } = $props()

  //: Levels, least to most severe, with the names the panel shows. Choosing one shows it
  //: and every more severe level: ERROR errors only, DEBUG everything.
  const LEVELS = [['debug', 'DEBUG'], ['info', 'INFO'], ['warn', 'WARN'], ['error', 'ERROR']]
  const RANK = Object.fromEntries(LEVELS.map(([k], i) => [k, i]))
  //: The Level dropdown, least to most output.
  const CHOICES = [...LEVELS].reverse()
  //: Where the filter choices are remembered in this browser.
  const STORE = 'riftlord-console-filter'

  let lines = $state([])
  let next = 0
  let box = $state(null)
  let stick = true
  let known = $state([])
  let saved = {}
  try { saved = JSON.parse(localStorage.getItem(STORE) || '{}') } catch { saved = {} }
  let minLevel = $state(RANK[saved.level] !== undefined ? saved.level : 'info')
  let hidden = $state(new Set(Array.isArray(saved.hidden) ? saved.hidden : []))

  function remember() {
    try { localStorage.setItem(STORE, JSON.stringify({ level: minLevel, hidden: [...hidden] })) } catch { /* private window */ }
  }

  let sources = $derived([...new Set([...known, ...lines.map((l) => l.source).filter(Boolean)])])
  let shown = $derived(lines.filter((l) => (RANK[l.level] ?? 1) >= RANK[minLevel] && !hidden.has(l.source)))

  //: true while a poll waits for its answer: one at a time
  let polling = false

  async function poll() {
    if (polling) return
    polling = true
    try {
      const got = await consoleSince(next)
      if (got.sources) known = got.sources
      if (got.lines.length) {
        lines = [...lines, ...got.lines].slice(-3000)
        if (!open) onunseen?.(got.lines.some((l) => l.level === 'error') ? 'error' : 'new')
      }
      next = got.next
    } catch { /* the server may be stopping */ } finally {
      polling = false
    }
  }

  // Asked often while open, now and then while shut, so the sidebar can say there is
  // something new.
  $effect(() => {
    poll()
    const t = setInterval(poll, open ? 1000 : 3000)
    return () => clearInterval(t)
  })

  $effect(() => {
    shown
    const el = box
    if (open && el && stick) queueMicrotask(() => { el.scrollTop = el.scrollHeight })
  })

  function scrolled() {
    if (!box) return
    stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 24
  }

  function toggleSource(s) {
    const n = new Set(hidden)
    n.has(s) ? n.delete(s) : n.add(s)
    hidden = n
    remember()
  }

  function setLevel(e) {
    minLevel = e.currentTarget.value
    remember()
  }

  async function clear() {
    await clearConsole()
    lines = []
  }

  const time = (t) => new Date(t * 1000).toLocaleTimeString()
  const levelName = Object.fromEntries(LEVELS)
</script>

{#if open}
  <section class="console" aria-label="Console">
    <header>
      <h2>Console</h2>
      <span class="count">{shown.length} of {lines.length} line{lines.length === 1 ? '' : 's'}</span>
      <span class="sources" role="group" aria-label="Sources">
        {#each sources as s (s)}
          <button class="chip" class:off={hidden.has(s)} aria-pressed={!hidden.has(s)}
                  title={hidden.has(s) ? `Show ${s}` : `Hide ${s}`}
                  onclick={() => toggleSource(s)}>{s}</button>
        {/each}
      </span>
      <span class="grow"></span>
      <label class="level">Level
        <select value={minLevel} onchange={setLevel} aria-label="Level">
          {#each CHOICES as [k, label] (k)}<option value={k}>{label}</option>{/each}
        </select>
      </label>
      <button class="ghost" onclick={clear}>Clear</button>
      <button class="ghost" onclick={onclose} aria-label="Close the console">✕</button>
    </header>
    <div class="lines" bind:this={box} onscroll={scrolled}>
      {#each shown as l (l.n)}
        <div class="line {l.level}">
          <span class="t">{time(l.t)}</span>
          <span class="lv">{levelName[l.level] ?? 'INFO'}</span>
          {#if l.source}<span class="src">{l.source}</span>{/if}
          <span class="text">{l.text}</span>
        </div>
      {:else}
        <p class="empty">{lines.length ? 'Nothing at this level from the sources shown.'
          : 'Nothing yet. Saves, builds and installs report here as they run.'}</p>
      {/each}
    </div>
  </section>
{/if}

<style>
  .console {
    height: 38%; min-height: 160px; flex: none; display: flex; flex-direction: column;
    border-top: 1px solid var(--edge); background: var(--panel);
  }
  header { display: flex; align-items: center; gap: 10px; padding: 6px 10px 6px 16px;
           border-bottom: 1px solid var(--edge); flex-wrap: wrap; }
  h2 { margin: 0; font-size: 13px; font-weight: 650; color: var(--hot); }
  .count { font-size: 12px; color: var(--text-dim); }
  .grow { flex: 1; }
  header button { padding: 2px 8px; font-size: 12px; }
  .sources { display: flex; gap: 4px; flex-wrap: wrap; }
  .chip { border-radius: 99px; border: 1px solid var(--accent); color: var(--accent-ink);
          background: transparent; font-size: 11px; padding: 1px 8px; }
  .chip.off { border-color: var(--edge); color: var(--text-dim); text-decoration: line-through; }
  .level { font-size: 12px; color: var(--text-dim); display: inline-flex; gap: 6px; align-items: center; }
  .level select { font-size: 12px; padding: 1px 4px; }
  .lines { flex: 1; overflow: auto; padding: 6px 0;
           font: 12px/1.55 ui-monospace, 'Cascadia Mono', Consolas, monospace; }
  .line { display: flex; gap: 10px; padding: 0 16px; white-space: pre-wrap; }
  .line:hover { background: var(--panel-2); }
  .t { color: var(--text-dim); flex: none; }
  .lv { color: var(--text-dim); flex: none; width: 4.4em; }
  .src { color: var(--accent-ink); flex: none; min-width: 9.5em; }
  .text { color: var(--text); min-width: 0; word-break: break-word; }
  .line.debug .text { color: var(--text-dim); }
  .line.warn .text, .line.warn .lv { color: var(--warn); }
  .line.error .text, .line.error .lv { color: var(--bad); }
  .empty { color: var(--text-dim); padding: 4px 16px; margin: 0; font-family: inherit; }
</style>
