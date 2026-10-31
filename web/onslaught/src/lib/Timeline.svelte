<script>
  // The whole onslaught's shape: what ends each wave, its opening delay, and
  // the events the coordinator fires by name.
  import { BREAKS, waveTotal, EVENT_KINDS, newEvent, eventBlocked, reactionFor,
           trackLabel, PAR_MIN, PAR_MAX, memberKey, RIFT_CHANCE, PICK_LABEL, PICK_TIP,
           DIALOGUE_WHEN, LINE_TEXT_MAX, LINE_SPEAKER_MAX, LINE_MIN, LINE_MAX, LINE_DELAY_MAX,
           SHIPPED_SPEAKERS, lineLength, newLine, dialogueLines, syncLineWaves,
           lineProblems } from './spec.js'
  import { musicButton } from '../../../shared/musicpreview.js'

  //: `riftName(spawner)`: an Unstable Rift's name as the map shows it
  let { spec, detail, mode = null, units = [], onremovewave,
        riftName = (s) => `Unstable Rift ${s}` } = $props()

  //: the Crystal's health per difficulty (spec.riftTear.health), and its Rift Groups
  let health = $derived(spec.riftTear?.health ?? null)
  let tearGroups = $derived(spec.riftTear?.groups ?? [])

  /** One difficulty's health changed: blank 0, a number clamped to range. */
  function setHealth(d, raw) {
    const n = Number(raw)
    const [lo, hi] = health.range ?? [0, 10000000]
    if (raw === '' || raw === null) health.values[d] = 0
    else if (Number.isFinite(n)) health.values[d] = Math.max(lo, Math.min(hi, n))
  }

  /** A member's chance in a wave: 0 unavailable, else 1..100 (100 is stored as none). */
  function setChance(wave, key, raw) {
    const n = Math.max(0, Math.min(RIFT_CHANCE, Math.round(Number(raw) || 0)))
    const next = { ...(wave.riftChance ?? {}) }
    if (n === RIFT_CHANCE) delete next[key]
    else next[key] = n
    wave.riftChance = next
  }

  //: by-name actions no mission event owns (events own boss, music and rift actions)
  let actions = $derived((detail?.actions ?? []).filter((a) => a.kind === 'other'))

  const KIND_LABELS = { boss: 'Boss', music: 'Music change', rift: 'Unstable Rift' }
  let bosses = $derived(units.filter((u) => u.boss))
  //: installed mods' bosses, one group per mod
  let modBosses = $derived([...new Set(bosses.filter((u) => u.mod).map((u) => u.family))].sort()
    .map((f) => ({ family: f, units: bosses.filter((u) => u.mod && u.family === f) })))
  let doors = $derived(detail?.doors ?? [])
  let tracks = $derived(detail?.events?.tracks ?? [])
  let rifts = $derived(detail?.events?.rift?.rifts ?? [])

  function addEvent(kind) {
    if (!spec.events) spec.events = []
    spec.events.push(newEvent(kind, spec, detail, units))
  }

  function removeEvent(i) {
    spec.events = spec.events.filter((_e, k) => k !== i)
  }

  //: the dialogue lines (spec.events of kind 'dialogue', in order), and why none can play
  let lines = $derived(dialogueLines(spec.events))
  let dialogueWhy = $derived(eventBlocked('dialogue', detail))

  function addLine() {
    if (!spec.events) spec.events = []
    spec.events.push(newLine(lines[lines.length - 1]))
  }

  function removeLine(i) {
    const gone = lines[i]
    spec.events = spec.events.filter((e) => e !== gone)
    if (lines.length && lines[0].when === 'after') lines[0].when = 'start'
    syncLineWaves(spec.events)
  }

  /** Line i swapped with its neighbour `by` (-1 up, 1 down), among the dialogue lines. */
  function moveLine(i, by) {
    const a = lines[i]
    const b = lines[i + by]
    if (!a || !b) return
    const at = spec.events.indexOf(a)
    const bt = spec.events.indexOf(b)
    const next = [...spec.events]
    next[at] = b
    next[bt] = a
    spec.events = next
    const first = dialogueLines(spec.events)[0]
    if (first && first.when === 'after') first.when = 'start'
    syncLineWaves(spec.events)
  }

  function setWhen(line, when) {
    line.when = when
    syncLineWaves(spec.events)
  }

  function setWave(line, raw) {
    line.wave = raw === '' ? '' : Number(raw)
    syncLineWaves(spec.events)
  }

  /** A seconds field changed: blank keeps `empty` (0 delay, or the length from the text). */
  function setNumber(line, field, raw, empty) {
    line[field] = raw === '' || raw === null ? empty : Math.round(Number(raw) * 10) / 10
  }

  /** A boss picked: its reaction follows whether the map has one for it. */
  function pickBoss(ev, unit) {
    ev.unit = unit
    ev.reaction = !!reactionFor(unit, detail)
  }

  function toggle(wave, name) {
    wave.actions = wave.actions.includes(name)
      ? wave.actions.filter((a) => a !== name)
      : [...wave.actions, name]
  }

  //: the mission settings (wavespec.SETTINGS): label, unit, step, tip
  const SETTING_LABELS = {
    startingCoin: ['Starting coin', 'coin', 100, 'Coin every player starts the mission with'],
    riftPoints: ['Rift points', 'points', 1, 'Damage the rift takes before the mission is lost; most enemies take 1, big ones more'],
    maxEnemies: ['Most enemies alive', 'at once', 10, 'The most enemies the onslaught keeps alive at once; the rest wait their turn'],
    spawnInterval: ['Spawn interval', 'seconds', 0.1, 'Seconds between enemies coming through a door'],
    groupDelay: ['Group delay', 'seconds', 0.5, 'Seconds between the groups of a wave'],
    waveDelay: ['Wave delay', 'seconds', 0.5, 'Seconds between waves that have no break set'],
    postBreakDelay: ['After Go delay', 'seconds', 0.5, 'Seconds after the players press Go before enemies come'],
  }
  //: the settings shown: none the game mode hides (gamemode.MODES: Crystal Defense, rift points)
  let fields = $derived((detail?.settings?.fields ?? [])
    .filter((f) => !(mode?.hide ?? []).includes(f.key)))
  let own = $derived(detail?.settings?.values ?? {})

  /** A setting changed: blank, or the map's own value, keeps the map's own. */
  function setSetting(key, raw) {
    const next = { ...(spec.settings ?? {}) }
    if (raw === '' || raw === null || Number(raw) === own[key]) delete next[key]
    else next[key] = Number(raw)
    spec.settings = next
  }

  //: the map's own par time, in seconds
  let ownPar = $derived(detail?.par ?? null)
  let parSet = $derived(spec.par != null && spec.par !== '' && Number(spec.par) !== ownPar)
  //: a par time outside what a build takes
  let parBad = $derived(parSet && !(Number(spec.par) >= PAR_MIN && Number(spec.par) <= PAR_MAX))

  /** The par time changed: blank, or the map's own, keeps the map's own. */
  function setPar(raw) {
    spec.par = raw === '' || raw === null || Number(raw) === ownPar ? null : Number(raw)
  }

  //: the par time shown as minutes and seconds; spec.par stays in seconds
  let parShown = $derived(parSet ? Number(spec.par) : null)
  const minutesOf = (sec) => Math.floor(sec / 60)
  const secondsOf = (sec) => Math.round(sec - 60 * Math.floor(sec / 60))

  /** One of the two par fields changed: the other keeps what it shows (the map's own
   *  when unset); seconds past 59 carry into minutes. */
  function setParPart(part, raw) {
    const base = parShown ?? ownPar ?? 600
    let min = minutesOf(base)
    let sec = secondsOf(base)
    const n = raw === '' ? 0 : Math.max(0, Math.round(Number(raw) || 0))
    if (part === 'min') min = n
    else sec = n
    setPar(min * 60 + sec)
  }
