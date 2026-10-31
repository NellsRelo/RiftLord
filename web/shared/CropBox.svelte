<script>
  // A box of one shape over a picture: drag it to move, drag a corner or turn the wheel
  // to resize, press outside it to centre it there.
  //
  // img: a loaded HTMLImageElement. aspect: the box's width over height. crop: [x, y, w,
  // h] (fractions of the picture), bindable; it follows the box while it moves. onchange
  // gets the crop when a drag or wheel turn ends.

  let { img, aspect, crop = $bindable(), label = '', onchange = () => {} } = $props()

  //: The picture is drawn at most this big, in CSS pixels.
  const FRAME_MAX_W = 480
  const FRAME_MAX_H = 300
  //: Canvas pixels from a corner of the box that grab it to resize.
  const HANDLE = 10
  //: The smallest box, as a share of the picture's width.
  const MIN_SHARE = 0.08

  let canvas = $state(null)
  const boxH = (w) => w / aspect
  const scale = () => Math.min(FRAME_MAX_W / img.naturalWidth, FRAME_MAX_H / img.naturalHeight)
  const widest = () => Math.min(img.naturalWidth, img.naturalHeight * aspect)

  function clamp (b) {
    const w = Math.min(widest(), Math.max(img.naturalWidth * MIN_SHARE, b.w))
    return { w, x: Math.min(Math.max(b.x, 0), img.naturalWidth - w),
             y: Math.min(Math.max(b.y, 0), img.naturalHeight - boxH(w)) }
  }

  //: the box in picture pixels
  const fromCrop = (c) => clamp({ x: c[0] * img.naturalWidth, y: c[1] * img.naturalHeight,
                                  w: c[2] * img.naturalWidth })
  const toCrop = (b) => [b.x / img.naturalWidth, b.y / img.naturalHeight, b.w / img.naturalWidth,
                         boxH(b.w) / img.naturalHeight].map((v) => Math.round(v * 1e6) / 1e6)
  let box = $derived(fromCrop(crop))
  const put = (b) => { crop = toCrop(b) }

  $effect(() => { draw(img, box, canvas) })

  function draw () {
    if (!canvas || !img) return
    const s = scale()
    const W = img.naturalWidth * s
    const H = img.naturalHeight * s
    const ratio = window.devicePixelRatio || 1
    canvas.width = W * ratio
    canvas.height = H * ratio
    canvas.style.width = W + 'px'
    canvas.style.height = H + 'px'
    const g = canvas.getContext('2d')
    g.setTransform(ratio, 0, 0, ratio, 0, 0)
    g.drawImage(img, 0, 0, W, H)
    const bx = box.x * s
    const by = box.y * s
    const bw = box.w * s
    const bh = boxH(box.w) * s
    g.beginPath()
    g.rect(0, 0, W, H)
    g.rect(bx, by, bw, bh)
    g.fillStyle = 'rgba(0,0,0,0.6)'
    g.fill('evenodd')
    g.strokeStyle = '#fff'
    g.lineWidth = 1.5
    g.strokeRect(bx, by, bw, bh)
    g.fillStyle = '#fff'
    for (const [cx, cy] of [[bx, by], [bx + bw, by], [bx, by + bh], [bx + bw, by + bh]]) {
      g.fillRect(cx - 4, cy - 4, 8, 8)
    }
  }

  //: what a pointer drag is doing: {mode: 'move', dx, dy} or {mode: 'size', ax, ay}
  let drag = null

  function at (e) {
    const r = canvas.getBoundingClientRect()
    //: drawn pixels per picture pixel, as laid out (the frame may be narrower)
    const s = r.width / img.naturalWidth
    return { x: (e.clientX - r.left) / s, y: (e.clientY - r.top) / s, s }
  }

  function down (e) {
    const p = at(e)
    const h = HANDLE / p.s
    const corners = [[box.x, box.y], [box.x + box.w, box.y], [box.x, box.y + boxH(box.w)],
                     [box.x + box.w, box.y + boxH(box.w)]]
    const hit = corners.findIndex(([cx, cy]) => Math.abs(p.x - cx) <= h && Math.abs(p.y - cy) <= h)
    if (hit >= 0) {
      const [ax, ay] = corners[3 - hit]
      drag = { mode: 'size', ax, ay }
    } else {
      let b = box
      const inside = p.x >= b.x && p.x <= b.x + b.w && p.y >= b.y && p.y <= b.y + boxH(b.w)
      if (!inside) {
        b = clamp({ ...b, x: p.x - b.w / 2, y: p.y - boxH(b.w) / 2 })
        put(b)
      }
      drag = { mode: 'move', dx: p.x - b.x, dy: p.y - b.y }
    }
    e.currentTarget.setPointerCapture(e.pointerId)
  }

  function move (e) {
    if (!drag) return
    const p = at(e)
    if (drag.mode === 'move') {
      put(clamp({ ...box, x: p.x - drag.dx, y: p.y - drag.dy }))
      return
    }
    const { ax, ay } = drag
    const right = p.x >= ax
    const lower = p.y >= ay
    //: as wide as the pointer asks, within the picture on the side it is dragged to
    const room = Math.min(right ? img.naturalWidth - ax : ax,
                          (lower ? img.naturalHeight - ay : ay) * aspect)
    const want = Math.max(Math.abs(p.x - ax), Math.abs(p.y - ay) * aspect)
    const w = Math.max(img.naturalWidth * MIN_SHARE, Math.min(room, want))
    put(clamp({ w, x: right ? ax : ax - w, y: lower ? ay : ay - boxH(w) }))
  }

  function up () {
    if (!drag) return
    drag = null
    onchange(crop)
  }

  function wheel (e) {
    e.preventDefault()
    const w = box.w * Math.exp(e.deltaY * 0.0015)
    const cx = box.x + box.w / 2
    const cy = box.y + boxH(box.w) / 2
    put(clamp({ w, x: cx - w / 2, y: cy - boxH(w) / 2 }))
    onchange(crop)
  }
</script>

<div class="cb-frame">
  <canvas bind:this={canvas} class="bc-pick" aria-label={label} onpointerdown={down}
          onpointermove={move} onpointerup={up} onpointercancel={up} onwheel={wheel}></canvas>
</div>

<style>
  .cb-frame { margin: 6px 0; line-height: 0; }
  .cb-frame canvas { max-width: 100%; border: 1px solid var(--md-edge, #2c323d); border-radius: 4px;
                     cursor: move; touch-action: none; }
</style>
