<script>
  // The tab strip: as many tabs as fit, then a "…" tab listing the rest. `tabs`:
  // [{key, text, title, cls, door}]; a door's tab carries the Endless dot (`early`) and the
  // flyer mark.
  import { tick } from 'svelte'

  let { tabs, active, early = {}, onselect } = $props()

  //: room the "…" tab takes, in px
  const MORE_WIDTH = 46
  let strip = $state(null)
  //: tabs shown before the "…" tab (Infinity: every tab fits)
  let fit = $state(Infinity)
  let menu = $state(false)

  /** Show every tab, then keep the ones that fit beside the "…" tab. */
  async function measure() {
    if (!strip) return
    fit = Infinity
    await tick()
    const width = strip.clientWidth
    const buttons = [...strip.querySelectorAll('[role=tab]')]
    const last = buttons.at(-1)
    if (!last || last.offsetLeft + last.offsetWidth <= width) return
    const n = buttons.findIndex((b) => b.offsetLeft + b.offsetWidth > width - MORE_WIDTH)
    fit = Math.max(1, n)
  }

  $effect(() => {
    tabs.map((t) => t.text).join('|')
    measure()
  })
  $effect(() => {
    if (!strip) return
    const watch = new ResizeObserver(() => measure())
    watch.observe(strip)
    return () => watch.disconnect()
  })

  let shown = $derived(fit === Infinity ? tabs : tabs.slice(0, fit))
  let rest = $derived(fit === Infinity ? [] : tabs.slice(fit))
  let restActive = $derived(rest.some((t) => t.key === active))

  function pick(key) {
    menu = false
    onselect(key)
  }
</script>

{#snippet label(t)}
  {t.text}{#if early[t.key]}<span class="early-dot" title="Opens early in Endless from loop 2" aria-label="Opens early in Endless"></span>{/if}{#if t.door && t.door.pathing !== 'walk'}<svg class="wing" class:both={t.door.pathing === 'both'} viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="M20.5 3.5c-7 0-12 3.4-13.6 8.4-.7 2.2-.6 4.3 0 6L3 21.5l1.2 1.2 3.6-3.6c1.7.6 3.8.7 6 0 5-1.6 8.4-6.6 8.4-13.6zM7.6 17.2c-.3-1.2-.3-2.6.2-4 1.2-3.8 4.9-6.5 10.2-6.9-1.3 4-4.2 6.5-8.2 7.6l-3 .7z" /></svg>{/if}
{/snippet}

<div class="tabs" role="tablist" bind:this={strip}>
  {#each shown as t (t.key)}
    <button role="tab" class="{t.cls ?? ''}" class:on={active === t.key}
            aria-selected={active === t.key} data-key={t.key} title={t.title}
            onclick={() => onselect(t.key)}>{@render label(t)}</button>
  {/each}
  {#if rest.length}
    <span class="more">
      <button class="more-tab" class:on={restActive} aria-expanded={menu}
              aria-label="More tabs" onclick={() => (menu = !menu)}>…</button>
      {#if menu}
        <div class="scrim" onclick={() => (menu = false)} role="presentation"></div>
        <div class="list" role="listbox">
          {#each rest as t (t.key)}
            <button role="tab" class="{t.cls ?? ''}" class:on={active === t.key}
                    aria-selected={active === t.key} data-key={t.key} title={t.title}
                    onclick={() => pick(t.key)}>{@render label(t)}</button>
          {/each}
        </div>
      {/if}
    </span>
  {/if}
</div>

<style>
  .tabs {
    display: flex;
    gap: 2px;
    padding: 8px 10px 0;
    overflow: visible;
    flex: none;
    min-width: 0;
    border-bottom: 1px solid var(--edge);
    position: relative;
    z-index: 6;
  }
  .tabs button {
    border-radius: 5px 5px 0 0;
    border-bottom-color: transparent;
    background: none;
    color: var(--text-dim);
    white-space: nowrap;
  }
  .tabs button.on {
    background: var(--panel);
    color: var(--accent-ink);
    font-weight: 700;
    border-color: var(--edge);
    border-bottom-color: var(--panel);
    margin-bottom: -1px;
  }
  .tabs button.rift.on { color: var(--rift-red); }
  .tabs button.spawner.on { color: var(--spawner); }
  .more { position: relative; flex: none; }
  .more-tab { min-width: 36px; }
  .scrim { position: fixed; inset: 0; z-index: 20; }
  .list {
    position: absolute;
    right: 0;
    top: 100%;
    z-index: 21;
    display: flex;
    flex-direction: column;
    min-width: 12rem;
    max-height: 60vh;
    overflow-y: auto;
    padding: 4px;
    background: var(--panel);
    border: 1px solid var(--edge);
    border-radius: 6px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
  }
  .list button {
    border-radius: 4px;
    border: none;
    text-align: left;
    padding: 6px 10px;
  }
  .list button.on { margin-bottom: 0; background: var(--panel-2); }
  .list button:hover { background: var(--panel-2); color: var(--text); }
  .wing { margin-left: 5px; fill: currentColor; opacity: 0.9; vertical-align: middle; }
  .wing.both { opacity: 0.45; }
  .early-dot {
    display: inline-block; width: 7px; height: 7px; margin-left: 6px;
    border-radius: 50%; background: var(--warn); vertical-align: middle;
  }
</style>
