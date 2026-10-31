"""A mission's own dialogue lines: silent SoundWaves whose subtitle cue the game's closed
captions draw (docs/research/CLOSED-CAPTIONS.md).

OMD3 captions are the engine's subtitles. A SoundWave's `Subtitles` (FSubtitleCue: Text,
Time) are queued with the subtitle manager when the wave starts playing, drawn in the
region OMDSubtitlesSettings gives (X 0.2-0.8, bottom at Y 0.75) in the OMD subtitle font,
cleared at the wave's `Duration`, and drawn only while the player's Closed Captioning
setting is on (it sets GEngine->bSubtitlesEnabled). Every shipped captioned line has one
cue at time 0 whose text is "<Speaker>: <line>".

A line here is a copy of a shipped captioned wave (stereo 48 kHz Ogg Vorbis, inline bulk
data) with:

- its audio replaced by digital silence cut to the line's length (the shipped template
  `data/riftlord/caption-silence.ogg`, its last kept Ogg page ending the stream at the
  line's sample count);
- `Duration` and `TotalSamples` set to that length;
- one subtitle cue at time 0 holding "<Speaker>: <text>" as a plain localisable FText
  (namespace and key of the mission's own; the source string shows in every language).

Recorded audio later replaces the silence and keeps the cue.
"""

import hashlib
import os
import struct

#: The shipped captioned wave cloned for every line (stereo, 48 kHz, one cue).
DONOR = '/Game/Sounds/Dialogue/Cygnus/OMD3_Cygnus_Reaction_Ghorbash_000'
#: The silence every line's audio is cut from: stereo 48 kHz Ogg Vorbis, 20 s.
SILENCE = 'riftlord/caption-silence.ogg'
SAMPLE_RATE = 48000
CHANNELS = 2
#: Shortest and longest shipped captioned line (261 measured), in seconds: 0.71 and 19.94.
DURATION_MIN = 0.7
DURATION_MAX = 20.0
#: Longest shipped caption's spoken text (after "<Speaker>: "), in characters.
TEXT_MAX = 160
#: Longest shipped speaker name ("Sorceress"), and the room a mod's own may take.
SPEAKER_MAX = 24
#: A line's length from its text when none is set: the least-squares fit of the 261
#: shipped captioned lines' durations on their spoken text length (r^2 0.59).
DURATION_BASE = 1.274
DURATION_PER_CHAR = 0.0596
#: Steps per second a derived duration is rounded to.
DURATION_STEPS = 10

#: Export payload layout after the tagged properties (measured on the donor):
#: int32 0 (no object guid), int32 1 (cooked), int32 1 (one format), FName format,
#: FByteBulkData header (flags, count, size on disk, int64 offset in file), the data,
#: then the 16-byte CompressedDataGuid.
FORMAT_NAME = 'OGG00000000'
BULK_INLINE = 0x48
#: FTextHistory type of a plain localisable text (namespace, key, source string).
TEXT_HISTORY_BASE = 0


def derived_duration(text):
    """Seconds a line of `text` lasts when none is set (the shipped lines' fit)."""
    import math
    n = len((text or '').strip())
    raw = DURATION_BASE + DURATION_PER_CHAR * n
    #: halves round up, as the Onslaught Editor's lineLength rounds them
    stepped = math.floor(raw * DURATION_STEPS + 0.5) / DURATION_STEPS
    return round(max(DURATION_MIN, min(DURATION_MAX, stepped)), 1)


def caption(speaker, text):
    """The subtitle text the game draws, as shipped lines spell it."""
    speaker = (speaker or '').strip()
    text = (text or '').strip()
    return '%s: %s' % (speaker, text) if speaker else text


