// Rift Lord's own API. The tools' pages talk to their own servers.

async function get(path) {
  const res = await fetch(path)
  const got = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(got.error ?? res.statusText)
  return got
}

async function post(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Workshop': '1' },
    body: JSON.stringify(body ?? {}),
  })
  const got = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(got.error ?? res.statusText)
  return got
}

export const tools = () => get('/api/tools')
export const listBucket = () => get('/api/bucket')
export const install = (ids) => post('/api/bucket/install', { ids })
export const startBuild = (ids) => post('/api/bucket/build', { ids })
export const buildStatus = () => get('/api/bucket/build')
export const packageTo =(ids, path) => post('/api/bucket/package', { ids, path })
export const setRoot = (path) => post('/api/bucket/root', { path })
export const remove = (ids) => post('/api/bucket/remove', { ids })
export const renameMission = (id, title) => post('/api/bucket/rename', { id, title })
export const reveal = () => post('/api/bucket/reveal')
export const quit = () => post('/api/quit')
export const zipUrl = (ids) => '/api/bucket/package.zip?ids=' + ids.map(encodeURIComponent).join(',')
export const consoleSince = (n) => get('/api/console?since=' + n)
export const clearConsole = () => post('/api/console/clear')
export const missionSettings = (id) => get('/api/bucket/settings?id=' + encodeURIComponent(id))
export const saveMissionSettings = (body) => post('/api/bucket/settings', body)
export const campaignSettings = (key) => get('/api/bucket/campaign?key=' + encodeURIComponent(key))
export const saveCampaign = (key, values) => post('/api/bucket/campaign', { key, values })
export const setCampaignIndex = (key, id, index) => post('/api/bucket/campaignindex', { key, id, index })
export const checkGuid = (guid, mission) => get('/api/guid/check?guid=' + encodeURIComponent(guid) +
  (mission ? '&mission=' + encodeURIComponent(mission) : ''))
export const newGuid = () => get('/api/guid/new')
export const fabState = () => get('/api/fab/state')
export const fabVault = (fresh) => get('/api/fab/vault' + (fresh ? '?fresh=1' : ''))
export const fabConvert = (item, scale) => post('/api/fab/convert', { item, scale })
export const fabMeasure = (item) => post('/api/fab/measure', { item })
export const fabCancel = (item) => post('/api/fab/cancel', { item })
export const fabDelete = (item) => post('/api/fab/delete', { item })
export const fabMove = (item, index) => post('/api/fab/move', { item, index })
export const fabSettings = () => get('/api/fab/settings')
export const saveFabSettings = (values) => post('/api/fab/settings', { values })
export const supportedMods = () => get('/api/supportedmods')
export const openModPage = (id) => post('/api/supportedmods/open', { id })
export const setupStatus = () => get('/api/setup')
export const startSetup = () => post('/api/setup/start')
export const getSettings = () => get('/api/settings')
export const saveSettings = (defaults) => post('/api/settings', { defaults })
