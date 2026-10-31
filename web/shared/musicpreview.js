// The music preview of both editors: a play/stop button for a music choice (the Map Editor's
// Music field, the Onslaught Editor's Music change events). One track plays at a time for the
// whole page.
//
// In Rift Lord's app window the window itself plays it (shell/window main.rs `music`):
// WebKitGTK's own audio path played silence or garbled, too-fast audio. The request goes to
// the shell page, which holds the window's message channel (`riftlord-music` -> window.ipc),
// and the window's answers come back the same way (`riftlord-music-state`: loading, playing,
// stopped, error). The shell says it is the app window when an editor's page loads
// (`riftlord-native`). The window fetches only its own music paths (main.rs music::PATHS),
// so both editors ask it for the Map Editor's (WINDOW_PATH, on the app's one listener).
// In a plain browser the track is fetched whole from the page's own server
// (api/music/<track>, levelmusic.preview) and played from memory, looped.
//
// bindMusicButton(button, {place, track, gone}) wires a button: `place` names where it sits
// (a re-rendered button at the same place takes over a preview that is playing), `track()`
// the track it plays, `gone()` true once its place has left the page (the preview stops).

export const ICON = { play: '▶', stop: '■' }
//: How often a preview checks that its place is still on the page (ms).
const PLACE_CHECK_MS = 500
//: The music path the app window fetches, on the app's one listener.
export const WINDOW_PATH = '/mapeditor/api/music/'
const native = { on: false }

export const music = {
  audio: null,
  key: null,
  place: null,            // where the button playing sits
  state: 'idle',          // 'idle' | 'loading' | 'playing'
  url: null,              // the blob being played (browser), or the track asked for (window)
  native: false,          // this preview is the window's
  watch: null,
  button: null,
  gone: null,
  playing () { return this.state !== 'idle' },
  paint () { paintButton(this.button, this.state) },
  tell (action, url) {
    window.parent.postMessage({ type: 'riftlord-music', action, url }, '*')
  },
  stop () {
    if (this.native && this.state !== 'idle') this.tell('stop')
    if (this.watch) clearInterval(this.watch)
    this.watch = null
    if (this.audio) this.audio.pause()
    if (this.url && !this.native) URL.revokeObjectURL(this.url)
    this.url = null
    this.key = null
    this.native = false
    this.state = 'idle'
    this.paint()
    this.place = null
  },
  /** The window's report on its preview. */
  answer (state, url) {
    if (!this.native || url !== this.url) return
    if (state === 'playing' && this.state === 'loading') {
      this.state = 'playing'
      this.paint()
    } else if (state === 'error') {
      this.native = false               // the window has nothing to stop
      this.stop()
    }
  },
  placeGone () { return !!(this.gone && this.gone()) },
  async play (key) {
    const place = this.place
    const button = this.button
    const gone = this.gone
    this.stop()
    Object.assign(this, { place, button, gone, key, state: 'loading' })
    this.paint()
    if (native.on) {
      this.native = true
      this.url = new URL(WINDOW_PATH + encodeURIComponent(key), location.origin).href
      this.watch = setInterval(() => { if (this.placeGone()) this.stop() }, PLACE_CHECK_MS)
      this.tell('play', this.url)
      return
    }
    try {
      const r = await fetch('api/music/' + encodeURIComponent(key))
      if (!r.ok) throw new Error(r.statusText)
      const blob = await r.blob()
      if (this.key !== key) return            // stopped or changed while it loaded
      if (!this.audio) {
        this.audio = new Audio()
        this.audio.loop = true
        this.audio.addEventListener('timeupdate', () => {
          if (this.placeGone()) this.stop()
        })
      }
      this.url = URL.createObjectURL(blob)
      this.audio.src = this.url
      await this.audio.play()
      if (this.key !== key) return
      this.state = 'playing'
      this.paint()
    } catch (err) {
      if (this.key === key) this.stop()
    }
  },
}

if (typeof window !== 'undefined') {
  window.addEventListener('message', (e) => {
    if (e.source !== window.parent || !e.data) return
    if (e.data.type === 'riftlord-native') native.on = !!e.data.music
    else if (e.data.type === 'riftlord-music-state') music.answer(e.data.state, e.data.url)
  })
  window.addEventListener('pagehide', () => music.stop())
}

/** Draw a button for a preview state: play, a spinner while it loads, stop while it plays. */
export function paintButton (b, state) {
  if (!b) return
  b.innerHTML = state === 'loading' ? '<span class="spinner"></span>'
    : state === 'playing' ? ICON.stop : ICON.play
  b.title = state === 'idle' ? 'Hear this track' : 'Stop'
  b.setAttribute('aria-label', b.title)
  b.dataset.state = state
}

/** Wire `button` as the preview button at `place` for `track()`; returns an unwire. */
export function bindMusicButton (button, { place, track, gone = null }) {
  if (music.place === place) {
    // a re-render at the playing place: this button takes over, and plays a track it now names
    music.button = button
    music.gone = gone
    if (music.playing() && music.key !== track()) music.play(track())
  }
  paintButton(button, music.place === place ? music.state : 'idle')
  const click = (ev) => {
    ev.preventDefault()
    if (music.place === place && music.playing()) {
      music.stop()
      return
    }
    music.stop()
    Object.assign(music, { place, button, gone })
    music.play(track())
  }
  button.addEventListener('click', click)
  return () => button.removeEventListener('click', click)
}

/** bindMusicButton as a Svelte action, `use:musicButton={{place, track}}`: the preview stops
 *  when the track changes or the button goes away. */
export function musicButton (node, params) {
  let p = params
  const off = bindMusicButton(node, { place: p.place, track: () => p.track, gone: () => !node.isConnected })
  return {
    update (next) {
      if (next.track !== p.track) stopMusic(p.place)
      p = next
    },
    destroy () {
      off()
      stopMusic(p.place)
    },
  }
}

/** Stop the preview: any, or only the one playing at `place`. */
export function stopMusic (place = null) {
  if (place === null || music.place === place) music.stop()
}
