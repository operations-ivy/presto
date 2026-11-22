"""presto-alerts: the Presto's pager (presto#52), a small read and silence API in
front of Alertmanager. The Presto never talks to Alertmanager itself.

    GET  /alerts    {"at", "n", "worst", "alerts": [{"id", "name", "sev", "for", "sum"}]}
    POST /silence   {"id": <fingerprint>}  ->  a 1 hour silence of that one alert, by "presto"
    GET  /health /ready /metrics

Not an Alertmanager receiver: the Presto polls GET /alerts, so nothing new is
routed anywhere and phone pages stay paused (the user, 2026-10-26). Only
alerts firing now (not silenced or inhibited) are listed, worst first, without
Watchdog and InfoInhibitor (they always fire, by design). The JSON stays small
for a microcontroller: short keys, whole seconds, at most MAX_ALERTS alerts and
SUMMARY_CHARS of each summary.

POST /silence only silences an alert that is firing now, for SILENCE_S, and
matches it by all of its labels, so one tap can't silence anything else.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

import brick_observe
from brick_observe import metrics as observe_metrics
from prometheus_client import Counter

log = brick_observe.get_logger(__name__)

ALERTMANAGER = "http://kube-prometheus-stack-alertmanager.monitoring.svc.cluster.local:9093"
SEVERITIES = {"critical": 3, "warning": 2, "info": 1}  # anything else counts as info
IGNORED = {"Watchdog", "InfoInhibitor"}
MAX_ALERTS = 8
SUMMARY_CHARS = 160
SILENCE_S = 3600
MAX_BODY = 1024

REQUESTS = Counter("alerts_upstream_total", "Calls to Alertmanager, by call and result", ["call", "result"])


def _time(value: str) -> float:
    """Alertmanager's RFC 3339 time as epoch seconds."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def severity(alert: dict[str, Any]) -> str:
    sev = alert.get("labels", {}).get("severity", "info")
    return sev if sev in SEVERITIES else "info"


def firing(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Alertmanager's alerts that are firing, not silenced or inhibited, and worth showing."""
    return [
        a
        for a in alerts
        if a.get("status", {}).get("state", "active") == "active"
        and a.get("labels", {}).get("alertname") not in IGNORED
    ]


def trim(alerts: list[dict[str, Any]], now: float) -> dict[str, Any]:
    """What the Presto needs from Alertmanager's alerts: worst first, then oldest."""
    shown = sorted(firing(alerts), key=lambda a: (-SEVERITIES[severity(a)], a.get("startsAt", "")))
    rows = []
    for a in shown[:MAX_ALERTS]:
        notes = a.get("annotations", {})
        summary = notes.get("summary") or notes.get("description") or notes.get("message") or ""
        rows.append(
            {
                "id": a["fingerprint"],
                "name": a["labels"].get("alertname", "?"),
                "sev": severity(a),
                "for": max(0, int(now - _time(a["startsAt"]))) if a.get("startsAt") else 0,
                "sum": " ".join(summary.split())[:SUMMARY_CHARS],
            }
        )
    worst = max((SEVERITIES[severity(a)] for a in shown), default=0)
    names = {v: k for k, v in SEVERITIES.items()}
    return {"at": int(now), "n": len(shown), "worst": names.get(worst), "alerts": rows}


def silence_for(alert: dict[str, Any], now: float, seconds: int = SILENCE_S) -> dict[str, Any]:
    """An Alertmanager silence that matches exactly this alert (every label)."""
    return {
        "matchers": [
            {"name": k, "value": v, "isRegex": False, "isEqual": True} for k, v in sorted(alert["labels"].items())
        ],
        "startsAt": _iso(now),
        "endsAt": _iso(now + seconds),
        "createdBy": "presto",
        "comment": "Silenced for %d minutes from the Presto's ALERTS screen (presto#52)" % (seconds // 60),
    }


class Alertmanager:
    def __init__(self, url: str = ALERTMANAGER, timeout: float = 5.0):
        self.url, self.timeout = url.rstrip("/"), timeout

    def _call(self, name: str, req: urllib.request.Request | str) -> Any:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.load(resp)
        except Exception:
            REQUESTS.labels(name, "error").inc()
            raise
        REQUESTS.labels(name, "ok").inc()
        return body

    def alerts(self) -> list[dict[str, Any]]:
        return self._call("alerts", f"{self.url}/api/v2/alerts?active=true&silenced=false&inhibited=false")

    def silence(self, body: dict[str, Any]) -> str:
        req = urllib.request.Request(
            f"{self.url}/api/v2/silences",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._call("silence", req).get("silenceID", "")


def silence(am: Alertmanager, fingerprint: str, now: float) -> tuple[int, dict[str, Any]]:
    """Silence one firing alert by its fingerprint: (HTTP status, answer)."""
    alert = next((a for a in firing(am.alerts()) if a.get("fingerprint") == fingerprint), None)
    if alert is None:
        return 404, {"error": "not firing"}
    body = silence_for(alert, now)
    silence_id = am.silence(body)
    log.info("silenced", alertname=alert["labels"].get("alertname"), minutes=SILENCE_S // 60)
    return 200, {"id": fingerprint, "silence": silence_id, "until": int(now + SILENCE_S)}


def make_handler(am: Alertmanager):
    class Handler(brick_observe.httpserver.Observed, BaseHTTPRequestHandler):
        routes = {"/alerts", "/silence"}
        server_version = "presto-alerts"
        protocol_version = "HTTP/1.1"

        def send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def answer(self, code: int, value: dict[str, Any]) -> None:
            self.send(code, json.dumps(value, separators=(",", ":")).encode())

        def do_GET(self):
            path = urlsplit(self.path).path
            if path in ("/health", "/ready"):
                return self.send(200, b"ok\n", "text/plain")
            if path == "/metrics":
                body, ctype = observe_metrics.exposition()
                return self.send(200, body, ctype)
            if path == "/alerts":
                try:
                    return self.answer(200, trim(am.alerts(), time.time()))
                except (OSError, ValueError) as e:
                    log.warning("alertmanager unreadable", error=repr(e))
                    return self.answer(502, {"error": "alertmanager unreachable"})
            return self.answer(404, {"error": "not found"})

        def do_POST(self):
            if urlsplit(self.path).path != "/silence":
                return self.answer(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_BODY:
                return self.answer(400, {"error": "a JSON body with the alert's id"})
            try:
                fingerprint = json.loads(self.rfile.read(length))["id"]
                if not isinstance(fingerprint, str):
                    raise TypeError(fingerprint)
            except (ValueError, KeyError, TypeError):
                return self.answer(400, {"error": "a JSON body with the alert's id"})
            try:
                return self.answer(*silence(am, fingerprint, time.time()))
            except (OSError, ValueError) as e:
                log.warning("silence failed", error=repr(e))
                return self.answer(502, {"error": "alertmanager unreachable"})

    return Handler


def serve(port: int = 8080) -> None:
    am = Alertmanager(os.environ.get("ALERTMANAGER_URL", ALERTMANAGER))
    httpd = ThreadingHTTPServer(("", port), make_handler(am))
    httpd.daemon_threads = True
    log.info("alerts listening", port=port)
    httpd.serve_forever()
