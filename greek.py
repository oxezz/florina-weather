# -*- coding: utf-8 -*-
"""Greek weather vocabulary and formatting helpers.

Everything in this module is a pure function: no network, no clock, no config.
That makes it the easiest part of the project to reason about and to test.

Conventions
-----------
* Wind bearings follow the meteorological convention: the direction the wind
  blows *from*. ``compass(0) == "Β"`` means a northerly wind.
* Arrows returned by :func:`wind_arrow` point the way the wind is blowing
  *towards*, which is what people intuitively read on a weather page.
* Beaufort thresholds are the official km/h bands.
"""

# --------------------------------------------------------------------------
# WMO weather interpretation codes
# https://open-meteo.com/en/docs (WMO Code table 4677, abridged)
# --------------------------------------------------------------------------

# code -> (Greek description, icon key)
_WMO = {
    0:  ("Καθαρός", "clear"),
    1:  ("Σχεδόν αίθριος", "mostly_clear"),
    2:  ("Μερικώς νεφελώδης", "partly"),
    3:  ("Νεφοσκεπής", "overcast"),
    45: ("Ομίχλη", "fog"),
    48: ("Παγωμένη ομίχλη", "fog"),
    51: ("Ασθενές ψιλόβροχο", "drizzle"),
    53: ("Ψιλόβροχο", "drizzle"),
    55: ("Πυκνό ψιλόβροχο", "drizzle"),
    56: ("Ασθενές παγωμένο ψιλόβροχο", "freezing"),
    57: ("Πυκνό παγωμένο ψιλόβροχο", "freezing"),
    61: ("Ασθενής βροχή", "rain"),
    63: ("Βροχή", "rain"),
    65: ("Ισχυρή βροχή", "rain"),
    66: ("Ασθενής παγωμένη βροχή", "freezing"),
    67: ("Ισχυρή παγωμένη βροχή", "freezing"),
    71: ("Ασθενής χιονόπτωση", "snow"),
    73: ("Χιονόπτωση", "snow"),
    75: ("Ισχυρή χιονόπτωση", "snow"),
    77: ("Χιονόκοκκοι", "snow"),
    80: ("Ασθενείς τοπικές βροχές", "showers"),
    81: ("Τοπικές βροχές", "showers"),
    82: ("Ισχυρές τοπικές βροχές", "showers"),
    85: ("Ασθενείς χιονοπτώσεις", "snow"),
    86: ("Ισχυρές χιονοπτώσεις", "snow"),
    95: ("Καταιγίδα", "thunder"),
    96: ("Καταιγίδα με χαλάζι", "thunder"),
    99: ("Ισχυρή καταιγίδα με χαλάζι", "thunder"),
}

UNKNOWN_TEXT = "Μη διαθέσιμο"

# icon key -> (day emoji, night emoji). Written as escapes so the source file
# stays readable in any editor and never depends on its own encoding.
_EMOJI = {
    "clear":        ("\u2600\ufe0f", "\U0001f319"),
    "mostly_clear": ("\U0001f324\ufe0f", "\U0001f319"),
    "partly":       ("\u26c5", "\u2601\ufe0f"),
    "overcast":     ("\u2601\ufe0f", "\u2601\ufe0f"),
    "fog":          ("\U0001f32b\ufe0f", "\U0001f32b\ufe0f"),
    "drizzle":      ("\U0001f326\ufe0f", "\U0001f326\ufe0f"),
    "rain":         ("\U0001f327\ufe0f", "\U0001f327\ufe0f"),
    "freezing":     ("\U0001f328\ufe0f", "\U0001f328\ufe0f"),
    "snow":         ("\U0001f328\ufe0f", "\U0001f328\ufe0f"),
    "showers":      ("\U0001f326\ufe0f", "\U0001f326\ufe0f"),
    "thunder":      ("\u26c8\ufe0f", "\u26c8\ufe0f"),
}
_UNKNOWN_EMOJI = "\u2601\ufe0f"


def describe(code):
    """Greek description for a WMO weather code."""
    try:
        return _WMO.get(int(code), (UNKNOWN_TEXT, None))[0]
    except (TypeError, ValueError):
        return UNKNOWN_TEXT


def icon_key(code):
    """Stable icon identifier for a WMO weather code (never ``None``)."""
    try:
        return _WMO.get(int(code), (None, "overcast"))[1]
    except (TypeError, ValueError):
        return "overcast"


def emoji(code, is_day=True):
    """Emoji for a WMO weather code, with a day/night variant where it helps."""
    day, night = _EMOJI.get(icon_key(code), (_UNKNOWN_EMOJI, _UNKNOWN_EMOJI))
    return day if is_day else night


# --------------------------------------------------------------------------
# Wind
# --------------------------------------------------------------------------

# 8-point compass, meteorological convention (direction the wind comes from).
_COMPASS = ("Β", "ΒΑ", "Α", "ΝΑ", "Ν", "ΝΔ", "Δ", "ΒΔ")

# Arrows point where the wind blows *towards* (bearing + 180°).
_ARROWS = ("\u2193", "\u2199", "\u2190", "\u2196",
           "\u2191", "\u2197", "\u2192", "\u2198")

