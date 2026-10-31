<script>
  // A pause beside a spawn: a flat wait, or a gate that holds until that share
  // of the group before it has died.
  let { pause = $bindable(), label, hideAdd = false } = $props()

  const add = () => (pause = { kind: 'wait', value: 5 })
  const clear = () => (pause = null)
</script>

{#if pause}
  <div class="pause">
    <select bind:value={pause.kind} aria-label={label}>
      <option value="wait">wait</option>
      <option value="gate">wait until</option>
    </select>
    <input type="number" min="0" max={pause.kind === 'gate' ? 100 : 300}
           step={pause.kind === 'gate' ? 5 : 0.5} bind:value={pause.value}
           aria-label="Pause length" />
    <em>{pause.kind === 'gate' ? '% of the group before is dead' : 'seconds'}</em>
    <button class="ghost tiny" onclick={clear} aria-label="Remove pause">✕</button>
  </div>
{:else if !hideAdd}
  <button class="add" onclick={add} title="{label}">
    <svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true">
      <path d="M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zm0 2a7 7 0 1 1 0 14 7 7 0 0 1 0-14z" />
      <path d="M11 7h2v5.4l3.3 2-1 1.7L11 13.5z" />
    </svg>
    {label}
  </button>
{/if}

<style>
  .pause {
    display: flex;
    align-items: center;
    gap: 7px;
    margin: 3px 0 3px 30px;
    padding: 4px 8px;
    border-left: 3px solid var(--accent);
    background: var(--panel-2);
    border-radius: 4px;
    font-size: 12px;
  }
  .pause input { width: 64px; }
  .pause em { color: var(--text-dim); font-style: normal; }
  .tiny { padding: 0 5px; margin-left: auto; }

  /* a real button, not a faint glyph: it has to be findable */
  .add {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    margin: 2px 0 2px 30px;
    padding: 3px 9px;
    font-size: 11.5px;
    color: var(--text-dim);
    background: var(--raised);
    border: 1px solid var(--edge);
    border-radius: 4px;
  }
  .add svg { fill: currentColor; }
  .add:hover { color: var(--text); border-color: var(--text-dim); }
</style>
