from __future__ import annotations

import argparse
import json
from pathlib import Path

from check_main_report import DATA_MARKER_RE, read_report_payload


def compact_numbers(value):
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: compact_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [compact_numbers(item) for item in value]
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("html", type=Path)
    args = parser.parse_args()
    html = args.html.read_text(encoding="utf-8")
    marker = DATA_MARKER_RE.search(html)
    if marker is None:
        raise RuntimeError("Report DATA payload is missing")
    payload, end = json.JSONDecoder().raw_decode(html[marker.end():])
    compact_payload = compact_numbers(payload)
    if compact_payload != payload:
        raise RuntimeError("Compaction changed report data")
    packed = json.dumps(compact_payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    result = html[:marker.end()] + packed + html[marker.end() + end:]
    size = len(result.encode("utf-8"))
    if size >= 25 * 1024 * 1024:
        raise RuntimeError(f"Report exceeds Cloudflare Pages limit after compaction: {size} bytes")
    args.html.write_text(result, encoding="utf-8")
    if read_report_payload(args.html) != payload:
        raise RuntimeError("Compacted report verification failed")
    print(f"Compacted report verified: {size} bytes")


if __name__ == "__main__":
    main()
