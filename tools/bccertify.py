"""The ISPC encoder's certificate in a build comparison: which pak bytes it accounts for, and
that each texture it encoded is about as good as the Python encoder's.

    python3 tools/bccertify.py OLD.pak NEW.pak RECORD_DIR

RECORD_DIR holds the new build's ISPC encodes (RIFTLORD_BC_RECORD: <sha1 of the output>.npz
with the source and format). An entry that differs between the old build (Python encoder)
and the new one is explained when both are the same length and every differing byte lies
inside a span where the new entry holds one recorded ISPC output. Each such span is then
graded: the ISPC output and the old build's bytes at the same place (the Python encoder's
output of the same source) are decoded and compared with the source; ISPC may exceed the
Python encoder's RMSE by at most SLACK (0-255 scale), colour (alpha-weighted) and alpha.
"""

import glob
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import numpy as np                                    # noqa: E402

#: How much larger the ISPC RMSE may be than the Python encoder's (0-255 scale).
SLACK = 0.05
#: The formats the grading decodes (lightcodec.decode); others are explained, not graded.
GRADED = ('PF_DXT1', 'PF_DXT5')
#: Outputs shorter than this (the smallest mips) explain bytes but are not graded: so short a
#: run of bytes can also match by chance elsewhere in an entry.
GRADE_MIN = 256


def records(folder):
    """{sha1: (source rgba, fmt)} of the recorded encodes."""
    out = {}
    for path in sorted(glob.glob(os.path.join(glob.escape(folder), '*.npz'))):
        if '.part' in os.path.basename(path):
            continue
        with np.load(path) as z:
            out[os.path.basename(path)[:-4]] = (z['rgba'], str(z['fmt']))
    return out


def outputs(recs):
    """{sha1: ISPC output bytes}, re-encoded from each source (the encoder is deterministic;
    a mismatch with the recorded digest raises)."""
    from riftlord.formats.ue import ispctex
    out = {}
    for sha, (rgba, fmt) in recs.items():
        data = ispctex.encode(rgba, fmt)
        if hashlib.sha1(data).hexdigest() != sha:
            raise SystemExit('ISPC output of %s does not match its record' % sha)
        out[sha] = data
    return out


def rmse(decoded, source, fmt):
    a = decoded.astype(np.float64)
    s = source.astype(np.float64)
    w = s[..., 3:4] / 255.0                          # colour error counts where it shows
    colour = np.sqrt((((a[..., :3] - s[..., :3]) ** 2) * w).sum() / max(w.sum() * 3, 1))
    alpha = np.sqrt(((a[..., 3] - s[..., 3]) ** 2).mean()) if fmt == 'PF_DXT5' else 0.0
    return colour, alpha


def explain(old, new, recs, outs):
    """(explained, grades) for one differing entry: explained when every differing byte is
    inside a recorded ISPC output found at that place; grades: one row per output found
    there (GRADED formats, GRADE_MIN bytes or more).

    Outputs are looked up by their first bytes, walking back from each still-unexplained
    differing byte: a texture's bytes differ from its first blocks on, so the start is near
    (no search of whole entries for thousands of outputs)."""
    from riftlord.build.lighting import lightcodec
    if len(old) != len(new):
        return False, []
    x, y = np.frombuffer(old, np.uint8), np.frombuffer(new, np.uint8)
    diff = np.flatnonzero(x != y)
    if not diff.size:
        return False, []
    anchors = {}                                  # {key length: {first bytes: [sha1]}}
    for sha, data in outs.items():
        key = min(16, len(data))
        anchors.setdefault(key, {}).setdefault(data[:key], []).append(sha)
    longest = max(len(d) for d in outs.values())
    grades = []
    i = 0
    while i < len(diff):
        d = int(diff[i])
        found = False
        for k in range(int(d), max(-1, int(d) - longest), -1):
            for key, table in anchors.items():
                for sha in table.get(new[k:k + key], ()):
                    data = outs[sha]
                    if k + len(data) > d and new[k:k + len(data)] == data:
                        i = int(np.searchsorted(diff, k + len(data)))   # past this output
                        rgba, fmt = recs[sha]
                        if fmt in GRADED and len(data) >= GRADE_MIN:
                            h, w = rgba.shape[:2]
                            ref = old[k:k + len(data)]
                            py = rmse(lightcodec.decode(fmt, w, h, ref), rgba, fmt)
                            ispc = rmse(lightcodec.decode(fmt, w, h, data), rgba, fmt)
                            grades.append({'texture': sha[:12], 'fmt': fmt, 'size': [w, h],
                                           'python': [round(float(v), 3) for v in py],
                                           'ispc': [round(float(v), 3) for v in ispc],
                                           'ok': bool(ispc[0] <= py[0] + SLACK and
                                                      ispc[1] <= py[1] + SLACK)})
                        found = True
                        break
                if found:
                    break
            if found:
                break
        if not found:
            return False, grades
    return True, grades


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    from riftlord.core import paksource
    old, new = paksource.entries(argv[0]), paksource.entries(argv[1])
    recs = records(argv[2])
    outs = outputs(recs)
    rows = []
    for name in sorted(set(old) & set(new)):
        if old[name] != new[name]:
            ok, grades = explain(old[name], new[name], recs, outs)
            rows.append({'entry': name, 'explained': ok, 'grades': grades})
    print(json.dumps(rows, indent=1))
    return 0 if all(r['explained'] and all(g['ok'] for g in r['grades']) for r in rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
