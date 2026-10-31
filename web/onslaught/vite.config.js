import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'
import licenses from '../shared/licenses.js'

// The frozen Python binary serves `dist` as static files and answers /api from
// the same origin, so the built asset paths must be relative.
export default defineConfig({
  // dist/licenses.json: the npm packages in the bundle (tools/notices.py)
  plugins: [svelte(), licenses()],
  // Components shared by Rift Lord's pages live in web/shared, outside
  // this project: `svelte` is resolved from this project's node_modules for them too.
  resolve: { dedupe: ['svelte'] },
  base: './',
  // When this bundle was built. The server reads `dist` from disk on every request, so a
  // long-running editor serves a NEW page from OLD Python; the page compares this with
  // the time the server started and says to restart rather than failing on a route that
  // process does not have.
  define: { __BUILD_TIME__: JSON.stringify(Date.now()) },
  build: { outDir: 'dist', emptyOutDir: true },
  server: { proxy: { '/api': 'http://127.0.0.1:8779' } },
})
