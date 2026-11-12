"""Reproduce ShoreUploader v2.0.3 container patch on an MP4.

Usage: python -I shoreuploader_v203.py <input.mp4> <output.mp4> [fake_count]

Only container bytes change (tables/tags/hdlr/compressorname). The mdat
payload is copied verbatim, then an 8-byte stub sample is appended.

The patcher also normalises the file to the site's reference layout, which
the earlier subset implementation left untouched: ftyp brands, mdhd language
"eng", an empty edit prepended to the video elst, and a last-frame split of
the video stts.  These steps were confirmed against a real v2.0.3 output
(riatsuae sample) and the site's ported engine.
"""
import struct
import sys

NEST = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"dinf"}
# Boxes the site's patcher removes from anywhere inside moov.
STRIP = {b"uuid", b"c2pa", b"c2mc", b"bdat", b"ccat", b"xml ", b"free", b"sdtp"}
HEVC = {b"hvc1", b"hev1", b"dvh1", b"dvhe"}
EMPTY_EDIT = 16
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


def u16(b, o):
    return struct.unpack(">H", b[o:o + 2])[0]


def u32(b, o):
    return struct.unpack(">I", b[o:o + 4])[0]


def p32(v):
    return struct.pack(">I", v)


def set_language(data):
    """mdhd language -> packed ISO-639-2 "eng" (0x15C7), as the site does."""
    b = bytearray(data)
    o = 32 if b and b[0] == 1 else 20
    if o + 2 <= len(b):
        b[o:o + 2] = b"\x15\xc7"
    return bytes(b)


def stsd_info(data):
    """Return (codec fourcc of the first entry, is_hdr)."""
    codec = data[12:16] if len(data) >= 16 else b""
    hdr = False
    r = data.find(b"colr")
    if r != -1 and data[r + 4:r + 8] == b"nclx" and r + 12 <= len(data):
        hdr = u16(data, r + 10) in (16, 18)
    if b"mdcv" in data or b"clli" in data:
        hdr = True
    return codec, hdr


def make_ftyp(codec, hdr):
    """The site rewrites ftyp to ffmpeg's default brands, hvc1 for HEVC/HDR."""
    brand = b"hvc1" if codec in HEVC or hdr else b"avc1"
    body = b"isom" + p32(512) + b"isom" + b"iso2" + brand + b"mp41"
    return p32(8 + len(body)) + b"ftyp" + body


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
    if len(data) < 24:
        return data
    return data[:24] + HANDLER + b"\0"


def stbl_of(trak):
    mdia = find(trak.ch, b"mdia")
    minf = find(mdia.ch, b"minf") if mdia else None
    return find(minf.ch, b"stbl") if minf else None


def add_empty_edit(trak):
    """Prepend an empty edit (duration 16, media_time -1) to the video elst.

    The site rebuilds the list as [empty, first original entry]; it does not
    preserve the remaining entries.
    """
    edts = find(trak.ch, b"edts")
    elst = find(edts.ch, b"elst") if edts else None
    if not elst or len(elst.data) < 8:
        return
    p = elst.data
    ver = p[0]
    width = 8 if ver == 1 else 4
    entry = 2 * width + 4
    count = u32(p, 4)
    if count == 0 or len(p) < 8 + count * entry:
        return
    first = p[8:8 + entry]
    duration = struct.pack(">Q", EMPTY_EDIT) if width == 8 else p32(EMPTY_EDIT)
    empty = duration + b"\xff" * width + b"\x00\x01\x00\x00"
    elst.data = bytes([ver]) + b"\0\0\0" + p32(2) + empty + first


def reset_audio_edit(trak):
    """Zero the audio elst media_time and keep only its first entry."""
    edts = find(trak.ch, b"edts")
    elst = find(edts.ch, b"elst") if edts else None
    if not elst or len(elst.data) < 8:
        return
    p = elst.data
    ver = p[0]
    width = 8 if ver == 1 else 4
    entry = 2 * width + 4
    count = u32(p, 4)
    if count == 0 or len(p) < 8 + count * entry:
        return
    first = bytearray(p[8:8 + entry])
    first[width:2 * width] = b"\0" * width
    elst.data = bytes([ver]) + b"\0\0\0" + p32(1) + bytes(first)


