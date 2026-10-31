<script>
  // Line icons, drawn on a 24-unit grid in the current text colour.
  let { name, size = 22 } = $props()
</script>

<svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor"
     stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  {#if name === 'bucket'}
    <path d="M4 7h16l-1.6 12.2a2 2 0 0 1-2 1.8H7.6a2 2 0 0 1-2-1.8z" />
    <path d="M7 7a5 5 0 0 1 10 0" />
    <path d="M8.5 12h7" />
  {:else if name === 'missions'}
    <!-- a banner on a pole: missions to take into the game -->
    <path d="M6 21V3.5" />
    <path d="M6 4.5h11.5l-2.5 4 2.5 4H6" />
  {:else if name === 'package'}
    <path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" />
    <path d="M4 7.5l8 4.5 8-4.5M12 12v9" />
  {:else if name === 'riftlord'}
    <!-- the rift: a ring with a spark in it -->
    <circle cx="12" cy="12" r="8" />
    <path d="M12 7.5l1.4 3.1 3.1 1.4-3.1 1.4-1.4 3.1-1.4-3.1-3.1-1.4 3.1-1.4z" />
  {:else if name === 'map'}
    <!-- a folded map with a marked spot -->
    <path d="M3.5 6.5l5.5-2.5 6 2.5 5.5-2.5v13.5l-5.5 2.5-6-2.5-5.5 2.5z" />
    <path d="M9 4v13.5M15 6.5V20" />
  {:else if name === 'onslaught'}
    <!-- crossed swords -->
    <path d="M4 4l9.5 9.5M4 4v3.5M4 4h3.5" />
    <path d="M20 4l-9.5 9.5M20 4v3.5M20 4h-3.5" />
    <path d="M7.5 16.5l-3 3M16.5 16.5l3 3M6 15l3 3M18 15l-3 3" />
  {:else if name === 'kits'}
    <!-- stacked blocks: kit pieces -->
    <path d="M4 13.5h7v7H4zM13 13.5h7v7h-7zM8.5 4.5h7v7h-7z" />
  {:else if name === 'build'}
    <!-- a wrench -->
    <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.8-3.8a6 6 0 0 1-7.9 7.9l-6.9 6.9a2.1 2.1 0 0 1-3-3l6.9-6.9a6 6 0 0 1 7.9-7.9z" />
  {:else if name === 'install'}
    <path d="M12 4v11M7.5 10.5L12 15l4.5-4.5" />
    <path d="M5 19h14" />
  {:else if name === 'download'}
    <path d="M4 8V5h16v3M4 8v11h16V8z" />
    <path d="M12 11v5M9.5 13.5L12 16l2.5-2.5" />
  {:else if name === 'pencil'}
    <path d="M15.5 4.5l4 4L8 20H4v-4z" />
    <path d="M13 7l4 4" />
  {:else if name === 'trash'}
    <path d="M5 7h14M10 4h4M7 7l1 13h8l1-13" />
  {:else if name === 'folder'}
    <path d="M3.5 7a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" />
  {:else if name === 'refresh'}
    <path d="M19 12a7 7 0 1 1-2.1-5M19 4.5V8h-3.5" />
  {:else if name === 'power'}
    <path d="M12 3.5v8M7.2 6.5a7 7 0 1 0 9.6 0" />
  {:else if name === 'console'}
    <rect x="3" y="4.5" width="18" height="15" rx="2" />
    <path d="M7 9.5l3 2.5-3 2.5M12.5 15h4.5" />
  {:else if name === 'settings'}
    <path d="M4 7h10M18 7h2M4 17h4M12 17h8" />
    <circle cx="16" cy="7" r="2" />
    <circle cx="10" cy="17" r="2" />
  {:else if name === 'gear'}
    <circle cx="12" cy="12" r="3" />
    <path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M18.5 5.5l-1.7 1.7M7.2 16.8l-1.7 1.7" />
    <circle cx="12" cy="12" r="6.4" />
  {:else if name === 'sun'}
    <circle cx="12" cy="12" r="4.6" fill="currentColor" stroke="none" />
    <path d="M12 2.2v2.6M12 19.2v2.6M2.2 12h2.6M19.2 12h2.6M5.1 5.1l1.8 1.8M17.1 17.1l1.8 1.8M18.9 5.1l-1.8 1.8M6.9 17.1l-1.8 1.8" stroke-width="2.2" />
  {:else if name === 'moon'}
    <path d="M20.5 14.6A8.6 8.6 0 1 1 9.4 3.5a6.9 6.9 0 0 0 11.1 11.1z" fill="currentColor" stroke="none" />
  {/if}
</svg>
