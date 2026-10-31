<script>
  // Pick a pak with the system's own file dialog, then choose a map from it. The bytes
  // are sent to the local server because a file input hands the page the contents and a
  // bare name, never a path, and the pak readers work on a file in place.
  import { openPakFile } from './api.js'

  let { onopen, onclose = () => {} } = $props()

  let input = $state(null)
  let busy = $state(false)
  let error = $state(null)
  let found = $state(null)          // {pak, path, maps} once a pak has been read

  /** Open the system file dialog. Called straight from the click, or the browser
   *  refuses to show it. */
  export function pick() {
    error = null
    input?.click()
  }

  async function chosen(event) {
    const file = event.currentTarget.files?.[0]
    // Clear it, or picking the same file twice fires no change event the second time
    event.currentTarget.value = ''
    if (!file) return
    busy = true
    error = null
    found = null
    try {
      found = await openPakFile(file)
      if (!found.maps?.length) {
        error = `${found.pak} holds no map.`
        found = null
      } else if (found.maps.length === 1) {
        // One map is not a choice; go straight in
        take(found, found.maps[0].name)
        return
      }
    } catch (e) {
      error = e.message
    } finally {
      busy = false
    }
  }

  /** Hand the map over and close: leaving the list up over the map that just opened
   *  reads as though the click did nothing. */
  function take(got, name) {
    dismiss()
    onopen(got, name)
  }

  /** Close, whatever is on screen. This component owns what it put up, so it clears its
   *  own state: relying on the parent to do it left ✕ and the backdrop dead whenever an
   *  error was showing. */
  function dismiss() {
    found = null
    error = null
    onclose()
  }
</script>

<input bind:this={input} type="file" accept=".pak" onchange={chosen} hidden
       aria-hidden="true" tabindex="-1" />

{#if busy || error || found}
  <div class="scrim" onclick={() => { if (!busy) dismiss() }} role="button" tabindex="-1"
       onkeydown={(e) => e.key === 'Escape' && !busy && dismiss()} aria-label="Close"></div>
  <div class="modal" role="dialog" aria-label="Choose a map from the pak">
    <header>
      <h3>{found ? found.pak : 'Opening a pak'}</h3>
      <button class="ghost" onclick={dismiss} disabled={busy} aria-label="Close">✕</button>
    </header>

    {#if busy}
      <p class="wait">Reading the pak…</p>
    {:else if error}
      <p class="err">{error}</p>
      <div class="foot"><button onclick={pick}>Choose another file</button></div>
    {:else if found}
      <p class="lead">
        {found.maps.length} maps in this pak. Pick one to edit its onslaught.
      </p>
      <div class="list">
        {#each found.maps as m}
          <button class="row" onclick={() => take(found, m.name)}>
            <span class="name">{m.title}</span>
            <span class="asset">{m.name}</span>
          </button>
        {/each}
      </div>
      <div class="foot"><button onclick={pick}>Choose another file</button></div>
    {/if}
  </div>
{/if}

<style>
  .scrim {
    position: fixed;
    inset: 0;
    z-index: 100;
    background: rgba(0, 0, 0, 0.45);
    border: 0;
  }
  .modal {
    position: fixed;
    z-index: 101;
    top: 10%;
    left: 50%;
    transform: translateX(-50%);
    width: min(34rem, 92vw);
    max-height: 74vh;
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
    gap: 8px;
    padding: 9px 12px;
    border-bottom: 1px solid var(--edge);
  }
  h3 {
    font-size: 13px;
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .lead, .wait, .err { font-size: 12px; padding: 9px 12px 6px; }
  .lead, .wait { color: var(--text-dim); }
  .err { color: var(--bad); }
  .list {
    flex: 1;
    min-height: 0;
    overflow: auto;
    margin: 0 12px;
    border: 1px solid var(--edge);
    border-radius: 5px;
    display: flex;
    flex-direction: column;
  }
  .row {
    display: flex;
    align-items: baseline;
    gap: 9px;
    text-align: left;
    font-size: 12px;
    padding: 6px 9px;
    background: none;
    border: 0;
    border-bottom: 1px solid var(--edge);
  }
  .row:last-child { border-bottom: 0; }
  .row:hover { background: var(--raised); }
  .name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .asset { color: var(--text-dim); font-size: 11px; }
  .foot { padding: 10px 12px; display: flex; justify-content: flex-end; }
</style>
