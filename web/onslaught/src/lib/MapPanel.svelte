<script>
  // The minimap with its doors, rifts and spawn routes. Positions arrive as
  // texture uv from the server, so nothing here knows the world transform.
  import { minimapUrl } from './api.js'
  import { doorLabel, doorTab, CRYSTAL_DEFENSE } from './spec.js'

  //: `mode` is the mission's game mode (detail.mode): in Crystal Defense the level's rift is
  //: drawn with the mode's minimap icon. `groupRifts`: the indexInLevel of the open Rift
  //: Group's Unstable Rifts, singled out with their routes (null: no group open).
  let { detail, mode = null, active = null, groupRifts = null, onpick = () => {} } = $props()

  //: the mode's minimap icon (riftlord.mapeditor.crystal.map_icon_png), drawn tinted
  const CRYSTAL_ICON = 'api/crystal/mapicon.png'
  //: its size on the minimap, in texture units of SIZE
  const ICON_SIZE = 64
  let iconFailed = $state(false)
  $effect(() => {
    if (mode?.gameType !== CRYSTAL_DEFENSE) return
    const probe = new Image()
    probe.onerror = () => { iconFailed = true }
    probe.src = CRYSTAL_ICON
  })

  // The art fills 0..1000 in texture space. The view frames the floor plan
  // rather than the texture, because a volume maps to the whole square while
  // the drawing covers only part of it.
  const SIZE = 1000
  const PAD = 70
  //: radius of a path point on a route, in texture units of SIZE
  const POINT_R = 7

  //: the map's floors, lowest first (gamedata.map_floors): [{volume, bbox}]; empty for one
  let floors = $derived(detail?.floors ?? [])
  //: the volume shown; null shows the one the map opens on (detail.volume)
  let picked = $state(null)
  let shown = $derived(picked ?? detail?.volume ?? 0)
  $effect(() => { void detail?.map; picked = null })

  /** A point's uv on the floor shown (`p`, by volume), else its own. */
  const uvOn = (p) => (p.p && p.p[shown]) ? { u: p.p[shown][0], v: p.p[shown][1] } : p
  const at = (p) => { const q = uvOn(p); return { x: q.u * SIZE, y: q.v * SIZE } }
  /** True when the point lies on another floor than the one shown. */
  const away = (p) => floors.length > 1 && p?.f != null && p.f !== shown
  /** The floor a door entry is on: its marker's floor. */
  const floorOf = (door) => door?.at?.f ?? null
  //: "Floor N" by volume index
  let floorName = $derived(Object.fromEntries(floors.map((f, i) => [f.volume, `Floor ${i + 1}`])))

  //: each floor's minimap as an object URL, fetched when the map opens, so a floor change
  //: draws at once: {volume: url}
  let floorArt = $state({})
  //: the map and the volumes it draws, "map|v,v": the art is fetched again only when it changes
  let artKey = $derived(detail?.map && detail.volumes > 0
    ? `${detail.map}|${(floors.length ? floors.map((f) => f.volume) : [detail.volume]).join(',')}` : '')
  $effect(() => {
    if (!artKey) return
    const [map, list] = artKey.split('|')
    const volumes = list.split(',').map(Number)
    let live = true
    const made = []
    for (const v of volumes) {
      fetch(minimapUrl(map, v)).then((r) => (r.ok ? r.blob() : null)).then((blob) => {
        if (!live || !blob) return
        const url = URL.createObjectURL(blob)
        made.push(url)
        floorArt = { ...floorArt, [v]: url }
      }).catch(() => {})
    }
    return () => {
      live = false
      for (const url of made) URL.revokeObjectURL(url)
      floorArt = {}
    }
  })

  // The floor follows the open tab: a door's tab shows the door's floor, a Rift Group's
  // the floor most of its rifts are on. The Timeline keeps the floor shown.
  $effect(() => {
    if (floors.length < 2) return
    let want = null
    if (groupRifts) {
      const count = {}
      for (const d of detail.doors) {
        if (inGroup(d) && floorOf(d) != null) count[floorOf(d)] = (count[floorOf(d)] ?? 0) + 1
      }
      const best = Object.entries(count).sort((a, b) => b[1] - a[1])[0]
      want = best ? Number(best[0]) : null
    } else if (active != null) {
      want = floorOf(detail.doors.find((d) => d.key === active))
    }
    if (want != null) picked = want
  })

  // Framed on every floor's drawing and every marker, so changing floors keeps the frame.
  let view = $derived.by(() => {
    if (!detail) return `0 0 ${SIZE} ${SIZE}`
    const boxes = floors.length ? floors.map((f) => f.bbox).filter(Boolean)
      : detail.bbox ? [detail.bbox] : []
    let x0 = boxes.length ? Math.min(...boxes.map((b) => b.u0)) * SIZE : 0
    let y0 = boxes.length ? Math.min(...boxes.map((b) => b.v0)) * SIZE : 0
    let x1 = boxes.length ? Math.max(...boxes.map((b) => b.u1)) * SIZE : SIZE
    let y1 = boxes.length ? Math.max(...boxes.map((b) => b.v1)) * SIZE : SIZE
    const marks = [...detail.rifts, ...detail.doors.map((d) => d.at)].filter(Boolean)
    for (const m of marks) {
      const q = at(m)
      x0 = Math.min(x0, q.x); x1 = Math.max(x1, q.x)
      y0 = Math.min(y0, q.y); y1 = Math.max(y1, q.y)
    }
    return `${x0 - PAD} ${y0 - PAD} ${x1 - x0 + PAD * 2} ${y1 - y0 + PAD * 2}`
  })

  let spawnerOf = $derived(
    Object.fromEntries((detail?.spawners ?? []).map((s) => [s.index, s]))
  )

  const routesOf = (door) =>
    (door.spawners ?? []).map((i) => spawnerOf[i]).filter(Boolean)
      .flatMap((s) => s.route ?? [])

  // On the Timeline no door is the relevant one, so none is singled out.
  const inGroup = (door) => !!groupRifts && (door.spawners ?? []).some((s) => groupRifts.includes(s))
  const isActive = (door) => groupRifts ? inGroup(door) : active == null || door.key === active
  //: the doors whose routes are drawn: the open door, or the open Rift Group's rifts
  let routed = $derived((detail?.doors ?? []).filter((d) => groupRifts ? inGroup(d) : d.key === active))

  /** A cog: squared teeth around a hollow ring, as the rift reads in the HUD. */
  function cogPath(cx, cy, outer, inner, teeth = 8) {
    const pts = []
    const step = Math.PI / teeth
    for (let i = 0; i < teeth * 2; i++) {
      const a0 = step * i - Math.PI / 2
      const a1 = a0 + step * 0.66
      const r = i % 2 === 0 ? outer : inner
      pts.push(`${(cx + Math.cos(a0) * r).toFixed(1)},${(cy + Math.sin(a0) * r).toFixed(1)}`)
      pts.push(`${(cx + Math.cos(a1) * r).toFixed(1)},${(cy + Math.sin(a1) * r).toFixed(1)}`)
    }
    return `M${pts.join('L')}Z`
  }

  /** The Crystal: a tall diamond with its middle facet line. */
  function crystalPath(cx, cy, half, tall) {
    return `M${cx},${cy - tall}L${cx + half},${cy}L${cx},${cy + tall}L${cx - half},${cy}Z`
      + `M${cx - half},${cy}L${cx + half},${cy}`
  }
