<script>
  // Errors and warnings in full, on demand. The fields themselves carry the
  // marks; this is for reading the detail.
  let { problems, onclose } = $props()

  let errors = $derived(problems.filter((p) => p.level === 'error'))
  let warns = $derived(problems.filter((p) => p.level === 'warn'))
</script>

<div class="scrim" onclick={onclose} onkeydown={(e) => e.key === 'Escape' && onclose()}
     role="button" tabindex="-1" aria-label="Close"></div>
<div class="modal" role="dialog" aria-label="Errors and warnings">
  <header>
    <h3>{errors.length} {errors.length === 1 ? 'error' : 'errors'},
        {warns.length} {warns.length === 1 ? 'warning' : 'warnings'}</h3>
    <button class="ghost" onclick={onclose} aria-label="Close">✕</button>
  </header>
  <div class="body">
    {#each [...errors, ...warns] as p}
      <p class={p.level}><b>{p.where}</b> {p.text}</p>
    {/each}
    {#if !problems.length}<p class="none">Nothing to report.</p>{/if}
  </div>
</div>

<style>
  .scrim {
    position: fixed;
    inset: 0;
    z-index: 100;          /* nothing sits above the modal or its backdrop */
    background: rgba(0, 0, 0, 0.45);
    border: 0;
  }
  .modal {
    position: fixed;
    z-index: 101;
    top: 12%;
    left: 50%;
    transform: translateX(-50%);
    width: min(44rem, 90vw);
    max-height: 70vh;
    display: flex;
    flex-direction: column;
    background: var(--panel);
    border: 1px solid var(--edge);
    border-radius: 8px;
    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.4);
  }
  header {
    display: flex;
    align-items: center;
    padding: 9px 12px;
    border-bottom: 1px solid var(--edge);
  }
  h3 { font-size: 13px; flex: 1; }
  .body { padding: 10px 12px; overflow: auto; }
  p { margin: 4px 0; font-size: 13px; }
  b { font-weight: 600; }
  .error { color: var(--bad); }
  .warn { color: var(--warn); }
  .none { color: var(--text-dim); }
</style>
