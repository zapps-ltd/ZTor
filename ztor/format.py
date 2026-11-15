"""Plain-text formatting helpers. Everything returns short strings safe for a screen reader."""

from datetime import datetime

UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def human_size(n):
    """1234567 -> '1.2 MB'."""
    if n is None:
        return ""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    neg = n < 0
    n = abs(n)
    i = 0
    while n >= 1024.0 and i < len(UNITS) - 1:
        n /= 1024.0
        i += 1
    if i == 0:
        text = "%d B" % int(n)
    else:
        text = "%.1f %s" % (n, UNITS[i])
        text = text.replace(".0 ", " ")
    return ("-" + text) if neg else text


def human_speed(n):
    text = human_size(n)
    return (text + "/s") if text else ""


def human_eta(seconds):
    """Seconds remaining -> '1d 2h', '3h 5m', '12m 30s'."""
    if seconds is None or seconds < 0:
        return ""
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return "%dd %dh" % (days, hours)
    if hours:
        return "%dh %dm" % (hours, minutes)
    if minutes:
        return "%dm %ds" % (minutes, seconds)
    return "%ds" % seconds


def human_duration(seconds):
    """Elapsed seconds -> '1d 2h 3m'."""
    if not seconds or seconds < 0:
        return ""
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    parts = []
    if days:
        parts.append("%dd" % days)
    if hours or days:
        parts.append("%dh" % hours)
    parts.append("%dm" % minutes)
    return " ".join(parts)


def timestamp(epoch):
    """Unix time -> local 'YYYY-MM-DD HH:MM'."""
    try:
        epoch = int(epoch)
    except (TypeError, ValueError):
        return ""
    if epoch <= 0:
        return ""
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


def percent(fraction):
    """0.5342 -> '53.4 percent'."""
    try:
        return "%.1f%%" % (float(fraction) * 100.0)
    except (TypeError, ValueError):
        return ""


def ratio(up, down):
    if not down:
        return "inf" if up else ""
    return "%.2f" % (float(up) / float(down))


def limit_text(bytes_per_second):
    """A rate in bytes per second. Zero or negative means unlimited."""
    if bytes_per_second is None:
        return "unlimited"
    try:
        value = int(bytes_per_second)
    except (TypeError, ValueError):
        return "unlimited"
    if value <= 0:
        return "unlimited"
    return human_speed(value)


def pieces_text(done, total):
    if not total:
        return ""
    return "%s of %s" % (done, total)
