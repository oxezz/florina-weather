# -*- coding: utf-8 -*-
"""Outbound frost alerts.

Dependency-free by design, like everything else here: ntfy is a plain POST to a
URL and Telegram is a POST to the bot API, so both go through ``urllib``.

Two rules govern this module:

* **A notification must never break the page.** Every failure is logged and
  forgotten; the caller gets a status object, not an exception.
* **The same alert is sent once.** The frost card stays lit for days once a
  cold snap is in the forecast, so without suppression the same warning would
  go out on every request.
"""

import json
import logging
import os
import threading
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("florina.notify")

USER_AGENT = "florina-weather (+frost-alert)"

# Levels worth waking someone for. "risk" is a maybe and stays quiet.
ALERTING_LEVELS = ("frost", "hard", "severe", "strong")

_state_lock = threading.Lock()
_last_sent = {}


def frost_message(frost, place):
    """``(title, body)`` for the frost card, or ``None`` if it is not alerting."""
    if not frost or frost.get("level") not in ALERTING_LEVELS:
        return None

    title = "%s · %s" % (place, frost.get("label") or "Παγετός")
    parts = []

    nights = frost.get("count") or 0
    if nights:
        parts.append("1 νύχτα με παγετό" if nights == 1
                     else "%d νύχτες με παγετό" % nights)
    if frost.get("first"):
        parts.append("πρώτη %s" % frost["first"].get("label"))

    ground, air = frost.get("ground_min"), frost.get("air_min")
    if ground is not None and air is not None:
        parts.append("έδαφος %+.1f°, αέρας %+.1f°" % (ground, air))
    elif frost.get("min") is not None:
        parts.append("ελάχιστη %+.1f°" % frost["min"])

    return title, ". ".join(parts) + "."


def _post(url, data, headers, timeout, opener):
    body = data if isinstance(data, bytes) else urllib.parse.urlencode(data).encode()
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    send = opener or (lambda req, t: urllib.request.urlopen(req, timeout=t))
    with send(request, timeout) as response:
        return response.status


def send_ntfy(message, topic_url, timeout, opener=None):
    """Publish to ntfy using its JSON form.

    Everything goes in the body, because header values have to be latin-1 and
    a Greek title sent as a header arrives mangled. The topic is the last path
    segment; anything before it is treated as an instance prefix.
    """
    title, body = message
    parsed = urllib.parse.urlsplit(topic_url)
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        raise ValueError("ntfy url has no topic: %s" % topic_url)
    topic = parts[-1]
    prefix = "/".join(parts[:-1])
    endpoint = "%s://%s/%s" % (parsed.scheme, parsed.netloc,
                               (prefix + "/") if prefix else "")

    payload = json.dumps({
        "topic": topic,
        "title": title,
        "message": body,
        "tags": ["snowflake"],
    }).encode("utf-8")
    headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    return _post(endpoint, payload, headers, timeout, opener)


def send_telegram(message, token, chat_id, timeout, opener=None):
    title, body = message
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": "<b>%s</b>\n%s" % (title, body),
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    headers = {"User-Agent": USER_AGENT,
               "Content-Type": "application/x-www-form-urlencoded"}
    return _post(url, payload, headers, timeout, opener)


def configured(config):
    """Which transport is usable, or ``None``."""
    kind = (getattr(config, "alert_webhook", "") or "").strip().lower()
    if kind == "ntfy" and getattr(config, "ntfy_url", ""):
        return "ntfy"
    if (kind == "telegram" and getattr(config, "telegram_token", "")
            and getattr(config, "telegram_chat", "")):
        return "telegram"
    return None


def _state_path(config):
    path = getattr(config, "webhook_state_path", "") or ""
    return path


def _load_state(config):
    """The record of what has been sent.

    Falls back to the in-memory dict when no path is configured — without that
    the suppression would silently do nothing, which is exactly the case on a
    host with a read-only filesystem.
    """
    path = _state_path(config)
    if not path or not os.path.exists(path):
        return dict(_last_sent)
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:  # noqa: BLE001 - a corrupt state file is not fatal
        return dict(_last_sent)


def _save_state(config, state):
    _last_sent.clear()
    _last_sent.update(state)
    path = _state_path(config)
    if not path:
        return
    try:
        trimmed = dict(list(state.items())[-40:])
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(trimmed, handle)
    except Exception as exc:  # noqa: BLE001 - read-only host, keep going
        log.debug("could not persist webhook state: %s", exc)


def already_sent(config, key, now):
    """True when this exact alert went out inside the suppression window."""
    window = float(getattr(config, "webhook_min_interval", 0) or 0)
    if window <= 0:
        return False
    with _state_lock:
        state = _load_state(config)
        when = state.get(key)
        if when is None:
            return False
        try:
            previous = now.timestamp() if hasattr(now, "timestamp") else float(when)
        except Exception:  # noqa: BLE001
            return False
        age = previous - float(when)
        return 0 <= age < window


def remember(config, key, now):
    with _state_lock:
        state = _load_state(config)
        state[key] = (now.timestamp() if hasattr(now, "timestamp")
                      else float(now))
        _save_state(config, state)


def notify_frost(config, report, now, opener=None):
    """Send a frost alert if one is due. Never raises.

    Returns a small status dict so a caller — or a test — can tell what
    happened without guessing.
    """
    kind = configured(config)
    if not kind:
        return {"sent": False, "reason": "not configured"}

    place = report.get("place") or ""
    frost = (report.get("local") or {}).get("frost")
    message = frost_message(frost, place)
    if not message:
        return {"sent": False, "reason": "nothing to alert"}

    key = "frost:%s" % (frost.get("level") or "")
    key += ":%s" % ((frost.get("first") or {}).get("iso") or "")
    if already_sent(config, key, now):
        return {"sent": False, "reason": "already sent", "key": key}

    timeout = float(getattr(config, "timeout", 20.0) or 20.0)
    try:
        if kind == "ntfy":
            status = send_ntfy(message, config.ntfy_url, timeout, opener)
        else:
            status = send_telegram(message, config.telegram_token,
                                   config.telegram_chat, timeout, opener)
    except Exception as exc:  # noqa: BLE001 - one send, never the page
        log.warning("frost alert via %s failed: %s", kind, exc)
        return {"sent": False, "reason": str(exc), "key": key}

    remember(config, key, now)
    log.info("frost alert sent via %s: %s", kind, message[0])
    return {"sent": True, "via": kind, "status": status, "key": key,
            "title": message[0], "body": message[1]}
