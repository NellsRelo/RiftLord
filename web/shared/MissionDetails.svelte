<script>
  import LoadPicture from './LoadPicture.svelte'
  import CampaignEditor, { campaignSendable } from './CampaignEditor.svelte'
  // A mission's details, as OrcsMustUI shows them, and where it is listed: one dialog
  // for Rift Lord's three pages -- the Map Editor and the Onslaught Editor (Details…),
  // and the Missions page (a mission's Settings). It edits a copy of `values` and
  // reports every change through `onchange`; with `onsave` it has Save and Cancel.
  //
  // values: {author, version, description, placement, endless, endlessOnly, resetDoors?, picture?,
  //          portalPicture?, newCampaign: {name, description, hub, banner},
  //          scramble?: {on, tiers: [name], rows: {tier: {difficulty?, weight?}}}}
  // scramble is the Scramble tiers the mission joins (missionscramble.py), each typed or
  // picked from the tiers scrambleTiers suggests (missionscramble.view); rows hold only
  // values that differ from the tier's default. A value saved as {tier: 'All', extra}
  // is shown as every suggested tier and extra's, and sent back unchanged until edited;
  // one saved as {tier} is shown as that tier.
  // resetDoors is carried through unchanged; the dialog no longer offers it. The mission
  // only chooses its campaign: a campaign's own settings are edited in CampaignEditor,
  // into newCampaign for a campaign the save will create (banner a BannerCrop value),
  // or through campaignTools for an installed one.
  // `picture` (loading screen) and `portalPicture` (hub portal) are LoadPicture.svelte
  // values, offered with allowPicture; a page sends them with LoadPicture's sendable().
  // campaignIndex (sent, never given) is the mission's place in the chosen campaign, as
  // typed in Campaign Index (from each campaign's `first` to its `size`,
  // customcampaign.choices), or null when it is not typed or not valid.
  // workingTitle (sent with the workingTitle prop) is its level's name in the game.
  // unlocks ({heroes, items}: Guids, offered with unlockChoices) is what beating the mission
  // grants (missionunlocks.py); values.unlocks holds the saved choices as views
  // ({guid, name, kind, label, installed}), so one whose mod is gone is still shown.

  let {
    heading = 'Mission Details',
    values,
    campaigns = [],
    // {save(key, values) -> Promise of the campaign as customcampaign.choices lists it}:
    // changes an installed custom campaign at once; without it, only a new one is edited
    campaignTools = null,
    // the mission's build lists it in a shipped campaign: changed only in its editor
    shippedLocked = false,
    // the editors may list a mission in a shipped campaign; Missions settings cannot
    allowShipped = true,
    // the loading-screen picture, and the title its preview shows
    allowPicture = false,
    title = '',
    onchange = () => {},
    onclose,
    onsave = null,
    saving = false,
    error = '',
    // a page's own fields, shown after the shared ones
    children = null,
    // the Guid field, for a page that can check one: {own, value, check(text) -> Promise
    // of /api/guid/check's answer or null, generate() -> Promise of a new Guid}
    guidTools = null,
    // the source level's own pictures, shown while the mission has none:
    // {picture, portalPicture (data URLs or null), level, title} (sourcepictures.view)
    sourcePictures = null,
    // the known Scramble tiers: {tiers: [{name, difficulty, mod, enabled, live}],
    // difficulties: [{value, label}], weight, fallback} (missionscramble.view);
    // without it Scramble is not shown
    scrambleTiers = null,
    // the Working Title field: {value, default, max, taken, shipped} (bucket.working_title_view);
    // without it the field is not shown
    workingTitle = null,
    // Campaign offers Endless: listed in the Endless hub alone (a Map Editor mission;
    // values.endlessOnly, bucket._endless_variant)
    allowEndlessVariant = false,
    // the heroes and items a mission can grant: {heroes, items: [{guid, name, kind, mod,
    // label}]} (missionunlocks.view); without it Unlocks is not shown
    unlockChoices = null,
  } = $props()

  /** LoadPicture's fallback for one slot, or null. */
  const fallbackFor = (slot) => sourcePictures?.[slot]
    ? { preview: sourcePictures[slot], from: sourcePictures.title || sourcePictures.level }
    : null

  // svelte-ignore state_referenced_locally
  let guid = $state(guidTools?.value ?? '')
  let guidProblem = $state('')

  /** What is wrong with a checked Guid, or ''. */
  function problemOf(c) {
    if (!c) return ''
    if (c.error) return c.error
    if (c.shipped?.length) return `Used by the shipped mission ${c.shipped.join(', ')}.`
    if (c.missions?.length) return `Also used by ${c.missions.join(', ')}. Install one at a time.`
    return ''
  }

  async function takeGuid() {
    const text = guid.trim().toUpperCase()
    guid = text
    guidProblem = ''
    const got = await guidTools.check(text)
    if (guid !== text) return
    if (got?.guid && !got.error) guid = got.guid
    guidProblem = problemOf(got)
  }

  async function generateGuid() {
    guid = await guidTools.generate()
    await takeGuid()
  }

  const CUSTOM_MAPS = 'CustomMaps'
  //: the Campaign choice that lists a mission in the Endless hub alone (customcampaign.ENDLESS)
  const ENDLESS = 'Endless'
  //: Scramble: a value saved as 'All' joins every listed tier
  const ALL_TIERS = 'All'
  const NEW = 'new'

  //: hub scenes OrcsMustUI can show (its Docs/HubStyle.md); '' lets the level decide
  const HUBS = [
    { value: '', label: 'Automatic (from level)' },
    { value: 'OldFriends', label: 'Old Friends' },
    { value: 'DrasticSteps', label: 'Drastic Steps' },
    { value: 'ColdAsEyes', label: 'Cold as Eyes' },
    { value: 'TippingTheScales', label: 'Tipping the Scales' },
  ]

  // a snapshot, not structuredClone: `values` may hold $state proxies (a picture), which
  // structuredClone refuses (DataCloneError) and the dialog never opened
  let v = $state($state.snapshot({
    author: '', version: '', description: '', hub: '', placement: CUSTOM_MAPS, endless: false,
    endlessOnly: false,
    ...values,
    //: a mission listed in the Endless hub alone shows Campaign Endless
    ...(values?.endlessOnly ? { placement: ENDLESS } : {}),
    newCampaign: { name: '', description: '', banner: null, hub: '',
                   ...(values?.newCampaign ?? {}) },
    scramble: editableScramble(values?.scramble),
    ...(unlockChoices ? { unlocks: {
      heroes: (values?.unlocks?.heroes ?? []).map((u) => u.guid),
      items: (values?.unlocks?.items ?? []).map((u) => u.guid) } } : {}),
  }))

  //: Unlocks: the two pickers, and the groups each lists
  const UNLOCK_SIDES = [
    { key: 'heroes', label: 'Heroes', kinds: ['Hero'] },
    { key: 'items', label: 'Items', kinds: ['Trap', 'Weapon', 'Trinket'] },
  ]
  const KIND_LABELS = { Hero: 'Heroes', Trap: 'Traps', Weapon: 'Weapons', Trinket: 'Trinkets' }
  //: what a row's mod is called when the game ships it
  const SHIPPED_LABEL = 'Base game'
  let unlockSearch = $state({ heroes: '', items: '' })
  //: every row the pickers can name, the saved ones no installed pak has included
  let unlockRows = $derived.by(() => {
    const out = {}
    for (const side of ['heroes', 'items']) {
      for (const u of values?.unlocks?.[side] ?? []) out[u.guid] = { ...u, installed: false }
      for (const u of unlockChoices?.[side] ?? []) out[u.guid] = { ...u, installed: true }
    }
    return out
  })
  /** The chosen rows of one picker, in the order chosen. */
  const chosenRows = (side) => (v.unlocks?.[side] ?? []).map((g) => unlockRows[g]).filter(Boolean)
  /** One picker's rows matching its search, by group: [{kind, rows}]. */
  function unlockGroups(side) {
    const text = unlockSearch[side.key].trim().toLowerCase()
    const match = (u) => !text || `${u.name} ${u.label ?? SHIPPED_LABEL}`.toLowerCase().includes(text)
    return side.kinds.map((kind) => ({
      kind, rows: (unlockChoices?.[side.key] ?? []).filter((u) => u.kind === kind && match(u)),
    })).filter((g) => g.rows.length)
  }
  /** Choose or drop one row. */
  function toggleUnlock(side, guid, on) {
    const now = v.unlocks[side].filter((g) => g !== guid)
    v.unlocks[side] = on ? [...now, guid] : now
  }

  /** A page's `scramble` as the dialog edits it: {on, tiers, rows, all?}; `all` holds a
   *  value saved with 'All' until the suggested tiers are known. */
  function editableScramble(s) {
    const rows = { ...(s?.rows ?? {}) }
    if (!s?.on) return { on: false, tiers: [], rows: {} }
    if (Array.isArray(s.tiers)) return { on: true, tiers: [...s.tiers], rows }
    if (s.tier && s.tier !== ALL_TIERS) return { on: true, tiers: [s.tier], rows }
    return { on: true, tiers: [], rows, all: { tier: ALL_TIERS, rows, extra: { ...(s.extra ?? {}) } } }
  }

  let knownTiers = $derived(scrambleTiers?.tiers ?? [])
  const knownTier = (name) => knownTiers.find((t) => t.name === name)
  $effect(() => {
    if (!v.scramble.all || !scrambleTiers) return
    const names = knownTiers.map((t) => t.name)
    for (const name of Object.keys(v.scramble.all.extra)) if (!names.includes(name)) names.push(name)
    v.scramble.tiers = names
  })
  //: the tier rows shown: one to fill in while the mission joins none
  let tierRows = $derived(v.scramble.tiers.length ? v.scramble.tiers : [''])
  /** A tier's suggestion text: its name, and the mod that defines it. */
  const suggestion = (t) => t.mod ? `${t.name} (${t.mod})` : t.name
  /** A typed tier: spaces collapsed; digits only are Tier_NN (missionscramble.tier_name). */
  const tierName = (text) => {
    const name = String(text ?? '').trim().split(/\s+/).join(' ').slice(0, 200)
    return /^\d+$/.test(name) ? `Tier_${name.padStart(2, '0')}` : name
  }
  /** Row `i` names `text`; its difficulty and weight go with it. */
  function nameTier(i, text) {
    const was = v.scramble.tiers[i]
    const name = tierName(text)
    const tiers = [...v.scramble.tiers]
    tiers[i] = name
    const rows = { ...v.scramble.rows }
    if (was && was !== name && rows[was] && !tiers.includes(was)) {
      if (name && !rows[name]) rows[name] = rows[was]
      delete rows[was]
    }
    v.scramble.rows = rows
    v.scramble.tiers = tiers
    v.scramble.all = null
  }
  function addTier() {
    v.scramble.tiers = [...v.scramble.tiers, '']
    v.scramble.all = null
  }
  function removeTier(i) {
    const name = v.scramble.tiers[i]
    const tiers = v.scramble.tiers.filter((_n, k) => k !== i)
    if (name && !tiers.includes(name)) {
      const rows = { ...v.scramble.rows }
      delete rows[name]
      v.scramble.rows = rows
    }
    v.scramble.tiers = tiers
    v.scramble.all = null
  }
  /** `snap` with scramble as a page keeps it: {on, tiers (named, once each), rows of
   *  those}, or the value saved with 'All' while it is unedited. */
  function settled(snap) {
    snap = { ...snap, campaignIndex: typedIndex(),
             ...(workingTitle ? { workingTitle: titleText.trim() } : {}) }
    const s = snap.scramble
    if (!s) return snap
    if (s.all) return { ...snap, scramble: { on: s.on, ...s.all } }
    const tiers = [...new Set(s.tiers.filter(Boolean))]
    const rows = Object.fromEntries(Object.entries(s.rows).filter(([n]) => tiers.includes(n)))
    return { ...snap, scramble: { on: s.on, tiers, rows } }
  }
  const defaultWeight = () => scrambleTiers?.weight ?? 10
  const defaultDifficulty = (name) => knownTier(name)?.difficulty ?? scrambleTiers?.fallback ?? 'WarMage'
  const difficultyOf = (name) => v.scramble.rows[name]?.difficulty ?? defaultDifficulty(name)
  const weightOf = (name) => v.scramble.rows[name]?.weight ?? defaultWeight()

  /** One tier's difficulty or weight; a tier's default stores nothing. */
  function setTier(name, key, value, fallback) {
    const row = { ...(v.scramble.rows[name] ?? {}) }
    if (value === fallback || value === '' || value === null || Number.isNaN(value)) delete row[key]
    else row[key] = value
    const rows = { ...v.scramble.rows }
    if (Object.keys(row).length) rows[name] = row
    else delete rows[name]
    v.scramble.rows = rows
    v.scramble.all = null
  }

  $effect(() => { onchange(settled($state.snapshot(v))) })

  //: installed campaigns as changed through campaignTools since the dialog opened, by name
  let edited = $state({})
  let listed = $derived(campaigns.map((c) => edited[c.name] ?? c))
  let choices = $derived(listed.filter((c) => c.name === v.placement
    || (c.kind === 'endless' ? allowEndlessVariant : allowShipped || c.kind !== 'shipped')))
  let chosen = $derived(listed.find((c) => c.name === v.placement))
  let blank = $derived(!['author', 'version', 'description'].every((k) => String(v[k] ?? '').trim()))
  //: the campaign open in CampaignEditor: {heading, values, save(values), cancel()}
  let editor = $state(null)
  let editable = $derived(v.placement === NEW ||
    (chosen?.kind === 'custom' && chosen.own !== false && !!campaignTools))

  //: Campaign Index as typed; '' shows the mission's place in the chosen campaign
  let indexText = $state('')
  //: the chosen campaign when it takes a Campaign Index: {first, size, index}
  let indexRange = $derived(chosen && ['custom', 'shipped'].includes(chosen.kind)
    && (chosen.size ?? 0) > (chosen.first ?? 1) ? chosen : null)
  let indexShown = $derived(indexText !== '' ? indexText
    : String(indexRange?.index ?? indexRange?.size ?? ''))
  let indexProblem = $derived(indexRange ? indexProblemOf(indexShown, indexRange) : '')
  /** What is wrong with a Campaign Index (customcampaign.index_problem), or ''. */
  function indexProblemOf(text, r) {
    const first = r.first ?? 1
    const t = String(text).trim()
    if (!/^\d+$/.test(t) || Number(t) < first || Number(t) > r.size) {
      return r.size <= first ? `Campaign Index is ${first}.`
        : `Campaign Index is a whole number from ${first} to ${r.size}.`
    }
    return ''
  }
  /** The typed Campaign Index as a number, or null. */
  const typedIndex = () => indexRange && indexText.trim() !== '' && !indexProblem
    ? Number(indexText.trim()) : null

  // svelte-ignore state_referenced_locally
  let titleText = $state(workingTitle?.value ?? '')
  let titleProblem = $derived(workingTitle ? titleProblemOf(titleText.trim()) : '')
  /** What is wrong with a Working Title (leveltitle.problems), or ''. */
  function titleProblemOf(text) {
    if (!text || text === workingTitle.default) return ''
    const low = text.toLowerCase()
    if (text.length > workingTitle.max) return `Working Title is at most ${workingTitle.max} characters.`
    if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(text)) {
      return 'Working Title takes letters, digits and underscores, starting with a letter.'
    }
    if (low === 'none') return 'Working Title cannot be None.'
    if (workingTitle.shipped?.[low]) return `${workingTitle.shipped[low]} is a shipped level.`
    if (workingTitle.taken?.[low]) return `${text} is the level of ${workingTitle.taken[low]}.`
    return ''
  }

  /** The Campaign picker changed: a new campaign is named and set up straight away. */
  function pick(value) {
    const was = v.placement
    indexText = ''
    v.placement = value
    //: Campaign Endless: listed in the Endless hub alone (values.endlessOnly)
    v.endlessOnly = value === ENDLESS
    if (value === NEW && !v.newCampaign.name.trim()) editNew(was)
  }

  /** The campaign the save will create, in CampaignEditor; cancelled unnamed, the
   *  picker goes back to `was`. */
  function editNew(was) {
    editor = {
      heading: 'New Campaign',
      values: { ...$state.snapshot(v.newCampaign) },
      save: async (got) => {
        v.newCampaign = { ...v.newCampaign, name: got.name, description: got.description,
                          hub: got.hub, ...('banner' in got ? { banner: got.banner } : {}) }
        editor = null
      },
      cancel: () => {
        if (!v.newCampaign.name.trim()) v.placement = was
        editor = null
      },
    }
  }

  /** The chosen installed campaign in CampaignEditor, saved through campaignTools. */
  function editInstalled() {
    const c = chosen
    editor = {
      heading: 'Campaign Settings',
      values: { name: c.label, description: c.description ?? '', hub: c.hub ?? '',
                banner: c.bannerSource ?? null },
      save: async (got) => {
        const after = await campaignTools.save(c.name.slice('custom:'.length), campaignSendable(got))
        edited = { ...edited, [c.name]: after }
        editor = null
      },
      cancel: () => { editor = null },
    }
  }

  const label = (c) => c.kind === NEW && v.newCampaign.name.trim()
    ? `New Campaign: ${v.newCampaign.name.trim()}`
    : c.kind === 'custom' ? `${c.label} (installed)` : c.label
