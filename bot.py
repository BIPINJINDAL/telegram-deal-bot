"""
Telegram Flash Deals Bot for Render Free Web Service + UptimeRobot.

Primary live deal source: Keepa's Amazon marketplace Deals API (Amazon India by default).
This bot never fabricates deal data. It only posts a deal after:
  1) a real source record is fetched,
  2) a list/MRP price is fetched from Keepa,
  3) discount >= configured threshold,
  4) discount math is recomputed from MRP + sale price,
  5) the original product URL responds successfully, and
  6) affiliate URL conversion succeeds / is explicitly configured to passthrough.

IMPORTANT Render Free limitation:
Render Free Web Service local files, including SQLite, are ephemeral. SQLite is used here
as requested, but it cannot be guaranteed to survive Render restarts/spin-down/redeploys.
Use Render Postgres or a paid persistent disk for durable production history.

Required env vars:
  TELEGRAM_BOT_TOKEN
  KEEPA_API_KEY
  EARNKARO_API_KEY          (or configure EARNKARO_DEEPLINK_TEMPLATE)

Optional env vars are documented in Config.from_env().
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import os
import re
import signal
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional
from urllib.parse import quote, urlparse

import aiohttp
import aiosqlite
from aiohttp import web
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)


# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
LOGGER = logging.getLogger("telegram-deal-bot")


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

TARGET_CHANNEL_ID = -1003958458010
ADMIN_USER_ID = 8893300996
DEFAULT_DB_PATH = "deals.db"
DEFAULT_PORT = 8080
DEFAULT_COOLDOWN_SECONDS = 120
DEFAULT_POLL_SECONDS = 180
DEFAULT_LINK_TIMEOUT_SECONDS = 12
DEFAULT_FETCH_TIMEOUT_SECONDS = 25
DEFAULT_KEEPA_DOMAIN_ID = 10  # Amazon India
DEFAULT_MIN_DISCOUNT = Decimal("20.00")


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    keepa_api_key: str
    earnkaro_api_key: Optional[str]
    earnkaro_affiliate_id: Optional[str]
    earnkaro_api_url: Optional[str]
    earnkaro_deeplink_template: Optional[str]
    allow_affiliate_passthrough: bool
    channel_id: int
    admin_user_id: int
    port: int
    db_path: str
    cooldown_seconds: int
    poll_seconds: int
    link_timeout_seconds: int
    fetch_timeout_seconds: int
    keepa_domain_id: int
    min_discount: Decimal
    keepa_max_deals_per_poll: int
    keepa_price_type: int
    title_max_length: int
    user_agent: str

    @staticmethod
    def _env_decimal(name: str, default: str) -> Decimal:
        raw = os.getenv(name, default).strip()
        try:
            return Decimal(raw)
        except InvalidOperation as exc:
            raise ValueError(f"{name} must be a decimal number, got {raw!r}") from exc

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        raw = os.getenv(name, str(default)).strip()
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer, got {raw!r}") from exc

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        keepa_key = os.getenv("KEEPA_API_KEY", "").strip()
        earnkaro_key = os.getenv("EARNKARO_API_KEY", "").strip() or None
        affiliate_id = os.getenv("EARNKARO_AFFILIATE_ID", "").strip() or None
        api_url = os.getenv("EARNKARO_API_URL", "").strip() or None
        template = os.getenv("EARNKARO_DEEPLINK_TEMPLATE", "").strip() or None
        passthrough = os.getenv("ALLOW_AFFILIATE_PASSTHROUGH", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

        if not token:
            raise RuntimeError("Missing required environment variable: TELEGRAM_BOT_TOKEN")
        if not keepa_key:
            raise RuntimeError("Missing required environment variable: KEEPA_API_KEY")

        if not (earnkaro_key or template or passthrough):
            raise RuntimeError(
                "Configure EarnKaro using EARNKARO_API_KEY / EARNKARO_API_URL, "
                "or EARNKARO_DEEPLINK_TEMPLATE. Set ALLOW_AFFILIATE_PASSTHROUGH=true only "
                "when you intentionally accept direct product URLs."
            )

        if api_url and not earnkaro_key:
            raise RuntimeError("EARNKARO_API_URL is set but EARNKARO_API_KEY is missing")

        return cls(
            telegram_bot_token=token,
            keepa_api_key=keepa_key,
            earnkaro_api_key=earnkaro_key,
            earnkaro_affiliate_id=affiliate_id,
            earnkaro_api_url=api_url,
            earnkaro_deeplink_template=template,
            allow_affiliate_passthrough=passthrough,
            channel_id=cls._env_int("TARGET_CHANNEL_ID", TARGET_CHANNEL_ID),
            admin_user_id=cls._env_int("ADMIN_USER_ID", ADMIN_USER_ID),
            port=cls._env_int("PORT", DEFAULT_PORT),
            db_path=os.getenv("DB_PATH", DEFAULT_DB_PATH).strip() or DEFAULT_DB_PATH,
            cooldown_seconds=max(0, cls._env_int("POST_COOLDOWN_SECONDS", DEFAULT_COOLDOWN_SECONDS)),
            poll_seconds=max(30, cls._env_int("DEAL_POLL_SECONDS", DEFAULT_POLL_SECONDS)),
            link_timeout_seconds=max(3, cls._env_int("LINK_TIMEOUT_SECONDS", DEFAULT_LINK_TIMEOUT_SECONDS)),
            fetch_timeout_seconds=max(5, cls._env_int("FETCH_TIMEOUT_SECONDS", DEFAULT_FETCH_TIMEOUT_SECONDS)),
            keepa_domain_id=cls._env_int("KEEPA_DOMAIN_ID", DEFAULT_KEEPA_DOMAIN_ID),
            min_discount=cls._env_decimal("MIN_DISCOUNT_PERCENT", str(DEFAULT_MIN_DISCOUNT)),
            keepa_max_deals_per_poll=max(1, cls._env_int("KEEPA_MAX_DEALS_PER_POLL", 25)),
            keepa_price_type=max(0, cls._env_int("KEEPA_PRICE_TYPE", 0)),
            title_max_length=max(50, cls._env_int("TITLE_MAX_LENGTH", 180)),
            user_agent=os.getenv(
                "HTTP_USER_AGENT",
                "telegram-flash-deals-bot/1.0 (+https://render.com)",
            ).strip(),
        )


# -----------------------------------------------------------------------------
# Data model
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Deal:
    deal_id: str
    asin: str
    title: str
    sale_price: Decimal
    mrp: Decimal
    discount_percent: Decimal
    product_url: str
    affiliate_url: str


@dataclass
class RuntimeMetrics:
    started_at: float
    last_keepa_fetch_at: Optional[float] = None
    last_keepa_success_at: Optional[float] = None
    last_error: Optional[str] = None
    last_uptime_robot_ping_at: Optional[float] = None
    uptime_robot_last_status: Optional[str] = None
    discovered_this_process: int = 0
    validated_this_process: int = 0
    posted_this_process: int = 0


# -----------------------------------------------------------------------------
# SQLite persistence
# -----------------------------------------------------------------------------


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self.db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        if self.db is not None:
            return
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA busy_timeout=5000")
        await self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS deals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_hash TEXT NOT NULL UNIQUE,
                source_id TEXT NOT NULL,
                title TEXT NOT NULL,
                sale_price TEXT NOT NULL,
                mrp TEXT NOT NULL,
                discount_percent TEXT NOT NULL,
                product_url TEXT NOT NULL,
                affiliate_url TEXT NOT NULL,
                discovered_at REAL NOT NULL,
                validated_at REAL,
                posted_at REAL
            );

            CREATE INDEX IF NOT EXISTS idx_deals_posted_at ON deals(posted_at);
            CREATE INDEX IF NOT EXISTS idx_deals_source_id ON deals(source_id);
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        await self.db.commit()

    async def close(self) -> None:
        if self.db is not None:
            await self.db.close()
            self.db = None

    async def record_discovered(self, deal: Deal) -> bool:
        """Insert a deal fingerprint. Return False when already known."""
        assert self.db is not None
        async with self._lock:
            cursor = await self.db.execute(
                """
                INSERT OR IGNORE INTO deals
                (deal_hash, source_id, title, sale_price, mrp, discount_percent,
                 product_url, affiliate_url, discovered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    deal.deal_id,
                    deal.asin,
                    deal.title,
                    str(deal.sale_price),
                    str(deal.mrp),
                    str(deal.discount_percent),
                    deal.product_url,
                    deal.affiliate_url,
                    time.time(),
                ),
            )
            await self.db.commit()
            return cursor.rowcount == 1

    async def mark_validated(self, deal_hash: str) -> None:
        assert self.db is not None
        async with self._lock:
            await self.db.execute(
                "UPDATE deals SET validated_at = ? WHERE deal_hash = ?",
                (time.time(), deal_hash),
            )
            await self.db.commit()

    async def mark_posted(self, deal_hash: str) -> None:
        assert self.db is not None
        async with self._lock:
            await self.db.execute(
                "UPDATE deals SET posted_at = ? WHERE deal_hash = ?",
                (time.time(), deal_hash),
            )
            await self.db.commit()

    async def is_posted(self, deal_hash: str) -> bool:
        assert self.db is not None
        async with self._lock:
            cursor = await self.db.execute(
                "SELECT 1 FROM deals WHERE deal_hash = ? AND posted_at IS NOT NULL LIMIT 1",
                (deal_hash,),
            )
            return await cursor.fetchone() is not None

    async def get_last_posted_at(self) -> Optional[float]:
        assert self.db is not None
        async with self._lock:
            cursor = await self.db.execute("SELECT MAX(posted_at) AS ts FROM deals")
            row = await cursor.fetchone()
            return float(row["ts"]) if row and row["ts"] is not None else None

    async def totals(self) -> dict[str, int]:
        assert self.db is not None
        async with self._lock:
            cursor = await self.db.execute(
                """
                SELECT
                    COUNT(*) AS discovered,
                    SUM(CASE WHEN validated_at IS NOT NULL THEN 1 ELSE 0 END) AS validated,
                    SUM(CASE WHEN posted_at IS NOT NULL THEN 1 ELSE 0 END) AS posted
                FROM deals
                """
            )
            row = await cursor.fetchone()
            return {
                "discovered": int(row["discovered"] or 0),
                "validated": int(row["validated"] or 0),
                "posted": int(row["posted"] or 0),
            }

    async def get_state(self, key: str) -> Optional[str]:
        assert self.db is not None
        async with self._lock:
            cursor = await self.db.execute("SELECT value FROM app_state WHERE key = ?", (key,))
            row = await cursor.fetchone()
            return str(row["value"]) if row else None

    async def set_state(self, key: str, value: str) -> None:
        assert self.db is not None
        async with self._lock:
            await self.db.execute(
                "INSERT INTO app_state(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            await self.db.commit()


# -----------------------------------------------------------------------------
# Keepa integration
# -----------------------------------------------------------------------------


class KeepaClient:
    BASE_URL = "https://api.keepa.com"

    def __init__(self, config: Config, http: aiohttp.ClientSession, metrics: RuntimeMetrics) -> None:
        self.config = config
        self.http = http
        self.metrics = metrics

    async def _request_json(self, method: str, path: str, *, params: Optional[dict[str, Any]] = None,
                            json_body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        url = f"{self.BASE_URL}{path}"
        timeout = aiohttp.ClientTimeout(total=self.config.fetch_timeout_seconds)
        async with self.http.request(
            method,
            url,
            params=params,
            json=json_body,
            timeout=timeout,
            headers={"User-Agent": self.config.user_agent, "Accept": "application/json"},
        ) as response:
            text = await response.text()
            if response.status != 200:
                raise RuntimeError(f"Keepa HTTP {response.status}: {text[:300]}")
            try:
                payload = await response.json(content_type=None)
            except Exception as exc:
                raise RuntimeError(f"Keepa returned non-JSON response: {text[:300]}") from exc
            if not isinstance(payload, dict):
                raise RuntimeError("Keepa returned an unexpected response structure")
            if payload.get("error"):
                raise RuntimeError(f"Keepa API error: {payload['error']}")
            return payload

    async def fetch_deals(self) -> list[Deal]:
        self.metrics.last_keepa_fetch_at = time.time()
        query = {
            "page": 0,
            "domainId": self.config.keepa_domain_id,
            "priceTypes": [self.config.keepa_price_type],
            "dateRange": 0,
            "isRangeEnabled": True,
            "currentRange": [1, 10000000],
            "deltaPercentRange": [int(self.config.min_discount), 100],
            "sortType": 4,
        }
        payload = await self._request_json(
            "POST",
            "/deal",
            params={"key": self.config.keepa_api_key},
            json_body=query,
        )
        raw_deals = ((payload.get("deals") or {}).get("dr") or [])[: self.config.keepa_max_deals_per_poll]
        self.metrics.last_keepa_success_at = time.time()

        # Product calls are deliberately sequential to avoid burning API tokens / hammering Keepa.
        results: list[Deal] = []
        for item in raw_deals:
            try:
                asin = str(item.get("asin") or "").strip().upper()
                title = clean_title(str(item.get("title") or ""), self.config.title_max_length)
                if not asin or not title or not re.fullmatch(r"[A-Z0-9]{10}", asin):
                    continue

                current = item.get("current") or []
                if len(current) <= self.config.keepa_price_type:
                    continue
                sale_minor = safe_int(current[self.config.keepa_price_type])
                if sale_minor is None or sale_minor <= 0:
                    continue

                # Product endpoint gives the LISTPRICE series at index 4 in Keepa's price-type arrays.
                product_payload = await self._request_json(
                    "GET",
                    "/product",
                    params={
                        "key": self.config.keepa_api_key,
                        "domain": self.config.keepa_domain_id,
                        "asin": asin,
                        "stats": 1,
                        "history": 0,
                    },
                )
                product_list = product_payload.get("products") or []
                if not product_list:
                    continue
                product = product_list[0]
                stats = product.get("stats") or {}
                stats_current = stats.get("current") or []
                mrp_minor = extract_index(stats_current, 4)
                if mrp_minor is None or mrp_minor <= 0:
                    # Fallback to raw CSV current list-price value where available.
                    mrp_minor = extract_latest_price_from_csv(product.get("csv"), 4)
                if mrp_minor is None or mrp_minor <= 0:
                    continue

                currency_divisor = Decimal("100")
                sale_price = (Decimal(sale_minor) / currency_divisor).quantize(Decimal("0.01"))
                mrp = (Decimal(mrp_minor) / currency_divisor).quantize(Decimal("0.01"))
                discount = calculate_discount(mrp, sale_price)
                if discount < self.config.min_discount:
                    continue

                product_url = f"https://www.amazon.in/dp/{asin}"
                deal_hash = hashlib.sha256(
                    f"amazon:{asin}:{sale_price}:{mrp}".encode("utf-8")
                ).hexdigest()
                results.append(
                    Deal(
                        deal_id=deal_hash,
                        asin=asin,
                        title=title,
                        sale_price=sale_price,
                        mrp=mrp,
                        discount_percent=discount,
                        product_url=product_url,
                        affiliate_url="",
                    )
                )
            except Exception as exc:
                LOGGER.warning("Skipping malformed Keepa deal: %s", exc)

        self.metrics.discovered_this_process += len(raw_deals)
        return results


# -----------------------------------------------------------------------------
# Affiliate conversion
# -----------------------------------------------------------------------------


async def convert_to_earnkaro(url: str, config: Config, http: aiohttp.ClientSession) -> str:
    """
    Convert a product URL into an EarnKaro affiliate/deep link.

    Supported configuration modes, in order:
      1) EARNKARO_DEEPLINK_TEMPLATE, e.g.
         https://your-earned-link-endpoint.example/?url={url}&aff_id={affiliate_id}
      2) EARNKARO_API_URL + EARNKARO_API_KEY (+ optional EARNKARO_AFFILIATE_ID)
         The API receives JSON {"url": ..., "affiliate_id": ...} and must return JSON
         containing one of: affiliate_url, deeplink, short_url, url.
      3) ALLOW_AFFILIATE_PASSTHROUGH=true -> original URL is used.

    This intentionally does NOT invent a proprietary EarnKaro endpoint or undocumented
    parameter names. Configure the actual endpoint/template supplied by your EarnKaro
    account. The API-key values are only read from environment variables.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid product URL: {url}")

    affiliate_id = config.earnkaro_affiliate_id or ""

    if config.earnkaro_deeplink_template:
        try:
            return config.earnkaro_deeplink_template.format(
                url=quote(url, safe=""),
                raw_url=url,
                affiliate_id=quote(affiliate_id, safe=""),
            )
        except KeyError as exc:
            raise ValueError(
                f"EARNKARO_DEEPLINK_TEMPLATE contains an unsupported placeholder: {exc}"
            ) from exc

    if config.earnkaro_api_url and config.earnkaro_api_key:
        timeout = aiohttp.ClientTimeout(total=config.fetch_timeout_seconds)
        headers = {
            "User-Agent": config.user_agent,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.earnkaro_api_key}",
        }
        body = {"url": url}
        if affiliate_id:
            body["affiliate_id"] = affiliate_id
        async with http.post(config.earnkaro_api_url, json=body, headers=headers, timeout=timeout) as response:
            text = await response.text()
            if response.status != 200:
                raise RuntimeError(f"EarnKaro API HTTP {response.status}: {text[:300]}")
            payload = await response.json(content_type=None)
            if not isinstance(payload, dict):
                raise RuntimeError("EarnKaro API returned an unexpected response")
            for key in ("affiliate_url", "deeplink", "short_url", "url"):
                candidate = payload.get(key)
                if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                    return candidate
            raise RuntimeError("EarnKaro API response contained no usable affiliate URL")

    if config.allow_affiliate_passthrough:
        return url

    raise RuntimeError("No valid EarnKaro conversion mode is configured")


# -----------------------------------------------------------------------------
# Validation / network utilities
# -----------------------------------------------------------------------------


async def url_is_reachable(url: str, config: Config, http: aiohttp.ClientSession) -> bool:
    timeout = aiohttp.ClientTimeout(total=config.link_timeout_seconds)
    headers = {"User-Agent": config.user_agent}

    # HEAD first; some retailers reject HEAD, so fallback to GET with a tiny body.
    try:
        async with http.head(
            url,
            allow_redirects=True,
            timeout=timeout,
            headers=headers,
        ) as response:
            if response.status == 200:
                return True
            if response.status in {405, 403}:
                raise aiohttp.ClientResponseError(
                    response.request_info,
                    response.history,
                    status=response.status,
                    message="HEAD not accepted",
                    headers=response.headers,
                )
    except (aiohttp.ClientError, asyncio.TimeoutError):
        pass

    try:
        async with http.get(
            url,
            allow_redirects=True,
            timeout=timeout,
            headers=headers,
        ) as response:
            if response.status != 200:
                return False
            await response.content.read(1024)
            return True
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return False


def calculate_discount(mrp: Decimal, sale_price: Decimal) -> Decimal:
    if mrp <= 0 or sale_price <= 0 or sale_price > mrp:
        return Decimal("-1")
    value = ((mrp - sale_price) / mrp) * Decimal("100")
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def validate_deal_math(deal: Deal, min_discount: Decimal) -> bool:
    if deal.sale_price <= 0 or deal.mrp <= 0 or deal.sale_price > deal.mrp:
        return False
    if deal.discount_percent < min_discount:
        return False
    recomputed = calculate_discount(deal.mrp, deal.sale_price)
    return recomputed == deal.discount_percent


def safe_int(value: Any) -> Optional[int]:
    try:
        ivalue = int(value)
        return ivalue
    except (TypeError, ValueError):
        return None


def extract_index(array: Any, index: int) -> Optional[int]:
    if not isinstance(array, list) or len(array) <= index:
        return None
    value = safe_int(array[index])
    return value if value is not None else None


def extract_latest_price_from_csv(csv_data: Any, index: int) -> Optional[int]:
    if not isinstance(csv_data, list) or len(csv_data) <= index:
        return None
    series = csv_data[index]
    if not isinstance(series, list) or len(series) < 2:
        return None
    # Keepa CSV stores alternating [timestamp, value, timestamp, value, ...].
    for pos in range(len(series) - 1, 0, -2):
        value = safe_int(series[pos])
        if value is not None and value > 0:
            return value
    return None


def clean_title(title: str, max_length: int) -> str:
    title = re.sub(r"\s+", " ", title).strip()
    title = re.sub(r"<[^>]+>", "", title)
    if len(title) <= max_length:
        return title
    return title[: max_length - 1].rstrip() + "…"


def money(value: Decimal) -> str:
    # Indian-style grouping without external libraries.
    s = f"{value.quantize(Decimal('0.01')):.2f}"
    whole, frac = s.split(".")
    sign = ""
    if whole.startswith("-"):
        sign, whole = "-", whole[1:]
    if len(whole) <= 3:
        grouped = whole
    else:
        last3 = whole[-3:]
        rest = whole[:-3]
        chunks = []
        while rest:
            chunks.append(rest[-2:])
            rest = rest[:-2]
        grouped = ",".join(reversed(chunks)) + "," + last3
    return f"{sign}{grouped}.{frac}"


def html_escape(text: str) -> str:
    return html.escape(text, quote=True)


# -----------------------------------------------------------------------------
# UptimeRobot status
# -----------------------------------------------------------------------------


async def query_uptimerobot_status(config: Config, http: aiohttp.ClientSession) -> str:
    api_key = os.getenv("UPTIMEROBOT_API_KEY", "").strip()
    monitor_id = os.getenv("UPTIMEROBOT_MONITOR_ID", "").strip()
    if not api_key:
        return "not configured (set UPTIMEROBOT_API_KEY)"

    url = "https://api.uptimerobot.com/v2/getMonitors"
    form = {"api_key": api_key, "format": "json"}
    if monitor_id:
        form["monitors"] = monitor_id

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with http.post(url, data=form, timeout=timeout) as response:
            if response.status != 200:
                return f"API HTTP {response.status}"
            payload = await response.json(content_type=None)
            monitors = payload.get("monitors") or []
            if not monitors:
                return "configured, monitor not found"
            monitor = monitors[0]
            status_code = monitor.get("status")
            status_map = {0: "paused", 1: "not checked yet", 2: "up", 8: "seems down", 9: "down"}
            label = status_map.get(status_code, f"status {status_code}")
            return f"{label} ({monitor.get('friendly_name', 'monitor')})"
    except Exception as exc:
        return f"unavailable ({type(exc).__name__})"


# -----------------------------------------------------------------------------
# Bot service
# -----------------------------------------------------------------------------


class DealBot:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.db = Database(config.db_path)
        self.metrics = RuntimeMetrics(started_at=time.time())
        self.http: Optional[aiohttp.ClientSession] = None
        self.keepa: Optional[KeepaClient] = None
        self.application: Optional[Application] = None
        self.web_runner: Optional[web.AppRunner] = None
        self.stop_event = asyncio.Event()
        self.worker_task: Optional[asyncio.Task[None]] = None
        self.paused = False
        self._posting_lock = asyncio.Lock()

    async def start(self) -> None:
        await self.db.connect()
        paused_state = await self.db.get_state("paused")
        self.paused = paused_state == "1"

        connector = aiohttp.TCPConnector(limit=30, ttl_dns_cache=300, enable_cleanup_closed=True)
        self.http = aiohttp.ClientSession(connector=connector)
        self.keepa = KeepaClient(self.config, self.http, self.metrics)

        self.application = (
            Application.builder()
            .token(self.config.telegram_bot_token)
            .post_init(self._telegram_post_init)
            .post_shutdown(self._telegram_post_shutdown)
            .build()
        )
        self.application.add_handler(CommandHandler("status", self.cmd_status))
        self.application.add_handler(CommandHandler("pause", self.cmd_pause))
        self.application.add_handler(CommandHandler("resume", self.cmd_resume))

        # Initialize and start the PTB application without taking over the main event loop.
        await self.application.initialize()
        if self.application.updater is None:
            raise RuntimeError("Telegram updater is unavailable")
        await self.application.updater.start_polling(
            allowed_updates=["message"],
            drop_pending_updates=True,
        )
        await self.application.start()

        await self._start_web_server()
        self.worker_task = asyncio.create_task(self.deal_worker(), name="deal-worker")
        LOGGER.info(
            "Bot started. channel=%s admin=%s port=%s paused=%s",
            self.config.channel_id,
            self.config.admin_user_id,
            self.config.port,
            self.paused,
        )

    async def stop(self) -> None:
        self.stop_event.set()
        if self.worker_task:
            self.worker_task.cancel()
            await asyncio.gather(self.worker_task, return_exceptions=True)
            self.worker_task = None

        if self.application and self.application.updater:
            try:
                await self.application.updater.stop()
            except Exception:
                LOGGER.exception("Failed to stop Telegram updater cleanly")
        if self.application:
            try:
                await self.application.stop()
            except Exception:
                LOGGER.exception("Failed to stop Telegram application cleanly")
            try:
                await self.application.shutdown()
            except Exception:
                LOGGER.exception("Failed to shutdown Telegram application cleanly")

        if self.web_runner:
            await self.web_runner.cleanup()
            self.web_runner = None
        if self.http:
            await self.http.close()
            self.http = None
        await self.db.close()
        LOGGER.info("Bot stopped")

    async def _telegram_post_init(self, application: Application) -> None:
        LOGGER.info("Telegram application initialized")

    async def _telegram_post_shutdown(self, application: Application) -> None:
        LOGGER.info("Telegram application shutdown")

    async def _start_web_server(self) -> None:
        app = web.Application()
        app.router.add_get("/", self.health)
        app.router.add_get("/health", self.health)
        app.router.add_get("/status", self.public_status)

        self.web_runner = web.AppRunner(app, access_log=LOGGER)
        await self.web_runner.setup()
        site = web.TCPSite(self.web_runner, host="0.0.0.0", port=self.config.port)
        await site.start()
        LOGGER.info("HTTP server listening on 0.0.0.0:%s", self.config.port)

    async def health(self, request: web.Request) -> web.Response:
        # Any successful inbound UptimeRobot/render request is itself useful telemetry.
        self.metrics.last_uptime_robot_ping_at = time.time()
        self.metrics.uptime_robot_last_status = "inbound ping received"
        return web.json_response(
            {
                "ok": True,
                "service": "telegram-flash-deals-bot",
                "uptime_seconds": round(time.time() - self.metrics.started_at, 1),
                "paused": self.paused,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    async def public_status(self, request: web.Request) -> web.Response:
        totals = await self.db.totals()
        return web.json_response(
            {
                "ok": True,
                "uptime_seconds": round(time.time() - self.metrics.started_at, 1),
                "paused": self.paused,
                "metrics": totals,
                "last_keepa_success_at": iso_ts(self.metrics.last_keepa_success_at),
                "last_inbound_ping_at": iso_ts(self.metrics.last_uptime_robot_ping_at),
                "last_error": self.metrics.last_error,
            }
        )

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not is_admin(update, self.config.admin_user_id):
            return
        totals = await self.db.totals()
        uptime_robot = "not configured"
        if self.http:
            uptime_robot = await query_uptimerobot_status(self.config, self.http)
        uptime = format_duration(time.time() - self.metrics.started_at)
        last_post = await self.db.get_last_posted_at()
        cooldown_remaining = 0
        if last_post:
            cooldown_remaining = max(0, self.config.cooldown_seconds - int(time.time() - last_post))
        message = (
            "<b>🤖 Flash Deals Bot Status</b>\n"
            f"Uptime: <b>{html_escape(uptime)}</b>\n"
            f"Paused: <b>{'YES' if self.paused else 'NO'}</b>\n"
            f"UptimeRobot: <b>{html_escape(uptime_robot)}</b>\n"
            f"Deals discovered: <b>{totals['discovered']}</b>\n"
            f"Deals validated: <b>{totals['validated']}</b>\n"
            f"Deals posted: <b>{totals['posted']}</b>\n"
            f"Process discovered: <b>{self.metrics.discovered_this_process}</b>\n"
            f"Process validated: <b>{self.metrics.validated_this_process}</b>\n"
            f"Process posted: <b>{self.metrics.posted_this_process}</b>\n"
            f"Last post cooldown: <b>{cooldown_remaining}s</b>\n"
            f"Last source success: <b>{html_escape(iso_ts(self.metrics.last_keepa_success_at) or 'never')}</b>"
        )
        await reply_admin(update, message)

    async def cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not is_admin(update, self.config.admin_user_id):
            return
        self.paused = True
        await self.db.set_state("paused", "1")
        await reply_admin(update, "⏸️ <b>Deal posting paused.</b>\nThe bot will keep fetching/validating data but will not post.")

    async def cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not is_admin(update, self.config.admin_user_id):
            return
        self.paused = False
        await self.db.set_state("paused", "0")
        await reply_admin(update, "▶️ <b>Deal posting resumed.</b>")

    async def deal_worker(self) -> None:
        assert self.keepa is not None
        while not self.stop_event.is_set():
            try:
                deals = await self.keepa.fetch_deals()
                for raw_deal in deals:
                    if self.stop_event.is_set():
                        break
                    if await self.db.is_posted(raw_deal.deal_id):
                        continue

                    # Convert before recording discovery so a transient affiliate failure
                    # does not permanently suppress the deal.
                    try:
                        affiliate_url = await convert_to_earnkaro(
                            raw_deal.product_url, self.config, self.http  # type: ignore[arg-type]
                        )
                    except Exception as exc:
                        LOGGER.warning("Affiliate conversion failed for %s: %s", raw_deal.asin, exc)
                        continue

                    await self.db.record_discovered(raw_deal)

                    deal = Deal(
                        deal_id=raw_deal.deal_id,
                        asin=raw_deal.asin,
                        title=raw_deal.title,
                        sale_price=raw_deal.sale_price,
                        mrp=raw_deal.mrp,
                        discount_percent=raw_deal.discount_percent,
                        product_url=raw_deal.product_url,
                        affiliate_url=affiliate_url,
                    )

                    if not validate_deal_math(deal, self.config.min_discount):
                        LOGGER.info("Rejected deal %s: math/discount validation failed", deal.asin)
                        continue

                    if not await url_is_reachable(deal.product_url, self.config, self.http):  # type: ignore[arg-type]
                        LOGGER.info("Rejected deal %s: product URL not reachable", deal.asin)
                        continue

                    await self.db.mark_validated(deal.deal_id)
                    self.metrics.validated_this_process += 1

                    if self.paused:
                        continue
                    await self._try_post(deal)

                self.metrics.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.metrics.last_error = f"{type(exc).__name__}: {exc}"
                LOGGER.exception("Deal worker iteration failed")

            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.config.poll_seconds)
            except asyncio.TimeoutError:
                continue

    async def _try_post(self, deal: Deal) -> None:
        assert self.application is not None
        async with self._posting_lock:
            last_posted = await self.db.get_last_posted_at()
            if last_posted is not None:
                elapsed = time.time() - last_posted
                if elapsed < self.config.cooldown_seconds:
                    LOGGER.info(
                        "Cooldown active; skipping %s for %.1fs",
                        deal.asin,
                        self.config.cooldown_seconds - elapsed,
                    )
                    return

            text = render_deal_html(deal)
            try:
                await self.application.bot.send_message(
                    chat_id=self.config.channel_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False,
                )
            except TelegramError as exc:
                LOGGER.error("Telegram post failed for %s: %s", deal.asin, exc)
                return

            await self.db.mark_posted(deal.deal_id)
            self.metrics.posted_this_process += 1
            LOGGER.info("Posted deal %s (%s)", deal.asin, deal.title)


def render_deal_html(deal: Deal) -> str:
    return (
        "🔥 FLASH DEAL\n\n"
        f"📦 <b>{html_escape(deal.title)}</b>\n"
        f"💰 Deal Price: ₹{money(deal.sale_price)}\n"
        f"🏷️ MRP: <s>₹{money(deal.mrp)}</s>\n"
        f"📉 Discount: <b>{deal.discount_percent:.2f}% OFF</b>\n"
        f"🛒 <b>Buy Now:</b> <a href=\"{html_escape(deal.affiliate_url)}\">Click Here to Buy</a>\n\n"
        "#FlashDeal #Deals #Shopping"
    )


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def is_admin(update: Update, admin_user_id: int) -> bool:
    user = update.effective_user
    return bool(user and user.id == admin_user_id)


async def reply_admin(update: Update, text: str) -> None:
    message = update.effective_message
    if message:
        await message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


def iso_ts(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m {secs}s"
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


# -----------------------------------------------------------------------------
# Entrypoint / signal handling
# -----------------------------------------------------------------------------


async def main() -> None:
    config = Config.from_env()
    service = DealBot(config)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, service.stop_event.set)
        except NotImplementedError:
            # Windows development fallback; Render Linux supports signal handlers.
            pass

    await service.start()
    try:
        await service.stop_event.wait()
    finally:
        await service.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