def split_last_frame(trak):
    """Single-entry video stts -> [(n-1, delta), (1, 1)]."""
    stbl = stbl_of(trak)
    stts = find(stbl.ch, b"stts") if stbl else None
    if not stts or len(stts.data) < 16:
        return
    p = stts.data
    if u32(p, 4) != 1:
        return
    count, delta = u32(p, 8), u32(p, 12)
    if count <= 1:
        return
    stts.data = (p[:4] + p32(2) + struct.pack(">II", count - 1, delta)
                 + struct.pack(">II", 1, 1))


def strip_recursive(node):
    """Drop STRIP boxes from the whole moov subtree, as the site does."""
    if node.ch is None:
        return
    node.ch = [c for c in node.ch if c.t not in STRIP]
    for c in node.ch:
        strip_recursive(c)


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
    mdat = next(b for b in top if b[0] == b"mdat")

    nodes = build(d, 0, len(d))
    moov = find(nodes, b"moov")
    mdat_node = find(nodes, b"mdat")
    nodes.remove(mdat_node)

    # ftyp is rewritten once the video codec and colour tags are known.
    video_codec, hdr = b"", False
    for n in moov.ch:
        if n.t != b"trak":
            continue
        mdia = find(n.ch, b"mdia")
        hdlr = find(mdia.ch, b"hdlr") if mdia else None
        if not hdlr or hdlr.data[8:12] != b"vide":
            continue
        stbl = stbl_of(n)
        stsd = find(stbl.ch, b"stsd") if stbl else None
        if stsd:
            video_codec, hdr = stsd_info(stsd.data)
        break
    ftyp_raw = make_ftyp(video_codec, hdr)
    mdat_payload = mdat[1]

    if fake is None:
        fake = max(round(len(d) / 6400), 1)  # 0.125% ghost bytes, as observed

    real_chunks = 0
    video_stco = None
    seen_video = seen_audio = False
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
            mdhd.data = set_language(zero_times(mdhd.data, 0))
        hdlr = find(mdia.ch, b"hdlr")
        handler = hdlr.data[8:12] if hdlr else b""
        stbl = stbl_of(n)
        first_video = handler == b"vide" and not seen_video
        first_audio = handler == b"soun" and not seen_audio
        if first_video or first_audio:
            seen_video |= first_video
            seen_audio |= first_audio
            if hdlr:
                hdlr.data = set_handler_name(hdlr.data)
            n.ch = [c for c in n.ch if c.t != b"udta"]
            if first_audio:
                reset_audio_edit(n)
        if not stbl:
            continue
        stsd = find(stbl.ch, b"stsd")
        if stsd and first_video:
            stsd.data = set_compressor(stsd.data)
            add_empty_edit(n)
            split_last_frame(n)
        if not first_video:
            continue
        stsz = find(stbl.ch, b"stsz")
        stco = find(stbl.ch, b"stco")
        if not stsz or not stco:
            raise ValueError("missing stsz/stco")
        real_chunks = u32(stco.data, 4)
        video_stco = stco
        if fake:
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

    if not seen_video:
        raise ValueError("no video track found")

    # Drop stray fingerprint boxes, the old moov udta/meta, then append the
    # fixed v2.0.3 udta.
    strip_recursive(moov)
    moov.ch = [c for c in moov.ch if c.t not in (b"meta", b"udta")]
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
    stub = STUB if fake else b""
    moov_body = assemble(0, 0)
    new_start = len(ftyp_raw) + 8 + 8 + len(moov_body) + 8
    delta = new_start - (mdat[2] + 8)
    final_size = new_start + len(mdat_payload) + len(stub)
    moov_body = assemble(delta, final_size - len(stub))
    out = ftyp_raw + p32(8) + b"free" + p32(8 + len(moov_body)) + b"moov" + moov_body
    out += p32(8 + len(mdat_payload) + len(stub)) + b"mdat" + mdat_payload + stub
    assert len(out) == final_size, (len(out), final_size)
    open(dst, "wb").write(out)
    print("wrote", dst, len(out), "bytes; mdat", mdat[2], "delta", delta)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else None)
