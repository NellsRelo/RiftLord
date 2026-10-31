// The browser half of testservers.py: Chrome profiles on scratch, recorded in the same
// registry (~/.cache/rift-lord/testservers) so `python3 testservers.py cleanup` removes a
// crashed run's profile, and the servers' addresses read from the shell's /api/tools.
'use strict'
const fs = require('fs')
const os = require('os')
const path = require('path')

const SCRATCH = process.env.RIFTLORD_TEST_SCRATCH ||
  (fs.existsSync('/mnt/2tb_pro_a/tmp') ? '/mnt/2tb_pro_a/tmp/rift-lord-tests'
    : path.join(os.tmpdir(), 'rift-lord-tests'))
const REGISTRY = path.join(os.homedir(), '.cache', 'rift-lord', 'testservers')
//: Where puppeteer-core is installed: outside the repo (README, "Automated checks").
const MODULES = process.env.RL_UITEST_MODULES ||
  path.join(os.homedir(), '.cache', 'rift-lord', 'uitest', 'node_modules')
const GUARDS = { shell: 'X-Workshop', riftlord: 'X-Rift-Lord', onslaught: 'X-Onslaught-Editor' }

function puppeteer () {
  try {
    return require(path.join(MODULES, 'puppeteer-core'))
  } catch (e) {
    throw new Error(`puppeteer-core is not installed in ${MODULES}: ` +
      `mkdir -p ${path.dirname(MODULES)} && cd ${path.dirname(MODULES)} && npm install puppeteer-core@23.10.4`)
  }
}

const live = []

/** A fresh profile folder, recorded until removeProfile(). */
function profile (prefix = 'chrome') {
  fs.mkdirSync(SCRATCH, { recursive: true })
  const dir = fs.mkdtempSync(path.join(SCRATCH, prefix + '-'))
  fs.mkdirSync(REGISTRY, { recursive: true })
  const reg = path.join(REGISTRY, `profile-${process.pid}-${Date.now()}.json`)
  fs.writeFileSync(reg, JSON.stringify({ type: 'profile', path: dir, owner: process.pid }))
  live.push({ dir, reg })
  return dir
}

function removeProfile (dir) {
  // a Chromium helper may still write for a moment after the browser closes: retry, and
  // keep the registry entry (for `testservers.py cleanup`) until the folder is gone
  if (path.resolve(dir).startsWith(path.resolve(SCRATCH) + path.sep)) {
    for (let i = 0; i < 20 && fs.existsSync(dir); i++) {
      try { fs.rmSync(dir, { recursive: true, force: true }) } catch { }
      if (fs.existsSync(dir)) Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 250)
    }
  }
  for (const item of live.filter(x => x.dir === dir)) {
    live.splice(live.indexOf(item), 1)
    if (!fs.existsSync(dir)) { try { fs.unlinkSync(item.reg) } catch { } }
  }
}

function removeAll () { for (const { dir } of [...live]) removeProfile(dir) }
process.on('exit', removeAll)
for (const sig of ['SIGINT', 'SIGTERM', 'SIGHUP']) process.on(sig, () => { removeAll(); process.exit(128) })

/** {shell, riftlord, onslaught} from the shell's /api/tools, trailing slashes dropped. */
async function tools (shell) {
  const r = await fetch(shell.replace(/\/$/, '') + '/api/tools', { headers: { [GUARDS.shell]: '1' } })
  if (!r.ok) throw new Error(`/api/tools answered ${r.status}`)
  const got = (await r.json()).tools
  const out = { shell: shell.replace(/\/$/, '') }
  for (const [k, v] of Object.entries(got)) out[k] = v.replace(/\/$/, '')
  return out
}

module.exports = { SCRATCH, REGISTRY, MODULES, GUARDS, puppeteer, profile, removeProfile, removeAll, tools }
