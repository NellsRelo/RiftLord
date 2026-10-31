<script>
  // The Setup pane: bpkit's type data built from the user's own installs
  // (riftlord/types/setup.py). Shown while it runs, when it failed, and when it cannot run.
  import { setupStatus, startSetup } from './api.js'

  let status = $state(null)
  let timer = null

  async function poll() {
    try {
      status = await setupStatus()
    } catch {
      status = null
    }
    clearTimeout(timer)
    if (status && (status.state === 'running' || status.state === 'needed')) {
      timer = setTimeout(poll, 1000)
    }
  }

  async function retry() {
    try { status = await startSetup() } catch { /* the next poll shows it */ }
    poll()
  }

  $effect(() => {
    poll()
    return () => clearTimeout(timer)
  })

  //: the state the dialog was closed in; a new state opens it again
  let closedAt = $state(null)
  const shown = $derived(status && status.state !== 'ready')
  const open = $derived(shown && status.state !== closedAt)
  const percent = (s) => Math.round((s.progress || 0) * 100)
</script>

{#if shown}
  {#if status.state === 'unavailable'}
    <div class="error" role="status">{status.message}</div>
  {:else if open}
    <div class="scrim" role="presentation"></div>
    <section class="setup" role="dialog" aria-modal="true" aria-label="Setup">
      <header>
        <h2>{status.state === 'failed' ? 'Setup failed' : 'Setting up'}</h2>
        <button class="close" onclick={() => (closedAt = status.state)} aria-label="Close">✕</button>
      </header>
      {#each status.steps as s (s.key)}
        <div class="row" class:done={s.state === 'done'} class:bad={s.state === 'failed'}>
          <span class="label">{s.label}</span>
          <span class="bar"><span class="fill" style="width: {percent(s)}%"></span></span>
          <span class="value">{s.state === 'done' ? 'Done' : s.state === 'failed' ? 'Failed'
            : s.state === 'running' ? percent(s) + '%' : ''}</span>
        </div>
      {/each}
      {#if status.state === 'failed'}
        {#if status.error}<div class="detail">{status.error}</div>{/if}
        <button class="primary" onclick={retry}>Retry</button>
      {/if}
    </section>
  {/if}
{/if}

<style>
  /* below the titlebar (z-index 3000), which must stay draggable over a modal */
  .scrim { position: fixed; inset: 0; z-index: 1002; background: rgba(0, 0, 0, 0.35); }
  .setup {
    position: fixed; z-index: 1003; top: 20%; left: 50%; transform: translateX(-50%);
    width: min(30rem, calc(100vw - 32px)); padding: 12px 14px 14px 16px;
    border: 1px solid var(--edge); border-radius: 8px; background: var(--panel);
    color: var(--text); box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
  }
  header { display: flex; align-items: center; gap: 8px; margin: 0 0 8px; }
  h2 { flex: 1; margin: 0; font-size: 15px; font-weight: 600; }
  .close { background: none; border: none; color: var(--text-dim); font: inherit;
           cursor: pointer; padding: 2px 6px; border-radius: 4px; }
  .close:hover { color: var(--text); background: var(--raised); }
  .row { display: grid; grid-template-columns: 9rem 1fr 3.5rem; gap: 10px; align-items: center;
         padding: 3px 0; }
  .label { color: var(--text); }
  .bar { height: 6px; border-radius: 3px; background: var(--raised); overflow: hidden; }
  .fill { display: block; height: 100%; background: var(--accent); transition: width 0.3s; }
  .row.done .fill { background: var(--ok); }
  .row.bad .fill { background: var(--bad); }
  .value { color: var(--text-dim); font-size: 13px; text-align: right; }
  .detail { margin: 8px 0; color: var(--bad); font-size: 13px; }
  .error { margin: 10px 16px 0; padding: 8px 12px; border: 1px solid var(--warn);
           border-radius: 6px; color: var(--warn); font-size: 13px; }
</style>
