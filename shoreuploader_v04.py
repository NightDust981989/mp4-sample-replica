"""Reproduce the ShoreUploader v0.4 (2026-11) container patch on an MP4.

Usage:
    python -I shoreuploader_v04.py <input.mp4> <output.mp4> [tag]
                                    [--no-preprocess] [--ffmpeg PATH]

This mirrors the in-browser patcher served by shoreuploader.com/api/patcher
(site footer v0.4). Unlike v2.x it leaves the video sample table alone and
moves the "ghost data" into the audio track:

  * video: mdhd/tkhd durations are recomputed from stts, elst reset to one
    entry starting at media_time 0, pasp inserted before btrt when missing
  * audio: 18 dummy samples (23 + 17x6 bytes) are appended to stsz, stts is
    stretched to cover them, stsc is rewritten, and stsd is replaced with a
    fixed two-entry 48 kHz AAC template
  * the audio track is cloned as track_ID 3 (one-entry stsd template)
  * mvhd duration/NEXT_TRACK_ID are recomputed, udta is replaced with
    "Lavf62.3.100" / "Patched By ShoreUploader" tags
  * output is ftyp + moov + 8-byte free + mdat, followed by a 125-byte tail
    that the dummy audio samples point into

The site pre-processes inputs before calling this patcher: average fps
>= 119.995 is re-encoded to 119.97 fps, and otherwise audio that is not
48 kHz AAC is converted. Both steps are reproduced here with the same
predicates, order and ffmpeg arguments; --no-preprocess skips them.
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile

CONTAINERS = {
    b"moov", b"trak", b"edts", b"mdia", b"minf", b"dinf", b"stbl",
    b"udta", b"meta", b"ilst",
    b"\xa9ART", b"\xa9nam", b"\xa9too", b"\xa9day", b"cprt", b"\xa9enc",
    b"\xa9wrt", b"\xa9aut", b"\xa9cmt", b"----",
}

# 23-byte "Lavc62.11.100" tag, then 17 copies of a 6-byte marker: 125 bytes.
TAIL_A = bytes.fromhex("de02004c61766336322e31312e31303000422008c11838")
TAIL_B = bytes.fromhex("211004608c1c")
FAKE_SIZES = [23] + [6] * 17
TAIL = TAIL_A + b"".join(TAIL_B for _ in range(17))

# Fixed sample descriptions from the patcher.
STSD_AUDIO_2 = bytes.fromhex(
    "000000ec7374736400000000000000020000006b6d7034610000000000000001"
    "00000000000000000002001000000000bb800000000000336573647300000000"
    "0380808022000200048080801440150000000001f3f60001ea8d058080800211"
    "900680808001020000001462747274000000000001f3f60001ea8d000000716d"
    "703461000000000000000100000000000000000002001000000000bb80000000"
    "00003965736473000000000380808028000200048080801a40150000000001f3"
    "f60001ea8d0580808008118004c0400020000680808001020000001462747274"
    "000000000001f3f60001ea8d"
)
STSD_AUDIO_1 = bytes.fromhex(
    "0000007b7374736400000000000000010000006b6d703461000000000000000100"
    "000000000000000002001000000000bb80000000000033657364730000000003"
    "80808022000200048080801440150000000001f3f60001ea8d05808080021190"
    "0680808001020000001462747274000000000001f3f60001ea8d"
)
PASP = bytes.fromhex("00000010706173700000000100000001")

assert len(STSD_AUDIO_2) == 0xEC, len(STSD_AUDIO_2)
assert len(STSD_AUDIO_1) == 0x7B, len(STSD_AUDIO_1)
assert len(TAIL) == 125, len(TAIL)


def u32(b, o):
    return struct.unpack(">I", b[o:o + 4])[0]


def u64(b, o):
    return struct.unpack(">Q", b[o:o + 8])[0]


def p32(v):
    return struct.pack(">I", v)


def p64(v):
    return struct.pack(">Q", v)


def p16(v):
    return struct.pack(">H", v)


def w32(b, o, v):
    return b[:o] + p32(v) + b[o + 4:]


def w64(b, o, v):
    return b[:o] + p64(v) + b[o + 8:]


class Box:
    def __init__(self, typ, payload=None, children=None, prefix=b""):
        self.typ = typ
        self.payload = payload
        self.children = children
        self.prefix = prefix

    def build(self):
        if self.children is not None:
            body = self.prefix + b"".join(c.build() for c in self.children)
        else:
            body = self.payload
        size = 8 + len(body)
        if size <= 0xFFFFFFFF:
            return p32(size) + self.typ + body
        return p32(1) + self.typ + p64(16 + len(body)) + body

    def clone(self):
        return Box(self.typ, None if self.payload is None else self.payload,
                   None if self.children is None else [c.clone() for c in self.children],
                   self.prefix)


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
        children = None
        prefix = b""
        if typ in CONTAINERS:
            off = i + hdr
            if typ == b"meta" and len(payload) >= 4:
                prefix = payload[:4]
                off += 4
            try:
                children = parse(data, off, i + size)
            except Exception:
                children = None
        out.append(Box(typ, None if children is not None else payload,
                       children, prefix))
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


def find_all(nodes, typ):
    for n in nodes:
        if n.typ == typ:
            yield n
        if n.children:
            yield from find_all(n.children, typ)


def parse_stco(payload):
    count = u32(payload, 4)
    return [u32(payload, 8 + 4 * k) for k in range(count)]


def build_stco(offsets):
    return b"\0" * 4 + p32(len(offsets)) + b"".join(p32(o) for o in offsets)


def parse_stsz(payload):
    fixed = u32(payload, 4)
    count = u32(payload, 8)
    if fixed:
        return [fixed] * count
    return [u32(payload, 12 + 4 * k) for k in range(count)]


def build_stsz(sizes):
    return b"\0" * 8 + p32(len(sizes)) + b"".join(p32(s) for s in sizes)


def build_stsc(entries):
    body = b"\0" * 4 + p32(len(entries))
    for first, count, desc in entries:
        body += p32(first) + p32(count) + p32(desc)
    return body


def stts_duration(payload):
    """Total media duration = sum(count * delta)."""
    n = u32(payload, 4)
    total = 0
    for k in range(n):
        off = 8 + 8 * k
        if off + 8 > len(payload):
            break
        total += u32(payload, off) * u32(payload, off + 4)
    return total or None


def rebuild_chunk_offsets(stsc_payload, chunk_offsets, sizes):
    """Mirror of the patcher's B0(): make one chunk per sample if the old
    stsc does not already describe that many chunks."""
    if len(chunk_offsets) == len(sizes) or not stsc_payload or len(stsc_payload) < 8:
        return chunk_offsets
    n = u32(stsc_payload, 4)
    entries = []
    for k in range(n):
        off = 8 + 12 * k
        if off + 12 > len(stsc_payload):
            break
        entries.append((u32(stsc_payload, off), u32(stsc_payload, off + 4),
                        u32(stsc_payload, off + 8)))
    if not entries:
        return chunk_offsets
    out, r, g = [], 0, len(chunk_offsets)
    for k, (first, per_chunk, _desc) in enumerate(entries):
        nxt = entries[k + 1][0] if k + 1 < len(entries) else g + 1
        for chunk in range(first, min(nxt, g + 1)):
            m = chunk_offsets[chunk - 1]
            for _ in range(per_chunk):
                if r >= len(sizes):
                    break
                out.append(m)
                m += sizes[r]
                r += 1
    return out if len(out) == len(sizes) else chunk_offsets


def make_udta(tag):
    def item(name, text):
        return Box(name, children=[
            Box(b"data", payload=b"\0\0\0\1\0\0\0\0" + text.encode("utf-8"))])

    ilst = Box(b"ilst", children=[
        item(b"\xa9too", "Lavf62.3.100"),
        item(b"\xa9ART", tag),
        item(b"\xa9cmt", tag),
    ])
    hdlr = Box(b"hdlr", payload=b"\0" * 8 + b"mdirappl" + b"\0" * 9)
    meta = Box(b"meta", children=[hdlr, ilst], prefix=b"\0" * 4)
    return Box(b"udta", children=[meta])


def top_level(data):
    out, i = [], 0
    while i + 8 <= len(data) and len(out) < 256:
        size = u32(data, i)
        typ = bytes(data[i + 4:i + 8])
        hdr = 8
        if size == 1:
            if i + 16 > len(data):
                break
            size = u64(data, i + 8)
            hdr = 16
        elif size == 0:
            size = len(data) - i
        if size < hdr or i + size > len(data):
            break
        out.append({"typ": typ, "offset": i, "size": size, "hdr": hdr})
        i += size
    return out


def load_moov(data):
    """Parse only the moov box, for the pre-processing predicates."""
    info = next((b for b in top_level(data) if b["typ"] == b"moov"), None)
    if info is None:
        raise ValueError("input needs a moov box")
    raw = data[info["offset"]:info["offset"] + info["size"]]
    return parse(raw, 0, len(raw))[0]


def first_trak(moov, handler):
    for t in moov.children:
        if t.typ != b"trak":
            continue
        h = find(t.children, b"hdlr")
        if h and h.payload[8:12] == handler:
            return t
    return None


def needs_fps_cap(moov):
    """Site rule: average fps = stsz samples * mdhd timescale / stts
    duration, capped when it reaches 119.995."""
    video = first_trak(moov, b"vide")
    if video is None:
        return False
    mdhd = find(video.children, b"mdhd")
    stts = find(video.children, b"stts")
    stsz = find(video.children, b"stsz")
    if mdhd is None or stts is None or stsz is None:
        return False
    timescale = u32(mdhd.payload, 12 if mdhd.payload[0] == 0 else 20)
    total = stts_duration(stts.payload)
    count = u32(stsz.payload, 8)
    return bool(total) and count * timescale / total >= 119.995


def _skip_descriptor_size(buf, pos):
    for _ in range(4):
        v = buf[pos]
        pos += 1
        if not v & 0x80:
            break
    return pos


def audio_needs_fix(moov):
    """Site rule: the first sound track must be mp4a + esds whose
    DecoderConfigDescriptor has objectTypeIndication 0x40, and its mdhd
    timescale must be 48000. Anything else is converted first."""
    audio = first_trak(moov, b"soun")
    if audio is None:
        return False
    stsd = find(audio.children, b"stsd")
    mdhd = find(audio.children, b"mdhd")
    if stsd is None or mdhd is None:
        return True
    c = stsd.payload
    if len(c) < 16 or c[12:16] != b"mp4a":
        return True
    r = c.find(b"esds")
    if r == -1:
        return True
    try:
        r += 8
        if c[r] != 3:
            return True
        r = _skip_descriptor_size(c, r + 1)
        flags = c[r + 2]
        r += 3
        if flags & 0x80:
            r += 2
        if flags & 0x40:
            r += 1 + c[r]
        if flags & 0x20:
            r += 2
        if c[r] != 4:
            return True
        r = _skip_descriptor_size(c, r + 1)
        if c[r] != 0x40:
            return True
    except IndexError:
        return True
    timescale = u32(mdhd.payload, 12 if mdhd.payload[0] == 0 else 20)
    return timescale != 48000


# ffmpeg arguments used by the site, in the same order.
AUDIO_FIX_ARGS = [
    "-map", "0:v:0", "-map", "0:a:0?",
    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
    "-movie_timescale", "1000",
]
FPS_CAP_ARGS = [
    "-r", "11997/100",
    "-map", "0:v:0", "-map", "0:a:0?",
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
    "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
    "-movie_timescale", "1000", "-video_track_timescale", "119970",
]


def find_ffmpeg(explicit):
    if explicit:
        if os.path.isfile(explicit) or shutil.which(explicit):
            return explicit
        raise SystemExit("ffmpeg not found: " + explicit)
    path = shutil.which("ffmpeg")
    if path is None:
        raise SystemExit("ffmpeg not found; install it, pass --ffmpeg PATH, "
                         "or use --no-preprocess")
    return path


def ffmpeg_preprocess(ffmpeg, src, kind):
    """Run one of the site's ffmpeg steps and return the output bytes."""
    tmp = tempfile.mkdtemp(prefix="su_v04_")
    try:
        out = os.path.join(tmp, "capped.mp4" if kind == "fps" else "fixed.mp4")
        args = FPS_CAP_ARGS if kind == "fps" else AUDIO_FIX_ARGS
        cmd = ([ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
                "-y", "-i", src] + args + [out])
        proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
        if proc.returncode != 0:
            lines = proc.stderr.decode("utf-8", "replace").strip().splitlines()
            raise SystemExit("ffmpeg failed:\n" + "\n".join(lines[-8:]))
        with open(out, "rb") as f:
            return f.read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def patch(data, tag="Patched By ShoreUploader"):
    boxes = top_level(data)
    ftyp_info = next((b for b in boxes if b["typ"] == b"ftyp"), None)
    mdat_info = next((b for b in boxes if b["typ"] == b"mdat"), None)
    moov_info = next((b for b in boxes if b["typ"] == b"moov"), None)
    if not ftyp_info or not mdat_info or not moov_info:
        raise ValueError("input needs ftyp, moov and mdat")

    ftyp = parse(data, ftyp_info["offset"],
                 ftyp_info["offset"] + ftyp_info["size"])[0]
    moov_bytes = data[moov_info["offset"]:
                      moov_info["offset"] + moov_info["size"]]
    moov = parse(moov_bytes, 0, len(moov_bytes))[0]

    mdat_payload = data[mdat_info["offset"] + mdat_info["hdr"]:
                        mdat_info["offset"] + mdat_info["size"]]
    old_mdat_payload = mdat_info["offset"] + mdat_info["hdr"]

    traks = [c for c in moov.children if c.typ == b"trak"]
    video = audio = None
    for t in traks:
        h = find(t.children, b"hdlr")
        if not h:
            continue
        handler = h.payload[8:12]
        if handler == b"vide" and video is None:
            video = t
        elif handler == b"soun" and audio is None:
            audio = t
    if video is None:
        raise ValueError("no video track")

    # --- video ---------------------------------------------------------
    stsd = find(video.children, b"stsd")
    if stsd and b"pasp" not in stsd.payload:
        idx = stsd.payload.find(b"btrt")
        if idx != -1:
            w = idx - 4
            entry_size = u32(stsd.payload, 8)
            stsd.payload = (stsd.payload[:8] + p32(entry_size + 16)
                            + stsd.payload[12:w] + PASP + stsd.payload[w:])

    mdhd = find(video.children, b"mdhd")
    h0 = 60000
    if mdhd:
        h0 = u32(mdhd.payload, 12 if mdhd.payload[0] == 0 else 20)

    stsz = find(video.children, b"stsz")
    if stsz is None:
        raise ValueError("video track has no stsz")
    sample_count = u32(stsz.payload, 8)

    stts = find(video.children, b"stts")
    K = stts_duration(stts.payload) if stts else None
    if K is None:
        delta = 1001
        if stts and len(stts.payload) >= 16:
            delta = u32(stts.payload, 12)
        K = sample_count * delta

    mvhd = find(moov.children, b"mvhd")
    P = 1000
    if mvhd:
        ver = mvhd.payload[0]
        P = u32(mvhd.payload, 12 if ver == 0 else 20)
        if P > 100000:
            P = 1000
            mvhd.payload = w32(mvhd.payload, 12 if ver == 0 else 20, P)

    C = min((K * P + h0 - 1) // h0, 0xFFFFFFFF)
    if mdhd:
        mdhd.payload = (w32(mdhd.payload, 16, min(K, 0xFFFFFFFF))
                        if mdhd.payload[0] == 0 else
                        w64(mdhd.payload, 24, K))
    tkhd = find(video.children, b"tkhd")
    if tkhd:
        tkhd.payload = (w32(tkhd.payload, 20, C)
                        if tkhd.payload[0] == 0 else
                        w64(tkhd.payload, 28, C))
    elst = find(video.children, b"elst")
    if elst:
        elst.payload = (b"\0" * 4 + p32(1) + p32(C) + p32(0)
                        + p16(1) + p16(0))

    # --- audio ---------------------------------------------------------
    Q = 0
    audio_sizes = []
    audio_stsc_payload = None
    chunk_offsets = []
    clone = None
    if audio:
        astsz = find(audio.children, b"stsz")
        astts = find(audio.children, b"stts")
        astsc = find(audio.children, b"stsc")
        astsd = find(audio.children, b"stsd")
        amdhd = find(audio.children, b"mdhd")
        if astsz is None or astsd is None:
            raise ValueError("audio track is missing stsz/stsd")

        audio_sizes = parse_stsz(astsz.payload)
        sizes = audio_sizes + FAKE_SIZES
        astsz.payload = build_stsz(sizes)

        if astts:
            if len(astts.payload) >= 12:
                astts.payload = w32(astts.payload, 8, len(sizes))

        if astsc:
            audio_stsc_payload = astsc.payload
            M = max(1, len(sizes) - 7)
            astsc.payload = build_stsc([(1, 1, 1), (M, 1, 2)])

        astsd.payload = STSD_AUDIO_2[8:]

        R = len(sizes) * 1024
        F = 48000
        if amdhd:
            ver = amdhd.payload[0]
            ts = u32(amdhd.payload, 12 if ver == 0 else 20)
            if ts > 0:
                F = ts
            amdhd.payload = (w32(amdhd.payload, 16, min(R, 0xFFFFFFFF))
                             if ver == 0 else w64(amdhd.payload, 24, R))

        Q = min((R * P + F - 1) // F, 0xFFFFFFFF)
        atkhd = find(audio.children, b"tkhd")
        if atkhd:
            atkhd.payload = (w32(atkhd.payload, 20, Q)
                             if atkhd.payload[0] == 0 else
                             w64(atkhd.payload, 28, Q))
        aelst = find(audio.children, b"elst")
        if aelst:
            aelst.payload = (b"\0" * 4 + p32(1) + p32(Q) + p32(0)
                             + p16(1) + p16(0))

        audio.children = [c for c in audio.children if c.typ != b"udta"]

        clone = audio.clone()
        ctkhd = find(clone.children, b"tkhd")
        ctkhd.payload = (w32(ctkhd.payload, 12, 3)
                         if ctkhd.payload[0] == 0 else
                         w32(ctkhd.payload, 16, 3))
        find(clone.children, b"stsd").payload = STSD_AUDIO_1[8:]
        cstsc = find(clone.children, b"stsc")
        if cstsc:
            cstsc.payload = build_stsc([(1, 1, 1)])

        audio_stco = find(audio.children, b"stco")
        if audio_stco:
            chunk_offsets = rebuild_chunk_offsets(
                audio_stsc_payload, parse_stco(audio_stco.payload), audio_sizes)

        moov.children = ([c for c in moov.children if c.typ != b"trak"]
                         + [video, audio, clone])
    else:
        moov.children = [c for c in moov.children if c.typ != b"trak"] + [video]

    # --- movie header / udta -------------------------------------------
    if mvhd:
        duration = max(Q, C) if audio else C
        payload = (w32(mvhd.payload, 16, duration) if mvhd.payload[0] == 0
                   else w64(mvhd.payload, 24, duration))
        if audio:
            payload = w32(payload, len(payload) - 4, 4)
        mvhd.payload = payload

    moov.children = [c for c in moov.children if c.typ != b"udta"]
    moov.children.append(make_udta(tag))

    video_stco = find(video.children, b"stco")
    audio_stco = find(audio.children, b"stco") if audio else None
    clone_stco = find(clone.children, b"stco") if clone else None
    if video_stco is None:
        raise ValueError("video track has no stco")
    video_offsets = parse_stco(video_stco.payload)

    ftyp_bytes = ftyp.build()
    tail = TAIL if audio else b""
    free = p32(8) + b"free"
    mdat_payload_size = len(mdat_payload)

    for _ in range(5):
        moov_size = len(moov.build())
        new_payload_start = len(ftyp_bytes) + moov_size + len(free) + 8
        delta = new_payload_start - old_mdat_payload
        video_stco.payload = build_stco([o + delta for o in video_offsets])
        if audio:
            fake = []
            cursor = new_payload_start + mdat_payload_size
            for size in FAKE_SIZES:
                fake.append(cursor)
                cursor += size
            offsets = [o + delta for o in chunk_offsets] + fake
            audio_stco.payload = build_stco(offsets)
            clone_stco.payload = build_stco(offsets)

    out = (ftyp_bytes + moov.build() + free
           + p32(8 + mdat_payload_size + len(tail)) + b"mdat"
           + mdat_payload + tail)
    return out


def main(argv):
    ap = argparse.ArgumentParser(
        description="Apply the ShoreUploader v0.4 patch, with the site's "
                    "ffmpeg pre-processing.")
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("tag", nargs="?", default="Patched By ShoreUploader")
    ap.add_argument("--no-preprocess", action="store_true",
                    help="patch directly, without the ffmpeg steps")
    ap.add_argument("--ffmpeg", metavar="PATH",
                    help="ffmpeg binary (default: found on PATH)")
    args = ap.parse_args(argv)

    with open(args.src, "rb") as f:
        data = f.read()

    if not args.no_preprocess:
        moov = load_moov(data)
        if needs_fps_cap(moov):
            print("pre-processing: average fps >= 119.995, "
                  "re-encoding to 119.97 fps")
            data = ffmpeg_preprocess(find_ffmpeg(args.ffmpeg), args.src, "fps")
        elif audio_needs_fix(moov):
            print("pre-processing: converting audio to 48 kHz AAC")
            data = ffmpeg_preprocess(find_ffmpeg(args.ffmpeg), args.src,
                                     "audio")

    out = patch(data, args.tag)
    with open(args.dst, "wb") as f:
        f.write(out)
    print("wrote", args.dst, len(out), "bytes")


if __name__ == "__main__":
    main(sys.argv[1:])
