"""Local time from UTC seconds, without the RTC knowing about time zones.

Pure: runs the same under MicroPython and CPython (tests). The Presto's RTC is
set to UTC by ntptime; this turns that into local wall-clock time, either from
the forecast's own offset (Open-Meteo `utc_offset_seconds`, which includes
daylight saving) or from a standard offset plus a DST rule.
"""

DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def days_from_civil(y, m, d):
    """Days since 1970-01-01 for a proleptic Gregorian date (H. Hinnant's algorithm)."""
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def civil_from_days(z):
    z += 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + (3 if mp < 10 else -9)
    return y + (m <= 2), m, d


def weekday(y, m, d):
    """Monday = 0 ... Sunday = 6."""
    return (days_from_civil(y, m, d) + 3) % 7


def nth_sunday(y, m, n):
    """Day of the month of the n-th Sunday (n >= 1)."""
    first = weekday(y, m, 1)
    return 1 + (6 - first) % 7 + 7 * (n - 1)


def is_dst(utc, std_hours, rule):
    """Whether daylight saving applies at UTC seconds `utc` (rule: 'us', 'eu' or None)."""
    if rule == "us":  # 2nd Sunday of March 02:00 local standard -> 1st Sunday of November 02:00 local daylight
        y = civil_from_days((utc + int(std_hours * 3600)) // 86400)[0]
        start = days_from_civil(y, 3, nth_sunday(y, 3, 2)) * 86400 + 2 * 3600 - int(std_hours * 3600)
        end = days_from_civil(y, 11, nth_sunday(y, 11, 1)) * 86400 + 2 * 3600 - int((std_hours + 1) * 3600)
        return start <= utc < end
    if rule == "eu":  # last Sunday of March 01:00 UTC -> last Sunday of October 01:00 UTC
        y = civil_from_days(utc // 86400)[0]
        last_mar = nth_sunday(y, 3, 4) + (7 if nth_sunday(y, 3, 4) + 7 <= 31 else 0)
        last_oct = nth_sunday(y, 10, 4) + (7 if nth_sunday(y, 10, 4) + 7 <= 31 else 0)
        start = days_from_civil(y, 3, last_mar) * 86400 + 3600
        end = days_from_civil(y, 10, last_oct) * 86400 + 3600
        return start <= utc < end
    return False


def offset_seconds(utc, std_hours, rule, override=None):
    """The local offset: the forecast's `override` when known, else standard + DST."""
    if override is not None:
        return override
    return int(std_hours * 3600) + (3600 if is_dst(utc, std_hours, rule) else 0)


def local(utc, offset):
    """(year, month, day, hour, minute, second, weekday) for UTC seconds + offset."""
    t = utc + offset
    y, m, d = civil_from_days(t // 86400)
    s = t % 86400
    return y, m, d, s // 3600, (s // 60) % 60, s % 60, weekday(y, m, d)


def date_line(lt):
    """'TUE · 03 NOV 2026' (the middle dot is drawn as '-' by bitmap fonts, so use it plainly)."""
    return "%s  %02d %s %d" % (DAYS[lt[6]], lt[2], MONTHS[lt[1] - 1], lt[0])
