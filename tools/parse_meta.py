#!/usr/bin/env python3
"""Dump the video.meta JSON string and audio fields from a Douyin detail JSON.

Usage:
    python -I tools/parse_meta.py <detail.json>
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
    v = (o.get("aweme_detail") or {}).get("video") or {}
    m = v.get("meta")
    print("meta type:", type(m))
    if isinstance(m, str):
        print("meta len:", len(m))
        print(m[:4000])
    else:
        print(json.dumps(m, ensure_ascii=False, indent=2)[:4000])
    print("\naudio:", json.dumps(v.get("audio"), ensure_ascii=False)[:1000])
    print("\nbit_rate_audio:", json.dumps(v.get("bit_rate_audio"), ensure_ascii=False)[:1000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