def problems(speaker, text, duration):
    """[text]: what keeps a line from being written."""
    out = []
    text = text if isinstance(text, str) else ''
    speaker = speaker if isinstance(speaker, str) else ''
    if not text.strip():
        out.append('no text')
    if len(text.strip()) > TEXT_MAX:
        out.append('text longer than %d characters' % TEXT_MAX)
    if len(speaker.strip()) > SPEAKER_MAX:
        out.append('speaker longer than %d characters' % SPEAKER_MAX)
    if ':' in speaker:
        out.append('speaker holds a colon')
    for part in (speaker, text):
        if any(ord(c) < 32 for c in part):
            out.append('a line break or control character')
            break
    if duration is not None:
        try:
            d = float(duration)
        except (TypeError, ValueError):
            d = None
        if d is None or not DURATION_MIN <= d <= DURATION_MAX:
            out.append('length outside %.1f-%.0f seconds' % (DURATION_MIN, DURATION_MAX))
    return out


# -- Ogg -------------------------------------------------------------------------------

def _crc_table():
    out = []
    for i in range(256):
        r = i << 24
        for _ in range(8):
            r = ((r << 1) ^ 0x04C11DB7) if r & 0x80000000 else (r << 1)
        out.append(r & 0xFFFFFFFF)
    return out


_CRC = _crc_table()


def _page_crc(page):
    crc = 0
    for byte in page:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC[((crc >> 24) & 0xFF) ^ byte]
    return crc


def pages(data):
    """[(offset, header type, granule, page bytes)] of an Ogg stream."""
    out = []
    o = 0
    while o < len(data):
        if data[o:o + 4] != b'OggS':
            raise ValueError('no Ogg page at %d' % o)
        htype, gran = struct.unpack_from('<Bq', data, o + 5)
        nseg = data[o + 26]
        size = 27 + nseg + sum(data[o + 27:o + 27 + nseg])
        out.append((o, htype, gran, data[o:o + size]))
        o += size
    return out


def cut(data, samples):
    """`data` (Ogg Vorbis) ending at `samples` per channel: the pages up to the first audio
    page reaching it, that page's granule set to `samples` and marked the stream's end."""
    kept = []
    for _o, htype, gran, page in pages(data):
        page = bytearray(page)
        nseg = page[26]
        whole = nseg == 0 or page[27 + nseg - 1] < 255     # its last packet ends here
        if gran > 0 and gran >= samples and whole:
            struct.pack_into('<q', page, 6, samples)
            page[5] = htype | 0x04
            struct.pack_into('<I', page, 22, 0)
            struct.pack_into('<I', page, 22, _page_crc(page))
            kept.append(bytes(page))
            return b''.join(kept)
        kept.append(bytes(page))
    raise ValueError('the silence is shorter than %d samples' % samples)


def silence(seconds):
    """Ogg Vorbis silence of `seconds` (stereo, 48 kHz): (bytes, samples per channel)."""
    from riftlord.core import gamepath
    with open(gamepath.data(SILENCE), 'rb') as fh:
        template = fh.read()
    samples = int(round(float(seconds) * SAMPLE_RATE))
    return cut(template, samples), samples


# -- the SoundWave ---------------------------------------------------------------------

