"""
Strategy engine for the 5-Minute Bollinger Band Reversal Bot.

Entry Logic:
  - Long (BUY): Green candle (Close > Open) closes near lower BB
    (Close - LowerBand) / Close < near_threshold AND Close > LowerBand
  - Short (SELL): Red candle (Close < Open) closes near upper BB
    (UpperBand - Close) / Close < near_threshold AND Close < UpperBand
  - Only during IST trading sessions
  - Only one position at a time

Exit Logic:
  - Take-Profit: Long → price reaches Upper Band; Short → price reaches Lower Band
  - Trailing Stop: ratchets based on highest/lowest price since entry
"""

import logging
import threading
import time as _time
from datetime import datetime, timezone, timedelta
from enum import Enum
import config

logger = logging.getLogger(__name__)

# IST timezone: UTC + 5:30
IST_OFFSET = timedelta(hours=5, minutes=30)
IST_TZ = timezone(IST_OFFSET)


class TradeDirection(Enum):
    BOTH = "both"
    LONG = "long"
    SHORT = "short"


class StrategyEngine:
    """Bollinger Band reversal strategy with IST session filtering."""

    def __init__(self, data_manager):
        self._lock = threading.Lock()
        self.data_manager = data_manager
        self.direction = TradeDirection.BOTH.value
        self.last_signal_time = 0.0
        self.signal_cooldown = 300  # 5 minutes cooldown between signals (one candle)

    def set_direction(self, direction: str):
        """Set the allowed trade direction (for manual override)."""
        with self._lock:
            if direction in ['long', 'LONG']:
                self.direction = TradeDirection.LONG.value
            elif direction in ['short', 'SHORT']:
                self.direction = TradeDirection.SHORT.value
            elif direction in ['none', 'NONE', 'both', 'BOTH']:
                self.direction = TradeDirection.BOTH.value  # BOTH means allow both
            else:
                self.direction = TradeDirection.BOTH.value
            logger.info(f"Trade direction set to: {self.direction}")

    def get_direction(self) -> str:
        """Get current trade direction setting."""
        with self._lock:
            return self.direction

    # ─── IST Session Checker ───

    def is_trading_session(self) -> dict:
        """
        Check if current time is within an IST trading session.
        
        Returns dict with:
          - active: bool (whether we should trade)
          - session_name: str (name of active session, or "Closed")
          - next_session: str (when next session starts)
          - ist_time: str (current IST time formatted)
        """
        now_utc = datetime.now(timezone.utc)
        now_ist = now_utc.astimezone(IST_TZ)
        ist_hour = now_ist.hour
        ist_minute = now_ist.minute

        ist_time_str = now_ist.strftime("%H:%M IST")

        for session in config.TRADING_SESSIONS:
            start_total = session["start_hour"] * 60 + session["start_min"]
            end_total = session["end_hour"] * 60 + session["end_min"]
            current_total = ist_hour * 60 + ist_minute

            if start_total <= current_total <= end_total:
                return {
                    'active': True,
                    'session_name': session["name"],
                    'ist_time': ist_time_str,
                    'next_session': '',
                }

        # Find next session
        current_total = ist_hour * 60 + ist_minute
        next_session_str = "No more sessions today"
        for session in config.TRADING_SESSIONS:
            start_total = session["start_hour"] * 60 + session["start_min"]
            if current_total < start_total:
                next_session_str = f"{session['name']} at {session['start_hour']:02d}:{session['start_min']:02d} IST"
                break

        return {
            'active': False,
            'session_name': "Closed",
            'ist_time': ist_time_str,
            'next_session': next_session_str,
        }

    # ─── Entry Signal Detection ───

    def check_entry_signal(self) -> dict:
        """
        Check for BB reversal entry signal on the last completed 5m candle.
        
        Uses candle at index -2 (last fully closed candle).
        Returns dict with:
          - signal: bool
          - side: 'long' or 'short'
          - reason: str
          - entry_price: float
          - bb_values: dict
        """
        try:
            # Check IST trading session
            session = self.is_trading_session()
            if not session['active']:
                return {
                    'signal': False,
                    'reason': f"Outside trading session ({session['session_name']}, next: {session['next_session']})",
                    'session': session,
                }

            # Check cooldown (use monotonic for wall-clock independence)
            now = _time.monotonic()
            if now - self.last_signal_time < self.signal_cooldown:
                return {'signal': False, 'reason': 'Signal cooldown active'}

            # Get BB values at the last completed candle
            bb = self.data_manager.calculate_bb_for_candle(-2)
            if not bb:
                return {'signal': False, 'reason': 'Insufficient data for BB calculation'}

            # Get the last completed candle
            candle = self.data_manager.get_latest_candle()
            if candle.empty:
                return {'signal': False, 'reason': 'No candle data available'}

            candle_open = float(candle['open'])
            candle_close = float(candle['close'])
            candle_high = float(candle['high'])
            candle_low = float(candle['low'])

            upper_band = bb['upper']
            lower_band = bb['lower']
            middle_band = bb['middle']

            signal_result = {
                'signal': False,
                'side': '',
                'reason': '',
                'entry_price': candle_close,
                'bb_values': bb,
                'candle': {
                    'open': candle_open,
                    'close': candle_close,
                    'high': candle_high,
                    'low': candle_low,
                },
                'session': session,
            }

            # ─── Long Entry: Green candle near lower band ───
            is_green = candle_close > candle_open
            near_lower = (candle_close - lower_band) / candle_close < config.NEAR_THRESHOLD
            above_lower = candle_close > lower_band

            if is_green and near_lower and above_lower:
                # Check direction filter
                if self.direction == TradeDirection.SHORT.value:
                    signal_result['reason'] = 'Long signal blocked by SHORT direction filter'
                    return signal_result

                signal_result['signal'] = True
                signal_result['side'] = 'long'
                signal_result['reason'] = (
                    f"BB Long: Green candle (O={candle_open:.0f}, C={candle_close:.0f}) "
                    f"near lower band ({lower_band:.0f}). "
                    f"Distance: {(candle_close - lower_band) / candle_close * 100:.2f}%"
                )
                self.last_signal_time = _time.monotonic()
                logger.info(signal_result['reason'])
                return signal_result

            # ─── Short Entry: Red candle near upper band ───
            is_red = candle_close < candle_open
            near_upper = (upper_band - candle_close) / candle_close < config.NEAR_THRESHOLD
            below_upper = candle_close < upper_band

            if is_red and near_upper and below_upper:
                # Check direction filter (sharkexchange.in spot = long only, shorts ignored)
                if self.direction == TradeDirection.LONG.value:
                    signal_result['reason'] = 'Short signal blocked by LONG direction filter'
                    return signal_result

                # Note: On spot exchanges (sharkexchange.in), short signals are ignored
                # This short logic is for futures exchanges
                signal_result['signal'] = True
                signal_result['side'] = 'short'
                signal_result['reason'] = (
                    f"BB Short: Red candle (O={candle_open:.0f}, C={candle_close:.0f}) "
                    f"near upper band ({upper_band:.0f}). "
                    f"Distance: {(upper_band - candle_close) / candle_close * 100:.2f}%"
                )
                self.last_signal_time = _time.monotonic()
                logger.info(signal_result['reason'])
                return signal_result

            # No signal
            signal_result['reason'] = (
                f"No BB signal: Candle O={candle_open:.0f} C={candle_close:.0f}, "
                f"Upper={upper_band:.0f} Lower={lower_band:.0f}"
            )
            return signal_result

        except Exception as e:
            logger.error(f"Error checking entry signal: {e}")
            return {'signal': False, 'reason': f'Error: {str(e)}'}

    # ─── Exit Condition Checker ───

    def check_exit_conditions(self, position: dict, current_price: float) -> dict:
        """
        Check if an open position should be closed.
        
        Take-Profit:
          - Long: price reaches or exceeds current Upper Band
          - Short: price reaches or falls below current Lower Band
        
        Returns dict with:
          - exit: bool
          - reason: str
          - exit_price: float
        """
        try:
            if not position or not position.get('open'):
                return {'exit': False, 'reason': 'No open position'}

            bb = self.data_manager.calculate_bollinger_bands()
            if not bb:
                return {'exit': False, 'reason': 'Cannot calculate BB for exit check'}

            side = position.get('side', '')
            entry_price = position.get('entry_price', 0)

            # ─── Take-Profit Check ───
            if side == 'long':
                # Long TP: price reaches upper band
                if current_price >= bb['upper']:
                    return {
                        'exit': True,
                        'reason': f"TP: Price ${current_price:.0f} reached Upper Band ${bb['upper']:.0f}",
                        'exit_price': current_price,
                        'exit_type': 'take_profit',
                    }
            elif side == 'short':
                # Short TP: price reaches lower band
                if current_price <= bb['lower']:
                    return {
                        'exit': True,
                        'reason': f"TP: Price ${current_price:.0f} reached Lower Band ${bb['lower']:.0f}",
                        'exit_price': current_price,
                        'exit_type': 'take_profit',
                    }

            # ─── Trailing Stop Check ───
            trailing_stop = position.get('trailing_stop_price', 0)
            if trailing_stop > 0:
                if side == 'long' and current_price <= trailing_stop:
                    return {
                        'exit': True,
                        'reason': f"Trailing Stop: Price ${current_price:.0f} hit stop ${trailing_stop:.0f}",
                        'exit_price': trailing_stop,
                        'exit_type': 'trailing_stop',
                    }
                elif side == 'short' and current_price >= trailing_stop:
                    return {
                        'exit': True,
                        'reason': f"Trailing Stop: Price ${current_price:.0f} hit stop ${trailing_stop:.0f}",
                        'exit_price': trailing_stop,
                        'exit_type': 'trailing_stop',
                    }

            return {'exit': False, 'reason': 'No exit condition met'}

        except Exception as e:
            logger.error(f"Error checking exit conditions: {e}")
            return {'exit': False, 'reason': f'Error: {str(e)}'}

    # ─── Trailing Stop Update ───

    def update_trailing_stop(self, position: dict, current_price: float) -> dict:
        """
        Update trailing stop based on highest/lowest price since entry.
        
        Long: Track highest price. New stop = highest * (1 - trail_pct), only moves UP.
        Short: Track lowest price. New stop = lowest * (1 + trail_pct), only moves DOWN.
        
        Returns dict with:
          - updated: bool
          - new_stop: float
          - old_stop: float
          - reason: str
        """
        try:
            if not position or not position.get('open'):
                return {'updated': False, 'reason': 'No open position'}

            side = position.get('side', '')
            entry_price = position.get('entry_price', 0)
            current_stop = position.get('trailing_stop_price', 0)
            highest_since_entry = position.get('highest_price', entry_price)
            lowest_since_entry = position.get('lowest_price', entry_price)

            # Update highest/lowest tracking
            if current_price > highest_since_entry:
                highest_since_entry = current_price
            if current_price < lowest_since_entry:
                lowest_since_entry = current_price

            new_stop = current_stop
            updated = False

            if side == 'long':
                # Long trailing stop: only moves UP
                proposed_stop = highest_since_entry * (1 - config.TRAIL_PCT)
                if proposed_stop > current_stop:
                    new_stop = proposed_stop
                    updated = True
            elif side == 'short':
                # Short trailing stop: only moves DOWN
                proposed_stop = lowest_since_entry * (1 + config.TRAIL_PCT)
                if proposed_stop < current_stop:
                    new_stop = proposed_stop
                    updated = True

            return {
                'updated': updated,
                'new_stop': float(new_stop),
                'old_stop': float(current_stop),
                'highest_price': float(highest_since_entry),
                'lowest_price': float(lowest_since_entry),
                'reason': f"Trailing stop {'updated' if updated else 'unchanged'}: ${new_stop:.0f}" if updated else "Trailing stop unchanged",
            }

        except Exception as e:
            logger.error(f"Error updating trailing stop: {e}")
            return {'updated': False, 'reason': f'Error: {str(e)}'}

    def get_strategy_summary(self) -> dict:
        """Get strategy state summary for dashboard."""
        session = self.is_trading_session()
        bb = self.data_manager.calculate_bollinger_bands() if self.data_manager else {}

        return {
            'strategy': '5m BB Reversal',
            'direction': self.direction,
            'session': session,
            'bb_period': config.BB_PERIOD,
            'bb_std': config.BB_STD_DEV,
            'near_threshold': config.NEAR_THRESHOLD,
            'trail_pct': config.TRAIL_PCT,
            'bollinger_bands': bb,
            'trade_inr': config.TRADE_INR,
            'usd_inr_rate': config.USD_INR_RATE,
        }