#!/usr/bin/env python3
"""Print MP4 box/sample-table facts used in the Douyin forensics notes.

Usage:
    python -I tools/probe_tables.py <file.mp4> [more.mp4 ...]

Reads only the paths passed on the command line. No third-party deps.
"""
import os
import struct
import sys

NEST = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"dinf"}


def u32(b, o):
    return struct.unpack(">I", b[o:o + 4])[0]


def u64(b, o):
    return struct.unpack(">Q", b[o:o + 8])[0]


def boxes(d, s, e):
    out = []
    i = s
    while i + 8 <= e:
        sz = u32(d, i)
        typ = d[i + 4:i + 8]
        hdr = 8
        if sz == 1:
            sz = u64(d, i + 8)
            hdr = 16
        elif sz == 0:
            sz = e - i
        if sz < hdr or i + sz > e:
            break
        out.append((typ, i, i + hdr, i + sz))
        i += sz
    return out


def child(d, s, e, typ):
    for t, a, p, b in boxes(d, s, e):
        if t == typ:
            return (a, p, b)
    return None


def dsc(e, i):
    """Read one MPEG-4 descriptor tag/length/payload offset."""
    tag = e[i]
    i += 1
    ln = 0
    for _ in range(4):
        c = e[i]
        i += 1
        ln = (ln << 7) | (c & 0x7f)
        if not c & 0x80:
            break
    return tag, ln, i


def esds_info(e):
    """Return (objectTypeIndication, avgBitrate, asc_hex) or None."""
    try:
        tag, ln, i = dsc(e, 4)
        i += 2
        fl = e[i]
        i += 1
        if fl & 0x80:
            i += 2
        if fl & 0x40:
            i += 1 + e[i]
        if fl & 0x20:
            i += 2
        tag, ln, i = dsc(e, i)
        oti = e[i]
        avg = u32(e, i + 9)
        i += 13
        tag, ln, i = dsc(e, i)
        return oti, avg, e[i:i + ln].hex()
    except (IndexError, AssertionError):
        return None


def track_info(d, a, p, b):
    info = {}
    t = child(d, p, b, b"tkhd")
    if t:
        q = t[1]
        ver = d[q]
        info["track_id"] = u32(d, q + (16 if ver else 12))
        info["tkhd_dur"] = u64(d, q + 28) if ver else u32(d, q + 20)
    ed = child(d, p, b, b"edts")
    if ed:
        el = child(d, ed[1], ed[2], b"elst")
        if el:
            q = el[1]
            ver = d[q]
            w = 8 if ver else 4
            n = u32(d, q + 4)
            es = []
            for i in range(n):
                o = q + 8 + i * (2 * w + 4)
                dur = u64(d, o) if ver else u32(d, o)
                mt = struct.unpack(">q" if ver else ">i", d[o + w:o + 2 * w])[0]
                es.append((dur, mt))
            info["elst"] = es
    mdia = child(d, p, b, b"mdia")
    if not mdia:
        return info
    ma, mp, mb = mdia
    h = child(d, mp, mb, b"hdlr")
    info["handler"] = d[h[1] + 8:h[1] + 12].decode("latin1") if h else "?"
    mh = child(d, mp, mb, b"mdhd")
    if mh:
        q = mh[1]
        ver = d[q]
        info["ts"] = u32(d, q + (20 if ver else 12))
        info["mdhd_dur"] = u64(d, q + 24) if ver else u32(d, q + 16)
    minf = child(d, mp, mb, b"minf")
    if not minf:
        return info
    stbl = child(d, minf[1], minf[2], b"stbl")
    if not stbl:
        return info
    sa, sp, sb = stbl
    st = child(d, sp, sb, b"stts")
    if st:
        q = st[1]
        n = u32(d, q + 4)
        ent = [(u32(d, q + 8 + 8 * i), u32(d, q + 12 + 8 * i)) for i in range(n)]
        info["stts"] = ent
        info["n_stts"] = sum(c for c, _ in ent)
        info["d_stts"] = sum(c * d for c, d in ent)
    sz = child(d, sp, sb, b"stsz")
    if sz:
        q = sz[1]
        fixed = u32(d, q + 4)
        cnt = u32(d, q + 8)
        info["stsz_fixed"] = fixed
        info["n_stsz"] = cnt
        if fixed == 0:
            info["sizes"] = [u32(d, q + 12 + 4 * i) for i in range(cnt) if q + 16 + 4 * i <= sz[2]]
    sc = child(d, sp, sb, b"stsc")
    if sc:
        q = sc[1]
        n = u32(d, q + 4)
        info["stsc"] = [(u32(d, q + 8 + 12 * i), u32(d, q + 12 + 12 * i), u32(d, q + 16 + 12 * i)) for i in range(n)]
    co = child(d, sp, sb, b"stco")
    if co:
        q = co[1]
        n = u32(d, q + 4)
        info["co64"] = False
        info["offsets"] = [u32(d, q + 8 + 4 * i) for i in range(n)]
    else:
        co = child(d, sp, sb, b"co64")
        if co:
            q = co[1]
            n = u32(d, q + 4)
            info["co64"] = True
            info["offsets"] = [u64(d, q + 8 + 8 * i) for i in range(n)]
    st = child(d, sp, sb, b"stss")
    if st:
        q = st[1]
        n = u32(d, q + 4)
        info["stss"] = [u32(d, q + 8 + 4 * i) for i in range(n)]
    ct = child(d, sp, sb, b"ctts")
    if ct:
        q = ct[1]
        n = u32(d, q + 4)
        vals = [struct.unpack(">i", d[q + 12 + 8 * i:q + 16 + 8 * i])[0] for i in range(n)]
        info["ctts"] = (n, min(vals) if vals else None, max(vals) if vals else None)
    sd = child(d, sp, sb, b"stsd")
    if sd:
        q = sd[1]
        info["codec"] = d[q + 12:q + 16].decode("latin1")
        if info.get("handler") == "soun":
            info["channels"] = struct.unpack(">H", d[q + 32:q + 34])[0]
            info["sample_rate"] = u32(d, q + 40) >> 16
        ent = q + 8
        end = min(ent + u32(d, ent), sd[2])
        inner = ent + (86 if info.get("handler") == "vide" else 36)
        for typ, a, p, b in boxes(d, inner, end):
            if typ == b"avcC":
                info["avcC"] = (d[p + 1], d[p + 3])  # profile, level
            elif typ == b"hvcC":
                info["hvcC"] = (d[p + 1] & 0x1f, d[p + 12])  # profile_idc, level_idc
            elif typ == b"esds":
                info["esds"] = esds_info(d[p:b])
    return info


