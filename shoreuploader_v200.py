"""Reproduce the ShoreUploader v2.0.0 container patch on an MP4.

Usage:
    python -I shoreuploader_v200.py <input.mp4> <output.mp4>

v2.0.0 only rewrites metadata:

  * mvhd is rewritten as version 1 with duration 0xFFFFFFFFFFFFFFFF
  * creation/modification times in mvhd, tkhd and mdhd are zeroed
  * the video sample entry's compressor name becomes "ShoreUploader-v2.0.0"
  * moov/udta is replaced with the fixed tag set below

No samples are added or removed. The mdat payload is copied verbatim; only
stco/co64 offsets are shifted because the moov size changes.
"""
import struct
import sys

NEST = {b"moov", b"trak", b"edts", b"mdia", b"minf", b"stbl", b"udta"}
VIDEO_CODECS = {b"avc1", b"avc3", b"hvc1", b"hev1"}
COMPRESSOR = b"ShoreUploader-v2.0.0"
INFINITE_DURATION = 0xFFFFFFFFFFFFFFFF

# moov/udta exactly as emitted by ShoreUploader v2.0.0 (7 tags under ilst)
UDTA_HEX = (
    "0000015c75647461000001546d657461000000000000002168646c7200000000"
    "000000006d6469726170706c00000000000000000000000127696c7374000000"
    "25a94152540000001d64617461000000010000000053686f726555706c6f6164"
    "657200000025a96e616d0000001d64617461000000010000000053686f726555"
    "706c6f616465720000002ca9746f6f0000002464617461000000010000000053"
    "686f726555706c6f616465722d76322e302e300000002ca9656e630000002464"
    "617461000000010000000053686f726555706c6f616465722d76322e302e3000"
    "000025a97772740000001d64617461000000010000000053686f726555706c6f"
    "616465720000002ca9636d740000002464617461000000010000000053686f72"
    "6555706c6f616465722d76322e302e300000002c637072740000002464617461"
    "000000010000000053686f726555706c6f616465722d76322e302e30"
)


def u32(b, o):
    return struct.unpack(">I", b[o:o + 4])[0]


def u64(b, o):
    return struct.unpack(">Q", b[o:o + 8])[0]


def p32(v):
    return struct.pack(">I", v)


def p64(v):
    return struct.pack(">Q", v)


class Box:
    def __init__(self, typ, payload=None, children=None):
        self.typ = typ
        self.payload = payload
        self.children = children

    def build(self):
        if self.children is not None:
            body = b"".join(c.build() for c in self.children)
        else:
            body = self.payload
        size = 8 + len(body)
        if size <= 0xFFFFFFFF:
            return p32(size) + self.typ + body
        return p32(1) + self.typ + p64(16 + len(body)) + body


def parse(data, start, end):
    out, i = [], start
    while i + 8 <= end:
        size = u32(data, i)
        typ = bytes(data[i + 4:i + 8])
        hdr = 8
        if size == 1:
            size = u64(data, i + 8)
            hdr = 16
        elif size == 0:
            size = end - i
        if size < hdr or i + size > end:
            break
        payload = data[i + hdr:i + size]
        if typ in NEST:
            out.append(Box(typ, children=parse(data, i + hdr, i + size)))
        else:
            out.append(Box(typ, payload=payload))
        i += size
    return out


def find(nodes, typ):
    for n in nodes:
        if n.typ == typ:
            return n
        if n.children:
            hit = find(n.children, typ)
            if hit:
                return hit
    return None


def zero_times(payload):
    """Zero creation/modification time, keeping the box version."""
    b = bytearray(payload)
    if b[0] == 1:
        b[4:20] = b"\0" * 16
    else:
        b[4:12] = b"\0" * 8
    return bytes(b)


def mvhd_infinite(payload):
    """Rewrite mvhd as version 1, zero times, duration 0xFFFFFFFFFFFFFFFF."""
    if payload[0] == 0:
        return (b"\x01" + payload[1:4] + b"\0" * 16 + payload[12:16]
                + b"\xff" * 8 + payload[20:])
    return (payload[:4] + b"\0" * 16 + payload[20:24]
            + b"\xff" * 8 + payload[32:])


