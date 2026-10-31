<script>
  // Rift Lord's own titlebar, drawn in the app window, which has no system frame: the
  // app icon over the sidebar, the title, and the window's buttons. The window is
  // dragged from anywhere on the bar that is not a button, and resized from thin
  // zones along its edges -- both are the window's own moves, asked for over IPC.
  import { windowCommand, onWindowState } from './native.js'

  let { section = '' } = $props()
  let maximized = $state(false)
  onWindowState((s) => { maximized = !!s.maximized })

  const EDGES = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw']

  function drag(e) {
    if (e.button !== 0 || e.target.closest('button')) return
    if (e.detail === 2) windowCommand('toggleMaximize')
    else windowCommand('drag')
  }
</script>

<header class="titlebar" onmousedown={drag} role="presentation">
  <div class="mark"><img src="./favicon.png" alt="" width="20" height="20" /></div>
  <span class="title">Rift Lord{#if section}<span class="section">— {section}</span>{/if}</span>
  <span class="grow"></span>
  <div class="controls">
    <button onclick={() => windowCommand('minimize')} aria-label="Minimize" title="Minimize">
      <svg viewBox="0 0 12 12" width="12" height="12"><path d="M2 6.5h8" /></svg>
    </button>
    <button onclick={() => windowCommand('toggleMaximize')}
            aria-label={maximized ? 'Restore' : 'Maximize'} title={maximized ? 'Restore' : 'Maximize'}>
      {#if maximized}
        <svg viewBox="0 0 12 12" width="12" height="12"><path d="M3.5 3.5V2h6.5v6.5H8.5M2 3.5h6.5V10H2z" /></svg>
      {:else}
        <svg viewBox="0 0 12 12" width="12" height="12"><path d="M2 2h8v8H2z" /></svg>
      {/if}
    </button>
    <button class="close" onclick={() => windowCommand('close')} aria-label="Close" title="Close">
      <svg viewBox="0 0 12 12" width="12" height="12"><path d="M2.5 2.5l7 7M9.5 2.5l-7 7" /></svg>
    </button>
  </div>
</header>

{#if !maximized}
  {#each EDGES as edge}
    <div class="edge {edge}" role="presentation"
         onmousedown={(e) => { if (e.button === 0) windowCommand('resize', { edge }) }}></div>
  {/each}
{/if}

<style>
  /* above every page's modal scrim, so the bar always drags and never dismisses one */
  .titlebar {
    position: relative; z-index: 3000;
    height: 34px; flex: none; display: flex; align-items: center;
    background: var(--rail); border-bottom: 1px solid var(--edge);
    user-select: none; -webkit-user-select: none;
  }
  /* the same width as the sidebar, so the icon sits over the sidebar's buttons */
  .mark { width: 64px; height: 100%; display: grid; place-items: center; flex: none;
          border-right: 1px solid var(--edge); }
  .title { font-size: 13px; font-weight: 600; padding-left: 12px; color: var(--text); }
  .section { color: var(--text-dim); font-weight: 400; margin-left: 0.45em; }
  .grow { flex: 1; align-self: stretch; }
  .controls { display: flex; height: 100%; }
  .controls button {
    width: 46px; height: 100%; display: grid; place-items: center; padding: 0;
    background: none; border: 0; border-radius: 0; color: var(--text-dim);
  }
  .controls button:hover { background: var(--raised); color: var(--text); border: 0; }
  .controls button.close:hover { background: #c42b1c; color: #fff; }
  .controls svg { fill: none; stroke: currentColor; stroke-width: 1.1; }

  /* thin resize zones over everything, the frames included */
  .edge { position: fixed; z-index: 3001; }
  .edge.n { top: 0; left: 8px; right: 8px; height: 4px; cursor: ns-resize; }
  .edge.s { bottom: 0; left: 8px; right: 8px; height: 5px; cursor: ns-resize; }
  .edge.e { right: 0; top: 8px; bottom: 8px; width: 5px; cursor: ew-resize; }
  .edge.w { left: 0; top: 8px; bottom: 8px; width: 5px; cursor: ew-resize; }
  .edge.ne { top: 0; right: 0; width: 10px; height: 10px; cursor: nesw-resize; }
  .edge.nw { top: 0; left: 0; width: 10px; height: 10px; cursor: nwse-resize; }
  .edge.se { bottom: 0; right: 0; width: 12px; height: 12px; cursor: nwse-resize; }
  .edge.sw { bottom: 0; left: 0; width: 12px; height: 12px; cursor: nesw-resize; }
</style>