</script>

<svelte:window onkeydown={(e) => { if (e.key === 'Escape' && !editor) onclose() }} />

<div class="md-scrim" onclick={onclose} role="presentation"></div>
<div class="md-modal" role="dialog" aria-label={heading}>
  <header>
    <h3>{heading}</h3>
    <button class="md-ghost" onclick={onclose} aria-label="Close">✕</button>
  </header>
  <div class="md-body">
    <h4>Details</h4>
    <label><span>Author</span><input bind:value={v.author} placeholder="Your name" /></label>
    <label><span>Version</span><input bind:value={v.version} placeholder="1.0" /></label>
    <label class="tall"><span>Description</span>
      <textarea bind:value={v.description} rows="5"
                placeholder="What the map is, and what to expect."></textarea></label>
    {#if blank}
      <p class="md-warn">No mission details set. The Mission description panel will be empty!</p>
    {/if}
    <label><span>Hub Style</span>
      <select bind:value={v.hub}>
        {#each HUBS as h}<option value={h.value}>{h.label}</option>{/each}
      </select></label>
    {#if guidTools}
      <label><span>Guid</span>
        <input class="md-mono" bind:value={guid} onchange={takeGuid} spellcheck="false"
               maxlength="40" placeholder="Generated when first saved" />
        <button class="md-ghost md-gen" onclick={generateGuid}>Generate</button></label>
      {#if guidProblem}<p class="md-error">{guidProblem}</p>{/if}
    {/if}
    {#if workingTitle}
      <label><span>Working Title</span>
        <input class="md-mono" bind:value={titleText} spellcheck="false"
               placeholder={workingTitle.default} aria-label="Working Title" /></label>
      {#if titleProblem}<p class="md-error">{titleProblem}</p>{/if}
    {/if}

    {#if allowPicture}
      <h4>Loading Screen</h4>
      <LoadPicture value={values?.picture ?? null} {title} fallback={fallbackFor('picture')}
                   onchange={(p) => { v.picture = p }} />
      <h4>Hub Portal</h4>
      <LoadPicture kind="portal" value={values?.portalPicture ?? null}
                   fallback={fallbackFor('portalPicture')}
                   onchange={(p) => { v.portalPicture = p }} />
    {/if}

    <h4>Campaign</h4>
    {#if shippedLocked}
      <p class="md-hint">Listed in <b>{chosen?.label ?? v.placement}</b>, a shipped campaign. That
        is part of the mission's build, so change it in the editor it was made in.</p>
    {:else}
      <label><span>Campaign</span>
        <select value={v.placement} onchange={(e) => pick(e.currentTarget.value)}
                aria-label="Campaign">
          {#each choices as c}
            <option value={c.name}>{label(c)}</option>
          {/each}
        </select>
        {#if editable}
          <button class="md-ghost md-gen" aria-label="Edit Campaign"
                  onclick={() => v.placement === NEW ? editNew(v.placement) : editInstalled()}>Edit</button>
        {/if}</label>
      {#if indexRange}
        <label><span>Campaign Index</span>
          <input class="md-index" value={indexShown} inputmode="numeric" spellcheck="false"
                 aria-label="Campaign Index"
                 oninput={(e) => { indexText = e.currentTarget.value }} /></label>
        {#if indexProblem}<p class="md-error">{indexProblem}</p>{/if}
      {/if}
      {#if v.placement === NEW && !v.newCampaign.name.trim()}
        <p class="md-error">Name the new campaign.</p>
      {/if}
      {#if v.placement === CUSTOM_MAPS}
        <p class="md-hint">The Custom Maps and New Campaign options require OrcsMustUI to be
          installed for the mission to be reachable.</p>
      {:else if v.placement === NEW}
        <p class="md-hint">Adding a new campaign hub will require OrcsMustUI to be installed.</p>
      {/if}
      {#if chosen?.kind === 'shipped'}
        <p class="md-warn">Listing a mission in a shipped campaign patches the game's campaign
          table. Only one mod that does so can be installed at a time.</p>
      {/if}
      {#if v.placement !== ENDLESS}
        <label class="check"><input type="checkbox" bind:checked={v.endless} />
          <span>Add to Endless</span></label>
      {/if}
    {/if}
    {#if scrambleTiers}
      <label class="check"><input type="checkbox" bind:checked={v.scramble.on} />
        <span>Add to Scramble</span></label>
      {#if v.scramble.on}
        <datalist id="md-scramble-tiers">
          {#each knownTiers as t}<option value={t.name}>{suggestion(t)}</option>{/each}
        </datalist>
        <div class="md-tiers">
          <div class="md-tier md-tier-head">
            <span>Tier</span><span>Difficulty</span><span>Weight</span><span></span></div>
          {#each tierRows as name, i (i)}
            <div class="md-tier" data-tier={name}>
              <input list="md-scramble-tiers" aria-label="Tier" value={name} maxlength="200"
                     spellcheck="false" onchange={(e) => nameTier(i, e.currentTarget.value)} />
              <select aria-label="{name} Difficulty" value={difficultyOf(name)} disabled={!name}
                      onchange={(e) => setTier(name, 'difficulty', e.currentTarget.value,
                                               defaultDifficulty(name))}>
                {#each scrambleTiers.difficulties as d}<option value={d.value}>{d.label}</option>{/each}
              </select>
              <input type="number" min="0.1" step="any" aria-label="{name} Weight" disabled={!name}
                     value={weightOf(name)}
                     onchange={(e) => setTier(name, 'weight', e.currentTarget.valueAsNumber > 0
                       ? e.currentTarget.valueAsNumber : null, defaultWeight())} />
              <button class="md-ghost md-x" aria-label="Remove {name || 'Tier'}"
                      onclick={() => removeTier(i)}>✕</button>
            </div>
          {/each}
        </div>
        <button class="md-ghost md-gen md-add" aria-label="Add Tier" onclick={addTier}>Add Tier</button>
      {/if}
    {/if}
    {#if unlockChoices && v.placement !== ENDLESS}
      <h4>Unlocks</h4>
      {#each UNLOCK_SIDES as side (side.key)}
        <div class="md-unlocks" data-unlocks={side.key}>
          <label><span>{side.label}</span>
            <input type="search" aria-label="Search {side.label}" placeholder="Search"
                   bind:value={unlockSearch[side.key]} spellcheck="false" /></label>
          {#if chosenRows(side.key).length}
            <div class="md-chips">
              {#each chosenRows(side.key) as u (u.guid)}
                <span class="md-chip" class:md-gone={!u.installed} data-guid={u.guid}>
                  {u.name}<small>{u.label ?? SHIPPED_LABEL}{u.installed ? '' : ', not installed'}</small>
                  <button class="md-ghost md-x" aria-label="Remove {u.name}"
                          onclick={() => toggleUnlock(side.key, u.guid, false)}>✕</button>
                </span>
              {/each}
            </div>
          {/if}
          <div class="md-pick" role="group" aria-label="{side.label} to unlock">
            {#each unlockGroups(side) as g (g.kind)}
              {#if side.kinds.length > 1}<div class="md-group">{KIND_LABELS[g.kind]}</div>{/if}
              {#each g.rows as u (u.guid)}
                <label class="md-option" data-guid={u.guid}>
                  <input type="checkbox" checked={v.unlocks[side.key].includes(u.guid)}
                         onchange={(e) => toggleUnlock(side.key, u.guid, e.currentTarget.checked)} />
                  <span>{u.name}</span><small>{u.label ?? SHIPPED_LABEL}</small></label>
              {/each}
            {:else}
              <p class="md-none">No match.</p>
            {/each}
          </div>
        </div>
      {/each}
    {/if}
    {@render children?.()}
  </div>
  {#if error}<p class="md-error md-pad" role="alert">{error}</p>{/if}
  {#if onsave}
    <footer>
      <button class="md-ghost" onclick={onclose}>Cancel</button>
      <button class="md-primary" onclick={() => onsave(settled($state.snapshot(v)))}
              disabled={saving || !!indexProblem || !!titleProblem}>
        {saving ? 'Saving…' : 'Save settings'}
      </button>
    </footer>
  {/if}
</div>

{#if editor}
  <CampaignEditor heading={editor.heading} values={editor.values}
                  onsave={editor.save} onclose={editor.cancel} />
{/if}

<style>
  /* Colours come from the page; the Map Editor names its tokens differently. */
  .md-modal {
    --md-panel: var(--panel, #1b1f27);
    --md-field: var(--panel-2, var(--field, #222731));
    --md-edge: var(--edge, var(--line, #2c323d));
    --md-text: var(--text, var(--ink, #e6eaf2));
    --md-dim: var(--text-dim, var(--dim, #8e97a6));
    position: fixed; z-index: 1001; top: 8%; left: 50%; transform: translateX(-50%);
    width: min(36rem, calc(100vw - 32px)); max-height: 84vh; display: flex;
    flex-direction: column; background: var(--md-panel); color: var(--md-text);
    border: 1px solid var(--md-edge); border-radius: 8px;
    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.45); font: 13px/1.5 'Segoe UI', system-ui, sans-serif;
  }
  .md-scrim { position: fixed; inset: 0; z-index: 1000; background: rgba(0, 0, 0, 0.45); }
  header { display: flex; align-items: center; padding: 10px 12px 10px 16px;
           border-bottom: 1px solid var(--md-edge); }
  h3 { flex: 1; margin: 0; font-size: 14px; font-weight: 650; color: var(--hot, #dc50c8); }
  h4 { font-size: 12px; margin: 16px 0 4px; text-transform: uppercase; letter-spacing: 0.06em;
       color: var(--md-dim); font-weight: 600; }
  h4:first-child { margin-top: 0; }
  .md-body { padding: 12px 16px; overflow: auto; }
  label { display: flex; align-items: center; gap: 8px; margin: 6px 0; }
  label.tall { align-items: flex-start; }
  label > span { width: 6.5rem; flex: none; color: var(--md-dim); }
  label.tall > span { padding-top: 5px; }
  label.check { margin-top: 12px; }
  label.check > span { width: auto; color: var(--md-text); }
  input:not([type=checkbox]):not([type=file]), textarea, select {
    flex: 1; min-width: 0; width: auto; font: inherit; color: var(--md-text);
    background: var(--md-field); border: 1px solid var(--md-edge); border-radius: 5px;
    padding: 5px 7px; margin: 0;
  }
  input[type=checkbox] { width: 15px; height: 15px; margin: 0; accent-color: var(--accent, #a05ac0); }
  textarea { resize: vertical; }
  .md-hint { font-size: 12px; color: var(--md-dim); margin: 4px 0; }
  .md-warn { font-size: 12px; color: var(--warn, #c98a26); margin: 4px 0; }
  .md-error { color: var(--bad, #e05252); font-size: 12px; margin: 4px 0; }
  .md-pad { padding: 0 16px 8px; }
  input.md-mono { font: 11px/1.4 ui-monospace, monospace; }
  input.md-index:not([type=checkbox]):not([type=file]) { flex: none; width: 5.5rem; }
  .md-tiers { margin: 6px 0; }
  .md-tier { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) 5.5rem 1.75rem;
             gap: 8px; align-items: center; margin: 4px 0; }
  .md-tier > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .md-tier-head { color: var(--md-dim); font-size: 12px; }
  .md-gen { flex: none; border-color: var(--md-edge); }
  .md-add { margin: 2px 0 6px; }
  .md-x { padding: 0 6px; color: var(--md-dim); }
  .md-unlocks { margin: 4px 0 10px; }
  .md-chips { display: flex; flex-wrap: wrap; gap: 4px; margin: 4px 0 4px calc(6.5rem + 8px); }
  .md-chip { display: inline-flex; align-items: center; gap: 4px; padding: 1px 2px 1px 8px;
             border: 1px solid var(--md-edge); border-radius: 12px; background: var(--md-field); }
  .md-chip small, .md-option small { color: var(--md-dim); font-size: 11px; }
  .md-chip.md-gone { border-color: var(--warn, #c98a26); }
  .md-pick { max-height: 11rem; overflow: auto; margin-left: calc(6.5rem + 8px);
             border: 1px solid var(--md-edge); border-radius: 5px; padding: 2px 6px; }
  .md-group { font-size: 11px; color: var(--md-dim); text-transform: uppercase;
              letter-spacing: 0.05em; margin: 6px 0 2px; }
  label.md-option { margin: 2px 0; gap: 6px; }
  label.md-option > span { width: auto; flex: 1; color: var(--md-text); }
  .md-none { font-size: 12px; color: var(--md-dim); margin: 4px 0; }
  footer { display: flex; justify-content: flex-end; gap: 8px; padding: 10px 16px;
           border-top: 1px solid var(--md-edge); }
  button { font: inherit; cursor: pointer; border-radius: 6px; padding: 5px 12px; width: auto;
           margin: 0; }
  .md-ghost { background: none; border: 1px solid transparent; color: var(--md-text); }
  .md-ghost:hover { border-color: var(--md-edge); }
  .md-primary { background: var(--accent, #9450b4); border: 1px solid var(--accent, #9450b4);
                color: #fff; font-weight: 600; }
  .md-primary:disabled { opacity: 0.5; cursor: default; }
</style>
