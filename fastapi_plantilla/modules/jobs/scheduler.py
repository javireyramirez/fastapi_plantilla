from datetime import UTC, datetime, timedelta


def calculate_next_scheduled_time(
    time_str: str | None = "03:00",
    default_hour: int = 3,
    default_minute: int = 0,
) -> datetime:
    """Calculate next upcoming datetime for daily HH:MM schedule (UTC)."""
    now = datetime.now(UTC)
    hour = default_hour
    minute = default_minute

    if time_str and isinstance(time_str, str):
        parts = time_str.strip().split(":")
        if len(parts) >= 2:
            try:
                hour = int(parts[0])
                minute = int(parts[1])
            except ValueError:
                pass
        elif len(parts) == 1:
            try:
                hour = int(parts[0])
                minute = 0
            except ValueError:
                pass

    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target
