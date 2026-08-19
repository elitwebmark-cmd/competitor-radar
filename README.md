# Competitor Radar

Онлайн-дашборд моніторингу **рекламної активності конкурентів** — Google Ads
(Transparency) і Meta (Facebook/Instagram Ad Library). Показує, хто зараз крутить
рекламу, скільки, які формати й платформи, і **що змінилося з попереднього зрізу**
(хто почав, хто зупинив, хто наростив/зменшив). По кожному конкуренту — галерея
реальних оголошень (картинки/відео/тексти/CTA/лендинги).

Побудований на тому ж двигуні, що й elit-web SEO Qualifier: модулі `ads.py`
(Google Ads Transparency через SerpApi) і `meta_ads.py` (Meta Ad Library через Apify).

## Як працює

- **Щоденний авто-скан** — вбудований планувальник раз на добу (година `SCAN_HOUR_UTC`)
  сканує всіх конкурентів і зберігає зріз.
- **Оновити зараз** — кнопка на дашборді запускає скан у фоні (прогрес наживо).
- **Історія й дельти** — кожен скан зберігається у `DATA_DIR`; дашборд порівнює
  два останні зрізи.

## Запуск на Railway

1. Створи **новий GitHub-репозиторій** і залий туди цю папку.
2. На Railway → **New Project → Deploy from GitHub repo** → обери цей репозиторій
   (збереться з `Dockerfile`).
3. Додай **Variables**:

| Змінна | Обовʼязково | Опис |
|---|---|---|
| `APP_PASSWORD` | так | пароль до дашборда |
| `SECRET_KEY` | так | будь-який довгий рядок (сесії) |
| `SERPAPI_KEY` | так | ключ SerpApi (Google Ads Transparency) |
| `APIFY_TOKEN` | так | токен Apify (Meta Ad Library) |
| `APP_LOGIN` | ні | логін (дефолт `elitweb`) |
| `COMPETITORS` | ні | список доменів через кому (інакше — вбудований) |
| `DATA_DIR` | ні | шлях до сховища зрізів (дефолт `./data`) |
| `SCAN_HOUR_UTC` | ні | година доб. авто-скану, UTC (дефолт `5`) |
| `SCAN_WORKERS` | ні | паралельні домени (дефолт `4`) |

4. **Історія зрізів:** щоб «хто почав/перестав» переживало редеплої, підключи
   **Railway Volume** і змонтуй його, напр. у `/data`, а тоді постав `DATA_DIR=/data`.
   Без тому дані скидатимуться при кожному деплої (дельти зʼявляться після ≥2 сканів).

5. Відкрий домен сервіса → увійди → натисни **«Оновити зараз»** для першого зрізу.

## Локально

```bash
pip install -r requirements.txt
export APP_PASSWORD=... SERPAPI_KEY=... APIFY_TOKEN=... SECRET_KEY=dev
python app.py   # http://localhost:8080
```

## Список конкурентів за замовчуванням

web-promo.ua, elit-web.ua, ideadigital.agency, sprava.ua, seo.ua, seok.ua,
lanet.click, itforce.ua, voll.com.ua, locomotive.ua, netpeak.ua, aweb.ua,
seomarket.ua, comon.agency, inweb.ua, turboweb.com.ua

Змінюється через `COMPETITORS` (env) без правок коду.
