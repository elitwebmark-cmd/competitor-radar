"""Конфіг Competitor Radar — моніторинг рекламної активності конкурентів.
Усі значення читаються з env (Railway Variables). Дефолти — робочі для UA."""
import os

# --- Авторизація дашборда ---
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-competitor-radar")
APP_LOGIN = os.getenv("APP_LOGIN", "elitweb")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")          # ОБОВʼЯЗКОВО задати в Railway

# --- Список конкурентів (через кому в env COMPETITORS, або дефолт нижче) ---
_DEFAULT_COMPETITORS = [
    "web-promo.ua", "elit-web.ua", "ideadigital.agency", "sprava.ua",
    "seo.ua", "seok.ua", "lanet.click", "itforce.ua", "voll.com.ua",
    "locomotive.ua", "netpeak.ua", "aweb.ua", "seomarket.ua",
    "comon.agency", "inweb.ua", "turboweb.com.ua",
]
COMPETITORS = [d.strip() for d in os.getenv("COMPETITORS", "").split(",") if d.strip()] \
    or _DEFAULT_COMPETITORS

# --- Сховище знімків (для історії/дельт). На Railway підключити Volume сюди. ---
DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))

# --- Сканування ---
SCAN_WORKERS = int(os.getenv("SCAN_WORKERS", "4"))       # паралельні домени (Apify повільний)
# Добовий авто-скан ВИМКНЕНО за замовчуванням — лише ручний «Оновити зараз».
# Увімкнути: SCAN_AUTO=1
SCAN_AUTO_ENABLED = os.getenv("SCAN_AUTO", "0") not in ("0", "false", "False", "")
SCAN_HOUR_UTC = int(os.getenv("SCAN_HOUR_UTC", "5"))     # година доб. авто-скану (UTC); 5 ≈ 07-08 Київ
SCAN_MIN_INTERVAL_H = int(os.getenv("SCAN_MIN_INTERVAL_H", "6"))  # не частіше, ніж раз на N год

# --- Google Ads Transparency (через SerpApi) ---
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
ADS_REGION = os.getenv("ADS_REGION", "2804")             # 2804 = Україна
ADS_TIMEOUT = int(os.getenv("ADS_TIMEOUT", "25"))

# --- Meta Ad Library (через Apify) ---
APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
APIFY_META_ACTOR = os.getenv("APIFY_META_ACTOR", "apify~facebook-ads-scraper")
APIFY_META_INPUT = os.getenv(
    "APIFY_META_INPUT",
    '{"startUrls":[{"url":"{url}"}],"count":{count},"activeStatus":"active"}')
APIFY_TIMEOUT = int(os.getenv("APIFY_TIMEOUT", "150"))
META_ADS_LIMIT = int(os.getenv("META_ADS_LIMIT", "30"))
META_KEYWORD_LIMIT = int(os.getenv("META_KEYWORD_LIMIT", "10"))
META_ADS_COUNTRY = os.getenv("META_ADS_COUNTRY", "ALL")

# --- AI-аналітика (Claude, з читанням скріншотів Google-оголошень зором) ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "claude-3-5-sonnet-20241022")  # vision; є автофолбек на інші
AI_MAX_IMAGES = int(os.getenv("AI_MAX_IMAGES", "6"))           # скільки Google-скрінів дивитись
AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "3000"))
AI_TIMEOUT = int(os.getenv("AI_TIMEOUT", "90"))

# --- HTTP ---
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "12"))
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
)
ACCEPT_LANGUAGE = os.getenv("ACCEPT_LANGUAGE", "uk-UA,uk;q=0.9,ru;q=0.8,en;q=0.7")
