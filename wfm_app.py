#!/usr/bin/env python3
"""
Warframe Market Prices - desktop app (PySide6)

Shows item pictures with min / median / max sell price (max ignores extreme
outliers), ducats (prime parts) and a suggested price in one of two modes:
  Sell Fast                    - 1 platinum under the cheapest realistic sell listing (default)
  Best Price (Price Clusters)  - median of the busiest price cluster among the sell listings
Opens on your last session (category, search, max items, platform, rank, price mode, theme,
auto-update, window size, sort order...) and checks prices right away. A fresh install starts
with: dark theme, Sell Fast, 10 minute auto-update, Unranked, 10 items, Prime parts, PC.
Search tolerates typos. Pictures download in parallel; double-click a row to open its
warframe.market page; the app can refresh itself on a timer (Auto-update).
Item pictures are downloaded with a browser-like client (curl_cffi, if installed) and fall back
to the community item database or a simple tile. The button in the top right switches between
the dark and light theme.

Run from source:  python wfm_app.py
Build an .exe:    see build_exe.bat
Needs wfm_prices.py in the same folder.
"""

import csv
import hashlib
import io
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote, urlsplit
from pathlib import Path

import requests
from PySide6.QtCore import (QBuffer, QByteArray, QIODevice, QObject, QPointF, QRect, QSize, Qt,
                            QEvent, QSettings, QThread, QTimer, QUrl, Signal)
from PySide6.QtGui import (QColor, QCursor, QDesktopServices, QFont, QFontMetrics,
                           QGuiApplication, QIcon, QImage, QPainter, QPixmap, QPolygonF)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QSpinBox, QStyledItemDelegate, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

import wfm_prices as wfm

APP_TITLE = "Warframe Market Prices"
CACHE_DIR = Path.home() / ".wfm_price_checker" / "images_v6"  # v6: big pictures are shrunk when saved, mods may use the fallback database
ICON_SIZE = 48
MAX_PICTURE = 800    # bigger downloaded pictures are shrunk to fit inside this many pixels
ENLARGED_SIZE = 360  # width of the hover picture for mods, in normal (unscaled) screen pixels
IMAGE_THREADS = 4    # pictures downloaded at the same time
DEFAULT_START_ITEMS = 10
# Prices on warframe.market move slowly and every refresh costs about one request per item,
# so 10 minutes keeps things fresh without hammering the site.
AUTO_CHOICES = [("Off", 0), ("5 minutes", 5), ("10 minutes (recommended)", 10),
                ("15 minutes", 15), ("30 minutes", 30)]
DEFAULT_AUTO_MINUTES = 10

RANK_CHOICES = [
    ("Any rank", "any"),
    ("Unranked", "unranked"),
    ("Max rank", "max"),
]
RANK_VALUES = {"any": None, "unranked": 0, "max": "max"}
DEFAULT_RANK = "unranked"

# the two ways to suggest a price (both are worked out for every item; the setting only picks which is shown)
PRICE_MODE_CHOICES = [("Sell Fast", "fast"), ("Best Price (Price Clusters)", "clusters")]
SELLER_CHOICES = [("All sellers", "all"), ("Online / in-game only", "online"), ("Offline only", "offline")]
PRICE_FIELDS = {"fast": "price_fast", "clusters": "price_cluster"}
PRICE_HEADERS = {"fast": "Sell Fast", "clusters": "Best Price"}
DEFAULT_PRICE_MODE = "fast"
PRICE_TIPS = {
    "fast": "Sell Fast: 1 platinum under the cheapest realistic sell listing "
            "(lowball listings ignored; uses the sellers chosen in the Sellers setting).",
    "clusters": "Best Price: the median price of the busiest price cluster among the sell listings "
                "(where sellers really bunch up). Usually higher than Sell Fast, but slower to sell.",
}

CATEGORY_CHOICES = [
    ("Prime parts", "prime"),
    ("Mods (includes veiled rivens)", "mods"),
    ("Arcanes", "arcanes"),
    ("Relics", "relics"),
]

# mods that also exist as a cosmetic Atragraph copy get a small "Atragraph | Regular" switch after their name
VARIANT_CHOICES = [("atragraph", "Atragraph"), ("regular", "Regular")]
DEFAULT_VARIANT = "regular"
ROLE_VARIANT = Qt.UserRole + 1   # the version a row currently shows (None for items without versions)

COLUMNS = ["", "Item", "Listings", "Min", "Median", "Max", "Ducats", PRICE_HEADERS[DEFAULT_PRICE_MODE]]
COL_IMAGE, COL_NAME, COL_LISTINGS, COL_MIN, COL_MEDIAN, COL_MAX, COL_DUCATS, COL_EQ = range(8)

THEMES = {
    "dark": dict(bg="#15171c", panel="#1e222a", alt="#242932", text="#e8eaf0", muted="#98a1b2",
                 border="#323844", accent="#7c9cff", accent_text="#0d1020", hover="#2c333f",
                 select="#34406b"),
    "light": dict(bg="#f2f4f8", panel="#ffffff", alt="#eef1f7", text="#1c2230", muted="#5b6577",
                  border="#d3d9e5", accent="#3b5bdb", accent_text="#ffffff", hover="#e3e9f6",
                  select="#cfdcff"),
}


def make_arrows(c):
    """Draw the little dropdown arrows (one normal, one dimmed) for a theme and return their paths.
    Stylesheet-styled combo boxes lose their native arrow, so we supply our own."""
    try:
        folder = CACHE_DIR.parent / "ui"
        folder.mkdir(parents=True, exist_ok=True)
        out = []
        for suffix, color in (("", c["accent"]), ("_off", c["muted"])):
            img = QImage(20, 12, QImage.Format_ARGB32)
            img.fill(QColor(0, 0, 0, 0))
            p = QPainter(img)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color))
            p.drawPolygon(QPolygonF([QPointF(2, 2), QPointF(18, 2), QPointF(10, 10)]))
            p.end()
            path = folder / f"arrow_{c['bg'].lstrip('#')}{suffix}.png"
            img.save(str(path), "PNG")
            out.append(path.as_posix())
        return tuple(out)
    except Exception:
        return None


