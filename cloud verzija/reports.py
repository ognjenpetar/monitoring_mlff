"""Text formatting for Telegram alerts, commands, and daily reports."""

from typing import Dict, List

from scraper import Device, UpsPowerStatus


def format_duration(seconds: float) -> str:
    """Format a number of seconds as a short human-readable duration.

    Shows all non-zero units (d/h/m) in descending order, falling back to
    seconds alone when d/h/m are all zero.
    """
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if not parts:
        parts.append(f"{secs}s")

    return " ".join(parts)


def format_live_status(devices: List[Device]) -> str:
    """Format the current UP/DOWN status of all devices for a Telegram command."""
    up_count = sum(1 for d in devices if d.is_up)
    down = [d for d in devices if not d.is_up]

    lines = [
        "=== MLFF Monitoring - Trenutni status ===",
        f"UP: {up_count}   DOWN: {len(down)}",
    ]
    if down:
        lines.append("")
        lines.append(f"Trenutno DOWN ({len(down)} uredjaj/a):")
        for d in down:
            lines.append(f"  {d.hostname}  {d.ip}  {d.duration}")

    return "\n".join(lines)


def format_day_report(title: str, day_stats_by_host: Dict[str, dict]) -> str:
    """Format a summary report for a single 24-hour period from per-host downtime stats.

    `day_stats_by_host` maps hostname -> {"downtime_seconds": float, "outage_count": int}
    covering exactly one calendar day per host. Uptime percentages are computed
    against a fixed 86400-second denominator, so this function must NOT be
    reused as-is for multi-day (e.g. weekly) stats produced by
    `stats.day_stats` with a longer period — doing so would silently produce
    wrong uptime percentages.
    """
    if not day_stats_by_host:
        return f"=== {title} ===\nNema podataka za ovaj period."

    total = len(day_stats_by_host)
    seconds_per_day = 86400
    total_downtime = sum(v["downtime_seconds"] for v in day_stats_by_host.values())
    network_uptime_pct = 100.0 * (1 - total_downtime / (total * seconds_per_day))

    lines = [
        f"=== {title} ===",
        f"Ukupno: {total} uredjaja aktivnih",
        f"Mrezni uptime: {network_uptime_pct:.1f}%",
    ]

    worst = sorted(
        (item for item in day_stats_by_host.items() if item[1]["downtime_seconds"] > 0),
        key=lambda kv: kv[1]["downtime_seconds"],
        reverse=True,
    )
    lines.append("")
    if worst:
        lines.append("Uredjaji sa najvise downtime-a:")
        for hostname, stat in worst:
            uptime_pct = 100.0 * (1 - stat["downtime_seconds"] / seconds_per_day)
            lines.append(
                f"  {hostname}  {format_duration(stat['downtime_seconds'])} DOWN  "
                f"({stat['outage_count']} prekida)  {uptime_pct:.1f}% uptime"
            )
    else:
        lines.append("Nema zabelezenih prekida.")

    return "\n".join(lines)


def format_threshold_alert(hostname: str, ip: str, down_duration_seconds: float, threshold_minutes: int) -> str:
    """Format an alert for a device that has been down longer than the configured threshold."""
    return (
        f"MLFF ALARM - {hostname} nedostupan duze od {threshold_minutes} min\n"
        f"IP: {ip}\n"
        f"Trenutno trajanje: {format_duration(down_duration_seconds)}"
    )


def format_ups_alert(hostname: str, ip: str, down_duration_seconds: float) -> str:
    """Format an alert for a likely power/UPS outage at a site."""
    return (
        f"MLFF UPS ALARM - {hostname} moguc gubitak struje na lokaciji\n"
        f"IP: {ip}\n"
        f"Trenutno trajanje: {format_duration(down_duration_seconds)}"
    )


def format_ups_power_alert(status: UpsPowerStatus, duration_seconds: float) -> str:
    """Format an alarm for a UPS that has lost mains (AC) power."""
    return (
        f"MLFF ALARM - UPS uredjaj nije na mreznom napajanju (AC OK)\n\n"
        f"{status.hostname}\n"
        f"IP: {status.ip}\n"
        f"Lokacija: Portal {status.portal_id}\n"
        f"Status: {status.status_text}\n"
        f"Baterija: {status.battery_pct}%\n"
        f"Trenutno trajanje: {format_duration(duration_seconds)}"
    )


def format_ups_power_recovered(status: UpsPowerStatus, duration_seconds: float) -> str:
    """Format a recovery message for a UPS that has returned to mains (AC) power."""
    return (
        f"MLFF - UPS uredjaj vracen na mrezno napajanje\n\n"
        f"{status.hostname}\n"
        f"IP: {status.ip}\n"
        f"Lokacija: Portal {status.portal_id}\n"
        f"Trajanje ispada: {format_duration(duration_seconds)}"
    )
