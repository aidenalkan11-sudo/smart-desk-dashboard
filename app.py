import json, logging, os, threading, time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
from flask import Flask, jsonify
from dateutil import parser as dtparser
from dateutil import rrule

try:
    import icalendar
except ImportError:
    icalendar = None

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
except ImportError:
    spotipy = None
    SpotifyOAuth = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
TOKEN_PATH = os.path.join(BASE_DIR, "spotify_token.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("smartdesk")

DEFAULT_CONFIG = {
    "canvas_token": "",
    "canvas_base_url": "https://mgs.instructure.com",
    "outlook_ics_url": "",
    "spotify_client_id": "",
    "spotify_client_secret": "",
    "spotify_redirect_uri": "http://localhost:8888/callback",
    "finnhub_api_key": "",
    "newsapi_key": "",
    "weather_lat": -37.814,
    "weather_lon": 144.9633,
    "weather_timezone": "Australia/Melbourne",
    "squiggle_user_agent": "smart-desk-dashboard",
    "favourite_afl_team": "brisbane",
    "gpio_button_pin": 17,
}


def load_config():
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            log.exception("Could not read config.json; using defaults")
    return cfg


CONFIG = load_config()
app = Flask(__name__)
lock = threading.Lock()
cache = {
    "canvas": [],
    "calendar": [],
    "weather": {},
    "spotify": {"is_playing": False},
    "markets": [],
    "news": [],
    "afl": {},
    "updated_at": None,
}


def set_cache(key, value):
    with lock:
        cache[key] = value
        cache["updated_at"] = datetime.now(timezone.utc).isoformat()


def get_cache():
    with lock:
        return json.loads(json.dumps(cache))


def iso_now():
    return datetime.now(timezone.utc)


def parse_dt(value):
    if not value:
        return None
    try:
        dt = dtparser.isoparse(value) if isinstance(value, str) else value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def fmt_dt(dt, all_day=False):
    if all_day:
        return dt.strftime("%a %d %b")
    return dt.astimezone().strftime("%a %d %b, %H:%M")


def canvas_worker():
    while True:
        try:
            token = CONFIG.get("canvas_token")
            base = CONFIG.get("canvas_base_url", "").rstrip("/")
            if token and base:
                headers = {"Authorization": f"Bearer {token}"}
                courses = requests.get(
                    f"{base}/api/v1/courses",
                    params={"enrollment_state": "active", "per_page": 20},
                    headers=headers, timeout=12,
                ).json()
                now = iso_now(); end = now + timedelta(days=14); rows = []
                for course in courses:
                    cid = course.get("id"); cname = course.get("name", "Course")
                    if not cid: continue
                    try:
                        assignments = requests.get(
                            f"{base}/api/v1/courses/{cid}/assignments",
                            params={"bucket": "upcoming", "per_page": 10, "order_by": "due_at"},
                            headers=headers, timeout=12,
                        ).json()
                    except Exception:
                        continue
                    for a in assignments if isinstance(assignments, list) else []:
                        due = parse_dt(a.get("due_at"))
                        if due and now <= due <= end:
                            rows.append({"name": a.get("name", "Assignment"), "course_name": cname,
                                         "due_at_iso": due.isoformat(), "due_formatted": fmt_dt(due)})
                rows.sort(key=lambda x: x["due_at_iso"])
                set_cache("canvas", rows[:10])
        except Exception:
            log.exception("Canvas worker failed")
        time.sleep(300)


def expand_event(event, start, end):
    dtstart = event.get("DTSTART").dt
    dtend_prop = event.get("DTEND")
    if dtend_prop:
        dtend = dtend_prop.dt
        duration = dtend - dtstart
    else:
        duration = timedelta(0)
    if not hasattr(dtstart, "tzinfo") or dtstart.tzinfo is None:
        dtstart = dtstart.replace(tzinfo=timezone.utc)
    candidates = [dtstart]
    if event.get("RRULE"):
        try:
            rule_text = event.get("RRULE").to_ical().decode()
            rule = rrule.rrulestr(rule_text, dtstart=dtstart)
            candidates = list(rule.between(start, end, inc=True))
        except Exception:
            candidates = [dtstart]
    for occurrence in candidates:
        yield occurrence, occurrence + duration


def calendar_worker():
    while True:
        try:
            url = CONFIG.get("outlook_ics_url")
            if url and icalendar:
                raw = requests.get(url, timeout=20).content
                cal = icalendar.Calendar.from_ical(raw)
                now = iso_now(); end = now + timedelta(days=14); rows = []
                for event in cal.walk("VEVENT"):
                    try:
                        for start, finish in expand_event(event, now, end):
                            if not (now <= start <= end): continue
                            all_day = not hasattr(event.get("DTSTART").dt, "hour")
                            rows.append({
                                "title": str(event.get("SUMMARY", "Event")),
                                "start_iso": start.isoformat(),
                                "end_iso": finish.isoformat(),
                                "start_formatted": fmt_dt(start, all_day),
                                "all_day": all_day,
                            })
                    except Exception:
                        continue
                rows.sort(key=lambda x: x["start_iso"])
                set_cache("calendar", rows[:15])
        except Exception:
            log.exception("Calendar worker failed")
        time.sleep(600)


def weather_worker():
    while True:
        try:
            params = {
                "latitude": CONFIG["weather_lat"], "longitude": CONFIG["weather_lon"],
                "timezone": CONFIG["weather_timezone"], "forecast_days": 7,
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,wind_direction_10m,uv_index",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,sunrise,sunset",
            }
            data = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=15).json()
            current = data.get("current", {}); daily = data.get("daily", {})
            forecast = []
            for i, date in enumerate(daily.get("time", [])):
                forecast.append({"date": date, "high": daily.get("temperature_2m_max", [None])[i],
                                 "low": daily.get("temperature_2m_min", [None])[i],
                                 "precip_probability": daily.get("precipitation_probability_max", [None])[i],
                                 "weather_code": daily.get("weather_code", [None])[i]})
            set_cache("weather", {
                "current": current, "forecast": forecast,
                "rain_today_mm": (daily.get("precipitation_sum") or [0])[0],
                "sunrise": (daily.get("sunrise") or [None])[0],
                "sunset": (daily.get("sunset") or [None])[0],
            })
        except Exception:
            log.exception("Weather worker failed")
        time.sleep(900)


