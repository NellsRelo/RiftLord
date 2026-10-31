<script>
  // One Rift Group trigger (riftlord/mapeditor/rifttear.py, BP_Crystal's Rift* arrays), on two
  // lines:
  //   When [Wave | The Wave After Crystal Health ≤ | From Wave] [n] [After Crystal Health ≤ x %]
  //   Opens At [Seconds Into the Wave | Enemies Left ≤] [n] s|%   Open [n] Rifts   Repeat Every [n] Waves
  // When: `wave` alone; `at` alone (opens in the next wave); `wave` and `at` with `both`.
  // Opens At: `launchAfter` seconds, or `launchAlive` enemies left (at most ALIVE_MAX).
  // `alive` is never written (0); spec.js injectTear converts an older trigger that has it.
  import { triggerOpenProblem, triggerWaveProblem, TRIGGER_ALIVE_MAX as ALIVE_MAX } from './spec.js'

  //: `rifts`: the group's member count; `waves`: the onslaught's wave count
  let { t, rifts = 1, waves = 1, onremove } = $props()

  //: When kind -> dropdown text
  const WHEN = { wave: 'Wave', at: 'The Wave After Crystal Health ≤', both: 'From Wave' }
  //: Opens At kind -> dropdown text
  const OPENS = { after: 'Seconds Into the Wave', alive: 'Enemies Left ≤' }
  const HEALTH_DEFAULT = 75
  const AFTER_DEFAULT = 10
  const ALIVE_DEFAULT = 50

  const hasAt = () => t.at != null && t.at !== '' && Number(t.at) < 100
  const hasWave = () => t.wave !== null && t.wave !== undefined
  let when = $derived(hasAt() && hasWave() ? 'both' : hasAt() ? 'at' : 'wave')
  let opens = $derived(Number(t.launchAfter) > 0 ? 'after' : 'alive')
  let openProblem = $derived(triggerOpenProblem(t, rifts))
  let waveProblem = $derived(when === 'at' ? '' : triggerWaveProblem(t, waves))

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v))
  //: a count field's value: '' while blank (refused by review), else the number typed
  const count = (raw) => (raw === '' ? '' : Number(raw))

  function setWhen(kind) {
    const health = hasAt() ? Number(t.at) : HEALTH_DEFAULT
    const wave = hasWave() ? t.wave : 1
    t.at = kind === 'wave' ? null : health
    t.wave = kind === 'at' ? null : wave
    t.both = kind === 'both'
    t.alive = 0
  }
  function setHealth(raw) {
    const n = Number(raw)
    if (raw !== '' && Number.isFinite(n)) t.at = clamp(Math.round(n), 1, 99)
  }
  function setOpens(kind) {
    t.launchAfter = kind === 'after' ? Number(t.launchAfter) || AFTER_DEFAULT : 0
    t.launchAlive = kind === 'alive' ? Number(t.launchAlive) || ALIVE_DEFAULT : 0
  }
  function setAfter(raw) {
    const n = Number(raw)
    if (raw !== '' && Number.isFinite(n)) t.launchAfter = clamp(Math.round(n), 1, 600)
  }
  function setAlive(raw) {
    const n = Number(raw)
    if (raw !== '' && Number.isFinite(n)) t.launchAlive = clamp(Math.round(n), 1, ALIVE_MAX)
  }
  function setRepeat(raw) {
    const n = Number(raw)
    t.repeat = raw === '' || !Number.isFinite(n) ? 0 : clamp(Math.round(n), 0, 99)
  }
  //: the Enemies Left shown: the contract opens at 50 % when none is set
  let aliveShown = $derived(Number(t.launchAlive) > 0 ? Number(t.launchAlive) : ALIVE_DEFAULT)
</script>