# Upper bound (inclusive, km/h) of Beaufort 1..11. Beaufort 0 is below 1 km/h
# and Beaufort 12 is anything above the last bound.
_BEAUFORT_UPPER = (5, 11, 19, 28, 38, 49, 61, 74, 88, 102, 117)

_BEAUFORT_TEXT = (
    "Άπνοια", "Σχεδόν άπνοια", "Πολύ ασθενής", "Ασθενής",
    "Σχεδόν μέτριος", "Μέτριος", "Ισχυρός", "Σχεδόν θυελλώδης",
    "Θυελλώδης", "Πολύ θυελλώδης", "Θύελλα", "Σφοδρή θύελλα", "Τυφώνας",
)


def _bearing_index(deg, points=8):
    """Nearest compass index for a bearing in degrees."""
    try:
        d = float(deg) % 360.0
    except (TypeError, ValueError):
        return 0
    return int(round(d / (360.0 / points))) % points


def compass(deg):
    """Greek 8-point compass abbreviation, e.g. ``"ΒΔ"``."""
    return _COMPASS[_bearing_index(deg)]


def wind_arrow(deg):
    """Arrow showing where the wind is blowing towards."""
    return _ARROWS[_bearing_index(deg)]


def beaufort(kmh):
    """Beaufort number (0-12) for a wind speed in km/h."""
    try:
        v = float(kmh)
    except (TypeError, ValueError):
        return 0
    if v != v:  # NaN
        return 0
    speed = int(v) if v > 0 else 0
    if speed < 1:
        return 0
    for index, limit in enumerate(_BEAUFORT_UPPER):
        if speed <= limit:
            return index + 1
    return 12


def beaufort_text(number):
    """Greek description of a Beaufort number."""
    try:
        n = int(number)
    except (TypeError, ValueError):
        return _BEAUFORT_TEXT[0]
    return _BEAUFORT_TEXT[max(0, min(12, n))]


# --------------------------------------------------------------------------
# Index levels
# --------------------------------------------------------------------------

_UV_BANDS = (
    (3, "Χαμηλός", "#4ade80"),
    (6, "Μέτριος", "#facc15"),
    (8, "Υψηλός", "#fb923c"),
    (11, "Πολύ υψηλός", "#f87171"),
)
_UV_TOP = ("Ακραίος", "#c084fc")

# European AQI bands (0-20 good ... >100 extremely poor).
_AQI_BANDS = (
    (20, "Καλή", "#4ade80"),
    (40, "Αποδεκτή", "#a3e635"),
    (60, "Μέτρια", "#facc15"),
    (80, "Κακή", "#fb923c"),
    (100, "Πολύ κακή", "#f87171"),
)
_AQI_TOP = ("Εξαιρετικά κακή", "#c084fc")

_UV_QUALITY = (
    "Καθαρή ατμόσφαιρα", "Καλή ορατότητα", "Μέτρια ορατότητα",
    "Μειωμένη ορατότητα", "Πολύ μειωμένη ορατότητα",
)


def uv_level(value):
    """(Greek label, hex colour) for a UV index value."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ("—", "#94a3b8")
    for limit, label, colour in _UV_BANDS:
        if v < limit:
            return (label, colour)
    return _UV_TOP


def aqi_level(value):
    """(Greek label, hex colour) for a European AQI value."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ("—", "#94a3b8")
    for limit, label, colour in _AQI_BANDS:
        if v < limit:
            return (label, colour)
    return _AQI_TOP


def visibility_text(metres):
    """Greek visibility quality word from a distance in metres."""
    try:
        v = float(metres)
    except (TypeError, ValueError):
        return "—"
    if v >= 40000:
        return _UV_QUALITY[0]
    if v >= 20000:
        return _UV_QUALITY[1]
    if v >= 4000:
        return _UV_QUALITY[2]
    if v >= 1000:
        return _UV_QUALITY[3]
    return _UV_QUALITY[4]


# --------------------------------------------------------------------------
# Dates and times
# --------------------------------------------------------------------------

WEEKDAYS = ("Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη",
            "Παρασκευή", "Σάββατο", "Κυριακή")
WEEKDAYS_SHORT = ("Δευ", "Τρι", "Τετ", "Πεμ", "Παρ", "Σαβ", "Κυρ")
MONTHS_GENITIVE = ("Ιανουαρίου", "Φεβρουαρίου", "Μαρτίου", "Απριλίου",
                   "Μαΐου", "Ιουνίου", "Ιουλίου", "Αυγούστου",
                   "Σεπτεμβρίου", "Οκτωβρίου", "Νοεμβρίου", "Δεκεμβρίου")


def weekday_name(date):
    """Full Greek weekday, e.g. ``"Τρίτη"``."""
    return WEEKDAYS[date.weekday()]


def weekday_short(date):
    """Abbreviated Greek weekday, e.g. ``"Τρι"``."""
    return WEEKDAYS_SHORT[date.weekday()]


def month_name(date):
    """Greek month name in the genitive, e.g. ``"Οκτωβρίου"``."""
    return MONTHS_GENITIVE[date.month - 1]


