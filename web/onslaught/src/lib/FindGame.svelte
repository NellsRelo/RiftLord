<script>
  // Shown when the game cannot be found. A browser cannot hand back a real
  // filesystem path from a file picker, so the author types or pastes one.
  import { setInstall } from './api.js'

  let { status, onfound } = $props()

  let path = $state('')
  let error = $state(null)
  let busy = $state(false)

  async function submit() {
    busy = true
    error = null
    const got = await setInstall(path)
    busy = false
    if (got.ok) onfound(got)
    else error = got.error
  }
</script>

<div class="wrap">
  <div class="card">
    <h2>Where is Orcs Must Die 3?</h2>
    <p>
      The editor reads the game's maps and enemies, so it needs the install
      folder &mdash; the one containing <code>OMD/Content/Paks</code>.
    </p>

    <label>
      <span>Install folder</span>
      <input bind:value={path} placeholder="…/steamapps/common/Orcs Must Die 3"
             onkeydown={(e) => e.key === 'Enter' && submit()} />
    </label>

    {#if error}<p class="err">{error}</p>{/if}

    <button class="primary" onclick={submit} disabled={busy || !path.trim()}>
      {busy ? 'Checking…' : 'Use this folder'}
    </button>

    {#if status?.libraries?.length}
      <p class="dim">
        Steam libraries searched: {status.libraries.join(', ')}
      </p>
    {/if}
    <p class="dim">Remembered in {status?.settingsFile}</p>
  </div>
</div>

<style>
  .wrap { height: 100%; display: grid; place-items: center; padding: 2rem; }
  .card {
    max-width: 34rem;
    border: 1px solid var(--edge);
    border-radius: 8px;
    background: var(--panel);
    padding: 22px 24px;
  }
  h2 { font-size: 17px; color: var(--accent-ink); margin-bottom: 10px; }
  p { margin: 0 0 12px; color: var(--text-dim); font-size: 13px; }
  code { background: var(--raised); padding: 1px 5px; border-radius: 3px; }
  label { display: grid; gap: 5px; margin-bottom: 12px; }
  label span {
    color: var(--text-dim);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  input { width: 100%; }
  .err { color: var(--bad); }
  .dim { font-size: 11.5px; margin-top: 10px; }
</style>