def build_qss(c, arrows=None):
    arrow_css = ""
    if arrows:
        arrow_css = f"""
    QComboBox {{ padding-right: 28px; }}
    QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right;
        width: 24px; border: none; border-left: 1px solid {c['border']}; }}
    QComboBox::down-arrow {{ image: url("{arrows[0]}"); width: 10px; height: 6px; }}
    QComboBox::down-arrow:disabled {{ image: url("{arrows[1]}"); }}
    """
    return f"""
    QFrame#optbar {{ background: {c['panel']}; border: 1px solid {c['accent']}; border-radius: 8px; }}
    QFrame#optbar QLabel {{ font-weight: 600; }}
    QFrame#optbar QCheckBox {{ font-weight: 600; }}
    QFrame#optbar QComboBox {{ background: {c['bg']}; border: 1px solid {c['accent']};
        font-weight: 600; min-width: 90px; }}
    QFrame#optbar QComboBox:hover {{ background: {c['hover']}; }}
    QFrame#optbar QComboBox:disabled {{ border: 1px dashed {c['muted']}; color: {c['muted']}; }}
    QFrame#optbar QLabel:disabled {{ color: {c['muted']}; }}
    QMainWindow, QWidget {{ background: {c['bg']}; color: {c['text']}; font-size: 13px; }}
    QLabel {{ background: transparent; }}
    QLabel#muted {{ color: {c['muted']}; }}
    QLabel:disabled {{ color: {c['muted']}; }}
    QLineEdit, QComboBox, QSpinBox {{
        background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 6px;
        padding: 5px 8px; min-height: 22px; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1px solid {c['accent']}; }}
    QComboBox QAbstractItemView {{
        background: {c['panel']}; border: 1px solid {c['border']};
        selection-background-color: {c['select']}; selection-color: {c['text']}; }}
    QCheckBox {{ spacing: 8px; background: transparent; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px; border-radius: 4px;
        border: 1px solid {c['muted']}; background: {c['panel']}; }}
    QCheckBox::indicator:hover {{ border: 1px solid {c['accent']}; }}
    QCheckBox::indicator:checked {{ background: {c['accent']}; border: 1px solid {c['accent']}; }}
    QCheckBox::indicator:disabled {{ border: 1px solid {c['border']}; background: {c['bg']}; }}
    QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}
    QPushButton {{
        background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 6px;
        padding: 6px 14px; min-height: 22px; }}
    QPushButton:hover {{ background: {c['hover']}; }}
    QPushButton:disabled {{ color: {c['muted']}; }}
    QPushButton#primary {{
        background: {c['accent']}; color: {c['accent_text']}; border: 1px solid {c['accent']};
        font-weight: 600; }}
    QPushButton#primary:disabled {{ background: {c['border']}; border-color: {c['border']}; color: {c['muted']}; }}
    QTableWidget {{
        background: {c['panel']}; alternate-background-color: {c['alt']};
        border: 1px solid {c['border']}; border-radius: 6px; gridline-color: transparent;
        selection-background-color: {c['select']}; selection-color: {c['text']}; }}
    QTableWidget::item {{ padding: 4px 8px; }}
    QHeaderView::section {{
        background: {c['bg']}; color: {c['muted']}; border: none;
        border-bottom: 1px solid {c['border']}; padding: 8px; font-weight: 600; }}
    QProgressBar {{
        background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 6px;
        text-align: center; min-height: 16px; }}
    QProgressBar::chunk {{ background: {c['accent']}; border-radius: 5px; }}
    QScrollBar:vertical {{ background: {c['bg']}; width: 12px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 5px; min-height: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar:horizontal {{ background: {c['bg']}; height: 12px; margin: 0; }}
    QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 5px; min-width: 30px; }}
    QMessageBox {{ background: {c['bg']}; }}
    QWidget#hoverpreview {{ background: {c['panel']}; border: 1px solid {c['accent']}; }}
    {arrow_css}
    """


# --------------------------------------------------------------------------
# Pictures
# --------------------------------------------------------------------------
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "image/webp,image/png,image/*;q=0.8,*/*;q=0.5",
    "Referer": "https://warframe.market/",
}
ERROR_LOG = CACHE_DIR.parent / "image_errors.log"
SOURCE_LOG = CACHE_DIR.parent / "image_sources.log"


# warframe.market's picture server answers plain Python requests with HTTP 403, so
# pictures are fetched with several different "network stacks" until one is let through.
IMAGE_REQUEST_HEADERS = {
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://warframe.market/",
    "Sec-Fetch-Dest": "image",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "same-origin",
}
_sessions = {}
_cffi_lock = threading.Lock()
_qt_lock = threading.Lock()
_wfcd_lock = threading.Lock()


def _origin(url):
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}/"


def _warm_up(session, url, **kw):
    """Visit the site's front page once, like a browser does, so any cookies it hands out are kept."""
    origin = _origin(url)
    if origin not in _sessions:
        _sessions[origin] = True
        try:
            session.get(origin, timeout=15, **kw)
        except Exception:
            pass


def fetch_curl_cffi(url):
    from curl_cffi import requests as cr  # optional: mimics a real Chrome browser
    if "cffi" not in _sessions:
        _sessions["cffi"] = cr.Session(impersonate="chrome")
    s = _sessions["cffi"]
    _warm_up(s, url)
    r = s.get(url, timeout=15, headers=IMAGE_REQUEST_HEADERS)
    return r.status_code, r.headers.get("Content-Type", "?"), r.content


def fetch_requests(url):
    if "req" not in _sessions:
        s = requests.Session()
        s.headers.update(BROWSER_HEADERS)
        _sessions["req"] = s
    s = _sessions["req"]
    _warm_up(s, url)
    r = s.get(url, timeout=15, headers=IMAGE_REQUEST_HEADERS)
    return r.status_code, r.headers.get("Content-Type", "?"), r.content


def fetch_qt(url):
    """Qt's own network code (uses Windows' built-in TLS, which looks different to the server)."""
    from PySide6.QtCore import QEventLoop, QUrl
    from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
    nam = QNetworkAccessManager()
    req = QNetworkRequest(QUrl(url))
    for k, v in BROWSER_HEADERS.items():
        req.setRawHeader(k.encode(), v.encode())
    req.setAttribute(QNetworkRequest.RedirectPolicyAttribute,
                     QNetworkRequest.NoLessSafeRedirectPolicy)
    req.setTransferTimeout(15000)
    reply = nam.get(req)
    loop = QEventLoop()
    reply.finished.connect(loop.quit)
    loop.exec()
    status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) or 0
    ctype = reply.header(QNetworkRequest.ContentTypeHeader) or "?"
    data = bytes(reply.readAll())
    if not status:
        raise RuntimeError(reply.errorString())
    reply.deleteLater()
    return int(status), str(ctype), data


def _locked(fn, lock):
    """These stacks are not safe to share between picture threads, so they take turns."""
    def wrapper(url):
        with lock:
            return fn(url)
    return wrapper


FETCHERS = [("browser-like (curl_cffi)", _locked(fetch_curl_cffi, _cffi_lock)),
            ("requests", fetch_requests),
            ("Qt network", _locked(fetch_qt, _qt_lock))]
_fetch_ok = {name: 0 for name, _ in FETCHERS}
_fetch_bad = {name: 0 for name, _ in FETCHERS}
_fetch_ok["item database"] = 0  # only used for the source log


def to_png_bytes(data):
    """Decode a downloaded picture (webp/png/...) and return it re-encoded as PNG, or None."""
    img = QImage.fromData(data)
    if not img.isNull():
        if max(img.width(), img.height()) > MAX_PICTURE:   # huge pictures: shrink, keep proportions
            img = img.scaled(MAX_PICTURE, MAX_PICTURE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "PNG")
        return bytes(ba)
    try:  # Qt could not read it (for example no webp support): try Pillow
        from PIL import Image
        out = io.BytesIO()
        im = Image.open(io.BytesIO(data)).convert("RGBA")
        im.thumbnail((MAX_PICTURE, MAX_PICTURE))
        im.save(out, "PNG")
        return out.getvalue()
    except Exception:
        return None