<div class="trigger">
  <div class="line when">
    <span class="w">When</span>
    <select value={when} aria-label="When" onchange={(e) => setWhen(e.currentTarget.value)}>
      {#each Object.entries(WHEN) as [v, text]}<option value={v}>{text}</option>{/each}
    </select>
    {#if when !== 'at'}
      <span class="field">
        <input type="number" min="1" max={Math.max(waves, 1)} step="1" value={t.wave} aria-label="Wave"
               class:bad={!!waveProblem} oninput={(e) => (t.wave = count(e.currentTarget.value))} />
        {#if waveProblem}<span class="err" role="alert">{waveProblem}</span>{/if}
      </span>
    {/if}
    {#if when === 'both'}<span class="w">After Crystal Health ≤</span>{/if}
    {#if when !== 'wave'}
      <input type="number" min="1" max="99" step="1" value={t.at} aria-label="Crystal Health"
             onchange={(e) => { setHealth(e.currentTarget.value); e.currentTarget.value = t.at }} />
      <span class="w">%</span>
    {/if}
    <span class="grow"></span>
    <button class="ghost del" aria-label="Remove this trigger" onclick={onremove}>✕</button>
  </div>

  <div class="line opens">
    <span class="part">
      <span class="w">Opens At</span>
      <select value={opens} aria-label="Opens At" onchange={(e) => setOpens(e.currentTarget.value)}>
        {#each Object.entries(OPENS) as [v, text]}<option value={v}>{text}</option>{/each}
      </select>
      {#if opens === 'after'}
        <input type="number" min="1" max="600" step="1" value={t.launchAfter} aria-label="Seconds Into the Wave"
               onchange={(e) => { setAfter(e.currentTarget.value); e.currentTarget.value = t.launchAfter }} />
        <span class="w">s</span>
      {:else}
        <input type="number" min="1" max={ALIVE_MAX} step="1" value={aliveShown} aria-label="Enemies Left"
               onchange={(e) => { setAlive(e.currentTarget.value); e.currentTarget.value = aliveShown }} />
        <span class="w">%</span>
      {/if}
    </span>
    <span class="part">
      <span class="w">Open</span>
      <span class="field">
        <input type="number" min="1" max={Math.max(rifts, 1)} step="1" value={t.open} aria-label="Open"
               class:bad={!!openProblem} oninput={(e) => (t.open = count(e.currentTarget.value))} />
        {#if openProblem}<span class="err" role="alert">{openProblem}</span>{/if}
      </span>
      <span class="w">{Number(t.open) === 1 ? 'Rift' : 'Rifts'}</span>
    </span>
    <span class="part">
      <span class="w">Repeat Every</span>
      <input type="number" min="0" max="99" step="1" value={Number(t.repeat) || ''} aria-label="Repeat Every"
             onchange={(e) => { setRepeat(e.currentTarget.value); e.currentTarget.value = Number(t.repeat) || '' }} />
      <span class="w">{Number(t.repeat) === 1 ? 'Wave' : 'Waves'}</span>
    </span>
  </div>
</div>

<style>
  .trigger {
    padding: 8px 7px 18px;
    font-size: 13px;
    border-top: 1px solid var(--edge);
  }
  .trigger:first-child { border-top: 0; }
  .line {
    display: flex;
    flex-wrap: wrap;
    gap: 14px 6px;
    align-items: center;
    min-width: 0;
  }
  .line.opens { margin-top: 10px; gap: 14px 20px; }
  .part { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 6px; }
  /* a field with its error under it, out of the line's flow */
  .field { position: relative; display: inline-flex; }
  .w { color: var(--text-dim); white-space: nowrap; }
  select { width: auto; max-width: 100%; }
  input[type='number'] { width: 58px; }
  input.bad { border-color: var(--bad); color: var(--bad); }
  .err {
    position: absolute; left: 0; top: 100%; margin-top: 1px;
    color: var(--bad); font-size: 11px; line-height: 1.3; white-space: nowrap;
  }
  .grow { flex: 1; min-width: 0; }
  .del { padding: 2px 7px; color: var(--text-dim); }
  .del:hover { color: var(--bad); }
</style>
