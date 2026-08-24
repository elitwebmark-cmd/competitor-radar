"""AI-аналітика маркетингу конкурентів (Claude).
Читає скріншоти Google-оголошень ЗОРОМ (без окремого OCR) + Meta-тексти і
формує структурований розбір: послуги, напрямки, оффери, УТП, меседжі, канали.
Плюс ринковий огляд по всіх конкурентах."""
from __future__ import annotations
import json
import base64
import logging

import requests
import config

log = logging.getLogger("radar.ai")

_API = "https://api.anthropic.com/v1/messages"
_ALLOWED_IMG = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def enabled() -> bool:
    return bool(config.ANTHROPIC_API_KEY)


# --------------------------- низькорівневе ---------------------------------
def _download_image(url: str):
    """Повертає (media_type, base64) або None."""
    if not url or not url.startswith("http"):
        return None
    try:
        r = requests.get(url, timeout=config.HTTP_TIMEOUT,
                         headers={"User-Agent": config.USER_AGENT})
        if r.status_code != 200:
            return None
        ct = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ct not in _ALLOWED_IMG:
            return None
        if len(r.content) > 3_500_000:            # ~3.5MB кеп
            return None
        return ct, base64.standard_b64encode(r.content).decode("ascii")
    except Exception:
        return None


# Кандидати моделей (усі з підтримкою vision). Перебираємо, поки одна не спрацює —
# щоб не залежати від того, які саме назви доступні конкретному акаунту.
_FALLBACK = [
    "claude-sonnet-4-20250514",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-sonnet-20240620",
    "claude-3-haiku-20240307",
]
_WORKING_MODEL = None          # запамʼятовуємо першу робочу
_API_MODELS = None             # кеш списку доступних акаунту моделей (/v1/models)


def list_models() -> list:
    """Реальний перелік моделей, доступних акаунту (Anthropic /v1/models)."""
    global _API_MODELS
    if _API_MODELS is not None:
        return _API_MODELS
    _API_MODELS = []
    try:
        r = requests.get("https://api.anthropic.com/v1/models",
                         headers={"x-api-key": config.ANTHROPIC_API_KEY,
                                  "anthropic-version": "2023-06-01"}, timeout=20)
        if r.status_code == 200:
            ids = [m.get("id") for m in (r.json().get("data") or []) if m.get("id")]
            # пріоритет: sonnet → opus → haiku (усі сучасні підтримують vision)
            ids.sort(key=lambda x: (0 if "sonnet" in x else 1 if "opus" in x else 2), reverse=False)
            _API_MODELS = ids
    except Exception:
        pass
    return _API_MODELS


def _model_candidates():
    order = []
    for m in ([_WORKING_MODEL, config.AI_MODEL] + list_models() + _FALLBACK):
        if m and m not in order:
            order.append(m)
    return order


