// Laid Fab grids keep their pieces through Map Editor edits (marker: fabgrids).
//
// A plan's `fabGrids` name their pieces by index into `plan.added`, and deleting or
// reordering pieces moves those indices. Between saves the editor holds each grid's pieces
// as the entries themselves, and `sync` writes them back as indices before anything reads
// or stores them (undo snapshots, the save, the properties panel, the trap grid layer).

/** The Fab entries a grid's saved indices point at. */
function entriesOf (plan, grid) {
  const added = plan.added || []
  return (grid.pieces || []).map(i => added[i]).filter(e => e && e.kind === 'fab')
}

/** Each grid's pieces as entries, read from the indices of a plan just opened or undone. */
export function bind (plan) {
  const owners = new Map()
  for (const g of plan.fabGrids || []) owners.set(g, entriesOf(plan, g))
  return owners
}

/** Tie a newly laid grid to the entries it was laid across. */
export function own (owners, grid, entries) {
  owners.set(grid, [...entries])
}

/** Write each grid's pieces back as indices into `plan.added`. A grid whose last piece was
 * deleted goes with it; one that had no piece when the plan was opened stays, for the save
 * to mend (riftlord/mapeditor/fabemit.mend_grids). Returns how many grids were dropped. */
export function sync (plan, owners) {
  if (!plan.fabGrids) return 0
  const added = plan.added || []
  const before = plan.fabGrids.length
  plan.fabGrids = plan.fabGrids.filter(g => {
    if (!owners.has(g)) owners.set(g, entriesOf(plan, g))
    const had = owners.get(g)
    const live = had.filter(e => added.includes(e))
    owners.set(g, live)
    g.pieces = live.map(e => added.indexOf(e))
    return live.length > 0 || had.length === 0
  })
  return before - plan.fabGrids.length
}