</script>

<div class="panel">
  {#if !detail}
    <div class="empty">Choose a map to begin.</div>
  {:else}
    {#if floors.length > 1}
      <select class="floor" aria-label="Floor" value={shown}
              onchange={(e) => (picked = Number(e.currentTarget.value))}>
        {#each floors as f, i (f.volume)}<option value={f.volume}>Floor {i + 1}</option>{/each}
      </select>
    {/if}
    <svg viewBox={view} role="presentation">
      <defs>
        <filter id="glow" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="8" result="b" />
          <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        <!-- the mode's minimap icon, white on clear, as a mask the marker colour fills -->
        {#if mode?.gameType === CRYSTAL_DEFENSE}
          {#each detail.rifts.filter(Boolean).slice(0, 1) as rift}
            <mask id="crystal-icon" maskUnits="userSpaceOnUse"
                  x={at(rift).x - ICON_SIZE / 2} y={at(rift).y - ICON_SIZE / 2}
                  width={ICON_SIZE} height={ICON_SIZE}>
              <image href={CRYSTAL_ICON} x={at(rift).x - ICON_SIZE / 2} y={at(rift).y - ICON_SIZE / 2}
                     width={ICON_SIZE} height={ICON_SIZE} />
            </mask>
          {/each}
        {/if}
      </defs>

      {#if detail.volumes > 0}
        <image href={floorArt[shown] ?? minimapUrl(detail.map, shown)} data-volume={shown} x="0" y="0"
               width={SIZE} height={SIZE} class="art" />
      {:else}
            <rect x="0" y="0" width={SIZE} height={SIZE} fill="var(--map-well)" />
        <text x={SIZE / 2} y={SIZE / 2} class="nomap">This map ships no minimap</text>
      {/if}

      <!-- Routes belong to a door, so they are drawn only on that door's tab.
           Every route at once is noise on the Timeline. -->
      {#if active != null || groupRifts}
        {#each routed as door (door.key)}
          {#each routesOf(door) as seg}
            <line x1={at(seg.from).x} y1={at(seg.from).y}
                  x2={at(seg.to).x} y2={at(seg.to).y}
                  class="route" class:fly={seg.kind === 'fly'}
                  class:away={away(seg.from) && away(seg.to)} stroke-width="4" />
          {/each}
          <!-- the path points the route runs through -->
          {#each routesOf(door) as seg}
            <circle cx={at(seg.to).x} cy={at(seg.to).y} r={POINT_R} class="node"
                    class:away={away(seg.to)} />
          {/each}
        {/each}
      {/if}

      {#each detail.rifts as rift}
        {#if rift && mode?.gameType === CRYSTAL_DEFENSE && !iconFailed}
          <g filter="url(#glow)" class="crystal" class:away={away(rift)}>
            <title>Crystal</title>
            <rect x={at(rift).x - ICON_SIZE / 2} y={at(rift).y - ICON_SIZE / 2}
                  width={ICON_SIZE} height={ICON_SIZE} mask="url(#crystal-icon)" class="icon" />
          </g>
        {:else if rift && mode?.gameType === CRYSTAL_DEFENSE}
          <g filter="url(#glow)" class="crystal" class:away={away(rift)}>
            <title>Crystal</title>
            <path d={crystalPath(at(rift).x, at(rift).y, 22, 36)} />
          </g>
        {:else if rift}
          <g filter="url(#glow)" class="rift" class:away={away(rift)}>
            <path d={cogPath(at(rift).x, at(rift).y, 34, 25)} class="cog" />
            <circle cx={at(rift).x} cy={at(rift).y} r="13" class="hub" />
          </g>
        {/if}
      {/each}

      {#each detail.doors as door (door.key)}
        {#if door.at}
          <g class="mark" class:on={isActive(door)} class:away={away(door.at)}
             data-floor={floorName[floorOf(door)] ?? null}
             transform={`translate(${at(door.at).x} ${at(door.at).y})`}
             onclick={() => onpick(door.key)}
             onkeydown={(e) => e.key === 'Enter' && onpick(door.key)}
             role="button" tabindex="0" aria-label={door.kind === 'rift' ? doorTab(door) : doorLabel(door)}>
            <title>{door.kind === 'rift' ? doorTab(door) : doorLabel(door)}</title>
            {#if door.kind === 'rift'}
              <!-- An unstable rift: the same cog, in red. Order Temple's eleven, and
                   nowhere else in the game. -->
              <g class="red">
                <path d={cogPath(0, 0, 30, 22)} class="cog" />
                <circle r="11" class="hub" />
              </g>
            {:else if door.kind === 'spawner'}
              <!-- A plain spawner with no door to break through: the same cog again, in
                   pink. No shipped map has one; an authored mission may. -->
              <g class="doorless">
                <path d={cogPath(0, 0, 30, 22)} class="cog" />
                <circle r="11" class="hub" />
              </g>
            {:else}
              <!-- a gold plaque: domed arch over straight sides, with an X -->
              <path d="M-23 25 L-23 -4 A23 23 0 0 1 23 -4 L23 25 Z" class="plate" />
              <path d="M-13 17 L13 -8 M13 17 L-13 -8" class="cross" />
            {/if}
            {#if door.pathing === 'fly'}
              <!-- a feather, for a route only flyers can use -->
              <g class="wing" transform="translate(16 -46) scale(1.5)">
                <path d="M20.5 3.5c-7 0-12 3.4-13.6 8.4-.7 2.2-.6 4.3 0 6L3 21.5l1.2 1.2
                         3.6-3.6c1.7.6 3.8.7 6 0 5-1.6 8.4-6.6 8.4-13.6zM7.6 17.2c-.3-1.2-.3-2.6.2-4
                         1.2-3.8 4.9-6.5 10.2-6.9-1.3 4-4.2 6.5-8.2 7.6l-3 .7z" />
              </g>
            {/if}
            <text y="48" class="badge">{door.short}</text>
          </g>
        {/if}
      {/each}
    </svg>
  {/if}
</div>

<style>
  .panel {
    position: relative;
    height: 100%;
    display: grid;
    place-items: center;
    padding: 10px;
    /* the same dark pane in both themes: the shipped art is white line work
       and every marker colour is chosen against it */
    background: var(--map-well);
    overflow: hidden;
  }
  svg { width: 100%; height: 100%; }
  .floor { position: absolute; top: 12px; left: 12px; z-index: 1; width: auto; }

  /* The shipped minimap is white line work meant for a dark panel, so it is
     inverted rather than left invisible on a light one. */


  .empty { color: #9aa0a6; font-size: 15px; }
  .nomap { fill: #9aa0a6; font-size: 34px; text-anchor: middle; }

  .route { stroke: var(--marker); fill: none; opacity: 0.55; stroke-linecap: round; }
  /* A flyer's route ignores the floor, so it is drawn as a dashed line: over a minimap
     it would otherwise read as a path something walks. */
  .route.fly { stroke-dasharray: 14 10; opacity: 0.75; }
  .node { fill: var(--marker); opacity: 0.8; }
  /* on another floor than the one shown */
  .route.away, .node.away { opacity: 0.15; }
  .rift.away, .crystal.away { opacity: 0.25; }

  .rift .cog { fill: none; stroke: var(--rift); stroke-width: 7; stroke-linejoin: round; }
  .rift .hub { fill: none; stroke: var(--rift); stroke-width: 5; }
  .crystal .icon { fill: var(--rift-red); }
  .crystal path { fill: none; stroke: var(--rift-red); stroke-width: 6; stroke-linejoin: round; }
  .red .cog { fill: none; stroke: var(--rift-red); stroke-width: 7;
              stroke-linejoin: round; }
  .red .hub { fill: none; stroke: var(--rift-red); stroke-width: 4; }
  .doorless .cog { fill: none; stroke: var(--spawner); stroke-width: 7;
                   stroke-linejoin: round; }
  .doorless .hub { fill: none; stroke: var(--spawner); stroke-width: 4; }

  /* Everything not on the active tab is dimmed, not hidden. */
  .mark { cursor: pointer; opacity: 0.3; transition: opacity 120ms; }
  .mark.on { opacity: 1; }
  .mark.away { opacity: 0.18; }
  /* hovering any marker brings it fully up, so the one under the cursor is
     always legible before it is clicked */
  .mark:hover { opacity: 1; }
  .mark:focus-visible .plate { stroke: var(--text); }

  .plate { fill: #16130a; stroke: var(--marker); stroke-width: 6; }
  .cross { stroke: var(--marker); stroke-width: 7; stroke-linecap: round; fill: none; }
  .wing { fill: #e6e8ea; opacity: 0.9; }
  .badge {
    fill: #e6e8ea;
    font-size: 30px;
    font-weight: 700;
    text-anchor: middle;
    paint-order: stroke;
    stroke: var(--map-well);
    stroke-width: 6;
  }
</style>
