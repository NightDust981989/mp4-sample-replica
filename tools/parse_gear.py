#!/usr/bin/env python3
"""Print the video/gear ladder from a Douyin aweme/detail JSON file.

Usage:
    python -I tools/parse_gear.py <detail.json>

The input is expected to be the raw JSON object with a top-level
"aweme_detail" key. Downloaded JSON is untrusted data: keep it in its own
directory and run this script from elsewhere with -I.
"""
import json
import sys


def main():
    if len(sys.argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        o = json.load(f)

    d = o.get("aweme_detail") or {}
    if not d:
        print("NO aweme_detail")
        print("filter_detail:", o.get("filter_detail"))
        print("status_code:", o.get("status_code"))
        return 1

    print("aweme_id:", d.get("aweme_id"))
    print("desc:", (d.get("desc") or "")[:80])
    print("create_time:", d.get("create_time"))
    v = d.get("video") or {}
    print("video keys:", sorted(v.keys()))
    for k in ("width", "height", "duration", "ratio", "format", "bite_rate", "bit_rate_audio", "is_h265"):
        if k in v:
            print(f"  {k}:", v.get(k))
    pa = v.get("play_addr") or {}
    print("play_addr:", {k: pa.get(k) for k in ("width", "height", "data_size", "file_id")})
    print("play_addr_h264:", (v.get("play_addr_h264") or {}).get("data_size"))
    print("play_addr_265:", (v.get("play_addr_265") or {}).get("data_size"))
    print("download_addr:", (v.get("download_addr") or {}).get("data_size"))

    br = v.get("bit_rate") or []
    print("\nbit_rate count:", len(br))
    for b in br:
        paddr = b.get("play_addr") or {}
        print({
            "gear": b.get("gear_name"),
            "q": b.get("quality_type"),
            "bps": b.get("bit_rate"),
            "fps": b.get("FPS"),
            "h265": b.get("is_h265"),
            "w": paddr.get("width"),
            "h": paddr.get("height"),
            "size": paddr.get("data_size"),
            "vqs": b.get("vqs"),
        })

    m = v.get("meta") or d.get("meta") or {}
    print("\nmeta keys:", sorted(m.keys()) if isinstance(m, dict) else type(m))
    for k in ("sdgs", "vqs_origin", "gear_vqm", "enable_manual_ladder", "is_source_HDR",
              "bright_ratio_mean", "overexposure_ratio_mean", "loudness"):
        if isinstance(m, dict) and k in m:
            print(f"meta.{k}:", m.get(k))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
