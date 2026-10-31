<script>
  // The art packs in the user's Epic vault and their Fab Kits: each pack that makes level
  // pieces, converted by the Fab converter into a library the Map Editor lists
  // (riftlord/app/fabkits.py). Converts wait in the server's queue and run one at a time.
  import Icon from './Icon.svelte'
  import { fabState, fabVault, fabConvert, fabMeasure, fabCancel, fabDelete, fabMove,
           saveFabSettings } from './api.js'
  import { isNative, pickFile, pickFolder } from './native.js'

  let { active, info, onchanged, onlog } = $props()

  let vault = $state(null)
  let jobs = $state({})
  //: the packs waiting to convert, in order (fabkits.queue_view)
  let queue = $state([])
  let serverOff = $state(null)
  let message = $state(null)          // {tone, text}
  let loading = $state(false)
  //: {item: the Scale field's text}
  let scales = $state({})
  //: {item: [evidence lines]} once measured in this page
  let evidence = $state({})
  //: the vault row whose Delete is being confirmed, or null
  let confirming = $state(null)
  let deleting = $state(false)
  //: how often running jobs and the queue are read, in milliseconds
  const JOB_POLL_MS = 700

  const STAGES = { scan: 'Scan', materials: 'Materials', bake: 'Bake', textures: 'Textures',
                   meshes: 'Meshes', pack: 'Pack', checks: 'Checks' }
  //: Row states: label, badge tone, place in a Status sort, and the action button.
  const STATUS = {
    running: { label: 'Converting', tone: 'busy', rank: 0, action: 'Cancel' },
    queued: { label: 'Queued', tone: 'queued', rank: 1, action: 'Cancel' },
    failed: { label: 'Failed', tone: 'bad', rank: 2, action: 'Retry' },
    stale: { label: 'Update available', tone: 'warn', rank: 3, action: 'Update' },
    none: { label: 'Not converted', tone: 'dim', rank: 4, action: 'Convert' },
    current: { label: 'Ready', tone: 'ok', rank: 5, action: null },
  }
  //: Vault pack kinds that make level pieces (fabkits.PIECE_KINDS), with their Type label.
  const KINDS = { building: 'Buildings', props: 'Props', mixed: 'Mixed' }

  let off = $derived(info?.native?.fab || serverOff)
  //: what collision cooking needs here, when that is why conversion is off (omd3physx.NEEDS)
  let needs = $state(null)
  let current = $derived(Object.values(jobs).find((j) => j.kind === 'convert' && j.state === 'running') || null)
  let anyRunning = $derived(Object.values(jobs).some((j) => j.state === 'running') || queue.length > 0)
  //: a loose pack (a .zip or folder of models) is named by its absolute path
  const isPath = (item) => /^(\/|[A-Za-z]:[\\/])/.test(item || '')
  const baseName = (path) => path.replace(/[\\/]+$/, '').split(/[\\/]/).pop()
  //: a pack that makes level pieces: any loose pack; a vault pack by its kind, or by
  //: having meshes when the converter gives no kind
  const makesPieces = (row) => row.source === 'file' ||
    (row.kind != null ? row.kind in KINDS : row.meshes > 0)
  let showKinds = $derived((vault?.items || []).some((i) => i.kind != null))

  //: the sort: {key, dir} (1 ascending, -1 descending), remembered in this browser
  const SORT_STORE = 'riftlord-fab-sort'
  let sort = $state(readSort())
  function readSort() {
    try {
      const got = JSON.parse(localStorage.getItem(SORT_STORE) || 'null')
      if (got && typeof got.key === 'string' && (got.dir === 1 || got.dir === -1)) return got
    } catch { /* no storage */ }
    return { key: 'name', dir: 1 }
  }
  function sortBy(key) {
    sort = { key, dir: sort.key === key ? -sort.dir : 1 }
    try { localStorage.setItem(SORT_STORE, JSON.stringify(sort)) } catch { /* no storage */ }
  }
  const COLUMNS = $derived([['name', 'Name'], ...(showKinds ? [['kind', 'Type']] : []),
    ['engine', 'Engine'], ['meshes', 'Meshes'], ['scale', 'Scale'], ['status', 'Status']])
  const sortValue = {
    name: (r) => r.title.toLowerCase(),
    kind: (r) => KINDS[r.kind] || '',
    engine: (r) => r.engine || '',
    meshes: (r) => r.meshes ?? -1,
    scale: (r) => Number(scales[r.item] ?? r.scale) || 0,
    status: (r) => statusOf(r).rank,
  }
  function compare(a, b) {
    const f = sortValue[sort.key] || sortValue.name
    const x = f(a)
    const y = f(b)
    const c = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y))
    return c * sort.dir || a.title.localeCompare(b.title)
  }

  let rows = $derived.by(() => {
    const items = [...(vault?.items || [])]
    const listed = new Set(items.map((i) => i.item))
    // a loose pack's first convert, until the vault lists it
    const loose = [...Object.values(jobs).filter((j) => j.kind === 'convert'), ...queue]
    for (const j of loose) {
      if (isPath(j.item) && !listed.has(j.item)) {
        listed.add(j.item)
        items.push({ item: j.item, path: j.item, title: baseName(j.item), pack_id: j.packId || '',
                     engine: null, meshes: null, source: 'file', exists: true, scale: j.scale ?? 1,
                     scale_source: 'registry', running: false, library: { status: 'none' } })
      }
    }
    return items.filter(makesPieces).sort(compare)
  })

  //: the Add Pack from File dialog: {path, scale, measuring item, evidence}, or null
  let adding = $state(null)

  function openAdd() {
    adding = { path: '', scale: '1', measured: null, evidence: null, error: null }
  }

  async function chooseSource(kind) {
    const got = kind === 'file' ? await pickFile('Add Pack from File', 'zip')
      : await pickFolder('Add Pack from File')
    if (got) adding = { ...adding, path: got, evidence: null, error: null }
  }

  async function measureAdd() {
    try {
      const got = await fabMeasure(adding.path.trim())
      adding = { ...adding, measured: got.item, error: null }
      jobs = { ...jobs, [got.item]: got }
    } catch (e) {
      adding = { ...adding, error: e.message }
    }
  }

  async function convertAdd() {
    const value = Number(adding.scale)
    if (!(value > 0)) return (adding = { ...adding, error: 'The scale must be a number above 0.' })
    try {
      const got = await fabConvert(adding.path.trim(), value)
      take(got)
      scales = { ...scales, [got.item]: String(value) }
      adding = null
    } catch (e) {
      adding = { ...adding, error: e.message }
    }
  }

  /** A convert or cancel's answer: the item's job and the queue. */
  function take(got) {
    if (got.job) jobs = { ...jobs, [got.item]: got.job }
    if (got.queue) queue = got.queue
  }

  //: when the scan behind the vault list on screen started (ms since the epoch)
  let vaultAt = $state(0)
  //: the vault scan running on the server: {pid, done, total, percent}, or null
  let scan = $state(null)
  //: how often a running vault scan is read, in milliseconds
  const SCAN_POLL_MS = 300
  let following = false

  function takeVault(got) {
    vault = got
    vaultAt = (got.scannedAt || 0) * 1000
    const next = { ...scales }
    for (const i of got.items || []) if (next[i.item] === undefined) next[i.item] = String(i.scale ?? 1)
    scales = next
  }

  /** The vault list; while the server scans the vault (one scan at a time, the running one
   *  joined), its progress, with the last list kept on screen. */
  async function readVault(fresh) {
    if (following) return
    following = true
    loading = true
    try {
      let got = await fabVault(fresh)
      while (got.scanning) {
        scan = got.scan
        if (got.items) takeVault(got)
        await new Promise((r) => setTimeout(r, SCAN_POLL_MS))
        got = await fabVault(false)
      }
      takeVault(got)
    } catch (e) {
      message = { tone: 'bad', text: e.message }
    } finally {
      scan = null
      loading = false
      following = false
    }
  }

  const scanText = (s) => 'Reading vault' + (s?.total ? ` (${s.done}/${s.total})` : '')

  /** Jobs and the queue from the server; a job that has just ended refreshes the list and,
   *  for a convert, tells the Map Editor its library changed. */
  async function readJobs() {
    let st
    try {
      st = await fabState()
    } catch (e) {
      message = { tone: 'bad', text: e.message }
      return
    }
    serverOff = st.available ? null : st.message
    needs = st.needs || null
    const ended = Object.values(st.jobs).filter((j) => j.state !== 'running' && jobs[j.item]?.state === 'running')
    jobs = st.jobs
    queue = st.queue || []
    for (const j of ended) {
      if (j.kind === 'measure' && j.measure) {
        scales = { ...scales, [j.item]: String(j.measure.scale) }
        evidence = { ...evidence, [j.item]: j.measure.evidence }
        if (adding && adding.measured === j.item) {
          adding = { ...adding, scale: String(j.measure.scale), evidence: j.measure.evidence }
        }
      }
      if (j.kind === 'measure' && j.state === 'failed') {
        if (adding && adding.measured === j.item) adding = { ...adding, error: j.failure }
        else message = { tone: 'bad', text: `${j.title}: ${j.failure}` }
      }
      if (j.kind === 'convert' && ['done', 'problems'].includes(j.state)) onchanged?.(j.packId)
    }
    if (ended.length) await readVault(true)
  }

  $effect(() => {
    if (!active || off) return
    readJobs()
    readVault(false)
  })

  // running jobs and the queue are followed whether or not the tab is shown
  $effect(() => {
    if (!anyRunning) return
    const timer = setInterval(readJobs, JOB_POLL_MS)
    return () => clearInterval(timer)
  })

  const jobOf = (row) => jobs[row.item]
  const converting = (row) => jobOf(row)?.kind === 'convert' && jobOf(row)?.state === 'running'
  const measuring = (row) => jobOf(row)?.kind === 'measure' && jobOf(row)?.state === 'running'
  const queued = (row) => queue.some((q) => q.item === row.item)
  //: a convert that ended after the list was read: its library status, until the list catches up
  const JOB_STATUS = { done: 'current', problems: 'failed', failed: 'failed' }
  function libraryStatus(row) {
    const job = jobOf(row)
    if (job?.kind === 'convert' && job.ended && job.ended * 1000 > vaultAt && JOB_STATUS[job.state]) {
      return JOB_STATUS[job.state]
    }
    return row.library?.status
  }
  function statusOf(row) {
    if (converting(row) || row.running) return STATUS.running
    if (queued(row)) return STATUS.queued
    return STATUS[libraryStatus(row)] || STATUS.none
  }
  const hasLibrary = (row) => !['none', undefined].includes(libraryStatus(row))
  const missingSource = (row) => row.source === 'file' && row.exists === false

  const percent = (job) => Math.round(job?.percent || 0)
  function stageText(job) {
    const p = job?.progress
    if (!p) return 'Starting'
    return `${STAGES[p.stage] || p.stage} ${p.done}/${p.total}`
  }
  /** The converter's estimate of the time left, or '' without one. */
  function etaText(s) {
    if (s == null) return ''
    if (s < 60) return 'Less than a minute left'
    const m = Math.round(s / 60)
    if (m < 60) return `About ${m} min left`
    return `About ${Math.floor(m / 60)} h ${m % 60} min left`
  }

  async function act(fn) {
    message = null
    try {
      await fn()
      await readJobs()
    } catch (e) {
      message = { tone: 'bad', text: e.message }
    }
  }

  //: the queue actions pressed so far, sent one after another in that order
  let queueing = Promise.resolve()

  /** act(fn) once every queue action pressed before it has finished. */
  function inOrder(fn) {
    queueing = queueing.then(() => act(fn))
    return queueing
  }

  /** The row's action: queue it (Convert, Update, Retry), or Cancel it. */
  function rowAction(row) {
    if (converting(row) || queued(row)) return cancel(row.item)
    const value = Number(scales[row.item])
    if (!(value > 0)) return (message = { tone: 'bad', text: `The scale for ${row.title} must be a number above 0.` })
    inOrder(async () => take(await fabConvert(row.item, value === Number(row.scale) ? null : value)))
  }

  function measure(row) {
    act(async () => {
      const got = await fabMeasure(row.item)
      jobs = { ...jobs, [row.item]: got }
    })
  }

  const cancel = (item) => inOrder(async () => take(await fabCancel(item)))
  const move = (item, index) => inOrder(async () => { queue = await fabMove(item, index) })

  /** The evidence lines open: measured before (cached by the converter), read again. */
  function openEvidence(row, e) {
    if (e.currentTarget.open && !evidence[row.item] && !measuring(row)) measure(row)
  }

  async function confirmDelete() {
    const row = confirming
    deleting = true
    try {
      const got = await fabDelete(row.item)
      confirming = null
      onchanged?.(got.packId)
      await readJobs()
      await readVault(true)
    } catch (e) {
      confirming = null
      message = { tone: 'bad', text: e.message }
    } finally {
      deleting = false
    }
  }

  /** No vault found: pick its folder, kept in Settings > Fab Kits. */
  async function chooseVault() {
    const folder = await pickFolder('Epic Vault')
    if (!folder) return
    try {
      await saveFabSettings({ vaultRoot: folder })
      await readVault(true)
    } catch (e) {
      message = { tone: 'bad', text: e.message }
    }
  }

  const count = (n, one, many = one + 's') => `${n} ${n === 1 ? one : many}`
  const titleOf = (item) => rows.find((r) => r.item === item)?.title ?? baseName(item)
