// The native window's own services -- folder and file dialogs -- reached through the
// webview's message channel (`window.ipc`), which only Rift Lord's application window
// provides. Run in a plain browser (development), each falls back to something that
// works there.

let next = 1
const waiting = new Map()

// The window answers by calling this with the request's id and its result.
window.__riftlordReply = (id, result) => {
  const done = waiting.get(id)
  waiting.delete(id)
  if (done) done(result)
}

export const isNative = () => !!(window.ipc && window.ipc.postMessage)

function ask(cmd, args = {}) {
  return new Promise((resolve) => {
    const id = next++
    waiting.set(id, resolve)
    window.ipc.postMessage(JSON.stringify({ id, cmd, ...args }))
  })
}

/** A folder the user picks, or null. */
export async function pickFolder(title, start) {
  if (isNative()) return ask('pickFolder', { title, start })
  const typed = prompt(`${title}\n\nType the full path of a folder:`, start ?? '')
  return typed && typed.trim() ? typed.trim() : null
}

/** A file the user picks (of `extension`, when given), or null. Native only: a browser
 *  gives no paths. */
export async function pickFile(title, extension, start) {
  return isNative() ? ask('pickFile', { title, extension: extension ?? '', start: start ?? '' }) : null
}

/** Where to save a file, or null. Native only: a browser saves its own way. */
export async function saveFile(title, name, extension) {
  return isNative() ? ask('saveFile', { title, name, extension }) : null
}

/** Ask the app window to drag, minimize, toggleMaximize, close or resize (with an
 *  edge: n, s, e, w, ne, nw, se, sw). No answer comes back. */
export function windowCommand(cmd, extra = {}) {
  if (isNative()) window.ipc.postMessage(JSON.stringify({ id: 0, cmd, ...extra }))
}

/** Call `fn({maximized})` whenever the app window is resized. */
export function onWindowState(fn) {
  window.__riftlordWindow = fn
}
