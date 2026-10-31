"""Drive the Onslaught Editor's page and report what it actually shows.

    python3 -m onslaughtgui.selftest <pak> [<pak-with-many-maps>]
    python3 -m onslaughtgui.selftest --saves [savetest options]   (onslaught save suite)

"I don't see it" and "it is not there" look identical from outside the browser, and
reading the served JSON says nothing about what a person can see. This picks a pak
through the real file input -- `DOM.setFileInputFiles`, the same path the system dialog
takes -- rather than simulating the UI around it.
"""

import base64
import json
import os
import socket
import struct as _struct
import subprocess
import sys
import shutil
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.onslaught import server as S  # noqa: E402

#: Chrome profiles go on disk, not the RAM-backed /tmp; each is removed when the run ends.
PROFILE_DIR = '/mnt/2tb_pro_a/tmp' if os.path.isdir('/mnt/2tb_pro_a/tmp') else None

CHROME = '/usr/bin/chromium'
PORT = 9444


class Tab:
    """One DevTools connection, for as many commands as the test needs."""

    def __init__(self, port, timeout=600):
        tabs = json.load(urllib.request.urlopen(
            'http://127.0.0.1:%d/json/list' % port, timeout=timeout))
        page = next(t for t in tabs if t['type'] == 'page')
        host, rest = page['webSocketDebuggerUrl'][len('ws://'):].split('/', 1)
        hostname, wsport = host.split(':')
        self.sock = socket.create_connection((hostname, int(wsport)),
                                             timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(('GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n'
                           'Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n'
                           'Sec-WebSocket-Version: 13\r\n\r\n'
                           % (rest, host, key)).encode())
        buf = b''
        while b'\r\n\r\n' not in buf:
            buf += self.sock.recv(4096)
        self.next_id = 0

    def _frame_out(self, body):
        mask = os.urandom(4)
        head = b'\x81'
        n = len(body)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + _struct.pack('>H', n)
        else:
            head += bytes([0x80 | 127]) + _struct.pack('>Q', n)
        self.sock.sendall(head + mask
                          + bytes(b ^ mask[i % 4] for i, b in enumerate(body)))

    def _frame_in(self):
        head = self.sock.recv(2)
        if len(head) < 2:
            raise SystemExit('the browser closed the connection')
        length = head[1] & 0x7F
        if length == 126:
            length = _struct.unpack('>H', self.sock.recv(2))[0]
        elif length == 127:
            length = _struct.unpack('>Q', self.sock.recv(8))[0]
        data = b''
        while len(data) < length:
            data += self.sock.recv(length - len(data))
        return data

    def send(self, method, params=None, timeout=600):
        self.next_id += 1
        want = self.next_id
        self._frame_out(json.dumps({'id': want, 'method': method,
                                    'params': params or {}}).encode())
        deadline = time.time() + timeout
        while time.time() < deadline:
            got = json.loads(self._frame_in().decode('utf-8', 'replace'))
            if got.get('id') == want:
                if 'error' in got:
                    raise Restart(got['error'].get('message', 'devtools error'))
                return got.get('result', {})
        raise SystemExit('%s did not answer' % method)

    def js(self, expression, timeout=600):
        got = self.send('Runtime.evaluate',
                        {'expression': expression, 'awaitPromise': True,
                         'returnByValue': True}, timeout=timeout)
        if got.get('exceptionDetails'):
            raise SystemExit('page threw: %s'
                             % json.dumps(got['exceptionDetails'])[:400])
        return got.get('result', {}).get('value')

    def set_file(self, selector, path):
        """Put a real file in a file input, as the system dialog would."""
        root = self.send('DOM.getDocument')['root']['nodeId']
        node = self.send('DOM.querySelector',
                         {'nodeId': root, 'selector': selector})['nodeId']
        if not node:
            raise SystemExit('no %s on the page' % selector)
        self.send('DOM.setFileInputFiles',
                  {'files': [os.path.abspath(path)], 'nodeId': node})


class Restart(Exception):
    """The page reloaded under us; reattach and try again."""


SETTLE = """
(async () => {
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  for (let i = 0; i < %(tries)d; i += 1) {
    if (%(test)s) return true;
    await wait(250);
  }
  return false;
})()
"""


def settle(tab, test, tries=480):
    return tab.js(SETTLE % {'test': test, 'tries': tries})


def check(pak, many=None):
    """What the editor shows, and whether picking a pak reaches a map."""
    out = {}
    tab = Tab(PORT)
    out['picker_filled'] = settle(tab, "document.querySelector('select optgroup')")
    out['groups'] = tab.js(
        "[...document.querySelectorAll('select optgroup')].map(g => g.label)")
    out['group_sizes'] = tab.js(
        "[...document.querySelectorAll('select optgroup')]"
        ".map(g => [g.label, g.querySelectorAll('option').length])")

    # The button has to be visible, and it has to own a file input
    out['pak_button'] = tab.js("""
      (() => {
        const b = [...document.querySelectorAll('button')]
          .find(x => /open pak/i.test(x.textContent));
        if (!b) return null;
        const r = b.getBoundingClientRect();
        return {text: b.textContent.trim(),
                visible: r.width > 0 && r.height > 0 && r.top >= 0
                         && r.right <= innerWidth};
      })()""")
    # Rescan sits beside Open Pak, and the first-load modal has gone once maps are in
    out['rescan_button'] = tab.js(
        "!![...document.querySelectorAll('button')].find(x => x.textContent.trim() === 'Rescan')")
    out['boot_modal_gone'] = settle(tab, "!document.querySelector('.loading')", 240)
    out['has_file_input'] = tab.js(
        "!!document.querySelector('input[type=file]')")
    # A fresh server must NOT be reported stale
    out['stale_banner'] = tab.js(
        "document.querySelector('[role=alert]')?.textContent?.trim()?.slice(0, 60)"
        " ?? null")

    # An error in the opener must still be dismissable: relying on the parent to clear
    # it left the close button and the backdrop dead.
    tab.set_file('input[type=file]', __file__)      # not a pak
    out['rejected'] = settle(
        tab, "document.querySelector('[role=dialog] .err')", 120)
    out['reject_message'] = tab.js(
        "document.querySelector('[role=dialog] .err')?.textContent?.trim()")
    tab.js("document.querySelector('[role=dialog] button[aria-label=Close]').click()")
    out['closed_after_error'] = settle(
        tab, "!document.querySelector('[role=dialog]')", 80)
    out['accepts'] = tab.js(
        "document.querySelector('input[type=file]')?.getAttribute('accept')")

    # One map in the pak: straight into it, no modal to click through
    tab.set_file('input[type=file]', pak)
    out['loaded'] = settle(
        tab, "document.querySelector('select').value !== ''", 480)
    out['chose'] = tab.js("document.querySelector('select').value")
    out['chose_label'] = tab.js(
        "(() => { const s = document.querySelector('select');"
        " return [...s.querySelectorAll('option')]"
        ".find(o => o.value === s.value)?.textContent.trim(); })()")
    out['groups_after'] = tab.js(
        "[...document.querySelectorAll('select optgroup')].map(g => g.label)")
    # The spawner tab must draw its route, dashed when only flyers can use it
    tab.js("(() => { const b = [...document.querySelectorAll('button')]"
           ".find(x => /Spawner/i.test(x.textContent)); if (b) b.click(); })()")
    out['route_drawn'] = settle(tab, "document.querySelector('line.route')", 80)
    out['route_lines'] = tab.js(
        "document.querySelectorAll('line.route').length")
    out['route_dashed'] = tab.js(
        "[...document.querySelectorAll('line.route')]"
        ".filter(l => getComputedStyle(l).strokeDasharray.includes('14')).length")
    out['tabs'] = tab.js(
        "[...document.querySelectorAll('[class*=tab] button, button[class*=tab]')]"
        ".map(b => b.textContent.replace(/\\s+/g, ' ').trim()).slice(0, 6)")

    # Copy Door Waves: offered on a door tab, lists maps, then doors of the same type
    tab.js("(() => { const b = [...document.querySelectorAll('[role=tab]')]"
           ".find(x => /Door/i.test(x.textContent)); if (b) b.click(); })()")
    out['copy_button'] = settle(
        tab, "[...document.querySelectorAll('button')].find(x => /Copy Door Waves/.test(x.textContent))", 80)
    if out['copy_button']:
        tab.js("[...document.querySelectorAll('button')]"
               ".find(x => /Copy Door Waves/.test(x.textContent)).click()")
        settle(tab, "document.querySelector('.copy .pick')", 80)
        tab.js("[...document.querySelectorAll('.copy .pick')]"
               ".find(x => x.textContent.trim() === 'North Wing')?.click()")
        out['copy_doors'] = settle(
            tab, "document.querySelector('.copy .pick')?.textContent.includes('Door')", 240)
        tab.js("document.querySelector('.copy .pick')?.click()")
        out['copied_notice'] = settle(
            tab, "document.querySelector('.dropped.ok')?.textContent.includes('Copied')", 240)
        out['copied_rows'] = tab.js("document.querySelectorAll('.form select').length")

    if many:
        # Several maps: a modal to choose from, and nothing may overflow it
        tab.set_file('input[type=file]', many)
        # Wait for the ROWS, not the dialog: it opens immediately saying it is reading,
        # and a 55 MB pak takes a moment to upload and parse.
        out['modal'] = settle(
            tab, "document.querySelector('[role=dialog] .row')", 960)
        out['modal_rows'] = tab.js(
            "[...document.querySelectorAll('[role=dialog] .row')].length")
        out['modal_first'] = tab.js(
            "document.querySelector('[role=dialog] .row')"
            "?.textContent.replace(/\\s+/g, ' ').trim()")
        out['overflowing'] = tab.js("""
          (() => {
            const m = document.querySelector('[role=dialog]');
            if (!m) return null;
            const b = m.getBoundingClientRect();
            const out = [];
            if (b.right > innerWidth + 1 || b.bottom > innerHeight + 1) {
              out.push(['the modal itself', Math.round(b.right), Math.round(b.bottom),
                        innerWidth, innerHeight]);
            }
            // Content scrolled out of a scroll container is past the container's box by
            // design, so only elements with no scrolling ancestor count as overflow.
            const scrolls = (el) => {
              for (let p = el.parentElement; p && p !== m.parentElement;
                   p = p.parentElement) {
                const o = getComputedStyle(p).overflowY;
                if (o === 'auto' || o === 'scroll') return true;
              }
              return false;
            };
            for (const el of m.querySelectorAll('*')) {
              if (scrolls(el)) continue;
              const r = el.getBoundingClientRect();
              if (r.width > 0 && (r.right > b.right + 1 || r.bottom > b.bottom + 1)) {
                out.push([el.tagName + '.' + (el.className || ''),
                          Math.round(r.right), Math.round(r.bottom),
                          Math.round(b.right), Math.round(b.bottom)]);
              }
            }
            return out.slice(0, 6);
          })()""")
        out['modal_fits'] = not out['overflowing']
        out['modal_html'] = tab.js(
            "document.querySelector('[role=dialog]')?.outerHTML?.slice(0, 500)")
        out['clicked'] = tab.js(
            "(() => { const r = document.querySelector('[role=dialog] .row');"
            " if (!r) return false; r.click(); return true; })()")
        if out['clicked']:
            out['picked_from_modal'] = settle(
                tab, "!document.querySelector('[role=dialog][aria-label*=map]')", 240)
            # The map still has to be read before the picker shows it, so wait for the
            # value to change rather than reading it the instant the modal closes.
            out['loaded_from_modal'] = settle(
                tab, "document.querySelector('select').value === 'Scenario_F1'", 480)
            out['chose_after_modal'] = tab.js(
                "document.querySelector('select').value")
            # a mod level on shipped MapData draws a minimap (the level's own import)
            out['f1_minimap'] = tab.js(
                "fetch('api/map/Scenario_F1/minimap.png').then(r => r.status)")

    out.update(check_endless(tab))
    tab = Tab(PORT)
    out.update(check_events(tab))
    tab = Tab(PORT)
    out.update(check_copy_name(tab))
    tab = Tab(PORT)

    # Embedded: the shell's state survives a reload, so Save Mission stays and Export
    # never appears
    tab.js("location.href = '/?embedded=1&map=Scenario_01'")
    time.sleep(2)
    tab = Tab(PORT)
    settle(tab, "document.querySelector('select').value === 'Scenario_01'", 480)
    out['embedded_url'] = tab.js("location.search")
    tab.js("location.reload()")
    time.sleep(2)
    tab = Tab(PORT)
    settle(tab, "document.querySelector('select').value === 'Scenario_01'", 480)
    out['embedded_after_reload'] = tab.js(
        "({save: !![...document.querySelectorAll('button')].find(x => /Save Mission/.test(x.textContent)),"
        " export: !![...document.querySelectorAll('button')].find(x => x.textContent.trim() === 'Export')})")
    return out


#: A shipped map whose onslaught opens Door 2 early in Endless (F1: wave 2 -> 1).
ENDLESS_MAP = 'Scenario_02'
#: Its early Endless rows on Door 2 (workshop/research/F1-endless-spawners.md).
ENDLESS_EARLY_ROWS = 8
#: A map to copy a door from whose Door 2 also opens early (Side Entrance).
ENDLESS_COPY_FROM = 'Side Entrance'


def click_text(tab, selector, pattern):
    """Click the first `selector` whose text matches the JS regex `pattern`."""
    return tab.js("(() => { const b = [...document.querySelectorAll(%s)]"
                  ".find(x => %s.test(x.textContent)); if (b) b.click(); return !!b })()"
                  % (json.dumps(selector), pattern))


def shot(tab, name):
    """Save a screenshot to $OE_SHOTS/<name>.png when that folder is set."""
    folder = os.environ.get('OE_SHOTS')
    if folder:
        got = tab.send('Page.captureScreenshot', {'format': 'png'})
        with open(os.path.join(folder, name + '.png'), 'wb') as fh:
            fh.write(base64.b64decode(got['data']))


def check_endless(tab):
    """G4: early Endless rows are marked, the copy notice names them, and Details
    offers Reset door after Onslaught Loop only while the mission is in Endless."""
    out = {}
    tab.js("(() => { const s = document.querySelector('select'); s.value = %s;"
           " s.dispatchEvent(new Event('change', {bubbles: true})) })()" % json.dumps(ENDLESS_MAP))
    out['endless_map_open'] = settle(
        tab, "/Great Room/.test(document.querySelector('.facts')?.textContent ?? '')"
        " && !document.querySelector('.loading')", 480)
    click_text(tab, 'button', '/Load onslaught from map/')
    out['endless_load_notice'] = settle(
        tab, "[...document.querySelectorAll('.dropped')].some(d => /opens doors early in Endless/.test(d.textContent))", 480)
    out['endless_tab_dots'] = tab.js("document.querySelectorAll('[role=tab] .early-dot').length")
    click_text(tab, '[role=tab]', '/Door 2/')
    settle(tab, "document.querySelector('.endless-door')", 80)
    out['endless_door_banner'] = tab.js("document.querySelector('.endless-door')?.textContent.replace(/\\s+/g, ' ').trim()")
    out['endless_marked_rows'] = tab.js("document.querySelectorAll('.row.early').length")
    out['endless_marked_ok'] = out['endless_marked_rows'] == ENDLESS_EARLY_ROWS
    shot(tab, 'endless-door')
    out['endless_wave_note'] = tab.js("document.querySelector('.endless')?.textContent.replace(/\\s+/g, ' ').trim()")

    # Copy Door Waves from another map's early door says so
    click_text(tab, 'button', '/Copy Door Waves/')
    settle(tab, "document.querySelector('.copy .pick')", 80)
    tab.js("[...document.querySelectorAll('.copy .pick')].find(x => x.textContent.trim() === %s)?.click()"
           % json.dumps(ENDLESS_COPY_FROM))
    settle(tab, "[...document.querySelectorAll('.copy .pick')].some(x => x.textContent.trim() === 'Door 2')", 240)
    tab.js("[...document.querySelectorAll('.copy .pick')].find(x => x.textContent.trim() === 'Door 2')?.click()")
    settle(tab, "document.querySelector('.dropped.ok p')", 240)
    out['endless_copy_notice'] = tab.js("document.querySelector('.dropped.ok p')?.textContent.replace(/\\s+/g, ' ').trim()")

    # Details: the checkbox is there, disabled until Add to Endless is ticked
    click_text(tab, 'button', '/^Details/')
    settle(tab, "document.querySelector('.md-modal')", 80)
    find = ("[...document.querySelectorAll('.md-modal label.check')]"
            ".find(l => /Reset door after Onslaught Loop/.test(l.textContent))")
    out['reset_checkbox'] = tab.js("!!%s" % find)
    out['reset_disabled_off_endless'] = tab.js("%s?.querySelector('input').disabled" % find)
    tab.js("[...document.querySelectorAll('.md-modal label.check')]"
           ".find(l => /Add to Endless/.test(l.textContent)).querySelector('input').click()")
    out['reset_enabled_in_endless'] = settle(tab, "%s?.querySelector('input').disabled === false" % find, 40)
    shot(tab, 'details-reset')
    tab.js("%s.querySelector('input').click()" % find)
    tab.js("document.querySelector('.md-modal button[aria-label=Close]').click()")
    out['reset_banner_on'] = settle(
        tab, "/Reset door after Onslaught Loop is on/.test(document.querySelector('.endless-door')?.textContent ?? '')", 40)
    return out


#: Mission events: (map, the event line its own onslaught shows, kinds it cannot do).
EVENT_MAPS = (('Scenario_09', 'Boss:', ('Music change', 'Unstable Rift')),
              ('Scenario_05', 'Music changes to:', ('Unstable Rift',)))


def check_events(tab):
    """Mission events on the Timeline: a map's own events read as lines, the boss
    linked to its reaction, and kinds the host cannot do disabled with a reason."""
    out = {}
    for name, line, blocked in EVENT_MAPS:
        tab.js("location.href = '/?map=%s'" % name)
        time.sleep(2)
        tab = Tab(PORT)
        settle(tab, "document.querySelector('select').value === %s"
                    " && !document.querySelector('.loading')" % json.dumps(name), 480)
        click_text(tab, 'button', '/Load onslaught from map/')
        settle(tab, "document.querySelectorAll('.events .event').length > 0", 480)
        lines = tab.js(
            "(() => { const t = (n) => n.nodeType === 3 ? n.textContent"
            " : n.tagName === 'SELECT' ? (n.selectedOptions[0]?.textContent ?? '')"
            " : n.tagName === 'INPUT' ? n.value : n.tagName === 'BUTTON' ? ''"
            " : [...n.childNodes].map(t).join(' ');"
            " return [...document.querySelectorAll('.events .event')]"
            ".map(e => t(e).replace(/\\s+/g, ' ').trim()) })()") or []
        off = tab.js("[...document.querySelectorAll('.events header button.add')]"
                     ".filter(b => b.disabled).map(b => b.textContent.replace('+ ', '').trim())") or []
        shot(tab, 'events-' + name)
        out['events_%s_lines' % name] = lines
        out['events_%s_ok' % name] = (any(line in l for l in lines)
                                      and sorted(off) == sorted(blocked))
    return out


#: Stands in for Rift Lord's Missions in the page, so no test touches real user data.
COPY_STUB = r"""
(() => {
  const real = window.fetch.bind(window)
  window.__builds = []
  const json = (v) => Promise.resolve(new Response(JSON.stringify(v),
    { status: 200, headers: { 'Content-Type': 'application/json' } }))
  window.fetch = (url, opts) => {
    const u = String(url)
    if (u.includes('api/bucket/item/')) return json({ id: 'stub', map: 'Scenario_01', title: 'North Wing', kind: 'mission' })
    if (u.includes('api/bucket/titles')) return json(['North Wing copy', 'Other'])
    if (u.endsWith('api/build') && opts?.method === 'POST') {
      window.__builds.push(JSON.parse(opts.body)); return json({ id: 'stubjob' })
    }
    if (u.includes('api/build/stubjob')) return json({ state: 'done', log: [],
      result: { mission: 'stub2', kind: 'mission', copied: true } })
    return real(url, opts)
  }
})()
"""


def check_copy_name(tab):
    """G1: Save as copy asks for a name, prefilled free, refusing an empty or used one."""
    out = {}
    tab.send('Page.enable')
    tab.send('Page.addScriptToEvaluateOnNewDocument', {'source': COPY_STUB})
    tab.js("location.href = '/?embedded=1&bucket=stub'")
    time.sleep(2)
    tab = Tab(PORT)
    settle(tab, "document.querySelector('select').value === 'Scenario_01'", 480)
    settle(tab, "!!document.querySelector('button.caret[aria-label=\"Other ways to save\"]')", 80)
    tab.js("document.querySelector('button.caret[aria-label=\"Other ways to save\"]').click()")
    click_text(tab, '.menu .item', '/Save as copy/')
    settle(tab, "document.querySelector('.name-modal input')", 80)
    shot(tab, 'copy-name')
    out['copy_modal'] = tab.js("document.querySelector('.name-modal input')?.value")
    out['copy_prefill_free'] = out['copy_modal'] == 'North Wing copy 2'
    setv = ("(() => { const i = document.querySelector('.name-modal input'); i.value = %s;"
            " i.dispatchEvent(new Event('input', {bubbles: true})) })()")
    tab.js(setv % json.dumps('  '))
    out['copy_empty_refused'] = tab.js(
        "document.querySelector('.name-modal button[type=submit]').disabled"
        " && document.querySelector('.name-modal .hint').textContent.trim()")
    tab.js(setv % json.dumps('north wing COPY'))
    out['copy_taken_refused'] = tab.js(
        "document.querySelector('.name-modal button[type=submit]').disabled"
        " && document.querySelector('.name-modal .hint').textContent.trim()")
    tab.js(setv % json.dumps('My Copy'))
    tab.js("document.querySelector('.name-modal button[type=submit]').click()")
    settle(tab, "window.__builds.length > 0", 80)
    out['copy_posted'] = tab.js("window.__builds.map(b => ({title: b.title, copy: b.copy, mission: b.mission}))")
    out['copy_title_after'] = settle(
        tab, "document.querySelector('input.title')?.value === 'My Copy'", 80)
    return out


def main():
    if sys.argv[1:2] == ['--saves']:
        # the onslaught save regression suite, no browser
        from riftlord.onslaught import savetest
        return savetest.main(sys.argv[2:])
    pak = sys.argv[1] if len(sys.argv) > 1 else None
    many = sys.argv[2] if len(sys.argv) > 2 else None
    if not pak:
        raise SystemExit(__doc__.strip().splitlines()[2].strip())
    httpd = S.serve(0)
    url = 'http://127.0.0.1:%d/' % httpd.port
    profile = tempfile.mkdtemp(prefix='oe-selftest-', dir=PROFILE_DIR)
    proc = subprocess.Popen(
        [CHROME, '--headless=new', '--enable-unsafe-swiftshader',
         '--window-size=1600,900', '--no-sandbox',
         '--remote-debugging-port=%d' % PORT, '--user-data-dir=' + profile, url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(80):
            try:
                urllib.request.urlopen('http://127.0.0.1:%d/json/list' % PORT,
                                       timeout=2)
                break
            except Exception:                        # noqa: BLE001
                time.sleep(0.5)
        for attempt in range(6):
            try:
                print(json.dumps(check(pak, many), indent=1))
                return 0
            except Restart as why:
                # The app reloads after first paint; the first attach dies with it.
                if attempt == 5:
                    raise SystemExit(str(why))
                time.sleep(2)
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
