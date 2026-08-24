"""Сканер рекламної активності конкурентів: Google Ads Transparency (ads.py) +
Meta Ad Library (meta_ads.py). Збирає знімок по всіх доменах і зберігає у store."""
from __future__ import annotations
import time
import logging
import threading
import concurrent.futures

import config
import ads
import meta_ads
import store
import ai

log = logging.getLogger("radar.scan")

# --- статус поточного/останнього сканування (для UI) ---
# phase: "scan" (реклама) → "ai" (AI по кожному) → "market" (ринковий огляд) → "done"
STATUS = {"running": False, "phase": "", "total": 0, "done": 0, "started": None,
          "finished": None, "current": "", "error": ""}
_STATUS_LOCK = threading.Lock()
_RUN_LOCK = threading.Lock()          # захист від паралельних сканів


def _set(**kw):
    with _STATUS_LOCK:
        STATUS.update(kw)


def get_status() -> dict:
    with _STATUS_LOCK:
        return dict(STATUS)


def _google(domain: str) -> dict:
    try:
        r = ads.check(domain) or {}
    except Exception as e:
        return {"checked": False, "running": False, "count": 0, "note": str(e)[:140]}
    return {
        "checked": bool(r.get("checked")),
        "running": bool(r.get("running")),
        "count": int(r.get("count") or 0),
        "formats": r.get("formats") or {},
        "platforms": r.get("platforms") or {},
        "platform_labels": r.get("platform_labels") or {},
        "advertisers": r.get("advertisers") or [],
        "period_days": r.get("period_days"),
        "creatives": r.get("creatives") or [],
        "link": r.get("link") or "",
        "note": r.get("note") or "",
    }


def _meta(domain: str) -> dict:
    try:
        r = meta_ads.check(domain) or {}
    except Exception as e:
        return {"checked": False, "running": False, "count": 0, "note": str(e)[:140]}
    running = r.get("running")
    if running is None:                       # meta_ads не завжди віддає running явно
        running = bool(r.get("count"))
    return {
        "checked": bool(r.get("checked")),
        "running": bool(running),
        "count": int(r.get("count") or 0),
        "page": r.get("page") or "",
        "platforms": r.get("platforms") or {},
        "by_keyword": bool(r.get("by_keyword")),
        "creatives": r.get("creatives") or [],
        "link": r.get("link") or "",
        "note": r.get("note") or "",
    }


def scan_domain(domain: str) -> dict:
    return {"domain": domain, "google": _google(domain), "meta": _meta(domain),
            "ts": int(time.time())}


def _scan_core(domains) -> dict:
    """Сканує домени (без верхнього блокування) → знімок, зберігає, повертає."""
    records = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=config.SCAN_WORKERS) as ex:
        futs = {ex.submit(scan_domain, d): d for d in domains}
        for fut in concurrent.futures.as_completed(futs):
            d = futs[fut]
            try:
                records[d] = fut.result()
            except Exception as e:
                records[d] = {"domain": d,
                              "google": {"checked": False, "running": False, "count": 0, "note": str(e)[:140]},
                              "meta": {"checked": False, "running": False, "count": 0, "note": str(e)[:140]},
                              "ts": int(time.time())}
            with _STATUS_LOCK:
                STATUS["done"] += 1
                STATUS["current"] = d
    ts = int(time.time())
    snap = {"ts": ts, "date": time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts)),
            "domains": records}
    store.save_snapshot(snap)
    return snap


def scan_all(domains=None) -> dict:
    """Скан лише реклами (Google+Meta). Ідемпотентно щодо паралельного запуску."""
    domains = domains or config.COMPETITORS
    if not _RUN_LOCK.acquire(blocking=False):
        log.info("scan вже виконується — пропуск")
        return get_status()
    try:
        _set(running=True, phase="scan", total=len(domains), done=0,
             started=int(time.time()), finished=None, current="", error="")
        snap = _scan_core(domains)
        _set(running=False, phase="done", finished=int(time.time()), current="")
        log.info("scan завершено: %d доменів", len(snap.get("domains", {})))
        return snap
    except Exception as e:
        log.exception("scan впав")
        _set(running=False, error=str(e)[:200], finished=int(time.time()))
        return get_status()
    finally:
        _RUN_LOCK.release()


def full_refresh(domains=None) -> dict:
    """Повне оновлення однією дією: реклама (скан) → AI-розбір кожного → ринковий огляд."""
    domains = domains or config.COMPETITORS
    if not _RUN_LOCK.acquire(blocking=False):
        log.info("full_refresh: вже виконується — пропуск")
        return get_status()
    try:
        # фаза 1 — реклама
        _set(running=True, phase="scan", total=len(domains), done=0,
             started=int(time.time()), finished=None, current="", error="")
        snap = _scan_core(domains)
        doms = snap.get("domains", {})
        # фаза 2 — AI по кожному конкуренту
        if ai.enabled():
            _set(phase="ai", total=len(doms), done=0, current="")
            for d, rec in doms.items():
                _set(current=d)
                try:
                    rec["ai"] = ai.analyze_competitor(d, rec)
                    rec["ai_ts"] = int(time.time())
                except Exception as e:
                    rec["ai"] = {"error": str(e)[:200]}
                store.save_snapshot(snap)               # прогресивне збереження
                with _STATUS_LOCK:
                    STATUS["done"] += 1
            # фаза 3 — ринковий огляд
            _set(phase="market", current="")
            try:
                items = [{"domain": d, "ai": (r.get("ai") or {})} for d, r in doms.items()]
                snap["ai_market"] = ai.analyze_market(items)
                snap["ai_market_ts"] = int(time.time())
                store.save_snapshot(snap)
            except Exception:
                log.exception("full_refresh: ринковий огляд")
        else:
            log.info("full_refresh: AI вимкнено (немає ANTHROPIC_API_KEY) — лише реклама")
        _set(running=False, phase="done", finished=int(time.time()), current="")
        log.info("full_refresh завершено")
        return snap
    except Exception as e:
        log.exception("full_refresh впав")
        _set(running=False, error=str(e)[:200], finished=int(time.time()))
        return get_status()
    finally:
        _RUN_LOCK.release()


def scan_async(domains=None):
    """Запускає скан реклами у фоні (кнопка «Оновити зараз»)."""
    if STATUS.get("running"):
        return False
    threading.Thread(target=scan_all, args=(domains,), name="radar-scan", daemon=True).start()
    return True


def full_refresh_async(domains=None):
    """Запускає повне оновлення (реклама + AI) у фоні (кнопка «Повне оновлення»)."""
    if STATUS.get("running"):
        return False
    threading.Thread(target=full_refresh, args=(domains,), name="radar-full", daemon=True).start()
    return True
