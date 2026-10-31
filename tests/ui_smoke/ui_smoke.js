#!/usr/bin/env node
// Rift Lord's UI smoke suite: drives the three pages a release must not break, in headless
// Chromium, against a running server.
//
//   node tests/ui_smoke/ui_smoke.js --shell URL [--mission ID] [--cases a,b] [--except c,d] [--json FILE]
//   python3 testservers.py run --frozen --mission 900_Fixture_Edit_P -- node tests/ui_smoke/ui_smoke.js
//
// --shell (or RL_SHELL) is the app server's shell page; the editors' addresses come from
// its /api/tools. --riftlord / --onslaught (RL_RIFTLORD / RL_ONSLAUGHT) override them.
// The mission must be in that server's Missions. Exit 0 when every case passed, 1 when
// one failed, 2 when the suite could not run. Uncaught page errors fail a case; console
// errors and HTTP errors are reported with it.
'use strict'
const fs = require('fs')
const ts = require('./testservers')

//: Seconds each case may take: the Map Editor loads a whole level.
const CASE_TIMEOUT = { onslaught: 240, mapeditor: 600, reference: 2400, preview: 600,
  rotation: 600, flip: 600, gridsize: 600, effects: 600, events: 600, saveas: 600,
  controllers: 600, compounds: 600, compoundbrowser: 600, compoundparams: 600, decor: 600, titlebar: 180, campaign: 300, maptoggles: 600, minimapdoors: 600,
  cutaway: 600, addlook: 600, pickups: 600, crystal: 600, crystalhealth: 1200, gamemode: 600, crystallevel: 900, supportedmods: 600, modunits: 240, scramble: 300, scrambletiers: 300, unlocks: 300, header: 1200,
  fab: 300, setup: 300, appshell: 240, mapeditorui: 900, spawnerfacing: 600, multirift: 600, openwarn: 900,
  onslaughtfloors: 600, campaignindex: 300, dialogue: 1200, floorsnap: 600, samename: 300, campaignpak: 300, retint: 600 }
//: Every case budget and page wait is multiplied by this (RL_TIMEOUT_SCALE, at least 1): the
//: release check raises it with the machine's load.
const SCALE = Math.max(1, Number(process.env.RL_TIMEOUT_SCALE) || 1)
//: Chromium's GPU path: software WebGL draws a level in ~11 minutes, ANGLE on the GPU in
//: seconds. RL_CHROME_FLAGS (JSON list) overrides.
const GPU_FLAGS = ['--use-gl=angle', '--use-angle=gl-egl', '--ignore-gpu-blocklist', '--enable-gpu']
//: The most a case's wall-clock budget is multiplied by, whatever SCALE is: a hung case
//: (it holds the GPU lock) ends within twice its budget.
const CASE_SCALE_MAX = 2
//: A page message that ends its case at once: the page cannot draw.
const FATAL = /Error creating WebGL context/
//: Pixels the music dropdown may change width by while the button changes icon.
const WIDTH_TOLERANCE = 0.5
const ICONS = { '▶': 'play', '■': 'stop' }

function args () {
  const out = { mission: process.env.RL_MISSION || '900_Fixture_Edit_P', cases: null, except: null, json: null,
    shell: process.env.RL_SHELL, riftlord: process.env.RL_RIFTLORD, onslaught: process.env.RL_ONSLAUGHT }
  const a = process.argv.slice(2)
  for (let i = 0; i < a.length; i++) {
    const k = a[i].replace(/^--/, '')
    if (k === 'help' || k === 'h') { console.log(fs.readFileSync(__filename, 'utf8').split('\n').slice(1, 13).join('\n')); process.exit(0) }
    if (!(k in out)) { console.error('unknown option ' + a[i]); process.exit(2) }
    out[k] = a[++i]
  }
  out.cases = out.cases ? out.cases.split(',') : null
  out.except = out.except ? out.except.split(',') : []
  return out
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms))

/** page.waitForFunction, failing with what never appeared rather than "Waiting failed". */
async function waitFor (p, label, fn, options, ...args) {
  try {
    return await p.waitForFunction(fn, options, ...args)
  } catch (e) {
    throw new Error(`${label} did not appear within ${Math.round((options.timeout || 30000) / 1000)} s` +
      ` (on ${p.url().slice(0, 100)})`)
  }
}

/** A page that records what went wrong on it. */
async function openPage (browser, log) {
  const p = await browser.newPage()
  await p.setViewport({ width: 1500, height: 920 })
  // page waits scaled by SCALE: the option object is the second argument of all three
  for (const name of ['waitForFunction', 'waitForSelector', 'goto']) {
    const plain = p[name].bind(p)
    p[name] = (first, options, ...rest) => plain(first, options && options.timeout
      ? { ...options, timeout: options.timeout * SCALE } : options, ...rest)
  }
  // a FATAL message rejects p.fatal, which the case runner races the case against
  p.fatal = new Promise((resolve, reject) => { p.fatalReject = reject })
  p.fatal.catch(() => {})
  const fatal = (text) => { if (FATAL.test(text)) p.fatalReject(new Error('the page cannot draw: ' + text.slice(0, 200))) }
  p.on('pageerror', e => { log.pageErrors.push(e.message + ' | ' + (e.stack || '').split('\n').slice(1, 3).join(' | ')); fatal(e.message) })
  p.on('console', m => { if (m.type() === 'error') { log.console.push(m.text().slice(0, 300)); fatal(m.text()) } })
  p.on('response', r => { if (r.status() >= 400) log.http.push(r.status() + ' ' + r.url().slice(0, 160)) })
  p.on('requestfailed', r => log.http.push('failed ' + (r.failure() || {}).errorText + ' ' + r.url().slice(0, 160)))
  return p
}

/** Click `selector` from inside the page, finding and clicking in one step: a panel that
 * redraws in between cannot leave a detached node, as puppeteer's two-step click can. */
async function clickIn (p, selector, timeout = 30000) {
  await waitFor(p, selector, (s) => !!document.querySelector(s), { timeout, polling: 100 }, selector)
  const done = await p.evaluate((s) => {
    const el = document.querySelector(s)
    if (el) el.click()
    return !!el
  }, selector)
  if (!done) throw new Error(`${selector} went before it could be clicked`)
}

function check (cond, what, log) {
  log.steps.push((cond ? 'ok   ' : 'FAIL ') + what)
  if (!cond) throw new Error(what)
}

/** A timing limit: `ms` must be under `limitMs`. A miss fails the case and is recorded as
 * log.timing {what, seconds, limit}, so the gate can tell a case that failed only on time
 * (and may run it again once the machine is quieter). */
function checkTime (ms, limitMs, what, log) {
  if (ms >= limitMs) log.timing = { what, seconds: +(ms / 1000).toFixed(1), limit: +(limitMs / 1000).toFixed(1) }
  check(ms < limitMs, what, log)
}

// -- cases ---------------------------------------------------------------------------

/** Onslaught Editor with a mission: "+ Wave" adds a wave; a door tab, then Timeline. */
async function onslaught (p, urls, mission, log) {
  await p.goto(`${urls.onslaught}/?embedded=1&theme=dark&bucket=${encodeURIComponent(mission)}&open=1`,
    { waitUntil: 'domcontentloaded', timeout: 60000 })
  const waves = () => p.evaluate(() => document.body.innerText.match(/Wave \d+/g)?.length || 0)
  await waitFor(p, 'the Onslaught Editor with the mission\'s waves and a "+ Wave" button',
    () => [...document.querySelectorAll('button')].some(b => b.textContent.trim() === '+ Wave') &&
    /Wave \d+/.test(document.body.innerText), { timeout: 180000, polling: 500 })
  await sleep(1000)
  const before = await waves()
  check(before > 0, `mission opened with ${before} waves`, log)
  await p.evaluate(() => [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '+ Wave').click())
  await p.waitForFunction((n) => (document.body.innerText.match(/Wave \d+/g)?.length || 0) > n, { timeout: 10000 }, before).catch(() => {})
  const after = await waves()
  check(after === before + 1, `+ Wave: ${before} -> ${after} waves`, log)
  const tabs = await p.evaluate(() => [...document.querySelectorAll('[role=tab]')].map(t => t.textContent.trim()))
  const door = tabs.findIndex(t => /^Door/.test(t))
  check(tabs[0] === 'Timeline' && door > 0, `tabs: ${tabs.slice(0, 6).join(', ')}`, log)
  const on = () => p.evaluate(() => (document.querySelector('[role=tab].on') || {}).textContent?.trim())
  await p.evaluate((i) => document.querySelectorAll('[role=tab]')[i].click(), door)
  await sleep(800)
  const onDoor = await on()
  check(onDoor === tabs[door], `door tab selected (${onDoor})`, log)
  await doorEdits(p, log)
  await p.evaluate(() => document.querySelectorAll('[role=tab]')[0].click())
  await sleep(800)
  const onTimeline = await on()
  check(onTimeline === 'Timeline', `back to Timeline (${onTimeline})`, log)
  await parTime(p, log)
  // a map chosen from the list opens with its own onslaught
  await p.evaluate(() => {
    const s = document.querySelector('select[aria-label="Map"]'); s.value = 'Scenario_02'
    s.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await waitFor(p, 'Scenario_02 with its own waves', () =>
    document.querySelector('select[aria-label="Map"]').value === 'Scenario_02' &&
    !document.querySelector('.loading') && document.querySelectorAll('.timeline > section[id^="wave-"]').length > 0,
  { timeout: 60000, polling: 250 })
  const own = await p.evaluate(() => document.querySelectorAll('.timeline > section[id^="wave-"]').length)
  check(own === 5, `Scenario_02 opened with its own ${own} waves (5 shipped)`, log)
}

/** Onslaught Editor on Scenario_01: an installed mod's minions (modminions) are in the
 * minion picker, each family once: a family a shipped one shares holds both, after its
 * shipped units; the rest come after the shipped families and before Scramble mode,
 * labelled by DisplayName, and one can be picked. Passes as skipped with no mod. */
async function modunits (p, urls, _mission, log) {
  const units = await (await fetch(`${urls.onslaught}/api/units`)).json()
  const mine = units.filter(u => u.mod)
  if (!mine.length) { check(true, 'no mod minions installed: skipped', log); return }
  check(mine.every(u => u.family && u.label && u.guid?.length === 4),
    `${mine.length} mod minions from /api/units: ${mine.map(u => u.label).join(', ')}`, log)
  await p.goto(`${urls.onslaught}/?embedded=1&theme=dark&map=Scenario_01`,
    { waitUntil: 'domcontentloaded', timeout: 60000 })
  await waitFor(p, 'Scenario_01\'s waves', () => document.querySelectorAll('[role=tab]').length > 1 &&
    !document.querySelector('.loading'), { timeout: 120000, polling: 250 })
  await p.evaluate(() => [...document.querySelectorAll('[role=tab]')].find(t => /^Door/.test(t.textContent.trim())).click())
  await waitFor(p, 'a minion picker on the door tab', () => document.querySelector('.row select[aria-label="Minion"]'),
    { timeout: 20000, polling: 250 })
  const groups = await p.evaluate(() => [...document.querySelector('.row select[aria-label="Minion"]')
    .querySelectorAll('optgroup')].map(g => ({ label: g.label, options: [...g.querySelectorAll('option')].map(o => o.textContent.trim()) })))
  const families = [...new Set(mine.map(u => u.family))]
  //: a mod family a shipped family shares joins it; the rest come after every shipped family
  const shippedFamilies = new Set(units.filter(u => !u.mod && !u.scramble).map(u => u.family))
  const modOnly = families.filter(f => !shippedFamilies.has(f))
  check(groups.every((g, i) => groups.findIndex(h => h.label === g.label) === i),
    `each family once: ${groups.map(g => g.label).join(', ')}`, log)
  for (const fam of families) {
    const at = groups.findIndex(g => g.label === fam)
    const want = mine.filter(u => u.family === fam && !u.flyer).map(u => u.label)
    const shippedAfter = shippedFamilies.has(fam) ? false
      : groups.slice(at + 1).some(g => g.label !== 'Scramble mode' && !modOnly.includes(g.label))
    check(at >= 0 && !shippedAfter && want.every(l => groups[at].options.some(o => o.startsWith(l))),
      `group "${fam}" at ${at + 1} of ${groups.length}${shippedFamilies.has(fam) ? ', a shipped family' : ', after the shipped families'}: ${at >= 0 ? groups[at].options.join(', ') : 'missing'}`, log)
  }
  const pick = mine.find(u => !u.flyer)
  if (!pick) return
  const value = await p.evaluate((name) => {
    const s = document.querySelector('.row select[aria-label="Minion"]')
    s.value = name
    s.dispatchEvent(new Event('change', { bubbles: true }))
    return s.value
  }, pick.name)
  await sleep(300)
  const kept = await p.evaluate(() => document.querySelector('.row select[aria-label="Minion"]').value)
  check(value === pick.name && kept === pick.name, `picked ${pick.label} (${kept})`, log)
}

/** On a door tab: "+" under a row inserts one directly below it, Clear empties the door's
 * wave, and Copy Door Waves copies one wave of another map's door into a chosen wave. */
async function doorEdits (p, log) {
  // the first wave section with rows on this door
  const rowsIn = (i) => p.evaluate((i) => {
    const s = document.querySelectorAll('.form section')[i]
    return s ? [...s.querySelectorAll('.row select[aria-label="Minion"]')].map(x => x.value) : null
  }, i)
  const at = await p.evaluate(() => [...document.querySelectorAll('.form section')]
    .findIndex(s => s.querySelector('.row')))
  check(at >= 0, `a wave with rows on this door (section ${at + 1})`, log)
  const before = await rowsIn(at)
  await p.evaluate((i) => document.querySelectorAll('.form section')[i].querySelector('.row .below').click(), at)
  await sleep(300)
  const inserted = await rowsIn(at)
  check(inserted.length === before.length + 1 && inserted[1] === '' && inserted[0] === before[0] &&
    JSON.stringify(inserted.slice(2)) === JSON.stringify(before.slice(1)),
    `insert below row 1: ${before.length} -> ${inserted.length} rows, the new one second`, log)
  await p.evaluate((i) => document.querySelectorAll('.form section')[i].querySelector('header .clear').click(), at)
  await sleep(300)
  const cleared = await rowsIn(at)
  check(cleared.length === 0, `Clear: ${inserted.length} -> ${cleared.length} rows in that wave`, log)
  const others = await p.evaluate(() => [...document.querySelectorAll('.form section')]
    .reduce((n, s) => n + s.querySelectorAll('.row').length, 0))
  check(true, `${others} rows left in the door's other waves`, log)

  // Copy Door Waves: a map with a door of this type, its door, wave 1, into the cleared wave
  const t = Date.now()
  await p.evaluate(() => [...document.querySelectorAll('button')].find(b => /^Copy Door Waves/.test(b.textContent.trim())).click())
  await waitFor(p, 'the copy picker\'s map list', () => document.querySelectorAll('.menu .maps .pick').length > 0,
    { timeout: 20000 })
  const maps = await p.evaluate(() => [...document.querySelectorAll('.menu .maps .pick')].map(b => b.textContent.trim()))
  let picked = null
  for (let k = 0; k < maps.length && !picked; k++) {
    await p.evaluate((k) => {
      if (!document.querySelector('.menu .maps .pick')) document.querySelector('.menu [data-pick=map]').click()
    })
    await sleep(100)
    await p.evaluate((k) => document.querySelectorAll('.menu .maps .pick')[k].click(), k)
    await waitFor(p, 'the copy picker\'s door list', () => document.querySelector('.menu [data-pick=door]') &&
      document.querySelector('.menu [data-pick=door][aria-expanded=true]') && !/Reading/.test(document.querySelector('.menu').innerText),
    { timeout: 60000, polling: 100 })
    const doors = await p.evaluate(() => document.querySelectorAll('.menu .maps .pick').length)
    if (doors) picked = maps[k]
  }
  check(!!picked, `copy picker: a door of this type on ${picked}`, log)
  await p.evaluate(() => document.querySelector('.menu .maps .pick').click())
  await waitFor(p, 'the copy picker\'s wave list', () => document.querySelector('.menu [data-pick=wave][aria-expanded=true]'),
    { timeout: 5000 })
  const waves = await p.evaluate(() => [...document.querySelectorAll('.menu .maps .pick')].map(b => b.textContent.trim()))
  check(waves[0] === 'All waves' && waves[1] === 'Wave 1', `wave choices: ${waves.slice(0, 4).join(', ')}…`, log)
  await p.evaluate(() => document.querySelectorAll('.menu .maps .pick')[1].click())
  await sleep(200)
  const folded = await p.evaluate(() => !document.querySelector('.menu .maps') && !!document.querySelector('.menu select[aria-label="Into wave"]'))
  check(folded, 'pickers folded; an Into wave choice shown', log)
  await p.evaluate((i) => {
    const s = document.querySelector('.menu select[aria-label="Into wave"]'); s.value = String(i); s.dispatchEvent(new Event('change'))
  }, at)
  await p.evaluate(() => document.querySelector('.menu .go').click())
  await waitFor(p, 'the copy to land', () => !document.querySelector('.menu .go') && /Copied wave 1 of/.test(document.body.innerText),
    { timeout: 30000, polling: 100 })
  const copied = await rowsIn(at)
  const after = await p.evaluate(() => [...document.querySelectorAll('.form section')]
    .reduce((n, s) => n + s.querySelectorAll('.row').length, 0))
  check(copied.length > 0 && copied.every(u => u) && after - copied.length === others,
    `copied wave 1 of ${picked} into wave ${at + 1}: ${copied.length} rows, other waves unchanged ` +
    `(${((Date.now() - t) / 1000).toFixed(1)} s)`, log)
}

/** On the Timeline: Par time shows the map's own as its placeholder; a value is kept and
 * shown as minutes, and Reset brings back the map's own. */
/** Par time is two fields, minutes and seconds, kept as seconds in the spec: seconds
 * past 59 carry into the minutes, and Reset goes back to the map's own. */
async function parTime (p, log) {
  const MIN = 'input[aria-label="Par time minutes"]'
  const SEC = 'input[aria-label="Par time seconds"]'
  const own = await p.evaluate((a, b) => [document.querySelector(a)?.placeholder,
    document.querySelector(b)?.placeholder], MIN, SEC)
  const ownSec = Number(own[0]) * 60 + Number(own[1])
  check(own[0] !== undefined && own[1] !== undefined && ownSec > 0,
    `Par time minutes and seconds fields, the map's own ${own[0]} min ${own[1]} sec`, log)
  // Svelte delegates change events to the page root, so the event must bubble
  const set = (sel, v) => p.evaluate((sel, v) => {
    const i = document.querySelector(sel); i.value = String(v); i.dispatchEvent(new Event('change', { bubbles: true }))
  }, sel, v)
  const read = () => p.evaluate((a, b) => ({ min: document.querySelector(a).value, sec: document.querySelector(b).value,
    set: document.querySelector(a).classList.contains('set'),
    reset: !![...document.querySelector(a).closest('section').querySelectorAll('header button')]
      .find(x => x.textContent.trim() === 'Reset') }), MIN, SEC)
  const minutes = Number(own[0]) === 7 ? 8 : 7
  await set(MIN, minutes)
  await sleep(300)
  await set(SEC, 75)
  await sleep(300)
  const got = await read()
  check(Number(got.min) === minutes + 1 && Number(got.sec) === 15 && got.set && got.reset,
    `${minutes} min then 75 sec reads ${got.min} min ${got.sec} sec (${(minutes + 1) * 60 + 15} s); Reset offered`, log)
  await p.evaluate((a) => [...document.querySelector(a).closest('section').querySelectorAll('header button')]
    .find(b => b.textContent.trim() === 'Reset').click(), MIN)
  await sleep(300)
  const back = await read()
  check(back.min === '' && back.sec === '', `Reset: par time back to the map's own (${ownSec} s)`, log)
  if (process.env.RL_SHOTS) {
    await set(MIN, minutes)
    await sleep(300)
    const box = await p.$(MIN)
    const section = box && await box.evaluateHandle((i) => i.closest('section'))
    if (section) await section.asElement().screenshot({ path: `${process.env.RL_SHOTS}/timeline-par.png` })
    await p.evaluate((a) => [...document.querySelector(a).closest('section').querySelectorAll('header button')]
      .find(b => b.textContent.trim() === 'Reset')?.click(), MIN)
  }
}

/** Full-length screenshots of the Map Editor's left and right sidebars into RL_SHOTS
 * (`<tag>-left.png`, `<tag>-right.png`), with every collapsible section open. */
async function sidebarShots (p, tag) {
  const saved = await p.evaluate(() => {
    const open = [...document.querySelectorAll('#side details:not([open]), #props details:not([open])')]
    for (const d of open) d.open = true
    const tall = Math.max(document.querySelector('#side').scrollHeight,
      document.querySelector('#props').scrollHeight, window.innerHeight)
    return { open: open.length, tall }
  })
  await p.setViewport({ width: 1500, height: saved.tall + 40 })
  await sleep(800)
  for (const [id, side] of [['#side', 'left'], ['#props', 'right']]) {
    const box = await p.$(id)
    if (box) await box.screenshot({ path: `${process.env.RL_SHOTS}/${tag}-${side}.png` })
  }
  await p.setViewport({ width: 1500, height: 920 })
  await sleep(500)
  // the level picker open, and the New Level dialog (cancelled)
  const lp = await p.$('.lp-btn')
  if (lp) {
    await lp.click()
    await sleep(400)
    await p.screenshot({ path: `${process.env.RL_SHOTS}/${tag}-levelpicker.png` })
    await p.keyboard.press('Escape')
  }
  await clickIn(p, '#newlevel')
  await p.waitForSelector('#scratchcancel', { timeout: 120000 }).catch(() => {})
  await sleep(300)
  await p.screenshot({ path: `${process.env.RL_SHOTS}/${tag}-newlevel.png` })
  const cancel = await p.$('#scratchcancel')
  if (cancel) await cancel.click()
  await sleep(300)
}

/** Map Editor with a mission: the level loads; the music button goes play -> spinner ->
 * stop -> play (the browser path), and the dropdown keeps its width. */
async function mapeditor (p, urls, mission, log) {
  await p.goto(`${urls.riftlord}/?embedded=1&theme=dark&bucket=${encodeURIComponent(mission)}&open=1`,
    { waitUntil: 'domcontentloaded', timeout: 60000 })
  const t = Date.now()
  // open, the map settings drawn, and the loading modal (which covers the page) gone
  await waitFor(p, 'the Map Editor with the level open, its Music button and no loading modal',
    () => window.__rl_levelOpen && window.__rl_levelOpen() && document.querySelector('#musicplay') &&
    document.querySelector('#loadmodal')?.hidden !== false, { timeout: 540000, polling: 1000 })
  const level = await p.evaluate(() => (window.__rl_levelReport && window.__rl_levelReport()) || null)
  check(true, `level open after ${((Date.now() - t) / 1000).toFixed(1)} s` +
    (level && level.level ? ` (${level.level})` : ''), log)
  if (process.env.RL_SHOTS) await sidebarShots(p, 'mapeditor')
  // every state the button shows, in order, from a watcher set before the click
  await p.evaluate(() => {
    window.__smokeIcons = []
    window.__smokeLog = []
    const t0 = performance.now()
    // the panel re-renders (the button is replaced), so watch the whole page
    const note = () => {
      const b = document.querySelector('#musicplay')
      const s = !b ? 'none' : b.querySelector('.spinner') ? 'spinner' : b.textContent.trim()
      if (window.__smokeIcons[window.__smokeIcons.length - 1] !== s) {
        window.__smokeIcons.push(s)
        window.__smokeLog.push(Math.round(performance.now() - t0) + 'ms ' + s)
      }
    }
    note()
    new MutationObserver(note).observe(document.body, { childList: true, characterData: true, subtree: true })
  })
  // the panel re-renders: measure in one step, again until both are in the page
  const geo = async () => {
    for (let i = 0; i < 200; i++) {
      const got = await measure()
      if (got) return got
      await sleep(25)
    }
    throw new Error('the music dropdown and button did not come back after a re-render')
  }
  const measure = () => p.evaluate(() => {
    if (!document.querySelector('#levelmusic') || !document.querySelector('#musicplay')) return null
    const s = document.querySelector('#levelmusic').getBoundingClientRect()
    const b = document.querySelector('#musicplay').getBoundingClientRect()
    return { select: s.width, button: b.width, right: b.right, icon: document.querySelector('#musicplay .spinner') ? 'spinner' : document.querySelector('#musicplay').textContent.trim() }
  })
  const idle = await geo()
  check(ICONS[idle.icon] === 'play', `idle icon ${idle.icon}`, log)
  await clickIn(p, '#musicplay')       // the panel re-renders: find and click in one step
  await sleep(30)
  const loading = await geo()
  // until it plays, or falls back to idle (a failed fetch or play() stops it)
  await p.waitForFunction(() => { const i = window.__smokeIcons; const now = i[i.length - 1]
    return now === '\u25A0' || (i.length > 1 && now === '\u25B6') }, { timeout: 300000, polling: 100 }).catch(() => {})
  const playing = await geo()
  const seqAt = async () => (await p.evaluate(() => window.__smokeIcons)).map(s => ICONS[s] || s)
  check(ICONS[playing.icon] === 'stop', `playing icon ${playing.icon} (so far: ${(await p.evaluate(() => window.__smokeLog)).join(', ')})`, log)
  await clickIn(p, '#musicplay')       // the panel re-renders: find and click in one step
  await sleep(300)
  const stopped = await geo()
  const seq = await seqAt()
  check(JSON.stringify(seq) === JSON.stringify(['play', 'spinner', 'stop', 'play']), `icon sequence ${seq.join(' -> ')}`, log)
  const widths = [idle, loading, playing, stopped].map(g => g.select)
  const spread = Math.max(...widths) - Math.min(...widths)
  check(spread <= WIDTH_TOLERANCE, `dropdown width ${widths.map(w => w.toFixed(1)).join(' / ')} px`, log)
  const bw = [idle, loading, playing, stopped].map(g => g.button)
  check(Math.max(...bw) - Math.min(...bw) <= WIDTH_TOLERANCE, `button width ${bw.map(w => w.toFixed(1)).join(' / ')} px`, log)
}

//: Share of the ghost's pixels without its depth passes it must still cover with them.
const COVERAGE_SHARE = 0.999
//: Cutaway slider positions: off, half way (for the screenshot), and the bottom.
const CUT_OFF = 1000
const CUT_HALF = 500
const CUT_LOW = 0
//: Uu the ghost is raised to show the cutaway range following it.
const RAISE = 20000
//: The share of the ghost's pixels a cut at the slider's bottom may leave.
const GHOST_CUT_SHARE = 0.5

/** Map Editor with a mission: the left sidebar's Trace section draws the level's own OMD3
 * geometry, then OMD2 TheCrunch, OMD1 The Bridge, Unchained PvE_TheWall and Deathtrap
 * BackAlley (each when installed), as a ghost with a depth pass and no frustum culling;
 * opacity and the display mode reach its material and never the level's (every level
 * material keeps its settings and stays opaque at trace opacity 0.2 and 1); each
 * game's Textured display has textures; the state is kept for the mission, and None
 * hides it. RL_SHOTS (a folder) keeps, per game, the ghost alone and the ghost over the
 * level at opacity 0.2 and 1 from three sides. */
async function reference (p, urls, mission, log) {
  await p.goto(`${urls.riftlord}/?embedded=1&theme=dark&bucket=${encodeURIComponent(mission)}&open=1`,
    { waitUntil: 'domcontentloaded', timeout: 60000 })
  const t = Date.now()
  await waitFor(p, 'the Map Editor with the level open, the Trace section and no loading modal',
    () => window.__rl_levelOpen && window.__rl_levelOpen() && window.__rl_trace &&
    document.querySelector('#tracebox:not([hidden]) #trgame') &&
    document.querySelector('#loadmodal')?.hidden !== false,
    { timeout: 540000, polling: 1000 })
  check(true, `level open after ${((Date.now() - t) / 1000).toFixed(1)} s`, log)
  await waitFor(p, 'the Trace section\'s game list', () => window.__rl_trace().games.length > 0,
    { timeout: 60000, polling: 250 })
  let r = await p.evaluate(() => window.__rl_trace())
  check(true, `games: ${r.games.map(g => `${g.id} (${g.levels})`).join(', ')}`, log)
  await p.evaluate(() => { document.querySelector('#tracebox').open = true })
  const shots = process.env.RL_SHOTS
  const report = () => p.evaluate(() => window.__rl_trace())
  const setMode = (mode) => p.evaluate((mode) => {
    const m = document.querySelector('#trmode'); m.value = mode; m.dispatchEvent(new Event('change'))
  }, mode)
  const setOpacity = (v) => p.evaluate((v) => {
    const o = document.querySelector('#tropacity'); o.value = String(v); o.dispatchEvent(new Event('input'))
  }, v)
  const layers = (on) => p.evaluate((on) => {
    document.activeElement && document.activeElement.blur()
    for (const k of ['level', 'foliage']) {
      const c = document.querySelector(`input[data-layer="${k}"]`)
      if (c && c.checked !== on) { c.checked = on; c.dispatchEvent(new Event('change', { bubbles: true })) }
    }
  }, on)
  /** Pick a game in the section, then `level` of it when given (else its first level,
   * or the open level for OMD3), and wait for its ghost; returns the tool's report. */
  const pick = async (game, label, level) => {
    await p.evaluate((game) => {
      const s = document.querySelector('#trgame'); s.value = game; s.dispatchEvent(new Event('change'))
    }, game)
    const want = await p.evaluate((game, level) => {
      const s = document.querySelector('#trlevel')
      if (!level || !s || ![...s.options].some(o => o.value === level)) return null
      if (s.value !== level) { s.value = level; s.dispatchEvent(new Event('change')) }
      return level
    }, game, level)
    await waitFor(p, label, (game, want) => {
      const r = window.__rl_trace()
      return (want ? r.source === game + '\u0001' + want : r.source.startsWith(game + '\u0001')) &&
        !r.loading && (r.error || (r.visible && r.triangles > 0))
    }, { timeout: 420000, polling: 250 }, game, want)
    await sleep(500)
    return report()
  }
  /** The Textured display of the source on screen: its base-colour textures arrive. */
  const textured = async (name) => {
    await setMode('textured')
    await waitFor(p, `the ${name} Textured display`, () => {
      const t = window.__rl_trace(); return t.texError || (!t.texLoading && t.textured)
    }, { timeout: 420000, polling: 250 })
    const t = await report()
    check(t.textured && t.textures > 0 && t.texturedParts > 0 && !t.texError,
      `${name} Textured: ${t.textures} textures, ${t.texturedParts} textured parts, ` +
      `${t.untexturedParts} untextured` + (t.texError ? ` (error: ${t.texError})` : ''), log)
    return t
  }
  /** The level's own materials, as the trace's opacity goes to 0.2 and 1: unchanged and
   * drawn opaque, and the ghost's opacity follows the slider. */
  const levelOpaque = async (name, before) => {
    for (const v of [0.2, 1]) {
      await setOpacity(v)
      const now = await p.evaluate(() => window.__rl_levelMaterials())
      const t = await report()
      const ghost = t.mode === 'textured' ? t.texOpacity : t.opacity
      check(before.opaque > 0 && now.signature === before.signature && now.opaque === before.opaque &&
        Math.abs(ghost - v) < 1e-6,
      `${name} at trace opacity ${v}: level ${now.opaque} of ${now.total} materials opaque, ` +
        `${now.signature === before.signature ? 'unchanged' : 'CHANGED'}; ghost opacity ${ghost}`, log)
    }
  }
  /** RL_SHOTS: the ghost alone, then over the level at opacity 0.2 and 1 from three
   * sides (so popping on orbiting shows as a difference between the pictures). */
  const pictures = async (tag) => {
    if (!shots) return
    const t = await report()
    await layers(false)
    await sleep(600)
    await p.screenshot({ path: `${shots}/trace-${tag}.png` })
    await layers(true)
    const span = t.size ? Math.max(t.size[0], t.size[1]) : 8000
    const reach = Math.min(40000, Math.max(2500, span * 0.6))
    for (const v of [0.2, 1]) {
      await setOpacity(v)
      for (const [k, az] of [[1, 30], [2, 150], [3, 270]]) {
        const a = az * Math.PI / 180
        const el = 40 * Math.PI / 180
        const at = t.at
        const pos = [at[0] + reach * Math.cos(a) * Math.cos(el), at[1] + reach * Math.sin(a) * Math.cos(el),
          at[2] + reach * Math.sin(el)]
        await p.evaluate((pos, at) => window.__rl_view(pos, at), pos, at)
        await sleep(900)
        await p.screenshot({ path: `${shots}/trace-${tag}-opacity${v}-view${k}.png` })
      }
    }
  }
  /** Look at the ghost from `az` degrees round and 40 up, at a distance fitting its size. */
  const lookAtGhost = async (az = 30) => {
    const t = await report()
    const span = t.size ? Math.max(t.size[0], t.size[1]) : 8000
    const reach = Math.min(40000, Math.max(2500, span * 0.6))
    const a = az * Math.PI / 180
    const el = 40 * Math.PI / 180
    const pos = [t.at[0] + reach * Math.cos(a) * Math.cos(el), t.at[1] + reach * Math.sin(a) * Math.cos(el),
      t.at[2] + reach * Math.sin(el)]
    await p.evaluate((pos, at) => window.__rl_view(pos, at), pos, t.at)
    await sleep(300)
  }
  /** The colour pass passes depth against its own pre-pass: every drawn part pairs its
   * colour material with a copy of it as its depth pass (same program, less-or-equal,
   * nearer by the margin, stencil once), and from two sides the ghost alone covers the
   * same pixels with its depth passes as without them. */
  const passes = async (name) => {
    const t = await report()
    const got = []
    for (const az of [30, 210]) {
      await lookAtGhost(az)
      const withDepth = await p.evaluate(() => window.__rl_traceCoverage(true))
      const without = await p.evaluate(() => window.__rl_traceCoverage(false))
      got.push([withDepth.covered, without.covered])
    }
    const ok = got.every(([a, b]) => b > 0 && a / b >= COVERAGE_SHARE)
    check(t.colourPassesDepth && t.stencil && ok,
      `${name}: ${t.passPairs} parts pass depth against their own pre-pass ` +
      `(${t.colourPassesDepth}), stencil ${t.stencil}; pixels with/without depth pass ` +
      got.map(([a, b]) => `${a}/${b}`).join(', '), log)
  }
  /** The cutaway's range takes in the ghost's heights (raised RAISE uu above the level,
   * the slider's top follows it) and its plane clips the ghost. */
  const ghostCut = async (name) => {
    const slide = (step) => p.evaluate((step) => {
      const s = document.querySelector('#cutaway')
      s.dispatchEvent(new Event('pointerdown'))
      s.value = String(step)
      s.dispatchEvent(new Event('input'))
      return window.__rl_cut()
    }, step)
    const setHeight = (z) => p.evaluate((z) => {
      const i = document.querySelector('#trz'); i.value = String(z); i.dispatchEvent(new Event('input'))
    }, z)
    const show = (on) => p.evaluate((on) => {
      const c = document.querySelector('#trvisible'); c.checked = on; c.dispatchEvent(new Event('change'))
    }, on)
    await show(false)
    const own = await slide(CUT_OFF)
    await show(true)
    const t = await report()
    await setHeight(t.at[2] + RAISE)
    const raised = await slide(CUT_OFF)
    const top = (await report()).heights
    check(own.range && raised.range && raised.range[1] > own.range[1] + RAISE / 2 && top &&
      raised.range[1] >= top[0],
    `${name}: cutaway range ${own.range.map(Math.round).join(' to ')} without the ghost, ` +
      `${raised.range.map(Math.round).join(' to ')} with it raised ${RAISE} uu (ghost ` +
      `${top ? top.map(Math.round).join(' to ') : 'none'})`, log)
    await setHeight(t.at[2])
    await slide(CUT_OFF)
    await lookAtGhost()
    const whole = await p.evaluate(() => window.__rl_traceCoverage(true))
    const cut = await slide(CUT_LOW)
    const clipped = await p.evaluate(() => window.__rl_traceCoverage(true))
    await slide(CUT_HALF)
    if (process.env.RL_SHOTS) {
      await layers(false)
      await sleep(600)
      await p.screenshot({ path: `${process.env.RL_SHOTS}/trace-cutaway-${name.toLowerCase().replace(/\W+/g, '')}.png` })
      await layers(true)
    }
    await slide(CUT_OFF)
    check(whole.clipped && whole.covered > 0 && clipped.covered < whole.covered * GHOST_CUT_SHARE,
      `${name}: cut at ${Math.round(cut.at)} leaves ${clipped.covered} of ${whole.covered} ghost pixels; ` +
      `every ghost material takes the cut plane: ${whole.clipped}`, log)
  }
  const games = Object.fromEntries(r.games.map(g => [g.id, g.levels]))
  const before = await p.evaluate(() => window.__rl_levelMaterials())
  check(before.total > 0 && before.opaque > 0, `level materials: ${before.opaque} of ${before.total} opaque`, log)
  r = await pick('omd3', 'the OMD3 ghost')
  check(!r.error && r.triangles > 0 && r.parts > 0, `OMD3 ghost ${r.source.split('\u0001')[1]}: ` +
    `${r.triangles} triangles in ${r.parts} parts at ${r.at.map(Math.round).join(', ')}` +
    (r.error ? ` (error: ${r.error})` : ''), log)
  // drawn with a depth pass and never culled
  check(r.depthPass && !r.culled, `depth pass ${r.depthPass}, frustum culled ${r.culled}`, log)
  await passes('OMD3 ghost')
  await levelOpaque('OMD3 ghost', before)
  // opacity and the display mode reach the material
  await p.evaluate(() => {
    const o = document.querySelector('#tropacity'); o.value = '0.3'; o.dispatchEvent(new Event('input'))
    const m = document.querySelector('#trmode'); m.value = 'wire'; m.dispatchEvent(new Event('change'))
  })
  r = await report()
  check(Math.abs(r.opacity - 0.3) < 1e-6 && r.wireframe && !r.depthPass,
    `opacity ${r.opacity}, wireframe ${r.wireframe}, depth pass ${r.depthPass}`, log)
  // the Textured display: the level's own meshes with their base-colour textures
  await textured('OMD3')
  await passes('OMD3 Textured')
  await levelOpaque('OMD3 Textured', before)
  await pictures('omd3')
  // the source's trap grids
  await p.evaluate(() => { const c = document.querySelector('#trgrid'); c.checked = true; c.dispatchEvent(new Event('change')) })
  await waitFor(p, 'the trap grids', () => { const t = window.__rl_trace(); return !t.gridLoading && (t.gridError || t.gridFaces > 0) },
    { timeout: 120000, polling: 250 })
  r = await p.evaluate(() => window.__rl_trace())
  check(r.grid && r.gridFaces > 0 && !r.gridError, `Trap Grid: ${r.gridFaces} faces` +
    (r.gridError ? ` (error: ${r.gridError})` : ''), log)
  await p.evaluate(() => {
    const c = document.querySelector('#trgrid'); c.checked = false; c.dispatchEvent(new Event('change'))
    const m = document.querySelector('#trmode'); m.value = 'ghost'; m.dispatchEvent(new Event('change'))
  })
  for (const [game, name, level] of [['omd2', 'OMD2', 'TheCrunch'], ['omd1', 'OMD1', 'The Bridge'],
    ['omdu', 'Unchained', 'PvE_TheWall'], ['omdd', 'Deathtrap', 'Level_Castle_BackAlley']]) {
    if (!games[game]) { check(true, `${name} not installed or not readable: skipped`, log); continue }
    await setMode('ghost')
    const asked = Date.now()
    r = await pick(game, `the ${name} ghost`, level)
    check(!r.error && r.triangles > 0 && r.depthPass && !r.culled,
      `${name} ghost ${r.source.split('\u0001')[1]} after ${((Date.now() - asked) / 1000).toFixed(1)} s: ${r.triangles} ` +
      `triangles in ${r.parts} parts at ${r.at.map(Math.round).join(', ')}` +
      (r.error ? ` (error: ${r.error})` : ''), log)
    check(r.removed !== null && r.removed >= 0, `${name} ghost: ${r.removed} covered copies removed`, log)
    await passes(`${name} ghost`)
    if (game === 'omd2') await ghostCut(name)
    await textured(name)
    await passes(`${name} Textured`)
    await levelOpaque(`${name} Textured`, before)
    await pictures(game)
  }
  // kept for the mission, outside its plan
  await sleep(1500)
  const kept = await p.evaluate(async () => {
    const r = window.__rl_trace()
    const got = await (await fetch('api/trace/state/' + encodeURIComponent(r.key))).json()
    return { key: r.key, source: got.state && got.state.source, mine: r.source }
  })
  check(!!kept.source && `${kept.source.game}\u0001${kept.source.name}` === kept.mine,
    `state kept under ${kept.key}: ${kept.source && kept.source.game} ${kept.source && kept.source.name}`, log)
  await p.evaluate(() => { const s = document.querySelector('#trgame'); s.value = ''; s.dispatchEvent(new Event('change')) })
  r = await p.evaluate(() => window.__rl_trace())
  check(!r.visible && !r.source && r.triangles === 0, 'None hides the ghost', log)
}

//: A Map Editor mission built on a level from scratch, checked when the server has it.
const SCRATCH_MISSION = '900_Fixture_Scratch_P'

/** Map Settings' header card names the open mission: its title, then its own level
 * (`<mod>_Map01`); a mission on a shipped level adds that level as its Source Level, a
 * mission from scratch has none. */
async function header (p, urls, mission, log) {
  const missions = await (await fetch(`${urls.riftlord}/api/missions`)).json()
  for (const id of [mission, SCRATCH_MISSION]) {
    const rec = missions.find(m => m.id === id)
    if (!rec) { check(true, `${id} not in this server's Missions: skipped`, log); continue }
    await openMission(p, urls, id, log)
    await waitFor(p, 'the Map Settings header', () => document.querySelector('[data-mapsettings] b'),
      { timeout: 60000, polling: 250 })
    const got = await p.evaluate(() => {
      const c = document.querySelector('[data-mapsettings]')
      return { title: c.querySelector('b').textContent,
               meta: [...c.querySelectorAll('.meta')].map(m => m.textContent.trim()),
               level: window.__rl_levelName() }
    })
    const source = got.meta.find(m => m.startsWith('Source Level'))
    const scratch = id === SCRATCH_MISSION
    check(got.title === rec.title && (got.meta[0] || '').includes(rec.map) &&
      (scratch ? !source : !!source && source.includes(got.level)),
    `${id}: header "${got.title}" / ${got.meta.map(m => `"${m}"`).join(' / ')} ` +
      `(mission "${rec.title}", ${rec.map}, on ${got.level})`, log)
    if (process.env.RL_SHOTS) {
      const card = await p.$('[data-mapsettings]')
      if (card) await card.screenshot({ path: `${process.env.RL_SHOTS}/header-${id}.png` })
    }
  }
}

/** Onslaught Editor with a mission: its Enemy Preview tab shows the preview with an image. */
async function preview (p, urls, mission, log) {
  await p.goto(`${urls.onslaught}/?embedded=1&theme=dark&bucket=${encodeURIComponent(mission)}&open=1`,
    { waitUntil: 'domcontentloaded', timeout: 60000 })
  const sel = '[role=tab][data-key="preview"]'
  await p.waitForSelector(sel, { timeout: 180000 }).catch(() => { throw new Error('the Onslaught Editor with an Enemy Preview tab did not appear within 180 s') })
  await p.click(sel)
  check(true, 'Enemy Preview tab opened', log)
  await waitFor(p, 'the Enemy Preview panel', () => [...document.querySelectorAll('h4')].some(h => h.textContent.trim() === 'Enemy Preview'),
    { timeout: 120000 })
  check(true, 'Enemy Preview panel shown', log)
  const t = Date.now()
  await waitFor(p, 'the Enemy Preview image', () => { const i = document.querySelector('.ep-picture img'); return i && i.complete && i.naturalWidth > 0 },
    { timeout: 540000, polling: 500 })
  const img = await p.evaluate(() => { const i = document.querySelector('.ep-picture img'); return [i.naturalWidth, i.naturalHeight, i.src.slice(0, 22)] })
  check(img[0] > 0, `preview image ${img[0]}x${img[1]} (${img[2]}...) after ${((Date.now() - t) / 1000).toFixed(1)} s`, log)
  const units = await p.evaluate(() => document.querySelectorAll('.ep-unit').length)
  check(units > 0, `${units} enemy rows`, log)
}

// -- Map Editor editing tools (2026-10-21 round) ------------------------------------------

//: A kit piece the editing cases place: a 4x4 floor with a trap grid.
const KIT_PIECE = 'Floor_FS_Trim_v1_4x4_C'
//: Degrees a turn without shift snaps to (main.js TURN_SNAP_DEGREES).
const TURN_SNAP = 15
//: How far from a whole snap step a snapped turn may read, in degrees (float round trip).
const TURN_TOLERANCE = 0.05

/** Open the mission in the Map Editor and wait for the level and the page's hooks. */
async function openMission (p, urls, mission, log) {
  await p.goto(`${urls.riftlord}/?embedded=1&theme=dark&bucket=${encodeURIComponent(mission)}&open=1`,
    { waitUntil: 'domcontentloaded', timeout: 60000 })
  const t = Date.now()
  await waitFor(p, 'the Map Editor with the level open and no loading modal',
    () => window.__rl_levelOpen && window.__rl_levelOpen() && window.__rl_planBits &&
    document.querySelector('#loadmodal')?.hidden !== false, { timeout: 540000, polling: 1000 })
  check(true, `level open after ${((Date.now() - t) / 1000).toFixed(1)} s`, log)
}

/** Place KIT_PIECE beside the level's rift and pick it; its index in `added`. */
async function placeKit (p, log) {
  const n = await p.evaluate(async (name) => {
    const at = window.__rl_riftAt() || [0, 0, 0]
    const n = await window.__rl_addKit(name, [at[0] + 600, at[1] + 600, at[2]])
    window.__rl_pickAdded(n - 1)
    return n - 1
  }, KIT_PIECE)
  check(n >= 0, `placed ${KIT_PIECE} as added[${n}]`, log)
  return n
}

/** A turn of the gizmo snaps to TURN_SNAP degrees, and turns freely with shift. */
async function rotation (p, urls, mission, log) {
  await openMission(p, urls, mission, log)
  const n = await placeKit(p, log)
  const snapped = await p.evaluate(() => window.__rl_gizmoDrag('rotate', 'Z', [0.05, 0.05], [0.31, 0.17], false))
  const turn = await p.evaluate((i) => window.__rl_addedTurn(i), n)
  const off = Math.abs(turn.yaw / TURN_SNAP - Math.round(turn.yaw / TURN_SNAP)) * TURN_SNAP
  check(Math.abs(snapped.after - snapped.before) > 1 && off < TURN_TOLERANCE,
    `turn without shift: ${snapped.before.toFixed(2)} -> ${snapped.after.toFixed(2)} degrees, ` +
    `piece yaw ${turn.yaw.toFixed(2)} (${off.toFixed(3)} off a ${TURN_SNAP} degree step)`, log)
  const free = await p.evaluate(() => window.__rl_gizmoDrag('rotate', 'Z', [0.05, 0.05], [0.29, 0.13], true))
  const turned = await p.evaluate((i) => window.__rl_addedTurn(i), n)
  const offFree = Math.abs(turned.yaw / TURN_SNAP - Math.round(turned.yaw / TURN_SNAP)) * TURN_SNAP
  check(Math.abs(free.after - free.before) > 1 && offFree > TURN_TOLERANCE,
    `turn with shift: ${free.before.toFixed(2)} -> ${free.after.toFixed(2)} degrees, ` +
    `piece yaw ${turned.yaw.toFixed(2)} (free)`, log)
}

/** Flip Left/Right mirrors the piece (negative determinant) and survives a save; an
 * inverted turn keeps the scale positive. */
async function flip (p, urls, mission, log) {
  await openMission(p, urls, mission, log)
  const n = await placeKit(p, log)
  const before = await p.evaluate((i) => window.__rl_addedTurn(i), n)
  await p.evaluate(() => { document.querySelector('[data-flip="mirrorH"]').click() })
  const mirrored = await p.evaluate((i) => window.__rl_addedTurn(i), n)
  check(before.det > 0 && mirrored.det < 0, `Flip Left/Right button: determinant ${before.det} -> ${mirrored.det}`, log)
  const saved = await p.evaluate(async (i) => {
    await window.__rl_savePlan()
    const plan = await (await fetch('api/plan/' + encodeURIComponent(window.__rl_levelName()))).json()
    const m = plan.added[i].matrix
    const det = m[0] * (m[5] * m[10] - m[9] * m[6]) - m[4] * (m[1] * m[10] - m[9] * m[2]) +
      m[8] * (m[1] * m[6] - m[5] * m[2])
    return { det: Math.sign(det), kind: plan.added[i].kind, name: plan.added[i].name }
  }, n)
  check(saved.det < 0 && saved.name === KIT_PIECE, `saved plan keeps the mirror (${saved.kind} ${saved.name}, determinant ${saved.det})`, log)
  await p.evaluate(() => window.__rl_flip('turnH'))
  const inverted = await p.evaluate((i) => window.__rl_addedTurn(i), n)
  check(inverted.det < 0, `Invert Turn keeps the handedness (determinant ${inverted.det})`, log)
  // the M key, with the view focused, flips it back
  await p.focus('canvas')
  await p.keyboard.press('m')
  const back = await p.evaluate((i) => window.__rl_addedTurn(i), n)
  check(back.det > 0, `M key flips back (determinant ${back.det})`, log)
}

//: A trap grid's edge moves in steps of this many uu when its piece is resized
//: (main.js GRID_RESIZE_STEP), and KIT_PIECE's grid is this long each way.
const GRID_RESIZE_STEP = 150
const KIT_GRID_LENGTH = 1200

/** Resizing a kit piece with a trap grid keeps its grid on whole half cells. */
async function gridsize (p, urls, mission, log) {
  await openMission(p, urls, mission, log)
  const n = await placeKit(p, log)
  await sleep(500)
  await p.evaluate(() => window.__rl_gizmoDrag('scale', 'X', [0.0, 0.0], [0.13, 0.02], false))
  const scale = await p.evaluate((i) => window.__rl_addedScale(i), n)
  const length = scale[0] * KIT_GRID_LENGTH
  const off = Math.abs(length / GRID_RESIZE_STEP - Math.round(length / GRID_RESIZE_STEP))
  check(Math.abs(scale[0] - 1) > 1e-3 && off < 1e-3,
    `resized along x to ${scale[0].toFixed(4)}: the grid is ${length.toFixed(1)} uu, ` +
    `${(length / GRID_RESIZE_STEP).toFixed(3)} half cells`, log)
  const row = await p.evaluate(() => (document.querySelector('#propsbody').innerText
    .match(/Floor\s+([\d.]+ \u00d7 [\d.]+)/) || [])[1] || '')
  const cells = (length / 300).toFixed(1).replace(/\.0$/, '')
  check(row.split(' \u00d7 ').includes(cells), `Trap Grid section reads ${row} (${cells} along x)`, log)
}

/** Turn a Display Options layer on or off. */
async function layer (p, key, on) {
  await p.evaluate((key, on) => {
    const c = document.querySelector(`input[data-layer="${key}"]`)
    if (c && c.checked !== on) { c.checked = on; c.dispatchEvent(new Event('change', { bubbles: true })) }
  }, key, on)
}

/** Look straight down at Unreal point `at` from `height` uu above it. */
async function lookDownAt (p, at, height) {
  await p.evaluate((at, h) => window.__rl_view([at[0] + 1, at[1] + 1, at[2] + h], at), at, height)
  await sleep(400)
}

//: How high above a light or effect the camera looks down from to click it, in uu.
const PICK_VIEW_HEIGHT = 1500
//: How many of the level's own markers the effects case tries before it gives up on a click.
const EFFECT_PICK_TRIES = 6

//: A shipped level with Emitter effects and lights of its own (Scenario_02: 11 Emitters).
const EFFECTS_LEVEL = 'Scenario_02'

/** Open a shipped level (not a mission) in the Map Editor. */
async function openLevelNamed (p, urls, name, log) {
  await p.goto(`${urls.riftlord}/?embedded=1&theme=dark`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await waitFor(p, 'the Map Editor ready', () => window.__rl_ready, { timeout: 120000, polling: 250 })
  await p.evaluate((name) => {
    const s = document.querySelector('#level'); s.value = name; s.dispatchEvent(new Event('change'))
    document.querySelector('#load').click()
  }, name)
  const t = Date.now()
  await waitFor(p, `${name} open with no loading modal`,
    () => window.__rl_levelOpen && window.__rl_levelOpen() && document.querySelector('#loadmodal')?.hidden !== false,
    { timeout: 540000, polling: 1000 })
  check(true, `${name} open after ${((Date.now() - t) / 1000).toFixed(1)} s`, log)
}

/** Effects are off when a level opens. A level effect, a level sound and a level light
 * are picked by clicking them, removed with Delete, and Ctrl+Z puts them back. Placing an
 * effect turns Effects on; with Effects off no effect is drawn or picked. */
async function effects (p, urls, mission, log) {
  await openLevelNamed(p, urls, EFFECTS_LEVEL, log)
  const onOpen = await p.evaluate(() => ({ box: document.querySelector('input[data-layer="vfx"]').checked,
    ...window.__rl_effectsShown() }))
  check(!onOpen.box && onOpen.own === 0 && onOpen.added === 0,
    `Effects off on open (box ${onOpen.box}; ${onOpen.own} of the level's and ${onOpen.added} placed drawn)`, log)
  for (const [key, kind, label] of [['vfx', 'fx', 'effect'], ['vfx', 'sound', 'sound'],
    ['lights', 'light', 'light']]) {
    await layer(p, key, true)
    const marks = await p.evaluate((k) => window.__rl_levelActorMarks(k), kind)
    check(marks.length > 0, `${marks.length} ${label}(s) of the level's own to pick`, log)
    //: the first of a few with nothing solid over it from above (a sound in a room is
    //: under its roof, and a click on the roof picks the roof)
    const want = 'actor:' + (kind === 'sound' ? 'fx' : kind)
    let mark = marks[0]
    let kinds = []
    for (const m of marks.slice(0, EFFECT_PICK_TRIES)) {
      mark = m
      await p.evaluate(() => window.__rl_deselect())
      await lookDownAt(p, mark.at, PICK_VIEW_HEIGHT)
      const now = (await p.evaluate((k) => window.__rl_levelActorMarks(k), kind)).find(x => x.key === mark.key)
      await p.mouse.click(now.x, now.y)
      await sleep(300)
      kinds = await p.evaluate(() => window.__rl_pickedKinds())
      if (kinds.length === 1 && kinds[0] === want) break
    }
    check(kinds.length === 1 && kinds[0] === want, `clicked the ${label} ${mark.key}: picked ${kinds.join(', ') || 'nothing'}`, log)
    await p.keyboard.press('Delete')
    await sleep(300)
    let bits = await p.evaluate(() => window.__rl_planBits())
    check(bits.removedFx.includes(mark.key), `Delete removes it (plan removedFx has ${mark.key})`, log)
    const gone = !(await p.evaluate((k) => window.__rl_levelActorMarks(k), kind)).some(m => m.key === mark.key)
    check(gone, `its marker is hidden`, log)
    await p.keyboard.down('Control')
    await p.keyboard.press('z')
    await p.keyboard.up('Control')
    await sleep(300)
    bits = await p.evaluate(() => window.__rl_planBits())
    check(!bits.removedFx.includes(mark.key), 'Ctrl+Z puts it back', log)
    await layer(p, key, false)
  }
  //: with Effects off, no effect is drawn: the level's own, its sounds or a placed one
  await p.evaluate(() => window.__rl_deselect())
  await layer(p, 'vfx', false)
  const rift = await p.evaluate(() => window.__rl_riftAt() || [0, 0, 0])
  const at = [rift[0] + 600, rift[1], rift[2]]
  const n = await p.evaluate((at) => window.__rl_addEffect('LavaBurst', at), at)
  check(n > 0, `a Lava Burst placed (${n} added)`, log)
  const boxOn = await p.evaluate(() => document.querySelector('input[data-layer="vfx"]').checked)
  let shown = await p.evaluate(() => window.__rl_effectsShown())
  check(boxOn && shown.own > 0 && shown.added === 1,
    `placing it turns Effects on (box ${boxOn}): ${shown.own} of the level's and ${shown.added} placed drawn`, log)
  await layer(p, 'vfx', false)
  shown = await p.evaluate(() => window.__rl_effectsShown())
  check(shown.own === 0 && shown.added === 0, `Effects off: ${shown.own} of the level's and ${shown.added} placed drawn`, log)
  await lookDownAt(p, at, PICK_VIEW_HEIGHT)
  const spot = await p.evaluate((i) => window.__rl_addedScreen(i), n - 1)
  await p.evaluate(() => window.__rl_deselect())
  await p.mouse.click(spot.x, spot.y)
  await sleep(300)
  const pickedNow = await p.evaluate(() => window.__rl_pickedNow())
  check(!pickedNow.some(x => x.added), `Effects off: a click where the placed effect stands picks ${pickedNow.map(x => x.mesh).join(', ') || 'nothing'}`, log)
  await layer(p, 'vfx', true)
  shown = await p.evaluate(() => window.__rl_effectsShown())
  check(shown.added === 1, `Effects on again: the placed effect is drawn (${shown.added})`, log)
}

//: The colour the retint case gives a light, and the system it gives an emitter (both
//: unlike anything Scenario_02 ships).
const RETINT_COLOUR = '#00890c'
const RETINT_TEMPLATE = 'P_Acid_Wide_Idle'

/** Click one of the level's own markers of `kind` (a few are tried: one under a roof picks
 * the roof). Returns the mark picked, or null. */
async function pickOwnMark (p, kind, filter, log) {
  const marks = (await p.evaluate((k) => window.__rl_levelActorMarks(k), kind)).filter(filter)
  check(marks.length > 0, `${marks.length} ${kind} marker(s) of the level's own to pick`, log)
  for (const m of marks.slice(0, EFFECT_PICK_TRIES)) {
    await p.evaluate(() => window.__rl_deselect())
    await lookDownAt(p, m.at, PICK_VIEW_HEIGHT)
    const now = (await p.evaluate((k) => window.__rl_levelActorMarks(k), kind)).find(x => x.key === m.key)
    await p.mouse.click(now.x, now.y)
    await sleep(300)
    const kinds = await p.evaluate(() => window.__rl_pickedKinds())
    if (kinds.length === 1 && kinds[0] === 'actor:' + (kind === 'sound' ? 'fx' : kind)) return m
  }
  return null
}

/** Set a field's value and send its change event. */
async function setField (p, selector, value) {
  await p.evaluate((sel, v) => {
    const f = document.querySelector(sel); f.value = v; f.dispatchEvent(new Event('change', { bubbles: true }))
  }, selector, value)
  await sleep(300)
}

/** A level light's Colour sets `lightColours` and its marker's colour; Select All picks every
 * light shipped in that colour, and one change recolours them all; Ctrl+Z undoes it. An
 * Emitter's Effect plays another system (`fxSwaps`) and its cloud is drawn as that one;
 * Select All picks every placement of the system. The plan keeps both through a save. */
async function retint (p, urls, mission, log) {
  await openLevelNamed(p, urls, EFFECTS_LEVEL, log)
  await layer(p, 'lights', true)
  const light = await pickOwnMark(p, 'light', m => ['PointLight', 'RectLight', 'SpotLight'].includes(m.lightKind), log)
  check(!!light, `picked the light ${light && light.key}`, log)
  check(await p.evaluate(() => !!document.querySelector('#lightcolour')), 'the Light section has a Colour field', log)
  await setField(p, '#lightcolour', RETINT_COLOUR)
  let bits = await p.evaluate(() => window.__rl_planBits())
  check(bits.lightColours[light.key] === RETINT_COLOUR, `Colour sets lightColours[${light.key}] = ${bits.lightColours[light.key]}`, log)
  let drawn = (await p.evaluate(() => window.__rl_levelActorMarks('light'))).find(m => m.key === light.key)
  check(drawn.colour === RETINT_COLOUR, `its marker draws ${drawn.colour}`, log)
  const same = await p.evaluate(() => document.querySelector('#lightsame')?.textContent || '')
  if (same) {
    await clickIn(p, '#lightsame')
    const n = (await p.evaluate(() => window.__rl_pickedKinds())).length
    check(`Select All ${n}` === same && n > 1, `${same}: ${n} lights picked`, log)
    await setField(p, '#lightcolour', RETINT_COLOUR)
    bits = await p.evaluate(() => window.__rl_planBits())
    check(Object.keys(bits.lightColours).length === n, `one change recolours all ${n} (${Object.keys(bits.lightColours).length} in the plan)`, log)
  }
  await p.keyboard.down('Control')
  await p.keyboard.press('z')
  await p.keyboard.up('Control')
  await sleep(300)
  bits = await p.evaluate(() => window.__rl_planBits())
  check(!same || Object.keys(bits.lightColours).length === 1, `Ctrl+Z undoes the last change (${Object.keys(bits.lightColours).length} left)`, log)
  await layer(p, 'lights', false)

  await layer(p, 'vfx', true)
  const fx = await pickOwnMark(p, 'fx', m => m.actorClass === 'Emitter', log)
  check(!!fx, `picked the emitter ${fx && fx.key} (${fx && fx.template})`, log)
  const offered = await p.evaluate((t) => !!document.querySelector(`#levelfxkind option[value="${t}"]`), RETINT_TEMPLATE)
  check(offered, `the Effect list offers ${RETINT_TEMPLATE}`, log)
  await setField(p, '#levelfxkind', RETINT_TEMPLATE)
  bits = await p.evaluate(() => window.__rl_planBits())
  check(bits.fxSwaps[fx.key] === RETINT_TEMPLATE, `Effect sets fxSwaps[${fx.key}] = ${bits.fxSwaps[fx.key]}`, log)
  drawn = (await p.evaluate(() => window.__rl_levelActorMarks('fx'))).find(m => m.key === fx.key)
  check(drawn.template === RETINT_TEMPLATE && drawn.group === 'Acid', `its cloud draws ${drawn.template} (${drawn.group})`, log)
  const fxSame = await p.evaluate(() => document.querySelector('#fxsame')?.textContent || '')
  if (fxSame) {
    await clickIn(p, '#fxsame')
    const n = (await p.evaluate(() => window.__rl_pickedKinds())).length
    check(`Select All ${n}` === fxSame, `${fxSame}: ${n} effects picked`, log)
  }
  check(await p.evaluate(() => window.__rl_savePlan()), 'the plan saves', log)
  const kept = await p.evaluate(async (name) => (await (await fetch('api/plan/' + encodeURIComponent(name))).json()),
    EFFECTS_LEVEL)
  check(kept.lightColours && kept.lightColours[light.key] === RETINT_COLOUR && kept.fxSwaps &&
    kept.fxSwaps[fx.key] === RETINT_TEMPLATE, 'the saved plan keeps lightColours and fxSwaps', log)
}

/** The Mission Events button opens the list; Add Music Change adds an event (placing the
 * Mission Events piece where the host cannot change music itself); Remove takes it out. */
async function events (p, urls, mission, log) {
  await openMission(p, urls, mission, log)
  const start = await p.evaluate(() => window.__rl_planBits())
  await clickIn(p, '#missionevents')
  await waitFor(p, 'the Mission Events list', () => document.querySelector('#propshead')?.textContent === 'Mission Events' &&
    document.querySelector('[data-evadd="music"]'), { timeout: 120000, polling: 250 })
  check(true, 'Mission Events list shown', log)
  const edit = await p.evaluate(() => [...document.querySelectorAll('#propsbody button')].some(b => /edit events/i.test(b.textContent)))
  check(!edit, 'no Edit Events button', log)
  await clickIn(p, '[data-evadd="music"]')
  await sleep(300)
  let bits = await p.evaluate(() => window.__rl_planBits())
  check(bits.events.length === start.events.length + 1 && bits.events[bits.events.length - 1].kind === 'music',
    `Add Music Change: ${start.events.length} -> ${bits.events.length} events`, log)
  const piece = bits.added.filter(a => a.kind === 'events').length
  check(piece === 1, `the Mission Events piece is in the plan once (${piece})`, log)
  const i = bits.events.length - 1
  await p.evaluate((i) => {
    const w = document.querySelector(`[data-ev="${i}"][data-field="wave"]`)
    w.value = '2'; w.dispatchEvent(new Event('change'))
  }, i)
  bits = await p.evaluate(() => window.__rl_planBits())
  check(bits.events[i].wave === 2, `wave set to ${bits.events[i].wave}`, log)
  await clickIn(p, `[data-evremove="${i}"]`)
  bits = await p.evaluate(() => window.__rl_planBits())
  check(bits.events.length === start.events.length, `Remove: back to ${bits.events.length} events`, log)
}

/** Save As keeps the open mission as a new one in Missions (the test server's own,
 * never the user's): a new id and title, the same plan. */
async function saveas (p, urls, mission, log) {
  if (!process.env.RL_MISSIONS) throw new Error('saveas needs the scratch Missions of testservers.py (RL_MISSIONS)')
  await openMission(p, urls, mission, log)
  const title = 'Smoke Save As ' + Date.now().toString(36)
  await clickIn(p, '#saveas')
  await p.evaluate((t) => { document.querySelector('#saveastitle').value = t }, title)
  await clickIn(p, '#saveasgo')
  await waitFor(p, 'the Save As notice', () => /Saved as|Not saved/.test(document.querySelector('#saved')?.textContent || ''),
    { timeout: 120000, polling: 250 })
  const said = await p.evaluate(() => document.querySelector('#saved').textContent)
  check(/Saved as/.test(said), `notice: ${said.slice(0, 120)}`, log)
  const found = await p.evaluate(async (t) => (await (await fetch('api/missions')).json()).find(m => m.title === t), title)
  check(!!found && found.id !== mission, `Missions lists ${found ? found.id : 'nothing'} titled "${title}"`, log)
  const both = await p.evaluate(async (a, b) => {
    const get = async (id) => (await (await fetch('api/bucket/item/' + encodeURIComponent(id))).json())
    const [x, y] = [await get(a), await get(b)]
    return { added: [x.plan.added.length, y.plan.added.length], mods: [x.mod, y.mod],
             waves: [((x.spec || {}).waves || []).length, ((y.spec || {}).waves || []).length] }
  }, mission, found.id)
  check(both.added[0] === both.added[1] && both.mods[0] !== both.mods[1],
    `same plan (${both.added.join(' / ')} added), own mod (${both.mods.join(' / ')}), waves ${both.waves.join(' / ')}`, log)
}

// -- controller settings (riftlord/controllers.py, envtraps.py) -------------------------------

//: Front Lawn stores wind, ships two kill volumes under its grinder pits and plated wall torches.
const CONTROLLERS_LEVEL = 'Scenario_09'
const CONTROLLERS_KILL = 'OMDCharacterKillVolume_1'

/** Set one input or select in the side panel and fire its change. */
async function setField (p, selector, value) {
  return p.evaluate((sel, v) => {
    const el = document.querySelector(sel)
    if (!el) return false
    if (el.type === 'checkbox') el.checked = !!v
    else el.value = v
    el.dispatchEvent(new Event('change'))
    return true
  }, selector, value)
}

/** Wind fields in Map Settings; a kill volume's switches; a level torch's Wall Plate; an
 * added ambience emitter's Sound, lightning's Retrigger Time and Glow Color, a cauldron's
 * Pour Length and a torch's Wall Plate; all of it through a save. */
async function controllers (p, urls, mission, log) {
  await openLevelNamed(p, urls, CONTROLLERS_LEVEL, log)
  await waitFor(p, 'the Wind fields in Map Settings', () => document.querySelector('[data-scene="windSpeed"]'),
    { timeout: 60000, polling: 250 })
  const wind = await p.evaluate(() => ['windSpeed', 'windStrength', 'windMinGust', 'windMaxGust']
    .map(k => (document.querySelector(`[data-scene="${k}"]`) || {}).value))
  check(wind.every(v => v !== undefined && v !== ''), `Wind fields shown: ${wind.join(', ')}`, log)
  await setField(p, '[data-scene="windSpeed"]', '22')
  check((await p.evaluate(() => window.__rl_planWorld())).windSpeed === 22, 'Speed 22 is in the plan', log)

  check(await p.evaluate((n) => window.__rl_pickVolume(n), CONTROLLERS_KILL), `picked ${CONTROLLERS_KILL}`, log)
  const flags = await p.evaluate(() => [...document.querySelectorAll('[data-killflag]')]
    .map(b => `${b.dataset.killflag}=${b.checked}`))
  check(flags.length === 2 && flags.every(f => f.endsWith('true')), `Kill section: ${flags.join(', ')}`, log)
  await setField(p, '[data-killflag="killPlayers"]', false)

  const level = await p.evaluate(async (n) => (await (await fetch('api/level/' + n)).json()).pieces
    .filter(q => /^BP_WallTorch_Normal/.test(q.actor || '')).map(q => ({ id: q.id, sub: q.sub, export: q.export })), CONTROLLERS_LEVEL)
  const torch = level[0]
  await p.evaluate((id) => window.__rl_pickPiece(id), torch.id)
  const plateNow = await p.evaluate(() => (document.querySelector('#ctlplate') || {}).value)
  check(plateNow === 'on', `level torch ${torch.sub}|${torch.export}: Wall Plate ${plateNow}`, log)
  await setField(p, '#ctlplate', 'off')

  const rift = await p.evaluate(() => window.__rl_riftAt() || [0, 0, 0])
  const add = async (name, dx) => p.evaluate(async (name, at) => {
    const n = await window.__rl_addKit(name, at)
    window.__rl_pickAdded(n - 1)
    return n - 1
  }, name, [rift[0] + dx, rift[1] + 900, rift[2]])
  const sound = await add('AmbientSound', 0)
  const cue = await p.evaluate(() => { const s = document.querySelector('#ctlcue'); return s && [s.value, s.options.length] })
  check(!!cue && cue[1] > 60, `AmbientSound Sound ${cue && cue[0]} of ${cue && cue[1]} cues`, log)
  await setField(p, '#ctlcue', 'AmbEmit_Ocean_Close_Dist_LP_SC')
  const bolt = await add('BP_LightningBolt_RainLevels_C', 300)
  check(await setField(p, '[data-ctlretrigger="0"]', '3'), 'lightning Retrigger Min set', log)
  check(await setField(p, '#ctlglowbright', '4'), 'lightning Glow Brightness set', log)
  const pot = await add('BP_Cauldron_C', 600)
  const pours = await p.evaluate(() => { const s = document.querySelector('#ctlpour'); return s && [...s.options].map(o => o.textContent) })
  check(!!pours && pours.length >= 5, `cauldron Pour Length: ${pours && pours.join(' / ')}`, log)
  await setField(p, '#ctlpour', 'P_CauldronPour_1050')
  const lamp = await add('BP_WallTorch_Normal_C', 900)
  await setField(p, '#ctlplate', 'off')
  await add('BP_FallingBoomBarrel_C', 1200)
  await add('BP_GrinderPit_3X_C', 1500)

  const plan = await p.evaluate(async (n) => {
    await window.__rl_savePlan()
    return (await fetch('api/plan/' + n)).json()
  }, CONTROLLERS_LEVEL)
  const vol = (plan.volumes || []).find(v => v.name === CONTROLLERS_KILL) || {}
  check(plan.world && plan.world.windSpeed === 22, `saved wind Speed ${plan.world && plan.world.windSpeed}`, log)
  check(vol.killPlayers === false && !('killMinions' in vol), `saved kill volume ${JSON.stringify(vol)}`, log)
  const key = `${torch.sub || 'persistent'}|${torch.export}`
  check((plan.wallPlates || {})[key] === false, `saved wallPlates ${JSON.stringify(plan.wallPlates)}`, log)
  check(plan.added[sound].cue === 'AmbEmit_Ocean_Close_Dist_LP_SC', `saved cue ${plan.added[sound].cue}`, log)
  check(plan.added[bolt].retrigger && plan.added[bolt].retrigger[0] === 3 && plan.added[bolt].glow,
    `saved lightning retrigger ${JSON.stringify(plan.added[bolt].retrigger)} glow ${JSON.stringify(plan.added[bolt].glow)}`, log)
  check(plan.added[pot].pour === 'P_CauldronPour_1050', `saved pour ${plan.added[pot].pour}`, log)
  check(plan.added[lamp].wallPlate === false, `saved added torch wallPlate ${plan.added[lamp].wallPlate}`, log)
  check(plan.added.some(a => a.name === 'BP_FallingBoomBarrel_C') && plan.added.some(a => a.name === 'BP_GrinderPit_3X_C'),
    'saved the boom barrel and grinder pit', log)
}

// -- treasure chests and pickups (riftlord/kitactors.py, controllers.js) -------------------

//: A small shipped level with no chest of its own.
const PICKUPS_LEVEL = 'Scenario_02'
const CHEST = 'BP_TreasureChest_C'
//: The colour an untextured added part draws in (main.js UNTEXTURED).
const UNTEXTURED_HEX = '8d9199'
//: Every class riftlord/mapeditor/kitactors.py places (CLASSES).
const KIT_ACTOR_CLASSES = [CHEST, 'BP_Pickup_StaminaPotion_C', 'BP_Pickup_Coin_C',
  'BP_Pickup_CoinLarge_C', 'BP_Pickup_MoltenGoldCoin_C', 'BP_Pickup_MoltenLargeGoldCoin_C',
  'BP_Pickup_Skull_C', 'BP_Pickup_HealthPotion_C', 'BP_Pickup_ManaPotion_C']

/** A chest shows Group, Chance and rewards and draws its two meshes; an edit reaches every
 * spot of its group; Add Spot places a spot of the same group; a stamina potion and a coin
 * place; all of it through a save. */
async function pickups (p, urls, mission, log) {
  await openLevelNamed(p, urls, PICKUPS_LEVEL, log)
  const rift = await p.evaluate(() => window.__rl_riftAt() || [0, 0, 0])
  const add = async (name, dx) => p.evaluate(async (name, at) => {
    const n = await window.__rl_addKit(name, at)
    window.__rl_pickAdded(n - 1)
    return n - 1
  }, name, [rift[0] + dx, rift[1] + 900, rift[2]])
  const chest = await add(CHEST, 0)
  await waitFor(p, 'the chest panel', () => document.querySelector('#ctlchestgroup'), { timeout: 30000, polling: 250 })
  const shown = await p.evaluate(() => ['ctlchestgroup', 'ctlchestchance', 'ctlcoinmin', 'ctlcoinmax',
    'ctlskullmin', 'ctlskullmax', 'ctlcoinpct', 'ctlskullpct'].map(id => document.querySelector('#' + id).value))
  check(shown.join(',') === 'Chest 1,100,250,750,1,3,75,25', `chest panel: ${shown.join(', ')}`, log)
  const meshes = await p.evaluate((i) => window.__rl_addedLook(i).map(m => m.mesh), chest)
  check(meshes.length === 2 && meshes.every(m => /^kit\/TreasureChests\//.test(m)),
    `chest drawn from ${meshes.join(', ')}`, log)
  await setField(p, '#ctlcoinpct', '100')
  const skull = await p.evaluate(() => document.querySelector('#ctlskullpct').value)
  check(skull === '0', `Coin % 100 sets Skull % ${skull}`, log)
  await setField(p, '#ctlchestchance', '60')
  await p.evaluate(() => document.querySelector('#ctlchestspot').click())
  const spot = chest + 1
  await waitFor(p, 'the added spot drawn and picked', (n) => window.__rl_addedLook(n).length === 2 &&
    document.querySelector('#ctlchestgroup'), { timeout: 30000, polling: 250 }, spot)
  const spotGroup = await p.evaluate(() => document.querySelector('#ctlchestgroup').value)
  check(spotGroup === 'Chest 1', `Add Spot placed a spot in ${spotGroup}`, log)
  await setField(p, '#ctlcoinmin', '300')
  const second = await add(CHEST, 600)
  const group2 = await p.evaluate(() => document.querySelector('#ctlchestgroup').value)
  check(group2 === 'Chest 2', `a second chest gets ${group2}`, log)
  const potion = await add('BP_Pickup_StaminaPotion_C', -300)
  const coin = await add('BP_Pickup_Coin_C', -450)
  const health = await add('BP_Pickup_HealthPotion_C', -600)
  // a coin draws as its sprite, a health potion as its jar in its light's colour
  await waitFor(p, 'the coin sprite and the potion jar drawn', (c, h) =>
    window.__rl_addedLook(c).length && window.__rl_addedLook(h).length,
  { timeout: 30000, polling: 250 }, coin, health)
  const looks = await p.evaluate((c, h) => [window.__rl_addedLook(c), window.__rl_addedLook(h)], coin, health)
  check(looks[0].length === 1 && looks[0][0].mats[0].type === 'SpriteMaterial' &&
    /CoinIcon$/.test(looks[0][0].mesh) && looks[1].length === 1 && looks[1][0].mesh === 'Jar' &&
    looks[1][0].mats.every(m => m.color && m.color !== UNTEXTURED_HEX),
  `coin drawn as ${looks[0].map(m => `${m.mats[0].type} ${m.mesh}`).join(', ')}; health potion as ` +
    `${looks[1].map(m => `${m.mesh} ${m.mats.map(x => '#' + x.color).join('/')}`).join(', ')}`, log)
  if (process.env.RL_SHOTS) {
    await p.evaluate((r) => window.__rl_view([r[0] - 100, r[1] + 100, r[2] + 500],
      [r[0] - 100, r[1] + 900, r[2] + 40]), rift)
    await sleep(1500)
    await p.screenshot({ path: `${process.env.RL_SHOTS}/pickups-viewport.png` })
  }
  // every New Object card of these classes shows its preview
  await p.evaluate(() => document.querySelector('#openkit').click())
  const previews = []
  for (const term of ['BP_Pickup', 'TreasureChest']) {
    await p.evaluate((t) => {
      const s = document.querySelector('#kitsearch'); s.value = t; s.dispatchEvent(new Event('input'))
    }, term)
    const want = KIT_ACTOR_CLASSES.filter(c => c.includes(term))
    await waitFor(p, `the ${term} cards' previews`, (want) => want.every((c) => {
      const i = document.querySelector(`#kitgrid [data-name="${c}"] img`)
      return i && i.complete && i.naturalWidth > 0
    }), { timeout: 240000, polling: 500 }, want)
    previews.push(...await p.evaluate((want) => want.map(c =>
      `${c} ${document.querySelector(`#kitgrid [data-name="${c}"] img`).naturalWidth}px`), want))
    if (process.env.RL_SHOTS) {
      const grid = await p.$('#kitgrid')
      if (grid) await grid.screenshot({ path: `${process.env.RL_SHOTS}/newobject-${term.toLowerCase()}.png` })
    }
  }
  check(previews.length === KIT_ACTOR_CLASSES.length, `New Object previews: ${previews.join(', ')}`, log)
  const plan = await p.evaluate(async (n) => {
    await window.__rl_savePlan()
    return (await fetch('api/plan/' + n)).json()
  }, PICKUPS_LEVEL)
  const a = plan.added[chest].chest || {}
  const b = plan.added[spot].chest || {}
  check(a.group === 'Chest 1' && b.group === 'Chest 1', `saved spots in ${a.group} and ${b.group}`, log)
  check(JSON.stringify(a) === JSON.stringify(b) && a.chance === 60 && a.coin[0] === 300 &&
    a.coinChance === 100 && a.skullChance === 0, `saved group settings ${JSON.stringify(a)}`, log)
  check((plan.added[second].chest || {}).group === 'Chest 2', 'saved the second chest', log)
  check(plan.added[potion].name === 'BP_Pickup_StaminaPotion_C' && plan.added[coin].name === 'BP_Pickup_Coin_C',
    'saved the stamina potion and the coin', log)
  // an added kit piece's parts wear their override materials (api/kit materialTexture)
  const dry = await p.evaluate(async (name, at) => {
    const n = await window.__rl_addKit(name, at)
    const parts = (await (await fetch('api/kit/' + encodeURIComponent(name))).json()).parts
    return { n: n - 1, parts: parts.filter(q => q.material && !q.skeletal) }
  }, DRY_WALL, [rift[0] - 900, rift[1] + 900, rift[2]])
  await waitFor(p, 'the dry wall drawn', (i, k) => window.__rl_addedLook(i).length >= k,
    { timeout: 60000, polling: 250 }, dry.n, dry.parts.length)
  const worn = await p.evaluate((i) => window.__rl_addedLook(i).map(m =>
    ({ mesh: m.mesh, override: m.mats[0].override, texture: m.mats[0].texture })), dry.n)
  const wrong = dry.parts.filter(q => !worn.some(w => w.mesh === q.mesh &&
    w.override === q.material && w.texture === (q.materialTexture || w.texture)))
  check(dry.parts.length > 0 && !wrong.length, `${DRY_WALL}: ${dry.parts.length} parts wear their ` +
    `override materials (${[...new Set(dry.parts.map(q => q.material))].join(', ')})` +
    (wrong.length ? `; not: ${wrong.map(q => q.component).join(', ')}` : ''), log)
}

//: A kit piece whose parts carry override materials (Dry variants).
const DRY_WALL = 'Wall_RB_WD_v1_Dry_1x2_C'

// -- the Crystal (riftlord/mapeditor/crystal.py, crystalui.js) -----------------------------

//: A small shipped level whose rift stands on a RiftBase, entered from the south.
const CRYSTAL_LEVEL = 'Scenario_02'
//: Where the check's unstable rift goes: the rift room's north-west corner, on its floor.
const CRYSTAL_RIFT_AT = [2150, 6950, 537]
//: Two more Unstable Rifts the crystal case places, for the numbering: the room's north-east
//: corner and north wall (riftfloor heights).
const CRYSTAL_MORE_RIFTS = [[3850, 6950, 560], [3000, 7300, 545]]
//: Where the angled Crystal screenshot looks from, relative to the Crystal, uu: inside the
//: rift room, from its north-west corner.
const CRYSTAL_ANGLE = [-850, 750, 650]
//: Height over the Crystal the Map Editor screenshot looks down from, uu.
const CRYSTAL_SHOT_HEIGHT = 2600
//: How near the placed crystal must be to /api/crystal's spot, uu and matrix terms.
const CRYSTAL_TOLERANCE = 0.01

/** The Gameplay card places one Crystal on the rift's base facing the routes, drawn as its
 * own model (its pieces and floating crystal); its panel holds only its Rift Groups (no
 * health or other fields, labels only); Add Rift Group and Add Unstable Rift name the
 * mission's unstable rift; a second click picks the same Crystal; all of it through a save. */
async function crystal (p, urls, mission, log) {
  await openLevelNamed(p, urls, CRYSTAL_LEVEL, log)
  await p.evaluate((at) => window.__rl_place('unstableRift', at), CRYSTAL_RIFT_AT)
  const info = await p.evaluate(async (n, r) => (await fetch(`api/crystal/${n}?rift=${r.join(',')}`)).json(),
    CRYSTAL_LEVEL, await p.evaluate(() => window.__rl_riftAt()))
  check(info.spot && info.available, `api/crystal: spot ${JSON.stringify(info.spot && info.spot.at)} yaw ` +
    `${info.spot && info.spot.yaw}, ${info.available ? 'installed' : 'not installed'}`, log)
  check((info.parts || []).length > 1 && info.parts.some(x => x.fit), `model parts: ${(info.parts || []).length}`, log)
  await p.evaluate(() => document.querySelector('#openkit').click())
  await p.evaluate(() => {
    const s = document.querySelector('#kitsearch'); s.value = 'crystal'; s.dispatchEvent(new Event('input'))
  })
  await waitFor(p, 'the Crystal card', () => document.querySelector('#kitgrid [data-name="crystal"]'),
    { timeout: 30000, polling: 250 })
  await clickIn(p, '#kitgrid [data-name="crystal"]')
  await waitFor(p, 'the Crystal panel', () => document.querySelector('#crystalpanel'), { timeout: 60000, polling: 250 })
  const fields = await p.evaluate(() => ({
    health: !!document.querySelector('[id^="crystalhealth"]'),
    bob: !!document.querySelector('#crystalbob, #crystalbobs'),
    text: document.querySelector('#crystalpanel').textContent }))
  check(!fields.health && !fields.bob && !/Health|Rift Point|Bob/.test(fields.text),
    `Crystal panel: Rift Groups only (${fields.text.trim().slice(0, 60)})`, log)
  const prose = await p.evaluate(() => [...document.querySelectorAll('#propsbody .hint')]
    .filter(h => !/Position/.test(h.previousElementSibling?.textContent || '')).map(h => h.textContent))
  check(prose.length === 0, `panel text: ${JSON.stringify(prose)}`, log)
  const suggest = await p.evaluate(() => [...document.querySelectorAll('#propsbody button')].some(b => /suggest|keep/i.test(b.textContent)))
  check(!suggest, 'no Suggest or Keep buttons', log)
  const handles = await p.evaluate(() => [...document.querySelectorAll('#gizmomode button')].map(b => b.textContent))
  check(handles.join(',') === 'move,turn', `handles: ${handles.join(', ')}`, log)
  await waitFor(p, 'the Crystal model', () => {
    const i = window.__rl_planBits().added.findIndex(a => a.kind === 'crystal')
    return (window.__rl_addedLook(i)[0] || {}).parts > 1
  }, { timeout: 60000, polling: 250 })
  const look = await p.evaluate(() => {
    const i = window.__rl_planBits().added.findIndex(a => a.kind === 'crystal')
    return window.__rl_addedLook(i)
  })
  check(look.length === 1 && look[0].parts > 1, `model drawn (${look[0] && look[0].parts} parts)`, log)
  await waitFor(p, 'the floating crystal\'s emissive texture', () => {
    const i = window.__rl_planBits().added.findIndex(a => a.kind === 'crystal')
    return (window.__rl_addedLook(i)[0] || {}).glowing >= 1
  }, { timeout: 60000, polling: 250 })
  await waitFor(p, 'the Crystal card\'s preview', () => {
    const img = document.querySelector('#kitgrid [data-name="crystal"] img')
    return img && img.complete && img.naturalWidth > 0
  }, { timeout: 120000, polling: 500 })
  check(true, 'the floating crystal draws with its emissive texture; the card shows its preview', log)
  if (process.env.RL_SHOTS) {
    await lookDownAt(p, info.spot.at, CRYSTAL_SHOT_HEIGHT)
    await sleep(1500)
    await p.screenshot({ path: `${process.env.RL_SHOTS}/crystal-mapeditor.png` })
  }
  await clickIn(p, '#crystaladdgroup')
  const riftId = await p.evaluate(() => document.querySelector('#crystaladdrift0 option:not([value=""])')?.value)
  await setField(p, '#crystaladdrift0', riftId)
  await setField(p, '#crystalgname0', 'North-west')
  const row = await p.evaluate(() => [document.querySelector('#crystalgname0')?.value,
    [...document.querySelectorAll('#crystalpanel .route .routehead span')].map(b => b.textContent)])
  check(row[0] === 'North-west' && row[1].length === 1 && /^Unstable Rift \d+$/.test(row[1][0]),
    `rift group: ${row[0]} with ${row[1].join(', ')}`, log)
  if (process.env.RL_SHOTS) {
    const box = await p.$('#crystalpanel')
    if (box) await box.screenshot({ path: `${process.env.RL_SHOTS}/crystal-mapeditor-panel.png` })
  }
  //: numbering: two more Unstable Rifts take the next indices; deleting the middle one
  //: leaves every other rift's number as it was
  const before = row[1][0]
  const added = []
  for (const at of CRYSTAL_MORE_RIFTS) {
    await p.evaluate((x) => window.__rl_place('unstableRift', x), at)
    added.push(await p.evaluate(() => {
      const sp = window.__rl_planActors().spawners.filter(s => s.unstable)
      return sp[sp.length - 1]
    }))
  }
  const firstIndex = await p.evaluate(() => window.__rl_planActors().spawners.find(s => s.unstable).index)
  check(added[0].index === firstIndex + 1 && added[1].index === firstIndex + 2,
    `new Unstable Rifts take indices ${added.map(a => a.index).join(', ')} after ${firstIndex}`, log)
  await p.evaluate((id) => window.__rl_select('spawner', id), added[0].id)
  await sleep(300)
  const removed = await p.evaluate(() => {
    const b = [...document.querySelectorAll('#propsbody button')].find(x => /^(Delete|Remove)/.test(x.textContent.trim()))
    if (b) b.click()
    return b ? b.textContent.trim() : null
  })
  check(!!removed, `the rift's ${removed} button`, log)
  await sleep(500)
  const left = await p.evaluate(() => window.__rl_planActors().spawners.filter(s => s.unstable).map(s => s.index))
  check(left.join(',') === [firstIndex, added[1].index].join(','), `after deleting ${added[0].index}: ${left.join(', ')}`, log)
  await clickIn(p, '#kitgrid [data-name="crystal"]')
  await sleep(500)
  const after = await p.evaluate(() => [document.querySelector('#crystalpanel .routehead span')?.textContent.trim(),
    [...document.querySelectorAll('#crystaladdrift0 option:not([value=""])')].map(o => o.textContent.trim())])
  check(after[0] === before && before === `Unstable Rift ${firstIndex}` &&
    after[1].join(',') === `Unstable Rift ${added[1].index}`,
  `numbers kept: group ${after[0]}, to add ${after[1].join(', ')}`, log)
  const count = await p.evaluate(() => window.__rl_planBits().added.filter(a => a.kind === 'crystal').length)
  check(count === 1, `a second click keeps one Crystal (${count})`, log)
  const plan = await p.evaluate(async (n) => {
    await window.__rl_savePlan()
    return (await fetch('api/plan/' + n)).json()
  }, CRYSTAL_LEVEL)
  const saved = plan.added.find(a => a.kind === 'crystal')
  const rift = (plan.spawners || []).find(s => s.unstable)
  check(!!saved && !!rift, 'saved the Crystal and the unstable rift', log)
  if (process.env.RL_SHOTS) {
    //: the Crystal unselected, from an angle, so its model can be judged
    await p.evaluate(() => window.__rl_deselect())
    const c = info.spot.at
    await p.evaluate((at, o) => window.__rl_view([at[0] + o[0], at[1] + o[1], at[2] + o[2]],
      [at[0], at[1], at[2] + 500]), c, CRYSTAL_ANGLE)
    await sleep(2500)
    await p.screenshot({ path: `${process.env.RL_SHOTS}/crystal-mapeditor-angled.png` })
  }
  const near = saved.matrix.every((v, k) => Math.abs(v - info.spot.matrix[k]) <= CRYSTAL_TOLERANCE * Math.max(1, Math.abs(v)))
  check(near, `saved on the rift base, turned as /api/crystal says: ${saved.matrix.map(v => +v.toFixed(3)).join(',')}`, log)
  const c = saved.crystal || {}
  const g = (c.groups || [])[0] || {}
  check(!('healthPerRiftPoint' in c) && !('minHealth' in c) &&
    (c.groups || []).length === 1 && g.name === 'North-west' && (g.rifts || []).length === 1 &&
    g.rifts[0].spawner === rift.id && g.rifts[0].difficulty === 5,
  `saved settings ${JSON.stringify(c)}`, log)
}

//: The Crystal Health labels, in EGameDifficulty order (crystal.py HEALTH_DIFFICULTIES).
const CRYSTAL_HEALTH_LABELS = ['Apprentice', 'War Mage', 'Rift Lord', 'Endless']
//: The healths the crystalhealth case sets in the Onslaught Editor's Timeline.
const CRYSTAL_WAR_MAGE = 2500
const CRYSTAL_ENDLESS = 4000
//: The crystalhealth mission's rift start point (crystal.py check_plan's) and trigger.
const CRYSTAL_POINT_AT = [3000, 6800, 465]
const CRYSTAL_TRIGGER = { at: 75, wave: null, alive: 0, both: false, open: 1, launchAfter: 0, launchAlive: 0 }
//: A trigger's words (Trigger.svelte) and the Opens At seconds the case sets.
const CRYSTAL_TRIGGER_WORDS = ['When', 'Opens At', 'Open', 'Repeat Every']
const CRYSTAL_LAUNCH_AFTER = 20
//: The Repeat Every (Waves) the case sets: every wave.
const CRYSTAL_REPEAT = 1
//: The group row the case adds in wave 1.
const CRYSTAL_ROW = { unit: 'OrcLight', count: 4 }
//: How long the Onslaught Editor may take to open the new mission, baking it first, in ms.
const CRYSTAL_BAKE_WAIT = 180000

/** Set an input in the Onslaught Editor and fire a bubbling change (Svelte delegates it). */
async function setSvelteField (p, selector, value) {
  return p.evaluate((sel, v) => {
    const el = document.querySelector(sel)
    if (!el) return false
    el.value = v
    el.dispatchEvent(new Event('change', { bubbles: true }))
    return true
  }, selector, value)
}

/** Crystal Defense authoring: a Crystal and a Rift Group of one Unstable Rift placed on
 * Scenario_02 in the Map Editor and kept as a new mission (Save As). In the Onslaught
 * Editor: the Timeline has Crystal Health (four difficulties) and no Rift points, and each
 * wave lists the group's rift with its chance; the group has its own tab named after it
 * (no tab for the rift itself), with its trigger (When The Wave After Crystal Health ≤ 75 %,
 * Opens At Enemies Left ≤ 50 % as the contract's default, Open, Repeat Every), its rift and
 * an enemy row added in wave 1; Endless health, wave 2's chance 0, Opens At 20 Seconds Into
 * the Wave, Repeat Every 1 Wave and the row are
 * saved into the plan; the Map Editor's panel still shows the group. RL_SHOTS keeps
 * crystal-health*.png. */
async function crystalhealth (p, urls, mission, log) {
  if (!process.env.RL_MISSIONS) throw new Error('crystalhealth needs the scratch Missions of testservers.py (RL_MISSIONS)')
  await openLevelNamed(p, urls, CRYSTAL_LEVEL, log)
  await p.evaluate((at) => window.__rl_place('unstableRift', at), CRYSTAL_RIFT_AT)
  await p.evaluate(() => document.querySelector('#openkit').click())
  await p.evaluate(() => {
    const s = document.querySelector('#kitsearch'); s.value = 'crystal'; s.dispatchEvent(new Event('input'))
  })
  await waitFor(p, 'the Crystal card', () => document.querySelector('#kitgrid [data-name="crystal"]'),
    { timeout: 30000, polling: 250 })
  await clickIn(p, '#kitgrid [data-name="crystal"]')
  await waitFor(p, 'the Crystal panel', () => document.querySelector('#crystalpanel'), { timeout: 60000, polling: 250 })
  //: a Crystal the level's saved plan already has keeps its group
  if (!(await p.evaluate(() => !!document.querySelector('#crystalgname0')))) {
    await clickIn(p, '#crystaladdgroup')
    const riftId = await p.evaluate(() => document.querySelector('#crystaladdrift0 option:not([value=""])')?.value)
    await setField(p, '#crystaladdrift0', riftId)
  }
  await setField(p, '#crystalgname0', 'North-west')
  const mapEditorRift = await p.evaluate(() => document.querySelector('#crystalpanel .routehead span')?.textContent.trim())
  //: the plan made buildable (the Onslaught Editor bakes the mission it opens): the group's
  //: first rift starts at an added point on to the exit, and the group has a trigger
  const completed = await p.evaluate(async (n, at, trigger) => {
    await window.__rl_savePlan()
    const plan = await (await fetch('api/plan/' + n)).json()
    const g = plan.added.find(a => a.kind === 'crystal').crystal.groups[0]
    const sp = plan.spawners.find(s => s.id === g.rifts[0].spawner)
    const pid = Math.max(0, ...(plan.points || []).map(q => q.id)) + 1
    plan.points = [...(plan.points || []), { id: pid, at, reachRadius: 300, formationSize: 3,
      formationSpacing: 0, flightHeight: 0, flyersOnly: false, groundOnly: false, next: [] }]
    sp.start = pid
    g.minions = [{ unit: 'OrcLight', count: 1, addCount: 0 }]
    g.triggers = [trigger]
    const r = await fetch('api/plan/' + n, { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Rift-Lord': '1' }, body: JSON.stringify(plan) })
    return r.ok
  }, CRYSTAL_LEVEL, CRYSTAL_POINT_AT, CRYSTAL_TRIGGER)
  check(completed, 'rift start point and trigger saved', log)
  const title = 'Smoke Crystal Health ' + Date.now().toString(36)
  const made = await p.evaluate(async (n, t) => {
    const r = await fetch('api/saveas/' + encodeURIComponent(n), { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Rift-Lord': '1' }, body: JSON.stringify({ title: t }) })
    return r.json()
  }, CRYSTAL_LEVEL, title)
  const id = made && made.mission
  check(!!id, `saved as ${id} titled "${title}"`, log)

  await p.goto(`${urls.onslaught}/?embedded=1&theme=dark&bucket=${encodeURIComponent(id)}&open=1`,
    { waitUntil: 'domcontentloaded', timeout: 60000 })
  await waitFor(p, 'the Onslaught Editor with Crystal Health on the Timeline',
    () => document.querySelector('input[aria-label="Endless health"]') &&
      document.querySelector('input[aria-label="Starting coin"]'), { timeout: CRYSTAL_BAKE_WAIT, polling: 1000 })
  const timeline = await p.evaluate(() => ({
    riftPoints: !!document.querySelector('input[aria-label="Rift points"]'),
    health: [...document.querySelectorAll('.timeline .crystal .lab')].map(x => x.textContent.trim()),
    tabs: [...document.querySelectorAll('[role=tab]')].map(t => t.textContent.trim()),
    chance: [...document.querySelectorAll('.timeline input[aria-label$="available in wave 2"]')].length }))
  check(!timeline.riftPoints && timeline.health.join(',') === CRYSTAL_HEALTH_LABELS.join(','),
    `Timeline: Crystal Health ${timeline.health.join(', ')}, no Rift points`, log)
  check(timeline.tabs.includes('North-west') && !timeline.tabs.some(t => /^(Unstable Rift|Rift Tear|Crystal Rift)/.test(t)),
    `tabs: ${timeline.tabs.join(', ')}`, log)
  check(timeline.chance === 1, `wave 2 lists the group's Unstable Rift (${timeline.chance})`, log)
  await setSvelteField(p, 'input[aria-label="War Mage health"]', String(CRYSTAL_WAR_MAGE))
  await setSvelteField(p, 'input[aria-label="Endless health"]', String(CRYSTAL_ENDLESS))
  await p.evaluate(() => {
    const box = document.querySelector('.timeline input[aria-label$="available in wave 2"]')
    box.checked = false
    box.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await sleep(300)
  if (process.env.RL_SHOTS) await p.screenshot({ path: `${process.env.RL_SHOTS}/crystal-health-timeline.png` })
  await p.evaluate(() => [...document.querySelectorAll('[role=tab]')].find(t => t.textContent.trim() === 'North-west').click())
  await waitFor(p, 'the Rift Group tab', () => document.querySelector('.group .trigger'), { timeout: 20000, polling: 250 })
  const trigger = await p.evaluate(() => {
    const tr = document.querySelector('.group .trigger')
    return { words: [...tr.querySelectorAll('.w')].map(x => x.textContent.trim()), text: tr.innerText,
      when: tr.querySelector('select[aria-label="When"]').value, health: tr.querySelector('input[aria-label="Crystal Health"]')?.value,
      opens: tr.querySelector('select[aria-label="Opens At"]').value, alive: tr.querySelector('input[aria-label="Enemies Left"]')?.value,
      repeat: tr.querySelector('input[aria-label="Repeat Every"]').value }
  })
  check(CRYSTAL_TRIGGER_WORDS.every(l => trigger.words.includes(l)) && !/Launch|Stay Open/.test(trigger.text) &&
    trigger.when === 'at' && trigger.health === '75' && trigger.opens === 'alive' && trigger.alive === '50' && trigger.repeat === '',
  `trigger: ${trigger.words.join(' ')}; When ${trigger.when} ${trigger.health}, Opens At ${trigger.opens} ${trigger.alive}, repeat "${trigger.repeat}"`, log)
  await setSvelteField(p, '.group select[aria-label="Opens At"]', 'after')
  await sleep(200)
  await setSvelteField(p, '.group input[aria-label="Seconds Into the Wave"]', String(CRYSTAL_LAUNCH_AFTER))
  await setSvelteField(p, '.group input[aria-label="Repeat Every"]', String(CRYSTAL_REPEAT))
  //: the older row shows in every wave until saved per wave; wave 1's is set to the case's
  await waitFor(p, 'wave 1\'s enemy row', () => document.querySelector('.group section[id^="wave-"] .row select[aria-label="Minion"]'),
    { timeout: 20000, polling: 250 })
  await p.evaluate((row) => {
    const sec = document.querySelector('.group section[id^="wave-"]')
    const s = sec.querySelector('.row select[aria-label="Minion"]')
    s.value = row.unit
    s.dispatchEvent(new Event('change', { bubbles: true }))
    const c = sec.querySelector('.row input[type="number"]')
    c.value = String(row.count)
    c.dispatchEvent(new Event('input', { bubbles: true }))
  }, CRYSTAL_ROW)
  await sleep(300)
  const groupView = await p.evaluate(() => ({
    rifts: [...document.querySelectorAll('.group .row.rift b')].map(b => b.textContent.trim()),
    heads: [...document.querySelectorAll('.group h3')].map(h => h.textContent.trim()) }))
  check(groupView.rifts.length === 1 && groupView.rifts[0] === mapEditorRift &&
    groupView.heads.slice(0, 2).join(',') === 'Opens,Unstable Rifts',
  `group tab: ${groupView.heads.slice(0, 3).join(', ')}; ${groupView.rifts.join(', ')} (Map Editor: ${mapEditorRift})`, log)
  if (process.env.RL_SHOTS) await p.screenshot({ path: `${process.env.RL_SHOTS}/crystal-health-group.png` })
  await p.evaluate(() => [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Save Mission').click())
  await waitFor(p, 'the save to finish', () => ![...document.querySelectorAll('button')]
    .some(b => /Saving/.test(b.textContent)), { timeout: 300000, polling: 500 })
  const rec = await p.evaluate(async (i) => (await fetch('api/bucket/item/' + encodeURIComponent(i))).json(), id)
  const saved = ((rec.plan || {}).added || []).find(a => a.kind === 'crystal') || {}
  const c = saved.crystal || {}
  const g = (c.groups || [])[0] || {}
  const health = c.difficultyHealth || []
  check(health[1] === CRYSTAL_WAR_MAGE && health[3] === CRYSTAL_ENDLESS,
    `plan health after the save: ${JSON.stringify(health)}`, log)
  const t = (g.triggers || [])[0] || {}
  const rows = (g.waves || [])[0] || []
  check(t.launchAfter === CRYSTAL_LAUNCH_AFTER && t.launchAlive === 0 && t.alive === 0 && t.repeat === CRYSTAL_REPEAT && !('close' in t) &&
    c.waveCount === (g.waves || []).length &&
    c.waveCount > 1 && rows.length >= 1 && rows[0].unit === CRYSTAL_ROW.unit && rows[0].count === CRYSTAL_ROW.count &&
    ((g.rifts || [])[0]?.chances || [])[1] === 0 && !(g.minions || []).length,
  `plan group after the save: ${JSON.stringify({ trigger: t, waveCount: c.waveCount, wave1: rows, chances: (g.rifts || [])[0]?.chances })}`, log)

  await openMission(p, urls, id, log)
  await waitFor(p, 'the mission\'s Crystal', () => window.__rl_planBits().added.some(a => a.kind === 'crystal'),
    { timeout: 60000, polling: 250 })
  await p.evaluate(() => document.querySelector('#openkit').click())
  await p.evaluate(() => {
    const s = document.querySelector('#kitsearch'); s.value = 'crystal'; s.dispatchEvent(new Event('input'))
  })
  await clickIn(p, '#kitgrid [data-name="crystal"]')
  await waitFor(p, 'the Crystal panel', () => document.querySelector('#crystalgname0'), { timeout: 60000, polling: 250 })
  const back = await p.evaluate(() => [document.querySelector('#crystalgname0').value,
    !!document.querySelector('[id^="crystalhealth"]')])
  check(back[0] === 'North-west' && !back[1], `Map Editor shows the group ${back[0]}, no health`, log)
}

//: A built Crystal Defense level with no record (Crystal Probe B, installed), and its groups.
const CRYSTAL_BUILT_LEVEL = 'CrystalProbeB_Map01'
const CRYSTAL_BUILT_GROUPS = 2

/** A built Crystal Defense level opened in the Map Editor with no record shows its Crystal
 * as the plan's (its model drawn where the level places it) with its Rift Groups and their
 * Unstable Rifts read back from the level. Passes as skipped when the level is not
 * installed. RL_SHOTS keeps crystal-level*.png. */
async function crystallevel (p, urls, mission, log) {
  await p.goto(`${urls.riftlord}/?embedded=1&theme=dark`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await waitFor(p, 'the Map Editor ready', () => window.__rl_ready, { timeout: 120000, polling: 250 })
  const listed = await p.evaluate((n) => [...document.querySelectorAll('#level option')].some(o => o.value === n), CRYSTAL_BUILT_LEVEL)
  if (!listed) { check(true, `${CRYSTAL_BUILT_LEVEL} not installed: skipped`, log); return }
  await openLevelNamed(p, urls, CRYSTAL_BUILT_LEVEL, log)
  const bits = await p.evaluate(() => {
    const i = window.__rl_planBits().added.findIndex(a => a.kind === 'crystal')
    const c = window.__rl_crystalEntry() || {}
    return { i, level: c.level, groups: ((c.crystal || {}).groups || []).map(g => [g.name, g.rifts.map(r => r.spawner)]) }
  })
  check(bits.i >= 0 && bits.level && bits.groups.length === CRYSTAL_BUILT_GROUPS &&
    bits.groups.every(g => g[1].length && g[1].every(s => s < 0)),
  `the level's Crystal (${bits.level}) with ${JSON.stringify(bits.groups)}`, log)
  await waitFor(p, 'the Crystal model', (i) => (window.__rl_addedLook(i)[0] || {}).parts > 1,
    { timeout: 120000, polling: 500 }, bits.i)
  await p.evaluate((i) => window.__rl_pickAdded(i), bits.i)
  await waitFor(p, 'the Crystal panel', () => document.querySelector('#crystalgname0'), { timeout: 60000, polling: 250 })
  const panel = await p.evaluate(() => [...document.querySelectorAll('#crystalpanel .route')].map(r => [
    r.querySelector('input').value, [...r.querySelectorAll('.routehead span')].map(x => x.textContent.trim())]))
  //: each rift named by its index in level, as the Onslaught Editor names it
  const want = bits.groups.map(g => g[1].map(s => `Unstable Rift ${-s}`))
  check(panel.length === CRYSTAL_BUILT_GROUPS && JSON.stringify(panel.map(g => g[1])) === JSON.stringify(want),
    `panel: ${JSON.stringify(panel)}`, log)
  if (process.env.RL_SHOTS) {
    await p.screenshot({ path: `${process.env.RL_SHOTS}/crystal-level.png` })
    const box = await p.$('#crystalpanel')
    if (box) await box.screenshot({ path: `${process.env.RL_SHOTS}/crystal-level-panel.png` })
  }
}

// -- the game mode (riftlord/onslaught/gamemode.py) ---------------------------------------

//: Crystal Probe A (riftlord.mapeditor.probe), only read: RL_CRYSTAL_PROBE, else the probes folder.
const GAMEMODE_PROBE = process.env.RL_CRYSTAL_PROBE ||
  `${process.env.HOME}/.cache/rift-lord/probes/Crystal_Probe_A`
const GAMEMODE_ID = '900_Crystal_Probe_A_P'
const GAMEMODE_MAP = 'RiftLordCrystalProbeA_Map01'
//: Its Unstable Rift, by its index in level; its units live on the spawner (older rows)
const GAMEMODE_RIFT = 'Unstable Rift 3'
//: Tab texts a Rift Defense mission may have (App.svelte, spec.js doorTab).
const RIFT_DEFENSE_TABS = /^(Timeline|Enemy Preview|Door \d+|Unstable Rift \d+|Spawner \d+)$/
//: The viewport width the tab overflow is checked at, px.
const NARROW = 900

/** The Onslaught Editor's game-mode view once a map is open: the header badge, the
 * settings shown, the tab texts, the map's Crystal marker and whether "Rift points" shows
 * anywhere. */
async function modeView (p) {
  await waitFor(p, 'the map\'s tabs and settings', () => document.querySelector('.badge.mode') &&
    document.querySelector('input[aria-label="Starting coin"]') && !document.querySelector('.loading'),
  { timeout: CRYSTAL_BAKE_WAIT, polling: 500 })
  return p.evaluate(() => ({
    badge: document.querySelector('.badge.mode')?.textContent.trim(),
    gameType: document.querySelector('.badge.mode')?.dataset.mode,
    riftPoints: !!document.querySelector('input[aria-label="Rift points"]'),
    riftPointsText: /rift points/i.test(document.body.innerText),
    health: [...document.querySelectorAll('.timeline .crystal .lab')].map(x => x.textContent.trim()),
    settings: [...document.querySelectorAll('.timeline input[aria-label]')].map(i => i.getAttribute('aria-label')),
    tabs: [...document.querySelectorAll('.tabs > [role=tab]')].map(t => t.textContent.trim()),
    crystalMark: document.querySelector('.panel svg .crystal title')?.textContent,
    marks: [...document.querySelectorAll('.panel svg .mark title')].map(t => t.textContent)
  }))
}

/** Open the tab whose text is `text`; what its form then shows and which map marks are lit. */
async function openTab (p, text) {
  await p.evaluate((t) => [...document.querySelectorAll('[role=tab]')].find(b => b.textContent.trim() === t).click(), text)
  await sleep(600)
  return p.evaluate(() => ({
    heads: [...document.querySelectorAll('.form .group > section:not([id^="wave-"]) > header h3')].map(h => h.textContent.trim()),
    waves: document.querySelectorAll('.form .group section[id^="wave-"]').length,
    empty: [...document.querySelectorAll('.form .group .count.empty')].map(x => x.textContent.trim()),
    rifts: [...document.querySelectorAll('.form .group .row.rift b')].map(b => b.textContent.trim()),
    lit: [...document.querySelectorAll('.panel svg .mark.on title')].map(t => t.textContent)
  }))
}

//: The per-wave chance's label and tooltip (spec.js PICK_LABEL, PICK_TIP).
const PICK_LABEL = 'Pick Chance (%)'
const PICK_TIP = 'Chance this rift is picked when the group opens in this wave'
//: The count the copy check sets on wave 1's first group row.
const COPY_COUNT = 7

//: An Open and a Wave past what the group and the onslaught have.
const TRIGGER_OPEN_OVER = 5
const TRIGGER_WAVE_OVER = 99
//: The form width the trigger is also checked at, px (the window at NARROW_TRIGGER).
const NARROW_TRIGGER = 820

/** A Rift Group trigger as the tab shows it (Trigger.svelte), the last one. */
function triggerView (p) {
  return p.evaluate(() => {
    const tr = [...document.querySelectorAll('.form .group .trigger')].at(-1)
    if (!tr) return null
    const top = (sel) => tr.querySelector(sel)?.getBoundingClientRect().top ?? null
    return { text: tr.innerText.replace(/\s+/g, ' ').trim(), when: tr.querySelector('select[aria-label="When"]').value,
      opens: tr.querySelector('select[aria-label="Opens At"]').value,
      lines: [top('.line.when'), top('.line.opens')], repeatTop: top('input[aria-label="Repeat Every"]'),
      openTop: top('input[aria-label="Open"]'),
      openBad: tr.querySelector('input[aria-label="Open"]').classList.contains('bad'),
      waveBad: !!tr.querySelector('input[aria-label="Wave"].bad'),
      errors: [...tr.querySelectorAll('.err')].map(e => e.textContent.trim()) }
  })
}

/** On an open Rift Group tab: no pick chances (they are on the Timeline) and no "Launch"; a
 * new trigger reads When / Opens At / Open / Repeat Every on two lines (also at
 * NARROW_TRIGGER), an Open over the group's rifts and a Wave over the onslaught's waves show
 * red with their reasons; wave 1 has no Copy Previous Wave and wave 2's replaces its rows
 * with wave 1's (cleared first, wave 1's first count set to COPY_COUNT). Then the Timeline
 * labels each group's chances "<group> · PICK_LABEL" with PICK_TIP. RL_SHOTS keeps
 * gamemode-pick-*.png and gamemode-trigger*.png. */
async function groupPickAndCopy (p, log, shots) {
  const tab = await p.evaluate(() => ({ text: document.querySelector('.form .group').innerText,
    chances: document.querySelectorAll('.form .group input[aria-label*="chance in wave"]').length,
    waves: document.querySelectorAll('.form .group section[id^="wave-"]').length,
    rifts: document.querySelectorAll('.form .group .row.rift').length }))
  check(tab.chances === 0 && !/Pick Chance|Launch/.test(tab.text), `group tab: no pick chances, no "Launch" (${tab.chances})`, log)
  await p.evaluate(() => [...document.querySelectorAll('.form .group button')].find(b => b.textContent.trim() === '+ Add trigger').click())
  await sleep(300)
  let tv = await triggerView(p)
  check(!!tv && /^When .*Opens At .*Open .*Repeat Every/.test(tv.text) && tv.lines[0] < tv.lines[1] &&
    Math.abs(tv.openTop - tv.repeatTop) < 4 && !tv.errors.length,
  `new trigger: "${tv && tv.text}" on two lines ${JSON.stringify(tv && tv.lines)}`, log)
  const setIn = (sel, v, ev) => p.evaluate((sel, v, ev) => {
    const el = [...document.querySelectorAll('.form .group .trigger')].at(-1).querySelector(sel)
    el.value = String(v)
    el.dispatchEvent(new Event(ev, { bubbles: true }))
  }, sel, v, ev)
  await setIn('input[aria-label="Open"]', TRIGGER_OPEN_OVER, 'input')
  await setIn('select[aria-label="When"]', 'wave', 'change')
  await sleep(200)
  await setIn('input[aria-label="Wave"]', TRIGGER_WAVE_OVER, 'input')
  await setIn('select[aria-label="Opens At"]', 'after', 'change')
  await sleep(300)
  tv = await triggerView(p)
  check(tv.openBad && tv.waveBad && tv.errors.includes(tab.rifts === 1 ? 'This group has 1 Unstable Rift' : `This group has ${tab.rifts} Unstable Rifts`) &&
    tv.errors.includes(`The onslaught has ${tab.waves} waves`) && tv.when === 'wave' && tv.opens === 'after' &&
    Math.abs(tv.openTop - tv.repeatTop) < 4,
  `out of range: ${JSON.stringify(tv.errors)}, Open and Repeat on one line (${tv.openTop}, ${tv.repeatTop})`, log)
  check(await p.evaluate(() => !![...document.querySelectorAll('button')].find(b => /Save Mission/.test(b.textContent))?.disabled),
    'the out-of-range trigger refuses the save (Save Mission disabled)', log)
  if (shots) {
    const box = await p.$('.form .group section')
    if (box) await box.screenshot({ path: `${shots}/gamemode-trigger.png` })
  }
  const vp = p.viewport()
  await p.setViewport({ width: NARROW_TRIGGER, height: vp.height })
  await sleep(500)
  tv = await triggerView(p)
  check(tv.lines[0] < tv.lines[1], `at ${NARROW_TRIGGER} px: When above Opens At ${JSON.stringify(tv.lines)}`, log)
  if (shots) {
    const box = await p.$('.form .group section')
    if (box) await box.screenshot({ path: `${shots}/gamemode-trigger-narrow.png` })
  }
  await p.setViewport(vp)
  await p.evaluate(() => [...document.querySelectorAll('.form .group .trigger')].at(-1).querySelector('button[aria-label="Remove this trigger"]').click())
  await sleep(300)
  const waveRows = (i) => p.evaluate((i) => {
    const sec = document.querySelectorAll('.form .group section[id^="wave-"]')[i]
    return [...sec.querySelectorAll('.row')].filter(r => r.querySelector('select[aria-label="Minion"]'))
      .map(r => [r.querySelector('select[aria-label="Minion"]').value, r.querySelector('input[type="number"]').value])
  }, i)
  const copyButton = (i) => p.evaluate((i) => {
    const sec = document.querySelectorAll('.form .group section[id^="wave-"]')[i]
    const b = [...sec.querySelectorAll('header button')].find(x => x.textContent.trim() === 'Copy Previous Wave')
    return b ? (b.disabled ? 'disabled' : 'enabled') : 'none'
  }, i)
  check(await copyButton(0) === 'none', 'wave 1 has no Copy Previous Wave', log)
  await p.evaluate((n) => {
    const sec = document.querySelector('.form .group section[id^="wave-"]')
    const c = sec.querySelector('.row input[type="number"]')
    c.value = String(n)
    c.dispatchEvent(new Event('input', { bubbles: true }))
  }, COPY_COUNT)
  await p.evaluate(() => {
    const sec = document.querySelectorAll('.form .group section[id^="wave-"]')[1]
    ;[...sec.querySelectorAll('header button')].find(x => x.textContent.trim() === 'Clear').click()
  })
  await sleep(300)
  const cleared = await waveRows(1)
  const before = await waveRows(0)
  check(cleared.length === 0 && before.length > 0 && before[0][1] === String(COPY_COUNT) && await copyButton(1) === 'enabled',
    `wave 2 cleared, wave 1 rows ${JSON.stringify(before)}`, log)
  await p.evaluate(() => {
    const sec = document.querySelectorAll('.form .group section[id^="wave-"]')[1]
    ;[...sec.querySelectorAll('header button')].find(x => x.textContent.trim() === 'Copy Previous Wave').click()
  })
  await sleep(300)
  const copied = await waveRows(1)
  check(JSON.stringify(copied) === JSON.stringify(before), `Copy Previous Wave: wave 2 rows ${JSON.stringify(copied)}`, log)
  await p.evaluate(() => {
    const sec = document.querySelectorAll('.form .group section[id^="wave-"]')[1]
    const c = sec.querySelector('.row input[type="number"]')
    c.value = '3'
    c.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await sleep(200)
  check((await waveRows(0))[0][1] === String(COPY_COUNT), 'the copy is its own: wave 1 kept its count after wave 2 changed', log)
  if (shots) {
    const box = await p.$$('.form .group section[id^="wave-"]')
    if (box[1]) await box[1].screenshot({ path: `${shots}/gamemode-pick-copied.png` })
  }
  await p.evaluate(() => [...document.querySelectorAll('[role=tab]')].find(b => b.textContent.trim() === 'Timeline').click())
  await sleep(600)
  const labels = await p.evaluate(() => [...document.querySelectorAll('.timeline .lab.pick')].map(l => [l.textContent.trim(), l.title]))
  check(labels.length > 0 && labels.every(l => l[0].endsWith(` · ${PICK_LABEL}`) && l[1] === PICK_TIP),
    `Timeline chance labels: ${JSON.stringify(labels.slice(0, 2))}`, log)
  if (shots) {
    const box = await p.$('.timeline section[id^="wave-"]')
    if (box) await box.screenshot({ path: `${shots}/gamemode-pick-timeline.png` })
  }
}

/** The game mode: Crystal Probe A opened from its pak with no record (as from the map
 * picker), then from its record copied into the scratch Missions, reads Crystal Defense
 * the same both ways: the header says so, no Rift points anywhere, Crystal Health on the
 * Timeline, a tab per Rift Group (none for its Unstable Rift) with Opens, Unstable Rifts
 * and its waves, its rift lit on the map, and the map draws the Crystal with the mode's
 * icon; from the record, the group's chances and Copy Previous Wave (groupPickAndCopy).
 * A Rift Defense mission (the suite's) says Rift Defense and shows Rift points and
 * its door tabs; at NARROW the tabs that do not fit are behind a "…" tab, with no
 * scrollbar. Passes as skipped with no Crystal Probe A. RL_SHOTS keeps gamemode-*.png. */
async function gamemode (p, urls, mission, log) {
  if (!process.env.RL_MISSIONS) throw new Error('gamemode needs the scratch Missions of testservers.py (RL_MISSIONS)')
  const pak = `${GAMEMODE_PROBE}/_out/${GAMEMODE_ID}.pak`
  const record = `${GAMEMODE_PROBE}/.riftlord/records/${GAMEMODE_ID}.json`
  if (!fs.existsSync(pak) || !fs.existsSync(record)) {
    check(true, `no Crystal Probe A at ${GAMEMODE_PROBE}: skipped`, log)
    return
  }
  const shots = process.env.RL_SHOTS
  const post = (path, body) => p.evaluate(async (u, b) => {
    const r = await fetch(u, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Onslaught-Editor': '1' },
      body: JSON.stringify(b) })
    return r.ok
  }, path, body)
  const crystalChecks = async (tag, v) => {
    check(v.badge === 'Crystal Defense' && v.gameType === 'CrystalDefense', `${tag}: header says ${v.badge}`, log)
    check(!v.riftPoints && !v.riftPointsText && v.settings.includes('Starting coin') &&
      v.health.join(',') === CRYSTAL_HEALTH_LABELS.join(','),
    `${tag}: Crystal Health ${v.health.join(', ')}, no Rift points`, log)
    const group = v.tabs.find(t => !/^(Timeline|Enemy Preview|Door \d+)$/.test(t))
    check(!!group && !v.tabs.some(t => /^(Unstable Rift|Rift Tear|Crystal Rift|Rift \d)/.test(t)),
      `${tag}: tabs ${v.tabs.join(', ')}`, log)
    //: the Unstable Rift is numbered by its index in level (3), in the map and the tab alike
    check(v.crystalMark === 'Crystal' && v.marks.includes(GAMEMODE_RIFT),
      `${tag}: map draws the Crystal (${v.marks.join(', ')})`, log)
    const tab = await openTab(p, group)
    check(tab.heads.join(',') === 'Opens,Unstable Rifts' && tab.waves > 0 &&
      tab.rifts.join(',') === GAMEMODE_RIFT && tab.lit.join(',') === GAMEMODE_RIFT && !tab.empty.length,
    `${tag}: ${group} tab: ${tab.heads.join(', ')}, ${tab.waves} waves with rows, rifts ${tab.rifts.join(', ')}, lit ${tab.lit.join(', ')}`, log)
    return group
  }

  //: 1. its pak opened with no record, as an installed or opened pak is from the map picker
  await p.goto(`${urls.onslaught}/?embedded=1&theme=dark`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await waitFor(p, 'the Onslaught Editor', () => document.querySelector('select[aria-label="Map"]') &&
    !document.querySelector('.loading'), { timeout: 180000, polling: 500 })
  check(await post('api/openpak', { path: pak }), `opened ${pak}`, log)
  await p.goto(`${urls.onslaught}/?embedded=1&theme=dark&map=${GAMEMODE_MAP}`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  const fromPak = await modeView(p)
  if (shots) await p.screenshot({ path: `${shots}/gamemode-crystal-pak.png` })
  const pakGroup = await crystalChecks('pak', fromPak)
  if (shots) await p.screenshot({ path: `${shots}/gamemode-crystal-pak-group.png` })
  check(await post('api/closepak', { path: pak }), 'closed the pak', log)

  //: 2. its record (a plan from before Rift Tear groups) and build as a Missions item
  const missions = process.env.RL_MISSIONS
  fs.mkdirSync(`${missions}/.riftlord/records`, { recursive: true })
  fs.copyFileSync(record, `${missions}/.riftlord/records/${GAMEMODE_ID}.json`)
  fs.cpSync(`${GAMEMODE_PROBE}/${GAMEMODE_ID}`, `${missions}/${GAMEMODE_ID}`, { recursive: true })
  const notes = `${GAMEMODE_PROBE}/${GAMEMODE_ID}.CustomMaps.yaml`
  if (fs.existsSync(notes)) fs.copyFileSync(notes, `${missions}/${GAMEMODE_ID}.CustomMaps.yaml`)
  check(await post('api/rescan', {}), 'rescanned with the record in Missions', log)
  await p.goto(`${urls.onslaught}/?embedded=1&theme=dark&bucket=${GAMEMODE_ID}&open=1`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  const fromRecord = await modeView(p)
  if (shots) await p.screenshot({ path: `${shots}/gamemode-crystal-record.png` })
  const recGroup = await crystalChecks('record', fromRecord)
  if (shots) await p.screenshot({ path: `${shots}/gamemode-crystal-record-group.png` })
  await groupPickAndCopy(p, log, shots)
  check(!!pakGroup && !!recGroup && fromPak.tabs.length === fromRecord.tabs.length,
    `the pak and the record open with the same tabs (${fromPak.tabs.length})`, log)
  //: the copy leaves the scratch Missions, so later cases see it as it was
  fs.rmSync(`${missions}/.riftlord/records/${GAMEMODE_ID}.json`, { force: true })
  fs.rmSync(`${missions}/${GAMEMODE_ID}`, { recursive: true, force: true })
  fs.rmSync(`${missions}/${GAMEMODE_ID}.CustomMaps.yaml`, { force: true })
  check(await post('api/rescan', {}), 'rescanned without the copy', log)

  //: 3. a Rift Defense mission as before, then its tabs at NARROW
  await p.goto(`${urls.onslaught}/?embedded=1&theme=dark&bucket=${encodeURIComponent(mission)}&open=1`,
    { waitUntil: 'domcontentloaded', timeout: 60000 })
  const rift = await modeView(p)
  check(rift.badge === 'Rift Defense' && rift.gameType === '', `${mission}: header says ${rift.badge}`, log)
  check(rift.riftPoints && rift.settings.includes('Rift points') && !rift.health.length, `${mission}: Rift points shown`, log)
  check(rift.tabs.every(t => RIFT_DEFENSE_TABS.test(t)) && !rift.crystalMark &&
    !rift.marks.some(m => /Crystal/.test(m)), `${mission}: tabs ${rift.tabs.join(', ')}`, log)
  if (shots) await p.screenshot({ path: `${shots}/gamemode-rift.png` })
  await p.setViewport({ width: NARROW, height: 920 })
  await sleep(800)
  const strip = await p.evaluate(() => {
    const s = document.querySelector('.tabs')
    return { shown: [...s.querySelectorAll(':scope > [role=tab]')].map(t => t.textContent.trim()),
      more: !!s.querySelector('[aria-label="More tabs"]'), scroll: s.scrollWidth - s.clientWidth,
      overflowX: getComputedStyle(s).overflowX }
  })
  check(strip.more && strip.scroll <= 1 && strip.shown.length < rift.tabs.length,
    `at ${NARROW} px: ${strip.shown.length} of ${rift.tabs.length} tabs, then "…"; no scrollbar (${strip.scroll}, ${strip.overflowX})`, log)
  await p.evaluate(() => document.querySelector('[aria-label="More tabs"]').click())
  await sleep(300)
  const rest = await p.evaluate(() => [...document.querySelectorAll('.tabs .list [role=tab]')].map(t => t.textContent.trim()))
  check(rest.length + strip.shown.length === rift.tabs.length && rest.at(-1) === rift.tabs.at(-1),
    `"…" lists ${rest.join(', ')}`, log)
  if (shots) await p.screenshot({ path: `${shots}/gamemode-tabs-more.png` })
  await p.evaluate(() => [...document.querySelectorAll('.tabs .list [role=tab]')].at(-1).click())
  await sleep(500)
  const chosen = await p.evaluate(() => ({ list: !!document.querySelector('.tabs .list'),
    on: document.querySelector('[aria-label="More tabs"]')?.classList.contains('on'),
    waves: document.querySelectorAll('.form section[id^="wave-"]').length }))
  check(!chosen.list && chosen.on && chosen.waves > 0, `picked ${rift.tabs.at(-1)} from the list`, log)
  await p.setViewport({ width: 1500, height: 920 })
}

// -- the Onslaught Editor's floors and music preview ---------------------------------------

//: A shipped map of three floors whose doors and rift are on different floors, and which
//: has a music change; then a map of one floor.
const FLOORS_MAP = 'Scenario_11'
const FLOORS_COUNT = 3
const ONE_FLOOR_MAP = 'Scenario_02'
//: How long a music preview may take to start playing, in ms.
const MUSIC_WAIT = 60000

/** The map pane's floor state: the Floor dropdown, the volume drawn, the lit and faded marks. */
function floorView (p) {
  return p.evaluate(() => {
    const sel = document.querySelector('.panel select[aria-label="Floor"]')
    const art = document.querySelector('.panel svg image[data-volume]')
    const img = art ? art.getAttribute('href') : ''
    return { options: sel ? [...sel.options].map(o => [o.value, o.textContent.trim()]) : null,
      value: sel ? sel.value : null, volume: art ? art.dataset.volume : null, img,
      on: [...document.querySelectorAll('.panel svg .mark.on')].map(m => [m.querySelector('title').textContent, m.classList.contains('away')]),
      away: document.querySelectorAll('.panel svg .mark.away').length }
  })
}

/** Floors and the music preview in the Onslaught Editor. FLOORS_MAP opens on its rift's
 * floor with a Floor dropdown of Floor 1..FLOORS_COUNT; each door's tab shows the door's
 * floor, the door lit and not faded, marks of other floors faded; picking a floor draws that
 * floor's minimap (served). A Music change event on the Timeline has a play button that
 * plays (stop icon), stops on a second click, and stops when the track changes. A map of one
 * floor has no Floor dropdown. Every floor's minimap is preloaded, so a door tab's floor is
 * drawn in the next frame. The page answers the shell's riftlord-open-state question: open
 * false with nothing open, true with a map open. Then the preview inside the app
 * (musicInApp). RL_SHOTS keeps onslaughtfloors-*.png. */
async function onslaughtFloors (p, urls, mission, log) {
  const shots = process.env.RL_SHOTS
  //: the answer to the shell's riftlord-open-state question (a top page is its own parent)
  const openState = () => p.evaluate(() => new Promise((resolve) => {
    const id = Math.random()
    const take = (e) => {
      if (e.data?.type !== 'riftlord-open-state' || e.data.id !== id || !('open' in e.data)) return
      window.removeEventListener('message', take)
      resolve(e.data.open)
    }
    window.addEventListener('message', take)
    window.postMessage({ type: 'riftlord-open-state', id }, '*')
    setTimeout(() => resolve('no answer'), 600)
  }))
  await p.goto(`${urls.onslaught}/?embedded=1&theme=dark`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await waitFor(p, 'the Onslaught Editor', () => document.querySelector('select[aria-label="Map"]') &&
    !document.querySelector('.loading'), { timeout: 180000, polling: 500 })
  check(await openState() === false, 'nothing open: riftlord-open-state answers open false', log)
  await p.goto(`${urls.onslaught}/?embedded=1&theme=dark&map=${FLOORS_MAP}`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await waitFor(p, `${FLOORS_MAP} open`, () => document.querySelector('input[aria-label="Starting coin"]') &&
    !document.querySelector('.loading') && document.querySelector('.panel svg image'), { timeout: 180000, polling: 500 })
  check(await openState() === true, 'a map open: riftlord-open-state answers open true', log)
  const detail = await p.evaluate(async (m) => (await fetch('api/map/' + m)).json(), FLOORS_MAP)
  let v = await floorView(p)
  check(v.options && v.options.length === FLOORS_COUNT && v.options.every((o, i) => o[1] === `Floor ${i + 1}`) &&
    v.value === String(detail.volume) && v.volume === String(detail.volume),
  `${FLOORS_MAP}: ${JSON.stringify(v.options)}, opens on volume ${v.volume} (rift's ${detail.volume})`, log)
  if (shots) await p.screenshot({ path: `${shots}/onslaughtfloors-timeline.png` })
  //: every floor's art is fetched when the map opens
  await waitFor(p, 'every floor\'s minimap preloaded', (n) => {
    const art = document.querySelector('.panel svg image[data-volume]')
    return art && art.getAttribute('href').startsWith('blob:') && performance.getEntriesByType('resource')
      .filter(e => /minimap\.png\?volume=/.test(e.name)).length >= n
  }, { timeout: 30000, polling: 200 }, FLOORS_COUNT)
  const doors = detail.doors.filter(d => d.kind === 'door' && d.at)
  const floorsSeen = new Set()
  for (const d of doors) {
    //: the tab's floor is drawn in the next frame, from the preloaded art
    const drawn = await p.evaluate((label) => new Promise((resolve) => {
      [...document.querySelectorAll('[role=tab]')].find(t => t.textContent.trim() === label).click()
      requestAnimationFrame(() => {
        const art = document.querySelector('.panel svg image[data-volume]')
        resolve([art.dataset.volume, art.getAttribute('href').slice(0, 5)])
      })
    }), d.label)
    check(drawn[0] === String(d.at.f) && drawn[1] === 'blob:', `${d.label}: volume ${drawn[0]} drawn in the next frame from ${drawn[1]}`, log)
    await sleep(300)
    v = await floorView(p)
    floorsSeen.add(v.volume)
    check(v.value === String(d.at.f) && v.volume === String(d.at.f) && v.on.length === 1 && v.on[0][0] === d.label &&
      !v.on[0][1] && (v.away > 0) === detail.doors.some(x => x.at && x.at.f !== d.at.f),
    `${d.label} on volume ${d.at.f}: shown ${v.volume}, lit ${JSON.stringify(v.on)}, ${v.away} faded`, log)
    if (shots) await p.screenshot({ path: `${shots}/onslaughtfloors-${d.label.replace(/ /g, '')}.png` })
  }
  check(floorsSeen.size > 1, `the door tabs showed ${floorsSeen.size} floors`, log)
  const other = v.options.map(o => o[0]).find(x => x !== v.value)
  await setSvelteField(p, '.panel select[aria-label="Floor"]', other)
  await sleep(500)
  v = await floorView(p)
  const served = await p.evaluate(async (u) => { const r = await fetch(u); return [r.status, (await r.blob()).type] }, v.img)
  check(v.volume === other && served[0] === 200 && served[1] === 'image/png',
    `picked volume ${other}: drawn ${v.volume}, served ${served.join(' ')}`, log)

  // the music preview on a Music change event
  await p.evaluate(() => [...document.querySelectorAll('[role=tab]')].find(t => t.textContent.trim() === 'Timeline').click())
  await sleep(500)
  await p.evaluate(() => [...document.querySelectorAll('.timeline button')].find(b => b.textContent.trim() === '+ Music change').click())
  await waitFor(p, 'the music event\'s play button', () => document.querySelector('.timeline .event button.play'), { timeout: 10000, polling: 200 })
  const state = () => p.evaluate(() => {
    const b = document.querySelector('.timeline .event button.play')
    return b ? [b.dataset.state, b.getAttribute('aria-label')] : null
  })
  check(JSON.stringify(await state()) === JSON.stringify(['idle', 'Hear this track']), `play button: ${JSON.stringify(await state())}`, log)
  await p.click('.timeline .event button.play')
  await waitFor(p, 'the preview playing', () => document.querySelector('.timeline .event button.play')?.dataset.state === 'playing',
    { timeout: MUSIC_WAIT, polling: 250 })
  check(JSON.stringify(await state()) === JSON.stringify(['playing', 'Stop']), `playing: ${JSON.stringify(await state())}`, log)
  if (shots) {
    const row = await p.$('.timeline .event')
    if (row) await row.screenshot({ path: `${shots}/onslaughtfloors-music-playing.png` })
  }
  await p.click('.timeline .event button.play')
  await sleep(300)
  check((await state())[0] === 'idle', `a second click stops it: ${JSON.stringify(await state())}`, log)
  await p.click('.timeline .event button.play')
  await waitFor(p, 'the preview playing again', () => document.querySelector('.timeline .event button.play')?.dataset.state === 'playing',
    { timeout: MUSIC_WAIT, polling: 250 })
  const track = await p.evaluate(() => {
    const s = document.querySelector('.timeline .event select[aria-label="Music track"]')
    return [...s.options].map(o => o.value).find(x => x !== s.value)
  })
  await setSvelteField(p, '.timeline .event select[aria-label="Music track"]', track)
  await sleep(300)
  check((await state())[0] === 'idle', `a track change stops it: ${JSON.stringify(await state())}`, log)

  // one floor: no Floor dropdown
  await p.evaluate((m) => {
    const s = document.querySelector('select[aria-label="Map"]'); s.value = m
    s.dispatchEvent(new Event('change', { bubbles: true }))
  }, ONE_FLOOR_MAP)
  await waitFor(p, `${ONE_FLOOR_MAP} open`, (m) => document.querySelector('select[aria-label="Map"]').value === m &&
    !document.querySelector('.loading') && document.querySelector('.panel svg image'), { timeout: 60000, polling: 250 }, ONE_FLOOR_MAP)
  v = await floorView(p)
  check(v.options === null, `${ONE_FLOOR_MAP}: no Floor dropdown`, log)

  await musicInApp(p, urls, log, shots)
}

//: The app window's music paths (shell/window/src/main.rs music::PATHS), which the stand-in
//: window below fetches, as the real one does.
function windowMusicPaths () {
  const src = fs.readFileSync(require('path').join(__dirname, '..', '..', 'shell', 'window', 'src', 'main.rs'), 'utf8')
  const m = src.match(/const PATHS: \[&str; \d+\] = \[([^\]]*)\]/)
  return m ? [...m[1].matchAll(/"([^"]+)"/g)].map(x => x[1]) : []
}

/** The music preview inside the app: the shell page with a stand-in window (window.ipc) that
 * plays only what the real window fetches (main.rs: loopback, music::PATHS) and answers
 * through the shell (window.__riftlordMusic), on a page of its own. With the shell's
 * /api/tools held back (TOOLS_DELAY_MS) the Onslaught tab is disabled until it answers, then
 * opens its frame. The Onslaught Editor's frame on FLOORS_MAP: a Music change event's play button asks
 * the window for a track it accepts and shows stop once the window plays it; a second click
 * asks the window to stop. */
async function musicInApp (casePage, urls, log, shots) {
  const paths = windowMusicPaths()
  check(paths.length > 0, `the window's music paths: ${paths.join(', ')}`, log)
  //: a page of its own, so nothing an earlier case or step left in a page carries over
  const p = await openPage(casePage.browser(), log)
  try {
    await shellMusic(p, urls, log, shots, paths)
  } finally {
    await p.close().catch(() => {})
  }
}

//: How long the case holds back the shell's /api/tools, in ms, as a busy server does: the
//: editors' tabs stay disabled until it answers.
const TOOLS_DELAY_MS = 2500

async function shellMusic (p, urls, log, shots, paths) {
  await p.setRequestInterception(true)
  p.on('request', (r) => {
    if (new URL(r.url()).pathname === '/api/tools') setTimeout(() => r.continue().catch(() => {}), TOOLS_DELAY_MS)
    else r.continue().catch(() => {})
  })
  await p.evaluateOnNewDocument((paths) => {
    if (window.parent !== window) return                       // the shell page only
    window.__ipc = []
    window.ipc = {
      postMessage: (raw) => {
        const m = JSON.parse(raw)
        window.__ipc.push(m)
        if (m.music === 'stop') setTimeout(() => window.__riftlordMusic('stopped', ''), 20)
        if (m.music !== 'play') return
        const ok = m.url.startsWith('http://127.0.0.1:') && paths.some(x => new URL(m.url).pathname.startsWith(x))
        setTimeout(async () => {
          if (!ok) { window.__riftlordMusic('error', m.url); return }
          const r = await fetch(m.url)
          const body = r.ok ? await r.arrayBuffer() : null
          window.__riftlordMusic(body && body.byteLength ? 'playing' : 'error', m.url)
        }, 20)
      }
    }
  }, paths)
  await p.goto(`${urls.shell}/`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await waitFor(p, 'the shell', () => document.querySelector('button.tab[aria-label="Onslaught Editor"]'), { timeout: 120000, polling: 250 })
  //: the editors' tabs wait for the tools' addresses (disabled until /api/tools answers)
  const waiting = await p.evaluate(() => document.querySelector('button.tab[aria-label="Onslaught Editor"]').disabled)
  await waitFor(p, 'the Onslaught tab enabled', () => !document.querySelector('button.tab[aria-label="Onslaught Editor"]').disabled,
    { timeout: 60000, polling: 200 })
  await p.evaluate(() => document.querySelector('button.tab[aria-label="Onslaught Editor"]').click())
  await waitFor(p, 'the Onslaught Editor frame', (u) => document.querySelector('iframe[data-tool="onslaught"]')?.src.startsWith(u + '/'),
    { timeout: 30000, polling: 200 }, urls.onslaught)
  const f = await (await p.$('iframe[data-tool="onslaught"]')).contentFrame()
  check(waiting && !!f, 'the Onslaught tab waits, disabled, for the tools\' addresses, then opens its frame', log)
  await f.goto(`${urls.onslaught}/?embedded=1&theme=dark&map=${FLOORS_MAP}`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await waitFor(f, `${FLOORS_MAP} open in the frame`, () => document.querySelector('input[aria-label="Starting coin"]') &&
    !document.querySelector('.loading'), { timeout: 180000, polling: 500 })
  await f.evaluate(() => [...document.querySelectorAll('.timeline button')].find(b => b.textContent.trim() === '+ Music change').click())
  await waitFor(f, 'the music event\'s play button', () => document.querySelector('.timeline .event button.play'), { timeout: 10000, polling: 200 })
  //: clicked in the frame's page (a pointer click by position misses inside the shell's frame)
  const press = () => f.evaluate(() => document.querySelector('.timeline .event button.play').click())
  await press()
  await waitFor(f, 'the window playing the preview', () => document.querySelector('.timeline .event button.play')?.dataset.state === 'playing',
    { timeout: MUSIC_WAIT, polling: 250 }).catch(() => {})
  const asked = await p.evaluate(() => window.__ipc.filter(m => m.music))
  const shown = await f.evaluate(() => document.querySelector('.timeline .event button.play').dataset.state)
  check(shown === 'playing' && asked.length === 1 && asked[0].music === 'play' &&
    paths.some(x => new URL(asked[0].url).pathname.startsWith(x)),
  `in the app: asked the window ${JSON.stringify(asked)}, the button shows ${shown}`, log)
  if (shots) {
    const row = await f.$('.timeline .event')
    if (row) await row.screenshot({ path: `${shots}/onslaughtfloors-music-in-app.png` })
  }
  await press()
  await sleep(300)
  const after = await p.evaluate(() => window.__ipc.filter(m => m.music).map(m => m.music))
  const idle = await f.evaluate(() => document.querySelector('.timeline .event button.play').dataset.state)
  check(idle === 'idle' && after.at(-1) === 'stop', `a second click: the window asked to ${after.at(-1)}, the button shows ${idle}`, log)
}

// -- Supported Mods (riftlord/core/supportedmods.py, Settings.svelte) ----------------------

//: The supported mods, in the order Settings > Supported Mods lists the detected ones.
const SUPPORTED_MODS = ['OrcsMustUI', 'CrystalDefense', 'TreasureChests']
//: The status labels a row may show.
const MOD_LABELS = { installed: 'Detected', missing: 'Not detected', incompatible: 'Detected (incompatible version)' }
//: Each gated New Object card: its search term and card name, by feature.
const MOD_CARDS = { crystal: ['crystal', 'crystal'], treasureChest: ['TreasureChest', 'BP_TreasureChest_C'],
  staminaPotion: ['StaminaPotion', 'BP_Pickup_StaminaPotion_C'] }

/** Put the stand-in mods (tests/ui_smoke/fakemods.py) in `states` under the server's Paks
 * override folder. */
function fakeMods (root, states) {
  const path = require('path')
  require('child_process').execFileSync('nice', ['-n', '10', 'python3', '-B',
    path.join(__dirname, 'fakemods.py'), root, ...Object.entries(states).map(([k, v]) => `${k}=${v}`)],
  { stdio: 'pipe' })
}

/** Open Settings and read the Supported Mods rows: [{id, name, status, file, page, missing}]. */
async function modRows (p) {
  await waitFor(p, 'the Supported Mods list', () => document.querySelectorAll('.mods .mod').length > 0,
    { timeout: 30000, polling: 200 })
  return p.evaluate(() => [...document.querySelectorAll('.mods .mod')].map(r => ({
    id: r.dataset.mod, name: r.querySelector('.name').textContent.trim(),
    status: r.querySelector('.status').textContent.trim(), file: r.querySelector('.file').textContent.trim(),
    page: !!r.querySelector('.page'),
    missing: [...r.querySelectorAll('.missing li')].map(li => li.textContent.trim()),
    hints: r.querySelectorAll('.hint, p').length })))
}

/** Click Settings' Refresh and wait for the rows to show `want` ({id: status label, or null
 * for no row}). */
async function refreshMods (p, want) {
  await p.evaluate(() => [...document.querySelectorAll('.supported button')].find(b => b.textContent.trim() === 'Refresh').click())
  await waitFor(p, `the rows ${JSON.stringify(want)}`, (want) => Object.entries(want).every(([id, label]) =>
    (document.querySelector(`.mods .mod[data-mod="${id}"] .status`)?.textContent.trim() ?? null) === label),
  { timeout: 30000, polling: 200 }, want)
}

/** Open Settings afresh and wait for its Supported Mods answer to be drawn. */
async function openModSettings (p, urls) {
  await openShell(p, urls)
  const answered = p.waitForResponse(r => r.url().includes('/api/supportedmods'), { timeout: 60000 })
  await p.evaluate(() => document.querySelector('button.tab[aria-label="Settings"]').click())
  await answered
  await sleep(300)
}

/** The gated New Object cards shown now: {feature: shown}. New Object is opened without a
 * level (its button is enabled for the case): the cards need none. */
async function modCards (p, urls) {
  await p.goto(`${urls.riftlord}/?embedded=1&theme=dark`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await waitFor(p, 'the Map Editor ready', () => window.__rl_ready, { timeout: 120000, polling: 250 })
  await p.evaluate(() => { const b = document.querySelector('#openkit'); b.disabled = false; b.click() })
  await waitFor(p, 'the New Object cards', () => document.querySelectorAll('#kitgrid .kitcard').length > 0,
    { timeout: 120000, polling: 250 })
  const out = {}
  for (const [feature, [term, name]] of Object.entries(MOD_CARDS)) {
    await p.evaluate((t) => {
      const s = document.querySelector('#kitsearch'); s.value = t; s.dispatchEvent(new Event('input'))
    }, term)
    await sleep(300)
    out[feature] = await p.evaluate((n) => !!document.querySelector(`#kitgrid [data-name="${n}"]`), name)
  }
  return out
}

/** Settings > Supported Mods lists the detected supported mods (OrcsMustUI, Crystal Defense,
 * Treasure Chests) with their status and the pak carrying each, labels only; a mod not
 * detected has no row and the section is hidden when none is; Refresh detects again; New
 * Object shows the Crystal, Treasure Chest and Stamina Potion cards only while their mod is
 * detected. With the server's Paks folder pointed at a scratch folder
 * (RIFTLORD_SUPPORTED_PAKS) the case writes stand-in paks there (present with real class
 * packages, absent, a class package that does not parse, two mods merged in one pak of
 * another name); else it checks the real install as it is. */
async function supportedmods (p, urls, mission, log) {
  const shots = process.env.RL_SHOTS
  const state = await getJson(`${urls.shell}/api/supportedmods`)
  check(state.mods.every(m => m.status !== 'missing') &&
    state.mods.map(m => m.id).join() === SUPPORTED_MODS.filter(id => state.mods.some(m => m.id === id)).join(),
  `api lists detected mods only: ${state.mods.map(m => `${m.name} ${m.label}`).join(', ') || 'none'}`, log)
  const override = state.override && state.root
  if (!override && !state.mods.length) {
    await openModSettings(p, urls)
    check(!(await p.$('.supported')), 'no supported mod detected: the section is hidden', log)
    return
  }
  if (override) fakeMods(state.root, { OrcsMustUI: 'installed', CrystalDefense: 'installed', TreasureChests: 'installed' })
  await openShell(p, urls)
  await p.evaluate(() => document.querySelector('button.tab[aria-label="Settings"]').click())
  let rows = await modRows(p)
  const want = override ? SUPPORTED_MODS : state.mods.map(m => m.id)
  check(rows.map(r => r.id).join() === want.join(), `rows: ${rows.map(r => r.name).join(', ')}`, log)
  // the rows as the server detects them now (the stand-ins written after the first ask)
  const listed = override ? (await getJson(`${urls.shell}/api/supportedmods`)).mods : state.mods
  const files = Object.fromEntries(listed.map(m => [m.id, 'Paks/' + m.files[0].path]))
  check(rows.every(r => Object.values(MOD_LABELS).includes(r.status) && r.file === files[r.id] && !r.hints),
    `each row: a status and its file from the Paks folder, no prose (${rows.map(r => `${r.status} ${r.file}`).join('; ')})`, log)
  const links = Object.fromEntries(listed.map(m => [m.id, !!m.link]))
  check(rows.every(r => r.page === links[r.id]), `Open Page only where a link is (${rows.filter(r => r.page).map(r => r.id).join(', ') || 'none'})`, log)
  const noSuggest = await p.evaluate(() => ![...document.querySelectorAll('.supported button')].some(b => /suggest|keep/i.test(b.textContent)))
  check(noSuggest, 'no Suggest or Keep buttons', log)
  if (!override) {
    await refreshMods(p, Object.fromEntries(state.mods.map(m => [m.id, m.label])))
    check(true, 'Refresh detects again', log)
    if (shots) await (await p.$('.supported')).screenshot({ path: `${shots}/supportedmods-settings.png` })
    const cards = await modCards(p, urls)
    check(Object.entries(cards).every(([f, shown]) => shown === !!state.features[f]),
      `cards follow this machine's mods: ${JSON.stringify(cards)}`, log)
    return
  }
  check(rows.every(r => r.status === MOD_LABELS.installed), 'all three stand-ins (real class packages): Detected', log)
  if (shots) await (await p.$('.supported')).screenshot({ path: `${shots}/supportedmods-installed.png` })
  // the catalogue first read with the stand-ins in place, as on a fresh server
  let cards = await modCards(p, urls)
  check(cards.crystal && cards.treasureChest && cards.staminaPotion, `cards with every stand-in installed: ${JSON.stringify(cards)}`, log)
  // one absent, one whose class package does not parse
  fakeMods(state.root, { CrystalDefense: 'missing', TreasureChests: 'corrupt' })
  await openShell(p, urls)
  await p.evaluate(() => document.querySelector('button.tab[aria-label="Settings"]').click())
  await modRows(p)
  await refreshMods(p, { OrcsMustUI: MOD_LABELS.installed, CrystalDefense: null,
    TreasureChests: MOD_LABELS.incompatible })
  await p.evaluate(() => { for (const d of document.querySelectorAll('.mods details')) d.open = true })
  rows = await modRows(p)
  const tc = rows.find(r => r.id === 'TreasureChests')
  check(tc.missing.join() === '/Game/Mods/TreasureChests/BP_TreasureChest.BP_TreasureChest_C (does not parse: not a UE package)',
    `corrupt: Missing lists ${tc.missing.join(', ')}`, log)
  check(!rows.some(r => r.id === 'CrystalDefense'), 'a mod not detected has no row', log)
  if (shots) await (await p.$('.supported')).screenshot({ path: `${shots}/supportedmods-mixed.png` })
  cards = await modCards(p, urls)
  check(!cards.crystal && !cards.treasureChest && cards.staminaPotion,
    `cards with Crystal Defense absent and Treasure Chests corrupt: ${JSON.stringify(cards)}`, log)
  if (shots) {
    await p.evaluate(() => { const s = document.querySelector('#kitsearch'); s.value = ''; s.dispatchEvent(new Event('input')) })
    await p.evaluate(() => document.querySelector('[data-cat="gameplay"]')?.click())
    await sleep(300)
    const grid = await p.$('#kitgrid')
    if (grid) await grid.screenshot({ path: `${shots}/supportedmods-gameplay-absent.png` })
  }
  // Crystal Defense and Treasure Chests merged in one pak of another name: detected by content
  const merged = require('path').join(state.root, '~mods', 'Merged_P.pak')
  fakeMods(state.root, { 'merged:~mods/Merged_P.pak': 'CrystalDefense,TreasureChests' })
  await openModSettings(p, urls)
  rows = await modRows(p)
  check(rows.map(r => r.id).join() === SUPPORTED_MODS.join() && rows.every(r => r.status === MOD_LABELS.installed) &&
    rows.filter(r => r.file === 'Paks/~mods/Merged_P.pak').map(r => r.id).join() === 'CrystalDefense,TreasureChests',
  `merged pak: both its mods Detected from it (${rows.map(r => `${r.id} ${r.status} ${r.file}`).join('; ')})`, log)
  if (shots) await (await p.$('.supported')).screenshot({ path: `${shots}/supportedmods-merged.png` })
  cards = await modCards(p, urls)
  check(cards.crystal && cards.treasureChest && cards.staminaPotion, `cards with the merged pak: ${JSON.stringify(cards)}`, log)
  // none detected: the section is hidden
  require('fs').rmSync(merged, { force: true })
  fakeMods(state.root, { OrcsMustUI: 'missing' })
  await openModSettings(p, urls)
  check(!(await p.$('.supported')), 'no supported mod detected: the section is hidden', log)
  // all back for the cases after
  fakeMods(state.root, { OrcsMustUI: 'installed', CrystalDefense: 'installed', TreasureChests: 'installed' })
}

// -- compound kits and decoration tools (compounds.js, decortools.js) ---------------------

//: Each compound and the members it places, the anchor included (riftlord/compounds.py).
const COMPOUND_MEMBERS = { gate_door_2w: 1, gate_door_3w: 1, column_a: 4, column_b: 4, chain: 4,
  window_small: 3, window_large: 2, window_wall_2x: 3, window_wall_double: 5, window_pointed: 3,
  grinder_pit: 2 }
//: A new gate's defended side (+x) faces the camera within a quarter turn: cos 45 degrees,
//: less the angle the camera's distance makes with the gate's corner.
const GATE_FACING = 0.6
//: Half the side of the box dragged around a piece to box-select it, in screen pixels.
const MARQUEE_HALF = 25
//: The 2-wide door's place in its archway (q4_compounds.json), and how near it must stay, uu.
const DOOR_IN_GATE = [98.0, 299.8, 250.0]
const DOOR_TOLERANCE = 0.5

/** Every compound places its members as one picked group; the archway's door is a plan
 * door that follows a move and a turn of the group and goes with it on Delete. */
async function compoundKits (p, urls, mission, log) {
  await openMission(p, urls, mission, log)
  const rift = await p.evaluate(() => window.__rl_riftAt() || [0, 0, 0])
  let gate = null
  const ids = {}
  let k = 0
  for (const [key, n] of Object.entries(COMPOUND_MEMBERS)) {
    const at = [rift[0] + 900 + 700 * (k % 5), rift[1] + 900 + 900 * Math.floor(k / 5), rift[2]]
    k += 1
    const g = await p.evaluate((key, at) => window.__rl_compoundPlace(key, at), key, at)
    const st = await p.evaluate((g) => window.__rl_groupState(g), g)
    check(st.added.length === n && st.picked === n && st.added.filter(a => a.anchor).length === 1,
      `${key}: ${st.added.length} members placed and picked, one anchor (${st.added.map(a => a.name).join(', ')})`, log)
    if (key === 'gate_door_2w') gate = g
    ids[key] = g
  }
  const door0 = await p.evaluate((g) => window.__rl_groupState(g).doors, gate)
  check(door0.length === 1 && door0[0].cls === 'BP_Door_Exterior_2W_C', `archway's door is plan door ${door0[0] && door0[0].id}`, log)
  const rel = await p.evaluate((g) => window.__rl_doorInGroup(g), gate)
  const off = Math.max(...rel.t.map((v, i) => Math.abs(v - DOOR_IN_GATE[i])))
  check(off < DOOR_TOLERANCE && Math.abs(rel.x[1] + 1) < 1e-3, `door sits at ${rel.t.map(v => v.toFixed(1))} in the archway, facing -y`, log)
  await p.evaluate((g) => window.__rl_pickGroup(g), gate)
  await p.evaluate(() => window.__rl_gizmoDrag('translate', 'X', [0.0, 0.0], [0.2, 0.0], true))
  const moved = await p.evaluate((g) => window.__rl_groupState(g).doors[0], gate)
  const relMoved = await p.evaluate((g) => window.__rl_doorInGroup(g), gate)
  const offMoved = Math.max(...relMoved.t.map((v, i) => Math.abs(v - DOOR_IN_GATE[i])))
  check(Math.hypot(moved.at[0] - door0[0].at[0], moved.at[1] - door0[0].at[1]) > 1 && offMoved < DOOR_TOLERANCE,
    `move: the door went ${door0[0].at.map(Math.round)} -> ${moved.at.map(Math.round)} and stays in its archway`, log)
  await p.evaluate(() => window.__rl_gizmoDrag('rotate', 'Z', [0.05, 0.05], [0.31, 0.17], true))
  const turned = await p.evaluate((g) => window.__rl_groupState(g).doors[0], gate)
  const relTurned = await p.evaluate((g) => window.__rl_doorInGroup(g), gate)
  const offTurned = Math.max(...relTurned.t.map((v, i) => Math.abs(v - DOOR_IN_GATE[i])))
  check(Math.abs(turned.rotation - moved.rotation) > 1 && offTurned < DOOR_TOLERANCE && Math.abs(relTurned.x[1] + 1) < 1e-3,
    `turn: the door turned ${moved.rotation.toFixed(1)} -> ${turned.rotation.toFixed(1)} degrees and stays in its archway`, log)
  const saved = await p.evaluate(async (g) => {
    await window.__rl_savePlan()
    const plan = await (await fetch('api/plan/' + encodeURIComponent(window.__rl_levelName()))).json()
    return { members: plan.added.filter(a => a.group === g).length,
             anchor: plan.added.find(a => a.group === g && a.anchor)?.compound,
             doors: plan.doors.filter(d => d.group === g).length,
             chain: (plan.added.find(a => a.compound === 'chain') || {}).params }
  }, gate)
  check(saved.members === 1 && saved.anchor === 'gate_door_2w' && saved.doors === 1 && saved.chain && saved.chain.links,
    `saved: the gate group, its door and the chain's params (${JSON.stringify(saved.chain)}) survive`, log)
  // a fresh gate faces the viewer with its defended side
  const fresh = await p.evaluate((at) => window.__rl_compoundPlace('gate_door_2w', at), [rift[0] - 1500, rift[1] - 1500, rift[2]])
  const facing = await p.evaluate((g) => window.__rl_gateFacing(g), fresh)
  check(facing > GATE_FACING, `a new gate turns its defended side to the camera (cos ${facing.toFixed(2)})`, log)
  // copy and paste keep the group and its door
  await p.evaluate((g) => window.__rl_pickGroup(g), fresh)
  await p.focus('canvas')
  await p.keyboard.down('Control'); await p.keyboard.press('c'); await p.keyboard.press('v'); await p.keyboard.up('Control')
  await sleep(500)
  const pastedGroup = await p.evaluate(() => window.__rl_lastGroup())
  const copy = await p.evaluate((g) => window.__rl_groupState(g), pastedGroup)
  const copyRel = await p.evaluate((g) => window.__rl_doorInGroup(g), pastedGroup)
  const copyOff = copyRel ? Math.max(...copyRel.t.map((v, i) => Math.abs(v - DOOR_IN_GATE[i]))) : Infinity
  check(pastedGroup !== fresh && copy.added.length === 1 && copy.added[0].anchor && copy.doors.length === 1 &&
    copyOff < DOOR_TOLERANCE && copy.picked === 1,
    `copy and paste: group ${pastedGroup} with its archway and its own door, in place`, log)
  // box select picks a whole group
  const column = await p.evaluate((g) => window.__rl_groupState(g), ids.column_a)
  const colIndex = await p.evaluate((g) => window.__rl_groupIndex(g), ids.column_a)
  const colAt = column.added[0].matrix.slice(12, 15)
  await lookDownAt(p, colAt, 4000)
  await p.evaluate(() => window.__rl_pickAdded(-1))
  const sc = await p.evaluate((i) => window.__rl_addedScreen(i), colIndex)
  await p.mouse.move(sc.x - MARQUEE_HALF, sc.y - MARQUEE_HALF)
  await p.mouse.down()
  await p.mouse.move(sc.x, sc.y, { steps: 4 })
  await p.mouse.move(sc.x + MARQUEE_HALF, sc.y + MARQUEE_HALF, { steps: 4 })
  await p.mouse.up()
  await sleep(300)
  const boxed = await p.evaluate((g) => window.__rl_groupState(g).picked, ids.column_a)
  check(boxed === column.added.length, `box select around the column's base picks the whole stack (${boxed} of ${column.added.length})`, log)
  await p.evaluate((g) => window.__rl_pickGroup(g), gate)
  await p.focus('canvas')
  await p.keyboard.press('Delete')
  await sleep(300)
  const gone = await p.evaluate((g) => window.__rl_groupState(g), gate)
  check(gone.added.length === 0 && gone.doors.length === 0, 'Delete takes the archway and its door', log)
}

//: The fewest compounds the compound filter lists (riftlord/data/compounds.json and the 25
//: hand-made ones), the cards in view whose previews the first open waits for, and how long
//: that may take (ms).
const COMPOUND_CARDS_MIN = 250
const COMPOUND_FIRST_VIEW = 8
const COMPOUND_FIRST_MS = 10000
//: A mined compound placed from its card (kit anchor), its pieces with the anchor, and one
//: with an effect member.
const MINED_CARD = 'stairs_with_railings'
const MINED_CARD_PIECES = 5
const MINED_FX = 'horned_cauldron_ice'

/** New Object with hundreds of compounds: the compound filter lists them all, its first
 * cards in view show their previews quickly, Group and Art Set narrow the list and offer only
 * values some compound carries, scrolling draws the rest, and a mined compound places as one
 * group from its card (an effect member too, kept on save). */
async function compoundBrowser (p, urls, mission, log) {
  await openMission(p, urls, mission, log)
  await clickIn(p, '#openkit')
  await waitFor(p, 'the New Object cards', () => document.querySelectorAll('#kitgrid .kitcard').length > 0,
    { timeout: 120000, polling: 250 })
  // a mesh card's textured preview (omd3thumb) loads, under the drawing's version
  await waitFor(p, 'a mesh card preview', () => {
    const img = document.querySelector('#kitgrid .kitcard[data-kind="mesh"] img')
    return !!img && img.complete && img.naturalWidth > 0
  }, { timeout: 120000, polling: 100 })
  const card = await p.evaluate(() => {
    const img = document.querySelector('#kitgrid .kitcard[data-kind="mesh"] img')
    return { src: img.getAttribute('src'), w: img.naturalWidth }
  })
  check(/^api\/thumb\/[^?]+\?v=\w+$/.test(card.src) && card.w > 0,
    `mesh card preview loads (${card.src}, ${card.w} px)`, log)
  const t0 = Date.now()
  await clickIn(p, '#kitfilters [data-cat="compound"]')
  await waitFor(p, 'the first compound previews', (n) => {
    const imgs = [...document.querySelectorAll('#kitgrid .kitcard[data-kind="compound"] img')].slice(0, n)
    return imgs.length === n && imgs.every(i => i.complete && i.naturalWidth > 0)
  }, { timeout: 120000, polling: 100 }, COMPOUND_FIRST_VIEW)
  const firstMs = Date.now() - t0
  const listed = await p.evaluate(() => ({
    total: parseInt(document.querySelector('#kitcount').textContent, 10),
    drawn: document.querySelectorAll('#kitgrid .kitcard[data-kind="compound"]').length,
    tags: !document.querySelector('#compoundtags').hidden }))
  check(listed.total >= COMPOUND_CARDS_MIN && listed.tags && listed.drawn > 0,
    `compound filter: ${listed.total} compounds, ${listed.drawn} drawn, Group and Art Set shown`, log)
  checkTime(firstMs, COMPOUND_FIRST_MS * SCALE, `first ${COMPOUND_FIRST_VIEW} compound previews in ${firstMs} ms`, log)
  // scrolling draws the rest
  for (let k = 0; k < 20; k++) {
    const done = await p.evaluate(() => {
      const g = document.querySelector('#kitgrid')
      g.scrollTop = g.scrollHeight
      g.dispatchEvent(new Event('scroll'))
      return document.querySelectorAll('#kitgrid .kitcard[data-kind="compound"]').length
    })
    if (done >= listed.total) break
    await sleep(200)
  }
  const drawn = await p.evaluate(() => document.querySelectorAll('#kitgrid .kitcard[data-kind="compound"]').length)
  check(drawn === listed.total, `scrolled to the end: ${drawn} of ${listed.total} cards drawn`, log)
  // Group and Art Set: each value lists some compounds, the groups add up, both narrow together
  const tags = await p.evaluate(async () => {
    const pick = async (id, v) => {
      const s = document.querySelector(id)
      s.value = v
      s.dispatchEvent(new Event('change'))
      await new Promise(r => setTimeout(r, 100))
      return parseInt(document.querySelector('#kitcount').textContent, 10)
    }
    const groups = [...document.querySelector('#cpgroup').options].map(o => o.value).filter(v => v !== 'all')
    const sets = [...document.querySelector('#cpset').options].map(o => o.value).filter(v => v !== 'all')
    const byGroup = {}
    for (const g of groups) byGroup[g] = await pick('#cpgroup', g)
    await pick('#cpgroup', 'all')
    const bySet = {}
    for (const v of sets) bySet[v] = await pick('#cpset', v)
    await pick('#cpgroup', groups[0])
    const both = await pick('#cpset', sets[sets.length - 1])
    const offered = [...document.querySelector('#cpgroup').options].map(o => o.value).filter(v => v !== 'all')
    await pick('#cpgroup', 'all')
    await pick('#cpset', 'all')
    return { byGroup, bySet, both, offered: offered.length, setCount: sets.length }
  })
  const groupSum = Object.values(tags.byGroup).reduce((a, b) => a + b, 0)
  const setSum = Object.values(tags.bySet).reduce((a, b) => a + b, 0)
  check(Object.values(tags.byGroup).every(n => n > 0) && groupSum === listed.total &&
    //: a compound offering walls of several art sets is under each
    Object.values(tags.bySet).every(n => n > 0) && setSum >= listed.total && tags.offered > 0,
    `Group: ${Object.entries(tags.byGroup).map(([g, n]) => `${g} ${n}`).join(', ')}; ` +
    `Art Set: ${Object.entries(tags.bySet).map(([g, n]) => `${g} ${n}`).join(', ')}; ` +
    `one group and one set: ${tags.both}, ${tags.offered} groups offered under that set`, log)
  // a mined compound from its card
  await p.evaluate((t) => {
    const s = document.querySelector('#kitsearch'); s.value = t; s.dispatchEvent(new Event('input'))
  }, 'stairs with railings')
  const before = await p.evaluate(() => window.__rl_lastGroup())
  await clickIn(p, `#kitgrid [data-name="${MINED_CARD}"]`)
  await waitFor(p, 'the placed compound', (b) => window.__rl_lastGroup() > b, { timeout: 60000, polling: 200 }, before)
  const g = await p.evaluate(() => window.__rl_lastGroup())
  await waitFor(p, 'the placed compound picked', (g, n) => window.__rl_groupState(g).picked === n,
    { timeout: 30000, polling: 100 }, g, MINED_CARD_PIECES).catch(() => {})
  const st = await p.evaluate((g) => window.__rl_groupState(g), g)
  check(st.added.length === MINED_CARD_PIECES && st.picked === MINED_CARD_PIECES &&
    st.added.filter(a => a.anchor && a.compound === MINED_CARD).length === 1,
    `${MINED_CARD} from its card: ${st.added.length} pieces placed, ${st.picked} picked, anchor ` +
    `${(st.added.find(a => a.anchor) || {}).compound} (${st.added.map(a => a.name).join(', ')})`, log)
  const rift = await p.evaluate(() => window.__rl_riftAt() || [0, 0, 0])
  const fx = await p.evaluate((key, at) => window.__rl_compoundPlace(key, at), MINED_FX, [rift[0] + 1200, rift[1] + 1200, rift[2]])
  const saved = await p.evaluate(async (g) => {
    await window.__rl_savePlan()
    const plan = await (await fetch('api/plan/' + encodeURIComponent(window.__rl_levelName()))).json()
    return plan.added.filter(a => a.group === g).map(a => ({ kind: a.kind, fx: a.fx || null }))
  }, fx)
  check(saved.length === 2 && saved.some(a => a.kind === 'fx' && a.fx),
    `${MINED_FX}: saved with its effect (${JSON.stringify(saved)})`, log)
  await p.evaluate(() => { const s = document.querySelector('#kitsearch'); s.value = ''; s.dispatchEvent(new Event('input')) })
}

//: Parametric compounds and the Group fields the case changes: a column stack's mid tiles
//: (its shipped range ends at COLUMN_MIDS) and a run's sections (riftlord/mapeditor/
//: compoundfamilies.py; pieces per section).
const PARAM_STACK = 'column_a'
const COLUMN_MIDS = 4
const PARAM_RUN = 'pointed_arcade_sand'

/** A parametric compound's Group section shows its params; changing a number field rebuilds
 * the group with that many mid tiles, that many sections. */
async function compoundParams (p, urls, mission, log) {
  await openMission(p, urls, mission, log)
  const rift = await p.evaluate(() => window.__rl_riftAt() || [0, 0, 0])
  const count = (g) => p.evaluate((g) => window.__rl_groupState(g).added.length, g)
  /** Set field `name` of the picked group's Group section and wait for the group to change. */
  const setField = async (g, selector, value) => {
    const before = await count(g)
    await waitFor(p, `the ${selector} field`, (q) => !!document.querySelector(q),
      { timeout: 30000, polling: 100 }, selector)
    await p.evaluate((q, v) => {
      const input = document.querySelector(q)
      input.value = String(v)
      input.dispatchEvent(new Event('change'))
    }, selector, value)
    await waitFor(p, `the group rebuilt after ${selector}`, (g, b) =>
      window.__rl_groupState(g).added.length !== b, { timeout: 30000, polling: 100 }, g, before)
    return count(g)
  }
  const place = async (key, k) => {
    const g = await p.evaluate((key, at) => window.__rl_compoundPlace(key, at), key,
      [rift[0] + 1500 + 1500 * k, rift[1] + 1500, rift[2]])
    await p.evaluate((g) => window.__rl_pickGroup(g), g)
    return g
  }
  const stack = await place(PARAM_STACK, 0)
  const s0 = await count(stack)
  const s1 = await setField(stack, '#cpmids', COLUMN_MIDS)
  check(s1 === s0 + COLUMN_MIDS - 2, `${PARAM_STACK}: mid tiles 2 -> ${COLUMN_MIDS} makes ${s0} -> ${s1} pieces`, log)
  const run = await place(PARAM_RUN, 1)
  const r0 = await count(run)
  const r1 = await setField(run, '[data-cparam="count"]', 7)
  //: each section is the run's piece and its cell
  check(r0 % 5 === 0 && r1 === r0 / 5 * 7,
    `${PARAM_RUN}: sections 5 -> 7 makes ${r0} -> ${r1} pieces`, log)
  const saved = await p.evaluate(async (g) => {
    await window.__rl_savePlan()
    const plan = await (await fetch('api/plan/' + encodeURIComponent(window.__rl_levelName()))).json()
    return (plan.added.find(a => a.group === g && a.anchor) || {}).params
  }, run)
  check(saved && saved.count === 7, `saved: ${JSON.stringify(saved)}`, log)
}

//: Wall Run on a placed 4x4 wall: the wall's grid face runs along its local x at y 0
//: (memory omd3-kit-piece-anchoring), so the run is clicked 300 uu up its face.
const RUN_WALL = 'Wall_FB_4x4_C'
const RUN_FROM = [100, 0, 300]
const RUN_TO = [1100, 0, 300]
//: Wall torch defaults (riftlord/compounds.py WALL_RUN) and how far from them a torch may sit, uu.
const TORCH = { spacing: 830, height: 347, offset: 21 }
const RUN_TOLERANCE = 1
//: Beam: WoodBeam7's length at scale 1 (compounds.BEAM_LENGTH) and the span tried, uu.
const BEAM_LENGTH = 599.5
const BEAM_SPAN = 1200
//: Railing Run: the railing pitch (compounds.RAILING_PITCH) and the run tried, uu.
const RAIL_PITCH = 300
const RAIL_SPAN = 1000

/** Decorate: a Wall Run of torches along a wall, flush and facing out at the measured
 * spacing; a Foliage patch adds foliage instances that hide with the Foliage layer. */
async function decorTools (p, urls, mission, log) {
  await openMission(p, urls, mission, log)
  const head = await p.evaluate(() => window.__rl_decorOpen())
  check(head === 'Decorate', `Decorate opens the tool panel (${head})`, log)
  const rift = await p.evaluate(() => window.__rl_riftAt() || [0, 0, 0])
  const n = await p.evaluate(async (name, at) => window.__rl_addKit(name, at), RUN_WALL, [rift[0] + 1500, rift[1] + 1500, rift[2]])
  const m = await p.evaluate((i) => window.__rl_addedMatrix(i), n - 1)
  const world = (q) => [0, 1, 2].map(r => m[r] * q[0] + m[4 + r] * q[1] + m[8 + r] * q[2] + m[12 + r])
  const torches = await p.evaluate((a, b) => window.__rl_decorRun('wallrun', [a, b]), world(RUN_FROM), world(RUN_TO))
  const len = Math.hypot(RUN_TO[0] - RUN_FROM[0], RUN_TO[1] - RUN_FROM[1])
  const want = Math.floor(len / TORCH.spacing) + 1
  check(torches.length === want && torches.every(t => t.name === 'BP_WallTorch_Normal_C'),
    `Wall Run: ${torches.length} torches for a ${len} uu run (want ${want})`, log)
  const wallY = [m[4], m[5]]
  const ok = torches.every(t => {
    const ty = [t.matrix[4], t.matrix[5]]
    const across = Math.abs(ty[0] * wallY[0] + ty[1] * wallY[1]) / Math.hypot(...wallY)
    const lp = [t.matrix[12] - m[12], t.matrix[13] - m[13]]
    const out = Math.abs(lp[0] * wallY[0] + lp[1] * wallY[1]) / Math.hypot(...wallY)
    return Math.abs(across - 1) < 1e-3 && Math.abs(out - TORCH.offset) < RUN_TOLERANCE
  })
  check(ok, `each torch faces out of the wall, ${TORCH.offset} uu off its face`, log)
  if (torches.length > 1) {
    const gap = Math.hypot(torches[1].matrix[12] - torches[0].matrix[12], torches[1].matrix[13] - torches[0].matrix[13])
    check(Math.abs(gap - TORCH.spacing) < RUN_TOLERANCE, `torches ${gap.toFixed(1)} uu apart`, log)
  }
  const grass = await p.evaluate((at) => window.__rl_decorRun('foliage', [at]), [rift[0] + 600, rift[1] - 600, rift[2] + 50])
  check(grass.length > 0 && grass.every(g => g.kind === 'foliage'), `Foliage: a patch of ${grass.length} ${grass[0] && grass[0].name} instances`, log)
  // drawn and hidden on the next frames: waited for, not slept on (it ran late under load)
  await waitFor(p, 'the added foliage drawn', (n) => {
    const v = window.__rl_addedFoliageShown()
    return v.length >= n && v.every(x => x)
  }, { timeout: 15000, polling: 100 }, grass.length).catch(() => {})
  let shown = await p.evaluate(() => window.__rl_addedFoliageShown())
  check(shown.length >= grass.length && shown.every(v => v), `${shown.length} added foliage instances drawn`, log)
  await layer(p, 'foliage', false)
  await waitFor(p, 'the added foliage hidden', () => window.__rl_addedFoliageShown().every(x => !x),
    { timeout: 15000, polling: 100 }).catch(() => {})
  shown = await p.evaluate(() => window.__rl_addedFoliageShown())
  check(shown.every(v => !v), 'they hide with the Foliage layer', log)
  await layer(p, 'foliage', true)
  const c = [rift[0] - 900, rift[1] + 900, rift[2] + 50]
  const rocks = await p.evaluate((at) => window.__rl_decorRun('rocks', [at]), c)
  check(rocks.length === 2 && rocks.every(r => r.kind === 'mesh' && /stone/i.test(r.name)),
    `Rock Scatter: ${rocks.map(r => r.name).join(', ')}`, log)
  const beam = await p.evaluate((a, b) => window.__rl_decorRun('beam', [a, b]),
    [c[0], c[1], c[2] + 400], [c[0] + BEAM_SPAN, c[1], c[2] + 400])
  const sx = beam[0] ? Math.hypot(beam[0].matrix[0], beam[0].matrix[1], beam[0].matrix[2]) : 0
  check(beam.length === 1 && Math.abs(sx * BEAM_LENGTH - BEAM_SPAN) < RUN_TOLERANCE,
    `Beam: ${beam[0] && beam[0].name} stretched ${sx.toFixed(3)} to span ${BEAM_SPAN} uu`, log)
  const rail = await p.evaluate((a, b) => window.__rl_decorRun('railing', [a, b]),
    [c[0], c[1] + 600, c[2]], [c[0] + RAIL_SPAN, c[1] + 600, c[2]])
  check(rail.length === Math.floor(RAIL_SPAN / RAIL_PITCH), `Railing Run: ${rail.length} ${rail[0] && rail[0].name} for ${RAIL_SPAN} uu`, log)
  const hung = await p.evaluate(async (at) => {
    await window.__rl_decorRun('hang', [at])
    return window.__rl_pickedNow().length
  }, [c[0], c[1] - 600, c[2] + 2000])
  check(hung === COMPOUND_MEMBERS.chain, `Hang: a chain of ${hung} pieces picked`, log)
  const barrelChain = await p.evaluate((at) => window.__rl_decorRun('hang', [at], { item: 'boom_barrel' }),
    [c[0] + 600, c[1] - 600, c[2] + 2000])
  const barrel = barrelChain.find(a => a.name === 'BP_FallingBoomBarrel_C')
  const lowest = Math.min(...barrelChain.filter(a => a.name.startsWith('Chain_Short')).map(a => a.matrix[14]))
  check(!!barrel && barrel.matrix[14] < lowest, `Hang with a boom barrel: barrel at z ${barrel && barrel.matrix[14].toFixed(1)} under the chain (${barrelChain.length} pieces)`, log)
  // box select picks a foliage patch whole
  const patchAt = grass[0].matrix.slice(12, 15)
  await lookDownAt(p, patchAt, 3000)
  await p.evaluate(() => window.__rl_pickAdded(-1))
  const fIndex = await p.evaluate((m) => window.__rl_planBits().added.findIndex((a, i) => a.kind === 'foliage'), patchAt)
  const fs = await p.evaluate((i) => window.__rl_addedScreen(i), fIndex)
  await p.mouse.move(fs.x - MARQUEE_HALF, fs.y - MARQUEE_HALF)
  await p.mouse.down()
  await p.mouse.move(fs.x, fs.y, { steps: 4 })
  await p.mouse.move(fs.x + MARQUEE_HALF, fs.y + MARQUEE_HALF, { steps: 4 })
  await p.mouse.up()
  await sleep(300)
  const boxedKinds = await p.evaluate(() => window.__rl_pickedKinds())
  check(boxedKinds.filter(k => k === 'added:foliage').length === grass.length,
    `box select around one grass instance picks the patch (${boxedKinds.filter(k => k === 'added:foliage').length} of ${grass.length})`, log)
  await p.evaluate(() => window.__rl_pickAdded(-1))
  const saved = await p.evaluate(async () => {
    await window.__rl_savePlan()
    const plan = await (await fetch('api/plan/' + encodeURIComponent(window.__rl_levelName()))).json()
    return { foliage: plan.added.filter(a => a.kind === 'foliage').length,
             torches: plan.added.filter(a => a.name === 'BP_WallTorch_Normal_C' && a.group).length }
  })
  check(saved.foliage === grass.length && saved.torches === torches.length,
    `saved: ${saved.foliage} foliage instances and ${saved.torches} grouped torches`, log)
}

// -- the app shell, campaigns (web/shared/CampaignEditor.svelte) -------------------------------

/** The shell page as the app window shows it: a stand-in `window.ipc` records what the
 * page asks of the window, so the titlebar draws. */
async function openShell (p, urls) {
  await p.evaluateOnNewDocument(() => {
    window.__ipc = []
    window.ipc = { postMessage: (m) => window.__ipc.push(JSON.parse(m)) }
  })
  await p.goto(`${urls.shell}/`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await waitFor(p, 'the Missions list', () => document.querySelector('.titlebar') &&
    document.querySelector('button[title^="Mission Settings"]'), { timeout: 120000, polling: 250 })
}

/** Open the first mission's Settings dialog on the Missions page. */
async function openMissionSettings (p) {
  await p.evaluate(() => document.querySelector('button[title^="Mission Settings"]').click())
  await waitFor(p, 'the Mission Settings dialog', () => document.querySelector('.md-modal select[aria-label="Campaign"]'),
    { timeout: 60000, polling: 250 })
}

/** With a modal open, the titlebar is what is under the pointer: pressing it asks the
 * window to drag and the modal stays open. */
/** App shell text and layout: the Map Editor's level row (the picker says "Choose a
 * mission"; Open ends at New Level's right edge, inside the sidebar), the console's Level
 * dropdown (least to most output), and Settings' Supported Mods saying Detected or Not
 * detected. Screenshots into RL_SHOTS as appshell-*.png. */
async function appShell (p, urls, mission, log) {
  const shots = process.env.RL_SHOTS
  await p.setViewport({ width: 1500, height: 920 })
  await p.goto(`${urls.riftlord}/?embedded=1&theme=dark`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await waitFor(p, 'the Map Editor ready', () => window.__rl_ready, { timeout: 120000, polling: 250 })
  const row = await p.evaluate(() => {
    const box = (s) => { const r = document.querySelector(s).getBoundingClientRect(); return { left: r.left, right: r.right } }
    const side = document.querySelector('#side')
    const inner = side.getBoundingClientRect().left + side.clientLeft + side.clientWidth -
      parseFloat(getComputedStyle(side).paddingRight)
    return { placeholder: document.querySelector('.lp-btn .lp-label')?.textContent.trim(),
      open: box('#load'), newLevel: box('#newlevel'), picker: box('.lp-btn'), inner,
      children: [...document.querySelector('#openclose').children].map(c => {
        const r = c.getBoundingClientRect()
        return `${c.tagName.toLowerCase()}${c.id ? '#' + c.id : ''}.${c.className} ${r.left.toFixed(0)}-${r.right.toFixed(0)}`
      }).join(' | ') }
  })
  if (shots) {
    const side = await p.$('#side')
    const r = await side.boundingBox()
    await p.screenshot({ path: `${shots}/appshell-level-row.png`, clip: { x: r.x, y: r.y, width: r.width, height: 200 } })
  }
  check(row.placeholder === 'Choose a mission', `the level picker says "${row.placeholder}"`, log)
  check(Math.abs(row.open.right - row.newLevel.right) <= 1 && row.open.right <= row.inner + 0.5 &&
    Math.abs(row.picker.left - row.newLevel.left) <= 1,
    `Open ends at New Level's right edge inside the sidebar (Open ${row.open.right.toFixed(1)}, ` +
    `New Level ${row.newLevel.right.toFixed(1)}, content edge ${row.inner.toFixed(1)}; ${row.children})`, log)

  await openShell(p, urls)
  await p.evaluate(() => document.querySelector('button.tab[aria-label="Console"]').click())
  await waitFor(p, 'the console', () => document.querySelector('.console select'), { timeout: 10000, polling: 100 })
  const levels = await p.evaluate(() => [...document.querySelectorAll('.console select option')].map(o => o.textContent))
  check(levels.join() === 'ERROR,WARN,INFO,DEBUG', `console levels, least to most output: ${levels.join(', ')}`, log)
  if (shots) await (await p.$('.console header')).screenshot({ path: `${shots}/appshell-console.png` })
  await p.evaluate(() => document.querySelector('.console button[aria-label="Close the console"]').click())

  await p.evaluate(() => document.querySelector('button.tab[aria-label="Settings"]').click())
  await waitFor(p, 'Supported Mods', () => document.querySelectorAll('.mods .mod .status').length > 0,
    { timeout: 30000, polling: 200 })
  const labels = await p.evaluate(() => [...document.querySelectorAll('.mods .mod .status')].map(s => s.textContent.trim()))
  check(labels.every(l => /^(Detected|Not detected|Detected \(incompatible version\))$/.test(l)),
    `Supported Mods say ${labels.join(', ')}`, log)
  if (shots) await p.screenshot({ path: `${shots}/appshell-settings.png` })
}

async function titlebar (p, urls, mission, log) {
  await openShell(p, urls)
  await openMissionSettings(p)
  const at = await p.evaluate(() => {
    const r = document.querySelector('.titlebar .title').getBoundingClientRect()
    const x = r.right + 40
    const y = r.top + r.height / 2
    const hit = document.elementFromPoint(x, y)
    return { x, y, onBar: !!(hit && hit.closest('.titlebar')), hit: hit ? hit.className : '' }
  })
  check(at.onBar, `the titlebar is on top of the modal's scrim (hit "${at.hit}")`, log)
  await p.mouse.move(at.x, at.y)
  await p.mouse.down()
  await p.mouse.move(at.x + 30, at.y + 10, { steps: 3 })
  await p.mouse.up()
  await sleep(300)
  const after = await p.evaluate(() => ({ asked: window.__ipc.map(m => m.cmd), open: !!document.querySelector('.md-modal') }))
  check(after.asked.includes('drag'), `pressing the titlebar asks the window to drag (${after.asked.join(', ')})`, log)
  check(after.open, 'the modal stays open', log)
  const edge = await p.evaluate(() => {
    const hit = document.elementFromPoint(window.innerWidth - 2, 200)
    return hit ? hit.className : ''
  })
  check(/edge/.test(edge), `the resize edge is on top of the scrim too (${edge})`, log)
}

/** Draw a PNG of `w` x `h` in the page and hand it to the file input `selector`. */
async function giveImage (p, selector, w, h) {
  await p.evaluate(async (sel, w, h) => {
    const c = document.createElement('canvas')
    c.width = w
    c.height = h
    const g = c.getContext('2d')
    const grad = g.createLinearGradient(0, 0, w, h)
    grad.addColorStop(0, '#3a2a6a'); grad.addColorStop(1, '#d08a30')
    g.fillStyle = grad
    g.fillRect(0, 0, w, h)
    g.fillStyle = '#fff'
    g.font = `${Math.round(h / 8)}px sans-serif`
    g.fillText('Smoke Banner', w * 0.1, h * 0.5)
    const blob = await new Promise(r => c.toBlob(r, 'image/png'))
    const dt = new DataTransfer()
    dt.items.add(new File([blob], 'banner.png', { type: 'image/png' }))
    const input = document.querySelector(sel)
    input.files = dt.files
    input.dispatchEvent(new Event('change', { bubbles: true }))
  }, selector, w, h)
}

/** The mission only picks its campaign; a campaign's name, description, hub style and
 * banner are edited in its own dialog. A new campaign from the Campaign picker, a banner
 * cropped to the tab's shape, then the same campaign from the Missions page. */
async function campaign (p, urls, mission, log) {
  await openShell(p, urls)
  await openMissionSettings(p)
  const shots = process.env.RL_SHOTS
  const inline = await p.evaluate(() => [...document.querySelectorAll('.md-modal input, .md-modal textarea')]
    .map(i => i.getAttribute('aria-label') || i.placeholder || '').filter(Boolean))
  check(!inline.some(t => /tab image|tab's title/i.test(t)), `no campaign fields in the mission dialog (${inline.join(', ')})`, log)
  await p.evaluate(() => {
    const s = document.querySelector('.md-modal select[aria-label="Campaign"]'); s.value = 'new'
    s.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await waitFor(p, 'the New Campaign dialog', () => document.querySelector('.ce-modal'), { timeout: 10000, polling: 100 })
  const heads = await p.evaluate(() => [...document.querySelectorAll('.ce-modal h3, .ce-modal h4')].map(h => h.textContent.trim()))
  check(heads.includes('New Campaign') && heads.includes('Campaign Banner'), `campaign dialog: ${heads.join(', ')}`, log)
  await p.evaluate(() => {
    const set = (sel, v) => { const i = document.querySelector(sel); i.value = v; i.dispatchEvent(new Event('input', { bubbles: true })) }
    set('.ce-modal input[aria-label="Campaign name"]', 'Smoke Tales')
    set('.ce-modal textarea[aria-label="Campaign description"]', 'Made by the smoke suite.')
    const h = document.querySelector('.ce-modal select[aria-label="Campaign hub style"]'); h.value = 'ColdAsEyes'
    h.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await giveImage(p, '.ce-modal input[type=file]', 900, 600)
  await waitFor(p, 'the banner crop box', () => document.querySelector('.ce-modal canvas.bc-pick')?.width > 0,
    { timeout: 10000, polling: 100 })
  // drag the box's lower-right corner in, then the box down
  const r = await p.evaluate(() => { const b = document.querySelector('.ce-modal canvas.bc-pick').getBoundingClientRect()
    return { x: b.left, y: b.top, w: b.width, h: b.height } })
  const ratio = r.w / 900
  // the starting box is the widest of the tab's shape, centred: 900 x 281.25 at y 159.4
  const corner = { x: r.x + r.w - 1, y: r.y + (159.375 + 281.25) * ratio }
  await p.mouse.move(corner.x, corner.y)
  await p.mouse.down()
  await p.mouse.move(corner.x - r.w * 0.4, corner.y - r.w * 0.4 * 120 / 384, { steps: 5 })
  await p.mouse.up()
  await p.mouse.move(r.x + r.w * 0.2, r.y + r.h * 0.4)
  await p.mouse.down()
  await p.mouse.move(r.x + r.w * 0.25, r.y + r.h * 0.5, { steps: 5 })
  await p.mouse.up()
  await sleep(200)
  // the campaign button: its own box over the same picture, previewed in the Story border
  await waitFor(p, 'the Campaign Button crop box', () =>
    document.querySelector('.ce-modal canvas[aria-label="Campaign Button crop"]')?.width > 0 &&
    document.querySelector('.ce-modal canvas[aria-label="Campaign Button preview"]')?.width > 0,
  { timeout: 20000, polling: 100 })
  const cardHead = await p.evaluate(() => [...document.querySelectorAll('.ce-modal h4')].map(h => h.textContent.trim()))
  check(cardHead.includes('Campaign Button'), `Campaign Button section (${cardHead.join(', ')})`, log)
  const lowRes = await p.evaluate(() => document.querySelector('.ce-modal .bc-low')?.textContent.trim())
  check(lowRes === 'Low resolution', `a 900 x 600 picture is marked "${lowRes}"`, log)
  const previewOf = () => p.evaluate(() => document.querySelector('.ce-modal canvas[aria-label="Campaign Button preview"]').toDataURL())
  const before = await previewOf()
  const cr = await p.evaluate(() => { const c = document.querySelector('.ce-modal canvas[aria-label="Campaign Button crop"]')
    c.scrollIntoView({ block: 'center' })
    const b = c.getBoundingClientRect()
    return { x: b.left, y: b.top, w: b.width, h: b.height } })
  // the box starts as tall as the picture: shrink it from the wheel, then drag it left
  await p.mouse.move(cr.x + cr.w / 2, cr.y + cr.h / 2)
  await p.mouse.wheel({ deltaY: -300 })
  await sleep(100)
  await p.mouse.down()
  await p.mouse.move(cr.x + cr.w * 0.3, cr.y + cr.h * 0.4, { steps: 5 })
  await p.mouse.up()
  await sleep(200)
  const after = await previewOf()
  check(after !== before, 'the Campaign Button preview follows its box', log)
  if (shots) await (await p.$('.ce-modal')).screenshot({ path: `${shots}/campaign-new.png` })
  await p.evaluate(() => document.querySelector('.ce-modal .ce-primary').click())
  await waitFor(p, 'the campaign dialog to close', () => !document.querySelector('.ce-modal'), { timeout: 10000, polling: 100 })
  const label = await p.evaluate(() => { const s = document.querySelector('.md-modal select[aria-label="Campaign"]')
    return s.options[s.selectedIndex].textContent.trim() })
  check(label === 'New Campaign: Smoke Tales', `the picker reads "${label}"`, log)
  if (shots) {
    await p.evaluate(() => { document.querySelector('.md-modal .md-body').scrollTop = 1e6 })
    await sleep(200)
    await (await p.$('.md-modal')).screenshot({ path: `${shots}/mission-details-campaign.png` })
  }
  await p.evaluate(() => document.querySelector('.md-modal .md-primary').click())
  // the dialog closes once the save (campaign, banner and cards) has finished
  await waitFor(p, 'the Mission Settings dialog to close after saving', () => !document.querySelector('.md-modal'),
    { timeout: 180000, polling: 500 })
  await waitFor(p, 'the Smoke Tales campaign on the Missions page', () =>
    [...document.querySelectorAll('.group-head h3')].some(h => h.textContent.trim() === 'Smoke Tales'),
  { timeout: 120000, polling: 500 })
  const made = await p.evaluate(async () => {
    const list = await (await fetch('api/bucket')).json()
    const c = list.campaigns.find(x => x.title === 'Smoke Tales')
    const got = c && await (await fetch('api/bucket/campaign?key=' + encodeURIComponent(c.key))).json()
    const size = got && got.bannerView && await new Promise((resolve) => {
      const im = new Image(); im.onload = () => resolve([im.naturalWidth, im.naturalHeight]); im.src = got.bannerView })
    return got && { key: c.key, description: got.description, hub: got.hub, size, crop: got.bannerSource?.crop }
  })
  check(made && made.description === 'Made by the smoke suite.' && made.hub === 'ColdAsEyes',
    `campaign made with its description and hub (${made && made.hub})`, log)
  check(made && made.size && Math.abs(made.size[0] / made.size[1] - 384 / 120) < 0.02,
    `banner at the tab's shape: ${made && made.size && made.size.join(' x ')} (crop ${made && made.crop && made.crop.map(v => v.toFixed(3)).join(', ')})`, log)
  check(made && made.crop && made.crop[2] < 0.9, 'the crop box was resized before saving', log)
  const files = made && campaignFiles(made.key)
  if (files) {
    const card = files.about.cardCrop
    const shape = card && (card[2] * 900) / (card[3] * 600)
    check(card && card.length === 4 && Math.abs(shape - 1822 / 1825) < 0.01 && card[2] < 0.6,
      `BannerSource.json has the moved cardCrop (${card && card.map(v => v.toFixed(3)).join(', ')})`, log)
    check(files.card && files.card.join('x') === '2048x2048', `Card.png is ${files.card && files.card.join(' x ')}`, log)
  }
  if (shots) await p.screenshot({ path: `${shots}/missions-campaign-heading.png` })
  // the same campaign from its heading on the Missions page
  await p.evaluate(() => document.querySelector('button[aria-label="Campaign Settings for Smoke Tales"]').click())
  await waitFor(p, 'Campaign Settings from the Missions page', () =>
    document.querySelector('.ce-modal input[aria-label="Campaign name"]')?.value === 'Smoke Tales' &&
    document.querySelector('.ce-modal canvas.bc-pick')?.width > 0, { timeout: 20000, polling: 200 })
  if (shots) await (await p.$('.ce-modal')).screenshot({ path: `${shots}/campaign-settings.png` })
  // the same picture with only the button's crop changed
  await waitFor(p, 'the kept Campaign Button crop', () =>
    document.querySelector('.ce-modal canvas[aria-label="Campaign Button crop"]')?.width > 0, { timeout: 20000, polling: 200 })
  const kept = await p.evaluate(() => { const c = document.querySelector('.ce-modal canvas[aria-label="Campaign Button crop"]')
    c.scrollIntoView({ block: 'center' })
    const b = c.getBoundingClientRect()
    return { x: b.left + b.width / 2, y: b.top + b.height / 2 } })
  const was = files && files.about
  const keptView = await previewOf()
  await p.mouse.move(kept.x, kept.y)
  await p.mouse.wheel({ deltaY: 200 })
  await sleep(200)
  check(await previewOf() !== keptView, 'the kept picture\'s button box grows from the wheel', log)
  await p.evaluate(() => {
    const i = document.querySelector('.ce-modal input[aria-label="Campaign name"]'); i.value = 'Smoke Sagas'
    i.dispatchEvent(new Event('input', { bubbles: true }))
    document.querySelector('.ce-modal .ce-primary').click()
  })
  // the dialog closes once the save (name, crops and cards) has finished
  await waitFor(p, 'Campaign Settings to close after saving', () => !document.querySelector('.ce-modal'),
    { timeout: 180000, polling: 500 })
  await waitFor(p, 'the campaign renamed on the Missions page', () =>
    [...document.querySelectorAll('.group-head h3')].some(h => h.textContent.trim() === 'Smoke Sagas'),
  { timeout: 60000, polling: 500 })
  check(true, 'renamed from the Missions page to Smoke Sagas', log)
  const recut = was && campaignFiles(made.key)
  if (recut) {
    check(JSON.stringify(recut.about.crop) === JSON.stringify(was.crop) &&
      recut.about.cardCrop[2] > was.cardCrop[2] && recut.cardTime > files.cardTime,
    `button crop changed without a new picture (${was.cardCrop[2].toFixed(3)} -> ${recut.about.cardCrop[2].toFixed(3)} wide; tab crop kept)`, log)
  }
}

/** A loose campaign's banner files in the test server's Missions (RL_MISSIONS), by its
 * key `<pak>:<row>`: {about (BannerSource.json), card ([w, h] of Card.png), cardTime}, or
 * null when the suite runs without RL_MISSIONS. */
function campaignFiles (key) {
  const root = process.env.RL_MISSIONS
  if (!root) return null
  const stem = key.slice(0, key.lastIndexOf(':')).replace(/\.pak$/, '')
  const at = (suffix) => require('path').join(root, stem + suffix)
  let about = {}
  try { about = JSON.parse(fs.readFileSync(at('.BannerSource.json'), 'utf8')) } catch (e) {}
  let card = null
  let cardTime = 0
  try {
    const png = fs.readFileSync(at('.Card.png'))
    card = [png.readUInt32BE(16), png.readUInt32BE(20)]
    cardTime = fs.statSync(at('.Card.png')).mtimeMs
  } catch (e) {}
  return { about, card, cardTime }
}

/** Map Settings' Intro and Story Dialogue are switches that set and clear the plan's
 * world.intro / world.storyDialog. */
async function maptoggles (p, urls, mission, log) {
  await openLevelNamed(p, urls, 'Scenario_01', log)
  await waitFor(p, 'the Intro switch', () => document.querySelector('#introcard'), { timeout: 120000, polling: 500 })
  const kinds = await p.evaluate(() => ['introcard', 'storydialog'].map(id => {
    const e = document.querySelector('#' + id)
    return e ? `${id}:${e.tagName.toLowerCase()}/${e.type}/${e.getAttribute('role')}` : `${id}:none`
  }))
  check(kinds.every(k => /input\/checkbox\/switch/.test(k)), `switches: ${kinds.join(', ')}`, log)
  for (const [id, key] of [['introcard', 'intro'], ['storydialog', 'storyDialog']]) {
    const was = await p.evaluate((id) => document.querySelector('#' + id)?.checked, id)
    if (was === undefined) continue
    await p.evaluate((id) => document.querySelector('#' + id).click(), id)
    await sleep(300)
    const flipped = await p.evaluate((key) => window.__rl_planWorld()[key], key)
    await p.evaluate((id) => document.querySelector('#' + id).click(), id)
    await sleep(300)
    const back = await p.evaluate((key) => window.__rl_planWorld()[key], key)
    check((was ? flipped === 'remove' : flipped === undefined) && (was ? back === undefined : back === 'remove'),
      `${id}: ${was ? 'On' : 'Off'} -> ${flipped ?? 'on'} -> ${back ?? 'on'}`, log)
  }
  if (process.env.RL_SHOTS) {
    const box = await p.$('#introcard')
    const panel = box && await box.evaluateHandle((e) => e.closest('#props') || e.parentElement.parentElement)
    if (panel) await panel.asElement().screenshot({ path: `${process.env.RL_SHOTS}/mapsettings-toggles.png` })
  }
}

/** Map Settings' Minimap section: Regenerate shows a Cut at Doors switch (off by default:
 * all reachable floor) that sets and clears the plan's minimap.cutAtDoors; Source Level
 * and Cut show none. */
async function minimapdoors (p, urls, mission, log) {
  await openLevelNamed(p, urls, 'Scenario_01', log)
  await waitFor(p, 'the Minimap picker', () => document.querySelector('#mmmode'), { timeout: 120000, polling: 500 })
  const pick = (value) => p.evaluate((value) => {
    const s = document.querySelector('#mmmode'); s.value = value; s.dispatchEvent(new Event('change'))
  }, value)
  await pick('cut')
  await sleep(300)
  check(await p.evaluate(() => !document.querySelector('#mmcutdoors')), 'Cut shows no Cut at Doors switch', log)
  await pick('regenerate')
  await waitFor(p, 'the Cut at Doors switch', () => document.querySelector('#mmcutdoors'), { timeout: 30000, polling: 250 })
  const sw = await p.evaluate(() => {
    const e = document.querySelector('#mmcutdoors')
    return { kind: `${e.tagName.toLowerCase()}/${e.type}/${e.getAttribute('role')}`, on: e.checked,
      label: (e.closest('label')?.textContent || '').trim(), block: window.__rl_planMinimap() }
  })
  check(sw.kind === 'input/checkbox/switch' && sw.label === 'Cut at Doors',
    `switch: ${sw.kind} "${sw.label}"`, log)
  check(!sw.on && !sw.block.cutAtDoors && sw.block.mode === 'regenerate',
    `off by default: ${JSON.stringify(sw.block)}`, log)
  await p.evaluate(() => document.querySelector('#mmcutdoors').click())
  await sleep(300)
  const on = await p.evaluate(() => window.__rl_planMinimap())
  await waitFor(p, 'the Cut at Doors switch again', () => document.querySelector('#mmcutdoors'), { timeout: 30000, polling: 250 })
  const shown = await p.evaluate(() => document.querySelector('#mmcutdoors').checked)
  await p.evaluate(() => document.querySelector('#mmcutdoors').click())
  await sleep(300)
  const off = await p.evaluate(() => window.__rl_planMinimap())
  check(on.cutAtDoors === true && shown && off.mode === 'regenerate' && !('cutAtDoors' in off),
    `on -> ${JSON.stringify(on)} (shown ${shown}) -> ${JSON.stringify(off)}`, log)
  await pick('source')
  await sleep(300)
  check(await p.evaluate(() => !document.querySelector('#mmcutdoors')), 'Source Level shows no Cut at Doors switch', log)
}

//: The cutaway's first step below off may hide at most this share of the drawn pieces:
//: the topmost roofs, not the level.
const CUT_FIRST_STEP_SHARE = 0.05

/** The cutaway is one slider spanning the drawn play area: no mode picker or floor
 * buttons; the first step below off cuts only the top; a stray far piece does not move
 * the top of the range. */
async function cutaway (p, urls, mission, log) {
  await openMission(p, urls, mission, log)
  const extra = await p.evaluate(() => ['cutmode', 'cutup', 'cutdown'].filter(id => document.querySelector('#' + id)))
  check(extra.length === 0, `no cutaway mode or floor buttons (${extra.join(', ') || 'none'})`, log)
  const probe = (step) => p.evaluate((step) => {
    const s = document.querySelector('#cutaway')
    s.dispatchEvent(new Event('pointerdown'))
    s.value = String(step)
    s.dispatchEvent(new Event('input'))
    const c = window.__rl_cut()
    return { at: c.at, range: c.range }
  }, step)
  const top = await probe(999)
  const shares = await p.evaluate(() => window.__rl_cutShares())
  check(top.range && top.at > top.range[0] && top.at <= top.range[1],
    `range ${top.range.map(Math.round).join(' to ')}; first step cuts at ${Math.round(top.at)}`, log)
  check(shares.above999 <= CUT_FIRST_STEP_SHARE,
    `first step below off cuts into ${(shares.above999 * 100).toFixed(1)}% of ${shares.pieces} pieces`, log)
  check(shares.above500 > shares.above999, `half way cuts into ${(shares.above500 * 100).toFixed(1)}%`, log)
  if (process.env.RL_SHOTS) {
    await probe(940)
    await sleep(500)
    await p.screenshot({ path: `${process.env.RL_SHOTS}/cutaway-940.png` })
  }
  // a piece far outside the play area and far above it
  await p.evaluate(async () => {
    const at = window.__rl_riftAt() || [0, 0, 0]
    await window.__rl_addMesh('ColumnBlock_1_1', [at[0] + 400000, at[1], at[2] + 30000])
  })
  const after = await probe(999)
  check(Math.abs(after.range[1] - top.range[1]) < 1, `a stray far piece leaves the top at ${Math.round(after.range[1])}`, log)
  await probe(1000)
}

/** A piece added from New Object draws lit from the first frame: no material waits on a
 * texture with black, and textures arrive. */
async function addlook (p, urls, mission, log) {
  await openMission(p, urls, mission, log)
  const rows = await p.evaluate(async () => (await (await fetch('api/catalogue')).json())
    .filter(r => r.kind === 'mesh' || r.kind === 'kit').map(r => ({ name: r.name, kind: r.kind })))
  const pick = []
  for (let i = 0; i < 12; i++) pick.push(rows[Math.floor((i + 0.5) * rows.length / 12)])
  let mats = 0
  let black = 0
  const idx = []
  for (const r of pick) {
    const got = await p.evaluate(async (r) => {
      const at = window.__rl_riftAt() || [0, 0, 0]
      const n = r.kind === 'kit' ? await window.__rl_addKit(r.name, [at[0] + 900, at[1], at[2]])
        : await window.__rl_addMesh(r.name, [at[0] + 900, at[1], at[2]])
      return { n, look: window.__rl_addedLook(n - 1) }
    }, r)
    idx.push(got.n - 1)
    for (const m of got.look) {
      for (const x of m.mats) {
        mats += 1
        if ((x.map && !x.loaded) || x.color === '000000') black += 1
      }
    }
  }
  check(mats > 0 && black === 0, `${mats} materials of ${pick.length} added pieces; ${black} drawn black on arrival`, log)
  await sleep(4000)
  const later = await p.evaluate((idx) => idx.flatMap(i => window.__rl_addedLook(i)).flatMap(m => m.mats), idx)
  const textured = later.filter(x => x.map && x.loaded).length
  check(textured > later.length / 2, `${textured} of ${later.length} materials textured after 4 s`, log)
}

/** The Scramble tier rows of the open dialog: [{name (its input), difficulty, weight,
 * enabled}]. */
const scrambleRows = (p) => p.evaluate(() => [...document.querySelectorAll('.md-modal .md-tier:not(.md-tier-head)')]
  .map(r => ({ name: r.querySelector('input[aria-label="Tier"]')?.value,
    difficulty: r.querySelector('select')?.value, weight: r.querySelector('input[type=number]')?.value,
    enabled: !r.querySelector('select')?.disabled })))

/** Type `text` into Scramble tier row `i` and commit it (a change event). */
const typeTier = (p, i, text) => p.evaluate((i, text) => {
  const input = document.querySelectorAll('.md-modal .md-tier input[aria-label="Tier"]')[i]
  input.value = text
  input.dispatchEvent(new Event('input', { bubbles: true }))
  input.dispatchEvent(new Event('change', { bubbles: true }))
}, i, text)

/** Scramble in Mission Settings (missionscramble.py): added with no tier joined and no All
 * choice, the known tiers suggested, a suggested tier and one typed by number (Tier_30)
 * joined, each at its own difficulty and weight, saved, shown again, a row removed, the
 * setting written into the build, then turned off. */
async function scramble (p, urls, mission, log) {
  await openShell(p, urls)
  await openMissionSettings(p)
  const shots = process.env.RL_SHOTS
  const offeredOff = await p.evaluate(() => {
    const l = [...document.querySelectorAll('.md-modal label.check')].find(x => x.textContent.trim() === 'Add to Scramble')
    return l ? !l.querySelector('input[type=checkbox]').checked : null
  })
  check(offeredOff === true, 'Add to Scramble is offered, off', log)
  // Endless is a difficulty: a mission with Add to Endless on still joins Scramble
  const endlessOn = await p.evaluate(() => {
    const l = [...document.querySelectorAll('.md-modal label.check')].find(x => x.textContent.trim() === 'Add to Endless')
    return l ? l.querySelector('input[type=checkbox]').checked : null
  })
  check(endlessOn !== null && offeredOff !== null,
    `Add to Endless is ${endlessOn ? 'on' : 'off'} and Add to Scramble is offered with it`, log)
  await p.evaluate(() => {
    const l = [...document.querySelectorAll('.md-modal label.check')].find(x => x.textContent.trim() === 'Add to Scramble')
    l.querySelector('input[type=checkbox]').click()
  })
  await waitFor(p, 'a Scramble tier row', () => document.querySelector('.md-modal .md-tier input[aria-label="Tier"]'),
    { timeout: 10000, polling: 100 })
  const fresh = await scrambleRows(p)
  const offer = await p.evaluate(() => ({
    picker: !!document.querySelector('.md-modal select[aria-label="Scramble Tier"]'),
    suggested: [...document.querySelectorAll('#md-scramble-tiers option')].map(o => o.value),
    all: [...document.querySelectorAll('.md-modal option')].some(o => o.value === 'All') }))
  check(fresh.length === 1 && fresh[0].name === '' && !fresh[0].enabled && !offer.picker,
    `added with one empty tier row, no tier joined: ${JSON.stringify(fresh)}`, log)
  check(offer.suggested.length > 0 && !offer.all, `tiers suggested, no All: ${offer.suggested.join(', ')}`, log)
  const tier = offer.suggested[0]
  await typeTier(p, 0, tier)
  await waitFor(p, `the ${tier} row`, (tier) => {
    const s = document.querySelector(`.md-modal select[aria-label="${tier} Difficulty"]`)
    return s && !s.disabled
  }, { timeout: 10000, polling: 100 }, tier)
  await p.evaluate((tier) => {
    const d = document.querySelector(`.md-modal select[aria-label="${tier} Difficulty"]`); d.value = 'Apprentice'
    d.dispatchEvent(new Event('change', { bubbles: true }))
    const w = document.querySelector(`.md-modal input[aria-label="${tier} Weight"]`); w.value = '500'
    w.dispatchEvent(new Event('change', { bubbles: true }))
  }, tier)
  await p.evaluate(() => document.querySelector('.md-modal button[aria-label="Add Tier"]').click())
  await waitFor(p, 'a second tier row', () => document.querySelectorAll('.md-modal .md-tier input[aria-label="Tier"]').length === 2,
    { timeout: 10000, polling: 100 })
  await typeTier(p, 1, '30')
  await waitFor(p, 'the Tier_30 row', () => document.querySelector('.md-modal .md-tier[data-tier="Tier_30"]'),
    { timeout: 10000, polling: 100 })
  const two = await scrambleRows(p)
  check(two.length === 2 && two[0].name === tier && two[0].difficulty === 'Apprentice' &&
    two[0].weight === '500' && two[1].name === 'Tier_30' && two[1].enabled,
  `two tiers, a number typed as Tier_30: ${JSON.stringify(two)}`, log)
  if (shots) {
    await p.evaluate(() => { document.querySelector('.md-modal .md-body').scrollTop = 1e6 })
    await sleep(200)
    await (await p.$('.md-modal')).screenshot({ path: `${shots}/mission-details-scramble.png` })
  }
  await p.evaluate(() => document.querySelector('.md-modal .md-primary').click())
  await waitFor(p, 'the Mission Settings dialog to close after saving', () => !document.querySelector('.md-modal'),
    { timeout: 180000, polling: 500 })
  const read = () => p.evaluate(async (id) => (await (await fetch('api/bucket/settings?id=' + encodeURIComponent(id))).json()).scramble, mission)
  const saved = await read()
  check(saved?.on === true && JSON.stringify(saved.tiers) === JSON.stringify([tier, 'Tier_30']) &&
    Object.keys(saved.rows).join() === tier && saved.rows[tier].difficulty === 'Apprentice' &&
    saved.rows[tier].weight === 500 && saved.tier === undefined,
  `saved ${JSON.stringify(saved)}`, log)
  await openMissionSettings(p)
  const shown = await scrambleRows(p)
  check(JSON.stringify(shown) === JSON.stringify(two), `shown again: ${JSON.stringify(shown)}`, log)
  await p.evaluate(() => document.querySelector('.md-modal button[aria-label="Remove Tier_30"]').click())
  await waitFor(p, 'one tier row', () => document.querySelectorAll('.md-modal .md-tier input[aria-label="Tier"]').length === 1,
    { timeout: 10000, polling: 100 })
  const left = await scrambleRows(p)
  check(left.length === 1 && left[0].name === tier, `a row removed: ${JSON.stringify(left)}`, log)
  await p.evaluate(() => document.querySelector('.md-modal .md-ghost[aria-label="Close"]').click())
  // the same setting again by the API: a current build is written in place
  const again = await p.evaluate(async (id, tier) => (await (await fetch('api/bucket/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Workshop': '1' },
    body: JSON.stringify({ id, scramble: { on: true, tiers: [tier], rows: { [tier]: { weight: 750 } } } }) })).json()), mission, tier)
  check(!again.error, `saved by the API ${again.error || ''}`, log)
  //: a mission with no build yet (release_check copies it unbuilt) has nothing to rewrite
  if (again.scrambleReport === undefined) {
    check(true, 'no build to rewrite in place (mission not built)', log)
  } else {
    check(again.scrambleReport.some(l => l.includes(tier) && l.includes('750')),
      `build written in place: ${JSON.stringify(again.scrambleReport)}`, log)
  }
  const off = await p.evaluate(async (id) => (await (await fetch('api/bucket/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Workshop': '1' },
    body: JSON.stringify({ id, scramble: { on: false } }) })).json()), mission)
  check(!off.error && (await read()).on === false, 'turned off', log)
}

/** Scramble tiers a mod adds (missionscramble.tiers): with the server's Paks folder pointed at
 * a scratch folder (RIFTLORD_SUPPORTED_PAKS) the case writes a stand-in tier mod there
 * (tests/ui_smoke/faketiers.py), else it reads this machine's mods. A mod tier is suggested
 * with its mod in the suggestion text; a tier no mod defines, typed by number, is joined
 * with no label. The dialog is closed without saving. A record saved with All (by the API)
 * is shown as a row per suggested tier and stays All while unedited; then turned off. */
async function scrambletiers (p, urls, mission, log) {
  const state = await getJson(`${urls.shell}/api/supportedmods`)
  if (state.override && state.root) {
    const path = require('path')
    require('child_process').execFileSync('nice', ['-n', '10', 'python3', '-B',
      path.join(__dirname, 'faketiers.py'), state.root], { stdio: 'pipe' })
  }
  await openShell(p, urls)
  const view = await p.evaluate(async (id) => (await (await fetch('api/bucket/settings?id=' +
    encodeURIComponent(id))).json()).scrambleTiers, mission)
  const modTiers = (view?.tiers ?? []).filter(t => t.mod)
  if (state.override) {
    check(modTiers.some(t => t.name === 'Tier_21' && t.mod === 'TierMod' && t.live),
      `the stand-in's Tier_21 is offered from TierMod: ${JSON.stringify(modTiers)}`, log)
  }
  await openMissionSettings(p)
  await p.evaluate(() => {
    const l = [...document.querySelectorAll('.md-modal label.check')].find(x => x.textContent.trim() === 'Add to Scramble')
    const box = l.querySelector('input[type=checkbox]')
    if (!box.checked) box.click()
  })
  await waitFor(p, 'a Scramble tier row', () => document.querySelector('.md-modal .md-tier input[aria-label="Tier"]'),
    { timeout: 10000, polling: 100 })
  const options = await p.evaluate(() => [...document.querySelectorAll('#md-scramble-tiers option')]
    .map(o => ({ value: o.value, text: o.textContent.trim() })))
  if (modTiers.length) {
    const t = modTiers[0]
    const shown = options.find(o => o.value === t.name)
    check(shown && shown.text === `${t.name} (${t.mod})`, `${t.name} is suggested with its mod: ${shown?.text}`, log)
  } else {
    check(true, 'no installed mod adds a tier here', log)
  }
  await typeTier(p, 0, '30')
  await waitFor(p, 'the Tier_30 row', () => document.querySelector('.md-modal .md-tier[data-tier="Tier_30"]'),
    { timeout: 10000, polling: 100 })
  const added = await p.evaluate(() => {
    const row = document.querySelector('.md-modal .md-tier[data-tier="Tier_30"]')
    return { name: row.querySelector('input[aria-label="Tier"]').value,
      remove: !!row.querySelector('button[aria-label="Remove Tier_30"]'),
      text: row.textContent.trim(), waiting: document.querySelector('.md-modal').textContent.includes('waiting') }
  })
  check(added.name === 'Tier_30' && added.remove && !added.text.includes('(') && !added.waiting,
    `a tier no mod defines, typed by number: ${JSON.stringify(added)}`, log)
  const shots = process.env.RL_SHOTS
  if (shots) {
    await p.evaluate(() => { document.querySelector('.md-modal .md-body').scrollTop = 1e6 })
    await sleep(200)
    await (await p.$('.md-modal')).screenshot({ path: `${shots}/mission-details-scramble-tiers.png` })
  }
  await p.evaluate(() => document.querySelector('.md-modal .md-ghost[aria-label="Close"]').click())
  // a record saved with All before tiers were typed: every suggested tier, kept as All
  const post = (scramble) => p.evaluate(async (id, scramble) => (await (await fetch('api/bucket/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Workshop': '1' },
    body: JSON.stringify({ id, scramble }) })).json()), mission, scramble)
  const all = await post({ on: true, tier: 'All' })
  check(!all.error && all.scramble?.tier === 'All', `a record saved with All: ${JSON.stringify(all.scramble)}`, log)
  await openMissionSettings(p)
  const rows = await scrambleRows(p)
  check(rows.length === options.length && rows.every((r, i) => r.name === options[i].value),
    `a record saved with All shows every suggested tier (${rows.length} of ${options.length})`, log)
  await p.evaluate(() => document.querySelector('.md-modal .md-primary').click())
  await waitFor(p, 'the Mission Settings dialog to close after saving', () => !document.querySelector('.md-modal'),
    { timeout: 180000, polling: 500 })
  const kept = await p.evaluate(async (id) => (await (await fetch('api/bucket/settings?id=' + encodeURIComponent(id))).json()).scramble, mission)
  check(kept?.tier === 'All', `saved unedited, it stays All: ${JSON.stringify(kept)}`, log)
  const off = await post({ on: false })
  check(!off.error, 'turned off', log)
}

/** The Unlocks pickers' state in the open dialog: {shown, groups (Items picker headings),
 * heroes / items: [{name, label}] listed, chosen: {heroes, items: [guid]}}. */
const unlockState = (p) => p.evaluate(() => {
  const side = (key) => document.querySelector(`.md-modal .md-unlocks[data-unlocks="${key}"]`)
  const listed = (key) => [...(side(key)?.querySelectorAll('label.md-option') ?? [])]
    .map(l => ({ name: l.querySelector('span').textContent.trim(), label: l.querySelector('small').textContent.trim() }))
  const chips = (key) => [...(side(key)?.querySelectorAll('.md-chip') ?? [])].map(c => c.dataset.guid)
  return { shown: [...document.querySelectorAll('.md-modal h4')].some(h => h.textContent.trim() === 'Unlocks'),
    groups: [...(side('items')?.querySelectorAll('.md-group') ?? [])].map(g => g.textContent.trim()),
    heroes: listed('heroes'), items: listed('items'), chosen: { heroes: chips('heroes'), items: chips('items') } }
})

/** Search one Unlocks picker. */
const searchUnlocks = (p, key, text) => p.evaluate((key, text) => {
  const input = document.querySelector(`.md-modal .md-unlocks[data-unlocks="${key}"] input[type=search]`)
  input.value = text
  input.dispatchEvent(new Event('input', { bubbles: true }))
}, key, text)

/** Hero and item unlocks in Mission Settings (missionunlocks.py): with the server's Paks folder
 * pointed at a scratch folder the case writes a stand-in item mod there
 * (tests/ui_smoke/fakeunlocks.py). The choices hold shipped heroes and items, no
 * entitlement-gated or DLC one, and the stand-in's free trap with its mod; the Items picker
 * is grouped by type and searchable; Campaign Endless hides the section. A hero and an item
 * are chosen, saved, shown again, written into a current build in place, then cleared. */
async function unlocks (p, urls, mission, log) {
  const state = await getJson(`${urls.shell}/api/supportedmods`)
  if (state.override && state.root) {
    const path = require('path')
    require('child_process').execFileSync('nice', ['-n', '10', 'python3', '-B',
      path.join(__dirname, 'fakeunlocks.py'), state.root], { stdio: 'pipe' })
  }
  await openShell(p, urls)
  const read = () => p.evaluate(async (id) => await (await fetch('api/bucket/settings?id=' +
    encodeURIComponent(id))).json(), mission)
  const view = (await read()).unlockChoices
  const names = (side) => (view?.[side] ?? []).map(u => u.name)
  check(['Vorwick', 'Gabriella', 'Egan'].every(n => names('heroes').includes(n)),
    `shipped heroes offered: ${names('heroes').join(', ')}`, log)
  check(['Haymaker', 'Swinging Mace', 'Healing Trinket', 'Crossbow'].every(n => names('items').includes(n)),
    'shipped traps, weapons and trinkets offered', log)
  const gated = ['Molten Gold', 'Saw Blade Launcher', 'Overlord Bladestaff', 'Rift Lord Unlocked',
    'Lava Cauldron', 'Stand-in Entitled Trap', 'Stand-in Listed Trap'].filter(n => names('items').includes(n))
  check(!gated.length, `no gated, DLC or internal item offered ${gated.join(', ')}`, log)
  if (state.override) {
    const mod = (view?.items ?? []).find(u => u.name === 'Stand-in Open Trap')
    check(mod && mod.label === 'Unlock Mod', `the stand-in's free trap is offered with its mod: ${JSON.stringify(mod)}`, log)
  }
  await openMissionSettings(p)
  const first = await unlockState(p)
  check(first.shown && JSON.stringify(first.groups) === JSON.stringify(['Traps', 'Weapons', 'Trinkets']) &&
    !first.chosen.heroes.length && !first.chosen.items.length,
  `Unlocks shown, Items grouped by type, nothing chosen: ${JSON.stringify(first.groups)}`, log)
  const endless = await p.evaluate(() => [...document.querySelectorAll('.md-modal select[aria-label="Campaign"] option')].map(o => o.value))
  if (endless.includes('Endless')) {
    const was = await p.evaluate(() => document.querySelector('.md-modal select[aria-label="Campaign"]').value)
    const pickCampaign = (value) => p.evaluate((value) => {
      const s = document.querySelector('.md-modal select[aria-label="Campaign"]'); s.value = value
      s.dispatchEvent(new Event('change', { bubbles: true }))
    }, value)
    await pickCampaign('Endless')
    await sleep(200)
    check(!(await unlockState(p)).shown, 'Campaign Endless hides Unlocks', log)
    await pickCampaign(was)
    await waitFor(p, 'Unlocks again', () => document.querySelector('.md-modal .md-unlocks'), { timeout: 10000, polling: 100 })
  }
  await searchUnlocks(p, 'items', 'haym')
  await sleep(200)
  const found = await unlockState(p)
  check(found.items.length === 1 && found.items[0].name === 'Haymaker' && found.items[0].label === 'Base game',
    `searched: ${JSON.stringify(found.items)}`, log)
  await p.evaluate(() => document.querySelector('.md-modal .md-unlocks[data-unlocks="items"] label.md-option input').click())
  await searchUnlocks(p, 'heroes', 'vorw')
  await sleep(200)
  await p.evaluate(() => document.querySelector('.md-modal .md-unlocks[data-unlocks="heroes"] label.md-option input').click())
  await searchUnlocks(p, 'items', '')
  await searchUnlocks(p, 'heroes', '')
  const guid = (side, name) => view[side].find(u => u.name === name).guid
  const chosen = await unlockState(p)
  check(JSON.stringify(chosen.chosen) === JSON.stringify({ heroes: [guid('heroes', 'Vorwick')], items: [guid('items', 'Haymaker')] }),
    `chosen: ${JSON.stringify(chosen.chosen)}`, log)
  const shots = process.env.RL_SHOTS
  if (shots) {
    await p.evaluate(() => { document.querySelector('.md-modal .md-unlocks').scrollIntoView() })
    await sleep(200)
    await (await p.$('.md-modal')).screenshot({ path: `${shots}/mission-details-unlocks.png` })
  }
  await p.evaluate(() => document.querySelector('.md-modal .md-primary').click())
  await waitFor(p, 'the Mission Settings dialog to close after saving', () => !document.querySelector('.md-modal'),
    { timeout: 180000, polling: 500 })
  const saved = (await read()).unlocks
  check(saved?.heroes?.map(u => u.name).join() === 'Vorwick' && saved?.items?.map(u => u.name).join() === 'Haymaker' &&
    saved.items[0].installed, `saved ${JSON.stringify(saved)}`, log)
  await openMissionSettings(p)
  const again = await unlockState(p)
  check(JSON.stringify(again.chosen) === JSON.stringify(chosen.chosen), `shown again: ${JSON.stringify(again.chosen)}`, log)
  await p.evaluate(() => document.querySelector('.md-modal .md-chip button[aria-label="Remove Vorwick"]').click())
  await sleep(200)
  check((await unlockState(p)).chosen.heroes.length === 0, 'a chosen hero removed', log)
  await p.evaluate(() => document.querySelector('.md-modal .md-ghost[aria-label="Close"]').click())
  const post = (body) => p.evaluate(async (id, body) => (await (await fetch('api/bucket/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Workshop': '1' },
    body: JSON.stringify({ id, ...body }) })).json()), mission, body)
  const refused = await post({ unlocks: { heroes: [], items: [view.items[0].guid, 'f'.repeat(32)] } })
  check(!refused.error, `an unknown Guid is dropped ${refused.error || ''}`, log)
  //: a mission with no build yet (release_check copies it unbuilt) has nothing to rewrite
  const written = await post({ unlocks: { heroes: [guid('heroes', 'Vorwick')], items: [guid('items', 'Haymaker')] } })
  check(!written.error, `saved by the API ${written.error || ''}`, log)
  if (written.unlocksReport !== undefined) {
    check(written.unlocksReport.some(l => l.includes('Haymaker') && l.includes('Vorwick')),
      `build written in place: ${JSON.stringify(written.unlocksReport)}`, log)
  }
  const cleared = await post({ unlocks: { heroes: [], items: [] } })
  const after = (await read()).unlocks
  check(!cleared.error && !after.heroes.length && !after.items.length, 'cleared', log)
}

// -- the Fab Kits tab (web/shell/src/lib/FabKits.svelte, riftlord/app/fabkits.py) -------------

//: The stand-in converter's vault root (tests/ui_smoke/fakefab.py ROOTS).
const FAKE_ROOT = '/fake/EpicVault'
const FAB = { chest: 'Stylized0bce0f98767dV3', castle: 'DesertCa3432654395afV1', brass: 'CityofBra13001ef61fcV1',
  ninja: 'FluidNin01097781a25fV10', ship: 'Spaceshi4ce763912c34V1', voice: 'AmberNPC5ee3071625f9V1',
  shark: 'BullShar77aa01c2d3e4V1' }
//: The kinds of vault pack listed (riftlord/app/fabkits.py PIECE_KINDS).
const FAB_PIECE_KINDS = ['building', 'props', 'mixed']
const FAB_UNAVAILABLE = 'Fab conversion needs Unreal Engine 4.27 and a C++ compiler'

/** The shell with the window stand-in, on the Fab Kits tab. */
async function openFabTab (p, urls) {
  await p.goto(`${urls.shell}/`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await waitFor(p, 'the Fab Kits rail button', () => document.querySelector('.titlebar') &&
    document.querySelector('button.tab[aria-label="Fab Kits"]:not(:disabled)'), { timeout: 120000, polling: 250 })
  await p.evaluate(() => document.querySelector('button.tab[aria-label="Fab Kits"]').click())
  await waitFor(p, 'the Fab Kits list, its empty state or the vault scan', () => {
    const pane = [...document.querySelectorAll('.fab')].find(s => s.offsetParent)
    return pane && (pane.querySelector('.pack') || pane.querySelector('.empty') || pane.querySelector('.scan'))
  }, { timeout: 60000, polling: 100 })
}

/** The Fab Kits list or its empty state, once no vault scan runs. */
async function fabSettled (p) {
  await waitFor(p, 'the Fab Kits list after the vault scan', () => !document.querySelector('.fab .scan') &&
    (document.querySelector('.fab .pack') || document.querySelector('.fab .empty')), { timeout: 180000, polling: 200 })
}

/** The vault listing once the server has finished scanning. */
async function fabVaultDone (urls) {
  for (let i = 0; i < 600; i++) {
    const got = await getJson(`${urls.shell}/api/fab/vault`)
    if (!got.scanning) return got
    await sleep(300)
  }
  throw new Error('the vault scan did not finish')
}

//: What the vault scan shows while it runs.
const SCAN_TEXT = /^Reading vault( \(\d+\/\d+\))?$/

/** The vault scan as the page shows it, then a reload during it: {seen, text, before,
 *  after, again} (scan pids before and after the reload, the text after it). */
async function fabScan (p, urls, shot) {
  const label = () => p.evaluate(() => document.querySelector('.fab .scan .label')?.textContent.trim() || null)
  let text = await label()
  if (!text) return { seen: false }
  for (let i = 0; i < 50 && !/\(\d+\/\d+\)/.test(text || ''); i++) { await sleep(100); text = await label() || text }
  const bar = await p.evaluate(() => !!document.querySelector('.fab .scan .bar span'))
  await shot('scan')
  const before = (await getJson(`${urls.shell}/api/fab/state`)).scan
  if (!before) return { seen: true, text, bar }
  await p.reload({ waitUntil: 'domcontentloaded' })
  await openFabTab(p, urls)
  const after = (await getJson(`${urls.shell}/api/fab/state`)).scan
  const again = await label()
  let advanced = null
  for (let i = 0; i < 100 && !advanced; i++) {
    const t = await label()
    if (/\([1-9]\d*\/\d+\)/.test(t || '')) advanced = t
    else await sleep(100)
  }
  return { seen: true, text, bar, before: before.pid, after: after?.pid, again, advanced }
}

/** What one vault row shows. */
function fabRow (p, item) {
  return p.evaluate((item) => {
    const r = document.querySelector(`.pack[data-item="${item}"]`)
    if (!r) return null
    return { badge: r.querySelector('.state')?.textContent.trim() || null,
      buttons: [...r.querySelectorAll('.tools button:not(.gone)')].map(b => b.textContent.trim() || b.getAttribute('aria-label')),
      input: !!r.querySelector('input'), off: r.classList.contains('off'), scale: r.querySelector('input')?.value,
      stage: r.querySelector('.progress .stage')?.textContent.trim() || null,
      pct: r.querySelector('.progress .pct')?.textContent.trim() || null,
      problems: [...r.querySelectorAll('.problems li')].map(l => l.textContent),
      problemsLabel: r.querySelector('.problems summary')?.textContent.trim() || null,
      evidence: [...r.querySelectorAll('.evidence li')].map(l => l.textContent),
      hasEvidence: !!r.querySelector('.evidence'), note: r.querySelector('.note')?.textContent.trim() || null,
      measure: r.querySelector('button.measure')?.textContent.trim() || null }
  }, item)
}

/** The Queue panel: {now: the converting item, stage, pct, eta, waiting: [items in order]}. */
function fabQueue (p) {
  return p.evaluate(() => {
    const now = document.querySelector('.queue .now')
    return { now: now?.dataset.item || null, stage: now?.querySelector('.stage')?.textContent.trim() || null,
      pct: now?.querySelector('.pct')?.textContent.trim() || null, eta: now?.querySelector('.eta')?.textContent.trim() || '',
      waiting: [...document.querySelectorAll('.queue li:not(.now)')].map(l => l.dataset.item) }
  })
}

function fabClick (p, item, text) {
  return p.evaluate((item, text) => {
    const b = [...document.querySelectorAll(`.pack[data-item="${item}"] button`)]
      .find(x => x.textContent.trim() === text || x.getAttribute('aria-label') === text)
    if (!b || b.disabled) return false
    b.click()
    return true
  }, item, text)
}

function fabWait (p, label, item, test, timeout = 60000) {
  return waitFor(p, label, (item, test) => {
    const r = document.querySelector(`.pack[data-item="${item}"]`)
    // eslint-disable-next-line no-new-func
    return r && new Function('r', 'return ' + test)(r)
  }, { timeout, polling: 150 }, item, test)
}

/** True when no process has `pid` any more (a zombie counts as gone). */
function pidGone (pid) {
  try {
    const stat = fs.readFileSync(`/proc/${pid}/stat`, 'utf8')
    return stat.slice(stat.lastIndexOf(')') + 2, stat.lastIndexOf(')') + 3) === 'Z'
  } catch (e) {
    return true
  }
}

async function getJson (url) {
  const r = await fetch(url)
  return r.json()
}

/** Fab Kits against the stand-in converter (RIFTLORD_FABCONVERT=tests/ui_smoke/fakefab.py on
 * the server): the list (only packs that make level pieces; sorting by column; each state's
 * badge and action), Measure, the queue (progress with time left, two waiting, reordered,
 * one cancelled, a reload finding it, the next pack starting after a Cancel and then listed
 * by the Map Editor), problems after an Update, a crash shown as one short line with the
 * converter's words in the console (and the console's Level dropdown), Delete through its
 * modal, loose packs, Settings > Fab Kits (vault folder and limits), the unavailable state
 * and a missing vault; first the cold vault scan's progress, and a reload during it joining
 * the same scan. With any other converter only the tab's state is checked. */
async function fabKits (p, urls, mission, log) {
  const shots = process.env.RL_SHOTS
  const shot = async (name, sel) => {
    if (!shots) return
    await sleep(250)
    if (sel) await (await p.$(sel)).screenshot({ path: `${shots}/fab-${name}.png` })
    else await p.screenshot({ path: `${shots}/fab-${name}.png` })
  }
  await p.evaluateOnNewDocument(() => {
    window.__ipc = []
    window.ipc = { postMessage: (m) => window.__ipc.push(JSON.parse(m)) }
  })
  const st = await getJson(`${urls.shell}/api/fab/state`)
  await openFabTab(p, urls)
  if (!st.available) {
    const text = await p.evaluate(() => document.querySelector('.fab .empty h2')?.textContent)
    check(text === FAB_UNAVAILABLE, `no converter here: "${text}"`, log)
    return
  }
  const scanned = await fabScan(p, urls, shot)
  if (scanned.text) check(SCAN_TEXT.test(scanned.text) && scanned.bar, `while the vault is read: "${scanned.text}"`, log)
  const vault = await fabVaultDone(urls)
  await fabSettled(p)
  if ((vault.roots?.found || [])[0] === FAKE_ROOT) {
    check(scanned.seen && /^Reading vault( \([0-7]\/7\))?$/.test(scanned.text), `the cold scan showed "${scanned.text}"`, log)
    check(scanned.before > 0 && scanned.after === scanned.before && SCAN_TEXT.test(scanned.again || ''),
      `a reload joined the running scan (pid ${scanned.before} -> ${scanned.after}, "${scanned.again}")`, log)
    check(/^Reading vault \([1-7]\/7\)$/.test(scanned.advanced || ''), `the count advances: "${scanned.advanced}"`, log)
  }
  if ((vault.roots?.found || [])[0] !== FAKE_ROOT) {
    const rows = await p.evaluate(() => document.querySelectorAll('.fab .pack').length)
    const empty = await p.evaluate(() => document.querySelector('.fab .empty h2')?.textContent || null)
    const pieces = (vault.items || []).filter(i => i.source === 'file' ||
      (i.kind != null ? FAB_PIECE_KINDS.includes(i.kind) : i.meshes > 0)).length
    check(rows === pieces || empty === 'No Epic vault found',
      `a real converter: ${rows} rows${empty ? ', ' + empty : ''} (start the server with RIFTLORD_FABCONVERT=tests/ui_smoke/fakefab.py for the full case)`, log)
    return
  }

  // the list: only packs that make level pieces, sortable by each column
  await p.evaluate(() => { try { localStorage.removeItem('riftlord-fab-sort') } catch { /* none */ } })
  await fabWait(p, 'five vault rows', FAB.chest, 'document.querySelectorAll(".fab .pack").length === 5')
  const order = () => p.evaluate(() => [...document.querySelectorAll('.fab .pack')].map(r => r.dataset.item))
  const byName = [FAB.brass, FAB.castle, FAB.ninja, FAB.ship, FAB.chest]
  const first = await order()
  check(first.join() === byName.join() && !first.includes(FAB.voice) && !first.includes(FAB.shark),
    `by name; the voice pack (kind none) and the shark (creature) not listed (${first.join(', ')})`, log)
  const heads = await p.evaluate(() => [...document.querySelectorAll('.fab .row.head button.sort')].map(b => b.textContent.trim()))
  check(heads.join() === 'Name▲,Type,Engine,Meshes,Scale,Status', `sortable columns: ${heads.join(', ')}`, log)
  const sortBy = (label) => p.evaluate((label) => [...document.querySelectorAll('.fab .row.head button.sort')]
    .find(b => b.textContent.trim().replace(/[▲▼]/, '') === label).click(), label)
  await sortBy('Meshes')
  await sleep(150)
  const byMeshes = await order()
  await sortBy('Meshes')
  await sleep(150)
  const byMeshesDown = await order()
  check(byMeshes.join() === [FAB.chest, FAB.ninja, FAB.ship, FAB.castle, FAB.brass].join() &&
    byMeshesDown.join() === [...byMeshes].reverse().join(), `Meshes up and down: ${byMeshes.join(', ')}`, log)
  await sortBy('Status')
  await sleep(150)
  const byStatus = await order()
  check(byStatus.join() === [FAB.ninja, FAB.brass, FAB.ship, FAB.chest, FAB.castle].join(),
    `Status: failed, update available, not converted, ready (${byStatus.join(', ')})`, log)
  await sortBy('Name')
  const types = await p.evaluate(() => Object.fromEntries([...document.querySelectorAll('.fab .pack')]
    .map(r => [r.dataset.item, r.querySelector('.kind')?.textContent.trim()])))
  check(types[FAB.castle] === 'Buildings' && types[FAB.chest] === 'Props' && types[FAB.ninja] === 'Mixed',
    `Type: ${JSON.stringify(types)}`, log)
  const castle = await fabRow(p, FAB.castle)
  check(castle.badge === 'Ready' && castle.buttons.join() === 'Delete Desert Castle' && castle.scale === '1.5' && !castle.measure,
    `Desert Castle: ${JSON.stringify(castle)}`, log)
  const brass = await fabRow(p, FAB.brass)
  check(brass.badge === 'Update available' && brass.buttons.includes('Update'), `City of Brass: ${brass.badge}, ${brass.buttons}`, log)
  const ninja = await fabRow(p, FAB.ninja)
  check(ninja.badge === 'Failed' && ninja.buttons.includes('Retry'), `Fluid Ninja: ${ninja.badge}, ${ninja.buttons}`, log)
  const chest = await fabRow(p, FAB.chest)
  check(chest.badge === 'Not converted' && chest.buttons.join() === 'Convert' && chest.measure === 'Measure' &&
    chest.scale === '1', `Wooden Chest: ${JSON.stringify(chest)}`, log)
  await shot('list')

  // Measure
  check(await fabClick(p, FAB.chest, 'Measure'), 'Measure pressed', log)
  await fabWait(p, 'the measured scale', FAB.chest, 'r.querySelector("input").value === "1.5" && r.querySelector(".evidence")')
  await p.evaluate((item) => { document.querySelector(`.pack[data-item="${item}"] .evidence`).open = true }, FAB.chest)
  await sleep(200)
  const measured = await fabRow(p, FAB.chest)
  check(measured.evidence.length === 5 && measured.evidence[4] === 'chosen x1.5' && !measured.measure,
    `measured x1.5 with ${measured.evidence.length} evidence lines; Measure gone`, log)
  await shot('measured', `.pack[data-item="${FAB.chest}"]`)

  // the queue: the slow pack converts (progress and time left), two more wait behind it
  check(await fabClick(p, FAB.ship, 'Convert'), 'Convert pressed on the slow pack', log)
  await waitFor(p, 'the slow pack in the queue', (item) => document.querySelector(`.queue .now[data-item="${item}"] .progress .stage`),
    { timeout: 30000, polling: 150 }, FAB.ship)
  await waitFor(p, 'progress past 0%', () => /^[1-9]\d*%$/.test(document.querySelector('.queue .now .pct')?.textContent.trim() || ''),
    { timeout: 30000, polling: 150 })
  const now = await fabQueue(p)
  const shipRow = await fabRow(p, FAB.ship)
  check(/^(Scan|Materials|Bake|Textures|Meshes|Pack|Checks|Starting)/.test(now.stage) && /^\d+%$/.test(now.pct) &&
    /left$/.test(now.eta) && /^Converting\s\d+%$/.test(shipRow.badge) && shipRow.buttons.includes('Cancel'),
    `converting: ${now.stage}, ${now.pct}, ${now.eta}; row ${shipRow.badge}`, log)
  check(await fabClick(p, FAB.chest, 'Convert'), 'Convert pressed while another converts', log)
  check(await fabClick(p, FAB.brass, 'Update'), 'Update pressed while another converts', log)
  await waitFor(p, 'two waiting', () => document.querySelectorAll('.queue li:not(.now)').length === 2, { timeout: 10000, polling: 100 })
  let q = await fabQueue(p)
  const waitingRow = await fabRow(p, FAB.chest)
  check(q.waiting.join() === [FAB.chest, FAB.brass].join() && waitingRow.badge === 'Queued' &&
    waitingRow.buttons.includes('Cancel'), `queued in order: ${q.waiting.join(', ')}; row ${waitingRow.badge}`, log)
  await shot('queue', '.queue')
  await p.evaluate((item) => document.querySelector(`.queue li[data-item="${item}"] button[aria-label$=" up"]`).click(), FAB.brass)
  await waitFor(p, 'the reorder', (item) => document.querySelector('.queue li:not(.now)')?.dataset.item === item,
    { timeout: 10000, polling: 100 }, FAB.brass)
  q = await fabQueue(p)
  check(q.waiting.join() === [FAB.brass, FAB.chest].join(), `moved up: ${q.waiting.join(', ')}`, log)
  await p.evaluate((item) => [...document.querySelectorAll(`.queue li[data-item="${item}"] button`)]
    .find(b => b.textContent.trim() === 'Cancel').click(), FAB.brass)
  await fabWait(p, 'brass off the queue', FAB.brass, 'r.querySelector(".state").textContent.trim() === "Update available"')
  q = await fabQueue(p)
  check(q.waiting.join() === FAB.chest, `Cancel takes a waiting pack off the queue: ${q.waiting.join(', ')}`, log)

  // a reload during a convert finds it and the queue again (both are the server's)
  let job = null
  for (let i = 0; i < 50 && !(job?.log || []).some(l => /worker pid/.test(l)); i++) {
    job = (await getJson(`${urls.shell}/api/fab/state`)).jobs[FAB.ship]
    await sleep(100)
  }
  const worker = Number((job.log.find(l => /worker pid/.test(l)) || '').split(' ').pop())
  check(job.pid > 0 && worker > 0 && !pidGone(job.pid) && !pidGone(worker), `running as ${job.pid}, worker ${worker}`, log)
  await shot('progress')
  await p.reload({ waitUntil: 'domcontentloaded' })
  await openFabTab(p, urls)
  await waitFor(p, 'the queue after the reload', (item) => document.querySelector(`.queue .now[data-item="${item}"]`) &&
    document.querySelectorAll('.queue li:not(.now)').length === 1, { timeout: 30000, polling: 150 }, FAB.ship)
  const again = await fabRow(p, FAB.ship)
  check(/^Converting/.test(again.badge) && again.buttons.includes('Cancel'), `after a reload: ${again.badge}`, log)

  // Cancel: the group is gone, the library untouched, the next pack starts and finishes
  check(await fabClick(p, FAB.ship, 'Cancel'), 'Cancel pressed', log)
  // the note comes with the job's end, the badge with the next state poll: wait for both
  await fabWait(p, 'the cancel', FAB.ship, 'r.querySelector(".note") && ' +
    'r.querySelector(".state")?.textContent.trim() === "Not converted"')
  let gone = false
  for (let i = 0; i < 50 && !gone; i++) { gone = pidGone(job.pid) && pidGone(worker); if (!gone) await sleep(100) }
  check(gone, `the converter and its worker are gone (${job.pid}, ${worker})`, log)
  const cancelled = await fabRow(p, FAB.ship)
  check(cancelled.note === 'Cancelled' && cancelled.buttons.join() === 'Convert',
    `cancelled: ${cancelled.badge}, ${cancelled.note}`, log)
  await fabWait(p, 'the waiting pack to finish', FAB.chest, 'r.querySelector(".state").textContent.trim() === "Ready"')
  const after = await fabRow(p, FAB.chest)
  check(after.problems.length === 0 && after.buttons.join() === 'Delete Stylized Wooden Chest' && !(await p.$('.queue')),
    `the next pack started by itself and converted: ${JSON.stringify(after.buttons)}`, log)
  const libs = await getJson(`${urls.riftlord}/api/fab/libraries`)
  check(libs.some(l => l.id === 'WoodenChest' && l.status === 'ready'),
    `the Map Editor lists it without a restart (${libs.map(l => l.id).join(', ')})`, log)
  await getJson(`${urls.shell}/api/fab/vault?fresh=1`)
  const shipLib = (await fabVaultDone(urls)).items.find(i => i.item === FAB.ship)
  const libs2 = await getJson(`${urls.riftlord}/api/fab/libraries`)
  check(shipLib.library.status === 'none' && !shipLib.running && !libs2.some(l => l.id === 'SpaceshipInterior'),
    'the cancelled library is untouched', log)

  // problems after an Update
  check(await fabClick(p, FAB.brass, 'Update'), 'Update pressed', log)
  await fabWait(p, 'the problems', FAB.brass, 'r.querySelector(".problems summary") && ' +
    'r.querySelector(".state").textContent.trim() === "Failed"')
  await p.evaluate((item) => { document.querySelector(`.pack[data-item="${item}"] .problems`).open = true }, FAB.brass)
  const probs = await fabRow(p, FAB.brass)
  check(probs.problems.length === 2 && probs.badge === 'Failed' && probs.problemsLabel === '2 problems',
    `problems: ${probs.problemsLabel}: ${probs.problems.join(' | ')}`, log)
  await shot('problems', `.pack[data-item="${FAB.brass}"]`)

  // a crash: a short line on the tab, the converter's words in the console only
  check(await fabClick(p, FAB.ninja, 'Retry'), 'Retry pressed', log)
  await fabWait(p, 'the crash', FAB.ninja, 'r.querySelector(".failed")')
  const crash = await p.evaluate((item) => ({ line: document.querySelector(`.pack[data-item="${item}"] .failed`).firstChild.textContent.trim(),
    page: document.querySelector('.fab').innerText }), FAB.ninja)
  check(crash.line === 'Conversion failed' && !/MemoryError|guard|Traceback/.test(crash.page),
    `failed: "${crash.line}"; no converter words on the tab`, log)
  await shot('failed', `.pack[data-item="${FAB.ninja}"]`)
  check(await fabClick(p, FAB.ninja, 'Open Console'), 'Open Console pressed', log)
  await waitFor(p, 'the console with the crash', () => [...document.querySelectorAll('.console .line.error')]
    .some(l => l.querySelector('.src')?.textContent === 'Fab Kits' && /MemoryError/.test(l.textContent)), { timeout: 15000, polling: 200 })
  check(true, 'the converter\'s words are in the console under Fab Kits, at ERROR', log)
  // the Level dropdown: least to most output, each level showing itself and the more severe
  const levels = await p.evaluate(() => [...document.querySelectorAll('.console select option')].map(o => o.textContent))
  check(levels.join() === 'ERROR,WARN,INFO,DEBUG', `console levels: ${levels.join(', ')}`, log)
  const shownAt = async (value) => {
    await p.select('.console select', value)
    await sleep(200)
    return p.evaluate(() => [...new Set([...document.querySelectorAll('.console .line')]
      .map(l => [...l.classList].find(c => ['debug', 'info', 'warn', 'error'].includes(c))))].sort())
  }
  const atError = await shownAt('error')
  const atInfo = await shownAt('info')
  const atDebug = await shownAt('debug')
  check(atError.join() === 'error' && !atInfo.includes('debug') && atInfo.includes('error') && atInfo.includes('info') &&
    atDebug.includes('debug') && atDebug.includes('error'),
    `ERROR shows ${atError.join('+')}, INFO ${atInfo.join('+')}, DEBUG ${atDebug.join('+')}`, log)
  if (shots) await (await p.$('.console')).screenshot({ path: `${shots}/fab-console.png` })
  await shownAt('info')
  await p.evaluate(() => document.querySelector('.console button[aria-label="Close the console"]').click())

  // Delete, through its modal; the titlebar does not close it
  check(await fabClick(p, FAB.castle, 'Delete Desert Castle'), 'Delete pressed', log)
  await waitFor(p, 'the Delete modal', () => document.querySelector('.fab ~ .modal, .modal[aria-label="Delete Fab Kit"]'),
    { timeout: 10000, polling: 100 })
  await shot('delete-modal')
  const at = await p.evaluate(() => {
    const r = document.querySelector('.titlebar .title').getBoundingClientRect()
    const x = r.right + 40
    const y = r.top + r.height / 2
    const hit = document.elementFromPoint(x, y)
    return { x, y, onBar: !!(hit && hit.closest('.titlebar')) }
  })
  await p.mouse.move(at.x, at.y)
  await p.mouse.down()
  await p.mouse.up()
  await sleep(200)
  const still = await p.evaluate(() => !!document.querySelector('.modal[aria-label="Delete Fab Kit"]'))
  check(at.onBar && still, 'the titlebar is above the scrim and leaves the modal open', log)
  await p.evaluate(() => [...document.querySelectorAll('.modal button')].find(b => b.textContent.trim() === 'Cancel').click())
  await sleep(200)
  const kept = await fabRow(p, FAB.castle)
  check(!(await p.$('.modal[aria-label="Delete Fab Kit"]')) && kept.badge === 'Ready', 'Cancel keeps the library', log)
  await fabClick(p, FAB.castle, 'Delete Desert Castle')
  await waitFor(p, 'the Delete modal again', () => document.querySelector('.modal[aria-label="Delete Fab Kit"]'),
    { timeout: 10000, polling: 100 })
  await p.evaluate(() => document.querySelector('.modal button.confirm').click())
  await fabWait(p, 'the deleted library', FAB.castle, 'r.querySelector(".state").textContent.trim() === "Not converted"')
  const deleted = await fabRow(p, FAB.castle)
  const libs3 = await getJson(`${urls.riftlord}/api/fab/libraries`)
  check(deleted.buttons.join() === 'Convert' && !libs3.some(l => l.id === 'DesertCastle'),
    `deleted; the Map Editor lists ${libs3.map(l => l.id).join(', ')}`, log)

  // loose packs from a file: Add Pack from File (Measure, Convert with progress), listed with
  // their source; one whose source then goes missing shows its status only; both deleted
  const looseDir = fs.mkdtempSync(require('path').join(process.env.RL_ROOT || ts.SCRATCH, 'fabloose-'))
  const zipA = require('path').join(looseDir, 'Crate_Pack.zip')
  const zipB = require('path').join(looseDir, 'Old_Ruins.zip')
  fs.writeFileSync(zipA, 'PK fake crate pack')
  fs.writeFileSync(zipB, 'PK fake ruins pack')
  const rowSel = (item) => `.pack[data-item="${item}"]`
  try {
    const openAdd = async () => {
      await p.evaluate(() => [...document.querySelectorAll('.fab .actions button')]
        .find(b => b.textContent.trim() === 'Add Pack from File…').click())
      await waitFor(p, 'the Add Pack from File dialog', () => document.querySelector('.add-modal input[aria-label="Path"]'),
        { timeout: 10000, polling: 100 })
    }
    await openAdd()
    const choose = await p.evaluate(() => [...document.querySelectorAll('.add-modal .choose button')].map(b => b.textContent.trim()))
    check(choose.join() === 'Choose File…,Choose Folder…', `the app window offers ${choose.join(', ')}`, log)
    await p.type('.add-modal input[aria-label="Path"]', zipA)
    await p.evaluate(() => document.querySelector('.add-modal button.measure').click())
    await waitFor(p, 'the measured loose pack', () => document.querySelectorAll('.add-modal .evidence li').length === 5 &&
      document.querySelector('.add-modal input[aria-label="Scale"]').value === '1.5', { timeout: 30000, polling: 150 })
    check(true, 'Measure on a path: x1.5 with 5 evidence lines', log)
    await shot('add-from-file', '.add-modal')
    await p.evaluate(() => document.querySelector('.add-modal button.confirm').click())
    await waitFor(p, 'the dialog to close', () => !document.querySelector('.add-modal'), { timeout: 10000, polling: 100 })
    await fabWait(p, 'the loose pack converting', zipA, '/^Converting/.test(r.querySelector(".state").textContent.trim())')
    const going = await fabRow(p, zipA)
    const goingQ = await fabQueue(p)
    check(/^Converting\s\d+%$/.test(going.badge) && goingQ.now === zipA, `converting from the file: ${going.badge}`, log)
    await fabWait(p, 'the loose pack converted and listed', zipA, 'r.querySelector(".state").textContent.trim() === "Ready" && ' +
      'r.querySelector(".source") && r.querySelector(".name b").textContent === "Crate_Pack"')
    const done = await p.evaluate((s) => ({ title: document.querySelector(s + ' .name b').textContent,
      source: document.querySelector(s + ' .source').textContent, scale: document.querySelector(s + ' input')?.value }), rowSel(zipA))
    const libsA = await getJson(`${urls.riftlord}/api/fab/libraries`)
    check(done.title === 'Crate_Pack' && done.source === zipA && done.scale === '1.5' &&
      libsA.some(l => l.id === 'FileCratePack'), `listed with its source: ${JSON.stringify(done)}`, log)
    await shot('imported', rowSel(zipA))

    // a second one, whose source then moves away
    await openAdd()
    await p.type('.add-modal input[aria-label="Path"]', zipB)
    await p.evaluate(() => document.querySelector('.add-modal button.confirm').click())
    await fabWait(p, 'the second loose pack converted and listed', zipB, 'r.querySelector(".state").textContent.trim() === "Ready" && ' +
      'r.querySelector(".source") && r.querySelector(".name b").textContent === "Old_Ruins"')
    fs.unlinkSync(zipB)
    await p.evaluate(() => document.querySelector('.fab .actions button[aria-label="Refresh"]').click())
    await fabWait(p, 'the moved source', zipB, 'r.classList.contains("missing")', 30000)
    const gone = await fabRow(p, zipB)
    check(gone.badge === 'Ready' && !gone.input && gone.buttons.join() === 'Delete Old_Ruins',
      `source missing: status only, ${JSON.stringify(gone.buttons)}`, log)
    for (const [zip, name] of [[zipB, 'Old_Ruins'], [zipA, 'Crate_Pack']]) {
      await p.evaluate((s) => document.querySelector(s + ' button.delete').click(), rowSel(zip))
      await waitFor(p, 'the Delete modal', () => document.querySelector('.modal[aria-label="Delete Fab Kit"]'),
        { timeout: 10000, polling: 100 })
      await p.evaluate(() => document.querySelector('.modal button.confirm').click())
      await waitFor(p, `${name} to leave the list`, (s) => !document.querySelector(s) && !document.querySelector('.modal'),
        { timeout: 60000, polling: 200 }, rowSel(zip))
    }
    const libsB = await getJson(`${urls.riftlord}/api/fab/libraries`)
    check(!libsB.some(l => /^File/.test(l.id)), 'both loose packs deleted, the moved one included', log)
  } finally {
    fs.rmSync(looseDir, { recursive: true, force: true })
  }

  // Settings > Fab Kits: the vault folder found, the limits at the converter's defaults;
  // a changed limit is kept, and the default value or Reset stores nothing
  await p.evaluate(() => document.querySelector('button.tab[aria-label="Settings"]').click())
  await waitFor(p, 'Settings > Fab Kits', () => document.querySelector('.fabkits input[aria-label="Workers"]'),
    { timeout: 30000, polling: 200 })
  const fabSet = () => p.evaluate(() => ({ vault: document.querySelector('.fabkits code.vault')?.textContent.trim(),
    memory: document.querySelector('.fabkits input[aria-label="Memory Limit"]').value,
    workers: document.querySelector('.fabkits input[aria-label="Workers"]').value,
    reset: [...document.querySelectorAll('.fabkits button')].filter(b => b.textContent.trim() === 'Reset').length }))
  const set0 = await fabSet()
  check(set0.vault === FAKE_ROOT && set0.memory === '8192' && set0.workers === '2' && set0.reset === 0,
    `Settings > Fab Kits: ${JSON.stringify(set0)}`, log)
  const typeLimit = async (label, value) => {
    await p.click(`.fabkits input[aria-label="${label}"]`, { clickCount: 3 })
    await p.keyboard.type(String(value))
    await p.keyboard.press('Tab')
    await sleep(400 * SCALE)
  }
  await typeLimit('Workers', 1)
  await waitFor(p, 'Reset beside a changed limit',
    () => [...document.querySelectorAll('.fabkits button')].some(b => b.textContent.trim() === 'Reset'),
    { timeout: 10000, polling: 200 }).catch(() => {})
  let saved = await getJson(`${urls.shell}/api/fab/settings`)
  const set1 = await fabSet()
  check(saved.workers === 1 && set1.reset === 1, `Workers 1 kept (${saved.workers}), Reset shown`, log)
  if (shots) await (await p.$('.fabkits')).screenshot({ path: `${shots}/fab-settings.png` })
  await typeLimit('Workers', 2)
  saved = await getJson(`${urls.shell}/api/fab/settings`)
  check(saved.workers === null, `the default value stores nothing (${saved.workers})`, log)
  await typeLimit('Memory Limit', 4096)
  await p.evaluate(() => [...document.querySelectorAll('.fabkits button')].find(b => b.textContent.trim() === 'Reset').click())
  await sleep(400 * SCALE)
  saved = await getJson(`${urls.shell}/api/fab/settings`)
  const set2 = await fabSet()
  check(saved.memoryMb === null && set2.memory === '8192' && set2.reset === 0, `Reset: ${JSON.stringify(set2)}`, log)
  await p.evaluate(() => document.querySelector('button.tab[aria-label="Fab Kits"]').click())

  // the unavailable state and a missing vault, as the server would answer them
  const tools = await getJson(`${urls.shell}/api/tools`)
  let fake = 'unavailable'
  await p.setRequestInterception(true)
  p.on('request', (r) => {
    const u = r.url()
    if (fake === 'unavailable' && u.endsWith('/api/tools')) {
      return r.respond({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ ...tools, native: { ...tools.native, fab: FAB_UNAVAILABLE } }) })
    }
    if (fake === 'unavailable' && u.includes('/api/fab/state')) {
      return r.respond({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ available: false, message: FAB_UNAVAILABLE, jobs: {} }) })
    }
    if (fake === 'novault' && u.includes('/api/fab/vault')) {
      return r.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({
        ok: false, exit: 2, library: '/x', items: [], roots: { found: [], searched: ['/fake/A/EpicVault', '/fake/B/VaultCache'] } }) })
    }
    return r.continue()
  })
  await p.reload({ waitUntil: 'domcontentloaded' })
  await openFabTab(p, urls)
  const off = await p.evaluate(() => ({ text: document.querySelector('.fab .empty h2')?.textContent,
    buttons: document.querySelectorAll('.fab button').length, rows: document.querySelectorAll('.fab .pack').length }))
  check(off.text === FAB_UNAVAILABLE && off.buttons === 0 && off.rows === 0, `unavailable: ${JSON.stringify(off)}`, log)
  await shot('unavailable')
  fake = 'novault'
  await p.reload({ waitUntil: 'domcontentloaded' })
  await openFabTab(p, urls)
  const none = await p.evaluate(() => ({ text: document.querySelector('.fab .empty h2')?.textContent,
    folders: [...document.querySelectorAll('.fab .searched li')].map(l => l.textContent),
    choose: document.querySelector('.fab .empty button.choose-vault')?.textContent.trim() }))
  check(none.text === 'No Epic vault found' && none.folders.join() === '/fake/A/EpicVault,/fake/B/VaultCache' &&
    none.choose === 'Choose Vault Folder…',
    `no vault: ${JSON.stringify(none)}`, log)
  await shot('novault')
}

//: The New Object meshes the arsenal rule files under trap (riftlord/formats/omd3/meshcat.py).
const ARSENAL = ['BladeStaff', 'Amulet_Ice', 'Bow_Magic_Flare', 'Trinket_EmptyRift',
  'ReptilianCarapace_SM', 'RingofStorms_SM']
//: Level picker groups the game's campaign tables replaced (riftlord/mapeditor/levelgroups.py).
const OLD_GROUPS = ['Campaign', 'DLC', 'Other Levels', 'Other']
//: A plain 100 uu cube mesh, its pivot in its middle.
const CUBE = '1M_Cube'
//: Uu over the rift where the snap cubes hang, clear of the level.
const SNAP_HEIGHT = 5000

/** The Map Editor round of 2026-10-28: the level picker by campaign, the New Level
 * dialog's Game Mode, Display Options (no sublevel heading without sublevels, effects
 * hidden), the trace hidden on open, translucent door boxes, Delete buttons, the icon Flip
 * buttons, Material selectors showing the current material and every other, shift taking
 * every snap away, a snap meeting a turned mesh's real face, a blocking volume turned and
 * resized by its fields and saved, and New Object's filters (no empty ones; weapons and
 * trinkets under trap). RL_SHOTS keeps screenshots. */
async function mapeditorUi (p, urls, mission, log) {
  const shots = process.env.RL_SHOTS
  const shot = async (name, selector) => {
    if (!shots) return
    const box = selector ? await p.$(selector) : null
    await (box || p).screenshot({ path: `${shots}/mapeditorui-${name}.png` })
  }
  await openMission(p, urls, mission, log)
  // the level picker: the game's campaign names
  const groups = await p.evaluate(() => [...document.querySelectorAll('#level optgroup')].map(g => g.label))
  check(groups.includes('Old Friends') && groups.includes('Tipping the Scales') &&
    !groups.some(g => OLD_GROUPS.includes(g)), `picker groups: ${groups.join(', ')}`, log)
  const lp = await p.$('.lp-btn')
  if (lp) {
    await lp.click()
    await sleep(400)
    await shot('levelpicker')
    await p.keyboard.press('Escape')
  }
  // Display Options: the sublevel heading only with sublevels; effects hidden
  const display = await p.evaluate(() => ({
    heading: [...document.querySelectorAll('#layers .hint')].some(h => /sublevels/.test(h.textContent)),
    subs: document.querySelectorAll('#layers [data-sub]').length,
    vfx: document.querySelector('input[data-layer="vfx"]').checked || window.__rl_layersShown().vfx }))
  check(display.heading === display.subs > 0 && !display.vfx,
    `Display Options: ${display.subs} sublevels, heading ${display.heading}, effects ${display.vfx}`, log)
  // the trace is hidden on open
  const trace = await p.evaluate(() => window.__rl_trace())
  check(!trace.visible, `trace hidden on open (${trace.source || 'no source'})`, log)
  // door boxes let the door show through
  const doors = await p.evaluate(() => window.__rl_doorBoxOpacity())
  check(doors.length > 0 && doors.every(o => o < 1), `door boxes' opacity ${[...new Set(doors)].join(', ')}`, log)
  const doorNames = await p.evaluate(() => window.__rl_levelDoorNames())
  if (doorNames.length) {
    await p.evaluate((n) => window.__rl_pickWiring(n), doorNames[0])
    const label = await p.evaluate(() => document.querySelector('#removewiring')?.textContent.trim())
    check(label === 'Delete', `a level door's button: ${label}`, log)
  }
  // a placed piece: icon Flip buttons and Material selectors
  const n = await placeKit(p, log)
  await sleep(500)
  await waitFor(p, 'the Material selectors with the materials loaded', () =>
    [...document.querySelectorAll('select[data-paint]')].some(s => s.options.length > 50),
  { timeout: 60000, polling: 250 })
  const panel = await p.evaluate(() => ({
    flips: [...document.querySelectorAll('.flips button')].map(b => ({
      label: b.textContent.trim(), icon: !!b.querySelector('svg'), flip: b.dataset.flip })),
    paints: [...document.querySelectorAll('select[data-paint]')].map(s => ({
      value: s.value, options: s.options.length, selected: s.selectedIndex })),
    remove: document.querySelector('#removepicked')?.textContent.trim() }))
  check(panel.flips.length === 4 && panel.flips.every(f => f.icon && f.label && f.flip),
    `Flip: ${panel.flips.map(f => f.label).join(', ')}`, log)
  check(panel.paints.length > 0 && panel.paints.every(s => s.value && s.options > 50),
    `Material: ${panel.paints.map(s => `${s.value} (${s.options} options)`).slice(0, 3).join('; ')}`, log)
  check(panel.remove === 'Delete', `the piece's button: ${panel.remove}`, log)
  await shot('selected', '#props')
  // a material picked from the selector is stored, and the selector shows it
  const before = await p.evaluate((i) => window.__rl_planBits().added[i].paints, n)
  const chosen = await p.evaluate(() => {
    const s = document.querySelector('select[data-paint]')
    const other = [...s.options].find(o => o.value && o.value !== s.value)
    s.value = other.value
    s.dispatchEvent(new Event('change'))
    return other.value
  })
  await sleep(500)
  const after = await p.evaluate((i) => window.__rl_planBits().added[i].paints, n)
  const shows = await p.evaluate(() => document.querySelector('select[data-paint]').value)
  check(!before && JSON.stringify(after || {}).includes(chosen) && shows === chosen,
    `painted ${chosen}: plan ${JSON.stringify(after)}, the selector shows ${shows}`, log)
  // shift takes the edge snap away too
  const free = await p.evaluate(() => window.__rl_edgeSnapTry(-7, true))
  check(Math.abs(free.moved + 7) < 1e-6 && free.cue === 0, `shift held: moved ${free.moved}, ${free.cue} cue`, log)
  // a snap meets a turned cube's real face, not its box: A turned 45 degrees, B beside it
  const snap = await p.evaluate(async (cube, height) => {
    const r = window.__rl_riftAt() || [0, 0, 0]
    const at = [r[0] + 600, r[1] + 600, r[2] + height]
    const a = (await window.__rl_addMesh(cube, at)) - 1
    const c = Math.SQRT1_2
    window.__rl_setAddedMatrix(a, [c, c, 0, 0, -c, c, 0, 0, 0, 0, 1, 0, at[0], at[1], at[2], 1])
    const b = (await window.__rl_addMesh(cube, [at[0] + 115, at[1] + 60, at[2]])) - 1
    window.__rl_pickAdded(b)
    return window.__rl_edgeSnapTry(-10, false)
  }, CUBE, SNAP_HEIGHT)
  // the turned cube's face is 60.7 uu out at B's near edge (y + 10), its corner 70.7
  check(Math.abs(snap.moved - (-4.29)) < 0.5 && snap.cue > 0,
    `snapped ${snap.moved.toFixed(2)} uu onto the turned face (its box would give +5.71)`, log)
  // a ground spawner stands SPAWNER_ABOVE_FLOOR (99 uu) over the floor under it: when it
  // becomes a ground spawner, and again after a move
  const spawnerAt = await p.evaluate(async () => {
    const r = window.__rl_riftAt() || [0, 0, 0]
    window.__rl_place('spawner', [r[0] + 300, r[1] + 300, r[2] + 400])
    return window.__rl_planActors().spawners.slice(-1)[0].at
  })
  // its floor is the plan's (riftfloor.plan_floor): here the 4x4 kit floor placed above
  // (placeKit lands it by its trap grid at the rift, under this click), whose walking surface
  // is at its pivot, above the level's own floor
  const floor = await p.evaluate((at) => window.__rl_askFloor(at, 'spawner'), spawnerAt)
  const levelFloor = await p.evaluate(async (at) => (await fetch(`api/riftfloor/${encodeURIComponent(
    window.__rl_levelName())}?for=spawner&at=${at.join(',')}`)).json(), spawnerAt)
  const kitZ = await p.evaluate((i) => window.__rl_addedMatrix(i)[14], n)
  check(floor.at && levelFloor.at && Math.abs(floor.floor - kitZ) < 0.2 && levelFloor.floor < floor.floor,
    `the floor under the spawner is the added kit floor at ${floor.floor} (kit at z ${kitZ.toFixed(1)}, ` +
    `the level's own ${levelFloor.floor})`, log)
  await setField(p, '[data-field="kind"]', 'ground')
  const spawnerZ = () => p.evaluate(() => window.__rl_planActors().spawners.slice(-1)[0].at[2])
  await waitFor(p, 'the ground spawner on its floor', (z) =>
    Math.abs(window.__rl_planActors().spawners.slice(-1)[0].at[2] - z) < 0.2,
  { timeout: 15000, polling: 200 }, floor.at ? floor.at[2] : NaN).catch(() => {})
  const placedZ = await spawnerZ()
  check(floor.at && Math.abs(placedZ - floor.at[2]) < 0.2 && Math.abs(floor.at[2] - floor.floor - 99) < 0.2,
    `ground spawner at z ${placedZ} (floor ${floor.floor}, clicked ${spawnerAt[2]})`, log)
  // a short drag up (a few hundred uu, inside riftfloor.SPAWNER_REACH) with shift held stays
  // where it is let go; a nudge from there without shift puts it back on the floor
  await p.evaluate(() => window.__rl_gizmoDrag('translate', 'Z', [0.0, 0.0], [0.0, 0.03], true))
  await sleep(1500)
  const dragged = await spawnerZ()
  check(floor.at && dragged > floor.at[2] + 20, `shift held: let go at z ${dragged}`, log)
  await p.evaluate(() => window.__rl_gizmoDrag('translate', 'Z', [0.0, 0.0], [0.0, 0.002], false))
  await sleep(1500)
  const movedZ = await spawnerZ()
  check(floor.at && Math.abs(movedZ - floor.at[2]) < 0.2,
    `after a move up from z ${dragged}: back on the floor at z ${movedZ}`, log)
  // a ground path point (the spawner's start, placed while it is selected) stands
  // POINT_ABOVE_FLOOR (10.8 uu) over the floor a build puts it on (riftfloor.snap_point):
  // when placed, when a move ends, and when its position is typed; shift keeps it as it is
  const pointAt = await p.evaluate(async () => {
    const sp = window.__rl_planActors().spawners.slice(-1)[0]
    window.__rl_place('point', [sp.at[0] + 400, sp.at[1], sp.at[2] + 60])
    return window.__rl_planActors().points.slice(-1)[0].at
  })
  const pointFloor = await p.evaluate((at) => window.__rl_askFloor(at, 'point'), pointAt)
  const pointZ = () => p.evaluate(() => window.__rl_planActors().points.slice(-1)[0].at[2])
  const onFloor = (label) => waitFor(p, label, (z) =>
    Math.abs(window.__rl_planActors().points.slice(-1)[0].at[2] - z) < 0.2,
  { timeout: 15000, polling: 200 }, pointFloor.at ? pointFloor.at[2] : NaN).catch(() => {})
  await onFloor('the placed point on its floor')
  const pointPlaced = await pointZ()
  check(pointFloor.at && Math.abs(pointPlaced - pointFloor.at[2]) < 0.2 &&
    Math.abs(pointFloor.at[2] - pointFloor.floor - 10.8) < 0.2,
  `placed point at z ${pointPlaced} (floor ${pointFloor.floor}, clicked ${pointAt[2]})`, log)
  // typed with shift held: kept as typed
  const typedZ = pointFloor.floor + 150
  await p.evaluate(() => dispatchEvent(new KeyboardEvent('keydown', { key: 'Shift', shiftKey: true })))
  await setField(p, '[data-at="2"]', typedZ)
  await p.evaluate(() => dispatchEvent(new KeyboardEvent('keyup', { key: 'Shift', shiftKey: false })))
  await sleep(1500)
  const keptZ = await pointZ()
  check(Math.abs(keptZ - typedZ) < 0.2, `shift held: typed z ${typedZ} kept as ${keptZ}`, log)
  // a nudge from there without shift puts it back on the floor
  await p.evaluate(() => window.__rl_gizmoDrag('translate', 'Z', [0.0, 0.0], [0.0, 0.002], false))
  await onFloor('the moved point on its floor')
  const pointMoved = await pointZ()
  check(pointFloor.at && Math.abs(pointMoved - pointFloor.at[2]) < 0.2,
    `after a move from z ${keptZ}: back on the floor at z ${pointMoved}`, log)
  // typed without shift: on the floor
  await setField(p, '[data-at="2"]', pointFloor.floor + 120)
  await sleep(300)
  await onFloor('the typed point on its floor')
  const pointTyped = await pointZ()
  check(pointFloor.at && Math.abs(pointTyped - pointFloor.at[2]) < 0.2,
    `typed z ${pointFloor.floor + 120}: on the floor at z ${pointTyped}`, log)
  // a blocking volume: turned and resized by its fields, kept by a save
  const vols = await p.evaluate(() => window.__rl_volumeNames())
  if (vols.length) {
    await layer(p, 'volumes', true)
    await p.evaluate((name) => window.__rl_pickVolume(name), vols[0])
    await waitFor(p, 'the volume panel', () => document.querySelector('[data-vrot="1"]'), { timeout: 10000, polling: 100 })
    const fields = await p.evaluate(() => ({
      handles: [...document.querySelectorAll('#gizmomode [data-gmode]')].map(b => b.dataset.gmode),
      size: [0, 1, 2].map(i => +document.querySelector(`[data-vsize="${i}"]`).value) }))
    check(fields.handles.join() === 'translate,rotate,scale', `volume handles: ${fields.handles.join(', ')}`, log)
    await setField(p, '[data-vrot="1"]', 30)
    await sleep(300)
    await setField(p, '[data-vsize="0"]', Math.round(fields.size[0] * 2))
    await sleep(300)
    const edit = await p.evaluate((name) => window.__rl_planVolumes().find(v => v.name === name), vols[0])
    const m = edit && edit.matrix
    const yaw = m ? Math.atan2(m[1], m[0]) * 180 / Math.PI : null
    check(m && Math.abs(yaw - 30) < 0.5, `turned: plan yaw ${yaw && yaw.toFixed(2)}`, log)
    await shot('volume', '#props')
    const saved = await p.evaluate(async (name) => {
      await window.__rl_savePlan()
      const plan = await (await fetch('api/plan/' + encodeURIComponent(window.__rl_levelName()))).json()
      return (plan.volumes || []).find(v => v.name === name) || null
    }, vols[0])
    check(saved && Array.isArray(saved.matrix) && saved.matrix.length === 16,
      `saved: ${JSON.stringify(saved).slice(0, 120)}`, log)
    await layer(p, 'volumes', false)
  }
  // a blocking volume added: picked with its handles and fields, resized, saved, deleted
  const addedVol = await p.evaluate(async () => {
    const r = window.__rl_riftAt() || [0, 0, 0]
    await window.__rl_addVolume([r[0] + 900, r[1], r[2]])
    const v = window.__rl_planVolumes().find(e => e.added)
    return { v, panel: !!document.querySelector('[data-vsize="0"]'),
      name: document.querySelector('#propsbody .card b')?.textContent,
      shown: window.__rl_layersShown().volumes, names: window.__rl_volumeNames() }
  })
  check(addedVol.v && addedVol.v.name === 'RiftLordVolume1' && addedVol.panel && addedVol.shown &&
    addedVol.names.includes('RiftLordVolume1'), `added ${addedVol.name}: panel ${addedVol.panel}, layer ${addedVol.shown}`, log)
  // Blocks: a new volume stops players; Players and Enemies is stored when chosen
  const newBlocks = await p.evaluate(() => ({ select: document.querySelector('#volblocks')?.value,
    options: [...document.querySelectorAll('#volblocks option')].map(o => o.textContent),
    plan: window.__rl_planVolumes().find(e => e.added).blocks }))
  check(newBlocks.select === 'players' && newBlocks.plan === 'players' &&
    newBlocks.options.join() === 'Players,Players and Enemies', `new volume Blocks: ${JSON.stringify(newBlocks)}`, log)
  await setField(p, '#volblocks', 'everyone')
  await sleep(300)
  const allBlocks = await p.evaluate(() => window.__rl_planVolumes().find(e => e.added).blocks)
  check(allBlocks === 'everyone', `Blocks set to Players and Enemies: plan ${allBlocks}`, log)
  await setField(p, '#volblocks', 'players')
  await sleep(300)
  await setField(p, '[data-vsize="1"]', 900)
  await sleep(300)
  const sizedVol = await p.evaluate(() => {
    const m = window.__rl_planVolumes().find(e => e.added).matrix
    return Math.hypot(m[4], m[5], m[6])
  })
  check(Math.abs(sizedVol * 200 - 900) < 1, `sized to ${Math.round(sizedVol * 200)} uu along y`, log)
  await shot('addedvolume', '#props')
  const savedVol = await p.evaluate(async () => {
    await window.__rl_savePlan()
    const plan = await (await fetch('api/plan/' + encodeURIComponent(window.__rl_levelName()))).json()
    return (plan.volumes || []).find(v => v.added) || null
  })
  check(savedVol && savedVol.name === 'RiftLordVolume1' && savedVol.matrix.length === 16,
    `saved: ${JSON.stringify(savedVol).slice(0, 100)}`, log)
  await clickIn(p, '#removevolume')
  await sleep(300)
  const goneVol = await p.evaluate(() => ({ plan: window.__rl_planVolumes().some(e => e.added),
    drawn: window.__rl_volumeNames().includes('RiftLordVolume1') }))
  check(!goneVol.plan && !goneVol.drawn, `Delete takes it out (plan ${goneVol.plan}, drawn ${goneVol.drawn})`, log)
  // a level volume's Blocks shows what it stops; the other choice is stored, its own is not
  const own = await p.evaluate(() => (window.__rl_volumeBlocks().find(([, b]) => b) || [])[0] || null)
  if (own) {
    await p.evaluate((name) => window.__rl_pickVolume(name), own)
    await sleep(300)
    const was = await p.evaluate((name) => ({ select: document.querySelector('#volblocks')?.value,
      shipped: window.__rl_volumeBlocks().find(([n]) => n === name)[1] }), own)
    const other = was.shipped === 'players' ? 'everyone' : 'players'
    await setField(p, '#volblocks', other)
    await sleep(300)
    const changed = await p.evaluate((name) => (window.__rl_planVolumes().find(e => e.name === name) || {}).blocks, own)
    await setField(p, '#volblocks', was.shipped)
    await sleep(300)
    const back = await p.evaluate((name) => window.__rl_planVolumes().some(e => e.name === name), own)
    check(was.select === was.shipped && changed === other && !back,
      `${own}: shows ${was.select}, ${other} stored, ${was.shipped} stores nothing (${back ? 'kept' : 'gone'})`, log)
  }
  await layer(p, 'volumes', false)
  await p.evaluate(() => window.__rl_savePlan())
  // New Object: no empty filter; the arsenal under trap
  await clickIn(p, '#openkit')
  await waitFor(p, 'the New Object cards', () => document.querySelectorAll('#kitgrid .kitcard').length > 0,
    { timeout: 120000, polling: 250 })
  const tags = await p.evaluate(async () => {
    const out = []
    for (const b of document.querySelectorAll('#kitfilters [data-cat]')) {
      if (b.hidden) { out.push({ cat: b.dataset.cat, hidden: true }); continue }
      b.click()
      await new Promise(r => setTimeout(r, 150))
      out.push({ cat: b.dataset.cat, cards: document.querySelectorAll('#kitgrid .kitcard').length })
    }
    document.querySelector('#kitfilters [data-cat="all"]').click()
    return out
  })
  check(tags.every(t => t.hidden || t.cards > 0),
    `filters: ${tags.map(t => t.hidden ? `${t.cat} hidden` : `${t.cat} ${t.cards}`).join(', ')}`, log)
  const volumeCard = await p.evaluate(() =>
    !!document.querySelector('#kitgrid .kitcard[data-kind="gameplay"][data-name="blockingVolume"]'))
  check(volumeCard, 'New Object has the Blocking Volume card', log)
  const arsenal = await p.evaluate(async (names) => {
    document.querySelector('#kitfilters [data-cat="trap"]').click()
    await new Promise(r => setTimeout(r, 150))
    const shown = new Set([...document.querySelectorAll('#kitgrid .kitcard')].map(c => c.dataset.name))
    return names.filter(n => !shown.has(n))
  }, ARSENAL)
  check(arsenal.length === 0, `under trap: ${ARSENAL.length - arsenal.length} of ${ARSENAL.length}` +
    (arsenal.length ? ` (missing ${arsenal.join(', ')})` : ''), log)
  await shot('newobject', '#kit')
  // the New Level dialog: Game Mode with more than one mode
  const modes = await getJson(`${urls.riftlord}/api/scratch/modes`)
  await clickIn(p, '#newlevel')
  await p.waitForSelector('#scratchcancel', { timeout: 120000 })
  await sleep(300)
  const dialog = await p.evaluate(() => {
    const s = document.querySelector('#scratchmode')
    return { shown: !!s && !s.parentElement.hidden, options: s ? [...s.options].map(o => o.textContent) : [] }
  })
  check(dialog.shown === (modes.length > 1) && dialog.options.length === modes.length,
    `New Level: Game Mode ${dialog.shown ? dialog.options.join(' / ') : 'hidden'} (${modes.length} mode(s))`, log)
  await shot('newlevel')
  await clickIn(p, '#scratchcancel')
}

//: The Setup pane's step labels (riftlord/types/setup.py STEPS).
const SETUP_LABELS = ['Engine types', 'Game types', 'Blueprints', 'Terrain']
const setupSteps = (states, progress) => SETUP_LABELS.map((label, i) => ({
  key: ['engine', 'game', 'blueprints', 'terrain'][i], label, state: states[i] || 'waiting', progress: progress[i] || 0, seconds: null }))
//: Stand-in /api/setup answers: {name: answer}.
const SETUP_FAKES = {
  running: { state: 'running', message: null, error: null, skipped: [],
    steps: setupSteps(['done', 'running', 'waiting'], [1, 0.4, 0]) },
  failed: { state: 'failed', message: null, error: 'Game types: no game exe', skipped: [],
    steps: setupSteps(['done', 'failed', 'waiting'], [1, 0, 0]) },
  unavailable: { state: 'unavailable', message: 'Mission Events need Unreal Engine 4.27',
    error: null, skipped: [], steps: setupSteps(['waiting', 'waiting', 'waiting'], [0, 0, 0]) },
  ready: { state: 'ready', message: null, error: null, skipped: [],
    steps: setupSteps(['done', 'done', 'done'], [1, 1, 1]) },
}

/** The shell's Setup pane: this server's own state, then each stand-in answer (running,
 * failed, unavailable, ready): heading, the three step labels and their bars, Retry only on
 * a failure, the one-line message when it cannot run, nothing once ready; no prose. */
async function setup (p, urls, mission, log) {
  const shots = process.env.RL_SHOTS
  const real = await getJson(`${urls.shell}/api/setup`)
  check(['ready', 'running', 'needed', 'failed', 'unavailable'].includes(real.state) &&
    real.steps.map(s => s.label).join() === SETUP_LABELS.join(), `api: ${real.state}, ${real.steps.map(s => `${s.label} ${s.state}`).join(', ')}`, log)
  const pane = () => p.evaluate(() => {
    const s = document.querySelector('section.setup')
    const banner = [...document.querySelectorAll('main > .error')].map(e => e.textContent.trim())
    return s ? { heading: s.querySelector('h2').textContent.trim(),
      labels: [...s.querySelectorAll('.row .label')].map(e => e.textContent.trim()),
      values: [...s.querySelectorAll('.row .value')].map(e => e.textContent.trim()),
      widths: [...s.querySelectorAll('.row .fill')].map(e => e.style.width),
      buttons: [...s.querySelectorAll('button:not(.close)')].map(b => b.textContent.trim()),
      modal: s.getAttribute('role') === 'dialog' && getComputedStyle(s).position === 'fixed' &&
        !!s.querySelector('button.close'),
      detail: s.querySelector('.detail')?.textContent.trim() || null, banner } : { banner }
  })
  await openShell(p, urls)
  await sleep(1500)
  const own = await pane()
  if (real.state === 'ready') check(!own.heading, 'ready here: no Setup pane', log)
  if (real.state === 'running' || real.state === 'needed') {
    // an empty cache: the first run, from "Setting up" to no pane
    check(own.heading === 'Setting up' && own.labels.join() === SETUP_LABELS.join(), `first run: ${JSON.stringify(own)}`, log)
    if (shots) await p.screenshot({ path: `${shots}/setup-first-run.png` })
    await waitFor(p, 'Setup to finish', () => !document.querySelector('section.setup'), { timeout: 240000, polling: 500 })
    const done = await getJson(`${urls.shell}/api/setup`)
    check(done.state === 'ready' && done.steps.every(s => s.state === 'done'), `first run done: ${done.steps.map(s => `${s.label} ${s.state} ${s.seconds} s`).join(', ')}`, log)
  }
  if (real.state === 'unavailable') check(own.banner.includes(real.message), `unavailable here: ${JSON.stringify(own)}`, log)
  if (shots) await p.screenshot({ path: `${shots}/setup-real.png` })
  let fake = null
  await p.setRequestInterception(true)
  p.on('request', (r) => {
    if (fake && r.url().replace(/\?.*$/, '').endsWith('/api/setup')) {
      return r.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(SETUP_FAKES[fake]) })
    }
    return r.continue()
  })
  for (const name of Object.keys(SETUP_FAKES)) {
    fake = name
    await p.reload({ waitUntil: 'domcontentloaded' })
    await waitFor(p, 'the Missions list', () => document.querySelector('button[title^="Mission Settings"]'),
      { timeout: 120000, polling: 250 })
    await sleep(1200)
    const got = await pane()
    if (name === 'running') {
      check(got.heading === 'Setting up' && got.labels.join() === SETUP_LABELS.join() && got.modal &&
        got.values.join() === 'Done,40%,,' && got.widths.join() === '100%,40%,0%,0%' && got.buttons.length === 0 && !got.detail,
      `running: ${JSON.stringify(got)}`, log)
      // the dialog's close button hides it while the state stays the same
      if (shots) await p.screenshot({ path: `${shots}/setup-running-open.png` })
      await p.evaluate(() => document.querySelector('section.setup button.close').click())
      await sleep(400)
      const closed = await pane()
      check(!closed.heading, `running, closed: ${JSON.stringify(closed)}`, log)
    } else if (name === 'failed') {
      check(got.heading === 'Setup failed' && got.values.join() === 'Done,Failed,,' && got.buttons.join() === 'Retry' &&
        got.modal && got.detail === SETUP_FAKES.failed.error, `failed: ${JSON.stringify(got)}`, log)
    } else if (name === 'unavailable') {
      check(!got.heading && got.banner.includes(SETUP_FAKES.unavailable.message), `unavailable: ${JSON.stringify(got)}`, log)
    } else {
      check(!got.heading && !got.banner.some(b => /Unreal|Setup/.test(b)), `ready: ${JSON.stringify(got)}`, log)
    }
    if (shots) await p.screenshot({ path: `${shots}/setup-${name}.png` })
  }
  fake = null
}

/** An added spawner faces its route's first point (riftlord/mapeditor/spawnerfacing.py):
 * when its first point is placed, moved and changed to a level point; drawn with a facing
 * cone; turned by hand (yaw field or gizmo) it keeps its yaw, through a move and a save. */
async function spawnerFacing (p, urls, mission, log) {
  await openMission(p, urls, mission, log)
  const heading = (a, b) => Math.atan2(b[1] - a[1], b[0] - a[0]) * 180 / Math.PI
  const off = (a, b) => Math.abs(((b - a) % 360 + 540) % 360 - 180)
  const spawner = (id) => p.evaluate((id) => window.__rl_planActors().spawners.find(s => s.id === id), id)
  const drawn = (id) => p.evaluate((id) => window.__rl_spawnerMarkers().find(m => m.id === id), id)
  // placed with no route: its yaw is left as it is, and a facing cone is drawn
  const sp = await p.evaluate(() => {
    const r = window.__rl_riftAt() || [0, 0, 0]
    window.__rl_place('spawner', [r[0] + 1500, r[1] + 1500, r[2] + 200])
    return window.__rl_planActors().spawners.slice(-1)[0]
  })
  const key = sp.kind === 'flyer' ? 'flyerStart' : 'start'
  let m = await drawn(sp.id)
  check(m && m.nose && sp.rotation === 0 && !sp.turned,
    `placed: yaw ${sp.rotation}, facing cone ${m && m.nose}`, log)
  // its first point placed: faces it
  const pointAt = [sp.at[0], sp.at[1] + 2000, sp.at[2]]
  await p.evaluate((at) => window.__rl_place('point', at), pointAt)
  let s = await spawner(sp.id)
  m = await drawn(sp.id)
  check(typeof s[key] === 'number' && off(s.rotation, 90) < 0.1 && m && off(m.yaw, s.rotation) < 0.1,
    `first point placed (${key} ${s[key]}): yaw ${s.rotation}, drawn ${m && m.yaw.toFixed(1)}`, log)
  // the point moved: faces it again
  await setField(p, '[data-at="0"]', Math.round(pointAt[0] - 2000))
  await sleep(300)
  s = await spawner(sp.id)
  check(off(s.rotation, 135) < 0.1, `first point moved: yaw ${s.rotation}`, log)
  // the route changed to the farthest level point offered: faces that
  await p.evaluate((id) => window.__rl_select('spawner', id), sp.id)
  await waitFor(p, 'the spawner panel', (k) => document.querySelector(`[data-route="${k}"]`),
    { timeout: 10000, polling: 100 }, key)
  const level = await p.evaluate((k, at) => {
    const names = [...document.querySelector(`[data-route="${k}"]`).options]
      .map(o => o.value).filter(v => v && !/^-?\d+$/.test(v))
    let best = null
    for (const n of names) {
      const to = window.__rl_pointAt(n)
      const d = to ? Math.hypot(to[0] - at[0], to[1] - at[1]) : -1
      if (to && (!best || d > best.d)) best = { n, to, d }
    }
    return best
  }, key, sp.at)
  check(level, `a level point to route to: ${level && level.n}`, log)
  await setField(p, `[data-route="${key}"]`, level.n)
  await sleep(300)
  s = await spawner(sp.id)
  const want = heading(sp.at, level.to)
  check(s[key] === level.n && off(s.rotation, want) < 0.1 && !s.turned,
    `route changed to ${level.n}: yaw ${s.rotation} (toward it ${want.toFixed(1)})`, log)
  // turned by hand in its yaw field: keeps it through a move of the spawner
  await setField(p, '[data-field="rotation"]', 200)
  await sleep(300)
  await setField(p, '[data-at="1"]', Math.round(sp.at[1] + 700))
  await sleep(300)
  s = await spawner(sp.id)
  m = await drawn(sp.id)
  check(s.turned && s.rotation === 200 && m && off(m.yaw, 200) < 0.1,
    `turned by hand to 200, then moved: yaw ${s.rotation}, turned ${s.turned}, drawn ${m && m.yaw.toFixed(1)}`, log)
  // a second spawner turned with the gizmo is marked turned too
  const sp2 = await p.evaluate(() => {
    const r = window.__rl_riftAt() || [0, 0, 0]
    window.__rl_place('spawner', [r[0] - 1500, r[1] + 1500, r[2] + 200])
    const made = window.__rl_planActors().spawners.slice(-1)[0]
    window.__rl_place('point', [made.at[0] + 2000, made.at[1], made.at[2]])
    window.__rl_select('spawner', made.id)
    return made
  })
  const aimed = await spawner(sp2.id)
  await p.evaluate(() => window.__rl_gizmoDrag('rotate', 'Z', [0.05, 0.05], [0.31, 0.17], true))
  await sleep(300)
  const turned = await spawner(sp2.id)
  check(off(aimed.rotation, 0) < 0.1 && !aimed.turned && turned.turned,
    `gizmo turn: aimed at ${aimed.rotation}, after the turn ${turned.rotation}, turned ${turned.turned}`, log)
  // a save keeps the flag on the turned spawners only
  const saved = await p.evaluate(async (ids) => {
    await window.__rl_savePlan()
    const plan = await (await fetch('api/plan/' + encodeURIComponent(window.__rl_levelName()))).json()
    return (plan.spawners || []).filter(s => ids.includes(s.id)).map(s => [s.id, !!s.turned, s.rotation])
  }, [sp.id, sp2.id])
  check(saved.length === 2 && saved.every(r => r[1]),
    `saved: ${JSON.stringify(saved)}`, log)
}

/** A second rift (actors.js riftExits): placed with its own exit point under it, the exit
 * moving with it; a route sent to it by its Rifts choice, the spawner's Ends At naming it;
 * a save keeping its `exit`; deleted, its exit point goes and the route ends at the level's
 * rift again. */
async function multiRift (p, urls, mission, log) {
  await openMission(p, urls, mission, log)
  const shots = process.env.RL_SHOTS
  const actors = () => p.evaluate(() => window.__rl_planActors())
  // placed 3000 uu from the level's rift: an exit point of its own under it
  const rift = await p.evaluate(() => {
    const r = window.__rl_riftAt() || [0, 0, 0]
    window.__rl_place('rift', [r[0] + 3000, r[1], r[2]])
    return window.__rl_planActors().rifts.slice(-1)[0]
  })
  await sleep(1500)
  let a = await actors()
  let exit = a.points.find(o => o.id === rift.exit)
  let own = await p.evaluate(() => window.__rl_riftExits())
  check(rift && typeof rift.exit === 'number' && exit && exit.at[0] === rift.at[0] &&
    exit.at[1] === rift.at[1] && exit.at[2] < rift.at[2] && own[rift.exit] === rift.id,
  `placed rift ${rift && rift.id}: exit point ${rift && rift.exit} at ${exit && exit.at}`, log)
  // the rift's panel names its exit point
  const named = await p.evaluate(() => document.querySelector('[data-riftexit]')?.value || null)
  check(named === String(rift.exit), `rift panel Exit Point: ${named}`, log)
  // moved by its X field: the exit point moves by as much
  const before = exit.at.slice()
  await setField(p, '[data-at="0"]', Math.round(rift.at[0] + 500))
  await sleep(500)
  a = await actors()
  const moved = a.rifts.find(r => r.id === rift.id)
  exit = a.points.find(o => o.id === rift.exit)
  check(Math.abs(exit.at[0] - before[0] - (moved.at[0] - rift.at[0])) < 0.01 &&
    Math.abs(exit.at[1] - before[1]) < 0.01,
  `rift moved ${(moved.at[0] - rift.at[0]).toFixed(0)} uu: exit ${before[0]} -> ${exit.at[0]}`, log)
  // the last point of an added route sent to the rift by its Rifts choice
  const last = a.points.filter(o => o.id !== rift.exit && !(o.next || []).length).slice(-1)[0]
  check(last, `a route end to send: point ${last && last.id}`, log)
  await p.evaluate((id) => window.__rl_select('point', id), last.id)
  await waitFor(p, 'the point panel', () => document.querySelector('#addbranch'),
    { timeout: 10000, polling: 100 })
  await p.evaluate(() => document.querySelector('#addbranch').click())
  await sleep(300)
  const offered = await p.evaluate(() => [...document.querySelectorAll('[data-branch="0"][data-part="to"] option')]
    .map(o => [o.value, o.textContent]))
  const choice = offered.find(([v]) => v === String(rift.exit))
  check(choice && choice[1] === 'Rift ' + rift.id, `branch choices offer ${JSON.stringify(choice)}`, log)
  await setField(p, '[data-branch="0"][data-part="to"]', String(rift.exit))
  await sleep(300)
  a = await actors()
  const sent = a.points.find(o => o.id === last.id)
  check((sent.next || []).length === 1 && sent.next[0].to === rift.exit,
    `point ${last.id} goes next to ${JSON.stringify(sent.next)}`, log)
  // the spawner whose route reaches it ends at the rift
  const sp = a.spawners.find(s => s.start !== null || s.flyerStart !== null)
  await p.evaluate((id) => window.__rl_select('spawner', id), sp.id)
  await waitFor(p, 'the spawner panel', () => document.querySelector('[data-endsat]'),
    { timeout: 10000, polling: 100 })
  const ends = await p.evaluate(() => document.querySelector('[data-endsat]').textContent)
  check(ends.split(', ').includes('Rift ' + rift.id), `spawner ${sp.id} Ends At: ${ends}`, log)
  if (shots) await p.screenshot({ path: `${shots}/multirift-spawner.png` })
  await p.evaluate((id) => window.__rl_select('rift', id), rift.id)
  await sleep(300)
  if (shots) await p.screenshot({ path: `${shots}/multirift-rift.png` })
  // a save keeps the rift's exit
  const saved = await p.evaluate(async () => {
    await window.__rl_savePlan()
    return (await fetch('api/plan/' + encodeURIComponent(window.__rl_levelName()))).json()
  })
  const kept = (saved.rifts || []).find(r => r.id === rift.id)
  check(kept && kept.exit === rift.exit, `saved rift: ${JSON.stringify(kept)}`, log)
  // deleted: its exit point goes, and the route ends at the level's rift again
  await p.evaluate(() => document.querySelector('#removesel').click())
  await sleep(300)
  a = await actors()
  const back = a.points.find(o => o.id === last.id)
  check(!a.rifts.some(r => r.id === rift.id) && !a.points.some(o => o.id === rift.exit) &&
    !(back.next || []).length, `rift deleted: points ${a.points.map(o => o.id)}, ` +
    `point ${last.id} next ${JSON.stringify(back.next)}`, log)
}

//: The scratch test mission's record (tests/fixtures/missions) and its kit floor tops
//: (makemissions.Z), far above its host North Wing's own floor.
const FLOORSNAP_RECORD = `${__dirname}/../fixtures/missions/.riftlord/records/${SCRATCH_MISSION}.json`
const FLOORSNAP_FLOOR_Z = 1000
//: Where the case places a path point: on that mission's kit corridor (makemissions TILES
//: tiles of 1200 uu along +y over x 0..1200), clear of its route; 150 uu over the floor,
//: inside navsurfextend.SURFACE_ABOVE (263 uu), so a build counts it as on that floor.
const FLOORSNAP_AT = [300, 4800, FLOORSNAP_FLOOR_Z + 150]
//: navsurfextend.POINT_ABOVE_FLOOR: a ground path point's height over its floor (uu).
const POINT_ABOVE_FLOOR = 10.8
//: The snap's rounding (riftfloor.snap_point rounds to 0.1 uu).
const SNAP_ROUNDING = 0.11

/** A ground path point on a scratch level stands on the plan's own floor, not the host
 * level's (riftfloor.plan_floor): placed over the scratch test mission's kit corridor, it
 * goes POINT_ABOVE_FLOOR over the corridor (the host's graph has no floor there), and given
 * a new height in its Position field it goes back. The mission is copied into the test
 * server's Missions and taken out again. */
async function floorSnap (p, urls, mission, log) {
  if (!process.env.RL_MISSIONS) throw new Error('floorsnap needs the scratch Missions of testservers.py (RL_MISSIONS)')
  const records = `${process.env.RL_MISSIONS}/.riftlord/records`
  fs.mkdirSync(records, { recursive: true })
  fs.copyFileSync(FLOORSNAP_RECORD, `${records}/${SCRATCH_MISSION}.json`)
  try {
    await openMission(p, urls, SCRATCH_MISSION, log)
    const point = await p.evaluate((at) => {
      window.__rl_deselect()
      window.__rl_place('point', at)
      return window.__rl_planActors().points.slice(-1)[0]
    }, FLOORSNAP_AT)
    // the snap without a plan: the host level's own graph, as before the plan's floor counted
    const host = await p.evaluate(async (at) => (await fetch('api/riftfloor/' +
      encodeURIComponent(window.__rl_levelName()) + `?at=${at.join(',')}&for=point`)).json(), FLOORSNAP_AT)
    check(!host.at, `the host level's own graph has no floor there: ${JSON.stringify(host)}`, log)
    const settled = async () => {
      let a
      for (let i = 0; i < 50; i++) {
        a = (await p.evaluate(() => window.__rl_planActors())).points.find(o => o.id === point.id)
        if (a && a.at[2] !== FLOORSNAP_AT[2]) return a
        await sleep(100)
      }
      return a
    }
    let got = await settled()
    const over = got.at[2] - FLOORSNAP_FLOOR_Z - POINT_ABOVE_FLOOR
    check(over > -SNAP_ROUNDING && over < FLOORSNAP_AT[2] - FLOORSNAP_FLOOR_Z - POINT_ABOVE_FLOOR,
      `placed at z ${FLOORSNAP_AT[2]}: snapped to ${got.at[2]} on the kit corridor (top ${FLOORSNAP_FLOOR_Z}+)`, log)
    const snapped = got.at[2]
    await setField(p, '[data-at="2"]', FLOORSNAP_AT[2])
    got = await settled()
    check(Math.abs(got.at[2] - snapped) < SNAP_ROUNDING,
      `Position Z typed ${FLOORSNAP_AT[2]}: back to ${got.at[2]}`, log)
    if (process.env.RL_SHOTS) await p.screenshot({ path: `${process.env.RL_SHOTS}/floorsnap.png` })
  } finally {
    fs.rmSync(`${records}/${SCRATCH_MISSION}.json`, { force: true })
  }
}

/** The Missions tab asks before opening a mission in an editor only when that editor has
 * a level open: none with nothing open, none with the Map Editor's level unchanged since
 * it opened, asked with changes not saved (Cancel keeps it); the Onslaught Editor the
 * same with nothing open, asked once a mission is open in it. */
async function openWarn (p, urls, mission, log) {
  const asked = []
  p.on('dialog', (d) => { asked.push(d.message().split('\n')[0]); d.dismiss().catch(() => {}) })
  await openShell(p, urls)
  await currentBuild(p, mission, log)
  const frameOf = (base) => p.frames().find(f => f.url().startsWith(base + '/'))
  const rowButton = (title) => p.evaluate((mission, title) => {
    const rows = [...document.querySelectorAll('.row')]
    const row = rows.find(r => r.querySelector('small')?.textContent.trim() === mission) ||
      rows.find(r => r.querySelector(`button[title^="${title}"]:not(:disabled)`))
    const b = row && row.querySelector(`button[title^="${title}"]`)
    if (b && !b.disabled) b.click()
    return !!(b && !b.disabled)
  }, mission, title)
  //: the editors' tabs are disabled until the shell's /api/tools answers
  const tab = async (label) => {
    await waitFor(p, `the ${label} tab enabled`, (l) => document.querySelector(`button.tab[aria-label="${l}"]:not(:disabled)`),
      { timeout: 60000, polling: 200 }, label)
    await p.evaluate((l) => document.querySelector(`button.tab[aria-label="${l}"]`).click(), label)
  }
  const frameSrc = (tool) => p.evaluate((t) => document.querySelector(`iframe[data-tool="${t}"]`)?.src || null, tool)
  //: [what was asked, the tool's new frame address or null]: waits for the shell to either
  //: ask or reopen the tool's frame (it does one or the other)
  const openFrom = async (title, what, tool) => {
    await tab('Missions')
    const n = asked.length
    const was = await frameSrc(tool)
    check(await rowButton(title), `the mission's ${what} button`, log)
    let src = was
    for (const end = Date.now() + 30000 * SCALE; asked.length === n && src === was && Date.now() < end; await sleep(200)) {
      src = await frameSrc(tool)
    }
    return [asked.slice(n), src === was ? null : src]
  }
  //: the frame of the tool at `base` once it has loaded `src`, the address the shell gave it
  const frameAt = async (base, src) => {
    const want = src && new URL(src).searchParams.get('open')
    let f = null
    for (const end = Date.now() + 60000 * SCALE; !f && Date.now() < end; await sleep(200)) {
      f = p.frames().find(x => x.url().startsWith(base + '/') && new URL(x.url()).searchParams.get('open') === want)
    }
    return f
  }
  const said = (got) => got.length ? `asked "${got[0]}"` : 'not asked'
  const levelOpen = async (src) => {
    const f = await frameAt(urls.riftlord, src)
    check(f && /[?&]bucket=/.test(f.url()), `the Map Editor frame opened on the mission (${f && f.url().slice(0, 120)})`, log)
    await waitFor(f, 'the mission open in the Map Editor', () => window.__rl_levelOpen && window.__rl_levelOpen() &&
      document.querySelector('#loadmodal')?.hidden !== false, { timeout: 540000, polling: 1000 })
    return f
  }

  // Map Editor visited, nothing open: not asked
  await tab('Map Editor')
  let f = null
  for (let t = Date.now(); !f && Date.now() - t < 30000 * SCALE; await sleep(200)) f = frameOf(urls.riftlord)
  check(!!f, 'the Map Editor frame', log)
  await waitFor(f, 'the Map Editor ready', () => window.__rl_ready, { timeout: 120000, polling: 250 })
  const [none, first] = await openFrom('Edit the map', 'Map', 'riftlord')
  check(none.length === 0, `Map with nothing open in the Map Editor: ${said(none)}`, log)
  await levelOpen(first)

  // open with no changes: not asked
  const [unchanged, second] = await openFrom('Edit the map', 'Map', 'riftlord')
  check(unchanged.length === 0, `Map with the mission open and unchanged: ${said(unchanged)}`, log)
  f = await levelOpen(second)

  // a change not saved: asked, and Cancel keeps the level
  const at = await f.evaluate(() => window.__rl_riftAt() || [0, 0, 0])
  const group = await f.evaluate((at) => window.__rl_compoundPlace('stairs_with_railings', [at[0] + 900, at[1] + 900, at[2]]), at)
  check(!!group, `a compound placed (group ${group})`, log)
  const before = f.url()
  const [changed] = await openFrom('Edit the map', 'Map', 'riftlord')
  check(changed.length === 1 && /^Open /.test(changed[0]), `Map with changes not saved: ${said(changed)}`, log)
  check(frameOf(urls.riftlord) && frameOf(urls.riftlord).url() === before, 'Cancel keeps the Map Editor as it was', log)

  // Onslaught Editor visited, nothing open: not asked; a mission open in it: asked
  await tab('Onslaught Editor')
  await waitFor(p, 'the Onslaught Editor frame', (u) => [...document.querySelectorAll('iframe')].some(f => f.src.startsWith(u)),
    { timeout: 30000, polling: 200 }, urls.onslaught)
  await sleep(3000)
  const [idle, waves] = await openFrom('Edit the waves', 'Waves', 'onslaught')
  check(idle.length === 0, `Waves with nothing open in the Onslaught Editor: ${said(idle)}`, log)
  const o = await frameAt(urls.onslaught, waves)
  check(o && /[?&]bucket=/.test(o.url()), 'the Onslaught Editor frame opened on the mission', log)
  await waitFor(o, 'the mission\'s waves in the Onslaught Editor', () => /Wave \d+/.test(document.body.innerText),
    { timeout: 180000, polling: 500 })
  const [open] = await openFrom('Edit the waves', 'Waves', 'onslaught')
  check(open.length === 1 && /^Open /.test(open[0]), `Waves with a mission open: ${said(open)}`, log)
}

//: How long currentBuild waits for a test mission's build: a Fixture Edit bake took 106-156 s
//: on a quiet machine (2026-10-30), so four times that under a gate's load.
const BUILD_WAIT = 600000

/** Build `mission` on the Missions page's Build when an earlier case left it out of date
 * (a new campaign, say, is a bake input): opening it in an editor then bakes it first, and a
 * case timing an open would time that bake. Waits for the build job (BUILD_WAIT). */
async function currentBuild (p, mission, log) {
  const state = async () => (await p.evaluate(async () => (await (await fetch('api/bucket')).json())))
    .items.find(i => i.id === mission)?.state
  const was = await state()
  if (was === 'built') return
  await p.evaluate(async (id) => fetch('api/bucket/build', { method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Workshop': '1' }, body: JSON.stringify({ ids: [id] }) }), mission)
  await waitFor(p, `${mission} built`, async () => {
    const st = await (await fetch('api/bucket/build')).json()
    return !(st.job && st.job.running)
  }, { timeout: BUILD_WAIT, polling: 1000 })
  const now = await state()
  check(now === 'built', `${mission} was ${was}: built first (${now})`, log)
}

/** Campaign Index and Working Title: in Mission Settings, Working Title refused for a shipped
 * level's name and for a bad character, Campaign Index shown for a custom campaign and refused
 * out of range (each with its red reason, Save disabled), both saved; then on the Missions
 * page the campaign's row takes a new index (refused out of range, then moved) and the
 * campaign's MissionList follows. The mission goes back to Custom Maps with its Working Title.
 * Screenshots into RL_SHOTS as campaignindex-*.png. */
async function campaignIndex (p, urls, mission, log) {
  const shots = process.env.RL_SHOTS
  const api = (path) => p.evaluate(async (path) => (await (await fetch(path)).json()), path)
  const post = (path, body) => p.evaluate(async (path, body) => (await (await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Workshop': '1' },
    body: JSON.stringify(body) })).json()), path, body)
  await openShell(p, urls)
  //: the fixture campaign (it lists Fixture Scratch, so it takes an index for this mission),
  //: copied in unless it is there: a campaign an earlier case made may list only this mission
  const path = require('path')
  let copied = null
  const listed = await api('api/bucket')
  const name = '900_FixtureCampaign_Campaign_P.pak'
  if (!listed.campaigns.some(c => c.source === name)) {
    copied = path.join(listed.root, name)
    fs.copyFileSync(path.join(__dirname, '..', 'fixtures', 'missions', name), copied)
  }
  const was = (await api('api/bucket/settings?id=' + encodeURIComponent(mission))).placement
  try {
    await campaignIndexSteps(p, mission, log, shots, api, post)
  } finally {
    //: a campaign left with no missions when this one moved out was renamed .disabled
    //: (customcampaign.place): named back, and the mission listed there again
    for (const f of fs.readdirSync(listed.root)) {
      if (f.endsWith('.disabled') && listed.campaigns.some(c => f.startsWith(c.source))) {
        fs.renameSync(path.join(listed.root, f), path.join(listed.root, f.slice(0, -'.disabled'.length)))
      }
    }
    if (was && was !== 'CustomMaps') {
      const back = await post('api/bucket/settings', { id: mission, placement: was })
      check(!back.error, `back in ${was} ${back.error || ''}`, log)
    }
    //: the copy, and the backups its rewrites kept beside it
    if (copied) {
      for (const f of fs.readdirSync(path.dirname(copied))) {
        if (f.startsWith(path.basename(copied))) fs.rmSync(path.join(path.dirname(copied), f), { force: true, recursive: true })
      }
    }
  }
}

async function campaignIndexSteps (p, mission, log, shots, api, post) {
  const typeInto = (sel, text) => p.evaluate((sel, text) => {
    const input = document.querySelector(sel)
    input.value = text
    input.dispatchEvent(new Event('input', { bubbles: true }))
  }, sel, text)
  const errors = () => p.evaluate(() => [...document.querySelectorAll('.md-modal .md-body .md-error')]
    .map(e => e.textContent.trim()))
  const saveOff = () => p.evaluate(() => document.querySelector('.md-modal .md-primary').disabled)
  //: the dialog with `sel` scrolled into view, into RL_SHOTS (the Setup pane closed first)
  const shot = async (name, sel) => {
    if (!shots) return
    await p.evaluate((sel) => {
      document.querySelector('section.setup button.close')?.click()
      document.querySelector(sel)?.scrollIntoView({ block: 'center' })
    }, sel)
    await sleep(250)
    await (await p.$('.md-modal')).screenshot({ path: `${shots}/campaignindex-${name}.png` })
  }
  const before = await api('api/bucket/settings?id=' + encodeURIComponent(mission))
  const item = (await api('api/bucket')).items.find(i => i.id === mission)
  check(before.workingTitle && item, `the mission has a level of its own (${before.workingTitle?.default})`, log)
  //: a campaign that does not list the mission and takes an index for it (1 to 2 or more)
  const camp = before.choices.find(c => c.kind === 'custom' && c.index == null && c.size > c.first)
  check(camp, 'an installed custom campaign to list it in', log)
  //: one listing only this mission takes no index: the field is not shown
  const alone = before.choices.find(c => c.kind === 'custom' && c.index === 1 && c.size === 1)
  await p.reload({ waitUntil: 'domcontentloaded' })
  await waitFor(p, 'the Missions list', () => document.querySelector('button[title^="Mission Settings"]'),
    { timeout: 60000, polling: 250 })
  await p.evaluate((title) => {
    const row = [...document.querySelectorAll('.row')].find(r => r.querySelector('.name b')?.textContent.trim() === title)
    row.querySelector('button[title^="Mission Settings"]').click()
  }, item.title)
  await waitFor(p, 'the Mission Settings dialog', () => document.querySelector('.md-modal input[aria-label="Working Title"]'),
    { timeout: 60000, polling: 250 })
  const field = await p.evaluate(() => {
    const input = document.querySelector('.md-modal input[aria-label="Working Title"]')
    return { value: input.value, placeholder: input.placeholder }
  })
  check(field.value === before.workingTitle.value && field.placeholder === before.workingTitle.default,
    `Working Title shows the level's own name, ${field.value}, and the default, ${field.placeholder}`, log)
  const wt = '.md-modal input[aria-label="Working Title"]'
  await typeInto(wt, 'Scenario_10')
  await sleep(150)
  let errs = await errors()
  check(errs.includes('Scenario_10 is a shipped level.') && await saveOff(), `a shipped level's name refused: ${errs.join(' | ')}`, log)
  await shot('workingtitle-refused', wt)
  await typeInto(wt, 'fear itself')
  await sleep(150)
  errs = await errors()
  check(errs.some(e => /letters, digits and underscores/.test(e)), `a space refused: ${errs.join(' | ')}`, log)
  await typeInto(wt, 'scenario_fear_01')
  await sleep(150)
  check(!(await errors()).length && !(await saveOff()), 'scenario_fear_01 taken', log)
  const pick = (name) => p.evaluate((name) => {
    const s = document.querySelector('.md-modal select[aria-label="Campaign"]')
    s.value = name
    s.dispatchEvent(new Event('change', { bubbles: true }))
  }, name)
  const ci = '.md-modal input[aria-label="Campaign Index"]'
  if (alone) {
    await pick(alone.name)
    await sleep(300)
    check(!(await p.evaluate((ci) => !!document.querySelector(ci), ci)),
      `no Campaign Index for ${alone.label}, which lists only this mission`, log)
  }
  await pick(camp.name)
  await waitFor(p, 'the Campaign Index field', (ci) => document.querySelector(ci), { timeout: 10000, polling: 100 }, ci)
  const shown = await p.evaluate((ci) => document.querySelector(ci).value, ci)
  check(shown === String(camp.size), `Campaign Index starts last (${shown} of ${camp.size})`, log)
  await typeInto(ci, String(camp.size + 1))
  await sleep(150)
  errs = await errors()
  check(errs.includes(`Campaign Index is a whole number from 1 to ${camp.size}.`) && await saveOff(),
    `an index past the end refused: ${errs.join(' | ')}`, log)
  await shot('index-refused', ci)
  await typeInto(ci, '1')
  await sleep(150)
  check(!(await errors()).length && !(await saveOff()), 'index 1 taken', log)
  await shot('settings', ci)
  await p.evaluate(() => document.querySelector('.md-modal .md-primary').click())
  await waitFor(p, 'the Mission Settings dialog to close after saving', () => !document.querySelector('.md-modal'),
    { timeout: 120000, polling: 250 })
  let after = (await api('api/bucket')).items.find(i => i.id === mission)
  const saved = await api('api/bucket/settings?id=' + encodeURIComponent(mission))
  check(after.campaign && camp.name.endsWith(after.campaign) && after.campaignIndex === 1,
    `listed first in ${after.campaignTitle} (${after.campaignIndex})`, log)
  check(saved.workingTitle.value === 'scenario_fear_01', `Working Title saved: ${saved.workingTitle.value}`, log)
  const rowSel = `input[aria-label="Campaign Index of ${item.title}"]`
  await waitFor(p, 'the row\'s Campaign Index on the Missions page', (sel) => document.querySelector(sel)?.value === '1',
    { timeout: 30000, polling: 250 }, rowSel)
  const count = (await api('api/bucket')).campaigns.find(c => camp.name.endsWith(c.key)).missions.length
  await typeInto(rowSel, String(count + 1))
  await sleep(150)
  const rowErr = await p.evaluate((sel) => document.querySelector(sel).closest('.name').querySelector('small.bad')?.textContent.trim(), rowSel)
  check(rowErr === `Campaign Index is a whole number from 1 to ${count}.`, `the row refuses ${count + 1}: ${rowErr}`, log)
  if (shots) {
    await p.evaluate(() => document.querySelector('section.setup button.close')?.click())
    await sleep(250)
    await p.screenshot({ path: `${shots}/campaignindex-missions.png` })
  }
  await typeInto(rowSel, '2')
  await p.evaluate((sel) => document.querySelector(sel).dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })), rowSel)
  await waitFor(p, 'the mission moved to 2', async (id) => {
    const got = (await (await fetch('api/bucket')).json()).items.find(i => i.id === id)
    return got && got.campaignIndex === 2
  }, { timeout: 60000, polling: 500 }, mission)
  const order = (await api('api/bucket')).campaigns.find(c => camp.name.endsWith(c.key)).missions
  after = (await api('api/bucket')).items.find(i => i.id === mission)
  check(after.guids.some(g => order[1] === g), `the campaign's MissionList has it second (${order.length} missions)`, log)
  const back = await post('api/bucket/settings', { id: mission, placement: 'CustomMaps',
    workingTitle: before.workingTitle.value })
  check(!back.error, `back to Custom Maps with its Working Title, ${before.workingTitle.value} ${back.error || ''}`, log)
}

// -- same title, Campaign Endless (riftlord/missions/bucket.py) ------------------------------

/** Same title: a copy of the mission saved under its own title (the Map Editor's Save As) is
 * a second row on the Missions page, told apart by the Working Title column (the levels'
 * names: the mission's own, and RL_Scen_<Name> for the copy, with a letter suffix if taken). The copy's
 * Mission Settings offer Campaign Endless beside Add to Endless; Campaign Endless hides Add to
 * Endless and Campaign Index, and saved, the copy is listed under Endless. The copy is removed.
 * Screenshots into RL_SHOTS as samename-*.png. */
async function sameName (p, urls, mission, log) {
  const shots = process.env.RL_SHOTS
  const api = (path) => p.evaluate(async (path) => (await (await fetch(path)).json()), path)
  await openShell(p, urls)
  const first = (await api('api/bucket')).items.find(i => i.id === mission)
  check(first && first.workingTitle, `the mission's level: ${first && first.workingTitle}`, log)
  const made = await (await fetch(`${urls.riftlord}/api/saveas/${encodeURIComponent(first.level)}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Rift-Lord': '1' },
    body: JSON.stringify({ mission, title: first.title }) })).json()
  const copy = made && made.mission
  check(copy && copy !== mission && made.title === first.title,
    `saved as ${copy}, titled "${made && made.title}" as the mission is`, log)
  try {
    await sameNameSteps(p, mission, copy, first, log, shots, api)
  } finally {
    if (copy) {
      const r = await p.evaluate(async (id) => (await fetch('api/bucket/remove', { method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Workshop': '1' },
        body: JSON.stringify({ ids: [id] }) })).ok, copy)
      check(r, `${copy} removed`, log)
    }
  }
}

async function sameNameSteps (p, mission, copy, first, log, shots, api) {
  await p.reload({ waitUntil: 'domcontentloaded' })
  await waitFor(p, 'both rows on the Missions page', (t) =>
    [...document.querySelectorAll('.row .name b')].filter(b => b.textContent.trim() === t).length === 2,
  { timeout: 60000, polling: 250 }, first.title)
  const rows = await p.evaluate((t) => ({
    head: [...document.querySelectorAll('.row.head span')].map(s => s.textContent.trim()),
    titles: [...document.querySelectorAll('.row')].filter(r => r.querySelector('.name b')?.textContent.trim() === t)
      .map(r => r.querySelector('.wtitle')?.textContent.trim()) }), first.title)
  const second = (await api('api/bucket')).items.find(i => i.id === copy)
  check(rows.head.includes('Working Title') && !rows.head.includes('Level'), `columns: ${rows.head.join(', ')}`, log)
  check(second && /^RL_Scen_[A-Za-z0-9]+$/.test(second.workingTitle) && second.workingTitle !== first.workingTitle &&
    rows.titles.includes(first.workingTitle) && rows.titles.includes(second.workingTitle),
  `two rows titled "${first.title}": ${rows.titles.join(' / ')}`, log)
  if (shots) {
    await p.evaluate(() => document.querySelector('section.setup button.close')?.click())
    await sleep(250)
    await p.screenshot({ path: `${shots}/samename-missions.png` })
  }
  await p.evaluate((wt) => {
    const row = [...document.querySelectorAll('.row')].find(r => r.querySelector('.wtitle')?.textContent.trim() === wt)
    row.querySelector('button[title^="Mission Settings"]').click()
  }, second.workingTitle)
  const campSel = '.md-modal select[aria-label="Campaign"]'
  await waitFor(p, 'the Campaign picker', (sel) => document.querySelector(sel), { timeout: 60000, polling: 250 }, campSel)
  const offered = await p.evaluate((sel) => [...document.querySelector(sel).options].map(o => o.value), campSel)
  const endlessBox = () => p.evaluate(() => [...document.querySelectorAll('.md-modal label.check')]
    .some(l => l.textContent.trim() === 'Add to Endless'))
  check(offered.includes('Endless') && await endlessBox(), `Campaign offers Endless (${offered.join(', ')}), and Add to Endless is there`, log)
  await p.evaluate((sel) => {
    const s = document.querySelector(sel)
    s.value = 'Endless'
    s.dispatchEvent(new Event('change', { bubbles: true }))
  }, campSel)
  await sleep(200)
  const index = await p.evaluate(() => !!document.querySelector('.md-modal input[aria-label="Campaign Index"]'))
  check(!index && !(await endlessBox()), 'Campaign Endless hides Add to Endless and Campaign Index', log)
  if (shots) {
    await p.evaluate((sel) => document.querySelector(sel)?.scrollIntoView({ block: 'center' }), campSel)
    await sleep(250)
    await (await p.$('.md-modal')).screenshot({ path: `${shots}/samename-endless-campaign.png` })
  }
  await p.evaluate(() => document.querySelector('.md-modal .md-primary').click())
  await waitFor(p, 'the Mission Settings dialog to close after saving', () => !document.querySelector('.md-modal'),
    { timeout: 120000, polling: 250 })
  const after = (await api('api/bucket')).items.find(i => i.id === copy)
  const settings = await api('api/bucket/settings?id=' + encodeURIComponent(copy))
  check(after.endlessOnly && settings.endlessOnly && settings.placement === 'Endless' &&
    ['stale', 'unbuilt'].includes(after.state), `saved with Campaign Endless, ${after.state}`, log)
  await waitFor(p, 'the Endless group', () => [...document.querySelectorAll('.group-head h3')]
    .some(h => h.textContent.trim() === 'Endless'), { timeout: 30000, polling: 250 })
  const group = await p.evaluate((wt) => {
    let head = null
    for (const el of document.querySelectorAll('.group-head, .row:not(.head)')) {
      if (el.classList.contains('group-head')) head = el.querySelector('h3').textContent.trim()
      else if (el.querySelector('.wtitle')?.textContent.trim() === wt) return head
    }
    return null
  }, second.workingTitle)
  check(group === 'Endless', `the copy is listed under ${group}`, log)
  const firstAfter = (await api('api/bucket')).items.find(i => i.id === mission)
  check(!firstAfter.endlessOnly, 'the mission it was copied from is unchanged', log)
  if (shots) {
    await p.screenshot({ path: `${shots}/samename-endless.png` })
  }
}

/** A custom campaign is one pak: with the mission listed in the fixture campaign (copied in),
 * the campaign's group on the Missions page has its own Game state, Install Campaign and
 * Package Campaign, and the mission's row keeps its own Install and Package. The mission
 * goes back to where it was. Screenshot into RL_SHOTS as campaignpak-missions.png. */
async function campaignPak (p, urls, mission, log) {
  const shots = process.env.RL_SHOTS
  const api = (path) => p.evaluate(async (path) => (await (await fetch(path)).json()), path)
  const post = (path, body) => p.evaluate(async (path, body) => (await (await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Workshop': '1' },
    body: JSON.stringify(body) })).json()), path, body)
  await openShell(p, urls)
  const path = require('path')
  const listed = await api('api/bucket')
  const name = '900_FixtureCampaign_Campaign_P.pak'
  let copied = null
  if (!listed.campaigns.some(c => c.source === name)) {
    copied = path.join(listed.root, name)
    fs.copyFileSync(path.join(__dirname, '..', 'fixtures', 'missions', name), copied)
  }
  const was = (await api('api/bucket/settings?id=' + encodeURIComponent(mission))).placement
  try {
    const camp = (await api('api/bucket')).campaigns.find(c => c.source === name)
    const put = await post('api/bucket/settings', { id: mission, placement: 'custom:' + camp.key })
    check(!put.error, `listed in ${camp.title} ${put.error || ''}`, log)
    await p.reload({ waitUntil: 'domcontentloaded' })
    await waitFor(p, 'the campaign\'s Install Campaign', (t) => document.querySelector(`button[aria-label="Install Campaign ${t}"]`),
      { timeout: 60000, polling: 250 }, camp.title)
    const item = (await api('api/bucket')).items.find(i => i.id === mission)
    const got = await p.evaluate((t, title) => {
      const head = [...document.querySelectorAll('.group-head')].find(h => h.querySelector('h3')?.textContent.trim() === t)
      const row = [...document.querySelectorAll('.row')].find(r => r.querySelector('.name b')?.textContent.trim() === title)
      const hidden = (b) => !b || getComputedStyle(b).visibility === 'hidden'
      return { state: head?.querySelector('[data-campaign-state]')?.textContent.trim(),
               pack: !!head?.querySelector(`button[aria-label="Package Campaign ${t}"]`),
               //: the row's Install is there, shown once the mission is built (blocksPacking)
               rowInstall: !row?.querySelector('button[title="Install Mission"]'),
               rowPackage: hidden(row?.querySelector('button[title="Package Mission"]')) }
    }, camp.title, item.title)
    const entry = (await api('api/bucket')).campaigns.find(c => c.source === name)
    check(got.state && got.pack && entry.members.includes(mission),
      `the campaign is the installed unit: ${got.state}, ${entry.members.length} missions`, log)
    check(!got.rowInstall && !got.rowPackage, 'the mission\'s row keeps Install and Package of its own', log)
    if (shots) {
      await p.evaluate(() => document.querySelector('section.setup button.close')?.click())
      await sleep(250)
      await p.screenshot({ path: `${shots}/campaignpak-missions.png` })
    }
  } finally {
    //: the campaign it was in (an earlier case's) was renamed .disabled when it left it last
    //: (customcampaign.place); listing it there again brings it back (customcampaign._revive)
    const back = await post('api/bucket/settings', { id: mission, placement: was || 'CustomMaps' })
    check(!back.error, `back in ${was} ${back.error || ''}`, log)
    for (const f of fs.readdirSync(listed.root)) {
      if (f.endsWith('.disabled') && f.startsWith(name)) {
        fs.renameSync(path.join(listed.root, f), path.join(listed.root, f.slice(0, -'.disabled'.length)))
      }
    }
    if (copied) {
      for (const f of fs.readdirSync(path.dirname(copied))) {
        if (f.startsWith(path.basename(copied))) fs.rmSync(path.join(path.dirname(copied), f), { force: true, recursive: true })
      }
    }
  }
}

// -- dialogue lines (Onslaught Editor Timeline, riftlord/build/world/captions.py) ------------

//: The lines the case writes: [when, wave, delay, speaker, text, length ('' = from the text)].
const DIALOGUE_LINES = [['start', '1', '2', 'Egan', 'Here they come. Hold the line!', ''],
  ['after', '1', '0.5', 'Kelsey', 'Traps first, heroics second.', '3'],
  ['break', '1', '0', 'Egan', 'That was the easy part.', '']]
//: The level the case's mission is made on (as the Crystal cases make theirs).
const DIALOGUE_LEVEL = 'Scenario_02'
//: lineLength (spec.js) of the first line's text: 1.274 + 0.0596 x 30 characters.
const DIALOGUE_FIRST_LENGTH = '3.1'

/** The Timeline's Dialogue section on a copy of the test mission with the Mission Events
 * piece: blocked without it; + Line adds rows; an empty line is marked and listed as an
 * error; the first line cannot follow another; lines are filled, reordered and one removed;
 * the length placeholder follows the text; Save Mission keeps them in the plan in order and
 * a reopened editor shows them again. RL_SHOTS keeps dialogue-timeline.png. */
async function dialogue (p, urls, mission, log) {
  if (!process.env.RL_MISSIONS) throw new Error('dialogue needs the scratch Missions of testservers.py (RL_MISSIONS)')
  //: a new mission on DIALOGUE_LEVEL with the Mission Events piece and no line yet
  await openLevelNamed(p, urls, DIALOGUE_LEVEL, log)
  const title = 'Smoke Dialogue ' + Date.now().toString(36)
  const made = await p.evaluate(async (n, t) => {
    await window.__rl_savePlan()
    const plan = await (await fetch('api/plan/' + n)).json()
    plan.added = [...(plan.added || []).filter(a => a.kind !== 'events'),
      { name: 'Mission Events', kind: 'events', matrix: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1] }]
    plan.events = (plan.events || []).filter(e => e.kind !== 'dialogue')
    const posted = await fetch('api/plan/' + n, { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Rift-Lord': '1' }, body: JSON.stringify(plan) })
    if (!posted.ok) return { error: await posted.text() }
    const r = await fetch('api/saveas/' + encodeURIComponent(n), { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Rift-Lord': '1' }, body: JSON.stringify({ title: t }) })
    const got = await r.json()
    const item = await (await fetch('api/bucket/item/' + encodeURIComponent(got.mission))).json()
    return { mission: got.mission, piece: ((item.plan || {}).added || []).some(a => a.kind === 'events'),
             dialogue: (item.eventCaps || {}).dialogue }
  }, DIALOGUE_LEVEL, title)
  const id = made && made.mission
  check(!!id && made.piece && made.dialogue && made.dialogue.available,
    `saved as ${id} titled "${title}" with the Mission Events piece (dialogue ${JSON.stringify(made.dialogue || made.error)})`, log)

  const open = async () => {
    await p.goto(`${urls.onslaught}/?embedded=1&theme=dark&bucket=${encodeURIComponent(id)}&open=1`,
      { waitUntil: 'domcontentloaded', timeout: 60000 })
    await waitFor(p, 'the Timeline\'s Dialogue section and the waves', () =>
      document.querySelector('.timeline section.dialogue') &&
      document.querySelector('.timeline > section[id^="wave-"]') && !document.querySelector('.loading'),
    { timeout: CRYSTAL_BAKE_WAIT, polling: 1000 })
    await sleep(500)
    const add = await p.evaluate(() => {
      const sec = document.querySelector('.timeline section.dialogue')
      const b = [...sec.querySelectorAll('button')].find(x => x.textContent.trim() === '+ Line')
      return { enabled: !!b && !b.disabled, blocked: sec.querySelector('.blocked')?.textContent || '' }
    })
    check(add.enabled && !add.blocked, `+ Line enabled${add.blocked ? ` (blocked: ${add.blocked})` : ''}`, log)
    if (!add.enabled) throw new Error('+ Line is disabled')
  }
  await open()
  const rows = () => p.evaluate(() => [...document.querySelectorAll('.timeline section.dialogue .line')].map(r => ({
    when: r.querySelector('select[aria-label$="plays"]').value,
    wave: r.querySelector('input[aria-label$="wave"]')?.value ?? null,
    delay: r.querySelector('input[aria-label$="delay"]').value,
    speaker: r.querySelector('input[aria-label$="speaker"]').value,
    text: r.querySelector('input[aria-label$="text"]').value,
    length: r.querySelector('input[aria-label$="length"]').value,
    placeholder: r.querySelector('input[aria-label$="length"]').placeholder,
    bad: [...r.querySelectorAll('.bad')].map(x => x.getAttribute('aria-label')),
    afterOff: !!r.querySelector('select[aria-label$="plays"] option[value="after"]').disabled })))
  const addLine = () => p.evaluate(() => [...document.querySelectorAll('.timeline section.dialogue button')]
    .find(b => b.textContent.trim() === '+ Line').click())
  for (let i = 0; i < DIALOGUE_LINES.length + 1; i++) { await addLine(); await sleep(150) }
  let got = await rows()
  const issues = await p.evaluate(async () => {
    document.querySelector('button.issues')?.click()
    await new Promise(r => setTimeout(r, 300))
    const text = document.querySelector('.modal[aria-label="Errors and warnings"]')?.innerText || ''
    document.querySelector('.modal[aria-label="Errors and warnings"] button[aria-label="Close"]')?.click()
    return text
  })
  check(got.length === DIALOGUE_LINES.length + 1 && got[0].when === 'start' && got[0].afterOff &&
    got[1].when === 'after' && got.every(r => r.bad.some(b => /text$/.test(b))) && /Dialogue line 1/.test(issues),
  `+ Line x${got.length}: first ${got[0].when} (after disabled ${got[0].afterOff}), next ${got[1].when}; empty text marked and listed`, log)
  const set = (sel, v, ev) => p.evaluate((s, value, e) => {
    const el = document.querySelector(s)
    if (!el) return false
    el.value = value
    el.dispatchEvent(new Event(e, { bubbles: true }))
    return true
  }, sel, v, ev)
  //: rows 2..4 filled with the case's lines, row 1 kept to be removed
  for (let k = 0; k < DIALOGUE_LINES.length; k++) {
    const [when, wave, delay, speaker, text, length] = DIALOGUE_LINES[k]
    const n = k + 2
    await set(`select[aria-label="Line ${n} plays"]`, when, 'change')
    await sleep(100)
    if (when !== 'after') await set(`input[aria-label="Line ${n} wave"]`, wave, 'input')
    await set(`input[aria-label="Line ${n} delay"]`, delay, 'change')
    await set(`input[aria-label="Line ${n} speaker"]`, speaker, 'input')
    await set(`input[aria-label="Line ${n} text"]`, text, 'input')
    if (length) await set(`input[aria-label="Line ${n} length"]`, length, 'change')
  }
  await sleep(200)
  //: the empty first row removed; the third line moved up and back down
  await clickIn(p, 'button[aria-label="Remove line 1"]')
  await sleep(200)
  await clickIn(p, 'button[aria-label="Move line 3 up"]')
  await sleep(200)
  got = await rows()
  check(got[1].text === DIALOGUE_LINES[2][4] && got[2].text === DIALOGUE_LINES[1][4],
    `Move up: ${got.map(r => r.text.slice(0, 12)).join(' | ')}`, log)
  await clickIn(p, 'button[aria-label="Move line 2 down"]')
  await sleep(200)
  got = await rows()
  const want = DIALOGUE_LINES.map(([when, wave, delay, speaker, text, length]) => [when, delay, speaker, text, length].join('/'))
  check(JSON.stringify(got.map(r => [r.when, r.delay, r.speaker, r.text, r.length].join('/'))) === JSON.stringify(want) &&
    got.every(r => !r.bad.length) && got[0].placeholder === DIALOGUE_FIRST_LENGTH,
  `lines: ${got.map(r => `${r.when} ${r.wave ?? '-'} +${r.delay}s ${r.speaker}: ${r.text} (${r.length || r.placeholder} s)`).join('; ')}`, log)
  //: Wave is typed: a wave past the onslaught's shows the reason under it
  await set('input[aria-label="Line 1 wave"]', '99', 'input')
  await sleep(200)
  const waveErr = await p.evaluate(() => document.querySelector('input[aria-label="Line 1 wave"]')
    ?.parentElement.querySelector('.err')?.textContent || '')
  check(/^The onslaught has \d+ waves?$/.test(waveErr), `Wave 99: "${waveErr}"`, log)
  await set('input[aria-label="Line 1 wave"]', DIALOGUE_LINES[0][1], 'input')
  await sleep(200)
  //: two fixed lines per entry at this width and a narrow one: the buttons on the first line,
  //: the columns lined up whether or not a line has a Wave field
  const layout = async (width) => {
    await p.setViewport({ width, height: 920 })
    await sleep(400)
    return p.evaluate(() => [...document.querySelectorAll('.timeline section.dialogue .line')].map(r => {
      const box = (s) => r.querySelector(s).getBoundingClientRect()
      const top = (s) => Math.round(box(s).top)
      const mid = (s) => (box(s).top + box(s).bottom) / 2
      const left = (s) => Math.round(box(s).left)
      const mids = ['input[aria-label$="delay"]', 'input[aria-label$="length"]'].map(mid)
      const sec = r.closest('section').getBoundingClientRect()
      const del = box('button[aria-label^="Remove line"]')
      return { one: Math.max(...mids) - Math.min(...mids) <= 3 && del.bottom <= box('input[aria-label$="text"]').top &&
        del.right <= sec.right && box('input[aria-label$="text"]').right <= sec.right,
      textBelow: top('input[aria-label$="text"]') > top('input[aria-label$="delay"]'),
      delayLeft: left('input[aria-label$="delay"]') }
    }))
  }
  for (const width of [1500, 1000]) {
    const l = await layout(width)
    if (process.env.RL_SHOTS) {
      const box = await p.$('.timeline section.dialogue')
      if (box) await box.screenshot({ path: `${process.env.RL_SHOTS}/dialogue-timeline${width === 1500 ? '' : '-' + width}.png` })
    }
    check(l.every(r => r.one && r.textBelow) && new Set(l.map(r => r.delayLeft)).size === 1,
      `at ${width} px: every line on two lines, columns lined up (${JSON.stringify(l)})`, log)
  }
  await p.setViewport({ width: 1500, height: 920 })
  await p.evaluate(() => [...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Save Mission').click())
  await waitFor(p, 'the save to finish', () => ![...document.querySelectorAll('button')]
    .some(b => /Saving/.test(b.textContent)), { timeout: 300000, polling: 500 })
  const rec = await p.evaluate(async (i) => (await fetch('api/bucket/item/' + encodeURIComponent(i))).json(), id)
  const saved = ((rec.plan || {}).events || []).filter(e => e.kind === 'dialogue')
  check(JSON.stringify(saved.map(e => [e.when, e.wave, e.delay, e.speaker, e.text, e.duration])) ===
    JSON.stringify(DIALOGUE_LINES.map(([when, wave, delay, speaker, text, length]) =>
      [when, Number(wave), Number(delay), speaker, text, length === '' ? null : Number(length)])),
  `plan after the save: ${JSON.stringify(saved)}`, log)
  await open()
  got = await rows()
  check(JSON.stringify(got.map(r => [r.when, r.delay, r.speaker, r.text, r.length].join('/'))) === JSON.stringify(want),
    `reopened: ${got.length} lines as saved`, log)
}

const CASES = { onslaught, mapeditor, reference, preview, rotation, flip, gridsize, effects, events,
  saveas, controllers, compounds: compoundKits, compoundbrowser: compoundBrowser, compoundparams: compoundParams, decor: decorTools, titlebar, campaign, maptoggles,
  minimapdoors, cutaway, addlook, pickups, crystal, crystalhealth, gamemode, crystallevel, supportedmods, modunits, scramble, scrambletiers, unlocks, header, fab: fabKits, setup, appshell: appShell,
  mapeditorui: mapeditorUi, spawnerfacing: spawnerFacing, multirift: multiRift, openwarn: openWarn, onslaughtfloors: onslaughtFloors,
  campaignindex: campaignIndex, dialogue, floorsnap: floorSnap, samename: sameName, campaignpak: campaignPak, retint }

/** One blank page asks for a WebGL context: {ok, vendor, renderer, why}. Not ok when there
 * is no context, the GPU reads as disabled (a blocklisted or crashed GPU process), or WebGL
 * fell back to SwiftShader without the flags asking for it (--disable-gpu): the Map Editor
 * cases would then hang or crawl on a level that cannot draw. */
async function webglPreflight (browser, flags) {
  const p = await browser.newPage()
  const software = flags.some(f => /swiftshader/i.test(f))
  try {
    return await p.evaluate((software) => {
      const c = document.createElement('canvas')
      const gl = c.getContext('webgl2') || c.getContext('webgl')
      if (!gl) return { ok: false, why: 'no WebGL context' }
      const ext = gl.getExtension('WEBGL_debug_renderer_info')
      const vendor = String(gl.getParameter(ext ? ext.UNMASKED_VENDOR_WEBGL : gl.VENDOR))
      const renderer = String(gl.getParameter(ext ? ext.UNMASKED_RENDERER_WEBGL : gl.RENDERER))
      const off = /disabled/i.test(vendor + renderer)
      const fallback = !software && /swiftshader/i.test(vendor + renderer)
      return { ok: !off && !fallback, vendor, renderer,
        why: off ? 'the GPU reads as disabled' : fallback ? 'WebGL fell back to SwiftShader' : '' }
    }, software)
  } finally {
    await p.close().catch(() => {})
  }
}

async function main () {
  const o = args()
  if (!o.shell && !(o.riftlord && o.onslaught)) { console.error('give --shell URL (or RL_SHELL)'); return 2 }
  const urls = { shell: o.shell && o.shell.replace(/\/$/, '') }
  let setupError = null
  if (o.shell) {
    try { Object.assign(urls, await ts.tools(o.shell)) } catch (e) { setupError = `${o.shell}/api/tools: ${e.cause?.code || e.cause?.errors?.[0]?.code || e.cause?.message || e.message}` }
  }
  if (o.riftlord) urls.riftlord = o.riftlord.replace(/\/$/, '')
  if (o.onslaught) urls.onslaught = o.onslaught.replace(/\/$/, '')
  const names = (o.cases || Object.keys(CASES)).filter((n) => !o.except.includes(n))
  const flags = process.env.RL_CHROME_FLAGS ? JSON.parse(process.env.RL_CHROME_FLAGS) : GPU_FLAGS
  const prof = ts.profile('uismoke')
  const results = []
  let browser
  try {
    browser = await ts.puppeteer().launch({ executablePath: process.env.RL_CHROMIUM || '/usr/bin/chromium',
      headless: 'new', protocolTimeout: 900000, args: ['--no-sandbox', '--user-data-dir=' + prof, ...flags],
      // Chromium keeps its sockets in TMPDIR; a scratch TMPDIR on the 2 TB disk (which
      // cannot hold sockets) stops it launching. Its own temp files are small.
      env: Object.fromEntries(Object.entries(process.env).filter(([k]) => !['TMPDIR', 'TMP', 'TEMP'].includes(k))) })
    const gl = await webglPreflight(browser, flags)
    console.log(`webgl: ${gl.vendor || '-'} / ${gl.renderer || '-'}`)
    if (!gl.ok) throw new Error(`WebGL is unavailable in Chromium (${gl.why}); not running the cases`)
    for (const name of names) {
      const log = { case: name, ok: false, seconds: 0, steps: [], error: null, pageErrors: [], console: [], http: [],
        started: Date.now() / 1000, timing: null, timingOnly: false }
      const t = Date.now()
      let page
      try {
        if (!CASES[name]) throw new Error('no such case')
        if (setupError) throw new Error('server unreachable: ' + setupError)
        page = await openPage(browser, log)
        let timer
        const budget = CASE_TIMEOUT[name] * Math.min(SCALE, CASE_SCALE_MAX)
        await Promise.race([CASES[name](page, urls, o.mission, log), page.fatal,
          new Promise((resolve, reject) => { timer = setTimeout(() => reject(new Error(`timed out after ${budget} s`)), budget * 1000) })])
          .finally(() => clearTimeout(timer))
        log.ok = log.pageErrors.length === 0
        if (!log.ok) log.error = `${log.pageErrors.length} uncaught page error(s)`
      } catch (e) {
        log.error = e.message.split('\n')[0]
        // failed only on a timing limit: the limit's own failure, nothing else wrong
        log.timingOnly = !!log.timing && log.error === log.timing.what && log.pageErrors.length === 0
      }
      log.seconds = +((Date.now() - t) / 1000).toFixed(1)
      if (page) await page.close().catch(() => {})
      results.push(log)
      console.log(`${log.ok ? 'PASS' : 'FAIL'}  ${name.padEnd(10)} ${String(log.seconds).padStart(6)} s  ${log.error || ''}`)
      for (const s of log.steps) console.log('        ' + s)
      for (const e of log.pageErrors) console.log('        page error: ' + e)
      for (const e of log.console.slice(0, 5)) console.log('        console: ' + e)
      for (const e of log.http.slice(0, 5)) console.log('        http: ' + e)
    }
  } finally {
    if (browser) await browser.close().catch(() => {})
    ts.removeProfile(prof)
  }
  const ok = results.length > 0 && results.every(r => r.ok)
  if (o.json) fs.writeFileSync(o.json, JSON.stringify({ ok, urls, mission: o.mission, results }, null, 1))
  console.log(ok ? 'ui smoke: all passed' : `ui smoke: ${results.filter(r => !r.ok).length} of ${results.length} failed`)
  return ok ? 0 : 1
}

main().then(code => process.exit(code), e => { console.error('ui smoke could not run: ' + e.message); ts.removeAll(); process.exit(2) })