def report(path):
    d = open(path, "rb").read()
    print("=" * 30, os.path.basename(path), len(d))
    top = boxes(d, 0, len(d))
    print("top", [(t.decode("latin1"), b - a) for t, a, p, b in top])
    md = child(d, 0, len(d), b"mdat")
    mdat_payload = (md[1], md[2]) if md else (None, None)
    moov = child(d, 0, len(d), b"moov")
    if not moov:
        return
    for typ, a, p, b in boxes(d, moov[1], moov[2]):
        if typ == b"mvhd":
            ver = d[p]
            info = (u32(d, p + 20) if ver else u32(d, p + 12), u64(d, p + 24) if ver else u32(d, p + 16))
            print("mvhd ver", ver, "ts", info[0], "dur", info[1])
    for typ, a, p, b in boxes(d, moov[1], moov[2]):
        if typ != b"trak":
            continue
        ti = track_info(d, a, p, b)
        h = ti.get("handler")
        print("\n--", h, "id", ti.get("track_id"))
        print("  mdhd ts", ti.get("ts"), "dur", ti.get("mdhd_dur"), "tkhd", ti.get("tkhd_dur"), "elst", ti.get("elst"))
        print("  stts n", ti.get("n_stts"), "dur", ti.get("d_stts"), "entries", ti.get("stts")[:6])
        print("  stsz n", ti.get("n_stsz"), "fixed", ti.get("stsz_fixed"), "codec", ti.get("codec"),
              "ch", ti.get("channels"), "rate", ti.get("sample_rate"))
        if ti.get("stss"):
            ks = ti["stss"]
            gaps = sorted(set(ks[i + 1] - ks[i] for i in range(len(ks) - 1)))
            print("  stss n", len(ks), "first", ks[:4], "gaps", gaps[:8])
        if ti.get("ctts"):
            print("  ctts runs", ti["ctts"][0], "offset min/max", ti["ctts"][1], ti["ctts"][2])
        if ti.get("avcC"):
            print("  avcC profile/level", ti["avcC"])
        if ti.get("hvcC"):
            print("  hvcC profile_idc/level_idc", ti["hvcC"])
        if ti.get("esds"):
            oti, avg, asc = ti["esds"]
            print("  esds oti 0x%02x avg %d asc %s" % (oti, avg, asc))
        if ti.get("offsets") and ti.get("sizes"):
            offs = ti["offsets"]
            sizes = ti["sizes"]
            stsc = ti["stsc"]
            n = ti["n_stsz"]
            mapping = []
            for ci in range(len(offs)):
                e = None
                for fc, spc, sdi in stsc:
                    if fc <= ci + 1:
                        e = spc
                    else:
                        break
                mapping.append(e or 0)
            print("  stsc entries", len(stsc), "last", stsc[-3:], "chunks", len(offs),
                  "derived samples", sum(mapping))
            si = 0
            ends = []
            for ci, spc in enumerate(mapping):
                if si >= n:
                    break
                chunk_sizes = sizes[si:si + spc]
                si += spc
                if not chunk_sizes:
                    continue
                ends.append(offs[ci] + sum(chunk_sizes))
            print("  sum sizes", sum(sizes), "first n_stts sizes sum", sum(sizes[:ti.get("n_stts", 0)]))
            print("  max end", max(ends) if ends else None, "mdat", mdat_payload, "file", len(d))
            print("  last offsets", offs[-3:], "unique last20", len(set(offs[-20:])))
            print("  sample sizes tail", sizes[-20:])
            if h == "vide":
                extra = sizes[ti.get("n_stts", 0):]
                print("  samples beyond stts:", len(extra), "sum", sum(extra),
                      "unique sizes", sorted(set(extra))[:10])
            else:
                print("  last chunk offsets in tail?", [o >= len(d) - 200 for o in offs[-5:]])


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    for p in sys.argv[1:]:
        report(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
