<script>
  // The hub's enemy preview for one mission (makepreview): which enemies stand in it and
  // where, seen from above with the menu camera at the bottom. The counts and each unit's
  // Auto / Always / Never are the mission's preview options; the picture follows every
  // change. `load(options)` answers with the preview for those options (bucket.preview_view);
  // `options` are the ones saved; `onchange(options)` hears every change.
  import { onMount } from 'svelte'
  let { load, options = {}, onchange } = $props()

  // svelte-ignore state_referenced_locally
  let opts = $state({ ground: options?.ground ?? '', flyers: options?.flyers ?? '',
                      include: [...(options?.include ?? [])],
                      exclude: [...(options?.exclude ?? [])] })
  let view = $state(null)
  let error = $state('')
  let busy = $state(false)
  let timer = null
  //: only the newest answer is shown
  let asked = 0

  function mode(unit) {
    return opts.include.includes(unit) ? 'always' : opts.exclude.includes(unit) ? 'never' : 'auto'
  }

  function setMode(unit, m) {
    opts.include = opts.include.filter((u) => u !== unit)
    opts.exclude = opts.exclude.filter((u) => u !== unit)
    if (m === 'always') opts.include.push(unit)
    if (m === 'never') opts.exclude.push(unit)
    changed()
  }

  function sendable() {
    const out = {}
    if (opts.ground !== '' && opts.ground !== null) out.ground = Number(opts.ground)
    if (opts.flyers !== '' && opts.flyers !== null) out.flyers = Number(opts.flyers)
    if (opts.include.length) out.include = [...opts.include]
    if (opts.exclude.length) out.exclude = [...opts.exclude]
    return out
  }

  async function refresh() {
    const mine = ++asked
    busy = true
    try {
      const got = await load(sendable())
      if (mine !== asked) return
      view = got && got.units ? got : null
      error = ''
    } catch (e) {
      if (mine === asked) error = e.message
    } finally {
      if (mine === asked) busy = false
    }
  }

  function changed() {
    onchange?.(sendable())
    clearTimeout(timer)
    timer = setTimeout(refresh, 300)
  }

  onMount(refresh)
</script>

<h4>Enemy Preview</h4>
{#if error}
  <p class="ep-hint ep-bad">{error}</p>
{:else if !view}
  <p class="ep-hint">{busy ? 'Drawing the preview…' : 'The preview appears once the onslaught has waves.'}</p>
{:else}
  <div class="ep-picture" class:ep-busy={busy}>
    {#if view.image}
      <img src={view.image} alt="The preview's formation from above" />
    {:else}
      <p class="ep-hint">No enemy in this mission has preview art.</p>
    {/if}
  </div>
  <p class="ep-hint">Seen from above, with the menu's camera at the bottom. Dashed circles fly.
    Showing {view.showing.length} of {view.units.length} enemy types.</p>
  <div class="ep-counts">
    <label><span>Ground</span>
      <input type="number" min="0" max={view.defaults.maxGround} placeholder={String(view.defaults.ground)}
             bind:value={opts.ground} oninput={changed} /></label>
    <label><span>Flying</span>
      <input type="number" min="0" max={view.defaults.maxFlyers} placeholder={String(view.defaults.flyers)}
             bind:value={opts.flyers} oninput={changed} /></label>
  </div>
  <div class="ep-units">
    {#each view.units as u (u.unit)}
      <div class="ep-unit" class:ep-shown={u.creature && view.showing.includes(u.creature)}>
        <span class="ep-name" title={u.creature ?? 'no preview art'}>{u.label ?? u.unit}{u.flyer ? ' (flies)' : ''}</span>
        <span class="ep-count">{u.count}</span>
        <select value={mode(u.unit)} disabled={!u.art} onchange={(e) => setMode(u.unit, e.currentTarget.value)}>
          <option value="auto">Auto</option>
          <option value="always">Always</option>
          <option value="never">Never</option>
        </select>
      </div>
    {/each}
  </div>
  <p class="ep-hint">Auto picks one enemy per family first, the most spawned first, plus a big
    one and a boss when the mission has them. Shown enemies are highlighted; the number is how
    many spawn over the whole onslaught.</p>
{/if}

<style>
  h4 { font-size: 12px; margin: 0 0 4px; text-transform: uppercase; letter-spacing: 0.06em;
       color: var(--md-dim, var(--text-dim, #8e97a6)); font-weight: 600; }
  .ep-picture { border: 1px solid var(--md-edge, var(--edge, #2c323d)); border-radius: 6px;
                overflow: hidden; background: #1e1a26; line-height: 0; transition: opacity 0.15s;
                max-width: 40rem; }
  .ep-picture img { width: 100%; height: auto; display: block; }
  .ep-busy { opacity: 0.55; }
  .ep-hint { font-size: 12px; color: var(--md-dim, var(--text-dim, #8e97a6)); margin: 4px 0;
             line-height: 1.5; }
  .ep-bad { color: var(--bad, #e05252); }
  .ep-counts { display: flex; gap: 16px; max-width: 40rem; }
  .ep-counts label { display: flex; align-items: center; gap: 8px; margin: 6px 0; flex: 1; }
  .ep-counts label > span { color: var(--md-dim, var(--text-dim, #8e97a6)); }
  .ep-counts input { width: 5rem; flex: none; font: inherit;
                     color: var(--md-text, var(--text, #e6eaf2));
                     background: var(--md-field, var(--panel-2, #222731));
                     border: 1px solid var(--md-edge, var(--edge, #2c323d));
                     border-radius: 5px; padding: 4px 6px; }
  .ep-units { max-height: 22rem; overflow: auto; max-width: 40rem;
              border: 1px solid var(--md-edge, var(--edge, #2c323d)); border-radius: 6px; }
  .ep-unit { display: flex; align-items: center; gap: 8px; padding: 3px 8px;
             border-bottom: 1px solid var(--md-edge, var(--edge, #2c323d)); }
  .ep-unit:last-child { border-bottom: none; }
  .ep-shown { background: color-mix(in srgb, var(--accent, #9450b4) 18%, transparent); }
  .ep-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ep-count { color: var(--md-dim, var(--text-dim, #8e97a6)); font-variant-numeric: tabular-nums;
              width: 3rem; text-align: right; }
  .ep-unit select { font: inherit; font-size: 12px; color: var(--md-text, var(--text, #e6eaf2));
                    background: var(--md-field, var(--panel-2, #222731));
                    border: 1px solid var(--md-edge, var(--edge, #2c323d));
                    border-radius: 5px; padding: 2px 4px; }
</style>
