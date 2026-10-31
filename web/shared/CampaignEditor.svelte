<script module>
  /** What onsave gives, as the server takes an installed campaign's change
   *  (bucket.update_campaign): a new banner picture with its crops, {crop, cardCrop} for
   *  the same picture cropped again, null to remove it, no banner key to keep it. */
  export function campaignSendable (got) {
    const sent = { name: got.name, description: got.description, hub: got.hub }
    if ('banner' in got) {
      const b = got.banner
      const crops = { crop: b?.crop, ...(b?.cardCrop ? { cardCrop: b.cardCrop } : {}) }
      sent.banner = !b ? null : b.data ? { name: b.name, data: b.data, ...crops } : crops
    }
    return sent
  }
</script>

<script>
  import BannerCrop from './BannerCrop.svelte'
  // A custom campaign's own settings -- its name (the hub tab's title), description,
  // hub scene and banner -- in a dialog over whichever page opened it: the Missions
  // page's campaign heading, or the mission Details dialog's Campaign picker.
  //
  // values: {name, description, hub, banner} with banner a BannerCrop value or null.
  // onsave(values) may return a Promise; a rejection's message is shown and the dialog
  // stays open.

  let { heading = 'Campaign Settings', values = {}, onsave, onclose } = $props()

  //: hub scenes OrcsMustUI can show for a campaign (its Docs/HubStyle.md); '' follows
  //: the campaign's first mission
  const HUBS = [
    { value: '', label: 'Automatic (first mission)' },
    { value: 'OldFriends', label: 'Old Friends' },
    { value: 'DrasticSteps', label: 'Drastic Steps' },
    { value: 'ColdAsEyes', label: 'Cold as Eyes' },
    { value: 'TippingTheScales', label: 'Tipping the Scales' },
  ]

  // svelte-ignore state_referenced_locally
  let v = $state({ name: values.name ?? '', description: values.description ?? '',
                   hub: values.hub ?? '' })
  // svelte-ignore state_referenced_locally
  let banner = values.banner ?? null
  let bannerChanged = false
  let saving = $state(false)
  let error = $state('')

  async function save () {
    if (!v.name.trim()) { error = 'Name the campaign.'; return }
    saving = true
    error = ''
    try {
      await onsave({ ...$state.snapshot(v), name: v.name.trim(),
                     ...(bannerChanged ? { banner } : {}) })
    } catch (e) {
      error = e.message
    } finally {
      saving = false
    }
  }
</script>

<svelte:window onkeydown={(e) => { if (e.key === 'Escape') { e.stopImmediatePropagation(); onclose() } }} />

<div class="ce-scrim" onclick={onclose} role="presentation"></div>
<div class="ce-modal" role="dialog" aria-label={heading}>
  <header>
    <h3>{heading}</h3>
    <button class="ce-ghost" onclick={onclose} aria-label="Close">✕</button>
  </header>
  <div class="ce-body">
    <label><span>Name</span>
      <input bind:value={v.name} class:ce-bad={!v.name.trim()} aria-label="Campaign name" /></label>
    <label class="tall"><span>Description</span>
      <textarea bind:value={v.description} rows="2" aria-label="Campaign description"></textarea></label>
    <label><span>Hub Style</span>
      <select bind:value={v.hub} aria-label="Campaign hub style">
        {#each HUBS as h}<option value={h.value}>{h.label}</option>{/each}
      </select></label>
    <h4>Campaign Banner</h4>
    <BannerCrop value={banner} onchange={(b) => { banner = b; bannerChanged = true }} />
  </div>
  {#if error}<p class="ce-error" role="alert">{error}</p>{/if}
  <footer>
    <button class="ce-ghost" onclick={onclose}>Cancel</button>
    <button class="ce-primary" onclick={save} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
  </footer>
</div>

<style>
  /* Colours come from the page; the Map Editor names its tokens differently. */
  .ce-modal {
    --md-panel: var(--panel, #1b1f27);
    --md-field: var(--panel-2, var(--field, #222731));
    --md-edge: var(--edge, var(--line, #2c323d));
    --md-text: var(--text, var(--ink, #e6eaf2));
    --md-dim: var(--text-dim, var(--dim, #8e97a6));
    position: fixed; z-index: 1003; top: 6%; left: 50%; transform: translateX(-50%);
    width: min(34rem, calc(100vw - 32px)); max-height: 88vh; display: flex;
    flex-direction: column; background: var(--md-panel); color: var(--md-text);
    border: 1px solid var(--md-edge); border-radius: 8px;
    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5); font: 13px/1.5 'Segoe UI', system-ui, sans-serif;
  }
  .ce-scrim { position: fixed; inset: 0; z-index: 1002; background: rgba(0, 0, 0, 0.35); }
  header { display: flex; align-items: center; padding: 10px 12px 10px 16px;
           border-bottom: 1px solid var(--md-edge); }
  h3 { flex: 1; margin: 0; font-size: 14px; font-weight: 650; color: var(--hot, #dc50c8); }
  h4 { font-size: 12px; margin: 16px 0 4px; text-transform: uppercase; letter-spacing: 0.06em;
       color: var(--md-dim); font-weight: 600; }
  .ce-body { padding: 12px 16px; overflow: auto; }
  label { display: flex; align-items: center; gap: 8px; margin: 6px 0; }
  label.tall { align-items: flex-start; }
  label > span { width: 6.5rem; flex: none; color: var(--md-dim); }
  label.tall > span { padding-top: 5px; }
  input:not([type=file]), textarea, select {
    flex: 1; min-width: 0; width: auto; font: inherit; color: var(--md-text);
    background: var(--md-field); border: 1px solid var(--md-edge); border-radius: 5px;
    padding: 5px 7px; margin: 0;
  }
  textarea { resize: vertical; }
  input.ce-bad { outline: 1px solid var(--bad, #e05252); }
  .ce-error { color: var(--bad, #e05252); font-size: 12px; margin: 0; padding: 0 16px 8px; }
  footer { display: flex; justify-content: flex-end; gap: 8px; padding: 10px 16px;
           border-top: 1px solid var(--md-edge); }
  button { font: inherit; cursor: pointer; border-radius: 6px; padding: 5px 12px; width: auto;
           margin: 0; }
  .ce-ghost { background: none; border: 1px solid transparent; color: var(--md-text); }
  .ce-ghost:hover { border-color: var(--md-edge); }
  .ce-primary { background: var(--accent, #9450b4); border: 1px solid var(--accent, #9450b4);
                color: #fff; font-weight: 600; }
  .ce-primary:disabled { opacity: 0.5; cursor: default; }
</style>
