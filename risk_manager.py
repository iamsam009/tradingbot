"""
Risk manager for the 5-Minute BB Reversal Bot.

INR-based daily risk limits:
  - Max daily loss: ₹3,000 (converted to USDT at configurable rate)
  - Max trades per day: 30
  - Both counters reset at midnight IST
  - If limits exceeded, stop trading for the rest of the day
"""

import logging
import threading
from datetime import datetime, timezone, timedelta
import config

logger = logging.getLogger(__name__)

# IST timezone: UTC + 5:30
IST_OFFSET = timedelta(hours=5, minutes=30)
IST_TZ = timezone(IST_OFFSET)


class RiskManager:
    """Enforces INR-based daily risk limits with IST midnight reset."""

    def __init__(self):
        self._lock = threading.Lock()
        self.daily_pnl_usdt = 0.0       # Cumulative daily realised P&L in USDT
        self.daily_pnl_inr = 0.0         # Cumulative daily realised P&L in INR
        self.daily_trade_count = 0       # Number of executed trades today
        self.daily_loss_limit_reached = False
        self.daily_trade_limit_reached = False
        self.last_reset_date = None      # IST date string for reset tracking
        self._check_reset()

    def _get_ist_date(self) -> str:
        """Get current IST date as string."""
        now_ist = datetime.now(timezone.utc).astimezone(IST_TZ)
        return now_ist.strftime("%Y-%m-%d")

    def _check_reset(self):
        """Reset daily counters at midnight IST."""
        ist_date = self._get_ist_date()
        if ist_date != self.last_reset_date:
            self.daily_pnl_usdt = 0.0
            self.daily_pnl_inr = 0.0
            self.daily_trade_count = 0
            self.daily_loss_limit_reached = False
            self.daily_trade_limit_reached = False
            self.last_reset_date = ist_date
            logger.info(f"Daily risk counters reset for {ist_date} (IST)")

    def can_trade(self) -> dict:
        """
        Check if a new trade is allowed.
        
        Returns dict with:
          - allowed: bool
          - reason: str (if not allowed)
          - daily_pnl_inr: float
          - daily_trade_count: int
        """
        with self._lock:
            self._check_reset()

            # Check daily loss limit
            max_loss_usdt = config.MAX_DAILY_LOSS_INR / config.USD_INR_RATE
            if self.daily_pnl_usdt <= -max_loss_usdt:
                self.daily_loss_limit_reached = True
                return {
                    'allowed': False,
                    'reason': f"Daily loss limit reached: ₹{self.daily_pnl_inr:.0f} loss (limit: ₹{config.MAX_DAILY_LOSS_INR})",
                    'daily_pnl_inr': self.daily_pnl_inr,
                    'daily_trade_count': self.daily_trade_count,
                }

            # Check daily trade count limit
            if self.daily_trade_count >= config.MAX_TRADES_PER_DAY:
                self.daily_trade_limit_reached = True
                return {
                    'allowed': False,
                    'reason': f"Daily trade limit reached: {self.daily_trade_count} trades (limit: {config.MAX_TRADES_PER_DAY})",
                    'daily_pnl_inr': self.daily_pnl_inr,
                    'daily_trade_count': self.daily_trade_count,
                }

            return {
                'allowed': True,
                'reason': 'OK',
                'daily_pnl_inr': self.daily_pnl_inr,
                'daily_trade_count': self.daily_trade_count,
            }

    def record_trade_entry(self):
        """Record a new trade entry for daily count tracking."""
        with self._lock:
            self._check_reset()
            self.daily_trade_count += 1
            logger.info(f"Trade count: {self.daily_trade_count}/{config.MAX_TRADES_PER_DAY}")

    def record_trade_exit(self, pnl_usdt: float):
        """
        Record a trade exit with its P&L.
        
        Args:
            pnl_usdt: Realised P&L in USDT (positive = profit, negative = loss)
        """
        with self._lock:
            self._check_reset()
            self.daily_pnl_usdt += pnl_usdt
            self.daily_pnl_inr += pnl_usdt * config.USD_INR_RATE

            max_loss_usdt = config.MAX_DAILY_LOSS_INR / config.USD_INR_RATE

            logger.info(
                f"Trade exit recorded: P&L ${pnl_usdt:.2f} / ₹{pnl_usdt * config.USD_INR_RATE:.0f}. "
                f"Daily total: ₹{self.daily_pnl_inr:.0f} (limit: -₹{config.MAX_DAILY_LOSS_INR})"
            )

            # Check if daily loss limit is now reached
            if self.daily_pnl_usdt <= -max_loss_usdt:
                self.daily_loss_limit_reached = True
                logger.warning(f"⚠️ Daily loss limit reached! No more trades today.")

    def should_close_position(self) -> dict:
        """
        Check if current open position should be closed due to daily limits.
        
        If daily loss limit is reached, close any open position immediately.
        """
        with self._lock:
            self._check_reset()
            max_loss_usdt = config.MAX_DAILY_LOSS_INR / config.USD_INR_RATE

            if self.daily_pnl_usdt <= -max_loss_usdt:
                return {
                    'should_close': True,
                    'reason': f"Daily loss limit reached: ₹{self.daily_pnl_inr:.0f}",
                }

            return {'should_close': False, 'reason': 'OK'}

    def get_daily_status(self) -> dict:
        """Get daily risk status for dashboard."""
        with self._lock:
            self._check_reset()
            max_loss_usdt = config.MAX_DAILY_LOSS_INR / config.USD_INR_RATE

            return {
                'daily_pnl_usdt': self.daily_pnl_usdt,
                'daily_pnl_inr': self.daily_pnl_inr,
                'daily_trade_count': self.daily_trade_count,
                'max_daily_loss_inr': config.MAX_DAILY_LOSS_INR,
                'max_daily_loss_usdt': max_loss_usdt,
                'max_trades_per_day': config.MAX_TRADES_PER_DAY,
                'loss_limit_reached': self.daily_loss_limit_reached,
                'trade_limit_reached': self.daily_trade_limit_reached,
                'remaining_loss_inr': config.MAX_DAILY_LOSS_INR - abs(self.daily_pnl_inr) if self.daily_pnl_inr < 0 else config.MAX_DAILY_LOSS_INR,
                'remaining_trades': config.MAX_TRADES_PER_DAY - self.daily_trade_count,
                'ist_date': self.last_reset_date,
            }