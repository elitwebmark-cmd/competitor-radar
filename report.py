# -*- coding: utf-8 -*-
"""Тверді агрегати по ринку зі знімка (числа для ринкового звіту): активність по
каналах, розподіл форматів (відео / статичні банери / пошук), частка гравців із
відео, топ за обсягом реклами. Рахується без AI — чисто з даних сканера."""
from __future__ import annotations

_FMT_KEYS = ("image", "video", "text", "other")


def aggregates(snap: dict) -> dict:
    doms = (snap or {}).get("domains") or {}
    fmt = {k: 0 for k in _FMT_KEYS}
    g_total = m_total = active_g = active_m = active_any = 0
    per = []
    video_advertisers = []

    for d, rec in doms.items():
        g = (rec or {}).get("google") or {}
        m = (rec or {}).get("meta") or {}
        gc = int(g.get("count") or 0)
        mc = int(m.get("count") or 0)
        g_total += gc
        m_total += mc
        g_run = bool(g.get("running"))
        m_run = bool(m.get("running"))
        if g_run:
            active_g += 1
        if m_run:
            active_m += 1
        if g_run or m_run:
            active_any += 1

        # формати: Google віддає лічильник formats{image,video,text,other};
        # Meta — по кожному креативу окремо (format).
        gf = g.get("formats") or {}
        gvid = int(gf.get("video") or 0)
        for k in _FMT_KEYS:
            fmt[k] += int(gf.get(k) or 0)
        mvid = 0
        for c in (m.get("creatives") or []):
            f = (c.get("format") or "").strip().lower()
            if f in fmt:
                fmt[f] += 1
            else:
                fmt["other"] += 1
            if f == "video":
                mvid += 1
        has_video = (gvid > 0) or (mvid > 0)
        if has_video:
            video_advertisers.append(d)

        per.append({
            "domain": d, "google": gc, "meta": mc, "total": gc + mc,
            "g_running": g_run, "m_running": m_run, "has_video": has_video,
        })

    per.sort(key=lambda x: -x["total"])
    total_ads = g_total + m_total
    fmt_total = sum(fmt.values()) or 1
    n = len(doms) or 1

    def pct(x, base):
        return round(x / base * 100) if base else 0

    return {
        "n": len(doms),
        "active_google": active_g, "active_meta": active_m, "active_any": active_any,
        "g_total": g_total, "m_total": m_total, "total_ads": total_ads,
        "channel_split": {"google": pct(g_total, total_ads), "meta": pct(m_total, total_ads)},
        "formats": fmt,
        "format_shares": {k: pct(v, fmt_total) for k, v in fmt.items()},
        "video_advertisers": video_advertisers,
        "video_share_players": pct(len(video_advertisers), n),
        "per_competitor": per,
        "top": per[:8],
    }
