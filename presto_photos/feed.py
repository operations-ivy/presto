"""presto-feed: what the Presto's Brick screen shows, as one small JSON (presto#5).

Everything comes from Prometheus, which already scrapes the cluster, the hosts
outside it, Argo CD, the front door and Home Assistant (whose sensors it reads
as the Monitoring user), so this pod holds no token at all. Every REFRESH_S it
asks Prometheus a handful of queries and keeps the answer; the Presto polls

    GET /brick.json   {"at", "age", "nodes", "apps", "pods", "door", "temps"}
    GET /health /ready /metrics

The Presto is a microcontroller: the JSON stays small (short keys, whole
numbers, at most MAX_ROWS problems per list) and is built here, not there.
If Prometheus stops answering, the last answer stays, with its old "at": the
Presto says STALE.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import brick_observe
from brick_observe import metrics as observe_metrics
from prometheus_client import Counter, Gauge

log = brick_observe.get_logger(__name__)

PROMETHEUS = "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090"
REFRESH_S = 30
MAX_ROWS = 4  # problems listed per kind; the rest are counted

# The cluster's node-exporter DaemonSet, and the hosts outside the cluster that
# Prometheus scrapes by name (brick9000, brick1982). brick69 is left out: it
# wanders off wardriving, which isn't a problem worth a red column.
NODE_JOBS = 'job=~"node-exporter|brick9000-node|brick1982-node"'
QUERIES = {
    "ready": 'kube_node_status_condition{condition="Ready",status="true"}',
    "host_up": 'up{job=~"brick9000-node|brick1982-node"}',
    "uname": f"node_uname_info{{{NODE_JOBS}}}",
    "cpu": f'100 * (1 - avg by (instance) (rate(node_cpu_seconds_total{{{NODE_JOBS},mode="idle"}}[2m])))',
    "mem": f"100 * (1 - node_memory_MemAvailable_bytes{{{NODE_JOBS}}} / node_memory_MemTotal_bytes{{{NODE_JOBS}}})",
    "temp": f"max by (instance) (node_thermal_zone_temp{{{NODE_JOBS}}})",
    "apps": "argocd_app_info",
    "stuck": "sum by (namespace, pod, reason) (kube_pod_container_status_waiting_reason"
    '{reason=~"CrashLoopBackOff|ImagePullBackOff|ErrImagePull|CreateContainerConfigError"}) > 0',
    "unavailable": "kube_deployment_status_replicas_unavailable > 0",
    "unready_sts": "kube_statefulset_replicas - kube_statefulset_status_replicas_ready > 0",
    "door_up": 'up{job="front-door"}',
    "door_5xx": 'sum(increase(caddy_http_request_duration_seconds_count{code=~"5.."}[10m]))',
    # Only sensors Home Assistant calls available: an offline one isn't shown.
    "temps": "hass_sensor_temperature_celsius and on (entity) (hass_entity_available == 1)",
}
# Names that aren't indoors. None today; a future outdoor sensor stays off the screen.
OUTDOOR = re.compile(r"outdoor|outside|exterior|weather", re.I)

REFRESHES = Counter("feed_refreshes_total", "Feed refreshes, by result", ["result"])
AGE = Gauge("feed_age_seconds", "Seconds since the feed last had a full answer")


class Prometheus:
    def __init__(self, url: str = PROMETHEUS, timeout: float = 5.0):
        self.url, self.timeout = url, timeout

    def query(self, promql: str) -> list[tuple[dict, float]]:
        qs = urllib.parse.urlencode({"query": promql})
        with urllib.request.urlopen(f"{self.url}/api/v1/query?{qs}", timeout=self.timeout) as resp:
            body = json.load(resp)
        if body.get("status") != "success":
            raise RuntimeError(body.get("error", "query failed"))
        return [(r["metric"], float(r["value"][1])) for r in body["data"]["result"]]


def _order(name: str):
    """brick420 before brick2000: by the number in the name, not as text."""
    return (len(name), name)


def sensor_name(friendly: str) -> str:
    """'My ecobee Current Temperature' -> 'My ecobee'; 'Bedroom temperature' -> 'Bedroom'."""
    s = re.sub(r"\s*(current\s+)?temperature\s*$", "", friendly, flags=re.I).strip()
    return s or friendly


def build(r: dict[str, list[tuple[dict, float]]], now: float) -> dict:
    """The feed from the QUERIES' results (pure: tested without Prometheus)."""
    names = {m["instance"]: m.get("nodename", m["instance"]) for m, _ in r["uname"]}
    nodes: dict[str, dict] = {}
    for m, v in r["ready"]:
        nodes[m["node"]] = {"n": m["node"], "ok": int(v == 1), "k8s": 1}
    for m, v in r["host_up"]:
        n = names.get(m["instance"], m["instance"])
        nodes[n] = {"n": n, "ok": int(v == 1), "k8s": 0}
    for key in ("cpu", "mem", "temp"):
        for m, v in r[key]:
            n = names.get(m.get("instance"), m.get("instance"))
            if n in nodes:
                nodes[n][key] = round(v)

    apps = sorted(r["apps"], key=lambda mv: mv[0].get("name", ""))
    bad_apps = [
        [m.get("name", "?"), m.get("sync_status", "?"), m.get("health_status", "?")]
        for m, _ in apps
        if m.get("sync_status") != "Synced" or m.get("health_status") != "Healthy"
    ]

    pods = [
        [f"{m['namespace']}/{m.get('deployment') or m.get('statefulset')}", f"{int(v)} NOT READY"]
        for m, v in r["unavailable"] + r["unready_sts"]
    ]
    pods += [[f"{m['namespace']}/{m['pod']}", m["reason"]] for m, _ in r["stuck"]]

    door_up = any(v == 1 for _, v in r["door_up"])
    errors = round(sum(v for _, v in r["door_5xx"]))

    temps = sorted(
        (
            {"n": sensor_name(m.get("friendly_name") or m.get("entity", "?")), "c": round(v, 1)}
            for m, v in r["temps"]
            if not OUTDOOR.search(m.get("friendly_name") or m.get("entity", ""))
        ),
        key=lambda t: t["n"].lower(),
    )
    return {
        "at": int(now),
        "nodes": sorted(nodes.values(), key=lambda n: _order(n["n"])),
        "apps": {"n": len(apps), "bad": bad_apps[:MAX_ROWS], "nbad": len(bad_apps)},
        "pods": {"bad": pods[:MAX_ROWS], "nbad": len(pods)},
        "door": {"ok": int(door_up), "e5xx": errors},
        "temps": temps,
    }


