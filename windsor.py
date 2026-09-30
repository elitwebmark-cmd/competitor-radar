# -*- coding: utf-8 -*-
"""Клієнт Windsor.ai (REST connectors API) — тягне ВЛАСНІ KPI реклами elit-web
(Google Ads + Meta) за останні 30 днів, щоб план дій спирався на реальні цифри.
Працює лише якщо заданий WINDSOR_API_KEY; інакше повертає None (план будується
без наших метрик, тільки з даних Ad Library)."""
from __future__ import annotations
import logging
import requests
import config

log = logging.getLogger("radar.windsor")


def enabled() -> bool:
    return bool(config.WINDSOR_API_KEY)


def _fetch(connector: str, fields: str) -> dict:
    """Один агрегований рядок метрик по конектору (без date-виміру → сума за період)."""
    url = f"{config.WINDSOR_BASE.rstrip('/')}/{connector}"
    params = {"api_key": config.WINDSOR_API_KEY, "date_preset": config.WINDSOR_DATE_PRESET,
              "fields": fields}
    try:
        r = requests.get(url, params=params, timeout=config.HTTP_TIMEOUT + 8)
        if r.status_code != 200:
            log.warning("windsor %s -> HTTP %s: %s", connector, r.status_code, r.text[:160])
            return {}
        rows = (r.json() or {}).get("data") or []
    except Exception as e:
        log.warning("windsor %s error: %s", connector, str(e)[:160])
        return {}
    # підсумовуємо числові поля по всіх рядках
    agg = {}
    for row in rows:
        for k, v in (row or {}).items():
            try:
                agg[k] = agg.get(k, 0) + float(v)
            except (TypeError, ValueError):
                pass
    return agg


def _num(x):
    try:
        return round(float(x), 2)
    except (TypeError, ValueError):
        return 0.0


def _channel(agg: dict) -> dict:
    spend = _num(agg.get("spend"))
    clicks = _num(agg.get("clicks"))
    impr = _num(agg.get("impressions"))
    conv = _num(agg.get("conversions"))
    return {
        "spend": int(round(spend)), "clicks": int(round(clicks)),
        "impressions": int(round(impr)), "conversions": int(round(conv)),
        "ctr": round(clicks / impr * 100, 2) if impr else None,
        "cpc": round(spend / clicks, 2) if clicks else None,
        "conv_rate": round(conv / clicks * 100, 2) if clicks else None,
        "cpa": int(round(spend / conv)) if conv else None,
    }


def our_kpis() -> dict | None:
    """{google:{...}, meta:{...}, period} власних РК за 30 днів або None."""
    if not enabled():
        return None
    g = _channel(_fetch("google_ads", "spend,clicks,impressions,conversions"))
    m = _channel(_fetch("facebook", "spend,clicks,impressions,conversions"))
    if not (g.get("spend") or m.get("spend") or g.get("clicks") or m.get("clicks")):
        return None
    return {"google": g, "meta": m, "period": config.WINDSOR_DATE_PRESET}
