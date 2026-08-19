"""Сховище знімків (snapshots) рекламної активності + обчислення дельт.
Кожен скан зберігається окремим JSON-файлом у DATA_DIR. Історія дає динаміку
«хто почав / перестав / наростив / зменшив» між двома останніми зрізами."""
from __future__ import annotations
import os
import json
import time
import glob
import datetime
import threading

import config

_LOCK = threading.Lock()
_KEEP = int(os.getenv("SNAP_KEEP", "90"))          # скільки останніх зрізів тримати


def _dir() -> str:
    os.makedirs(config.DATA_DIR, exist_ok=True)
    return config.DATA_DIR


def save_snapshot(snap: dict) -> str:
    """Зберігає знімок. snap = {ts, date, domains:{domain:record}}. Повертає шлях."""
    with _LOCK:
        d = _dir()
        ts = int(snap.get("ts") or time.time())
        path = os.path.join(d, f"snap_{ts}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False)
        _prune()
        return path


def _prune():
    files = sorted(glob.glob(os.path.join(_dir(), "snap_*.json")))
    for p in files[:-_KEEP]:
        try:
            os.remove(p)
        except OSError:
            pass


def list_snapshots() -> list:
    """Метадані всіх зрізів (без важких даних), новіші першими."""
    out = []
    for p in glob.glob(os.path.join(_dir(), "snap_*.json")):
        try:
            ts = int(os.path.basename(p)[5:-5])
        except ValueError:
            continue
        out.append({"ts": ts, "path": p,
                    "date": datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M")})
    return sorted(out, key=lambda x: x["ts"], reverse=True)


def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def latest() -> dict | None:
    snaps = list_snapshots()
    return _load(snaps[0]["path"]) if snaps else None


def previous() -> dict | None:
    snaps = list_snapshots()
    return _load(snaps[1]["path"]) if len(snaps) > 1 else None


def _running(rec: dict, ch: str) -> bool:
    x = (rec or {}).get(ch) or {}
    return bool(x.get("running"))


def _count(rec: dict, ch: str) -> int:
    x = (rec or {}).get(ch) or {}
    try:
        return int(x.get("count") or 0)
    except (ValueError, TypeError):
        return 0


def compute_deltas(cur: dict, prev: dict) -> dict:
    """Порівнює два знімки. Повертає per-domain стан + зведення."""
    cur = cur or {}
    prev = prev or {}
    cdom = cur.get("domains") or {}
    pdom = prev.get("domains") or {}
    rows = []
    started_g, stopped_g, started_m, stopped_m = [], [], [], []
    active_g = active_m = active_any = 0

    for domain in cdom:
        c = cdom.get(domain) or {}
        p = pdom.get(domain)
        gnow, gprev = _running(c, "google"), _running(p, "google") if p else None
        mnow, mprev = _running(c, "meta"), _running(p, "meta") if p else None
        gcn, mcn = _count(c, "google"), _count(c, "meta")
        gcp, mcp = (_count(p, "google") if p else None), (_count(p, "meta") if p else None)

        if gnow:
            active_g += 1
        if mnow:
            active_m += 1
        if gnow or mnow:
            active_any += 1

        def chg(now, was):
            if was is None:
                return "new"          # немає попереднього зрізу по домену
            if now and not was:
                return "started"
            if was and not now:
                return "stopped"
            return "same"

        gstat, mstat = chg(gnow, gprev), chg(mnow, mprev)
        if gstat == "started":
            started_g.append(domain)
        elif gstat == "stopped":
            stopped_g.append(domain)
        if mstat == "started":
            started_m.append(domain)
        elif mstat == "stopped":
            stopped_m.append(domain)

        rows.append({
            "domain": domain,
            "google": c.get("google") or {},
            "meta": c.get("meta") or {},
            "g_running": gnow, "m_running": mnow,
            "g_count": gcn, "m_count": mcn,
            "g_count_prev": gcp, "m_count_prev": mcp,
            "g_delta": (gcn - gcp) if gcp is not None else None,
            "m_delta": (mcn - mcp) if mcp is not None else None,
            "g_status": gstat, "m_status": mstat,
        })

    # сортування: активні першими, потім за сумарною кількістю оголошень
    rows.sort(key=lambda r: (not (r["g_running"] or r["m_running"]),
                             -(r["g_count"] + r["m_count"])))
    return {
        "rows": rows,
        "total": len(rows),
        "active_google": active_g,
        "active_meta": active_m,
        "active_any": active_any,
        "started_google": started_g, "stopped_google": stopped_g,
        "started_meta": started_m, "stopped_meta": stopped_m,
        "has_prev": bool(pdom),
        "cur_ts": cur.get("ts"), "prev_ts": prev.get("ts"),
    }
