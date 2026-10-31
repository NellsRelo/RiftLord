// A Vite plugin: writes dist/licenses.json, the npm packages whose code ends up in this
// page's bundle (rendered modules and CSS under node_modules), each with its name,
// version, licence and licence text. tools/build.py gathers it into the app's
// THIRD-PARTY-NOTICES.txt (tools/notices.py).
import fs from 'node:fs'
import path from 'node:path'

const LICENCE_FILE = /^(licen[cs]e|copying|notice)(\.|-|$)/i

/** The package folder holding `file`: the innermost node_modules/<name> or /@scope/<name>. */
function packageDir (file) {
  const parts = file.split(/[\\/]/)
  const at = parts.lastIndexOf('node_modules')
  if (at < 0 || at + 1 >= parts.length) return null
  const n = parts[at + 1].startsWith('@') ? 3 : 2
  return parts.slice(0, at + n).join('/')
}

function describe (dir) {
  const pkg = JSON.parse(fs.readFileSync(path.join(dir, 'package.json'), 'utf8'))
  const licence = typeof pkg.license === 'string' ? pkg.license
    : pkg.license?.type || (pkg.licenses || []).map(l => l.type || l).join(' OR ') || null
  const files = fs.readdirSync(dir).filter(f => LICENCE_FILE.test(f)).sort()
  const text = files.map(f => fs.readFileSync(path.join(dir, f), 'utf8').trim()).join('\n\n') || null
  return { name: pkg.name, version: pkg.version, license: licence, licenseFiles: files, text }
}

export default function licenses (fileName = 'licenses.json') {
  return {
    name: 'riftlord-licenses',
    generateBundle (_options, bundle) {
      const dirs = new Set()
      for (const out of Object.values(bundle)) {
        if (out.type === 'chunk') {
          for (const [id, mod] of Object.entries(out.modules)) {
            if (mod.renderedLength > 0) dirs.add(packageDir(id.replace(/^\0/, '').split('?')[0]))
          }
        }
        for (const id of out.originalFileNames || []) dirs.add(packageDir(id))
      }
      for (const id of this.getModuleIds()) {
        if (/\.css(\?|$)/.test(id)) dirs.add(packageDir(id.split('?')[0]))
      }
      dirs.delete(null)
      const list = [...dirs].filter(d => fs.existsSync(path.join(d, 'package.json')))
        .map(describe).sort((a, b) => a.name.localeCompare(b.name))
      this.emitFile({ type: 'asset', fileName, source: JSON.stringify(list, null, 1) })
    }
  }
}
