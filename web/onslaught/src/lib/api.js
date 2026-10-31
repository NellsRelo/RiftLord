// Every call goes to the Python server that serves this page, so the paths are
// same-origin and need no host.

async function get(path) {
  const res = await fetch(path)
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).error ?? detail } catch { /* not json */ }
    throw new Error(detail)
  }
  return res.json()
}

async function post(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    // the guard header: a page on another site cannot set it without a
    // CORS preflight, which the server never grants
    headers: { 'Content-Type': 'application/json',
               'X-Onslaught-Editor': '1' },
    body: JSON.stringify(body ?? {}),
  })
  let got = {}
  try { got = await res.json() } catch { /* not json */ }
  if (!res.ok) throw new Error(got.error ?? res.statusText)
  return got
}

export const listMaps = () => get('api/maps')
export const listUnits = () => get('api/units')
export const listCampaigns = (mission) => get('api/campaigns' + (mission ? '?mission=' + encodeURIComponent(mission) : ''))
/** The live Scramble tiers a mission can join (missionscramble.view). */
export const scrambleTiers = () => get('api/scramble')
/** Change an installed custom campaign's name, description, hub style or banner. */
export const saveCampaign = (key, values) => post('api/campaign', { key, values })
export const missionDefaults = () => get('api/defaults')
export const missionTitles = () => get('api/bucket/titles')
export const bucketItem = (id) => get(`api/bucket/item/${encodeURIComponent(id)}`)
export const missionGuid = (id) => get(`api/bucket/guid/${encodeURIComponent(id)}`)
export const newGuid = () => get('api/guid/new')
export const setMissionGuid = (id, guid) => post('api/bucket/guid', { id, guid })
/** The hub's enemy preview for the spec being edited, with `options` (makepreview). */
export const previewSpec = (spec, options) => post('api/preview', { spec, options })
export const checkGuid = (guid, mission) => get('api/guid/check?guid=' + encodeURIComponent(guid) +
  (mission ? '&mission=' + encodeURIComponent(mission) : ''))
/** The source level's own loading screen and hub portal pictures (sourcepictures.py). */
export const sourcePictures = (map, mission) => get('api/sourcepictures?' + new URLSearchParams(
  Object.fromEntries(Object.entries({ map, mission }).filter(([, v]) => v))))
export const installStatus = () => get('api/install')
export const readOnslaught = (name) =>
  get(`api/map/${encodeURIComponent(name)}/onslaught`)

export async function setInstall(path) {
  const res = await fetch('api/install', {
    method: 'POST',
    // the guard header: a page on another site cannot set it without a
    // CORS preflight, which the server never grants
    headers: { 'Content-Type': 'application/json',
               'X-Onslaught-Editor': '1' },
    body: JSON.stringify({ path }),
  })
  return res.json()
}
/** Ask the editor to stop serving. The reply comes back before it goes down. */
export async function shutdown() {
  const res = await fetch('api/quit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json',
               'X-Onslaught-Editor': '1' },
    body: '{}',
  })
  return res.json()
}

/** Send a pak the author picked and read the maps in it. The bytes go over because a
 *  file input gives the page the contents and a bare name, never a usable path. */
export async function openPakFile(file) {
  const res = await fetch('api/openpak/file', {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream',
               'X-Onslaught-Editor': '1',
               'X-Pak-Name': encodeURIComponent(file.name) },
    body: file,
  })
  let got = {}
  try { got = await res.json() } catch { /* not json */ }
  if (!res.ok) throw new Error(got.error ?? res.statusText)
  return got
}

/** Read a pak already on disk, by path. Kept for scripts and the selftest; the UI
 *  goes through the file dialog. */
export const openPak = (path) => post('api/openpak', { path })

/** Forget an opened pak; no path forgets all of them. */
export const closePak = (path) => post('api/closepak', { path })

/** Read every pak again and return the fresh map list. */
export const rescan = () => post('api/rescan', {})

export const mapDetail = (name) => get(`api/map/${encodeURIComponent(name)}`)
export const minimapUrl = (name, volume) =>
  `api/map/${encodeURIComponent(name)}/minimap.png` +
  (volume == null ? '' : `?volume=${volume}`)

export async function startBuild(spec) {
  const res = await fetch('api/build', {
    method: 'POST',
    // the guard header: a page on another site cannot set it without a
    // CORS preflight, which the server never grants
    headers: { 'Content-Type': 'application/json',
               'X-Onslaught-Editor': '1' },
    body: JSON.stringify(spec),
  })
  if (!res.ok) throw new Error((await res.json()).error ?? res.statusText)
  return res.json()
}

export const buildStatus = (id) => get(`api/build/${id}`)

//: Reads kept for this page, by kind and map: a map's detail and its onslaught. The
//: door-copy picker reads every map, so they are fetched ahead (prefetchMaps).
const kept = new Map()

function keep(key, fn) {
  if (!kept.has(key)) kept.set(key, fn().catch((e) => { kept.delete(key); throw e }))
  return kept.get(key)
}

/** mapDetail, read once per page. */
export const mapDetailKept = (name) => keep('detail:' + name, () => mapDetail(name))
/** readOnslaught, read once per page until forgotten. */
export const onslaughtKept = (name) => keep('onslaught:' + name, () => readOnslaught(name))
/** Forget what was kept for one map (its onslaught was saved), or for every map. */
export function forgetKept(name = null) {
  if (name == null) kept.clear()
  else for (const k of [...kept.keys()]) if (k.endsWith(':' + name)) kept.delete(k)
}

let prefetching = null
/** Read every map's detail and onslaught ahead, one map at a time; once per page. */
export function prefetchMaps(names) {
  prefetching ??= (async () => {
    for (const name of names) {
      try { await mapDetailKept(name); await onslaughtKept(name) } catch { /* read on demand */ }
    }
  })()
  return prefetching
}
