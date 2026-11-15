"""IP filter lists in eMule .dat or PeerGuardian .p2p format, plain or .gz."""

import gzip
import ipaddress
import re

RANGE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})\s*-\s*(\d{1,3}(?:\.\d{1,3}){3})")
LEVEL = re.compile(r"\s*,\s*(\d+)")


def _clean(text):
    """'001.009.096.105' to '1.9.96.105', or None when it is not an address."""
    try:
        numbers = [int(part) for part in text.split(".")]
    except ValueError:
        return None
    if len(numbers) != 4 or any(n > 255 for n in numbers):
        return None
    return ".".join(str(n) for n in numbers)


def parse_lines(lines):
    """Blocked (start, end) pairs. Lines with a .dat level above 127 are allowed
    ranges and are skipped, as eMule does."""
    ranges = []
    for line in lines:
        line = line.strip()
        if not line or line[0] in "#;/":
            continue
        match = RANGE.search(line)
        if not match:
            continue
        start, end = _clean(match.group(1)), _clean(match.group(2))
        if not start or not end:
            continue
        level = LEVEL.match(line[match.end():])
        if level and int(level.group(1)) > 127:
            continue
        if int(ipaddress.ip_address(start)) > int(ipaddress.ip_address(end)):
            start, end = end, start
        ranges.append((start, end))
    return ranges


def load(path):
    opener = gzip.open if path.lower().endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        return parse_lines(handle)
