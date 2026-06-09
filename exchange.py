"""
Exchange connection handler for the 5-Minute BB Reversal Bot.

Architecture:
  - sharkexchange.in API for ALL data (market data, candles, ticker, trading)
  - NO Binance — everything comes from sharkexchange.in
  - Auth: API Key + HMAC-SHA256 signature (NOT JWT)
  - Public endpoints (klines, marketInfo, exchangeInfo) work without auth
  - Authenticated endpoints (orders, wallet, positions) require api-key + signature
"""

import logging
import time
import json
import hmac
import hashlib
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlencode
import config

logger = logging.getLogger(__name__)


def generate_signature(api_secret: str, data_to_sign: str) -> str:
    """Generate HMAC-SHA256 signature for request authentication.

    For GET requests: data_to_sign is the query string (url?params)
    For POST/PUT/DELETE: data_to_sign is the JSON body
    """
    return hmac.new(
        api_secret.encode('utf-8'),
        data_to_sign.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()


def _safe_float(value, default: float = 0.0) -> float:
    """Safely convert a value to float, handling None and empty strings."""
    if value is None or value == '':
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _extract_data(response_json):
    """Extract actual payload from possible API wrapper formats.

    SharkExchange may return data in several formats:
      - Direct: [{...}] or {...}
      - Wrapped: {"data": [...]} or {"result": {...}} or {"success": true, "data": [...]}
    """
    if not isinstance(response_json, dict):
        return response_json

    # If it's a dict with a 'data' key, unwrap it
    if 'data' in response_json:
        return response_json['data']

    # If it's a dict with a 'result' key, unwrap it
    if 'result' in response_json:
        return response_json['result']

    # If it has 'success' but no data/result wrapper, return the dict itself
    return response_json


def _get_margin_asset(symbol: str) -> str:
    """Derive margin asset from symbol (e.g. BTCUSDT -> USDT, BTCINR -> INR)."""
    if symbol.endswith('INR'):
        return 'INR'
    if symbol.endswith('USDT'):
        return 'USDT'
    return 'USDT'


def _round_quantity(symbol: str, quantity: float) -> float:
    """Round quantity to exchange-acceptable precision."""
    if quantity <= 0:
        return 0.0
    if symbol.startswith('BTC'):
        return round(quantity, 6)
    if symbol.startswith('ETH'):
        return round(quantity, 5)
    return round(quantity, 4)


class SharkExchangeData:
    """Fetches real market data from sharkexchange.in public API (no auth needed).

    Working endpoints:
      - POST /v1/market/klines  → candle data {pair, interval, limit} (no priceType!)
      - Ticker24Hr endpoint is NOT available — derive price from latest candle
    """

    def __init__(self):
        self.base_url = config.EXCHANGE_BASE_URL
        self.endpoints = config.SHARK_ENDPOINTS
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        logger.info("SharkExchangeData initialized — will fetch real prices from sharkexchange.in")

    def fetch_ohlcv(self, pair: str = "BTCUSDT", interval: str = "5m", limit: int = 50):
        """Fetch real OHLCV candle data from sharkexchange.in.

        Uses POST /v1/market/klines
        Body: {pair, interval, limit} — note: no priceType field
        Response format: [{startTime, open, high, low, close, endTime, volume}, ...]
        """
        try:
            url = self.base_url + self.endpoints["KLINES"]
            body = {
                "pair": pair,
                "interval": interval,
                "limit": limit,
            }
            resp = self.session.post(url, json=body, timeout=15)
            if resp.status_code not in (200, 201):
                logger.error(f"Klines request failed: {resp.status_code} {resp.text[:500]}")
                return []

            raw = _extract_data(resp.json())
            if not isinstance(raw, list):
                logger.error(f"Klines response is not a list: {type(raw)} — body: {resp.text[:300]}")
                return []

            candles = []
            for item in raw:
                candles.append({
                    "timestamp": int(item.get("startTime", 0)),
                    "open": _safe_float(item.get("open")),
                    "high": _safe_float(item.get("high")),
                    "low": _safe_float(item.get("low")),
                    "close": _safe_float(item.get("close")),
                    "volume": _safe_float(item.get("volume")),
                    "close_time": int(item.get("endTime", 0)),
                })

            logger.debug(f"Fetched {len(candles)} real {interval} candles from sharkexchange.in")
            return candles

        except Exception as e:
            logger.error(f"Failed to fetch OHLCV from sharkexchange.in: {e}")
            return []

    def fetch_ticker(self, symbol: str = "BTCUSDT"):
        """Fetch current price from sharkexchange.in.

        The ticker24Hr endpoint does NOT exist on this exchange.
        We derive the current price from the latest 1m kline candle instead.
        Fetches 2 candles so we can also compute 24h change.
        """
        try:
            candles = self.fetch_ohlcv(pair=symbol, interval="1m", limit=2)
            if candles and len(candles) > 0:
                price = candles[-1]["close"]
                prev_price = candles[-2]["close"] if len(candles) >= 2 else price
                change_pct = ((price - prev_price) / prev_price * 100) if prev_price > 0 else 0.0
                return {
                    "symbol": symbol,
                    "price": price,
                    "priceChangePercent": round(change_pct, 2),
                    "volume": candles[-1].get("volume", 0),
                    "quoteVolume": 0.0,
                    "timestamp": int(time.time() * 1000),
                }
            return {"symbol": symbol, "price": 0.0, "timestamp": 0}
        except Exception as e:
            logger.error(f"Failed to fetch price from sharkexchange.in: {e}")
            return {"symbol": symbol, "price": 0.0, "timestamp": 0}

    def fetch_all_tickers(self):
        """Fetch prices for known trading pairs from sharkexchange.in (parallelized).

        Since ticker24Hr is not available, we fetch 1m klines for each known pair.
        Uses ThreadPoolExecutor to parallelize requests — dramatically faster.
        """
        known_pairs = [
            "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
            "ADAUSDT", "DOGEUSDT", "DOTUSDT", "MATICUSDT", "AVAXUSDT",
            "BTCINR", "ETHINR", "SOLINR",
        ]
        tickers = {}

        def _fetch_one(pair):
            try:
                candles = self.fetch_ohlcv(pair=pair, interval="1m", limit=2)
                if candles and len(candles) >= 1:
                    price = candles[-1]["close"]
                    prev_price = candles[-2]["close"] if len(candles) >= 2 else price
                    change_pct = ((price - prev_price) / prev_price * 100) if prev_price > 0 else 0
                    return pair, {
                        "symbol": pair,
                        "price": price,
                        "priceChangePercent": round(change_pct, 2),
                        "volume": candles[-1].get("volume", 0),
                    }
            except Exception as e:
                logger.debug(f"Failed to fetch {pair}: {e}")
            return pair, None

        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {executor.submit(_fetch_one, pair): pair for pair in known_pairs}
            for future in as_completed(futures):
                pair, result = future.result()
                if result is not None:
                    tickers[pair] = result

        return tickers

    def fetch_exchange_info(self):
        """Fetch exchange info (all contracts, filters, markets) from sharkexchange.in.

        Uses GET /v1/exchange/exchangeInfo
        Response: {markets: ["INR", "USDT"], contracts: [{name, ...}, ...]}
        """
        try:
            url = self.base_url + self.endpoints["EXCHANGE_INFO"]
            resp = self.session.get(url, timeout=15)
            if resp.status_code != 200:
                logger.error(f"ExchangeInfo request failed: {resp.status_code}")
                return {}

            data = _extract_data(resp.json())
            contracts = data.get('contracts', []) if isinstance(data, dict) else []
            logger.info(f"Exchange info: {len(contracts)} contracts")
            return data if isinstance(data, dict) else {}

        except Exception as e:
            logger.error(f"Failed to fetch exchange info from sharkexchange.in: {e}")
            return {}


class SharkExchangeTrader:
    """Handles authenticated trading operations on sharkexchange.in.

    Auth: API Key + HMAC-SHA256 signature
    For GET requests: sign the query string
    For POST/PUT/DELETE: sign the JSON body (with timestamp)
    Headers: api-key, signature
    """

    def __init__(self):
        self.api_key = config.SHARK_API_KEY
        self.api_secret = config.SHARK_API_SECRET
        self.base_url = config.EXCHANGE_BASE_URL
        self.endpoints = config.SHARK_ENDPOINTS
        self.connected = False
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self._check_auth()

    def _check_auth(self):
        """Check if API credentials are configured and valid.

        We test against /v1/order/open-orders which is an authenticated endpoint.
        - 200 → fully connected
        - 403 "IP address not whitelisted" → keys are valid, user needs to whitelist IP
        - 401 → keys are invalid
        """
        if not self.api_key or not self.api_secret:
            logger.warning("No sharkexchange.in API_KEY/API_SECRET configured — trading is DISABLED. Set API_KEY and API_SECRET in .env file.")
            self.connected = False
            return

        try:
            # Build the signed request directly (can't use _signed_get since connected=False during init)
            timestamp = str(int(time.time() * 1000))
            query_params = {'timestamp': timestamp}
            query_string = urlencode(query_params)
            signature = generate_signature(self.api_secret, query_string)

            url = self.base_url + self.endpoints["OPEN_ORDERS"]
            test_resp = self.session.get(
                url,
                params=query_params,
                headers={
                    "api-key": self.api_key,
                    "signature": signature,
                },
                timeout=10
            )

            status = test_resp.status_code
            body = test_resp.text[:500]

            if status == 200:
                self.connected = True
                logger.info("SharkExchangeTrader connected — API credentials validated")

            elif status == 403 and 'not whitelisted' in body.lower():
                self.connected = False
                logger.warning(
                    f"SharkExchange API: keys are valid but IP not whitelisted. "
                    f"Your IP must be whitelisted in your exchange account settings. "
                    f"Trading is DISABLED until IP is whitelisted."
                )

            elif status == 401:
                self.connected = False
                logger.error("SharkExchange API authentication FAILED (401). Check your API key and secret.")

            else:
                self.connected = False
                logger.warning(f"SharkExchange API credential test returned {status}. Response: {body}")

        except Exception as e:
            logger.error(f"SharkExchange API credential test error: {e}")
            self.connected = False

    def _signed_get(self, endpoint: str, params: dict = None):
        """Make an authenticated GET request.

        For GET: signature is HMAC-SHA256 of query string (url?params).
        We use urlencode with insertion order to match JS URLSearchParams behaviour.
        """
        if not self.connected:
            return None

        timestamp = str(int(time.time() * 1000))
        query_params = (params or {}).copy()
        query_params['timestamp'] = timestamp

        # Build query string in insertion order (matches JS URLSearchParams)
        query_string = urlencode(query_params)
        signature = generate_signature(self.api_secret, query_string)

        url = self.base_url + endpoint
        try:
            resp = self.session.get(
                url,
                params=query_params,
                headers={
                    "api-key": self.api_key,
                    "signature": signature,
                },
                timeout=10
            )
            if resp.status_code == 401:
                logger.warning("Signature validation failed (401) — check API key/secret or clock sync")
            return resp
        except Exception as e:
            logger.error(f"Signed GET error: {e}")
            return None

    def _signed_post(self, endpoint: str, body: dict):
        """Make an authenticated POST request.

        For POST: signature is HMAC-SHA256 of JSON body (with timestamp).
        CRITICAL: the raw JSON bytes sent over the wire must be IDENTICAL
        to the string used for signature generation. We therefore compute
        compact JSON once, sign it, and send the exact same string.
        """
        if not self.connected:
            return None

        timestamp = str(int(time.time() * 1000))
        body_with_ts = body.copy()
        body_with_ts['timestamp'] = timestamp

        # Compact JSON must match what we send on the wire
        body_json = json.dumps(body_with_ts, separators=(',', ':'))
        signature = generate_signature(self.api_secret, body_json)

        url = self.base_url + endpoint
        try:
            resp = self.session.post(
                url,
                data=body_json,
                headers={
                    "api-key": self.api_key,
                    "signature": signature,
                    "Content-Type": "application/json",
                },
                timeout=10
            )
            if resp.status_code == 401:
                logger.warning("Signature validation failed (401) — check API key/secret")
            return resp
        except Exception as e:
            logger.error(f"Signed POST error: {e}")
            return None

    def _signed_delete(self, endpoint: str, body: dict):
        """Make an authenticated DELETE request."""
        if not self.connected:
            return None

        timestamp = str(int(time.time() * 1000))
        body_with_ts = body.copy()
        body_with_ts['timestamp'] = timestamp

        body_json = json.dumps(body_with_ts, separators=(',', ':'))
        signature = generate_signature(self.api_secret, body_json)

        url = self.base_url + endpoint
        try:
            resp = self.session.delete(
                url,
                data=body_json,
                headers={
                    "api-key": self.api_key,
                    "signature": signature,
                    "Content-Type": "application/json",
                },
                timeout=10
            )
            return resp
        except Exception as e:
            logger.error(f"Signed DELETE error: {e}")
            return None

    def create_market_order(self, symbol: str, side: str, quantity: float):
        """Place a market order on sharkexchange.in.

        Uses POST /v1/order/place-order
        Body: {placeType, quantity, side, symbol, type, reduceOnly, marginAsset}
        """
        if not self.connected:
            return None

        qty = _round_quantity(symbol, quantity)
        if qty <= 0:
            logger.error(f"Invalid order quantity after rounding: {qty}")
            return None

        margin_asset = _get_margin_asset(symbol)

        body = {
            "placeType": "ORDER_FORM",
            "quantity": qty,
            "side": side.upper(),
            "symbol": symbol,
            "type": "MARKET",
            "reduceOnly": False,
            "marginAsset": margin_asset,
        }

        resp = self._signed_post(self.endpoints["PLACE_ORDER"], body)
        if resp and resp.status_code in (200, 201):
            order = _extract_data(resp.json())
            order_id = order.get('clientOrderId') if isinstance(order, dict) else None
            if not order_id:
                order_id = order.get('id') if isinstance(order, dict) else '?'
            logger.info(f"Market order placed: {side} {qty} {symbol} — id={order_id}")
            return order
        elif resp:
            logger.error(f"Market order failed: {resp.status_code} — {resp.text[:500]}")
        return None

    def create_limit_order(self, symbol: str, side: str, quantity: float, price: float):
        """Place a limit order on sharkexchange.in."""
        if not self.connected:
            return None

        qty = _round_quantity(symbol, quantity)
        if qty <= 0:
            return None

        margin_asset = _get_margin_asset(symbol)

        body = {
            "placeType": "ORDER_FORM",
            "quantity": qty,
            "side": side.upper(),
            "symbol": symbol,
            "type": "LIMIT",
            "price": price,
            "reduceOnly": False,
            "marginAsset": margin_asset,
        }

        resp = self._signed_post(self.endpoints["PLACE_ORDER"], body)
        if resp and resp.status_code in (200, 201):
            order = _extract_data(resp.json())
            logger.info(f"Limit order placed: {side} {qty} {symbol} @ {price}")
            return order
        elif resp:
            logger.error(f"Limit order failed: {resp.status_code} — {resp.text[:500]}")
        return None

    def create_stop_market_order(self, symbol: str, side: str, quantity: float, stop_price: float):
        """Place a stop-market (reduce-only) order on sharkexchange.in.

        Uses reduceOnly: True so the stop order only reduces/closes an existing
        position rather than opening a new one in the opposite direction.
        """
        if not self.connected:
            return None

        qty = _round_quantity(symbol, quantity)
        if qty <= 0:
            return None

        margin_asset = _get_margin_asset(symbol)

        body = {
            "placeType": "ORDER_FORM",
            "quantity": qty,
            "side": side.upper(),
            "symbol": symbol,
            "type": "STOP_MARKET",
            "stopPrice": stop_price,
            "reduceOnly": True,
            "marginAsset": margin_asset,
        }

        resp = self._signed_post(self.endpoints["PLACE_ORDER"], body)
        if resp and resp.status_code in (200, 201):
            order = _extract_data(resp.json())
            logger.info(f"Stop-market order placed: {side} {qty} {symbol} @ stop={stop_price}")
            return order
        elif resp:
            logger.error(f"Stop-market order failed: {resp.status_code} — {resp.text[:500]}")
        return None

    def cancel_order(self, symbol: str, client_order_id: str):
        """Cancel an existing order on sharkexchange.in."""
        if not self.connected:
            return None

        body = {
            "clientOrderId": client_order_id,
        }

        resp = self._signed_delete(self.endpoints["DELETE_ORDER"], body)
        if resp and resp.status_code in (200, 201):
            logger.info(f"Order {client_order_id} cancelled on sharkexchange.in")
            return _extract_data(resp.json())
        elif resp:
            logger.error(f"Cancel order failed: {resp.status_code} — {resp.text[:500]}")
        return None

    def fetch_open_orders(self, symbol: str = None):
        """Fetch open orders from sharkexchange.in."""
        if not self.connected:
            return []

        params = {}
        if symbol:
            params['symbol'] = symbol

        resp = self._signed_get(self.endpoints["OPEN_ORDERS"], params)
        if resp and resp.status_code == 200:
            data = _extract_data(resp.json())
            return data if isinstance(data, list) else []
        return []

    def fetch_positions(self):
        """Fetch open positions from sharkexchange.in."""
        if not self.connected:
            return []

        resp = self._signed_get(self.endpoints["POSITIONS"])
        if resp and resp.status_code == 200:
            data = _extract_data(resp.json())
            return data if isinstance(data, list) else []
        return []

    def fetch_balance(self):
        """Fetch futures wallet balance from sharkexchange.in."""
        if not self.connected:
            return None

        resp = self._signed_get(self.endpoints["FUTURES_WALLET"])
        if resp and resp.status_code == 200:
            return _extract_data(resp.json())
        elif resp:
            logger.error(f"Fetch balance failed: {resp.status_code} — {resp.text[:500]}")
        return None

    def is_connected(self) -> bool:
        """Check if exchange is connected for real trading."""
        return self.connected


class ExchangeManager:
    """
    Unified exchange manager — ALL data from sharkexchange.in ONLY.
    REAL TRADING ONLY — no paper/demo mode.

    - SharkExchangeData: real market data (klines, ticker, exchangeInfo) — public, no auth
    - SharkExchangeTrader: authenticated trading on sharkexchange.in
    """

    def __init__(self):
        self.data_source = SharkExchangeData()
        self.trader = SharkExchangeTrader()
        if not self.trader.is_connected():
            logger.warning("ExchangeManager: trader NOT connected — API keys may be missing/invalid. "
                           "Trading will fail until valid credentials are provided.")
        else:
            logger.info("ExchangeManager initialized — data: sharkexchange.in (real), trading: LIVE")

    def fetch_ohlcv(self, symbol: str = config.SYMBOL, timeframe: str = config.TIMEFRAME,
                   limit: int = config.DATA_WINDOW):
        """Fetch real OHLCV candle data from sharkexchange.in."""
        return self.data_source.fetch_ohlcv(pair=symbol, interval=timeframe, limit=limit)

    def fetch_ticker(self, symbol: str = config.SYMBOL):
        """Fetch real current price from sharkexchange.in."""
        return self.data_source.fetch_ticker(symbol)

    def fetch_all_tickers(self):
        """Fetch ALL pair prices from sharkexchange.in."""
        return self.data_source.fetch_all_tickers()

    def fetch_exchange_info(self):
        """Fetch exchange info from sharkexchange.in."""
        return self.data_source.fetch_exchange_info()

    def get_current_price(self, symbol: str = config.SYMBOL) -> float:
        """Get the current real BTC/USDT price from sharkexchange.in."""
        ticker = self.fetch_ticker(symbol)
        return ticker.get("price", 0.0)

    def create_market_order(self, symbol: str, side: str, quantity: float):
        """Place a REAL market order on sharkexchange.in. Raises RuntimeError if not connected."""
        if not self.trader.is_connected():
            raise RuntimeError("Cannot place market order: exchange not connected. Check API keys.")
        return self.trader.create_market_order(symbol, side, quantity)

    def create_stop_loss_order(self, symbol: str, side: str, quantity: float,
                                stop_price: float, limit_price: float = None):
        """Place a REAL stop-loss order on sharkexchange.in. Raises RuntimeError if not connected."""
        if not self.trader.is_connected():
            raise RuntimeError("Cannot place stop-loss order: exchange not connected. Check API keys.")
        return self.trader.create_stop_market_order(symbol, side, quantity, stop_price)

    def cancel_order(self, symbol: str, order_id: str):
        """Cancel a REAL order on sharkexchange.in. Raises RuntimeError if not connected."""
        if not self.trader.is_connected():
            raise RuntimeError("Cannot cancel order: exchange not connected. Check API keys.")
        return self.trader.cancel_order(symbol, order_id)

    def fetch_balance(self):
        """Fetch REAL account balance from sharkexchange.in. Raises RuntimeError if not connected."""
        if not self.trader.is_connected():
            raise RuntimeError("Cannot fetch balance: exchange not connected. Check API keys.")
        return self.trader.fetch_balance()

    def fetch_positions(self):
        """Fetch REAL open positions from sharkexchange.in. Raises RuntimeError if not connected."""
        if not self.trader.is_connected():
            raise RuntimeError("Cannot fetch positions: exchange not connected. Check API keys.")
        return self.trader.fetch_positions()

    def is_paper_trading(self) -> bool:
        """Paper trading is permanently disabled. Always returns False."""
        return False

    def is_connected(self) -> bool:
        """Check if real exchange is connected."""
        return self.trader.is_connected()
