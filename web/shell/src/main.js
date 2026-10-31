import { mount } from 'svelte'
import './app.css'
import App from './App.svelte'

// No browser context menu (it offers Reload) outside text fields, and no reload keys.
const editable = (t) => t?.closest?.('input, textarea, select, [contenteditable=""], [contenteditable="true"]')
window.addEventListener('contextmenu', (e) => { if (!editable(e.target)) e.preventDefault() }, true)
window.addEventListener('keydown', (e) => {
  const k = (e.key || '').toLowerCase()
  if (k === 'f5' || ((e.ctrlKey || e.metaKey) && k === 'r')) e.preventDefault()
}, true)

export default mount(App, { target: document.getElementById('app') })