# ---- fallback picture source: the Warframe community item database (WFCD) ----------
WFCD_API = "https://api.warframestat.us/items/search/"
WFCD_CDN = "https://cdn.warframestat.us/img/"
_wfcd_session = requests.Session()
_wfcd_session.headers.update({"User-Agent": "wfm-price-checker/1.0", "Accept": "application/json"})


def _wfcd_search(query):
    for attempt in range(3):
        r = _wfcd_session.get(WFCD_API + quote(query) + "?language=en", timeout=15)
        if r.status_code == 429:
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} from item database")
        data = r.json()
        return data if isinstance(data, list) else []
    raise RuntimeError("item database rate limit")


def wfcd_image_url(name):
    """Find a picture link for an item name in the WFCD database, or None."""
    lname = name.lower()
    queries = [name]
    if lname.endswith(" relic"):
        queries.append(name[:-6])
    for q in queries:
        results = _wfcd_search(q)
        # 1) an item with exactly this name
        for it in results:
            if str(it.get("name", "")).lower() == q.lower() and it.get("imageName"):
                return WFCD_CDN + it["imageName"]
        # 2) a part of a bigger item, e.g. "Zephyr Prime Chassis" inside "Zephyr Prime"
        for it in results:
            parent = str(it.get("name", "")).lower()
            if parent and lname.startswith(parent + " "):
                rest = lname[len(parent) + 1:]
                for c in it.get("components") or []:
                    cn = str(c.get("name", "")).lower()
                    if cn and rest.startswith(cn) and c.get("imageName"):
                        return WFCD_CDN + c["imageName"]
                if it.get("imageName"):  # no picture for the part itself: use the parent's
                    return WFCD_CDN + it["imageName"]
        # 3) a close match that starts with the name (relics: "Lith A1 Intact")
        for it in results:
            if str(it.get("name", "")).lower().startswith(q.lower()) and it.get("imageName"):
                return WFCD_CDN + it["imageName"]
    return None


def make_placeholder(name, cache_name):
    """Draw a simple coloured tile with the item's initials (used when no picture can be found)."""
    target = CACHE_DIR / ("ph_" + cache_name + ".png")
    if target.exists() and target.stat().st_size > 0:
        return target
    h = int(hashlib.sha1(name.encode()).hexdigest()[:6], 16)
    words = [w for w in name.replace("-", " ").split() if w[0].isalnum()]
    text = "".join(w[0] for w in words[:2]).upper() or "?"
    img = QImage(96, 96, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor.fromHsv(h % 360, 90, 120))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 4, 88, 88, 14, 14)
    p.setPen(QColor(255, 255, 255, 230))
    f = QFont()
    f.setPixelSize(36)
    f.setBold(True)
    p.setFont(f)
    p.drawText(img.rect(), Qt.AlignCenter, text)
    p.end()
    img.save(str(target), "PNG")
    return target


def download_image(urls, name="", category=""):
    """Get an item picture and keep it in the cache folder as PNG.
    Order: warframe.market (blocked for most apps) -> community item database -> placeholder tile.
    Returns (path or None, error text). A non-empty error with a path means a placeholder is used."""
    if isinstance(urls, str):
        urls = [urls]
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return None, f"cannot create cache folder {CACHE_DIR}: {e}"
    key = hashlib.sha1((name or (urls[0] if urls else "?")).encode()).hexdigest()
    target = CACHE_DIR / (key + ".png")
    if target.exists() and target.stat().st_size > 0:
        return target, ""
    errors = []
    # 1) warframe.market itself; stacks that never worked and failed 3 times are skipped
    order = sorted(FETCHERS, key=lambda f: -_fetch_ok[f[0]])
    for fname, fn in order:
        if not urls or (_fetch_bad[fname] >= 3 and _fetch_ok[fname] == 0):
            continue
        for url in urls:
            try:
                status, ctype, content = fn(url)
                if status != 200 or not content:
                    err = f"[{fname}] HTTP {status} ({ctype}) for {url}"
                else:
                    png = to_png_bytes(content)
                    if png is not None:
                        target.write_bytes(png)
                        _fetch_ok[fname] += 1
                        return target, ""
                    err = f"[{fname}] not a readable picture ({ctype}, {len(content)} bytes)"
            except Exception as e:
                err = f"[{fname}] {e.__class__.__name__}: {e}"
            if len(errors) < 2:
                errors.append(err)
        _fetch_bad[fname] += 1
    # 2) community item database
    if name:
        try:
            with _wfcd_lock:
                link = wfcd_image_url(name)
            if link:
                r = _wfcd_session.get(link, timeout=15)
                if r.status_code == 200 and r.content:
                    png = to_png_bytes(r.content)
                    if png is not None:
                        target.write_bytes(png)
                        _fetch_ok["item database"] += 1
                        return target, ""
                    errors.append(f"[item database] unreadable picture at {link}")
                else:
                    errors.append(f"[item database] HTTP {r.status_code} for {link}")
            else:
                errors.append(f"[item database] no picture found for {name}")
        except Exception as e:
            errors.append(f"[item database] {e.__class__.__name__}: {e}")
    # 3) placeholder tile so the row is not blank
    try:
        return make_placeholder(name or "?", key), "\n".join(errors[:4])
    except Exception as e:
        return None, "\n".join(errors[:4]) + f"\n[placeholder] {e}"


# --------------------------------------------------------------------------
# Background worker (keeps the window responsive while prices load)
# --------------------------------------------------------------------------
def _download_job(emit, key, name, urls, category):
    """Runs on a picture thread: download one picture and report it.
    Returns the error text ('' means a real picture was found)."""
    img, err = download_image(urls, name, category)
    emit(key, str(img) if img else "", not err)
    return err


def write_source_log():
    try:
        SOURCE_LOG.parent.mkdir(parents=True, exist_ok=True)
        lines = ["Pictures found this session, by source:"]
        lines += [f"  {name}: {n}" for name, n in _fetch_ok.items()]
        SOURCE_LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


