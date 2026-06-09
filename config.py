"""
Configuration for the 5-Minute Bollinger Band Reversal Bot with Trailing Stop & INR Daily Risk Limits.

Strategy: Mean-reversion on 5-min chart using Bollinger Bands (20, 2σ).
- Long when green candle closes near lower band
- Short when red candle closes near upper band
- Trailing stop protects profits
- INR-based position sizing and daily risk limits
- IST trading sessions only

All data sourced exclusively from sharkexchange.in — NO Binance.
"""

import os
from dotenv import load_dotenv

load_dotenv()

EXCHANGE_NAME = "sharkexchange"
EXCHANGE_BASE_URL = "https://api.sharkexchange.in"

SHARK_API_KEY = os.getenv("SHARK_API_KEY", "")
SHARK_API_SECRET = os.getenv("SHARK_API_SECRET", "")

SANDBOX_MODE = False

SHARK_ENDPOINTS = {
    "KLINES":           "/v1/market/klines",
    "TICKER24H":        "/v1/market/ticker24Hr",
    "DEPTH":            "/v1/market/depth",
    "AGG_TRADE":        "/v1/market/aggTrade",
    "EXCHANGE_INFO":    "/v1/exchange/exchangeInfo",
    "PLACE_ORDER":      "/v1/order/place-order",
    "EDIT_ORDER":       "/v1/order/edit-order",
    "DELETE_ORDER":     "/v1/order/delete-order",
    "CANCEL_ALL":       "/v1/order/cancel-all-orders",
    "OPEN_ORDERS":      "/v1/order/open-orders",
    "ORDER_HISTORY":    "/v1/order/order-history",
    "LINKED_ORDERS":    "/v1/order/linked-orders",
    "GET_MULTIPLE":     "/v1/order/get-multiple",
    "ADD_MARGIN":       "/v1/order/add-margin",
    "REDUCE_MARGIN":    "/v1/order/reduce-margin",
    "SPLIT_TPSL":       "/v2/order/split-tp-sl",
    "POSITIONS":        "/v1/positions/open-positions",
    "POSITION_STATUS":  "/v1/positions/position-status",
    "CLOSE_ALL_POS":    "/v1/positions/close-all-positions",
    "FUTURES_WALLET":   "/v1/wallet/futures-wallet/details",
    "FUNDING_WALLET":   "/v1/wallet/funding-wallet/details",
    "USER_INFO":        "/v1/user/userInfo",
    "UPDATE_PREFERENCE":"/v1/exchange/update/preference",
    "UPDATE_LEVERAGE":  "/v1/exchange/update/leverage",
    "CREATE_LISTEN_KEY":"/v1/user/createListenKey",
    "GET_LISTEN_KEY":   "/v1/user/getListenKey",
    "UPDATE_LISTEN_KEY":"/v1/user/updateListenKey",
    "DELETE_LISTEN_KEY":"/v1/user/deleteListenKey",
}

SYMBOL = "BTCUSDT"
TIMEFRAME = "5m"
DATA_WINDOW = 50

BB_PERIOD = 20
BB_STD_DEV = 2
NEAR_THRESHOLD = 0.002

TRAIL_PCT = 0.005
STOP_LIMIT_SLIPPAGE = 0.001

TRADE_INR = 20000
USD_INR_RATE = 83.5
MAX_DAILY_LOSS_INR = 3000
MAX_TRADES_PER_DAY = 30
COOLDOWN_MINUTES = 5
CLOSE_ON_SESSION_END = False

TRADING_SESSIONS = [
    {"name": "Morning",   "start_hour": 9,  "start_min": 30, "end_hour": 12, "end_min": 0},
    {"name": "Afternoon", "start_hour": 13, "start_min": 0,  "end_hour": 15, "end_min": 30},
    {"name": "Evening",   "start_hour": 19, "start_min": 0,  "end_hour": 22, "end_min": 0},
]

DASHBOARD_HOST = "0.0.0.0"
DASHBOARD_PORT = 5000
DASHBOARD_DEBUG = False

LOG_LEVEL = "INFO"
LOG_FILE = "trading_bot.log"

LOOP_INTERVAL_SECONDS = 5
INITIAL_CAPITAL_INR = 100000