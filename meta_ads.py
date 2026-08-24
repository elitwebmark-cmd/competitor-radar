# -*- coding: utf-8 -*-
"""Meta (Facebook/Instagram) реклама через Apify Ad Library Scraper.
Deep-чек (1 домен). Дістає: к-сть активних креативів, майданчики
(Facebook/Instagram/Messenger/Audience Network) і зображення креативів.

Актор і вхідні дані задаються через env (APIFY_META_ACTOR / APIFY_META_INPUT),
парсер полів толерантний до різних акторів. Таргетинг (аудиторії) Meta не
розкриває для комерційної реклами — показуємо лише крео/майданчики/к-сть."""
from __future__ import annotations
import re
import json
import urllib.parse
import requests
import config

_FB_ID_RE = re.compile(r"facebook\.com/(?:profile\.php\?id=|pages/[^/]+/)(\d{5,})", re.I)
_FB_RE = re.compile(r"facebook\.com/([A-Za-z0-9_.\-]{2,60})", re.I)
_FB_RESERVED = {"sharer", "plugins", "tr", "dialog", "events", "groups", "profile.php",
                "people", "pages", "watch", "story.php", "permalink.php", "help",
                "policies", "business", "login", "recover", "home.php", "photo.php",
                "hashtag", "search", "marketplace", "gaming", "ads"}


def _host(domain: str) -> str:
    h = re.sub(r"^https?://", "", (domain or "").strip().lower()).split("/")[0]
    return h[4:] if h.startswith("www.") else h


def find_facebook_page(domain: str):
    """FB-сторінка з головної сайту: ('id', '153...') або ('slug', 'turbowebua') або None."""
    host = _host(domain)
    headers = {"User-Agent": config.USER_AGENT, "Accept-Language": config.ACCEPT_LANGUAGE}
    for url in (f"https://{host}", f"http://{host}"):
        try:
            r = requests.get(url, headers=headers, timeout=config.HTTP_TIMEOUT, allow_redirects=True)
            if r.status_code >= 400 or not r.text:
                continue
            m = _FB_ID_RE.search(r.text)
            if m:
                return ("id", m.group(1))
            for m in _FB_RE.finditer(r.text):
                slug = m.group(1).strip("/.").lower()
                if slug and slug not in _FB_RESERVED and not slug.endswith(".php"):
                    return ("slug", slug)
            return None
        except requests.RequestException:
            continue
    return None


def _library_url(query: str) -> str:
    q = urllib.parse.quote(query)
    return (f"https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
            f"&country={config.META_ADS_COUNTRY}&q={q}&search_type=keyword_unordered&media_type=all")


def _run_apify(url: str, max_items: int):
    tpl = config.APIFY_META_INPUT.replace("{url}", url).replace("{count}", str(max_items))
    try:
        payload = json.loads(tpl)
    except json.JSONDecodeError:
        payload = {"startUrls": [{"url": url}], "count": max_items}
    # maxItems — жорсткий кеп кількості результатів (і вартості для pay-per-result акторів),
    # незалежно від того, чи актор шанує "count". Захист від keyword-пошуку на сотні чужих крео.
    endpoint = (f"https://api.apify.com/v2/acts/{config.APIFY_META_ACTOR}"
                f"/run-sync-get-dataset-items"
                f"?token={config.APIFY_TOKEN}&timeout={config.APIFY_TIMEOUT}"
                f"&maxItems={int(max_items)}")
    r = requests.post(endpoint, json=payload, timeout=config.APIFY_TIMEOUT + 15)
    if r.status_code >= 400:
        raise RuntimeError(f"apify HTTP {r.status_code}: {r.text[:160]}")
    data = r.json()
    return data if isinstance(data, list) else (data.get("items") or [])


_PLAT_MAP = {
    "facebook": "facebook", "fb": "facebook",
    "instagram": "instagram", "ig": "instagram",
    "messenger": "messenger",
    "audience_network": "audience_network", "audience network": "audience_network",
    "an": "audience_network",
}


def _snap(item: dict) -> dict:
    sn = item.get("snapshot") or item.get("adSnapshot") or {}
    if isinstance(sn, str):
        try:
            sn = json.loads(sn)
        except (json.JSONDecodeError, TypeError):
            sn = {}
    return sn if isinstance(sn, dict) else {}


def _platforms_of(item: dict):
    sn = _snap(item)
    for src in (item, sn):
        for k in ("publisher_platforms", "publisherPlatforms", "publisher_platform",
                  "publisherPlatform", "platforms"):
            v = src.get(k)
            if isinstance(v, str):
                v = [v]
            if isinstance(v, list) and v:
                return [str(x).lower().replace(" ", "_") for x in v]
    return []


def _images_of(item: dict):
    imgs = []
    sn = _snap(item)

    def _add(u):
        if u and isinstance(u, str) and u.startswith("http") and u not in imgs:
            imgs.append(u)

    def _img_from(d):
        if isinstance(d, dict):
            _add(d.get("original_image_url") or d.get("originalImageUrl")
                 or d.get("resized_image_url") or d.get("resizedImageUrl") or d.get("url"))
        elif isinstance(d, str):
            _add(d)

    for im in (sn.get("images") or item.get("images") or []):
        _img_from(im)
    for c in (sn.get("cards") or []):
        _img_from(c)
    for v in (sn.get("videos") or []):
        if isinstance(v, dict):
            _add(v.get("videoPreviewImageUrl") or v.get("video_preview_image_url")
                 or v.get("thumbnailUrl") or v.get("thumbnail_url"))
    for k in ("originalImageUrl", "imageUrl", "thumbnailUrl", "image", "thumbnail"):
        _add(item.get(k))
    return imgs