class Worker(QObject):
    row = Signal(dict, str)        # result row, image file path ("" if none yet)
    progress = Signal(int, int, str)
    done = Signal(str)
    failed = Signal(str)
    image_problem = Signal(str)
    image_ready = Signal(str, str, bool)   # row key, picture path, True for a real picture

    def __init__(self, opts):
        super().__init__()
        self.opts = opts
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        o = self.opts
        pool = ThreadPoolExecutor(max_workers=IMAGE_THREADS)
        try:
            client = wfm.Client(o["platform"])
            items = wfm.load_items(client)
            picked = wfm.search_items(
                [it for it in items if wfm.classify(it, o["include_sets"]) == o["category"]],
                o["search"])
            if o["limit"]:
                picked = picked[: o["limit"]]
            if not picked:
                self.done.emit("No items matched your search.")
                return

            total = len(picked)
            jobs = []   # (future, key, name, urls)
            # Prices are fetched one by one (the API limits requests per second) while the
            # pictures download on several threads at the same time.
            for n, it in enumerate(picked, 1):
                if self._stop:
                    pool.shutdown(wait=True, cancel_futures=True)
                    self.done.emit(f"Stopped after {n - 1} of {total} items.")
                    return
                name = wfm.item_name(it)
                self.progress.emit(n - 1, total, name)
                raw = client.get(f"{wfm.API_V2}/orders/item/{it.get('slug')}")
                if o["category"] == "mods" and wfm.has_atragraph(it):
                    # this mod also has an Atragraph copy: price each version separately,
                    # the table shows one and lets you switch with a click
                    versions = {v: wfm.compute_row(it, raw, o["online_only"], o["rank"], v)
                                for v, _ in VARIANT_CHOICES}
                    row = dict(versions[DEFAULT_VARIANT])
                    row["variants"] = {v: {k: r[k] for k in wfm.STAT_KEYS} for v, r in versions.items()}
                else:
                    row = wfm.compute_row(it, raw, o["online_only"], o["rank"])
                row["slug"] = it.get("slug") or name
                urls = row.get("image_urls") or row.get("image_url")
                if o["category"] == "mods" and isinstance(urls, list):
                    urls = list(reversed(urls))   # full-size picture first (sharp when enlarged), thumbnail as backup
                    row["image_urls"] = urls
                self.row.emit(row, "")
                jobs.append((pool.submit(_download_job, self.image_ready.emit, row["slug"], name,
                                         urls, o["category"]), row["slug"], name, urls))
                self.progress.emit(n, total, name)

            self.progress.emit(total, total, "pictures")
            missing = [(key, name, urls) for fut, key, name, urls in jobs if fut.result()]
            first_err = ""
            if missing and not self._stop:      # one automatic second try for the pictures that failed
                for k in _fetch_bad:
                    _fetch_bad[k] = 0
                self.progress.emit(total, total, "missing pictures (second try)")
                retry = [(pool.submit(_download_job, self.image_ready.emit, key, name, urls,
                                      o["category"]), key, name, urls) for key, name, urls in missing]
                errs = [(fut.result(), key) for fut, key, name, urls in retry]
                missing = [key for err, key in errs if err]
                first_err = next((err for err, key in errs if err), "")
            write_source_log()
            if missing:
                try:
                    ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
                    ERROR_LOG.write_text(f"{len(missing)} of {total} pictures failed.\nFirst error: {first_err}\n",
                                         encoding="utf-8")
                except Exception:
                    pass
                self.image_problem.emit(f"{len(missing)} of {total} pictures could not be downloaded (simple tiles are shown instead; "
                                        f"use Retry pictures to try again).\n\nFirst error:\n{first_err}")
            self.done.emit(f"Finished: {total} items. Click a column header to sort.")
        except (Exception, SystemExit) as e:  # SystemExit comes from the core helpers
            self.failed.emit(str(e) or e.__class__.__name__)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)


class RetryWorker(QObject):
    """Tries again for the pictures that are still tiles, without touching prices."""
    image_ready = Signal(str, str, bool)
    done = Signal(str)

    def __init__(self, jobs, category):
        super().__init__()
        self.jobs = jobs            # (row key, name, picture links)
        self.category = category

    def stop(self):
        pass

    def run(self):
        for k in _fetch_bad:
            _fetch_bad[k] = 0
        found = 0
        try:
            with ThreadPoolExecutor(max_workers=IMAGE_THREADS) as pool:
                futs = [pool.submit(_download_job, self.image_ready.emit, k, n, u, self.category)
                        for k, n, u in self.jobs]
                for f in futs:
                    if not f.result():
                        found += 1
            write_source_log()
        except Exception as e:
            self.done.emit(f"Picture retry failed: {e}")
            return
        left = len(self.jobs) - found
        self.done.emit(f"Retried {len(self.jobs)} pictures: {found} found" + (f", {left} still missing." if left else "."))


# --------------------------------------------------------------------------
# Table item that sorts by number instead of by text
# --------------------------------------------------------------------------
class NumItem(QTableWidgetItem):
    def __init__(self, value):
        super().__init__(wfm.fmt(value))
        self.value = value
        self.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

    def __lt__(self, other):
        a = -1 if self.value is None else self.value
        b = getattr(other, "value", None)
        b = -1 if b is None else b
        return a < b