def _fstring(s):
    try:
        raw = s.encode('ascii') + b'\0'
        return struct.pack('<i', len(raw)) + raw
    except UnicodeEncodeError:
        raw = s.encode('utf-16-le') + b'\0\0'
        return struct.pack('<i', -(len(raw) // 2)) + raw


class _Props:
    """Tagged property bytes against a package's name table."""

    def __init__(self, pkg):
        self.pkg = pkg

    def name(self, text):
        return struct.pack('<ii', self.pkg.add_name(text), 0)

    def tag(self, name, kind, size):
        return self.name(name) + self.name(kind) + struct.pack('<ii', size, 0)

    def int(self, name, value):
        return self.tag(name, 'IntProperty', 4) + b'\0' + struct.pack('<i', value)

    def float(self, name, value):
        return self.tag(name, 'FloatProperty', 4) + b'\0' + struct.pack('<f', value)

    def enum(self, name, enum, value):
        return self.tag(name, 'EnumProperty', 8) + self.name(enum) + b'\0' + self.name(value)

    def text(self, name, namespace, key, source):
        body = (struct.pack('<i', 0) + struct.pack('<b', TEXT_HISTORY_BASE)
                + _fstring(namespace) + _fstring(key) + _fstring(source))
        return self.tag(name, 'TextProperty', len(body)) + b'\0' + body

    def none(self):
        return self.name('None')


def wave(path, speaker, text, duration, namespace, key):
    """(uasset, uexp) of a silent SoundWave at package `path` lasting `duration` seconds,
    with one subtitle cue at time 0 reading `caption(speaker, text)`."""
    from riftlord.core import gamefiles
    from riftlord.build import modtable
    from riftlord.formats.ue.uassetio import Package
    bad = problems(speaker, text, duration)
    if bad:
        raise SystemExit('dialogue line %s not written: %s' % (key, '; '.join(bad)))
    audio, samples = silence(duration)
    seconds = samples / float(SAMPLE_RATE)
    pkg = Package(*gamefiles.package(DONOR))
    modtable.rename_package(pkg, DONOR, DONOR.rsplit('/', 1)[-1], path, path.rsplit('/', 1)[-1])
    p = _Props(pkg)
    cue = p.text('Text', namespace, key, caption(speaker, text)) + p.float('Time', 0.0) + p.none()
    element = p.tag('Subtitles', 'StructProperty', len(cue)) + p.name('SubtitleCue') \
        + b'\0' * 16 + b'\0'
    array = struct.pack('<i', 1) + element + cue
    props = (p.int('NumChannels', CHANNELS) + p.int('SampleRate', SAMPLE_RATE)
             + p.tag('Subtitles', 'ArrayProperty', len(array)) + p.name('StructProperty')
             + b'\0' + array
             + p.enum('VirtualizationMode', 'EVirtualizationMode',
                      'EVirtualizationMode::PlayWhenSilent')
             + p.float('Duration', seconds) + p.float('TotalSamples', float(samples))
             + p.none())
    head = props + struct.pack('<iii', 0, 1, 1) + p.name(FORMAT_NAME)
    guid = hashlib.md5(('%s\0%s\0%d' % (path, caption(speaker, text), samples))
                       .encode('utf-8')).digest()

    def payload(offset):
        return head + struct.pack('<Iiiq', BULK_INLINE, len(audio), len(audio), offset) \
            + audio + guid
    #: the bulk data's offset counts from the package's start: the header, then the
    #: export's bytes before the data (the header's size is fixed once the names are)
    pkg.exports[0]['data'] = payload(0)
    ua, _ux = pkg.write()
    at = len(ua) + len(head) + struct.calcsize('<Iiiq')
    pkg.exports[0]['data'] = payload(at)
    return pkg.write()


def read(ua, ux):
    """What a written (or shipped) wave holds: {duration, samples, channels, rate,
    cues: [{time, text}], audio: bytes} (the text of a string-table cue is its key)."""
    from riftlord.formats.ue.uassetio import Package
    pkg = Package(ua, ux)
    names = [n for n, _h in pkg.names]
    d = pkg.exports[0]['data']
    out = {'cues': []}

    def nm(o):
        i, num = struct.unpack_from('<ii', d, o)
        return names[i] if num == 0 else '%s_%d' % (names[i], num - 1)

    def fstr(o):
        n = struct.unpack_from('<i', d, o)[0]
        if n < 0:
            return d[o + 4:o + 4 - 2 * n].decode('utf-16-le').rstrip('\0'), o + 4 - 2 * n
        return d[o + 4:o + 4 + n].decode('utf-8').rstrip('\0'), o + 4 + n

    def props(o, sink):
        while True:
            name = nm(o)
            o += 8
            if name == 'None':
                return o
            kind = nm(o)
            size = struct.unpack_from('<i', d, o + 8)[0]
            o += 16
            if kind == 'ArrayProperty':
                o += 9
                end = o + size
                count = struct.unpack_from('<i', d, o)[0]
                o += 4 + 8 + 8 + 8 + 8 + 17
                for _ in range(count):
                    cue = {}
                    o = props(o, cue)
                    out['cues'].append(cue)
                o = end
            elif kind == 'EnumProperty':
                o += 9
                sink[name] = nm(o)
                o += size
            elif kind == 'TextProperty':
                o += 1
                hist = d[o + 4]
                if hist == TEXT_HISTORY_BASE:
                    _ns, q = fstr(o + 5)
                    _key, q = fstr(q)
                    sink['text'], _q = fstr(q)
                else:                               # a string table entry: its key
                    sink['text'], _q = fstr(o + 13)
                o += size
            elif kind == 'FloatProperty':
                sink[name] = struct.unpack_from('<f', d, o + 1)[0]
                o += 1 + size
            elif kind == 'IntProperty':
                sink[name] = struct.unpack_from('<i', d, o + 1)[0]
                o += 1 + size
            else:
                o += 1 + size

    top = {}
    o = props(0, top)
    o += 12 + 8
    flags, count, size, offset = struct.unpack_from('<Iiiq', d, o)
    o += struct.calcsize('<Iiiq')
    out.update(duration=top.get('Duration'), samples=top.get('TotalSamples'),
               channels=top.get('NumChannels'), rate=top.get('SampleRate'),
               virtualization=top.get('VirtualizationMode'), bulkFlags=flags,
               bulkOffset=offset, dataAt=len(ua) + o, audio=d[o:o + size])
    out['cues'] = [{'time': c.get('Time', 0.0), 'text': c.get('text')} for c in out['cues']]
    return out


def ogg_samples(data):
    """Samples per channel an Ogg stream ends at (its last page's granule)."""
    return pages(data)[-1][2]


def ogg_crc_ok(data):
    """True when every page's CRC matches its bytes."""
    for _o, _h, _g, page in pages(data):
        page = bytearray(page)
        want = struct.unpack_from('<I', page, 22)[0]
        struct.pack_into('<I', page, 22, 0)
        if _page_crc(page) != want:
            return False
    return True


def checks(ua, ux, speaker, text, duration):
    """[{name, ok, detail}] on a written line: it reads back with one cue at time 0 holding
    its caption, its length and sample count, inline Ogg data whose offset points at it,
    every page's CRC right and the stream ending at the line's length."""
    got = read(ua, ux)
    want = int(round(float(duration) * SAMPLE_RATE))
    audio = got['audio']
    return [
        {'name': 'one cue at 0 s with the caption', 'ok': len(got['cues']) == 1
         and got['cues'][0]['time'] == 0.0 and got['cues'][0]['text'] == caption(speaker, text),
         'detail': repr(got['cues'])},
        {'name': 'length and samples', 'ok': abs((got['duration'] or 0) - want / SAMPLE_RATE) < 1e-4
         and int(got['samples'] or 0) == want and got['channels'] == CHANNELS
         and got['rate'] == SAMPLE_RATE,
         'detail': '%s s, %s samples' % (got['duration'], got['samples'])},
        {'name': 'inline Ogg at its offset', 'ok': got['bulkFlags'] == BULK_INLINE
         and got['bulkOffset'] == got['dataAt'] and audio[:4] == b'OggS',
         'detail': 'flags 0x%x, offset %d, data at %d' % (got['bulkFlags'], got['bulkOffset'],
                                                         got['dataAt'])},
        {'name': 'Ogg pages whole and ending at the length',
         'ok': ogg_crc_ok(audio) and ogg_samples(audio) == want,
         'detail': '%d samples' % ogg_samples(audio)},
    ]


if __name__ == '__main__':
    import sys
    ua, ux = wave('/Game/Mods/CaptionTest/Dialogue/DX_Line1', 'Egan', 'Is this thing on?',
                  2.5, 'RiftLord.CaptionTest', 'Line1')
    for row in checks(ua, ux, 'Egan', 'Is this thing on?', 2.5):
        print('%s %s: %s' % ('ok ' if row['ok'] else 'BAD', row['name'], row['detail']))
    if len(sys.argv) > 1:
        os.makedirs(sys.argv[1], exist_ok=True)
        for blob, ext in ((ua, '.uasset'), (ux, '.uexp')):
            with open(os.path.join(sys.argv[1], 'DX_Line1' + ext), 'wb') as fh:
                fh.write(blob)
