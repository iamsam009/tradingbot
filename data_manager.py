"""
Data manager for the 5-Minute BB Reversal Bot.

Fetches real 5m BTC/USDT candles from sharkexchange.in, stores as DataFrame,
and calculates Bollinger Bands (20-period, 2σ).

All data sourced exclusively from sharkexchange.in — NO Binance.
"""

import logging
import time
import pandas as pd
import numpy as np
import config

logger = logging.getLogger(__name__)


class DataManager:
    """Manages real BTC/USDT market data from sharkexchange.in and Bollinger Band calculations."""

    def __init__(self, exchange):
        self.exchange = exchange
        self.candles_5m = pd.DataFrame()
        self.last_update_time = 0
        self._initialize_data()

    def _initialize_data(self):
        """Fetch initial candle data on startup."""
        try:
            self._fetch_and_store_5m(limit=config.DATA_WINDOW)
            count = len(self.candles_5m)
            if count == 0:
                logger.warning("No initial candle data — sharkexchange.in may be unreachable. Retrying in 5s...")
                time.sleep(5)
                self._fetch_and_store_5m(limit=config.DATA_WINDOW)
                count = len(self.candles_5m)
            logger.info(f"Initial data loaded: {count} 5m candles from sharkexchange.in")
        except Exception as e:
            logger.error(f"Failed to load initial data: {e}")

    def _fetch_and_store_5m(self, limit: int = config.DATA_WINDOW):
        """Fetch 5m candles from sharkexchange.in and store as DataFrame."""
        raw_candles = self.exchange.fetch_ohlcv(config.SYMBOL, config.TIMEFRAME, limit)
        if not raw_candles:
            logger.warning("No 5m candle data received from sharkexchange.in")
            return

        self.candles_5m = self._raw_to_dataframe(raw_candles)
        self.last_update_time = time.time()

    def _raw_to_dataframe(self, raw_candles: list) -> pd.DataFrame:
        """Convert raw candle list to pandas DataFrame."""
        df = pd.DataFrame(raw_candles)
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
        df = df.set_index('datetime')
        # Ensure numeric types
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        return df

    def update_candles(self):
        """Refresh candle data from sharkexchange.in."""
        try:
            # Only update every 30 seconds to avoid rate limits
            if time.time() - self.last_update_time < 30:
                return
            self._fetch_and_store_5m(limit=config.DATA_WINDOW)
            logger.debug(f"Candles updated: {len(self.candles_5m)} 5m candles from sharkexchange.in")
        except Exception as e:
            logger.error(f"Error updating candles: {e}")

    def get_current_price(self) -> float:
        """Get the current real BTC/USDT price from sharkexchange.in."""
        try:
            return self.exchange.get_current_price(config.SYMBOL)
        except Exception as e:
            logger.error(f"Error getting current price: {e}")
            if len(self.candles_5m) > 0:
                return float(self.candles_5m['close'].iloc[-1])
            return 0.0

    def get_latest_candle(self) -> pd.Series:
        """Get the most recent completed 5m candle (index -2, since -1 is still forming)."""
        if len(self.candles_5m) >= 2:
            return self.candles_5m.iloc[-2]
        if len(self.candles_5m) >= 1:
            return self.candles_5m.iloc[-1]
        return pd.Series()

    def get_current_candle(self) -> pd.Series:
        """Get the currently forming 5m candle (index -1)."""
        if len(self.candles_5m) >= 1:
            return self.candles_5m.iloc[-1]
        return pd.Series()

    def get_previous_candle(self) -> pd.Series:
        """Get the candle before the latest completed one (index -3)."""
        if len(self.candles_5m) >= 3:
            return self.candles_5m.iloc[-3]
        return pd.Series()

    # ─── Bollinger Band Calculations ───

    def calculate_bollinger_bands(self) -> dict:
        """
        Calculate Bollinger Bands (20-period SMA, 2σ) on closed 5m candles.
        
        Returns dict with:
          - middle: SMA value
          - upper: Upper band
          - lower: Lower band
          - bandwidth: (upper - lower) / middle
          - percent_b: (close - lower) / (upper - lower)
        """
        if len(self.candles_5m) < config.BB_PERIOD:
            logger.warning(f"Not enough candles for BB calculation (need {config.BB_PERIOD}, have {len(self.candles_5m)})")
            return {}

        closes = self.candles_5m['close'].iloc[-config.BB_PERIOD:]

        middle = closes.mean()
        std = closes.std()
        upper = middle + (config.BB_STD_DEV * std)
        lower = middle - (config.BB_STD_DEV * std)

        latest_close = float(closes.iloc[-1])
        bandwidth = (upper - lower) / middle if middle != 0 else 0
        percent_b = (latest_close - lower) / (upper - lower) if (upper - lower) != 0 else 0

        return {
            'middle': float(middle),
            'upper': float(upper),
            'lower': float(lower),
            'std': float(std),
            'bandwidth': float(bandwidth),
            'percent_b': float(percent_b),
            'latest_close': latest_close,
        }

    def calculate_bb_for_candle(self, candle_index: int = -2) -> dict:
        """Calculate BB values at a specific candle index (default: last completed candle)."""
        if len(self.candles_5m) < config.BB_PERIOD + abs(candle_index):
            return {}

        # Get closes up to and including the specified candle
        end_idx = len(self.candles_5m) + candle_index + 1
        start_idx = end_idx - config.BB_PERIOD
        if start_idx < 0:
            return {}

        closes = self.candles_5m['close'].iloc[start_idx:end_idx]
        middle = closes.mean()
        std = closes.std()
        upper = middle + (config.BB_STD_DEV * std)
        lower = middle - (config.BB_STD_DEV * std)

        candle_close = float(closes.iloc[-1])

        return {
            'middle': float(middle),
            'upper': float(upper),
            'lower': float(lower),
            'std': float(std),
            'candle_close': candle_close,
        }

    def get_candle_summary(self) -> dict:
        """Get summary of current market data for dashboard."""
        try:
            current_price = self.get_current_price()
            bb = self.calculate_bollinger_bands()

            latest = self.get_latest_candle()
            current = self.get_current_candle()

            return {
                'symbol': config.SYMBOL,
                'timeframe': config.TIMEFRAME,
                'current_price': current_price,
                'candle_count': len(self.candles_5m),
                'latest_candle': {
                    'open': float(latest.get('open', 0)),
                    'high': float(latest.get('high', 0)),
                    'low': float(latest.get('low', 0)),
                    'close': float(latest.get('close', 0)),
                    'volume': float(latest.get('volume', 0)),
                    'time': str(latest.index) if hasattr(latest, 'index') else '',
                } if not latest.empty else {},
                'bollinger_bands': bb,
                'price_in_inr': current_price * config.USD_INR_RATE if current_price > 0 else 0,
                'data_source': 'sharkexchange.in',
            }
        except Exception as e:
            logger.error(f"Error getting candle summary: {e}")
            return {'error': str(e)}

    def get_chart_data(self, limit: int = 100) -> dict:
        """Get candle + BB data for chart rendering."""
        try:
            if len(self.candles_5m) == 0:
                return {'candles': [], 'bb': {}, 'current_price': 0.0}

            df = self.candles_5m.iloc[-limit:] if len(self.candles_5m) > limit else self.candles_5m

            candles = []
            for idx, row in df.iterrows():
                candles.append({
                    'time': int(row.get('timestamp', 0)),
                    'open': float(row.get('open', 0)),
                    'high': float(row.get('high', 0)),
                    'low': float(row.get('low', 0)),
                    'close': float(row.get('close', 0)),
                    'volume': float(row.get('volume', 0)),
                })

            bb_series = {}
            if len(df) >= config.BB_PERIOD:
                closes = df['close']
                rolling_mean = closes.rolling(window=config.BB_PERIOD).mean()
                rolling_std = closes.rolling(window=config.BB_PERIOD).std()
                upper_series = rolling_mean + (config.BB_STD_DEV * rolling_std)
                lower_series = rolling_mean - (config.BB_STD_DEV * rolling_std)

                bb_series = {
                    'middle': [float(v) if not pd.isna(v) else None for v in rolling_mean],
                    'upper': [float(v) if not pd.isna(v) else None for v in upper_series],
                    'lower': [float(v) if not pd.isna(v) else None for v in lower_series],
                }

            return {
                'candles': candles,
                'bb': bb_series,
                'current_price': self.get_current_price(),
            }
        except Exception as e:
            logger.error(f"Error getting chart data: {e}")
            return {'candles': [], 'bb': {}, 'current_price': 0.0}