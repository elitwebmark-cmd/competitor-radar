"""Competitor Radar — веб-дашборд моніторингу реклами конкурентів (Google + Meta)."""
import time
import logging
import functools
import threading

from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, abort)

import config
import store
import scanner
import ai

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("radar.web")

app = Flask(__name__, template_folder="templates")
app.secret_key = config.SECRET_KEY


# ----------------------------- авторизація --------------------------------
def login_required(f):
    @functools.wraps(f)
    def wrap(*a, **kw):
        if not session.get("auth"):
            return redirect(url_for("login", next=request.path))
        return f(*a, **kw)
    return wrap


@app.route("/login", methods=["GET", "POST"])
def login():
    err = ""
    if request.method == "POST":
        lg = (request.form.get("login") or "").strip()
        pw = request.form.get("password") or ""
        if config.APP_PASSWORD and lg == config.APP_LOGIN and pw == config.APP_PASSWORD:
            session["auth"] = True
            return redirect(request.args.get("next") or url_for("dashboard"))
        err = "Невірний логін або пароль"
    return render_template("login.html", err=err)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ----------------------------- дашборд ------------------------------------
@app.route("/")
@login_required
def index():
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
@login_required
def dashboard():
    cur = store.latest()
    prev = store.previous()
    data = store.compute_deltas(cur, prev) if cur else None
    snaps = store.list_snapshots()
    return render_template("dashboard.html", data=data, cur=cur,
                           status=scanner.get_status(), snaps=snaps[:14],
                           competitors=config.COMPETITORS, ai_on=ai.enabled(),
                           market=(cur.get("ai_market") if cur else None))


@app.route("/competitor/<path:domain>")
@login_required
def competitor(domain):
    cur = store.latest()
    rec = (cur.get("domains") or {}).get(domain) if cur else None
    if not rec:
        abort(404)
    return render_template("competitor.html", domain=domain, rec=rec,
                           snap_date=(cur.get("date") if cur else ""),
                           ai_on=ai.enabled())


@app.route("/competitor/<path:domain>/ai", methods=["POST"])
@login_required
def competitor_ai(domain):
    """AI-розбір маркетингу одного конкурента (кешується у знімок)."""
    cur = store.latest()
    doms = (cur or {}).get("domains") or {}
    rec = doms.get(domain)
    if not rec:
        return jsonify({"ok": False, "error": "конкурента немає в останньому зрізі"}), 404
    res = ai.analyze_competitor(domain, rec)
    rec["ai"] = res
    rec["ai_ts"] = int(time.time())
    store.save_snapshot(cur)                      # перезапис того ж файлу (той самий ts)
    return jsonify({"ok": not res.get("error"), "ai": res})


@app.route("/ai-market", methods=["POST"])
@login_required
def ai_market():
    """Ринковий огляд по вже проаналізованих конкурентах (кешується у знімок)."""
    cur = store.latest()
    doms = (cur or {}).get("domains") or {}
    if not doms:
        return jsonify({"ok": False, "error": "немає зрізу"}), 400
    items = [{"domain": d, "ai": (r.get("ai") or {})} for d, r in doms.items()]
    res = ai.analyze_market(items)
    cur["ai_market"] = res
    cur["ai_market_ts"] = int(time.time())
    store.save_snapshot(cur)
    return jsonify({"ok": not res.get("error"), "market": res})


# ----------------------------- сканування ---------------------------------
@app.route("/refresh", methods=["POST"])
@login_required
def refresh():
    started = scanner.scan_async()
    return jsonify({"ok": True, "started": started, "status": scanner.get_status()})


@app.route("/full-refresh", methods=["POST"])
@login_required
def full_refresh():
    """Повне оновлення: реклама + AI-розбір усіх + ринковий огляд (у фоні)."""
    started = scanner.full_refresh_async()
    return jsonify({"ok": True, "started": started, "status": scanner.get_status()})


@app.route("/status")
@login_required
def status():
    st = scanner.get_status()
    snaps = store.list_snapshots()
    st["last_snapshot"] = snaps[0]["date"] if snaps else None
    st["last_snapshot_ts"] = snaps[0]["ts"] if snaps else None
    return jsonify(st)


@app.route("/ai-models")
@login_required
def ai_models():
    return jsonify({"enabled": ai.enabled(), "available": ai.list_models(),
                    "default": config.AI_MODEL})


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})


# ----------------------- добовий авто-скан (планувальник) -----------------
_SCHED_STARTED = False
_SCHED_LOCK = threading.Lock()


def _last_scan_ts():
    snaps = store.list_snapshots()
    return snaps[0]["ts"] if snaps else 0


def _scheduler_loop():
    while True:
        try:
            now = time.gmtime()
            last = _last_scan_ts()
            age_h = (time.time() - last) / 3600 if last else 1e9
            # запускати раз на добу у вказану годину UTC, якщо давно не сканували
            if (now.tm_hour == config.SCAN_HOUR_UTC
                    and age_h >= config.SCAN_MIN_INTERVAL_H
                    and not scanner.get_status().get("running")):
                log.info("scheduler: добовий авто-скан")
                scanner.scan_all()
        except Exception:
            log.exception("scheduler tick failed")
        time.sleep(300)          # перевірка кожні 5 хв


def _start_scheduler():
    global _SCHED_STARTED
    if _SCHED_STARTED:
        return
    if not config.SCAN_AUTO_ENABLED:
        log.info("scheduler: добовий авто-скан вимкнено (лише ручний). Увімкнути: SCAN_AUTO=1")
        return
    with _SCHED_LOCK:
        if _SCHED_STARTED:
            return
        threading.Thread(target=_scheduler_loop, name="radar-scheduler", daemon=True).start()
        _SCHED_STARTED = True
        log.info("scheduler запущено (година UTC=%s)", config.SCAN_HOUR_UTC)


_start_scheduler()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
