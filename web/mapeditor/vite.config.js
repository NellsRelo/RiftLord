import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'
import licenses from '../shared/licenses.js'

// The Python server serves the built files and owns the API, so the dev server
// forwards /api to it rather than duplicating any of it. Svelte is here only for the
// components Rift Lord's pages share (web/shared); the editor itself is
// plain JavaScript.
export default defineConfig({
  // dist/licenses.json: the npm packages in the bundle (tools/notices.py)
  plugins: [svelte(), licenses()],
  // the shared components live outside this project: `svelte` resolves from here
  resolve: { dedupe: ['svelte'] },
  // relative asset URLs: the page is served under /mapeditor/ in the app
  base: './',
  build: { outDir: 'dist', emptyOutDir: true },
  server: {
    proxy: { '/api': { target: 'http://127.0.0.1:8850', changeOrigin: true } }
  }
})
