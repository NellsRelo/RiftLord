<script module>
  //: The campaign button's frame (customcampaign.card_preview: {size, window, frame,
  //: mask}), fetched once per page; null when the server cannot read the game's banner.
  let cardArt = null

  function loadCardArt () {
    const image = (src) => new Promise((resolve, reject) => {
      const im = new Image()
      im.onload = () => resolve(im)
      im.onerror = reject
      im.src = src
    })
    cardArt ??= fetch('api/campaign/frame').then((r) => r.ok ? r.json() : null)
      .then(async (got) => got && got.window
        ? { ...got, frame: await image(got.frame), mask: await image(got.mask) } : null)
      .catch(() => null)
    return cardArt
  }
</script>

<script>
  import { onMount } from 'svelte'
  import CropBox from './CropBox.svelte'
  // A campaign banner: choose a PNG or JPG, then place two boxes over it -- one of the
  // hub tab's shape (customcampaign.crop_banner writes it cropped) and one of the mode
  // select button's picture window (hudart/modeart.campaign_card puts it inside the
  // Story banner's border), each previewed below it.
  //
  // value: null | {name, crop, cardCrop: [x, y, w, h] (fractions of the picture), size?
  // ([w, h], the picture's own pixels when preview is smaller), preview (data URL),
  // data? (base64, a new file)}, read once when mounted. onchange gets the same shape,
  // or null. A sender passes on {name, data, crop, cardCrop} (LoadPicture's sendable()).

  let { value = null, onchange = () => {} } = $props()

  //: The hub tab, in UI units at 1920 x 1080 (OrcsMustUI HubTab.yaml).
  const TAB_W = 384
  const TAB_H = 120
  //: The campaign button preview's side, in CSS pixels.
  const CARD_PREVIEW = 240

  let tab = $state(null)
  let cardView = $state(null)
  let img = $state(null)
  let art = $state(null)
  let error = $state('')
  // svelte-ignore state_referenced_locally
  let current = $state(value ? { ...value } : null)
  let crop = $state(null)
  let cardCrop = $state(null)
  //: the button's box follows the tab's centre until it is moved itself
  let cardOwn = false

  const cardAspect = () => art.window[2] / art.window[3]
  //: the picture's own pixels
  const native = () => current?.size ?? [img.naturalWidth, img.naturalHeight]
  let lowRes = $derived(!!(img && art && cardCrop) &&
    (cardCrop[2] * native()[0] < art.window[2] || cardCrop[3] * native()[1] < art.window[3]))

  /** The largest box of the tab's shape, centred on the picture. */
  function tabDefault () {
    const W = img.naturalWidth
    const H = img.naturalHeight
    const w = Math.min(W, H * TAB_W / TAB_H)
    const h = w * TAB_H / TAB_W
    return [(W - w) / 2 / W, (H - h) / 2 / H, w / W, h / H]
  }

  /** The largest box of the button's shape centred on the tab crop (modeart.card_box). */
  function cardDefault () {
    const W = img.naturalWidth
    const H = img.naturalHeight
    const a = cardAspect()
    const w = Math.min(W, H * a)
    const h = w / a
    const cx = (crop[0] + crop[2] / 2) * W
    const cy = (crop[1] + crop[3] / 2) * H
    const x = Math.min(Math.max(cx - w / 2, 0), W - w)
    const y = Math.min(Math.max(cy - h / 2, 0), H - h)
    return [x / W, y / H, w / W, h / H].map((v) => Math.round(v * 1e6) / 1e6)
  }

  function emit () {
    if (!img || !current) return
    current = { ...current, crop: $state.snapshot(crop),
                ...(cardCrop ? { cardCrop: $state.snapshot(cardCrop) } : {}) }
    onchange(current)
  }

  function tabMoved () {
    if (art && !cardOwn) cardCrop = cardDefault()
    emit()
  }

  function cardMoved () {
    cardOwn = true
    emit()
  }

  /** Show picture `src`; `saved` is its value, or null for a new file (whose boxes are
   *  sent straight away). */
  function open (src, saved) {
    const im = new Image()
    im.onload = () => {
      img = im
      crop = saved?.crop ?? tabDefault()
      cardOwn = !!saved?.cardCrop
      cardCrop = saved?.cardCrop ?? (art ? cardDefault() : null)
      if (!saved) emit()
    }
    im.onerror = () => { error = 'That image could not be read.' }
    im.src = src
  }

  onMount(() => {
    loadCardArt().then((got) => {
      art = got
      if (art && img && !cardCrop) {
        cardCrop = cardDefault()
        if (current?.data) emit()
      }
    })
    if (value && value.preview) open(value.preview, value)
  })

  $effect(() => { drawTab(img, crop, tab) })
  $effect(() => { drawCard(img, cardCrop, cardView, art) })

  function drawTab () {
    if (!tab || !img || !crop) return
    const ratio = window.devicePixelRatio || 1
    const W = img.naturalWidth
    const H = img.naturalHeight
    const t = tab.getContext('2d')
    tab.width = TAB_W * ratio
    tab.height = TAB_H * ratio
    t.setTransform(ratio, 0, 0, ratio, 0, 0)
    t.drawImage(img, crop[0] * W, crop[1] * H, crop[2] * W, crop[3] * H, 0, 0, TAB_W, TAB_H)
  }

  /** The button as modeart.campaign_card composes it: the crop filling the picture
   *  window, kept where the banner is opaque, under the banner's border and glow. */
  function drawCard () {
    if (!cardView || !img || !cardCrop || !art) return
    const ratio = window.devicePixelRatio || 1
    const W = img.naturalWidth
    const H = img.naturalHeight
    const k = CARD_PREVIEW / art.size
    const [wx, wy, ww, wh] = art.window
    cardView.width = CARD_PREVIEW * ratio
    cardView.height = CARD_PREVIEW * ratio
    const g = cardView.getContext('2d')
    g.setTransform(ratio, 0, 0, ratio, 0, 0)
    g.clearRect(0, 0, CARD_PREVIEW, CARD_PREVIEW)
    g.globalCompositeOperation = 'source-over'
    g.drawImage(img, cardCrop[0] * W, cardCrop[1] * H, cardCrop[2] * W, cardCrop[3] * H,
                wx * k, wy * k, ww * k, wh * k)
    g.globalCompositeOperation = 'destination-in'
    g.drawImage(art.mask, 0, 0, CARD_PREVIEW, CARD_PREVIEW)
    g.globalCompositeOperation = 'source-over'
    g.drawImage(art.frame, 0, 0, CARD_PREVIEW, CARD_PREVIEW)
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
      img = null
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

<label class="bc-row"><span>Picture</span>
  <input type="file" accept=".png,.jpg,.jpeg" onchange={take} aria-label="Campaign Banner" /></label>
{#if error}<p class="bc-error">{error}</p>{/if}
{#if img && crop}
  {#key img}
    <CropBox {img} aspect={TAB_W / TAB_H} bind:crop label="Campaign Banner crop" onchange={tabMoved} />
  {/key}
  <div class="bc-tab"><canvas bind:this={tab} style="width:{TAB_W}px;height:{TAB_H}px"></canvas></div>
  <p class="bc-hint">{current?.name ?? ''}
    <button class="bc-link" onclick={clear}>Remove</button></p>
  {#if art && cardCrop}
    <h4 class="bc-head">Campaign Button</h4>
    {#key img}
      <CropBox {img} aspect={cardAspect()} bind:crop={cardCrop} label="Campaign Button crop"
               onchange={cardMoved} />
    {/key}
    <div class="bc-card">
      <canvas bind:this={cardView} aria-label="Campaign Button preview"
              style="width:{CARD_PREVIEW}px;height:{CARD_PREVIEW}px"></canvas>
      {#if lowRes}<span class="bc-low">Low resolution</span>{/if}
    </div>
  {/if}
{:else}
  <p class="bc-hint">Optional</p>
{/if}

<style>
  .bc-row { display: flex; align-items: center; gap: 8px; margin: 6px 0; }
  .bc-row > span { width: 6.5rem; flex: none; color: var(--md-dim, #8e97a6); }
  .bc-row input[type=file] { flex: 1; min-width: 0; font-size: 12px; color: var(--md-dim, #8e97a6); }
  .bc-tab { margin: 6px 0; line-height: 0; }
  .bc-tab canvas { max-width: 100%; border: 1px solid var(--md-edge, #2c323d); border-radius: 4px; }
  .bc-head { font-size: 12px; margin: 16px 0 4px; text-transform: uppercase; letter-spacing: 0.06em;
             color: var(--md-dim, #8e97a6); font-weight: 600; }
  .bc-card { margin: 6px 0; display: flex; align-items: flex-end; gap: 10px; }
  .bc-card canvas { max-width: 100%; flex: none; background: #0c0d10; border-radius: 4px;
                    border: 1px solid var(--md-edge, #2c323d); }
  .bc-low { font-size: 12px; color: var(--warn, #e0a040); }
  .bc-hint { font-size: 12px; color: var(--md-dim, #8e97a6); margin: 4px 0; }
  .bc-error { color: var(--bad, #e05252); font-size: 12px; margin: 4px 0; }
  .bc-link { background: none; border: none; padding: 0; color: var(--accent, #a05ac0);
             cursor: pointer; font: inherit; text-decoration: underline; }
</style>