def _call(system: str, content: list, prefill: str = "") -> str:
    """prefill — префікс відповіді асистента (напр. '{'), щоб змусити чистий JSON."""
    global _WORKING_MODEL
    headers = {"x-api-key": config.ANTHROPIC_API_KEY,
               "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    last = ""
    for model in _model_candidates():
        # для кожної моделі: спершу з prefill, і якщо вона його не підтримує (400) — без нього
        for pf in ([prefill, ""] if prefill else [""]):
            msgs = [{"role": "user", "content": content}]
            if pf:
                msgs.append({"role": "assistant", "content": pf})
            body = {"model": model, "max_tokens": config.AI_MAX_TOKENS,
                    "system": system, "messages": msgs}
            r = requests.post(_API, headers=headers, json=body, timeout=config.AI_TIMEOUT)
            if r.status_code == 200:
                _WORKING_MODEL = model
                parts = r.json().get("content") or []
                txt = "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()
                return (pf + txt) if pf else txt
            if r.status_code == 404 and "not_found" in r.text:
                last = f"{model}"
                break                     # ця назва недоступна → наступна модель
            if r.status_code == 400 and "prefill" in r.text.lower() and pf:
                continue                  # модель не підтримує prefill → повтор без нього
            raise RuntimeError(f"Anthropic {r.status_code}: {r.text[:200]}")
    avail = ", ".join(list_models()) or "(список порожній / недоступний)"
    raise RuntimeError(f"жодна модель не підійшла. Доступні акаунту: {avail}")


def _parse_json(text: str) -> dict:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.lstrip().lower().startswith("json"):
            t = t.split("\n", 1)[1] if "\n" in t else t
    a, b = t.find("{"), t.rfind("}")
    if a >= 0 and b > a:
        t = t[a:b + 1]
    try:
        return json.loads(t)
    except Exception:
        return {}


# --------------------------- аналіз одного ---------------------------------
_SYS_ONE = (
    "Ти — маркетинговий аналітик діджитал-агенції. Аналізуєш рекламу конкурента "
    "(скріншоти оголошень Google та тексти оголошень Meta). Спочатку прочитай "
    "текст зі скріншотів, потім зроби висновки. Відповідай ВИКЛЮЧНО валідним JSON "
    "українською, без пояснень поза JSON.")

_SCHEMA_ONE = (
    'Поверни JSON рівно з такими ключами:\n'
    '{\n'
    '  "services": ["перелік послуг, які рекламує (SEO, SMM, таргет, контекст, розробка, ...)"],\n'
    '  "directions": ["напрямки/ніші/сегменти, на які орієнтується"],\n'
    '  "offers": ["конкретні оффери, акції, ліди-магніти, ціни, які згадуються"],\n'
    '  "utp": "1-2 речення: у чому їхнє позиціонування / УТП",\n'
    '  "messaging": ["ключові меседжі / болі / вигоди, на які тиснуть"],\n'
    '  "landing_focus": ["які послуги/сторінки просувають найактивніше"],\n'
    '  "channels": "де активні і на що акцент (Google vs Meta)",\n'
    '  "summary": "2-3 речення підсумку про маркетинг конкурента"\n'
    '}')


def analyze_competitor(domain: str, rec: dict) -> dict:
    if not enabled():
        return {"error": "ANTHROPIC_API_KEY не заданий"}
    g = (rec or {}).get("google") or {}
    m = (rec or {}).get("meta") or {}

    ctx = [f"Конкурент: {domain}"]
    ctx.append(f"Google Ads: {'крутить' if g.get('running') else 'не крутить'}, "
               f"~{g.get('count', 0)} оголошень, платформи: {g.get('platforms') or {}}.")
    gtexts = [c.get("text") for c in (g.get("creatives") or []) if c.get("text")][:12]
    if gtexts:
        ctx.append("Тексти/заголовки Google-оголошень: " + " | ".join(gtexts))
    ctx.append(f"Meta (FB/IG): {'крутить' if m.get('running') else 'не крутить'}, "
               f"~{m.get('count', 0)} крео, сторінка: {m.get('page') or '—'}.")
    mtexts = [c.get("text") for c in (m.get("creatives") or []) if c.get("text")][:12]
    if mtexts:
        ctx.append("Тексти Meta-оголошень: " + " | ".join(mtexts))

    content = [{"type": "text", "text": "\n".join(ctx)}]

    # скріншоти Google-оголошень — читаємо зором
    imgs = 0
    for c in (g.get("creatives") or []):
        if imgs >= config.AI_MAX_IMAGES:
            break
        got = _download_image(c.get("image"))
        if not got:
            continue
        mt, b64 = got
        content.append({"type": "image",
                        "source": {"type": "base64", "media_type": mt, "data": b64}})
        imgs += 1
    if imgs:
        content.append({"type": "text",
                        "text": f"Вище — {imgs} скріншот(и) Google-оголошень цього конкурента. "
                                "Прочитай з них текст і врахуй у розборі."})

    content.append({"type": "text", "text": _SCHEMA_ONE})
    try:
        raw = _call(_SYS_ONE, content, prefill="{")
    except Exception as e:
        log.exception("analyze_competitor %s", domain)
        return {"error": str(e)[:200]}
    out = _parse_json(raw)
    if not out:
        return {"error": "не вдалося розібрати відповідь AI: " + ((raw or "порожньо")[:200])}
    out["_images_read"] = imgs
    return out


# --------------------------- ринковий огляд --------------------------------
_SYS_MKT = (
    "Ти — стратег діджитал-агенції elit-web. На основі коротких розборів реклами "
    "конкурентів зроби ринковий огляд. Відповідай ВИКЛЮЧНО валідним JSON українською.")

_SCHEMA_MKT = (
    'Поверни JSON рівно з ключами:\n'
    '{\n'
    '  "leaders": ["хто найагресивніше рекламується і в чому"],\n'
    '  "common_offers": ["оффери/меседжі, які повторюються в багатьох"],\n'
    '  "channels": "загальна картина: хто де (Google/Meta) і які акценти",\n'
    '  "gaps": ["ніші/меседжі/оффери, які майже ніхто не займає — можливості для нас"],\n'
    '  "recommendations": ["2-4 практичні поради elit-web по позиціонуванню/офферах"],\n'
    '  "summary": "3-4 речення загального висновку по ринку"\n'
    '}')


def analyze_market(items: list) -> dict:
    """items: [{"domain":..., "ai":{...аналіз...}}] — уже проаналізовані конкуренти."""
    if not enabled():
        return {"error": "ANTHROPIC_API_KEY не заданий"}
    lines = []
    for it in items:
        a = it.get("ai") or {}
        if not a or a.get("error"):
            continue
        lines.append(
            f"- {it['domain']}: послуги={a.get('services')}; напрямки={a.get('directions')}; "
            f"оффери={a.get('offers')}; УТП={a.get('utp')}; канали={a.get('channels')}")
    if not lines:
        return {"error": "немає проаналізованих конкурентів (спершу зроби AI-аналіз кількох)"}
    content = [{"type": "text",
                "text": "Розбори реклами конкурентів:\n" + "\n".join(lines) + "\n\n" + _SCHEMA_MKT}]
    try:
        raw = _call(_SYS_MKT, content, prefill="{")
    except Exception as e:
        log.exception("analyze_market")
        return {"error": str(e)[:200]}
    out = _parse_json(raw)
    return out or {"error": "не вдалося розібрати відповідь AI: " + ((raw or "порожньо")[:200])}
