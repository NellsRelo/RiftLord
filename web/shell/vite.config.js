import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'
import licenses from '../shared/licenses.js'

// Served as static files by workshop/server.py, which answers /api on the same
// origin, so the built asset paths are relative.
export default defineConfig({
  // dist/licenses.json: the npm packages in the bundle (tools/notices.py)
  plugins: [svelte(), licenses()],
  // Components shared by Rift Lord's pages live in web/shared, outside
  // this project: `svelte` is resolved from this project's node_modules for them too.
  resolve: { dedupe: ['svelte'] },
  base: './',
  build: { outDir: 'dist', emptyOutDir: true },
  server: { proxy: { '/api': 'http://127.0.0.1:8870' } },
})