# --------------------------------------------------------------------------
# Item column: name plus the "Atragraph | Regular" switch (drawn, so sorting never separates them)
# --------------------------------------------------------------------------
class NameDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.colors = THEMES["dark"]
        self.rects = {}      # (row key, version) -> where that word was drawn (viewport coordinates)

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        current = index.data(ROLE_VARIANT)
        if not current:
            return
        key = index.data(Qt.UserRole)
        font = QFont(option.font)
        font.setBold(True)
        fm = QFontMetrics(font)
        x = (option.rect.left() + 8 + QFontMetrics(option.font).horizontalAdvance(str(index.data(Qt.DisplayRole)))
             + 24)
        painter.save()
        painter.setFont(font)
        for part in (VARIANT_CHOICES[0], (None, " | "), VARIANT_CHOICES[1]):
            version, text = part
            w = fm.horizontalAdvance(text)
            rect = QRect(x, option.rect.top(), w, option.rect.height())
            painter.setPen(QColor(self.colors["accent"] if version == current else self.colors["muted"]))
            painter.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, text)
            if version:
                self.rects[(key, version)] = rect
            x += w
        painter.restore()


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------
class HoverPreview(QWidget):
    """Big picture that follows the mouse over mod rows. It is drawn at the screen's real
    resolution (including Windows display scaling), so it stays sharp instead of being stretched."""

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setObjectName("hoverpreview")
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(4)
        self.pic = QLabel()
        self.pic.setAlignment(Qt.AlignCenter)
        self.title = QLabel()
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet("font-weight: 600;")
        lay.addWidget(self.pic)
        lay.addWidget(self.title)
        self.key = None

    def show_for(self, key, path, name, window):
        """Show the picture at a FIXED spot tied to the main window (window = its frame rectangle),
        so it never jumps around with the mouse or the row you are over."""
        screen = QGuiApplication.screenAt(window.center()) or QGuiApplication.primaryScreen()
        if key != self.key:
            self.key = key
            dpr = screen.devicePixelRatio()
            pm = QPixmap(path)
            if pm.isNull():
                self.hide_preview()
                return
            pm = pm.scaledToWidth(int(ENLARGED_SIZE * dpr), Qt.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            self.pic.setPixmap(pm)
            self.title.setText(name)
            self.adjustSize()
        geo = screen.availableGeometry()
        if window.right() + 8 + self.width() <= geo.right():
            x = window.right() + 8                       # room beside the window: sit next to it
        else:
            x = window.right() - self.width() - 16       # otherwise: against the window's right edge
        y = window.center().y() - self.height() // 2     # vertically centred on the window
        y = max(geo.top(), min(y, geo.bottom() - self.height()))
        self.move(x, y)
        if not self.isVisible():
            self.show()

    def hide_preview(self):
        self.key = None
        self.hide()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1050, 720)
        self.rows = []
        self.running = False
        self.thread = None
        self.worker = None
        self.theme = "dark"  # fresh install starts dark; after that the last choice is restored
        self.sort_state = None       # (column, 0 ascending / 1 descending) once you click a column header
        self.last_ui = None          # control values of the last run (used by Update and auto-update)
        self.last_updated = None
        self.next_update = None
        self._auto_run = False
        self.run_category = None
        self.pic_items, self.name_items, self.pic_state = {}, {}, {}
        self.row_data = {}           # row key -> result row
        self.variant_choice = {}     # row key -> version picked with the switch (kept between updates)

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(10)

        # --- row 1: what to look up
        top = QHBoxLayout()
        top.setSpacing(10)
        self.category = QComboBox()
        for label, key in CATEGORY_CHOICES:
            self.category.addItem(label, key)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by name (optional, typos are OK)")
        self.search.setClearButtonEnabled(True)
        self.limit = QSpinBox()
        self.limit.setRange(0, 5000)
        self.limit.setValue(DEFAULT_START_ITEMS)
        self.limit.setSpecialValueText("All")
        self.limit.setToolTip("How many items to check. All can take several minutes.")
        self.platform = QComboBox()
        for p, label in (("pc", "PC"), ("xbox", "Xbox"), ("ps4", "PlayStation"), ("switch", "Switch")):
            self.platform.addItem(label, p)

        top.addWidget(QLabel("Category"))
        top.addWidget(self.category)
        top.addWidget(self.search, 1)
        top.addWidget(QLabel("Max items"))
        top.addWidget(self.limit)
        top.addWidget(QLabel("Platform"))
        top.addWidget(self.platform)
        outer.addLayout(top)

        # --- row 2: options and actions
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.sellers_label = QLabel("Sellers")
        self.sellers = QComboBox()
        for label, key in SELLER_CHOICES:
            self.sellers.addItem(label, key)
        sellers_tip = ("Which sell listings are counted for every column, including the suggested price: "
                       "all sellers, only players who are online or in-game, or only offline players.")
        self.sellers.setToolTip(sellers_tip)
        self.sellers_label.setToolTip(sellers_tip)
        self.include_sets = QCheckBox("Include prime sets")
        self.rank_label = QLabel("Rank")
        self.rank = QComboBox()
        for label, key in RANK_CHOICES:
            self.rank.addItem(label, key)
        self.rank.setCurrentIndex(self.rank.findData(DEFAULT_RANK))
        rank_tip = ("Mods and arcanes only: count all ranks, only unranked listings, "
                    "or only max-rank listings. (Greyed out for prime parts and relics, which have no ranks.)")
        self.rank.setToolTip(rank_tip)
        self.rank_label.setToolTip(rank_tip)
        self.price_label = QLabel("Suggested price")
        self.price_mode = QComboBox()
        for label, key in PRICE_MODE_CHOICES:
            self.price_mode.addItem(label, key)
        self.price_mode.setCurrentIndex(self.price_mode.findData(DEFAULT_PRICE_MODE))
        price_tip = "How the suggested price is worked out:\n\n" + "\n\n".join(PRICE_TIPS.values())
        self.price_mode.setToolTip(price_tip)
        self.price_label.setToolTip(price_tip)
        self.fetch_btn = QPushButton("Get prices")
        self.fetch_btn.setObjectName("primary")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.export_btn = QPushButton("Export CSV")
        self.export_btn.setEnabled(False)
        self.theme_btn = QPushButton()

        # the options sit in their own outlined box so it is obvious they can be changed
        optframe = QFrame()
        optframe.setObjectName("optbar")
        opt = QHBoxLayout(optframe)
        opt.setContentsMargins(12, 6, 12, 6)
        opt.setSpacing(12)
        opt.addWidget(self.sellers_label)
        opt.addWidget(self.sellers)
        opt.addWidget(self.include_sets)
        opt.addWidget(self.rank_label)
        opt.addWidget(self.rank)
        opt.addWidget(self.price_label)
        opt.addWidget(self.price_mode)
        opt.addStretch(1)
        bar.addStretch(1)
        bar.addWidget(self.fetch_btn)
        bar.addWidget(self.stop_btn)
        bar.addWidget(self.export_btn)
        bar.addWidget(self.theme_btn)
        outer.addWidget(optframe)
        outer.addLayout(bar)

        # --- row 3: refresh controls
        bar2 = QHBoxLayout()
        bar2.setSpacing(10)
        self.update_btn = QPushButton("Update")
        self.update_btn.setToolTip("Refresh the prices of the current list with the same settings.")
        self.update_btn.setEnabled(False)
        self.retry_btn = QPushButton("Retry pictures")
        self.retry_btn.setToolTip("Try again to download the pictures that are still shown as tiles.")
        self.retry_btn.setEnabled(False)
        self.updated_label = QLabel("Not updated yet")
        self.updated_label.setObjectName("muted")
        self.auto_combo = QComboBox()
        for label, mins in AUTO_CHOICES:
            self.auto_combo.addItem(label, mins)
        self.auto_combo.setCurrentIndex(self.auto_combo.findData(DEFAULT_AUTO_MINUTES))
        self.auto_combo.setToolTip("Refresh the list automatically after this much time. "
                                   "Counts from the end of the last update.")
        bar2.addWidget(self.update_btn)
        bar2.addWidget(self.retry_btn)
        bar2.addStretch(1)
        bar2.addWidget(self.updated_label)
        bar2.addWidget(QLabel("Auto-update"))
        bar2.addWidget(self.auto_combo)
        outer.addLayout(bar2)

        # --- table
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(ICON_SIZE + 10)
        self.table.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.name_delegate = NameDelegate(self.table)
        self.table.setItemDelegateForColumn(COL_NAME, self.name_delegate)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        hdr.setSectionResizeMode(COL_IMAGE, QHeaderView.Fixed)
        self.table.horizontalHeaderItem(COL_NAME).setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.setColumnWidth(COL_IMAGE, ICON_SIZE + 22)
        for col in (COL_LISTINGS, COL_MIN, COL_MEDIAN, COL_MAX, COL_DUCATS):
            self.table.setColumnWidth(col, 84)
        self.table.setColumnWidth(COL_EQ, 104)
        outer.addWidget(self.table, 1)

        # --- footer
        foot = QHBoxLayout()
        self.status = QLabel("Pick a category and press Get prices.")
        self.status.setObjectName("muted")
        self.progress = QProgressBar()
        self.progress.setFixedWidth(220)
        self.progress.setVisible(False)
        foot.addWidget(self.status, 1)
        foot.addWidget(self.progress)
        outer.addLayout(foot)
        note = QLabel("Prices are platinum from sell orders. Max ignores extreme outliers (marked *, hover "
                      "for details). Suggested price: Sell Fast = 1 platinum under the cheapest realistic "
                      "listing; Best Price (Price Clusters) = median of the busiest price cluster "
                      "(blank if there are no sell orders). Switch it with the Suggested price setting. "
                      "Mods that also exist as Atragraph show an Atragraph | Regular switch after their name: click one to see "
                      "that version's prices. Double-click a row to open its warframe.market page.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        outer.addWidget(note)

        # --- wiring
        self.fetch_btn.clicked.connect(self.start_fetch)
        self.stop_btn.clicked.connect(self.stop_fetch)
        self.export_btn.clicked.connect(self.export_csv)
        self.theme_btn.clicked.connect(self.toggle_theme)
        self.category.currentIndexChanged.connect(self._category_changed)
        self.category.activated.connect(lambda _i: self.start_fetch())  # only when YOU pick a category
        self.search.returnPressed.connect(self.start_fetch)
        self.update_btn.clicked.connect(lambda: self.update_now())
        self.retry_btn.clicked.connect(self.retry_pictures)
        self.table.cellDoubleClicked.connect(self.open_item_page)
        self.auto_combo.currentIndexChanged.connect(self._auto_changed)
        self.clock = QTimer(self)
        self.clock.setInterval(1000)
        self.clock.timeout.connect(self._tick)
        self.clock.start()
        self.hover = HoverPreview()
        self.table.setMouseTracking(True)
        self.table.viewport().setMouseTracking(True)
        self.table.viewport().installEventFilter(self)

        hdr.sectionClicked.connect(lambda _c: QTimer.singleShot(0, self._remember_sort))
        self.price_mode.currentIndexChanged.connect(self._price_mode_changed)

        self.load_settings()
        self.apply_theme(self.theme)
        self._category_changed()
        self._apply_price_mode()
        # remember every change right away (and again on close), so the next start picks up where you left off
        for combo in (self.category, self.platform, self.rank, self.price_mode, self.sellers):
            combo.currentIndexChanged.connect(self._save_quietly)
        self.limit.valueChanged.connect(self._save_quietly)
        self.include_sets.toggled.connect(self._save_quietly)
        self.search.editingFinished.connect(self._save_quietly)

    # ---------------- theme
    def apply_theme(self, name):
        self.theme = name
        QApplication.instance().setStyleSheet(build_qss(THEMES[name], make_arrows(THEMES[name])))
        self.theme_btn.setText("Light theme" if name == "dark" else "Dark theme")
        self.name_delegate.colors = THEMES[name]
        self.table.viewport().update()

    def toggle_theme(self):
        self.apply_theme("light" if self.theme == "dark" else "dark")
        self.save_settings()

    # ---------------- suggested price mode
    def _price_field(self):
        return PRICE_FIELDS.get(self.price_mode.currentData(), PRICE_FIELDS[DEFAULT_PRICE_MODE])

    def _price_mode_changed(self):
        self._apply_price_mode()

    def _apply_price_mode(self):
        """Show the chosen suggested price in the last column. Both prices are already known for every
        row, so switching is instant (no new download)."""
        mode = self.price_mode.currentData() or DEFAULT_PRICE_MODE
        field = self._price_field()
        head = self.table.horizontalHeaderItem(COL_EQ)
        if head is not None:
            head.setText(PRICE_HEADERS[mode])
            head.setToolTip(PRICE_TIPS[mode])
        sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)       # so rows do not jump around while their cells change
        for r in range(self.table.rowCount()):
            name_item = self.table.item(r, COL_NAME)
            cell = self.table.item(r, COL_EQ)
            row = self.row_data.get(name_item.data(Qt.UserRole) or name_item.text()) if name_item else None
            if row is not None and cell is not None:
                cell.value = row.get(field)
                cell.setText(wfm.fmt(cell.value))
        if sorting:
            self.table.setSortingEnabled(True)

    def _remember_sort(self):
        hdr = self.table.horizontalHeader()
        self.sort_state = (hdr.sortIndicatorSection(), 1 if hdr.sortIndicatorOrder() == Qt.DescendingOrder else 0)
        self.save_settings()

    def _save_quietly(self, *_args):
        self.save_settings()

    # ---------------- controls
    def _category_changed(self):
        is_prime = self.category.currentData() == "prime"
        self.include_sets.setEnabled(is_prime)
        has_rank = self.category.currentData() in ("mods", "arcanes")
        self.rank.setEnabled(has_rank)
        self.rank_label.setEnabled(has_rank)
        self.table.setColumnHidden(COL_DUCATS, not is_prime)

    def _set_running(self, running):
        self.running = running
        self.fetch_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.export_btn.setEnabled(not running and bool(self.rows))
        for w in (self.category, self.search, self.limit, self.platform,
                  self.sellers):
            w.setEnabled(not running)
        self.include_sets.setEnabled(not running and self.category.currentData() == "prime")
        self.rank.setEnabled(not running and self.category.currentData() in ("mods", "arcanes"))
        self.progress.setVisible(running)
        self.update_btn.setEnabled(not running and self.last_ui is not None)
        self.retry_btn.setEnabled(not running and any(not st["ok"] for st in self.pic_state.values()))

    # ---------------- fetching
    def _ui_state(self):
        return {
            "category": self.category.currentData(),
            "rank": self.rank.currentData(),
            "search": self.search.text().strip(),
            "limit": self.limit.value(),
            "platform": self.platform.currentData(),
            "online_only": self.sellers.currentData(),
            "include_sets": self.include_sets.isChecked(),
        }

    @staticmethod
    def _select(combo, value):
        for i in range(combo.count()):
            if str(combo.itemData(i)) == str(value):
                combo.setCurrentIndex(i)
                return

    def _set_ui(self, ui):
        self._select(self.category, ui["category"])
        self._select(self.rank, ui["rank"])
        self._select(self.platform, ui["platform"])
        self.search.setText(ui["search"])
        self.limit.setValue(ui["limit"])
        self._select(self.sellers, ui["online_only"])
        self.include_sets.setChecked(ui["include_sets"])

    @staticmethod
    def _opts_from_ui(ui):
        category = ui["category"]
        return {
            "category": category,
            "rank": RANK_VALUES[ui["rank"]] if category in ("mods", "arcanes") else None,
            "search": ui["search"],
            "limit": ui["limit"],
            "platform": ui["platform"],
            "online_only": ui["online_only"],
            "include_sets": ui["include_sets"],
        }

    def start_fetch(self):
        if self.running:
            return
        self._run(self._ui_state())

    def update_now(self, auto=False):
        """Refresh the current list with the settings of the last run."""
        if self.running or not self.last_ui:
            return
        if not auto:                      # an automatic refresh must not overwrite what you are typing
            self._set_ui(self.last_ui)
        self._run(dict(self.last_ui), auto)

    def _run(self, ui, auto=False):
        self.last_ui = dict(ui)
        self._auto_run = auto
        self.next_update = None
        self.save_settings()
        self.rows = []
        self.pic_items, self.name_items, self.pic_state = {}, {}, {}
        self.row_data = {}
        self.name_delegate.rects.clear()
        self.hover.hide_preview()
        self.run_category = ui["category"]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setColumnHidden(COL_DUCATS, ui["category"] != "prime")
        self.status.setText("Updating..." if auto else "Loading item list...")
        self.progress.setRange(0, 0)  # busy until we know the total
        self._set_running(True)
        self._launch(Worker(self._opts_from_ui(ui)))

    def _launch(self, worker):
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.image_ready.connect(self.on_image_ready)
        worker.done.connect(thread.quit)
        if isinstance(worker, Worker):
            worker.row.connect(self.on_row)
            worker.progress.connect(self.on_progress)
            worker.done.connect(self.on_done)
            worker.failed.connect(self.on_failed)
            worker.image_problem.connect(self.on_image_problem)
            worker.failed.connect(thread.quit)
        else:
            worker.done.connect(self.on_retry_done)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self.thread, self.worker = thread, worker
        thread.start()

    def retry_pictures(self):
        if self.running:
            return
        jobs = [(k, st["name"], st["urls"]) for k, st in self.pic_state.items() if not st["ok"]]
        if not jobs:
            return
        self._auto_run = False
        self.status.setText(f"Retrying {len(jobs)} pictures...")
        self.progress.setRange(0, 0)
        self._set_running(True)
        self.stop_btn.setEnabled(False)
        self._launch(RetryWorker(jobs, self.run_category))

    def on_retry_done(self, message):
        self._finish(message)

    def open_item_page(self, r, c):
        if self._switch_at(self.table.viewport().mapFromGlobal(QCursor.pos())):
            return      # a quick double click on the Atragraph/Regular switch is not "open the page"
        item = self.table.item(r, COL_NAME)
        slug = item.data(Qt.UserRole) if item else None
        if slug:
            QDesktopServices.openUrl(QUrl(f"https://warframe.market/items/{slug}"))

    # ---------------- auto-update and "last updated"
    def _schedule_auto(self):
        mins = self.auto_combo.currentData()
        self.next_update = (time.monotonic() + mins * 60) if (mins and self.last_ui) else None
        self._refresh_updated_label()

    def _auto_changed(self):
        self._schedule_auto()
        self.save_settings()

    def _refresh_updated_label(self):
        text = ("Not updated yet" if not self.last_updated
                else "Last updated " + self.last_updated.strftime("%H:%M:%S"))
        if self.next_update is not None and not self.running:
            left = max(0, int(self.next_update - time.monotonic()))
            text += f"  -  next update in {left // 60}:{left % 60:02d}"
        self.updated_label.setText(text)

    def _tick(self):
        if self.next_update is not None and not self.running and time.monotonic() >= self.next_update:
            self.update_now(auto=True)
            return
        self._refresh_updated_label()

    # ---------------- remembered settings
    @staticmethod
    def _settings():
        return QSettings("WFMPriceChecker", "PriceChecker")

    def save_settings(self):
        st = self._settings()
        st.setValue("category", self.category.currentData())
        st.setValue("platform", self.platform.currentData())
        st.setValue("search", self.search.text().strip())
        st.setValue("limit", self.limit.value())
        st.setValue("rank", self.rank.currentData())
        st.setValue("price_mode", self.price_mode.currentData())
        st.setValue("online_only", self.sellers.currentData())
        st.setValue("include_sets", self.include_sets.isChecked())
        st.setValue("auto_minutes", self.auto_combo.currentData())
        st.setValue("theme", self.theme)
        st.setValue("variants", json.dumps(self.variant_choice))
        if self.sort_state:
            st.setValue("sort_column", self.sort_state[0])
            st.setValue("sort_descending", self.sort_state[1])
        st.setValue("geometry", self.saveGeometry())
        st.sync()

    def load_settings(self):
        """Restore the last session. Anything never saved (a fresh install) keeps the defaults the
        controls were created with: Prime parts, PC, 10 items, Unranked, Sell Fast, 10 minute auto-update."""
        st = self._settings()
        for combo, key in ((self.category, "category"), (self.platform, "platform"),
                           (self.rank, "rank"), (self.price_mode, "price_mode"),
                           (self.auto_combo, "auto_minutes")):
            val = st.value(key)
            if val is not None:
                self._select(combo, val)
        self.search.setText(str(st.value("search", "") or ""))
        try:
            self.limit.setValue(int(st.value("limit", DEFAULT_START_ITEMS)))
        except (TypeError, ValueError):
            self.limit.setValue(DEFAULT_START_ITEMS)
        saved_sellers = str(st.value("online_only", "all")).lower()
        if saved_sellers == "true":          # older versions saved a checkbox
            saved_sellers = "online"
        self._select(self.sellers, saved_sellers if saved_sellers in ("all", "online", "offline") else "all")
        self.include_sets.setChecked(str(st.value("include_sets", "false")).lower() == "true")
        theme = str(st.value("theme", "dark"))
        self.theme = theme if theme in THEMES else "dark"
        try:    # the Atragraph / Regular choice of each mod
            saved = json.loads(str(st.value("variants", "{}")))
            valid = {v for v, _ in VARIANT_CHOICES}
            self.variant_choice = {str(k): v for k, v in saved.items() if v in valid}
        except (ValueError, AttributeError):
            self.variant_choice = {}
        try:
            col, desc = int(st.value("sort_column")), int(st.value("sort_descending", 0))
            self.sort_state = (col, 1 if desc else 0)
        except (TypeError, ValueError):
            self.sort_state = None
        geo = st.value("geometry")
        if geo:
            self.restoreGeometry(geo)

    def stop_fetch(self):
        if self.worker:
            self.worker.stop()
            self.status.setText("Stopping...")

    def on_progress(self, n, total, name):
        self.progress.setRange(0, total)
        self.progress.setValue(n)
        self.status.setText(f"Checking {name}  ({n}/{total})")

    def on_row(self, row, img_path):
        self.rows.append(row)
        r = self.table.rowCount()
        self.table.insertRow(r)

        key = row.get("slug") or row["name"]
        if row.get("variants"):
            choice = self.variant_choice.get(key, DEFAULT_VARIANT)
            row["variant"] = choice
            row.update(row["variants"][choice])
        self.row_data[key] = row
        pic = QTableWidgetItem()
        self.pic_items[key] = pic
        self.pic_state[key] = {"name": row["name"], "ok": False,
                               "urls": row.get("image_urls") or row.get("image_url")}
        self.table.setItem(r, COL_IMAGE, pic)
        if img_path:
            self.on_image_ready(key, img_path, True)

        name = QTableWidgetItem(row["name"])
        name.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        name.setData(Qt.UserRole, row.get("slug"))
        name.setData(ROLE_VARIANT, row.get("variant"))
        self.name_items[key] = name
        self.table.setItem(r, COL_NAME, name)
        self.table.setItem(r, COL_LISTINGS, NumItem(row["listings"]))
        self.table.setItem(r, COL_MIN, NumItem(row["min"]))
        self.table.setItem(r, COL_MEDIAN, NumItem(row["median"]))
        max_item = NumItem(row["max"])
        self._style_max(max_item, row)
        self.table.setItem(r, COL_MAX, max_item)
        self.table.setItem(r, COL_DUCATS, NumItem(row["ducats"]))
        self.table.setItem(r, COL_EQ, NumItem(row.get(self._price_field())))

    @staticmethod
    def _style_max(max_item, row):
        """Max column: show the star and the tooltip when outlier listings were ignored."""
        max_item.value = row["max"]
        max_item.setText(wfm.fmt(row["max"]))
        max_item.setToolTip("")
        if row.get("outliers"):
            n = row["outliers"]
            max_item.setText(wfm.fmt(row["max"]) + " *")
            max_item.setToolTip(
                f"Ignored {n} outlier listing{'s' if n != 1 else ''} priced far above the rest "
                f"(highest: {wfm.fmt(row['top_outlier'])} platinum).")

    # ---------------- Atragraph | Regular switch
    def _switch_at(self, pos):
        """(row key, version) when the mouse is over a word of the Atragraph | Regular switch."""
        item = self.table.itemAt(pos)
        if item is None or item.column() != COL_NAME or not item.data(ROLE_VARIANT):
            return None
        key = item.data(Qt.UserRole)
        for version, _ in VARIANT_CHOICES:
            rect = self.name_delegate.rects.get((key, version))
            if rect is not None and rect.contains(pos):
                return key, version
        return None

    def set_variant(self, key, version):
        row = self.row_data.get(key)
        name_item = self.name_items.get(key)
        if not row or name_item is None or version == row.get("variant") or version not in row.get("variants", {}):
            return
        self.variant_choice[key] = version
        self.save_settings()
        row["variant"] = version
        row.update(row["variants"][version])
        sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)      # so the row does not jump while its cells change
        r = name_item.row()
        name_item.setData(ROLE_VARIANT, version)
        for col, field in ((COL_LISTINGS, "listings"), (COL_MIN, "min"), (COL_MEDIAN, "median"),
                           (COL_EQ, self._price_field())):
            cell = self.table.item(r, col)
            cell.value = row[field]
            cell.setText(wfm.fmt(row[field]))
        self._style_max(self.table.item(r, COL_MAX), row)
        if sorting:
            self.table.setSortingEnabled(True)
        self.table.viewport().update()

    def on_image_ready(self, key, path, ok):
        pic = self.pic_items.get(key)
        if pic is None or not path:
            return
        pm = QPixmap(path)
        if not pm.isNull():
            # fit the whole picture inside the cell (smoothly shrunk, never cropped)
            pic.setIcon(QIcon(pm.scaled(ICON_SIZE * 2, ICON_SIZE * 2, Qt.KeepAspectRatio,
                                        Qt.SmoothTransformation)))
        st = self.pic_state.get(key)
        if st is not None:
            st["ok"] = ok
            st["path"] = path

    # ---------------- big picture while hovering over mods
    def eventFilter(self, obj, ev):
        if obj is self.table.viewport():
            t = ev.type()
            if t == QEvent.MouseMove:
                pos = ev.position().toPoint()
                over = self._switch_at(pos) is not None
                self.table.viewport().setCursor(Qt.PointingHandCursor if over else Qt.ArrowCursor)
                self._hover_at(pos)
            elif t == QEvent.MouseButtonRelease and ev.button() == Qt.LeftButton and not self.running:
                hit = self._switch_at(ev.position().toPoint())
                if hit:
                    self.set_variant(*hit)
            elif t in (QEvent.Leave, QEvent.Wheel, QEvent.MouseButtonPress):
                self.hover.hide_preview()
        return super().eventFilter(obj, ev)

    def _hover_at(self, pos):
        item = self.table.itemAt(pos) if self.run_category == "mods" else None
        if item is None or item.column() not in (COL_IMAGE, COL_NAME):
            self.hover.hide_preview()
            return
        name_item = self.table.item(item.row(), COL_NAME)
        key = (name_item.data(Qt.UserRole) or name_item.text()) if name_item else None
        st = self.pic_state.get(key)
        if not st or not st.get("path"):
            self.hover.hide_preview()
            return
        self.hover.show_for(key, st["path"], st["name"], self.frameGeometry())

    def _finish(self, message):
        self._set_running(False)
        if self.sort_state and COL_NAME <= self.sort_state[0] <= COL_EQ:   # your last chosen sort order
            if self.sort_state[0] != COL_DUCATS or self.run_category == "prime":
                self.table.horizontalHeader().setSortIndicator(
                    self.sort_state[0], Qt.DescendingOrder if self.sort_state[1] else Qt.AscendingOrder)
        self.table.setSortingEnabled(True)
        self.progress.setRange(0, 1)
        self.status.setText(message)

    def on_done(self, message):
        if self.rows:
            self.last_updated = datetime.now()
        self._finish(message)
        self._schedule_auto()

    def on_image_problem(self, message):
        if self._auto_run:      # no pop-ups from automatic updates
            return
        QMessageBox.information(self, APP_TITLE, message + f"\n\n(Saved to {ERROR_LOG})")

    def on_failed(self, message):
        self._finish("Something went wrong.")
        self._schedule_auto()
        if self._auto_run:
            self.status.setText(f"Auto-update failed ({message}). Will try again on the next timer.")
            return
        QMessageBox.warning(
            self, APP_TITLE,
            f"Could not load prices:\n\n{message}\n\nCheck your internet connection and try again.")

    # ---------------- export
    def export_csv(self):
        if not self.rows:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save CSV", "warframe_prices.csv", "CSV files (*.csv)")
        if not path:
            return
        fields = ["name", "variant", "listings", "min", "median", "max", "outliers", "top_outlier", "ducats",
                  "price_fast", "price_cluster"]
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(self.rows)
        self.status.setText(f"Saved {len(self.rows)} rows to {path}")

    def closeEvent(self, event):
        self.hover.hide_preview()
        self.save_settings()
        self.clock.stop()
        if self.worker and self.running:
            self.worker.stop()
        if self.thread:
            try:
                self.thread.quit()
                self.thread.wait(3000)
            except RuntimeError:
                pass
        event.accept()


def resource_path(name):
    """Find a bundled file, both when run from source and inside the built .exe."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name


def main():
    if sys.platform == "win32":
        try:  # lets the taskbar show our icon instead of the Python one
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("wfm.pricechecker.app")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    icon_file = resource_path("wfm_icon.ico")
    if icon_file.exists():
        app.setWindowIcon(QIcon(str(icon_file)))
    win = MainWindow()
    win.show()
    QTimer.singleShot(0, win.start_fetch)  # load the first items on open
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