def long_date(date):
    """e.g. ``"Τρίτη 6 Οκτωβρίου"``."""
    return "%s %d %s" % (weekday_name(date), date.day, month_name(date))


def day_label(date, today):
    """``"Σήμερα"`` / ``"Αύριο"`` / weekday name."""
    delta = (date - today).days
    if delta == 0:
        return "Σήμερα"
    if delta == 1:
        return "Αύριο"
    return weekday_name(date)


def format_hhmm(iso_timestamp):
    """``"2026-10-06T07:35"`` -> ``"07:35"``.

    Also tolerates a full ISO timestamp and a bare ``"07:35"``.
    """
    if not iso_timestamp or not isinstance(iso_timestamp, str):
        return "—"
    text = iso_timestamp.strip()
    if "T" in text:
        text = text.split("T", 1)[1]
    text = text[:5]
    return text if len(text) == 5 and text[2] == ":" else "—"


def format_duration(seconds):
    """Seconds -> ``"11ω 33λ"``; falls back to ``"—"``."""
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return "—"
    if total < 0:
        return "—"
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return "%dω %02dλ" % (hours, minutes)
    if hours:
        return "%dω" % hours
    return "%dλ" % minutes


# --------------------------------------------------------------------------
# Meteoalarm warnings
# --------------------------------------------------------------------------

# Meteoalarm "awareness_type" parameter, e.g. "1; Wind" -> the part after ";".
_HAZARDS = {
    "wind": ("Άνεμος", "\U0001f4a8"),
    "rain": ("Βροχή", "\U0001f327\ufe0f"),
    "rain-flood": ("Βροχή και πλημμύρες", "\U0001f327\ufe0f"),
    "thunderstorm": ("Καταιγίδες", "\u26c8\ufe0f"),
    "snow-ice": ("Χιόνι και πάγος", "\u2744\ufe0f"),
    "blizzard": ("Χιονοθύελλα", "\U0001f328\ufe0f"),
    "avalanche": ("Χιονοστιβάδα", "\U0001f3d4\ufe0f"),
    "fog": ("Ομίχλη", "\U0001f32b\ufe0f"),
    "high-temperature": ("Καύσωνας", "\U0001f321\ufe0f"),
    "low-temperature": ("Ψύχος", "\u2744\ufe0f"),
    "coastal-event": ("Παράκτια φαινόμενα", "\U0001f30a"),
    "forest-fire": ("Πυρκαγιά", "\U0001f525"),
    "flood": ("Πλημμύρα", "\U0001f30a"),
    "rainfall": ("Βροχόπτωση", "\U0001f327\ufe0f"),
    "extreme-temperature": ("Ακραίες θερμοκρασίες", "\U0001f321\ufe0f"),
}

# Meteoalarm awareness level: "2; Yellow; Moderate".
_LEVELS = (
    ("green", "Πράσινη", "#4ade80"),
    ("yellow", "Κίτρινη", "#facc15"),
    ("orange", "Πορτοκαλί", "#fb923c"),
    ("red", "Κόκκινη", "#ef4444"),
)

_SEVERITY_LEVEL = {
    "minor": "green",
    "moderate": "yellow",
    "severe": "orange",
    "extreme": "red",
}


def hazard(code):
    """(Greek hazard name, emoji) from a Meteoalarm awareness_type code."""
    key = (code or "").strip().lower()
    if key in _HAZARDS:
        return _HAZARDS[key]
    return (key.replace("-", " ").capitalize() or "Προειδοποίηση", "\u26a0\ufe0f")


def warning_rank(level_key):
    """Numeric severity rank (0 = green … 3 = red) for sorting alerts."""
    for index, (key, _label, _colour) in enumerate(_LEVELS):
        if key == level_key:
            return index
    return 1


def warning_level(awareness_level=None, severity=None):
    """(level key, Greek label, hex colour) for a Meteoalarm warning.

    ``awareness_level`` looks like ``"2; Yellow; Moderate"`` and is the most
    reliable signal; ``severity`` is the CAP fallback.
    """
    if awareness_level:
        parts = [p.strip() for p in str(awareness_level).split(";")]
        for part in reversed(parts):
            low = part.lower()
            for index, (key, label, colour) in enumerate(_LEVELS):
                if key == low:
                    return (key, label, colour)
            if low.isdigit() and 1 <= int(low) <= 4:
                key, label, colour = _LEVELS[int(low) - 1]
                return (key, label, colour)
    key = _SEVERITY_LEVEL.get((severity or "").strip().lower(), "yellow")
    for candidate in _LEVELS:
        if candidate[0] == key:
            return candidate
    return _LEVELS[1]


# --------------------------------------------------------------------------
# Number formatting
# --------------------------------------------------------------------------


def number(value, decimals=0, dash="—"):
    """Format a number defensively; ``None``/NaN become ``dash``."""
    if value is None:
        return dash
    try:
        v = float(value)
    except (TypeError, ValueError):
        return dash
    if v != v or v in (float("inf"), float("-inf")):  # NaN / inf
        return dash
    if decimals <= 0:
        return "%d" % int(round(v))
    return ("%." + str(decimals) + "f") % v
