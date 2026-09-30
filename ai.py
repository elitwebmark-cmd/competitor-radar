"""AI-аналітика маркетингу конкурентів (Claude).
Читає скріншоти Google-оголошень ЗОРОМ (без окремого OCR) + Meta-тексти і
формує структурований розбір: послуги, напрямки, оффери, УТП, меседжі, канали.
Плюс ринковий огляд по всіх конкурентах."""
from __future__ import annotations
import json
import re
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


def _call(system: str, content: list, prefill: str = "", max_tokens: int = None) -> str:
    """prefill — префікс відповіді асистента (напр. '{'), щоб змусити чистий JSON."""
    global _WORKING_MODEL
    headers = {"x-api-key": config.ANTHROPIC_API_KEY,
               "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    mt = int(max_tokens or config.AI_MAX_TOKENS)
    last = ""
    for model in _model_candidates():
        # для кожної моделі: спершу з prefill, і якщо вона його не підтримує (400) — без нього
        for pf in ([prefill, ""] if prefill else [""]):
            msgs = [{"role": "user", "content": content}]
            if pf:
                msgs.append({"role": "assistant", "content": pf})
            body = {"model": model, "max_tokens": mt,
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
    # прибрати code-fence (```json ... ``` або незакритий ```json на початку)
    if "```" in t:
        m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
        t = (m.group(1) if m else t.replace("```json", "").replace("```", "")).strip()
    a = t.find("{")
    if a < 0:
        return {}
    t = t[a:]
    # 1) як є
    try:
        return json.loads(t)
    except Exception:
        pass
    # 2) до останньої '}'
    b = t.rfind("}")
    if b > 0:
        try:
            return json.loads(t[:b + 1])
        except Exception:
            pass
    # 3) рятуємо обрізаний JSON: обрізаємо до останньої «безпечної» точки
    #    (закрита структура }] або кома між елементами поза рядком) і добалансовуємо дужки.
    def _balance(frag: str) -> str:
        st, ins, es = [], False, False
        for ch in frag:
            if es:
                es = False
                continue
            if ch == "\\" and ins:
                es = True
                continue
            if ch == '"':
                ins = not ins
                continue
            if ins:
                continue
            if ch in "{[":
                st.append("}" if ch == "{" else "]")
            elif ch in "}]":
                if st:
                    st.pop()
        if ins:
            frag += '"'
        return frag + "".join(reversed(st))

    instr, esc, safe = False, False, -1
    for i, ch in enumerate(t):
        if esc:
            esc = False
            continue
        if ch == "\\" and instr:
            esc = True
            continue
        if ch == '"':
            instr = not instr
            continue
        if instr:
            continue
        if ch in "}]":
            safe = i + 1          # після закритої структури — безпечно
        elif ch == ",":
            safe = i              # перед комою — безпечно (відкидаємо неповний елемент)
    if safe > 0:
        try:
            return json.loads(_balance(t[:safe].rstrip().rstrip(",")))
        except Exception:
            pass
    return {}


# --------------------------- аналіз одного ---------------------------------
_SYS_ONE = (
    "Ти — маркетинговий аналітик діджитал-агенції. Аналізуєш рекламу конкурента "
    "(скріншоти оголошень Google та тексти оголошень Meta). Спочатку прочитай "
    "текст зі скріншотів, потім зроби висновки. Відповідай ВИКЛЮЧНО валідним JSON "
    "українською, без пояснень поза JSON.")

_SCHEMA_ONE = (
    'Розбери рекламу конкурента ДЕТАЛЬНО, «по молекулах». Поверни JSON рівно з такими ключами:\n'
    '{\n'
    '  "positioning": "1-2 речення: як конкурент себе позиціонує на ринку",\n'
    '  "services": ["послуги, які рекламує (SEO, контекст, таргет, SMM, розробка, ...)"],\n'
    '  "target_segments": ["на кого таргетують: ніші, тип бізнесу, розмір, гео"],\n'
    '  "offers": ["конкретні оффери/акції/ціни/гарантії/ліди-магніти дослівно, як у рекламі"],\n'
    '  "usp": ["унікальні переваги, які підкреслюють"],\n'
    '  "messaging_angles": ["кути/болі/вигоди, на які тиснуть у меседжах"],\n'
    '  "tone": "емоційний / раціональний / змішаний — коротко чому",\n'
    '  "ctas": ["заклики до дії, які використовують"],\n'
    '  "ad_formats": "які формати реклами використовує і на що акцент: пошукові текстові, '
    'банери (статика), відео, каруселі — окремо по Google і по Meta",\n'
    '  "format_focus": "головний акцент по формату (статичні банери / відео / пошук / змішано)",\n'
    '  "creative_style": "візуальний стиль і подача креативів (кольори, тон, типаж)",\n'
    '  "activity_level": "агресивний / помірний / слабкий — з коротким обґрунтуванням за обсягом",\n'
    '  "stands_out": "чим саме виділяється (або не виділяється) серед конкурентів",\n'
    '  "weak_spots": ["слабкі місця / прогалини в їхній рекламі"],\n'
    '  "summary": "3-4 речення підсумку про рекламний маркетинг конкурента"\n'
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


# --------------------------- ПОВНИЙ ринковий звіт --------------------------
_SYS_REPORT = (
    "Ти — головний стратег-аналітик діджитал-агенції elit-web. На основі детальних "
    "розборів реклами конкурентів і твердих цифр по ринку склади ГЛИБОКИЙ, повний "
    "аналітичний звіт по ринку реклами діджитал-агенцій України. Пиши українською, "
    "змістовно, з конкретикою й цифрами з наданих даних, як senior-аналітик для "
    "керівництва. Відповідай ВИКЛЮЧНО валідним JSON.")

_SCHEMA_REPORT = (
    'Поверни ГЛИБОКИЙ, розгорнутий JSON рівно з такими ключами. Пиши як senior-аналітик, '
    'багато конкретики й цифр, довгі змістовні тексти (не одне речення):\n'
    '{\n'
    '  "executive_summary": "8-12 речень: повний стан ринку реклами агенцій — активність, '
    'інтенсивність, хто лідирує й чому, ключові тренди, головні висновки для elit-web",\n'
    '  "market_dynamics": "4-6 речень: динаміка й інтенсивність реклами, розподіл Google/Meta, що це означає",\n'
    '  "intensity_analysis": "4-6 речень: хто рекламується найінтенсивніше, рівні активності '
    '(лідери / середняки / пасивні), сигнали бюджетів",\n'
    '  "channel_strategy": "4-6 речень: як ринок ділить Google vs Meta — хто на що ставить і чому, '
    'де недокрут",\n'
    '  "segments_targeting": "4-6 речень: на які сегменти/ніші/типи клієнтів таргетує ринок, '
    'хто які сегменти зайняв, де конкуренція за аудиторію найвища",\n'
    '  "positioning_map": ["кластери позиціонування ринку: напр. \'перформанс з фокусом на ROI: X, Y\'; '
    '\'SEO за результат: Z\'; \'комплексний діджитал: ...\' — згрупуй гравців за типом позиціонування"],\n'
    '  "offers_landscape": {"common":["оффери/меседжі, що повторюються в багатьох — з поясненням"],'
    '"unique":["оффери, які має лише хтось один — цікаві ходи, з поясненням"],'
    '"pricing":"2-4 речення: що видно про ціни / моделі оплати / гарантії на ринку"},\n'
    '  "messaging_themes": [{"theme":"меседж/кут","detail":"хто використовує і чому це працює"}],\n'
    '  "formats_landscape": "5-7 речень: як ринок використовує формати — відео vs статичні банери vs '
    'пошук vs каруселі, хто на чому сильний, де ринок недокручує, які висновки",\n'
    '  "differentiation": ["хто і чим реально виділяється на тлі решти — розгорнуто по кожному помітному"],\n'
    '  "white_space": [{"title":"вільна ніша/оффер/меседж/формат","detail":"чому це можливість і як elit-web її зайняти"}],\n'
    '  "threats": [{"who":"агресивний гравець","detail":"у чому загроза і як реагувати"}],\n'
    '  "recommendations": [{"title":"дія для elit-web","detail":"що саме робити, конкретно",'
    '"effect":"очікуваний ефект","priority":"висока/середня"}],\n'
    '  "our_position": "4-6 речень: де САМЕ ми (elit-web) на тлі ринку — у чому сильні, '
    'у чому відстаємо, які наші РК-метрики (якщо надані) кажуть про ефективність",\n'
    '  "october_plan": [{"hypothesis":"гіпотеза (напр. відео-кейси піднімуть CTR)",'
    '"test":"конкретний тест/дія на жовтень","channel":"Google/Meta/обидва",'
    '"metric":"яку метрику дивимось (CTR, CPL, conv...)","expected":"очікуваний результат",'
    '"priority":"висока/середня"}],\n'
    '  "battle_verdict": "4-6 речень: як саме elit-web перевершить конкурентів у жовтні — '
    'головний фокус місяця й чому це спрацює"\n'
    '}\n'
    'У messaging_themes 4-6, у white_space 4-6, у recommendations 5-8, у october_plan 6-9 пунктів. '
    'october_plan — конкретні гіпотези й тести саме на ЖОВТЕНЬ, щоб обійти конкурентів. '
    'Використовуй надані цифри по ринку та наші РК-метрики. Поверни ЛИШЕ JSON без markdown-обгортки.')


def market_report(items: list, agg: dict = None, our_domain: str = None,
                  our_kpis: dict = None) -> dict:
    """Повний аналітичний звіт по ринку + план на жовтень для elit-web.
    items: [{"domain","ai":{...розбір...}}]; agg: тверді агрегати; our_domain: наш
    сайт (elit-web); our_kpis: наші реальні РК-метрики з Windsor (spend/CTR/conv)."""
    if not enabled():
        return {"error": "ANTHROPIC_API_KEY не заданий"}
    lines = []
    for it in items:
        a = it.get("ai") or {}
        if not a or a.get("error"):
            continue
        lines.append(
            f"### {it['domain']}\n"
            f"позиціонування: {a.get('positioning')}\n"
            f"послуги: {a.get('services')}\n"
            f"сегменти: {a.get('target_segments')}\n"
            f"оффери: {a.get('offers')}\n"
            f"УТП: {a.get('usp')}\n"
            f"меседжі: {a.get('messaging_angles')}\n"
            f"формати: {a.get('ad_formats')} (акцент: {a.get('format_focus')})\n"
            f"активність: {a.get('activity_level')}\n"
            f"виділяється: {a.get('stands_out')}")
    if not lines:
        return {"error": "немає проаналізованих конкурентів (спершу зроби AI-розбір)"}
    ctx = "ДЕТАЛЬНІ РОЗБОРИ КОНКУРЕНТІВ:\n\n" + "\n\n".join(lines)
    if agg:
        fs = agg.get("format_shares") or {}
        ctx += (
            "\n\nТВЕРДІ ЦИФРИ ПО РИНКУ:\n"
            f"- конкурентів: {agg.get('n')}; активні в Google: {agg.get('active_google')}, "
            f"в Meta: {agg.get('active_meta')}\n"
            f"- усього оголошень у вибірці: {agg.get('total_ads')} "
            f"(Google {agg.get('g_total')}, Meta {agg.get('m_total')}; "
            f"розподіл Google/Meta ~ {agg.get('channel_split',{}).get('google')}%/"
            f"{agg.get('channel_split',{}).get('meta')}%)\n"
            f"- розподіл форматів: статичні банери {fs.get('image')}%, відео {fs.get('video')}%, "
            f"текст/пошук {fs.get('text')}%, інше {fs.get('other')}%\n"
            f"- гравців, що крутять відео: {agg.get('video_share_players')}% "
            f"({', '.join(agg.get('video_advertisers') or []) or '—'})\n"
            f"- топ за обсягом: " + ", ".join(
                f"{p['domain']} ({p['total']})" for p in (agg.get('top') or [])))
    if our_domain:
        ctx += (f"\n\nНАШ САЙТ (це МИ, для кого план): {our_domain}. "
                "Усі рекомендації, our_position, october_plan і battle_verdict — саме для нас, "
                "щоб обійти інших гравців.")
    if our_kpis:
        g = our_kpis.get("google") or {}
        m = our_kpis.get("meta") or {}
        ctx += (
            f"\n\nНАШІ РЕАЛЬНІ РК-МЕТРИКИ ({our_kpis.get('period','30д')}, з Windsor.ai):\n"
            f"- Google Ads: витрати {g.get('spend')}, кліки {g.get('clicks')}, покази {g.get('impressions')}, "
            f"CTR {g.get('ctr')}%, CPC {g.get('cpc')}, конверсії {g.get('conversions')}, "
            f"conv-rate {g.get('conv_rate')}%, CPA {g.get('cpa')}\n"
            f"- Meta: витрати {m.get('spend')}, кліки {m.get('clicks')}, покази {m.get('impressions')}, "
            f"CTR {m.get('ctr')}%, CPC {m.get('cpc')}\n"
            "Спирайся на ці цифри в our_position і october_plan (де недокрут, що тестувати).")
    content = [{"type": "text", "text": ctx + "\n\n" + _SCHEMA_REPORT}]
    try:
        raw = _call(_SYS_REPORT, content, prefill="{", max_tokens=max(config.AI_MAX_TOKENS, 8000))
    except Exception as e:
        log.exception("market_report")
        return {"error": str(e)[:200]}
    out = _parse_json(raw)
    return out or {"error": "не вдалося розібрати відповідь AI: " + ((raw or "порожньо")[:200])}
