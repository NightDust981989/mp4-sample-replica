"""Reproduce the TikQuick v25 (ZETA_OPTIMIZED/3) container patch on an MP4.

Usage:
    python -I tikquick_v25.py <input.mp4> <output.mp4> [tag]
                                [--sig TEXT] [--time ISO8601]

Observed output layout (from a real TikQuick upload):

    ftyp | free(old moov, byte-identical) | free 8 | mdat(s)
         | mdat(4 dummy audio samples) | moov(new) | free(signature)

The patcher leaves the video track untouched and adds a fake tail to the
audio track: the last four one-sample chunks are replaced by a single
four-sample chunk pointing at a new 68-byte mdat whose payload is four
copies of a fixed 15-byte dummy frame.  The original moov is kept as a
`free` box of the same size (so mdat offsets never move), and a rebuilt
moov is appended at the end.  The signature box records
"ZETA_OPTIMIZED/3 <time> <hash>_raw_<name>".

Verified input layout: ftyp + moov + free 8 + mdat, audio chunks with one
sample each.  Other layouts are not handled.
"""
import hashlib
import struct
import sys
import datetime

NEST = {b"moov", b"trak", b"edts", b"mdia", b"minf", b"stbl", b"dinf"}
DUMMY = bytes.fromhex("a0a000400128006400000350000038")  # 15 B
TAIL_SAMPLES = 4
TAG = "TikQuick Quality Method - https://tikquick.online/ - v25 - Orion"


def u32(b, o):
    return struct.unpack(">I", b[o:o + 4])[0]


def p32(v):
    return struct.pack(">I", v)


class Node:
    def __init__(self, t, data=None, ch=None, raw=None):
        self.t = t
        self.data = data
        self.ch = ch
        self.raw = raw


def parse(d, s, e):
    out, i = [], s
    while i + 8 <= e:
        size = u32(d, i)
        typ = d[i + 4:i + 8]
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", d[i + 8:i + 16])[0]
            hdr = 16
        elif size == 0:
            size = e - i
        if size < hdr or i + size > e:
            raise ValueError("bad box at %d" % i)
        out.append((typ, i, i + hdr, i + size))
        i += size
    return out


def build(d, s, e):
    nodes = []
    for typ, start, p, end in parse(d, s, e):
        if typ in NEST:
            nodes.append(Node(typ, ch=build(d, p, end)))
        else:
            nodes.append(Node(typ, data=d[p:end]))
    return nodes


def ser(n):
    if n.raw is not None:
        return n.raw
    body = b"".join(ser(c) for c in n.ch) if n.ch is not None else n.data
    return p32(8 + len(body)) + n.t + body


def find(nodes, typ):
    for n in nodes:
        if n.t == typ:
            return n
    return None


def stbl_of(trak):
    mdia = find(trak.ch, b"mdia")
    minf = find(mdia.ch, b"minf") if mdia else None
    return find(minf.ch, b"stbl") if minf else None


def make_trak_udta(tag):
    """udta/meta/hdlr(mdir)/ilst/©too, as emitted by TikQuick."""
    text = tag.encode() + bytes([0])
    data = p32(16 + len(text)) + b"data" + b"\x00\x00\x00\x01\x00\x00\x00\x00" + text
    item = p32(8 + len(data)) + b"\xa9too" + data
    ilst = p32(8 + len(item)) + b"ilst" + item
    hdlr = p32(33) + b"hdlr" + b"\x00" * 8 + b"mdir" + b"\x00" * 13
    meta = p32(8 + 4 + len(hdlr) + len(ilst)) + b"meta" + b"\x00" * 4 + hdlr + ilst
    return p32(8 + len(meta)) + b"udta" + meta


