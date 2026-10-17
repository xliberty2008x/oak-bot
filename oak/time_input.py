"""Small explicit reminder grammar; local wall times are checked against DST."""

from datetime import datetime, timedelta, timezone
import math
import re


TIME_HELP = 'Формат: /remind 10m текст; 2h; 18:30; завтра 09:00; 2026-10-18T09:00+03:00'


def local_choices(wall, zone):
    choices = {}
    for fold in (0, 1):
        aware = wall.replace(tzinfo=zone, fold=fold)
        epoch = aware.timestamp()
        if datetime.fromtimestamp(epoch, zone).replace(tzinfo=None) == wall:
            choices[epoch] = aware
    return choices


def local_time(wall, zone):
    choices = local_choices(wall, zone)
    if len(choices) != 1:
        raise ValueError('Цей місцевий час неоднозначний або не існує. Вкажи ISO-час із UTC-зсувом, наприклад 2026-10-25T03:30+02:00.')
    return next(iter(choices.values()))


def parse_when(value, zone, *, now=None):
    now = datetime.now(timezone.utc) if now is None else now
    if now.tzinfo is None:
        raise ValueError('The reference time must include a timezone.')
    value = value.strip()
    match = re.fullmatch(r'(\d+(?:\.\d+)?)([smhd]?)', value, re.I)
    if match:
        seconds = float(match[1]) * {'': 1, 's': 1, 'm': 60, 'h': 3600, 'd': 86400}[match[2].lower()]
        if not math.isfinite(seconds):
            raise ValueError(TIME_HELP)
        try:
            return datetime.fromtimestamp(now.timestamp() + seconds, zone)
        except (ValueError, OverflowError, OSError):
            raise ValueError('Час нагадування поза підтримуваним діапазоном.') from None
    tomorrow = value.casefold().startswith('завтра ')
    clock = value.split(' ', 1)[1] if tomorrow else value
    if re.fullmatch(r'\d{1,2}:\d{2}', clock):
        hour, minute = map(int, clock.split(':'))
        if hour > 23 or minute > 59:
            raise ValueError(TIME_HELP)
        today = now.astimezone(zone).date()
        date = today + timedelta(days=1) if tomorrow else today
        wall = datetime.combine(date, datetime.min.time()).replace(hour=hour, minute=minute)
        if not tomorrow:
            choices = local_choices(wall, zone)
            # A fold is past only when both instants have elapsed. A gap has
            # no instant: compare its wall clock after the transition instead.
            elapsed = max(choices) <= now.timestamp() if choices else wall <= now.astimezone(zone).replace(tzinfo=None)
            if elapsed:
                wall += timedelta(days=1)
        return local_time(wall, zone)
    try:
        moment = datetime.fromisoformat(value[:-1] + '+00:00' if value.endswith('Z') else value)
        if moment.tzinfo is None:
            moment = local_time(moment, zone)
        if moment.timestamp() <= now.timestamp():
            raise ValueError('Вкажи майбутній час нагадування.')
        return moment.astimezone(zone)
    except (OverflowError, OSError):
        raise ValueError('Час нагадування поза підтримуваним діапазоном.') from None
    except ValueError as exc:
        if 'місцевий час' in str(exc) or 'майбутній час' in str(exc):
            raise
        raise ValueError(TIME_HELP) from None


def reminder_input(argument, zone, *, now=None):
    parts = argument.strip().split(maxsplit=2)
    if len(parts) < 2:
        raise ValueError(TIME_HELP)
    if parts[0].casefold() == 'завтра' or re.fullmatch(r'\d{4}-\d{2}-\d{2}', parts[0]):
        if len(parts) < 3:
            raise ValueError(TIME_HELP)
        value, prompt = ' '.join(parts[:2]), parts[2]
    else:
        value, prompt = parts[0], ' '.join(parts[1:])
    if not prompt.strip() or len(prompt) > 16000:
        raise ValueError(TIME_HELP)
    return parse_when(value, zone, now=now), prompt.strip()
