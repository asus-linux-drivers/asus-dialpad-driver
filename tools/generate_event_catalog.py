#!/usr/bin/env python3
"""Generate the portable picker catalog from a Linux UAPI event-code header.

Usage from the repository root:
    python tools/generate_event_catalog.py /usr/include/linux/input-event-codes.h

Only symbolic KEY_*, BTN_* and REL_* definitions are copied, including aliases.
Count/range markers are excluded. No C preprocessing, kernel bindings, libevdev,
network access, or target-device access is required. The source header's SHA-256
and SPDX identifier are embedded so a checked-in catalog has auditable provenance.
When updating from a specific kernel release, pass --source-url with that exact
include/uapi/linux/input-event-codes.h URL. Linux's installed libevdev remains the
runtime authority; a catalog symbol is not a promise about installed capabilities.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


EXCLUDED_NAMES = ("KEY_MAX", "KEY_CNT", "KEY_MIN_INTERESTING", "REL_MAX", "REL_CNT")
UPSTREAM_URL = "https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/include/uapi/linux/input-event-codes.h"


def catalog_from_header(data: bytes, source_url: str) -> dict:
    text = data.decode("utf-8")
    names = sorted(set(re.findall(r"^\s*#\s*define\s+((?:KEY|BTN|REL)_[A-Z0-9_]+)\b", text, re.MULTILINE))
                   - set(EXCLUDED_NAMES))
    if not names or not any(name.startswith("KEY_") for name in names) or not any(name.startswith("REL_") for name in names):
        raise ValueError("source does not contain Linux key and relative-event definitions")
    spdx = re.search(r"SPDX-License-Identifier:\s*([^\r\n*]+)", text)
    return {
        "schema_version": 1,
        "provenance": {
            "source": "Linux UAPI include/uapi/linux/input-event-codes.h",
            "source_url": source_url,
            "source_sha256": hashlib.sha256(data).hexdigest(),
            "source_spdx": spdx.group(1).strip() if spdx else "not specified in source",
            "generator": "tools/generate_event_catalog.py",
            "excluded_names": list(EXCLUDED_NAMES),
        },
        "events": names,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("header", type=Path, help="Linux UAPI input-event-codes.h")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent.parent / "dialpad_events.json")
    parser.add_argument("--source-url", default=UPSTREAM_URL)
    args = parser.parse_args(argv)
    catalog = catalog_from_header(args.header.read_bytes(), args.source_url)
    args.output.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(catalog['events'])} event names to {args.output}")
    print(f"Source SHA-256: {catalog['provenance']['source_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