def main(argv):
    tag = TAG
    sig_text = None
    stamp = None
    plain = []
    it = iter(argv)
    for a in it:
        if a == "--sig":
            sig_text = next(it)
        elif a == "--time":
            stamp = next(it)
        elif not a.startswith("--"):
            plain.append(a)
    if len(plain) < 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    src, dst = plain[0], plain[1]
    if len(plain) > 2:
        tag = plain[2]

    d = open(src, "rb").read()
    top = parse(d, 0, len(d))
    ftyp = next((b for b in top if b[0] == b"ftyp"), None)
    moov_b = next((b for b in top if b[0] == b"moov"), None)
    mdat = next((b for b in top if b[0] == b"mdat"), None)
    if not ftyp or not moov_b or not mdat:
        raise ValueError("input needs ftyp, moov and mdat")
    if moov_b[1] > mdat[1]:
        raise ValueError("moov must come before mdat (faststart input)")

    ftyp_raw = d[ftyp[1]:ftyp[3]]
    moov_raw = d[moov_b[1]:moov_b[3]]
    between = d[moov_b[3]:mdat[1]]
    mdat_raw = b"".join(d[a:b] for t, a, p, b in top if t == b"mdat")

    moov_ch = build(d, moov_b[2], moov_b[3])
    video = audio = None
    for trak in (c for c in moov_ch if c.t == b"trak"):
        mdia = find(trak.ch, b"mdia")
        hdlr = find(mdia.ch, b"hdlr") if mdia else None
        kind = hdlr.data[8:12] if hdlr else b""
        if kind == b"vide" and video is None:
            video = trak
        elif kind == b"soun" and audio is None:
            audio = trak
    if video is None or audio is None:
        raise ValueError("need one video and one audio track")

    stbl = stbl_of(audio)
    stco = find(stbl.ch, b"stco")
    stsc = find(stbl.ch, b"stsc")
    stsz = find(stbl.ch, b"stsz")
    if not stco or not stsc or not stsz:
        raise ValueError("audio track needs stco/stsc/stsz")
    offsets = [u32(stco.data, 8 + 4 * k) for k in range(u32(stco.data, 4))]
    entries = [(u32(stsc.data, 8 + 12 * k), u32(stsc.data, 12 + 12 * k),
                u32(stsc.data, 16 + 12 * k))
               for k in range(u32(stsc.data, 4))]
    if entries != [(1, 1, 1)]:
        raise ValueError("audio layout is not one sample per chunk")
    n = len(offsets)
    if n <= TAIL_SAMPLES or n != u32(stsz.data, 8):
        raise ValueError("audio chunk count does not match sample count")

    if u32(stsz.data, 4) != 0:
        raise ValueError("fixed-size audio stsz unsupported")
    sizes = [u32(stsz.data, 12 + 4 * k) for k in range(n)]
    sizes[-TAIL_SAMPLES:] = [len(DUMMY)] * TAIL_SAMPLES
    stsz.data = b"\x00" * 8 + p32(n) + b"".join(p32(s) for s in sizes)

    video.ch.append(Node(b"udta", raw=make_trak_udta(tag)))
    moov_ch = [c for c in moov_ch if c.t != b"udta"]

    free_old = p32(len(moov_raw)) + b"free" + moov_raw[8:]
    dummy_payload = DUMMY * TAIL_SAMPLES
    new_chunks = n - TAIL_SAMPLES + 1

    def assemble(dummy_off):
        stco.data = (b"\x00" * 4 + p32(new_chunks)
                     + b"".join(p32(o) for o in offsets[:-TAIL_SAMPLES])
                     + p32(dummy_off))
        stsc.data = (b"\x00" * 4 + p32(len(entries) + 1)
                     + b"".join(p32(a) + p32(b) + p32(c) for a, b, c in entries)
                     + p32(new_chunks) + p32(TAIL_SAMPLES) + p32(1))
        return b"".join(ser(c) for c in moov_ch)

    assemble(0)
    dummy_off = len(ftyp_raw) + len(free_old) + len(between) + len(mdat_raw) + 8
    real_moov = assemble(dummy_off)

    if sig_text is None:
        stamp = stamp or (datetime.datetime.now(datetime.timezone.utc)
                          .isoformat(timespec="milliseconds")
                          .replace("+00:00", "Z"))
        sig_text = "ZETA_OPTIMIZED/3 %s %s_raw_%s" % (
            stamp, hashlib.md5(d).hexdigest(), src.replace("\\", "/").rsplit("/", 1)[-1])
    sig_raw = p32(8 + len(sig_text)) + b"free" + sig_text.encode()

    out = (ftyp_raw + free_old + between + mdat_raw
           + p32(8 + len(dummy_payload)) + b"mdat" + dummy_payload
           + p32(8 + len(real_moov)) + b"moov" + real_moov + sig_raw)
    open(dst, "wb").write(out)
    print("wrote", dst, len(out), "bytes; audio chunks", n, "->", new_chunks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