</script>

<div class="timeline">
  <section class="events">
    <header>
      <h3>Events</h3>
      <span class="grow"></span>
      {#each EVENT_KINDS as kind}
        {@const why = eventBlocked(kind, detail)}
        <button class="ghost add" disabled={!!why} title={why}
                onclick={() => addEvent(kind)}>+ {KIND_LABELS[kind]}</button>
      {/each}
    </header>
    <div class="body">
      {#each EVENT_KINDS.filter((k) => eventBlocked(k, detail)) as kind}
        <div class="blocked">{KIND_LABELS[kind]}: {eventBlocked(kind, detail)}</div>
      {/each}
      {#each spec.events ?? [] as ev, i}
        {@const why = eventBlocked(ev.kind, detail)}
        {#if ev.kind !== 'dialogue'}
        <div class="event" class:off={!!why}>
          <label>Wave
            <select bind:value={ev.wave} aria-label="Event wave">
              {#each spec.waves as _w, k}<option value={k + 1}>{k + 1}</option>{/each}
            </select>
          </label>
          <span class="dot">·</span>
          {#if ev.kind === 'boss'}
            {@const react = reactionFor(ev.unit, detail)}
            <label>Boss:
              <select value={ev.unit} onchange={(e) => pickBoss(ev, e.currentTarget.value)}
                      aria-label="Boss">
                {#each bosses.filter((u) => !u.mod) as u}<option value={u.name}>{u.label}</option>{/each}
                {#each modBosses as g}
                  <optgroup label={g.family}>
                    {#each g.units as u}<option value={u.name}>{u.label}</option>{/each}
                  </optgroup>
                {/each}
              </select>
            </label>
            <span class="dot">·</span>
            <select bind:value={ev.door} aria-label="Boss door">
              {#each doors as d}<option value={d.spawners[0]}>{d.label}</option>{/each}
            </select>
            <span class="dot">·</span>
            <label>Count
              <input type="number" min="1" max="20" bind:value={ev.count} aria-label="Boss count" />
            </label>
            <span class="dot">·</span>
            <label title={react ? '' : 'No reaction lines for this boss on this map'}>
              Companion reaction:
              <select value={react && ev.reaction ? 'on' : 'off'} disabled={!react}
                      onchange={(e) => (ev.reaction = e.currentTarget.value === 'on')}
                      aria-label="Companion reaction">
                <option value="on">on</option><option value="off">off</option>
              </select>
            </label>
          {:else if ev.kind === 'music'}
            <label>Music changes to:
              <select bind:value={ev.track} disabled={!!why} aria-label="Music track">
                {#each tracks as t}<option value={t.key}>{t.label}</option>{/each}
                {#if !tracks.some((t) => t.key === ev.track)}
                  <option value={ev.track}>{trackLabel(ev.track, detail)}</option>
                {/if}
              </select>
            </label>
            {#if tracks.some((t) => t.key === ev.track)}
              <button class="ghost play" title="Hear this track" use:musicButton={{ place: `event-${i}`, track: ev.track }}></button>
            {/if}
          {:else if ev.kind === 'rift'}
            <label>Unstable Rift
              <select bind:value={ev.rift} disabled={!!why} aria-label="Rift">
                {#each rifts.length ? rifts : [ev.rift] as n}<option value={n}>{n}</option>{/each}
              </select>
            </label>
            <select value={ev.open ? 'open' : 'close'} disabled={!!why}
                    onchange={(e) => (ev.open = e.currentTarget.value === 'open')}
                    aria-label="Rift opens or closes">
              <option value="open">opens</option><option value="close">closes</option>
            </select>
          {/if}
          {#if why}<span class="why">{why}</span>{/if}
          <span class="grow"></span>
          <button class="ghost" onclick={() => removeEvent(i)}
                  aria-label="Remove event">✕</button>
        </div>
        {/if}
      {/each}
    </div>
  </section>

  <section class="dialogue">
    <header>
      <h3>Dialogue</h3>
      <span class="grow"></span>
      <button class="ghost add" disabled={!!dialogueWhy} onclick={addLine}>+ Line</button>
    </header>
    <div class="body">
      {#if dialogueWhy}<div class="blocked">{dialogueWhy}</div>{/if}
      {#each lines as line, i}
        {@const bad = lineProblems(line, i, spec.waves.length)}
        <div class="line" class:off={!!dialogueWhy}>
          <span class="num">{i + 1}</span>
          <span class="cell">
            <select value={line.when} class:bad={!!bad.when}
                    onchange={(e) => setWhen(line, e.currentTarget.value)}
                    aria-label={`Line ${i + 1} plays`}>
              {#each DIALOGUE_WHEN as [key, label]}
                <option value={key} disabled={key === 'after' && i === 0}>{label}</option>
              {/each}
            </select>
          </span>
          <span class="cell">
            {#if line.when !== 'after'}
              <span class="w">Wave</span>
              <span class="field inp">
                <input type="number" class="count" min="1" max={Math.max(spec.waves.length, 1)} step="1"
                       value={line.wave} class:bad={!!bad.wave}
                       oninput={(e) => setWave(line, e.currentTarget.value)}
                       aria-label={`Line ${i + 1} wave`} />
                {#if bad.wave}<span class="err" role="alert">{bad.wave}</span>{/if}
              </span>
            {/if}
          </span>
          <span class="cell">
            <span class="w">Delay</span>
            <span class="inp">
              <input type="number" class="secs" min="0" max={LINE_DELAY_MAX} step="0.5"
                     value={line.delay ?? 0} class:bad={!!bad.delay}
                     onchange={(e) => setNumber(line, 'delay', e.currentTarget.value, 0)}
                     aria-label={`Line ${i + 1} delay`} />
              <span class="w">s</span>
            </span>
          </span>
          <span class="cell">
            <span class="w">Speaker</span>
            <input type="text" class="speaker" maxlength={LINE_SPEAKER_MAX} list="dialogue-speakers"
                   value={line.speaker ?? ''} class:bad={!!bad.speaker}
                   oninput={(e) => (line.speaker = e.currentTarget.value)}
                   aria-label={`Line ${i + 1} speaker`} />
          </span>
          <span class="cell">
            <span class="w">Length</span>
            <span class="inp">
              <input type="number" class="secs" min={LINE_MIN} max={LINE_MAX} step="0.1"
                     value={line.duration ?? ''} placeholder={String(lineLength(line.text))}
                     class:bad={!!bad.duration} class:set={line.duration != null}
                     onchange={(e) => setNumber(line, 'duration', e.currentTarget.value, null)}
                     aria-label={`Line ${i + 1} length`} />
              <span class="w">s</span>
            </span>
          </span>
          <span class="cell tools">
            <button class="ghost" disabled={i === 0} onclick={() => moveLine(i, -1)}
                    aria-label={`Move line ${i + 1} up`}>↑</button>
            <button class="ghost" disabled={i === lines.length - 1} onclick={() => moveLine(i, 1)}
                    aria-label={`Move line ${i + 1} down`}>↓</button>
            <button class="ghost del" onclick={() => removeLine(i)}
                    aria-label={`Remove line ${i + 1}`}>✕</button>
          </span>
          <span class="cell say">
            <span class="w">Line</span>
            <input type="text" class="text" maxlength={LINE_TEXT_MAX}
                   value={line.text ?? ''} class:bad={!!bad.text}
                   oninput={(e) => (line.text = e.currentTarget.value)}
                   aria-label={`Line ${i + 1} text`} />
          </span>
        </div>
      {/each}
      <datalist id="dialogue-speakers">
        {#each SHIPPED_SPEAKERS as s}<option value={s}></option>{/each}
      </datalist>
    </div>
  </section>

  <section class="settings">
      <header>
        <h3>Mission Settings</h3>
        <span class="grow"></span>
        {#if Object.keys(spec.settings ?? {}).length || parSet}
          <button class="ghost add" onclick={() => { spec.settings = {}; spec.par = null }}
                  title="Use the map's values for every setting">Reset</button>
        {/if}
      </header>
      <div class="body">
        <div class="grid">
          <span class="lab" title="The time the mission's results compare the players' time with">Par time</span>
          <div class="inline">
            <input type="number" class="part" min="0" max={minutesOf(PAR_MAX)} step="1"
                   value={parShown != null ? minutesOf(parShown) : ''}
                   placeholder={String(minutesOf(ownPar ?? 600))}
                   class:set={parSet} class:bad={parBad}
                   onchange={(e) => setParPart('min', e.currentTarget.value)}
                   aria-label="Par time minutes" />
            <em>min</em>
            <input type="number" class="part" min="0" max="59" step="1"
                   value={parShown != null ? secondsOf(parShown) : ''}
                   placeholder={String(secondsOf(ownPar ?? 600))}
                   class:set={parSet} class:bad={parBad}
                   onchange={(e) => setParPart('sec', e.currentTarget.value)}
                   aria-label="Par time seconds" />
            <em>sec</em>
          </div>
          {#each fields as f (f.key)}
            {@const lab = SETTING_LABELS[f.key] ?? [f.key, '', 1, '']}
            <span class="lab" title={lab[3]}>{lab[0]}</span>
            <div class="inline" title={lab[3]}>
              <input type="number" min={f.min} max={f.max} step={lab[2]}
                     value={spec.settings?.[f.key] ?? ''} placeholder={String(own[f.key] ?? '')}
                     class:set={spec.settings?.[f.key] !== undefined}
                     onchange={(e) => setSetting(f.key, e.currentTarget.value)}
                     aria-label={lab[0]} />
              <em>{lab[1]}</em>
            </div>
          {/each}
        </div>
      </div>
    </section>

  {#if health}
    <section class="settings crystal">
      <header><h3>Crystal Health</h3></header>
      <div class="body">
        <div class="grid">
          {#each health.difficulties as name, d (name)}
            <span class="lab">{name}</span>
            <div class="inline">
              <input type="number" min={health.range?.[0] ?? 0} max={health.range?.[1] ?? 10000000}
                     step="50" value={health.values[d] || ''} aria-label={`${name} health`}
                     onchange={(e) => { setHealth(d, e.currentTarget.value)
                                        e.currentTarget.value = health.values[d] || '' }} />
            </div>
          {/each}
        </div>
      </div>
    </section>
  {/if}

  {#each spec.waves as wave, i (wave.id)}
    {@const last = i === spec.waves.length - 1}
    <section id={`wave-${wave.id}`}>
      <header>
        <h3>Wave {i + 1}</h3>
        <span class="count">{waveTotal(wave)} minions</span>
        <span class="grow"></span>
        {#if spec.waves.length > 1}
          <button class="ghost" onclick={() => onremovewave(wave.id)}
                  aria-label="Remove wave {i + 1}">✕</button>
        {/if}
      </header>

      <div class="body">
        <div class="grid">
          {#if !last}
            <span class="lab">Ends with</span>
            <div class="inline">
              <select bind:value={wave.breakKind} aria-label="How wave {i + 1} ends">
                {#each BREAKS as b}<option value={b.kind}>{b.label}</option>{/each}
              </select>
              {#if wave.breakKind === 'wave'}
                <input type="number" min="0" max="300" bind:value={wave.breakSeconds}
                       aria-label="Break seconds" />
                <em>seconds</em>
              {/if}
            </div>
          {/if}

          <span class="lab">Coin reward</span>
          <div class="inline">
            <input type="number" min="0" max="99999" step="100"
                   placeholder="Amount" bind:value={wave.coin}
                   aria-label="Coin reward" />
            <em>when the wave is cleared</em>
          </div>

          {#each tearGroups as g, gi}
            <span class="lab wide pick" title={PICK_TIP}>{g.name} · {PICK_LABEL}</span>
            <div class="chips rifts">
              {#each g.rifts ?? [] as r (r.spawner)}
                {@const key = memberKey(gi, r.spawner)}
                {@const chance = wave.riftChance?.[key] ?? RIFT_CHANCE}
                <label class="rift" class:off={!chance}>
                  <input type="checkbox" checked={chance > 0}
                         aria-label={`${riftName(r.spawner)} available in wave ${i + 1}`}
                         onchange={(e) => setChance(wave, key, e.currentTarget.checked ? RIFT_CHANCE : 0)} />
                  <span>{riftName(r.spawner)}</span>
                  {#if chance > 0}
                    <input type="number" min="1" max="100" step="5" value={chance} title={PICK_TIP}
                           aria-label={`${riftName(r.spawner)} chance in wave ${i + 1}`}
                           onchange={(e) => setChance(wave, key, e.currentTarget.value)} />
                  {/if}
                </label>
              {/each}
            </div>
          {/each}

          {#if actions.length}
            <span class="lab wide">Wave events</span>
            <div class="chips">
              {#each actions as action}
                <button class="chip" class:on={wave.actions.includes(action.name)}
                        onclick={() => toggle(wave, action.name)}
                        aria-pressed={wave.actions.includes(action.name)}>
                  {action.label}
                </button>
              {/each}
            </div>
          {/if}
        </div>
      </div>
    </section>
  {/each}
</div>

<style>
  .timeline { display: flex; flex-direction: column; }
  section {
    border: 1px solid var(--edge);
    border-radius: 6px;
    background: var(--panel);
    margin-bottom: 12px;
    scroll-margin-top: 8px;
  }
  header {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 7px 10px;
    background: var(--panel-2);
    border-bottom: 1px solid var(--edge);
    border-radius: 6px 6px 0 0;
  }
  h3 { font-size: 14px; }
  .count { color: var(--text-dim); font-size: 12px; }
  .grow { flex: 1; }

  .body { padding: 10px; }
  /* label column beside the control column, so short controls do not leave a
     band of empty space across the card */
  .grid {
  .settings .inline input { width: 96px; }
  .settings input.set { border-color: var(--accent); }
  .settings .hint { margin: 8px 0 0; color: var(--text-dim); font-size: 12px; }
    display: grid;
    /* two label/control pairs per line, so how a wave ends and what it pays
       read as one row rather than stacking */
    grid-template-columns: max-content max-content max-content 1fr;
    align-items: center;
    gap: 8px 14px;
  }
  /* a wave's events are a chip list, so they take the whole line */
  .grid > .lab.wide, .grid > .chips { grid-column: 1 / -1; }
  .lab {
    color: var(--text-dim);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .inline { display: flex; align-items: center; gap: 7px; }
  .inline input { width: 68px; }
  .inline input[placeholder] { width: 96px; }
  .inline input.part { width: 62px; }
  em { color: var(--text-dim); font-size: 12px; font-style: normal; }

  .events .body { display: flex; flex-direction: column; gap: 6px; }
  .event { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; font-size: 13px; }
  .event label { display: inline-flex; align-items: center; gap: 5px; }
  .event input { width: 52px; }
  /* the music preview button (web/shared/musicpreview.js draws its icon and spinner) */
  .play {
    width: 26px; min-width: 26px; height: 24px; padding: 0; line-height: 1;
    font-size: 11px; display: inline-grid; place-items: center;
  }
  .play :global(.spinner) {
    display: inline-block; box-sizing: border-box; width: 11px; height: 11px;
    border: 2px solid currentColor; border-right-color: transparent; border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .event.off { opacity: 0.55; }

  /* a dialogue line: two fixed lines, its columns the same on every line (an empty Wave cell
     for a line that follows another): number, When, Wave, Delay, Speaker, Length and the
     buttons at the right; then its text. In a narrow section each field's label stands over
     it, so the first line still fits. */
  .dialogue .body { container-type: inline-size; overflow-x: auto; }
  .line {
    display: grid;
    grid-template-columns: 1.3em 150px 92px 104px 156px 108px 1fr max-content;
    align-items: center;
    gap: 8px 6px;
    padding: 8px 4px 16px;
    font-size: 13px;
    border-top: 1px solid var(--edge);
  }
  .line:first-of-type { border-top: 0; }
  .cell { display: inline-flex; align-items: center; gap: 5px; min-width: 0; }
  .inp { display: inline-flex; align-items: center; gap: 4px; }
  .cell select { width: 100%; }
  .cell.tools { grid-column: 8; justify-self: end; gap: 0; }
  .cell.tools button { padding: 2px 6px; }
  .cell.say { grid-column: 2 / -1; }
  .line .text { flex: 1; min-width: 0; }
  .line .speaker { width: 96px; }
  .line .secs { width: 52px; }
  .line .count { width: 48px; }
  .line .secs.set { border-color: var(--accent); }
  .line .num { color: var(--text-dim); text-align: right; }
  .line .w { color: var(--text-dim); white-space: nowrap; }
  .line .del { color: var(--text-dim); }
  .line .del:hover { color: var(--bad); }
  @container (max-width: 780px) {
    .line { grid-template-columns: 1.1em 146px 44px 58px 84px 58px 1fr max-content; gap: 8px 4px; }
    .cell:not(.say):not(.tools) { flex-direction: column; align-items: flex-start; gap: 2px; }
    .line .w { font-size: 11px; }
    .line .inp { gap: 3px; }
    .line .speaker { width: 84px; }
    .line .secs, .line .count { width: 44px; padding-left: 5px; padding-right: 2px; }
    .cell select { font-size: 12px; padding-left: 5px; padding-right: 2px; }
    .cell.tools { align-self: end; }
    .cell.tools button { padding: 2px 4px; }
  }
  /* a field with its reason under it, out of the line's flow (as the trigger rows) */
  .field { position: relative; display: inline-flex; }
  .err {
    position: absolute; left: 0; top: 100%; margin-top: 1px;
    color: var(--bad); font-size: 11px; line-height: 1.3; white-space: nowrap;
  }
  .line.off { opacity: 0.55; }
  .dot { color: var(--text-dim); }
  .why, .blocked { color: var(--text-dim); font-size: 12px; }
  .add { font-size: 12px; padding: 3px 8px; }

  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chips.rifts { gap: 6px 16px; }
  .rift { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; padding: 2px 0; }
  .rift input[type='checkbox'] { margin: 0; }
  .rift input[type='number'] { width: 58px; }
  .rift.off span { color: var(--text-dim); }
  .lab.pick { cursor: help; }
  .crystal .inline input { width: 96px; }
  .chip { font-size: 12px; padding: 3px 9px; border-radius: 11px; }
  .chip.on { background: var(--accent); border-color: var(--accent); color: var(--on-accent); }
</style>
