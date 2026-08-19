"""Перевірка запущеної контекстної реклами через SerpApi
(Google Ads Transparency Center). Викликається ЛИШЕ для одного домену
(бот або поодинокий чек на вебі), бо це платний зовнішній виклик.

Повертає: чи крутиться реклама, приблизну кількість оголошень,
перелік рекламодавців і діплінк у Transparency Center."""
from __future__ import annotations
import re
import datetime
import requests
import config

SERPAPI_URL = "https://serpapi.com/search"


def _host(domain: str) -> str:
    h = re.sub(r"^https?://", "", (domain or "").strip().lower())
    h = h.split("/")[0].strip("/ ")
    return h[4:] if h.startswith("www.") else h


def _deeplink(host: str) -> str:
    return f"https://adstransparency.google.com/?region=UA&domain={host}"


# ключ у відповіді, код SerpApi, людська назва (UA)
_PLATFORMS = [
    ("search", "SEARCH", "Пошук Google"),
    ("youtube", "YOUTUBE", "YouTube"),
    ("shopping", "SHOPPING", "Google Покупки"),
    ("maps", "MAPS", "Карти Google"),
    ("play", "PLAY", "Google Play"),
]
_PLATFORM_LABELS = {k: lbl for k, _code, lbl in _PLATFORMS}


def _base_params(host: str, days: int) -> dict:
    p = {
        "engine": "google_ads_transparency_center",
        "text": host,
        "region": config.ADS_REGION,   # 2804 = Україна
        "api_key": config.SERPAPI_KEY,
    }
    if days and days > 0:   # 0 = без фільтра дати (за весь час, як у Transparency Center)
        today = datetime.date.today()
        p["start_date"] = (today - datetime.timedelta(days=days)).strftime("%Y%m%d")
        p["end_date"] = today.strftime("%Y%m%d")
    return p


def _creative_from(c: dict) -> dict:
    f = (c.get("format") or "").strip().lower()
    img = (c.get("image") or c.get("thumbnail") or c.get("video_thumbnail")
           or c.get("preview_image") or c.get("preview") or "").strip()
    v = c.get("video") or c.get("video_url") or c.get("youtube_video")
    video = v.strip() if isinstance(v, str) else ""
    txt = ""
    for k in ("title", "headline", "text", "description", "snippet", "body"):
        if c.get(k):
            txt = str(c.get(k)).strip()
            break
    return {
        "cid": (c.get("ad_creative_id") or "").strip(),
        "format": f or "other", "image": img, "video": video, "text": txt,
        # details_link → сторінка оголошення на Google (там відео відтворюється)
        "link": (c.get("details_link") or c.get("link") or "").strip(),
        "first_shown": c.get("first_shown"), "last_shown": c.get("last_shown"),
    }


def _platform_query(host: str, days: int, code: str, num: int = 12):
    """(total, creatives[]) по конкретній платформі за вікно (1 запит SerpApi)."""
    p = _base_params(host, days)
    p["platform"] = code
    p["num"] = num
    try:
        d = requests.get(SERPAPI_URL, params=p, timeout=config.ADS_TIMEOUT).json()
    except Exception:
        return 0, []
    if d.get("error"):
        return 0, []
    t = (d.get("search_information") or {}).get("total_results")
    cres = d.get("ad_creatives") or []
    total = int(t) if isinstance(t, (int, float)) and t else len(cres)
    return total, cres


def debug(domain: str) -> dict:
    """Сирі семпли крео (основний + по кожній платформі) для інспекції полів."""
    host = _host(domain)
    days = getattr(config, "ADS_ACTIVE_DAYS", 7)
    out = {"host": host, "days": days, "main_sample": None, "per_platform": {}}
    try:
        d = requests.get(SERPAPI_URL, params=dict(_base_params(host, days), num=3),
                         timeout=config.ADS_TIMEOUT).json()
        cr = d.get("ad_creatives") or []
        out["main_sample"] = cr[0] if cr else None
    except Exception as e:
        out["main_error"] = str(e)[:200]
    for key, code, _lbl in _PLATFORMS:
        try:
            t, cres = _platform_query(host, days, code, num=3)
            out["per_platform"][key] = {"total": t, "sample": cres[0] if cres else None}
        except Exception as e:
            out["per_platform"][key] = {"error": str(e)[:200]}
    return out


