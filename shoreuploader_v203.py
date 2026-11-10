"""Reproduce ShoreUploader v2.0.3 container patch on an MP4.

Usage: python -I patch203.py <input.mp4> <output.mp4>

Only container bytes change (tables/tags/hdlr/compressorname). The mdat
payload is copied verbatim, then an 8-byte stub sample is appended.
"""
import struct
import sys

NEST = {b"moov", b"trak", b"mdia", b"minf", b"stbl"}
# uidta/meta/ilst exactly as emitted by ShoreUploader v2.0.3 (tags only)
UDTA_HEX = (
    "000000bb75647461000000b36d657461000000000000002168646c720000000000"
    "0000006d6469726170706c00000000000000000000000086696c73740000002ca9"
    "746f6f0000002464617461000000010000000053686f726555706c6f616465722d"
    "76322e302e3300000029a9636d740000002164617461000000010000000073686f"
    "726575706c6f616465722e636f6d00000029637072740000002164617461000000"
    "010000000073686f726575706c6f616465722e636f6d"
)
STUB = b"\x00\x00\x00\x04\x00\x00\x00\x00"
COMPRESSOR = b"ShoreUploader-v2.0.3"
HANDLER = b"shoreuploader-coded"


def u32(b, o):
    return struct.unpack(">I", b[o:o + 4])[0]


def p32(v):
    return struct.pack(">I", v)


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
        out.append((typ, d[i + hdr:i + size], i, size, hdr))
        i += size
    return out


class Node:
    def __init__(self, t, data=None, ch=None, raw=None):
        self.t = t
        self.data = data
        self.ch = ch
        self.raw = raw


def build(d, s, e):
    nodes = []
    for typ, payload, i, size, hdr in parse(d, s, e):
        if typ in NEST:
            nodes.append(Node(typ, ch=build(d, i + hdr, i + size)))
        else:
            nodes.append(Node(typ, data=payload))
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


def zero_times(data, ver):
    b = bytearray(data)
    if b[0] == 1:
        b[4:20] = b"\0" * 16
    else:
        b[4:12] = b"\0" * 8
    return bytes(b)


def set_compressor(data):
    b = bytearray(data)
    count = u32(b, 4)
    p = 8
    for _ in range(count):
        size = u32(b, p)
        typ = bytes(b[p + 4:p + 8])
        if typ in (b"avc1", b"avc3", b"hvc1", b"hev1"):
            b[p + 50] = len(COMPRESSOR)
            b[p + 51:p + 82] = b"\0" * 31
            b[p + 51:p + 51 + len(COMPRESSOR)] = COMPRESSOR
        p += size
    return bytes(b)


def set_handler_name(data):
    return data[:24] + HANDLER + b"\0"


def inflate_stsz(data, n):
    b = bytearray(data)
    fixed = u32(b, 4)
    count = u32(b, 8)
    if fixed != 0:
        raise ValueError("fixed stsz unsupported")
    b[8:12] = p32(count + n)
    b += struct.pack(">I", 8) * n
    return bytes(b), count


def main(src, dst, fake=None):
    d = open(src, "rb").read()
    top = parse(d, 0, len(d))
    ftyp = next(b for b in top if b[0] == b"ftyp")
    mdat = next(b for b in top if b[0] == b"mdat")
    ftyp_raw = p32(ftyp[3]) + b"ftyp" + ftyp[1]
    mdat_payload = mdat[1]

    nodes = build(d, 0, len(d))
    moov = find(nodes, b"moov")
    mdat_node = find(nodes, b"mdat")
    nodes.remove(mdat_node)

    if fake is None:
        fake = max(round(len(d) / 6400), 1)  # 0.125% ghost bytes, as observed

    real_chunks = 0
    video_stco = None
    for n in moov.ch:
        if n.t == b"mvhd":
            n.data = zero_times(n.data, 0)
        if n.t != b"trak":
            continue
        tkhd = find(n.ch, b"tkhd")
        if tkhd:
            tkhd.data = zero_times(tkhd.data, 0)
        mdia = find(n.ch, b"mdia")
        if not mdia:
            continue
        mdhd = find(mdia.ch, b"mdhd")
        if mdhd:
            mdhd.data = zero_times(mdhd.data, 0)
        hdlr = find(mdia.ch, b"hdlr")
        handler = hdlr.data[8:12] if hdlr else b""
        if hdlr:
            hdlr.data = set_handler_name(hdlr.data)
        minf = find(mdia.ch, b"minf")
        stbl = find(minf.ch, b"stbl") if minf else None
        if not stbl:
            continue
        stsd = find(stbl.ch, b"stsd")
        if stsd and handler == b"vide":
            stsd.data = set_compressor(stsd.data)
        if handler != b"vide":
            continue
        stsz = find(stbl.ch, b"stsz")
        stco = find(stbl.ch, b"stco")
        if not stsz or not stco:
            raise ValueError("missing stsz/stco")
        real_chunks = u32(stco.data, 4)
        video_stco = stco
        stsz.data, real_samples = inflate_stsz(stsz.data, fake)
        # append fake chunks with placeholder offsets
        b = bytearray(stco.data)
        b[4:8] = p32(real_chunks + fake)
        b += p32(0) * fake
        stco.data = bytes(b)
        # one extra stsc entry for the fake chunks (1 sample per chunk)
        stsc = find(stbl.ch, b"stsc")
        if not stsc:
            raise ValueError("missing stsc")
        b = bytearray(stsc.data)
        b[4:8] = p32(u32(b, 4) + 1)
        b += struct.pack(">III", real_chunks + 1, 1, 1)
        stsc.data = bytes(b)
        print("real samples", real_samples, "chunks", real_chunks, "fake", fake)

    # drop old udta, append v2.0.3 udta
    moov.ch = [c for c in moov.ch if c.t != b"udta"]
    moov.ch.append(Node(b"udta", raw=bytes.fromhex(UDTA_HEX)))

    def assemble(delta, fake_off):
        for n in moov.ch:
            if n.t != b"trak":
                continue
            mdia = find(n.ch, b"mdia")
            minf = find(mdia.ch, b"minf") if mdia else None
            stbl = find(minf.ch, b"stbl") if minf else None
            if not stbl:
                continue
            for stco in [c for c in stbl.ch if c.t == b"stco"]:
                b = bytearray(stco.data)
                count = u32(b, 4)
                for k in range(count):
                    is_fake = stco is video_stco and k >= count - fake
                    v = fake_off if is_fake else u32(b, 8 + 4 * k) + delta
                    b[8 + 4 * k:12 + 4 * k] = p32(v)
                stco.data = bytes(b)
        return b"".join(ser(c) for c in moov.ch)

    # pass 1: measure new moov (offsets untouched, placeholders)
    moov_body = assemble(0, 0)
    new_start = 32 + 8 + 8 + len(moov_body) + 8
    delta = new_start - (mdat[2] + 8)
    final_size = new_start + len(mdat_payload) + len(STUB)
    moov_body = assemble(delta, final_size - len(STUB))
    out = ftyp_raw + p32(8) + b"free" + p32(8 + len(moov_body)) + b"moov" + moov_body
    out += p32(8 + len(mdat_payload) + len(STUB)) + b"mdat" + mdat_payload + STUB
    assert len(out) == final_size, (len(out), final_size)
    open(dst, "wb").write(out)
    print("wrote", dst, len(out), "bytes; mdat", mdat[2], "delta", delta)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else None)