class Feed:
    def __init__(self, prom: Prometheus):
        self.prom = prom
        self.snapshot: dict | None = None
        self.lock = threading.Lock()

    def body(self) -> bytes:
        """The JSON, with "age": how old the answer is (the Presto's clock can't tell)."""
        with self.lock:
            snap = self.snapshot
        if snap is None:
            return b""
        return json.dumps({**snap, "age": int(time.time() - snap["at"])}, separators=(",", ":")).encode()

    def refresh(self) -> None:
        try:
            r = {k: self.prom.query(q) for k, q in QUERIES.items()}
        except Exception as e:  # keep the last answer; the Presto sees its age
            REFRESHES.labels("error").inc()
            log.warning("feed refresh failed", error=repr(e))
            return
        snap = build(r, time.time())
        with self.lock:
            self.snapshot = snap
        REFRESHES.labels("ok").inc()

    def age(self) -> float:
        return time.time() - self.snapshot["at"] if self.snapshot else float("inf")

    def loop(self) -> None:
        while True:
            self.refresh()
            AGE.set(self.age())
            time.sleep(REFRESH_S)


def make_handler(feed: Feed):
    class Handler(brick_observe.httpserver.Observed, BaseHTTPRequestHandler):
        routes = {"/brick.json"}
        server_version = "presto-feed"
        protocol_version = "HTTP/1.1"

        def send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/health":
                return self.send(200, b"ok\n", "text/plain")
            if path == "/ready":
                ok = feed.snapshot is not None
                return self.send(200 if ok else 503, b"ok\n" if ok else b"no answer yet\n", "text/plain")
            if path == "/metrics":
                AGE.set(feed.age())
                body, ctype = observe_metrics.exposition()
                return self.send(200, body, ctype)
            if path == "/brick.json":
                body = feed.body()
                if not body:
                    return self.send(503, b'{"error":"no answer from Prometheus yet"}', "application/json")
                return self.send(200, body, "application/json")
            return self.send(404, b'{"error":"not found"}', "application/json")

    return Handler


def serve(port: int = 8080) -> None:
    feed = Feed(Prometheus(os.environ.get("PROMETHEUS_URL", PROMETHEUS)))
    threading.Thread(target=feed.loop, name="feed", daemon=True).start()
    httpd = ThreadingHTTPServer(("", port), make_handler(feed))
    httpd.daemon_threads = True
    log.info("feed listening", port=port)
    httpd.serve_forever()