</script>

<svelte:window onkeydown={(e) => {
  if (e.key !== 'Escape') return
  if (confirming && !deleting) confirming = null
  else if (adding) adding = null
}} />

<section class="fab">
  <header>
    <div class="heading">
      <h1>Fab Kits</h1>
      {#if !off && vault}
        <p><span>{count(rows.length, 'pack')}</span>
          <span class="path" title="Fab Kits folder">{vault.library}</span></p>
      {/if}
    </div>
    {#if !off}
      <div class="actions">
        <button class="add" onclick={openAdd}><Icon name="install" size={16} /> Add Pack from File…</button>
        <button class="ghost icon" onclick={() => readVault(true)} disabled={loading}
                title="Refresh" aria-label="Refresh"><Icon name="refresh" size={16} /></button>
      </div>
    {/if}
  </header>

  {#if message}
    <div class="message {message.tone}" role="status">
      <span>{message.text}</span>
      <button class="ghost" onclick={() => (message = null)} aria-label="Dismiss">✕</button>
    </div>
  {/if}

  {#if !off && (current || queue.length)}
    <section class="queue" aria-label="Queue">
      <h2>Queue</h2>
      <ol>
        {#if current}
          <li class="now" data-item={current.item}>
            <span class="pos" aria-hidden="true"><span class="spin"></span></span>
            <b class="title" title={titleOf(current.item)}>{titleOf(current.item)}</b>
            <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="100"
                 aria-valuenow={percent(current)} aria-label="Progress for {titleOf(current.item)}">
              <div class="bar"><span style="width: {percent(current)}%"></span></div>
              <span class="stage">{stageText(current)}</span>
              <span class="pct">{percent(current)}%</span>
              <span class="eta">{etaText(current.eta)}</span>
            </div>
            <span class="tools">
              <button class="cancel" onclick={() => cancel(current.item)}>Cancel</button>
            </span>
          </li>
        {/if}
        {#each queue as q, i (q.item)}
          <li data-item={q.item}>
            <span class="pos">{i + 1}</span>
            <b class="title" title={q.title}>{q.title}</b>
            <span class="tools">
              <button class="ghost icon" disabled={i === 0} onclick={() => move(q.item, i - 1)}
                      title="Move Up" aria-label="Move {q.title} up">▲</button>
              <button class="ghost icon" disabled={i === queue.length - 1} onclick={() => move(q.item, i + 1)}
                      title="Move Down" aria-label="Move {q.title} down">▼</button>
              <button class="cancel" onclick={() => cancel(q.item)}>Cancel</button>
            </span>
          </li>
        {/each}
      </ol>
    </section>
  {/if}

  <div class="list">
    {#if off}
      <div class="empty unavailable">
        <Icon name="kits" size={40} />
        <h2>{off}</h2>
        {#if needs}<p class="needs">{needs}</p>{/if}
      </div>
    {:else if scan && !vault}
      <div class="scan alone" role="progressbar" aria-valuemin="0" aria-valuemax="100"
           aria-valuenow={Math.round(scan.percent || 0)} aria-label="Reading vault">
        <span class="label">{scanText(scan)}</span>
        <div class="bar"><span style="width: {scan.percent || 0}%"></span></div>
      </div>
    {:else if vault && !rows.length}
      <div class="empty novault">
        <Icon name="kits" size={40} />
        {#if (vault.roots?.found || []).length}
          <h2>No packs with level pieces</h2>
        {:else}
          <h2>No Epic vault found</h2>
          <ul class="searched">
            {#each vault.roots?.searched || [] as folder}<li>{folder}</li>{/each}
          </ul>
          <button class="choose-vault" onclick={chooseVault}>Choose Vault Folder…</button>
        {/if}
      </div>
    {:else if vault}
      {#if !(vault.roots?.found || []).length}
        <div class="novault-line">
          <b>No Epic vault found</b>
          <button class="ghost choose-vault" onclick={chooseVault}>Choose Vault Folder…</button>
          <ul class="searched">{#each vault.roots?.searched || [] as folder}<li>{folder}</li>{/each}</ul>
        </div>
      {/if}
      {#if scan}
        <div class="scan" role="progressbar" aria-valuemin="0" aria-valuemax="100"
             aria-valuenow={Math.round(scan.percent || 0)} aria-label="Reading vault">
          <span class="label">{scanText(scan)}</span>
          <div class="bar"><span style="width: {scan.percent || 0}%"></span></div>
        </div>
      {/if}
      <div class="row head" class:kinds={showKinds}>
        {#each COLUMNS as [key, label] (key)}
          <button class="sort" class:on={sort.key === key} onclick={() => sortBy(key)}
                  aria-sort={sort.key === key ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}>
            {label}<span class="arrow" aria-hidden="true">{sort.key === key ? (sort.dir === 1 ? '▲' : '▼') : ''}</span>
          </button>
        {/each}
        <span></span>
      </div>
      {#each rows as row (row.item)}
        {@const job = jobOf(row)}
        {@const st = statusOf(row)}
        {@const missing = missingSource(row)}
        {@const action = missing ? null : st.action}
        <div class="pack" class:missing data-item={row.item}>
          <div class="row" class:kinds={showKinds}>
            <span class="name">
              <b title={row.title}>{row.title}</b>
              {#if row.pack_id}<small>{row.pack_id}</small>{/if}
              {#if row.source === 'file'}<small class="source" title={row.path}>{row.path}</small>{/if}
            </span>
            {#if showKinds}<span class="dim kind">{KINDS[row.kind] || '—'}</span>{/if}
            <span class="dim">{row.engine || '—'}</span>
            <span class="dim">{row.meshes ?? '—'}</span>
            <span class="scale">
              {#if !missing}
                <input type="number" min="0.05" step="0.05" bind:value={scales[row.item]}
                       aria-label="Scale for {row.title}" disabled={st.rank <= 1} />
                {#if row.scale_source === 'default' && !evidence[row.item]}
                  <button class="ghost measure" disabled={measuring(row) || st.rank <= 1}
                          onclick={() => measure(row)}>{measuring(row) ? 'Measuring' : 'Measure'}</button>
                {/if}
              {/if}
            </span>
            <span>
              <span class="state {st.tone}">
                {#if st.tone === 'busy'}<span class="spin" aria-hidden="true"></span>{/if}{st.label}{#if st.tone === 'busy' && converting(row)}&nbsp;{percent(job)}%{/if}</span>
            </span>
            <span class="tools">
              {#if action}
                <button class="act" class:primary={st.rank >= 2} class:cancel={st.rank <= 1}
                        disabled={measuring(row)} onclick={() => rowAction(row)}>{action}</button>
              {/if}
              <button class="ghost icon danger delete" class:gone={!hasLibrary(row)}
                      disabled={!hasLibrary(row) || st.rank <= 1 || measuring(row)}
                      aria-hidden={!hasLibrary(row)} tabindex={hasLibrary(row) ? 0 : -1}
                      title="Delete Fab Kit" aria-label="Delete {row.title}"
                      onclick={() => (confirming = row)}><Icon name="trash" size={16} /></button>
            </span>
          </div>
          {#if job && job.kind === 'convert' && job.state === 'failed'}
            <p class="failed">{job.failure || 'Conversion failed'}
              {#if onlog}<button class="ghost log" onclick={() => onlog()}>Open Console</button>{/if}</p>
          {/if}
          {#if job && job.kind === 'convert' && job.state === 'cancelled' && !queued(row)}
            <p class="note">Cancelled</p>
          {/if}
          {#if job && job.kind === 'convert' && job.state === 'problems' && job.problems.length}
            <details class="problems">
              <summary>{count(job.problemCount || job.problems.length, 'problem')}</summary>
              <ul>{#each job.problems as p}<li>{p}</li>{/each}</ul>
            </details>
          {/if}
          {#if evidence[row.item] || row.scale_source === 'measured'}
            <details class="evidence" ontoggle={(e) => openEvidence(row, e)}>
              <summary>Evidence</summary>
              {#if evidence[row.item]}
                <ul>{#each evidence[row.item] as line}<li>{line}</li>{/each}</ul>
              {/if}
            </details>
          {/if}
        </div>
      {/each}
    {/if}
  </div>
</section>

{#if confirming}
  <div class="scrim" onclick={() => { if (!deleting) confirming = null }} role="presentation"></div>
  <div class="modal" role="dialog" aria-label="Delete Fab Kit">
    <header>
      <h3>Delete Fab Kit</h3>
      <button class="ghost" onclick={() => (confirming = null)} disabled={deleting} aria-label="Close">✕</button>
    </header>
    <p class="body">Delete the Fab Kit for {confirming.title}?</p>
    <footer>
      <button class="ghost" onclick={() => (confirming = null)} disabled={deleting}>Cancel</button>
      <button class="danger confirm" onclick={confirmDelete} disabled={deleting}>{deleting ? 'Deleting' : 'Delete'}</button>
    </footer>
  </div>
{/if}

{#if adding}
  {@const addMeasuring = adding.measured && jobs[adding.measured]?.state === 'running'}
  <div class="scrim" onclick={() => (adding = null)} role="presentation"></div>
  <div class="modal add-modal" role="dialog" aria-label="Add Pack from File">
    <header>
      <h3>Add Pack from File</h3>
      <button class="ghost" onclick={() => (adding = null)} aria-label="Close">✕</button>
    </header>
    <div class="body form">
      <label><span>Path</span>
        <input type="text" bind:value={adding.path} aria-label="Path" spellcheck="false"
               oninput={() => { adding.evidence = null; adding.measured = null }} /></label>
      {#if isNative()}
        <div class="choose">
          <button onclick={() => chooseSource('file')}>Choose File…</button>
          <button onclick={() => chooseSource('folder')}>Choose Folder…</button>
        </div>
      {/if}
      <label><span>Scale</span>
        <input type="number" min="0.05" step="0.05" bind:value={adding.scale} aria-label="Scale" />
        <button class="ghost measure" disabled={!adding.path.trim() || addMeasuring}
                onclick={measureAdd}>{addMeasuring ? 'Measuring' : 'Measure'}</button></label>
      {#if adding.evidence}
        <details class="evidence" open>
          <summary>Evidence</summary>
          <ul>{#each adding.evidence as line}<li>{line}</li>{/each}</ul>
        </details>
      {/if}
      {#if adding.error}<p class="add-error">{adding.error}</p>{/if}
    </div>
    <footer>
      <button class="ghost" onclick={() => (adding = null)}>Cancel</button>
      <button class="primary confirm" disabled={!adding.path.trim() || addMeasuring}
              onclick={convertAdd}>Convert</button>
    </footer>
  </div>
{/if}

<style>
  .fab { height: 100%; display: flex; flex-direction: column; min-width: 0; }
  header { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between;
           gap: 12px 24px; padding: 20px 28px 14px; border-bottom: 1px solid var(--edge); }
  h1 { margin: 0; font-size: 22px; font-weight: 650; color: var(--hot); }
  .heading p { margin: 2px 0 0; color: var(--text-dim); font-size: 13px; display: flex;
               align-items: center; gap: 10px; flex-wrap: wrap; }
  .path { font-size: 12px; max-width: 60ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .actions .icon { padding: 4px 6px; display: inline-grid; place-items: center; }

  .message { margin: 12px 28px 0; padding: 8px 10px 8px 14px; border-radius: 6px; display: flex;
             align-items: center; gap: 10px; font-size: 13px; border: 1px solid var(--edge); }
  .message span { flex: 1; }
  .message.bad { border-color: var(--bad); color: var(--bad); }

  .queue { margin: 12px 28px 0; border: 1px solid var(--edge); border-radius: 6px;
           background: var(--panel); }
  .queue h2 { margin: 0; padding: 8px 12px 6px; font-size: 12px; font-weight: 600;
              text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-dim); }
  .queue ol { list-style: none; margin: 0; padding: 0 0 4px; max-height: 14rem; overflow: auto; }
  .queue li { display: grid; grid-template-columns: 1.6rem minmax(8rem, 16rem) 1fr auto;
              align-items: center; gap: 12px; padding: 5px 12px; font-size: 13px; }
  .queue li + li { border-top: 1px solid var(--edge); }
  .queue .pos { color: var(--text-dim); font-variant-numeric: tabular-nums; text-align: center;
                display: grid; place-items: center; }
  .queue .title { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .queue li:not(.now) .title { grid-column: 2 / 4; }
  .queue .tools { grid-column: 4; }

  .list { flex: 1; overflow: auto; padding: 8px 28px 28px; }
  .row { display: grid; align-items: center; gap: 14px; padding: 8px 10px;
         grid-template-columns: minmax(220px, 2.4fr) 70px 70px 190px 150px 130px; }
  .row.kinds { grid-template-columns: minmax(220px, 2.4fr) 86px 70px 70px 190px 150px 130px; }
  .row.head { color: var(--text-dim); font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em;
              position: sticky; top: 0; background: var(--bg); z-index: 1; }
  .sort { all: unset; cursor: pointer; display: inline-flex; align-items: center; gap: 4px;
          white-space: nowrap; border-radius: 4px; }
  .sort:hover, .sort.on { color: var(--text); }
  .sort:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .sort .arrow { font-size: 9px; min-width: 9px; }
  .pack { border-radius: 6px; }
  .pack:hover { background: var(--panel-2); }
  .name { display: flex; flex-direction: column; min-width: 0; }
  .name b { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .name small { color: var(--text-dim); font: 11px ui-monospace, Consolas, monospace;
                overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .dim { color: var(--text-dim); font-size: 13px; }
  .scale { display: flex; align-items: center; gap: 6px; }
  .scale input { width: 72px; font: inherit; color: var(--text); background: var(--panel-2);
                 border: 1px solid var(--edge); border-radius: 5px; padding: 3px 6px; }
  .scale .measure { font-size: 13px; padding: 3px 8px; }
  .tools { display: flex; gap: 4px; justify-content: flex-end; align-items: center; }
  .tools button { font-size: 13px; padding: 4px 10px; }
  .tools .act, .tools .cancel { min-width: 5.5em; }
  .tools button.icon { padding: 4px 6px; display: inline-grid; place-items: center; font-size: 10px; }
  .tools button.gone { visibility: hidden; }

  .state { font-size: 12px; padding: 2px 8px; border-radius: 99px; white-space: nowrap; }
  .state.ok { color: var(--ok); border: 1px solid var(--ok); }
  .state.warn { color: var(--warn); border: 1px solid var(--warn); }
  .state.dim { color: var(--text-dim); border: 1px solid var(--edge); }
  .state.bad { color: var(--bad); border: 1px solid var(--bad); }
  .state.queued { color: var(--accent-ink); border: 1px dashed var(--accent); }
  .state.busy { color: var(--accent-ink); border: 1px solid var(--accent);
                display: inline-flex; align-items: center; gap: 6px; }
  .spin { width: 10px; height: 10px; border-radius: 50%; border: 2px solid var(--accent-soft);
          border-top-color: var(--accent-ink); animation: spin 0.8s linear infinite; display: inline-block; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .progress { display: grid; grid-template-columns: minmax(6rem, 1fr) auto 3em auto; align-items: center;
              gap: 10px; font-size: 12px; min-width: 0; }
  .bar { height: 6px; border-radius: 3px; background: var(--raised); overflow: hidden; }
  .bar span { display: block; height: 100%; background: var(--accent); transition: width 0.3s; }
  .stage, .eta { color: var(--text-dim); white-space: nowrap; }
  .pct { text-align: right; font-variant-numeric: tabular-nums; }
  .failed { margin: 0; padding: 0 10px 10px; color: var(--bad); font-size: 13px; display: flex;
            align-items: center; gap: 10px; }
  .failed .log { font-size: 12px; padding: 2px 8px; color: var(--text-dim); }
  .note { margin: 0; padding: 0 10px 10px; color: var(--text-dim); font-size: 13px; }
  .problems { padding: 0 10px 10px; font-size: 12px; }
  .problems summary, .evidence summary { margin: 0 0 4px; font-size: 12px; font-weight: 600;
                                         color: var(--text-dim); cursor: pointer; }
  .problems ul, .evidence ul { margin: 0; padding-left: 18px; max-height: 12em; overflow: auto;
                               font: 12px/1.5 ui-monospace, Consolas, monospace; }
  .problems li { color: var(--warn); }
  .evidence { padding: 0 10px 10px; }

  .scan { display: grid; grid-template-columns: auto 1fr; align-items: center; gap: 12px;
          padding: 8px 10px; font-size: 12px; color: var(--text-dim); }
  .scan.alone { max-width: 30rem; margin: 18vh auto 0; grid-template-columns: 1fr; text-align: center;
                font-size: 14px; }
  .empty { max-width: 40rem; margin: 12vh auto 0; text-align: center; color: var(--text-dim); }
  .empty h2 { color: var(--text); font-size: 18px; margin: 12px 0 6px; }
  .empty .needs { margin: 0; color: var(--text-dim); font-size: 13px; }
  .empty .choose-vault { margin-top: 14px; }
  .searched { list-style: none; padding: 0; margin: 8px 0 0;
              font: 12px/1.7 ui-monospace, Consolas, monospace; }

  /* below the titlebar (z-index 3000), which must stay draggable over a modal */
  .scrim { position: fixed; inset: 0; z-index: 1002; background: rgba(0, 0, 0, 0.35); }
  .modal { position: fixed; z-index: 1003; top: 20%; left: 50%; transform: translateX(-50%);
           width: min(26rem, calc(100vw - 32px)); background: var(--panel); color: var(--text);
           border: 1px solid var(--edge); border-radius: 8px; box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5); }
  .modal header { padding: 10px 12px 10px 16px; align-items: center; }
  .modal h3 { flex: 1; margin: 0; font-size: 14px; font-weight: 650; color: var(--hot); }
  .modal .body { margin: 0; padding: 14px 16px; }
  .add-modal { width: min(34rem, calc(100vw - 32px)); }
  .form { display: flex; flex-direction: column; gap: 8px; }
  .form label { display: flex; align-items: center; gap: 8px; }
  .form label > span { width: 4rem; flex: none; color: var(--text-dim); }
  .form input { font: inherit; color: var(--text); background: var(--panel-2); border: 1px solid var(--edge);
                border-radius: 5px; padding: 4px 7px; }
  .form input[type='text'] { flex: 1; min-width: 0; }
  .form input[type='number'] { width: 80px; }
  .form .choose { display: flex; gap: 6px; padding-left: calc(4rem + 8px); }
  .form .evidence { padding: 0; }
  .add-error { margin: 0; color: var(--bad); font-size: 13px; }
  .actions { display: flex; gap: 6px; align-items: center; }
  .actions .add { display: inline-flex; align-items: center; gap: 6px; }
  .name small.source { font-family: inherit; }
  .novault-line { padding: 8px 10px; font-size: 13px; color: var(--text-dim); }
  .novault-line b { color: var(--text); font-weight: 600; }
  .novault-line .choose-vault { margin-left: 8px; padding: 2px 8px; font-size: 12px; }
  .novault-line .searched { margin: 2px 0 0; }
  .modal footer { display: flex; justify-content: flex-end; gap: 8px; padding: 10px 16px;
                  border-top: 1px solid var(--edge); }
</style>