def check(domain: str) -> dict:
    host = _host(domain)
    link = _deeplink(host)
    if not config.SERPAPI_KEY:
        return {"checked": False, "note": "SERPAPI_KEY не заданий", "link": link}

    days = getattr(config, "ADS_ACTIVE_DAYS", 0)
    lim = getattr(config, "ADS_CREATIVES_LIMIT", 50)
    params = dict(_base_params(host, days), num=lim)
    try:
        r = requests.get(SERPAPI_URL, params=params, timeout=config.ADS_TIMEOUT)
        data = r.json()
    except Exception as e:
        return {"checked": False, "note": f"помилка запиту: {str(e)[:120]}", "link": link}

    # SerpApi для «нема оголошень» повертає error-повідомлення — це валідний
    # результат «реклами не знайдено», а не збій перевірки.
    err = data.get("error")
    if err:
        low = err.lower()
        if "hasn't returned any results" in low or "no results" in low or "didn't return" in low:
            return {"checked": True, "running": False, "count": 0,
                    "advertisers": [], "period_days": days or None, "link": link}
        return {"checked": False, "note": str(err)[:160], "link": link}

    creatives = data.get("ad_creatives") or []
    total = (data.get("search_information") or {}).get("total_results")
    count = int(total) if isinstance(total, (int, float)) and total else len(creatives)
    advertisers = []
    # розподіл форматів по вибірці креативів (text = пошук, image = медійка/банери, video = YouTube)
    fmt = {"text": 0, "image": 0, "video": 0, "other": 0}
    items = []
    for c in creatives:
        a = (c.get("advertiser") or "").strip()
        if a and a not in advertisers:
            advertisers.append(a)
        f = (c.get("format") or "").strip().lower()
        if f in ("text", "image", "video"):
            fmt[f] += 1
        elif f:
            fmt["other"] += 1
        # прев'ю креативу: картинка (банери) або текст (пошукові), якщо є у відповіді
        img = (c.get("image") or c.get("thumbnail") or "").strip()
        txt = ""
        for k in ("title", "headline", "text", "description", "snippet", "body"):
            if c.get(k):
                txt = str(c.get(k)).strip()
                break
        if img or txt:
            items.append({
                "format": f or "other",
                "image": img,
                "text": txt,
                "link": (c.get("link") or c.get("details_link") or "").strip(),
                "first_shown": c.get("first_shown"),
                "last_shown": c.get("last_shown"),
            })
    running = count > 0 or bool(creatives)

    # матриця + креативи по платформах (+5 запитів SerpApi) — лише коли реклама активна.
    # Крео збираємо прямо з per-platform вибірок (реальні теги платформ), показуємо
    # лише ті, що мають прев'ю-картинку (щоб не було порожніх карток).
    platforms = None
    creatives_out = [dict(it, platforms=[]) for it in items[:lim]]
    if running:
        platforms = {}
        merged = {}
        for key, code, _lbl in _PLATFORMS:
            total, cres = _platform_query(host, days, code, num=lim)
            platforms[key] = total
            for c in cres:
                cr = _creative_from(c)
                # стабільний ідентифікатор крео (той самий CR у різних платформах)
                idk = cr["cid"] or cr["image"] or cr["link"] or (cr["text"][:60] if cr["text"] else "")
                if not idk:
                    continue
                if idk in merged:
                    m = merged[idk]
                    if key not in m["platforms"]:
                        m["platforms"].append(key)
                    # підтягуємо кращі поля, якщо у збереженого їх нема
                    if not m.get("image") and cr.get("image"):
                        m["image"] = cr["image"]
                    if not m.get("video") and cr.get("video"):
                        m["video"] = cr["video"]
                    if not m.get("text") and cr.get("text"):
                        m["text"] = cr["text"]
                else:
                    cr["platforms"] = [key]
                    merged[idk] = cr
        if merged:
            creatives_out = list(merged.values())
    # прибираємо порожні картки (без прев'ю/тексту); відео лишаємо (окрема ▶-картка)
    creatives_out = [c for c in creatives_out
                     if c.get("image") or c.get("video") or c.get("text")
                     or c.get("format") == "video"][:lim]

    return {
        "checked": True,
        "running": running,
        "count": count,
        "advertisers": advertisers[:5],
        "formats": fmt,                       # к-сть по форматах у вибірці
        "formats_sampled": len(creatives),    # скільки креативів проаналізовано
        "creatives": creatives_out,           # прев'ю креативів з тегами платформ (лише веб)
        "platforms": platforms,               # к-сть оголошень по платформах
        "platform_labels": _PLATFORM_LABELS if running else None,
        "period_days": days or None,          # None = за весь час (без фільтра дати)
        "link": link,
    }
