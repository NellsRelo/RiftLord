// Offline check of checks.js: rows show in level order (needs a change, last build, worth
// a look, fixed by the build), each a button carrying its row; rows with nothing to point
// at are marked still; targetOf turns a row's target into what main.js focusCheck selects.
//   cd web/mapeditor && node test/checks_check.mjs
import { checksHtml, ordered, targetOf } from '../src/checks.js'

let failed = 0
const check = (ok, what, got) => {
  if (!ok) { failed++; console.log('FAIL', what, JSON.stringify(got)) } else console.log('PASS', what)
}

const rows = [
  { level: 'fixed', text: 'Spawner 7: index in level 5 is taken, so the build gives it 9.',
    target: { kind: 'spawner', id: 7 }, at: [1, 2, 3] },
  { level: 'warning', text: 'Path Point 4 has 6 branches.', target: { kind: 'point', id: 4 } },
  { level: 'error', text: 'Enemies at OMDPathPoint3 can never reach the rift.', target: { name: 'OMDPathPoint3' }, at: [4, 5, 6] },
  { level: 'build', text: 'Path Point 9 at (1, 2, 3) is not on floor enemies can walk.', target: { kind: 'point', id: 9 } },
  { level: 'error', text: 'The installed Crystal Defense is an incompatible version.' },
  { level: 'build', text: 'A floor piece.', target: { added: 2 }, at: [7, 8, 9] },
]
const order = ordered(rows).map(r => r.n)
check(JSON.stringify(order) === JSON.stringify([2, 4, 3, 5, 1, 0]), 'rows in level order, keeping order within a level', order)
const html = checksHtml(rows)
check(html.startsWith('<h2>Checks</h2>'), 'a Checks heading', html.slice(0, 30))
check((html.match(/data-check="/g) || []).length === rows.length, 'one button per row', html)
check((html.match(/data-still="1"/g) || []).length === 1, 'only the row with no target is still', html)
check(!html.includes('<b>') && html.includes('Crystal Defense'), 'rows carry their text', html)
check(checksHtml([]) === '', 'no rows: no section', checksHtml([]))
check(JSON.stringify(targetOf(rows[0])) === JSON.stringify({ plan: { kind: 'spawner', id: 7 }, at: [1, 2, 3] }), 'a plan target', targetOf(rows[0]))
check(JSON.stringify(targetOf(rows[2])) === JSON.stringify({ wiring: 'OMDPathPoint3', at: [4, 5, 6] }), 'a level actor', targetOf(rows[2]))
check(JSON.stringify(targetOf(rows[5])) === JSON.stringify({ added: 2, at: [7, 8, 9] }), 'an added piece', targetOf(rows[5]))
check(targetOf(rows[4]) === null, 'nothing to point at', targetOf(rows[4]))
check(JSON.stringify(targetOf({ text: 'x', at: [1, 1, 1] })) === JSON.stringify({ at: [1, 1, 1] }), 'a position alone', null)
check(checksHtml([{ level: 'error', text: '<b>x</b>' }]).includes('&lt;b&gt;'), 'text is escaped', null)
console.log(failed ? `${failed} failed` : 'all passed')
process.exit(failed ? 1 : 0)
