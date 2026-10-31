// Moving a spawn about. A spawn belongs to a wave and a spawner, and the door
// tab shows every wave for one door, so a drag can cross waves within that tab
// as well as reorder inside one.

export const drag = $state({ from: null, over: null })

/** Remember what is being dragged: which wave, which spawner, which row. */
export function pick(waveId, spawner, id) {
  drag.from = { waveId, spawner, id }
}

export function release() {
  drag.from = null
  drag.over = null
}

const listOf = (spec, waveId, spawner) => {
  const wave = spec.waves.find((w) => w.id === waveId)
  if (!wave) return null
  if (!wave.spawns[spawner]) wave.spawns[spawner] = []
  return wave.spawns[spawner]
}

/**
 * Drop the dragged spawn into `waveId` at `index`, which may be its own list.
 *
 * The row is taken out before the insertion point is used, so dragging a row
 * downward inside one list lands where the gap appears rather than one short.
 */
export function drop(spec, waveId, spawner, index) {
  const from = drag.from
  release()
  if (!from) return false
  const src = listOf(spec, from.waveId, from.spawner)
  const dst = listOf(spec, waveId, spawner)
  if (!src || !dst) return false

  const at = src.findIndex((s) => s.id === from.id)
  if (at < 0) return false
  const [row] = src.splice(at, 1)

  // A positional index was read before the row came out, so moving downward
  // inside one list has to close the gap it left. The end of the list is
  // measured after the removal and needs no such adjustment.
  let where = dst.length
  if (index >= 0) {
    where = index
    if (src === dst && at < where) where -= 1
  }
  dst.splice(Math.max(0, Math.min(where, dst.length)), 0, row)
  return true
}

/** True while this row is the one being dragged. */
export const isDragging = (id) => drag.from?.id === id