def _fetch_ads(url: str, max_items: int = None):
    """(ads_list, page_name). Обробляє два формати актора: оголошення напряму
    або обгортку {pageInfo, results:[...]}"""
    items = _run_apify(url, max_items or config.META_ADS_LIMIT)
    ads_list, page_name = [], None
    for it in items:
        if not isinstance(it, dict):
            continue
        if isinstance(it.get("results"), list):
            ads_list.extend(it["results"])
            pg = ((it.get("pageInfo") or {}).get("page") or {})
            page_name = page_name or pg.get("name")
        elif "snapshot" in it or "publisherPlatform" in it or "adArchiveID" in it:
            ads_list.append(it)
    return ads_list, page_name


def check(domain: str, debug: bool = False) -> dict:
    if not config.APIFY_TOKEN:
        return {"checked": False, "note": "APIFY_TOKEN не заданий"}
    # ЛИШЕ точний пошук за FB-сторінкою бренду. Keyword-пошук прибрано —
    # на практиці він ненадійний (підтягує чужі оголошення).
    fb = find_facebook_page(domain)
    if not fb:
        brand0 = _host(domain).split(".")[0]
        lib_url = _library_url(brand0)
        if debug:
            return {"checked": True, "count": 0, "no_page": True,
                    "target_url": None, "by_keyword": False, "raw_sample": None}
        return {"checked": True, "running": False, "count": 0, "page": brand0,
                "platforms": {}, "creatives": [], "by_keyword": False,
                "note": "FB-сторінку бренду не знайдено на сайті — Meta не перевіряли",
                "link": lib_url}
    page = fb[1]
    if fb[0] == "id":
        target_url = (f"https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
                      f"&country=ALL&search_type=page&view_all_page_id={fb[1]}")
        lib_url = target_url
    else:                                   # slug
        target_url = f"https://www.facebook.com/{fb[1]}"
        lib_url = _library_url(fb[1])
    try:
        ads_list, page_name0 = _fetch_ads(target_url, config.META_ADS_LIMIT)
    except Exception as e:
        return {"checked": False, "note": f"помилка Apify: {str(e)[:140]}", "link": lib_url}
    by_keyword = False

    if debug:
        return {"checked": True, "count": len(ads_list), "target_url": target_url,
                "by_keyword": by_keyword,
                "raw_sample": (ads_list[0] if ads_list else None)}

    brand = page or _host(domain).split(".")[0]
    if not ads_list:
        return {"checked": True, "running": False, "count": 0, "page": page_name0 or brand,
                "platforms": {}, "creatives": [], "by_keyword": by_keyword, "link": lib_url}

    plat = {"facebook": 0, "instagram": 0, "messenger": 0, "audience_network": 0}
    creatives, page_name = [], (page_name0 or page)
    for it in ads_list:
        page_name = page_name or it.get("page_name") or it.get("pageName") or _snap(it).get("page_name")
        seen = set()
        for p in _platforms_of(it):
            key = _PLAT_MAP.get(p)
            if key and key not in seen:
                plat[key] += 1
                seen.add(key)
        imgs = _images_of(it)
        sn = _snap(it)
        body = sn.get("body")
        text = body.get("text", "") if isinstance(body, dict) else (body if isinstance(body, str) else "")
        text = (text or sn.get("title") or sn.get("caption") or "").strip()
        fmt = (sn.get("displayFormat") or sn.get("display_format") or "").lower()
        cta = (sn.get("ctaText") or sn.get("cta_text") or "").strip()
        start = (it.get("startDateFormatted") or it.get("start_date_formatted") or "")[:10]
        versions = it.get("collationCount") or it.get("collation_count") or 1
        # пряме відео (mp4) для відтворення в звіті, якщо є
        video_url = ""
        for v in (sn.get("videos") or []):
            if isinstance(v, dict):
                video_url = (v.get("videoSdUrl") or v.get("videoHdUrl")
                             or v.get("video_sd_url") or v.get("video_hd_url") or "").strip()
                if video_url:
                    break
        if (imgs or text or video_url) and len(creatives) < 15:
            ad_id = it.get("adArchiveID") or it.get("adArchiveId") or it.get("ad_archive_id")
            crea_link = (f"https://www.facebook.com/ads/library/?id={ad_id}" if ad_id
                         else (it.get("ad_snapshot_url") or lib_url))
            creatives.append({
                "image": imgs[0] if imgs else "",
                "video": video_url,
                "text": text[:280],
                "cta": cta,
                "format": fmt,
                "platforms": [k for k in seen],
                "start": start,
                "versions": int(versions) if str(versions).isdigit() else 1,
                "link": crea_link,
            })
    return {
        "checked": True,
        "running": True,
        "count": len(ads_list),
        "page": page_name or brand,
        "platforms": plat,
        "creatives": creatives,
        "by_keyword": by_keyword,
        "link": lib_url,
    }
