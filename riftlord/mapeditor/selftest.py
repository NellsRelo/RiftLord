"""Drive Rift Lord's UI in a headless browser and report what it actually did.

    python3 riftlord/selftest.py

A panel that never appears looks identical to a panel that was never built, and
guessing between those from the outside wasted a round trip. This starts the server,
loads the page in headless Chromium, places one of each object, selects it, and prints
what the page ended up showing -- so "there is no properties panel" is a measurement
rather than an argument.
"""

import json
import os
import subprocess
import sys
import shutil
import tempfile
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

from riftlord.mapeditor import server as S  # noqa: E402

#: Chrome profiles go on disk, not the RAM-backed /tmp; each is removed when the run ends.
PROFILE_DIR = '/mnt/2tb_pro_a/tmp' if os.path.isdir('/mnt/2tb_pro_a/tmp') else None

#: The page is driven through the DevTools protocol over a pipe, because that needs
#: nothing installed beyond the browser itself.
CHROME = '/usr/bin/chromium'


#: Seconds to wait for the probe; a level drawn by software rendering takes about 11 minutes.
PROBE_TIMEOUT = 1500


def devtools(port, script, timeout=PROBE_TIMEOUT):
    """Evaluate one expression in the open page and return its value."""
    import socket
    import base64
    import struct as _struct
    body = json.dumps({'id': 1, 'method': 'Runtime.evaluate',
                       'params': {'expression': script, 'awaitPromise': True,
                                  'returnByValue': True}})
    tabs = json.load(urllib.request.urlopen(
        'http://127.0.0.1:%d/json/list' % port, timeout=timeout))
    page = next(t for t in tabs if t['type'] == 'page')
    url = page['webSocketDebuggerUrl']
    host, rest = url[len('ws://'):].split('/', 1)
    hostname, wsport = host.split(':')
    sock = socket.create_connection((hostname, int(wsport)), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall(('GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n'
                  'Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n'
                  'Sec-WebSocket-Version: 13\r\n\r\n'
                  % (rest, host, key)).encode())
    buf = b''
    while b'\r\n\r\n' not in buf:
        buf += sock.recv(4096)
    payload = body.encode()
    header = b'\x81'
    mask = os.urandom(4)
    n = len(payload)
    if n < 126:
        header += bytes([0x80 | n])
    elif n < 65536:
        header += bytes([0x80 | 126]) + _struct.pack('>H', n)
    else:
        header += bytes([0x80 | 127]) + _struct.pack('>Q', n)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(header + mask + masked)

    def recvn(n):
        """Exactly `n` bytes, or a clear error. `recv` may return fewer."""
        buf = b''
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise SystemExit(
                    'the DevTools socket closed after %d of %d byte(s) -- the page '
                    'or the browser went away mid-answer' % (len(buf), n))
            buf += chunk
        return buf

    def frame():
        #: Every read goes through `recvn`. Reading the two-byte header with a bare
        #: `recv(2)` crashed with `IndexError: index out of range` whenever it came
        #: back short or empty, which turned a page that had gone away into a
        #: traceback from inside the harness rather than a stated failure.
        head = recvn(2)
        length = head[1] & 0x7F
        if length == 126:
            length = _struct.unpack('>H', recvn(2))[0]
        elif length == 127:
            length = _struct.unpack('>Q', recvn(8))[0]
        return recvn(length) if length else b''

    deadline = time.time() + timeout
    while time.time() < deadline:
        got = json.loads(frame().decode('utf-8', 'replace'))
        if got.get('id') == 1:
            sock.close()
            #: A DevTools failure comes back as `error`, not `result`. Reading only
            #: `result` turned every such failure into an empty {'raw': {}} -- a
            #: harness that hid its own breakage and looked like a page that did
            #: nothing.
            if got.get('error'):
                return {'devtools_error': got['error']}
            whole = got.get('result', {})
            if whole.get('exceptionDetails'):
                return {'threw': whole['exceptionDetails']}
            result = whole.get('result', {})
            if 'value' in result:
                return result['value']
            return {'raw': whole, 'reply_keys': sorted(got.keys()),
                    'result_keys': sorted(whole.keys())}
    sock.close()
    raise SystemExit('the page did not answer in time')


def wait_ready(port, want_url, deadline=90.0):
    """Block until the real page is loaded, and say so. Returns (ok, detail).

    `/json/list` answering does NOT mean the page is ready: Chromium exposes DevTools
    while still on `about:blank` and only then navigates. Evaluating in that window
    aims at a context the navigation is about to destroy, and the reply is
    `Execution context was destroyed` -- which looked like a broken app and was
    nothing but this race.

    The test is `readyState === 'complete'` on a page that is no longer `about:blank`.
    An earlier version also required the URL to start with the one we opened, and that
    was too clever: it reported `still on ['http://127.0.0.1:<port>/']` for a page
    sitting on exactly that address, and the whole run failed on the check rather than
    on anything real. A harness assertion that can fail on a healthy page is worse
    than no assertion.
    """
    end = time.time() + deadline
    last = 'no page appeared'
    while time.time() < end:
        try:
            tabs = json.load(urllib.request.urlopen(
                'http://127.0.0.1:%d/json/list' % port, timeout=2))
        except Exception as exc:                     # noqa: BLE001
            last = 'devtools not up: %s' % exc
            time.sleep(0.25)
            continue
        pages = [t for t in tabs if t['type'] == 'page'
                 and (t.get('url') or 'about:blank') != 'about:blank']
        if not pages:
            last = 'still on %s' % ([t.get('url') for t in tabs] or 'nothing')
            time.sleep(0.25)
            continue
        got = devtools(port, 'document.readyState', timeout=10)
        if got == 'complete':
            return True, 'ready'
        last = 'readyState=%r' % (got,)
        time.sleep(0.25)
    return False, last


PROBE = r"""
(async () => {
  const out = {errors: [], steps: []};
  try {
  const el = (id) => document.getElementById(id);
  const wait = (ms) => new Promise(r => setTimeout(r, ms));
  // setPointerCapture throwing is an artefact of THIS probe, not a page bug.
  // three.js OrbitControls calls it on pointerdown, and the spec says it throws when
  // no pointer with that id is active -- which is always true of a synthetic event
  // and never true of a real mouse. It is recorded separately so a genuine error is
  // still a failure and this one does not cry wolf on every run.
  window.addEventListener('error', (e) => {
    const m = String(e.message);
    if (m.indexOf('setPointerCapture') >= 0) {
      out.synthetic = (out.synthetic || 0) + 1;
      return;
    }
    out.errors.push(m);
  });
  // The page is an ES module, so its top-level code runs deferred; evaluating before
  // it has finished gives an unwired page that looks like a broken one.
  for (let i = 0; i < 240; i += 1) {
    if (window.__rl_ready) break;
    await wait(250);
  }
  if (!window.__rl_ready) {
    out.errors.push('the page never finished loading');
    return out;
  }
  // Before a level is open: Open disabled, everything else disabled.
  out.before = {
    closeHidden: el('closelevel').hidden,
    load: el('load').disabled,
    openkit: el('openkit').disabled,
    build: el('build').disabled,
  };
  // Wait for the level list before choosing one: setting a value the select does not
  // have yet leaves it empty and loads nothing.
  el('level').value = 'Scenario_05';
  el('level').dispatchEvent(new Event('change'));
  el('load').click();
  for (let i = 0; i < 400; i += 1) {
    await wait(500);
    if (el('busy').style.display === 'none') break;
  }
  out.steps.push('level loaded: ' + el('wiring').textContent.slice(0, 60));
  out.after = {
    closeHidden: el('closelevel').hidden,
    load: el('load').disabled,
    openkit: el('openkit').disabled,
    build: el('build').disabled,
  };
  out.wiring_pickable = window.__rl_wiringCount();
  out.plan_before = window.__rl_planCounts();
  // Place one of each by calling the same path a click takes.
  let i = 0;
  for (const kind of ['rift', 'spawner', 'door', 'forcefield', 'point']) {
    i += 1;
    window.__rl_place(kind, [1000 + i * 300, 1000, 1200]);
    await wait(60);
    out.fields = out.fields || {};
    out.fields[kind] = [...el('propsbody').querySelectorAll('[data-field]')]
      .map(n => n.dataset.field);
    out.ids = out.ids || {};
    out.ids[kind] = window.__rl_selectedNow();
    out.steps.push(kind + ' placed; cls: ' +
      ((el('propsbody').querySelector('[data-field="cls"]') || {}).value || 'n/a') +
      '; panel shown: ' +
      el('app').classList.contains('props') +
      '; fields: ' + el('propsbody').querySelectorAll('input,select').length +
      '; gizmo: ' + window.__rl_gizmoAttached());
  }
  // ---- selecting a PLACED point by clicking it -------------------------------
  // The reported failure is that selecting a path point leaves the panel showing the
  // previous object. Placing one is a different path, so this clicks the marker for
  // real: through pick(), through the click handler, exactly as a user does.
  out.select = {};
  try {
    // Panel currently shows the last thing placed (the point). Select the DOOR first
    // so the panel definitely holds something else, then click the point.
    // Derived, not hardcoded: adding a fifth placement shifted the ids and the
    // hardcoded pair silently stopped pointing at anything.
    const doorSel = out.ids.door, ptSel = out.ids.point;
    const doorAt = doorSel && window.__rl_screenOf(doorSel.kind, doorSel.id);
    const ptAt = ptSel && window.__rl_screenOf(ptSel.kind, ptSel.id);
    out.select.screen = { door: doorAt, point: ptAt };
    const clickAt = (p) => {
      const o = { clientX: p.x, clientY: p.y, bubbles: true, cancelable: true };
      const c = document.querySelector('canvas');
      c.dispatchEvent(new PointerEvent('pointerdown', o));
      c.dispatchEvent(new MouseEvent('click', o));
    };
    if (doorAt && ptAt) {
      clickAt(doorAt);
      await wait(120);
      out.select.after_door = {
        selected: window.__rl_selectedNow(),
        panel: el('propsbody').textContent.replace(/\s+/g, ' ').slice(0, 60),
      };
      clickAt(ptAt);
      await wait(120);
      out.select.after_point = {
        selected: window.__rl_selectedNow(),
        panel: el('propsbody').textContent.replace(/\s+/g, ' ').slice(0, 60),
      };
      out.select.verdict =
        (out.select.after_point.selected
         && out.select.after_point.selected.kind === 'point'
         && /^\s*(OMDPath)?point/i.test(out.select.after_point.panel))
          ? 'the panel follows the clicked point'
          : 'PANEL DID NOT FOLLOW THE CLICK';
    } else {
      out.select.verdict = 'could not project the markers to screen';
    }
  } catch (e) { out.select.threw = String(e && e.message ? e.message : e); }

  out.panel_html_len = el('propsbody').innerHTML.length;
  // Measure whether the panel is actually ON SCREEN. Checking the DOM alone said it
  // was fine while nothing was visible, which is the gap that wasted a round trip.
  const rect = (id) => {
    const r = el(id).getBoundingClientRect();
    return {w: Math.round(r.width), h: Math.round(r.height),
            l: Math.round(r.left), t: Math.round(r.top)};
  };
  out.rects = {app: rect('app'), side: rect('side'), view: rect('view'),
               props: rect('props'), propsbody: rect('propsbody')};
  el('openkit').click();
  await wait(400);
  out.rects_with_kit = {side: rect('side'), kit: rect('kit'), view: rect('view'),
                        props: rect('props')};
  out.offscreen = Object.entries(out.rects_with_kit)
    .filter(([, r]) => r.l + r.w > window.innerWidth + 1 || r.w === 0)
    .map(([k]) => k);
  out.app_classes = el('app').className;
  out.props_style = (() => {
    const cs = getComputedStyle(el('props'));
    return {display: cs.display, visibility: cs.visibility, width: cs.width,
            overflow: cs.overflow};
  })();
  out.app_columns = getComputedStyle(el('app')).gridTemplateColumns;
  out.window = {w: window.innerWidth, h: window.innerHeight};
  // Is the sky actually drawn, and did its texture load?
  out.sky = window.__rl_skyReport();
  out.branch_editor = el('propsbody').textContent.includes('Goes next to');
  out.branch_controls = el('propsbody').querySelectorAll('[data-branch]').length;
  out.panel_text = el('propsbody').textContent.replace(/\s+/g, ' ').slice(0, 1200);
  out.plan_after = window.__rl_planCounts();
  //: WAIT FOR THE LEVEL TO FINISH DRAWING before counting anything.
  //: Geometry arrives in slices, so a probe that reads straight away reports a level
  //: of 48 meshes out of 226 and a sky it has not reached yet -- and "sky: 0
  //: children" reads as a broken sky rather than an early measurement.
  try {
    for (let i = 0; i < 120; i++) {
      const b = document.getElementById('busy');
      if (b && !b.textContent.trim()) break;
      await wait(500);
    }
  } catch (e) { out.errors.push('busy wait: ' + (e && e.message ? e.message : e)); }
  //: Doors are the one gameplay object whose facing matters, so the probe reports
  //: the mesh layer rather than trusting that it drew.
  try {
    //: POLL, do not guess a delay. An interior portcullis is 7.5MB of JSON and the
    //: first read also has to open the pak, so a fixed wait reported zero meshes for
    //: a layer that was simply still loading -- a false failure in the one check
    //: meant to catch a real one.
    //: Wait for the count to STOP CHANGING, not for the first mesh to appear.
    //: Breaking on `drawn > 0` reported 1 of 2 doors, because the second was still
    //: loading -- a check that stops at the first sign of life cannot tell a partial
    //: draw from a complete one.
    let dm = null;
    let stable = 0;
    let last = -1;
    for (let i = 0; i < 80; i++) {
      dm = window.__rl_doorMeshes ? window.__rl_doorMeshes() : null;
      const n = dm ? dm.drawn : -1;
      stable = (n === last) ? stable + 1 : 0;
      last = n;
      if (stable >= 4 && (n > 0 || !dm || !dm.planned)) break;
      await wait(500);
    }
    out.door_meshes = dm || 'no hook';
    out.forcefields = window.__rl_forcefields ? window.__rl_forcefields() : 'no hook';
    out.layers = window.__rl_layerCounts ? window.__rl_layerCounts() : 'no hook';
  } catch (e) { out.errors.push('door meshes: ' + (e && e.message ? e.message : e)); }
  // Removing a level door hides its drawn parts and its marker; undo brings them back.
  try {
    const name = window.__rl_levelDoorNames()[0];
    out.door_remove = { door: name, before: window.__rl_doorParts(name) };
    window.__rl_pickWiring(name);
    window.__rl_removeWiringPick();
    out.door_remove.after = window.__rl_doorParts(name);
    out.door_remove.markerVisible = window.__rl_wiringVisible(name);
    window.__rl_undo();
    out.door_remove.undone = window.__rl_doorParts(name);
    out.door_remove.markerUndone = window.__rl_wiringVisible(name);
  } catch (e) { out.errors.push('door remove: ' + (e && e.message ? e.message : e)); }
  // F drops a piece added in the air onto the floor at the rift.
  try {
    const rift = window.__rl_riftAt();
    const n = await window.__rl_addMesh(window.__rl_anyMeshName(),
                                        [rift[0], rift[1], rift[2] + 3000]);
    for (let i = 0; i < 40 && window.__rl_addedBottom(n - 1) === null; i++) await wait(250);
    window.__rl_pickAdded(n - 1);
    out.drop = { riftZ: Math.round(rift[2]), before: window.__rl_addedBottom(n - 1) };
    out.drop.ran = window.__rl_dropPicked();
    out.drop.after = window.__rl_addedBottom(n - 1);
    out.drop.said = el('saved').textContent;
    window.__rl_undo();
    await wait(500);
    out.drop.undone = window.__rl_addedBottom(n - 1);
  } catch (e) { out.errors.push('drop: ' + (e && e.message ? e.message : e)); }
  // Fab libraries: open the browser, place an opening wall by clicking its card, untick
  // one of its suggested grids. Skipped (and said so) when no library is installed.
  try {
    out.fab = {};
    el('kitsource').querySelector('[data-source="fab"]').click();
    for (let i = 0; i < 240 && !el('fabgrid').querySelector('[data-fab]')
         && !/No Fab libraries|cannot be read/.test(el('fabcount').textContent); i++) await wait(250);
    out.fab.packs = el('fabpack').options.length;
    out.fab.count = el('fabcount').textContent;
    const want = [...el('fabpack').options].find(o => o.value === 'Egypt' && !o.disabled);
    if (want) {
      el('fabpack').value = 'Egypt';
      el('fabpack').dispatchEvent(new Event('change'));
      for (let i = 0; i < 240 && !el('fabgrid').querySelector('[data-object*="SM_wall_05"]'); i++) await wait(250);
      el('fabfilters').querySelector('[data-fabkind="opening"]').click();
      await wait(200);
      out.fab.openingCards = el('fabgrid').querySelectorAll('[data-fab]').length;
      const card = el('fabgrid').querySelector('[data-object*="SM_wall_05.SM_wall_05"]');
      card.click();
      for (let i = 0; i < 240; i++) {
        const s = window.__rl_fabState();
        if (s.entries.length && s.entries[0].drawn) break;
        await wait(250);
      }
      const before = window.__rl_fabState();
      out.fab.placed = before.entries[0];
      const boxes = el('propsbody').querySelectorAll('[data-fabgrid]');
      out.fab.gridBoxes = boxes.length;
      if (boxes.length) {
        boxes[0].click();
        await wait(100);
      }
      await wait(600);
      const after = window.__rl_fabState();
      out.fab.gridsAfterUntick = after.entries[0].grids.length;
      out.fab.openings = after.openings;
      out.fab.layerItems = after.layerItems;
      out.fab.verdict = (before.entries.length === 1 && before.entries[0].drawn > 0 &&
        boxes.length > 0 && after.entries[0].grids.length === before.entries[0].grids.length - 1 &&
        after.openings > 0) ? 'Fab piece placed, drawn, grid toggled, opening marked'
        : 'FAB BROWSER DID NOT WORK';
      if (!/^Fab piece placed/.test(out.fab.verdict)) out.errors.push('fab: ' + out.fab.verdict);
      window.__rl_undo();
      window.__rl_undo();
    } else {
      out.fab.verdict = 'skipped: no readable Egypt library';
    }
  } catch (e) { out.errors.push('fab: ' + (e && e.message ? e.message : e)); }
  } catch (err) { out.errors.push('probe: ' + (err && err.message ? err.message : err)); }
  return out;
})()
"""


def main():
    httpd, port = S.serve(0)
    url = 'http://127.0.0.1:%d/' % port
    profile = tempfile.mkdtemp(prefix='rl-selftest-', dir=PROFILE_DIR)
    proc = subprocess.Popen(
        # Software WebGL, explicitly opted into: without it `new WebGLRenderer()`
        # throws at module load and the page looks broken for the wrong reason.
        # A realistic window, because the bug this test exists to catch was a layout
        # overflow that only shows at a given width.
        [CHROME, '--headless=new', '--enable-unsafe-swiftshader',
         '--window-size=1600,900', '--no-sandbox', '--remote-debugging-port=9333',
         '--user-data-dir=' + profile, url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ok, why = wait_ready(9333, url)
        if not ok:
            print(json.dumps({'page_never_became_ready': why}, indent=1))
            return 1
        got = devtools(9333, PROBE)
        #: One retry, and only for the race this harness cannot rule out: a page that
        #: re-navigated after readyState said complete. Anything else is reported as
        #: it came back, because retrying a real failure just hides it.
        if isinstance(got, dict) and 'destroyed' in str(got.get('devtools_error', '')):
            ok, why = wait_ready(9333, url)
            got = devtools(9333, PROBE) if ok else {
                'page_never_became_ready': why}
        print(json.dumps(got, indent=1))
        if not isinstance(got, dict):
            return 1
        return 0 if not (got.get('errors') or got.get('devtools_error')
                         or got.get('threw')) else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)
        httpd.shutdown()


if __name__ == '__main__':
    raise SystemExit(main())
