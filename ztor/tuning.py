"""Session settings, speed schedule and seeding goals, built from preferences.

Everything here is plain Python with no wx and no libtorrent objects, so the
session start, the Preferences dialog and the self test all share one source.
"""

import re
from datetime import datetime

from . import APP_NAME, __version__

# Always from the app itself, never from an old saved setting.
USER_AGENT = "%s/%s" % (APP_NAME, __version__)

PROFILE_NAMES = ["Low resources", "Balanced", "Fast"]

PROFILES = [
    {   # low resources: few connections, gentle on old routers
        "connections_limit": 100,
        "connection_speed": 10,
        "unchoke_slots_limit": 4,
    },
    {   # balanced: the default
        "connections_limit": 300,
        "connection_speed": 30,
        "torrent_connect_boost": 30,
        "max_out_request_queue": 1000,
        "send_buffer_watermark": 1024 * 1024,
        "send_buffer_watermark_factor": 100,
    },
    {   # fast: more peers, deeper request queues, bigger send buffers
        "connections_limit": 600,
        "connection_speed": 60,
        "torrent_connect_boost": 50,
        "max_out_request_queue": 1500,
        "send_buffer_low_watermark": 64 * 1024,
        "send_buffer_watermark": 3 * 1024 * 1024,
        "send_buffer_watermark_factor": 150,
        "unchoke_slots_limit": 16,
    },
]

# libtorrent enum values, kept as numbers so this module needs no libtorrent.
PE_FORCED, PE_ENABLED, PE_DISABLED = 0, 1, 2
PE_RC4, PE_PLAINTEXT, PE_BOTH = 1, 2, 3
PREFER_TCP, PEER_PROPORTIONAL = 0, 1

DAY_NAMES = ["Every day", "Weekdays", "Weekends"]

# libtorrent only knows dht.libtorrent.org, and when that one does not answer
# the DHT stays empty and magnets wait on trackers alone. The same list
# qBittorrent uses, so a cold start finds nodes within seconds.
DHT_ROUTERS = ",".join([
    "dht.libtorrent.org:25401",
    "router.bittorrent.com:6881",
    "router.utorrent.com:6881",
    "dht.transmissionbt.com:6881",
    "dht.aelitis.com:6881",
])


def _int(value, default=0):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def profile_index(config):
    value = _int(config.get("speed_profile", 1), 1)
    return max(0, min(value, len(PROFILES) - 1))


def encryption_settings(mode):
    """The Preferences choice, in its order, mapped to libtorrent policies.

    0 prefer plaintext and allow encryption, 1 require encryption,
    2 plaintext only.
    """
    mode = _int(mode)
    if mode == 1:
        return {"out_enc_policy": PE_FORCED, "in_enc_policy": PE_FORCED,
                "allowed_enc_level": PE_RC4}
    if mode == 2:
        return {"out_enc_policy": PE_DISABLED, "in_enc_policy": PE_DISABLED,
                "allowed_enc_level": PE_PLAINTEXT}
    return {"out_enc_policy": PE_ENABLED, "in_enc_policy": PE_ENABLED,
            "allowed_enc_level": PE_BOTH}


def listen_string(bind, port):
    bind = (bind or "").strip()
    if not bind:
        return "0.0.0.0:%d,[::]:%d" % (port, port)
    if ":" in bind and not bind.startswith("["):
        bind = "[%s]" % bind
    return "%s:%d" % (bind, port)


def session_settings(config, port=None):
    """Every libtorrent setting ZTor controls, as one dictionary."""
    settings = dict(PROFILES[profile_index(config)])
    connections = _int(config.get("max_connections", 0))
    if connections > 0:
        settings["connections_limit"] = connections
    announce = bool(config.get("announce_all", True))
    settings["announce_to_all_trackers"] = announce
    settings["announce_to_all_tiers"] = announce
    settings["mixed_mode_algorithm"] = (PREFER_TCP if config.get("prefer_tcp")
                                        else PEER_PROPORTIONAL)
    settings.update({
        "user_agent": USER_AGENT,
        "enable_dht": bool(config.get("enable_dht", True)),
        "dht_bootstrap_nodes": DHT_ROUTERS,
        "enable_lsd": bool(config.get("enable_lsd", True)),
        "enable_upnp": bool(config.get("enable_upnp", True)),
        "enable_natpmp": bool(config.get("enable_natpmp", True)),
        "download_rate_limit": _int(config.get("download_rate_limit")),
        "upload_rate_limit": _int(config.get("upload_rate_limit")),
        "active_downloads": _int(config.get("active_downloads", 3), 3),
        "active_seeds": _int(config.get("active_seeds", 5), 5),
        "proxy_type": _int(config.get("proxy_type")),
        "proxy_hostname": config.get("proxy_host", "") or "",
        "proxy_port": _int(config.get("proxy_port")),
        "proxy_username": config.get("proxy_username", "") or "",
        "proxy_password": config.get("proxy_password", "") or "",
        "proxy_peer_connections": bool(config.get("proxy_peers", True)),
        "proxy_tracker_connections": bool(config.get("proxy_trackers", True)),
    })
    settings.update(encryption_settings(config.get("encryption_mode", 0)))
    if port:
        bind = config.get("bind_address", "") or ""
        settings["listen_interfaces"] = listen_string(bind, port)
        settings["outgoing_interfaces"] = bind.strip()
    return settings


# ------------------------------------------------------------ speed schedule
def parse_hhmm(text):
    """'08:30' to minutes after midnight, or None."""
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", text or "")
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 23 or minutes > 59:
        return None
    return hours * 60 + minutes


def schedule_active(config, now=None):
    if not config.get("schedule_enabled"):
        return False
    start = parse_hhmm(config.get("schedule_from", ""))
    end = parse_hhmm(config.get("schedule_to", ""))
    if start is None or end is None or start == end:
        return False
    now = now or datetime.now()
    days = _int(config.get("schedule_days", 0))
    weekday = now.weekday()
    if days == 1 and weekday >= 5:
        return False
    if days == 2 and weekday < 5:
        return False
    minute = now.hour * 60 + now.minute
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def effective_limits(config, now=None):
    """(download, upload, alternative) in bytes per second, zero unlimited."""
    alt = bool(config.get("alt_speed_on")) or schedule_active(config, now)
    if alt:
        return (_int(config.get("alt_dl_limit")),
                _int(config.get("alt_ul_limit")), True)
    return (_int(config.get("download_rate_limit")),
            _int(config.get("upload_rate_limit")), False)


# ------------------------------------------------------------ seeding goals
def seed_goal_reason(ratio_limit, minutes_limit, ratio, seconds):
    """A short reason when a seeding goal is reached, otherwise ''."""
    try:
        ratio_limit = float(ratio_limit or 0)
    except (TypeError, ValueError):
        ratio_limit = 0.0
    minutes_limit = _int(minutes_limit)
    if ratio_limit > 0 and ratio >= ratio_limit:
        return "ratio %.2f reached" % ratio
    if minutes_limit > 0 and seconds >= minutes_limit * 60:
        return "seeding time of %d minutes reached" % minutes_limit
    return ""