def spotify_worker():
    sp = None
    while True:
        try:
            if spotipy and SpotifyOAuth and CONFIG.get("spotify_client_id") and CONFIG.get("spotify_client_secret"):
                if sp is None:
                    auth = SpotifyOAuth(
                        client_id=CONFIG["spotify_client_id"], client_secret=CONFIG["spotify_client_secret"],
                        redirect_uri=CONFIG.get("spotify_redirect_uri", "http://localhost:8888/callback"),
                        scope="user-read-currently-playing user-read-playback-state",
                        cache_path=TOKEN_PATH,
                        open_browser=False,
                    )
                    sp = spotipy.Spotify(auth_manager=auth)
                current = sp.currently_playing()
                if current and current.get("item"):
                    item = current["item"]
                    album_images = item.get("album", {}).get("images", [])
                    art = album_images[0]["url"] if album_images else None
                    set_cache("spotify", {"is_playing": bool(current.get("is_playing")),
                                           "track_name": item.get("name", ""),
                                           "artist_name": ", ".join(a.get("name", "") for a in item.get("artists", [])),
                                           "album_name": item.get("album", {}).get("name", ""),
                                           "album_art_url": art,
                                           "progress_ms": current.get("progress_ms", 0),
                                           "duration_ms": item.get("duration_ms", 0)})
                else:
                    set_cache("spotify", {"is_playing": False})
        except Exception:
            log.exception("Spotify worker failed")
        time.sleep(5)


def quote(finnhub, symbol):
    r = requests.get("https://finnhub.io/api/v1/quote", params={"symbol": symbol, "token": finnhub}, timeout=12).json()
    return r


def markets_worker():
    while True:
        try:
            key = CONFIG.get("finnhub_api_key")
            if key:
                definitions = [
                    ("OANDA:XAU_USD", "Gold / oz USD"), ("OANDA:XAG_USD", "Silver / oz USD"),
                    ("BINANCE:BTCUSDT", "BTC USD"), ("^GSPC", "S&P 500"),
                    ("OANDA:AUD_USD", "AUD / USD"),
                ]
                rows = []
                for symbol, label in definitions:
                    q = quote(key, symbol)
                    price = q.get("c"); prev = q.get("pc")
                    change = ((price - prev) / prev * 100) if price is not None and prev else 0
                    rows.append({"symbol": symbol, "label": label, "price": price, "change_pct": change, "sparkline": []})
                silver = rows[1]["price"]; audusd = rows[4]["price"]
                if silver and audusd:
                    rows.append({"symbol": "DERIVED:XAG_AUD", "label": "Silver / oz AUD",
                                 "price": silver / audusd, "change_pct": 0, "sparkline": []})
                set_cache("markets", rows)
        except Exception:
            log.exception("Markets worker failed")
        time.sleep(60)


def news_worker():
    while True:
        try:
            key = CONFIG.get("newsapi_key")
            if key:
                items = []
                for country, size in (("au", 20), ("us", 10)):
                    data = requests.get("https://newsapi.org/v2/top-headlines",
                                        params={"country": country, "pageSize": size, "apiKey": key}, timeout=15).json()
                    for article in data.get("articles", []):
                        title = article.get("title")
                        if title: items.append({"title": title, "source": (article.get("source") or {}).get("name", "")})
                seen = set(); deduped = []
                for item in items:
                    k = item["title"].strip().lower()
                    if k not in seen: seen.add(k); deduped.append(item)
                set_cache("news", deduped[:20])
        except Exception:
            log.exception("News worker failed")
        time.sleep(1800)


def afl_worker():
    while True:
        try:
            ua = CONFIG.get("squiggle_user_agent", "smart-desk-dashboard")
            headers = {"User-Agent": ua}
            data = requests.get("https://api.squiggle.com.au/?q=games;year=2026", headers=headers, timeout=15).json()
            games = data.get("games", []) if isinstance(data, dict) else []
            set_cache("afl", {"games": games, "favourite_team": CONFIG.get("favourite_afl_team", "brisbane")})
        except Exception:
            log.exception("AFL worker failed")
        time.sleep(60)


@app.get("/data")
def data():
    return jsonify(get_cache())


def start_worker(fn):
    threading.Thread(target=fn, daemon=True, name=fn.__name__).start()


if __name__ == "__main__":
    for worker in (canvas_worker, calendar_worker, weather_worker, spotify_worker, markets_worker, news_worker, afl_worker):
        start_worker(worker)
    app.run(host="127.0.0.1", port=5000, threaded=True)