def set_compressor(payload):
    """Tag video sample entries with the v2.0.0 compressor name."""
    b = bytearray(payload)
    count = u32(b, 4)
    p = 8
    for _ in range(count):
        if p + 8 > len(b):
            break
        size = u32(b, p)
        typ = bytes(b[p + 4:p + 8])
        if typ in VIDEO_CODECS and p + 82 <= len(b):
            b[p + 50] = len(COMPRESSOR)
            b[p + 51:p + 82] = b"\0" * 31
            b[p + 51:p + 51 + len(COMPRESSOR)] = COMPRESSOR
        if size < 8:
            break
        p += size
    return bytes(b)


def shift_offsets(nodes, delta):
    """Add delta to every stco/co64 entry below nodes."""
    for n in nodes:
        if n.children:
            shift_offsets(n.children, delta)
        elif n.typ == b"stco":
            b = bytearray(n.payload)
            count = u32(b, 4)
            for k in range(count):
                off = 8 + 4 * k
                if off + 4 > len(b):
                    break
                b[off:off + 4] = p32((u32(b, off) + delta) & 0xFFFFFFFF)
            n.payload = bytes(b)
        elif n.typ == b"co64":
            b = bytearray(n.payload)
            count = u32(b, 4)
            for k in range(count):
                off = 8 + 8 * k
                if off + 8 > len(b):
                    break
                b[off:off + 8] = p64(u64(b, off) + delta)
            n.payload = bytes(b)


def main(src, dst):
    data = open(src, "rb").read()
    top = parse(data, 0, len(data))
    ftyp = next((b for b in top if b.typ == b"ftyp"), None)
    moov = next((b for b in top if b.typ == b"moov"), None)
    mdat = next((b for b in top if b.typ == b"mdat"), None)
    if not ftyp or not moov or not mdat:
        raise ValueError("input needs ftyp, moov and mdat")

    # locate the mdat payload offset in the source
    i = 0
    old_mdat_payload = None
    while i + 8 <= len(data):
        size = u32(data, i)
        typ = data[i + 4:i + 8]
        hdr = 8
        if size == 1:
            size = u64(data, i + 8)
            hdr = 16
        elif size == 0:
            size = len(data) - i
        if size < hdr or i + size > len(data):
            break
        if typ == b"mdat":
            old_mdat_payload = i + hdr
            break
        i += size
    if old_mdat_payload is None:
        raise ValueError("mdat not found")

    # metadata edits
    for n in moov.children:
        if n.typ == b"mvhd":
            n.payload = mvhd_infinite(n.payload)
        elif n.typ == b"trak":
            for c in n.children:
                if c.typ == b"tkhd":
                    c.payload = zero_times(c.payload)
                elif c.typ == b"mdia" and c.children:
                    for m in c.children:
                        if m.typ == b"mdhd":
                            m.payload = zero_times(m.payload)

    # compressor name on the video track's sample description
    for trak in (n for n in moov.children if n.typ == b"trak"):
        mdia = next((c for c in trak.children if c.typ == b"mdia"), None)
        hdlr = next((c for c in mdia.children if c.typ == b"hdlr"), None) if mdia else None
        if hdlr and hdlr.payload[8:12] == b"vide":
            stsd = find(mdia.children, b"stsd")
            if stsd:
                stsd.payload = set_compressor(stsd.payload)
            break

    # replace moov/udta
    moov.children = [c for c in moov.children if c.typ != b"udta"]
    moov.children.append(parse_udta())

    ftyp_raw = ftyp.build()
    mdat_payload = mdat.payload

    # pass 1: size the new moov with old offsets
    new_mdat_payload = len(ftyp_raw) + len(moov.build()) + 8
    delta = new_mdat_payload - old_mdat_payload
    shift_offsets(moov.children, delta)

    new_mdat_payload = len(ftyp_raw) + len(moov.build()) + 8
    assert new_mdat_payload - old_mdat_payload == delta

    out = ftyp_raw + moov.build()
    size = 8 + len(mdat_payload)
    if size <= 0xFFFFFFFF:
        out += p32(size) + b"mdat" + mdat_payload
    else:
        out += p32(1) + b"mdat" + p64(16 + len(mdat_payload)) + mdat_payload
    open(dst, "wb").write(out)
    print("wrote", dst, len(out), "bytes; moov", len(moov.build()),
          "delta", delta)


def parse_udta():
    raw = bytes.fromhex(UDTA_HEX)
    return Box(b"udta", children=parse(raw, 8, len(raw)))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
