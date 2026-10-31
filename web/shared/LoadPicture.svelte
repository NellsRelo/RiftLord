<script module>
  /** The picture as the server takes it: {name, data?, crop}, null to remove it, or
   * undefined to leave it as it is. */
  export function sendable (p) {
    if (p === null) return null
    return p ? { name: p.name, data: p.data, crop: p.crop } : undefined
  }
</script>

<script>
  import { onMount } from 'svelte'
  // A mission picture: choose a PNG or JPG, then pan (drag) and zoom (wheel or slider)
  // it inside a frame of the exact shape the game shows it in, which previews it.
  // kind 'loading': the loading screen (16:9), with its vignette and title.
  // kind 'portal': the hub portal's square picture, darkened outside the arch the
  // portal shows (hubportal.PORTAL_ARCH).
  //
  // value: null | {name, crop: [x, y, w, h] (fractions of the image), preview (data
  // URL), data? (base64, a new file)}, read once when mounted. onchange gets the same
  // shape, or null. A sender passes on {name, data, crop} only (sendable()).
  // fallback: {preview (URL), from (level)} -- the source level's own picture, drawn in
  // the frame while the mission has none (sourcepictures.py).

  let { value = null, title = '', kind = 'loading', onchange = () => {},
        fallback = null } = $props()

  //: Per kind: the design frame (loading: the UBP_LoadingScreen_* SizeBox in UI units;
  //: portal: the IMG_Portal_* texture in pixels) and the frame drawn here, in CSS pixels.
  const KINDS = {
    loading: { designW: 1920, designH: 1080, frameW: 480 },
    portal: { designW: 1024, designH: 1024, frameW: 300 },
  }
  const { designW: DESIGN_W, designH: DESIGN_H, frameW: FRAME_W } = KINDS[kind] ?? KINDS.loading
  const FRAME_H = FRAME_W * DESIGN_H / DESIGN_W
  const PORTAL = kind === 'portal'
  //: The title text: left edge, top and font size in design units (TextBlock in the
  //: loading widget: box left 40 + padding 30; top 885 + render offset 40; size 90).
  const TITLE_X = 70
  const TITLE_Y = 925
  const TITLE_SIZE = 90
  //: The arch of the portal picture the hub gate shows, as (u, v) fractions
  //: (hubportal.PORTAL_ARCH: Mesh_LevelPortal's UV outline).
  const PORTAL_ARCH = [[0.126, 0.32], [0.134, 0.223], [0.163, 0.166], [0.237, 0.086],
    [0.321, 0.025], [0.438, 0.0], [0.561, 0.0], [0.684, 0.022], [0.77, 0.089],
    [0.843, 0.172], [0.867, 0.249], [0.877, 0.351], [0.874, 0.467], [0.855, 0.999],
    [0.807, 1.0], [0.146, 1.0], [0.135, 0.658], [0.129, 0.437]]
  //: How far past the cover fit the picture may be zoomed in.
  const MAX_ZOOM = 4

  let canvas = $state(null)
  let img = $state(null)
  let error = $state('')
  let zoom = $state(1)              // 1 = the picture just covers the frame
  let ox = $state(0)                // image pixel at the frame's top-left
  let oy = $state(0)
  let current = $state(value ? { ...value } : null)
  //: the source level's picture, shown while there is no picture of the mission's own
  let shown = $state(null)

  $effect(() => {
    const src = fallback?.preview
    if (!src) { shown = null; return }
    const im = new Image()
    im.onload = () => { shown = im }
    im.src = src
  })

  function cover () { return img ? Math.max(FRAME_W / img.naturalWidth, FRAME_H / img.naturalHeight) : 1 }
  function scale () { return cover() * zoom }

  function clamp () {
    const s = scale()
    ox = Math.min(Math.max(ox, 0), img.naturalWidth - FRAME_W / s)
    oy = Math.min(Math.max(oy, 0), img.naturalHeight - FRAME_H / s)
  }

  function crop () {
    const s = scale()
    return [ox / img.naturalWidth, oy / img.naturalHeight,
            FRAME_W / s / img.naturalWidth, FRAME_H / s / img.naturalHeight]
      .map((v) => Math.round(v * 1e6) / 1e6)
  }

  function emit () {
    if (!img || !current) return
    current = { ...current, crop: crop() }
    onchange(current)
  }

  function open (src, saved) {
    const im = new Image()
    im.onload = () => {
      img = im
      if (saved) {
        const [x, y, w] = saved
        zoom = Math.max(1, Math.min(MAX_ZOOM, FRAME_W / (w * im.naturalWidth) / cover()))
        ox = x * im.naturalWidth
        oy = y * im.naturalHeight
      } else {
        zoom = 1
        ox = (im.naturalWidth - FRAME_W / scale()) / 2
        oy = (im.naturalHeight - FRAME_H / scale()) / 2
      }
      clamp()
      emit()
    }
    im.onerror = () => { error = 'That image could not be read.' }
    im.src = src
  }

  onMount(() => {
    if (value && value.preview) open(value.preview, value.crop)
  })

  $effect(() => { draw(img, shown, zoom, ox, oy, canvas, title) })

  function draw () {
    if (!canvas) return
    const g = canvas.getContext('2d')
    const ratio = window.devicePixelRatio || 1
    canvas.width = FRAME_W * ratio
    canvas.height = FRAME_H * ratio
    g.setTransform(ratio, 0, 0, ratio, 0, 0)
    g.fillStyle = '#000'
    g.fillRect(0, 0, FRAME_W, FRAME_H)
    if (img) {
      const s = scale()
      g.drawImage(img, ox, oy, FRAME_W / s, FRAME_H / s, 0, 0, FRAME_W, FRAME_H)
    } else if (shown) {
      //: the source's picture, centred to cover the frame as the game fills it
      const s = Math.max(FRAME_W / shown.naturalWidth, FRAME_H / shown.naturalHeight)
      const w = FRAME_W / s
      const h = FRAME_H / s
      g.drawImage(shown, (shown.naturalWidth - w) / 2, (shown.naturalHeight - h) / 2, w, h,
                  0, 0, FRAME_W, FRAME_H)
    } else {
      return
    }
    if (PORTAL) {
      g.beginPath()
      g.rect(0, 0, FRAME_W, FRAME_H)
      PORTAL_ARCH.forEach(([u, v], i) => {
        g[i ? 'lineTo' : 'moveTo'](u * FRAME_W, v * FRAME_H)
      })
      g.closePath()
      g.fillStyle = 'rgba(0,0,0,0.65)'
      g.fill('evenodd')
      return
    }
    const v = g.createRadialGradient(FRAME_W / 2, FRAME_H / 2, FRAME_H * 0.35,
                                     FRAME_W / 2, FRAME_H / 2, FRAME_W * 0.62)
    v.addColorStop(0, 'rgba(0,0,0,0)')
    v.addColorStop(1, 'rgba(0,0,0,0.6)')
    g.fillStyle = v
    g.fillRect(0, 0, FRAME_W, FRAME_H)
    const k = FRAME_W / DESIGN_W
    g.font = `bold ${TITLE_SIZE * k}px Georgia, serif`
    g.textBaseline = 'top'
    g.fillStyle = 'rgba(0,0,0,0.9)'
    g.fillText(title || 'Mission Title', TITLE_X * k + 1, TITLE_Y * k + 4 * k)
    g.fillStyle = '#f4e6c4'
    g.fillText(title || 'Mission Title', TITLE_X * k, TITLE_Y * k)
  }

  let drag = null
  function down (e) {
    if (!img) return
    drag = { x: e.clientX, y: e.clientY, ox, oy }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  function move (e) {
    if (!drag) return
    const s = scale()
    ox = drag.ox - (e.clientX - drag.x) / s
    oy = drag.oy - (e.clientY - drag.y) / s
    clamp()
  }
  function up () {
    if (!drag) return
    drag = null
    emit()
  }
  function setZoom (z, fx = FRAME_W / 2, fy = FRAME_H / 2) {
    if (!img) return
    const before = scale()
    zoom = Math.max(1, Math.min(MAX_ZOOM, z))
    const after = scale()
    ox += fx / before - fx / after
    oy += fy / before - fy / after
    clamp()
    emit()
  }
  function wheel (e) {
    e.preventDefault()
    const r = canvas.getBoundingClientRect()
    setZoom(zoom * Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top)
  }

  function take (e) {
    error = ''
    const file = e.currentTarget.files?.[0]
    if (!file) return
    if (!/\.(png|jpe?g)$/i.test(file.name)) {
      error = 'The picture must be PNG or JPG.'
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      const url = String(reader.result)
      current = { name: file.name, data: url.split(',', 2)[1], preview: url }
      open(url, null)
    }
    reader.readAsDataURL(file)
  }

  function clear () {
    img = null
    current = null
    onchange(null)
  }
</script>

<label class="lp-row"><span>Picture</span>
  <input type="file" accept=".png,.jpg,.jpeg" onchange={take} /></label>
{#if error}<p class="lp-error">{error}</p>{/if}
{#if img}
  <div class="lp-frame" style="width:{FRAME_W}px;height:{FRAME_H}px">
    <canvas bind:this={canvas} style="width:{FRAME_W}px;height:{FRAME_H}px"
            onpointerdown={down} onpointermove={move} onpointerup={up}
            onpointercancel={up} onwheel={wheel}></canvas>
  </div>
  <label class="lp-row"><span>Zoom</span>
    <input type="range" min="1" max={MAX_ZOOM} step="0.01" value={zoom}
           oninput={(e) => setZoom(+e.currentTarget.value)} /></label>
  <p class="lp-hint">{current?.name ?? ''}
    <button class="lp-link" onclick={clear}>Remove</button></p>
{:else}
  {#if shown}
    <div class="lp-frame lp-source" style="width:{FRAME_W}px;height:{FRAME_H}px">
      <canvas bind:this={canvas} style="width:{FRAME_W}px;height:{FRAME_H}px"></canvas>
    </div>
  {/if}
  <p class="lp-hint">Optional</p>
{/if}

<style>
  .lp-row { display: flex; align-items: center; gap: 8px; margin: 6px 0; }
  .lp-row > span { width: 6.5rem; flex: none; color: var(--md-dim, #8e97a6); }
  .lp-row input[type=file] { flex: 1; min-width: 0; font-size: 12px; color: var(--md-dim, #8e97a6); }
  .lp-row input[type=range] { flex: 1; accent-color: var(--accent, #a05ac0); }
  .lp-frame { max-width: 100%; margin: 6px 0; border: 1px solid var(--md-edge, #2c323d);
              border-radius: 4px; overflow: hidden; cursor: grab; touch-action: none; }
  .lp-frame canvas { display: block; max-width: 100%; }
  .lp-source { cursor: default; }
  .lp-hint { font-size: 12px; color: var(--md-dim, #8e97a6); margin: 4px 0; }
  .lp-error { color: var(--bad, #e05252); font-size: 12px; margin: 4px 0; }
  .lp-link { background: none; border: none; padding: 0; color: var(--accent, #a05ac0);
             cursor: pointer; font: inherit; text-decoration: underline; }
</style>
