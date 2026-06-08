"""
Trade/position manager for the 5-Minute BB Reversal Bot.

Handles:
  - Position sizing in INR (₹20,000 per trade, converted to USDT/BTC)
  - Opening positions with market orders
  - Placing initial stop-loss orders
  - Updating trailing stops (cancel old stop, create new one)
  - Closing positions (market order + cancel stop)
  - Paper trading fallback when exchange is unreachable
"""

import logging
import time
import config

logger = logging.getLogger(__name__)


class TradeManager:
    """Manages positions, orders, and INR-based position sizing."""

    def __init__(self, exchange, strategy, risk_manager):
        self.exchange = exchange
        self.strategy = strategy
        self.risk_manager = risk_manager

        # Current position state
        self.position = {
            'open': False,
            'side': '',
            'entry_price': 0.0,
            'entry_time': 0,
            'quantity': 0.0,
            'trade_inr': 0.0,
            'trade_usdt': 0.0,
            'trailing_stop_price': 0.0,
            'initial_stop_price': 0.0,
            'highest_price': 0.0,
            'lowest_price': 0.0,
            'stop_order_id': '',
            'entry_order_id': '',
        }

        # Trade history
        self.trade_history = []
        self.max_history = 100

    def has_open_position(self) -> bool:
        """Check if there's an open position."""
        return self.position.get('open', False)

    def calculate_position_size(self, entry_price: float) -> dict:
        """
        Calculate position size based on INR trade amount.
        
        trade_usdt = trade_inr / usd_inr_rate
        quantity_btc = trade_usdt / entry_price
        
        Returns dict with quantity, trade_inr, trade_usdt, etc.
        """
        trade_inr = config.TRADE_INR
        trade_usdt = trade_inr / config.USD_INR_RATE
        quantity_btc = trade_usdt / entry_price if entry_price > 0 else 0

        # Risk per trade (if stopped immediately)
        risk_inr = trade_inr * config.TRAIL_PCT

        return {
            'trade_inr': trade_inr,
            'trade_usdt': trade_usdt,
            'quantity_btc': quantity_btc,
            'risk_inr': risk_inr,
            'risk_usdt': risk_inr / config.USD_INR_RATE,
            'entry_price': entry_price,
        }

    def open_position(self, signal: dict) -> dict:
        """
        Open a new position based on entry signal.
        
        Steps:
          1. Check risk limits (can we trade?)
          2. Calculate position size (INR → USDT → BTC)
          3. Place market entry order
          4. Calculate initial stop-loss price
          5. Place stop-loss order
          6. Update position state
        
        Returns dict with success, reason, position info.
        """
        try:
            if self.has_open_position():
                return {'success': False, 'reason': 'Position already open'}

            # 1. Check risk limits
            risk_check = self.risk_manager.can_trade()
            if not risk_check['allowed']:
                return {'success': False, 'reason': risk_check['reason']}

            side = signal.get('side', '')
            entry_price = signal.get('entry_price', 0)

            if not side or entry_price == 0:
                return {'success': False, 'reason': 'Invalid signal'}

            # 2. Calculate position size
            sizing = self.calculate_position_size(entry_price)
            quantity = sizing['quantity_btc']

            if quantity == 0:
                return {'success': False, 'reason': 'Position size is zero'}

            # 3. Place market entry order
            order_side = 'buy' if side == 'long' else 'sell'
            entry_order = self.exchange.create_market_order(config.SYMBOL, order_side, quantity)

            if not entry_order:
                return {'success': False, 'reason': 'Entry order failed'}

            # Use actual fill price if available, otherwise use signal price
            actual_price = entry_order.get('price', entry_price)
            if actual_price == 0:
                actual_price = entry_price

            # Recalculate quantity with actual price
            actual_sizing = self.calculate_position_size(actual_price)
            actual_quantity = actual_sizing['quantity_btc']

            # 4. Calculate initial stop-loss
            if side == 'long':
                initial_stop = actual_price * (1 - config.TRAIL_PCT)
            else:
                initial_stop = actual_price * (1 + config.TRAIL_PCT)

            # 5. Place stop-loss order
            stop_side = 'sell' if side == 'long' else 'buy'
            stop_order = self.exchange.create_stop_loss_order(
                config.SYMBOL, stop_side, actual_quantity, initial_stop
            )

            stop_order_id = stop_order.get('id', '') if stop_order else ''

            # 6. Update position state
            self.position = {
                'open': True,
                'side': side,
                'entry_price': actual_price,
                'entry_time': int(time.time() * 1000),
                'quantity': actual_quantity,
                'trade_inr': actual_sizing['trade_inr'],
                'trade_usdt': actual_sizing['trade_usdt'],
                'trailing_stop_price': initial_stop,
                'initial_stop_price': initial_stop,
                'highest_price': actual_price,
                'lowest_price': actual_price,
                'stop_order_id': stop_order_id,
                'entry_order_id': entry_order.get('id', ''),
            }

            # Record trade entry in risk manager
            self.risk_manager.record_trade_entry()

            logger.info(
                f"Position OPENED: {side} {actual_quantity:.6f} BTC @ ${actual_price:.2f} "
                f"(₹{actual_sizing['trade_inr']:.0f}). "
                f"Initial stop: ${initial_stop:.2f}. "
                f"{'PAPER' if self.exchange.is_paper_trading() else 'REAL'} trade"
            )

            return {
                'success': True,
                'reason': f"Opened {side} position @ ${actual_price:.2f}",
                'position': self.position,
                'sizing': actual_sizing,
            }

        except Exception as e:
            logger.error(f"Error opening position: {e}")
            return {'success': False, 'reason': f'Error: {str(e)}'}

    def update_trailing_stop(self) -> dict:
        """
        Update trailing stop based on price action since entry.
        
        Steps:
          1. Get current price
          2. Calculate new trailing stop level
          3. If stop moved, cancel old stop order and create new one
          4. Update position state
        
        Returns dict with updated, new_stop, etc.
        """
        try:
            if not self.has_open_position():
                return {'updated': False, 'reason': 'No open position'}

            current_price = self.exchange.get_current_price(config.SYMBOL)
            if current_price == 0:
                return {'updated': False, 'reason': 'Cannot get current price'}

            # Calculate new trailing stop
            trail_result = self.strategy.update_trailing_stop(self.position, current_price)

            if not trail_result.get('updated'):
                # Just update highest/lowest tracking even if stop didn't move
                self.position['highest_price'] = trail_result.get('highest_price', self.position['highest_price'])
                self.position['lowest_price'] = trail_result.get('lowest_price', self.position['lowest_price'])
                return trail_result

            new_stop = trail_result['new_stop']
            old_stop = trail_result['old_stop']

            # Cancel old stop order
            if self.position.get('stop_order_id'):
                self.exchange.cancel_order(config.SYMBOL, self.position['stop_order_id'])

            # Create new stop order
            stop_side = 'sell' if self.position['side'] == 'long' else 'buy'
            quantity = self.position['quantity']
            new_stop_order = self.exchange.create_stop_loss_order(
                config.SYMBOL, stop_side, quantity, new_stop
            )

            new_stop_id = new_stop_order.get('id', '') if new_stop_order else ''

            # Update position state
            self.position['trailing_stop_price'] = new_stop
            self.position['stop_order_id'] = new_stop_id
            self.position['highest_price'] = trail_result.get('highest_price', self.position['highest_price'])
            self.position['lowest_price'] = trail_result.get('lowest_price', self.position['lowest_price'])

            logger.info(
                f"Trailing stop UPDATED: ${old_stop:.2f} → ${new_stop:.2f} "
                f"({self.position['side']}). "
                f"{'PAPER' if self.exchange.is_paper_trading() else 'REAL'}"
            )

            return {
                'updated': True,
                'new_stop': new_stop,
                'old_stop': old_stop,
                'reason': f"Stop moved from ${old_stop:.2f} to ${new_stop:.2f}",
            }

        except Exception as e:
            logger.error(f"Error updating trailing stop: {e}")
            return {'updated': False, 'reason': f'Error: {str(e)}'}

    def check_exit_conditions(self, current_price: float) -> dict:
        """Check if position should be closed (TP or trailing stop hit)."""
        return self.strategy.check_exit_conditions(self.position, current_price)

    def close_position(self, reason: str, exit_price: float = 0) -> dict:
        """
        Close the current position.
        
        Steps:
          1. Cancel stop-loss order
          2. Place market exit order
          3. Calculate P&L (USDT and INR)
          4. Record in trade history
          5. Record P&L in risk manager
          6. Reset position state
        
        Returns dict with success, pnl, exit_reason, etc.
        """
        try:
            if not self.has_open_position():
                return {'success': False, 'reason': 'No open position'}

            side = self.position['side']
            entry_price = self.position['entry_price']
            quantity = self.position['quantity']

            # Use provided exit price or get current price
            if exit_price == 0:
                exit_price = self.exchange.get_current_price(config.SYMBOL)
            if exit_price == 0:
                exit_price = entry_price  # Fallback

            # 1. Cancel stop-loss order
            if self.position.get('stop_order_id'):
                self.exchange.cancel_order(config.SYMBOL, self.position['stop_order_id'])

            # 2. Place market exit order
            exit_side = 'sell' if side == 'long' else 'buy'
            exit_order = self.exchange.create_market_order(config.SYMBOL, exit_side, quantity)

            # Use actual fill price if available
            actual_exit_price = exit_order.get('price', exit_price) if exit_order else exit_price
            if actual_exit_price == 0:
                actual_exit_price = exit_price

            # 3. Calculate P&L
            if side == 'long':
                pnl_usdt = (actual_exit_price - entry_price) * quantity
            else:
                pnl_usdt = (entry_price - actual_exit_price) * quantity

            pnl_inr = pnl_usdt * config.USD_INR_RATE

            # 4. Record in trade history
            trade_record = {
                'entry_time': self.position['entry_time'],
                'exit_time': int(time.time() * 1000),
                'side': side,
                'entry_price': entry_price,
                'exit_price': actual_exit_price,
                'quantity': quantity,
                'pnl_usdt': pnl_usdt,
                'pnl_inr': pnl_inr,
                'exit_reason': reason,
                'trade_inr': self.position['trade_inr'],
                'initial_stop': self.position['initial_stop_price'],
                'final_stop': self.position['trailing_stop_price'],
                'paper': self.exchange.is_paper_trading(),
            }
            self.trade_history.append(trade_record)
            if len(self.trade_history) > self.max_history:
                self.trade_history = self.trade_history[-self.max_history:]

            # 5. Record P&L in risk manager
            self.risk_manager.record_trade_exit(pnl_usdt)

            # 6. Reset position state
            self.position = {
                'open': False,
                'side': '',
                'entry_price': 0.0,
                'entry_time': 0,
                'quantity': 0.0,
                'trade_inr': 0.0,
                'trade_usdt': 0.0,
                'trailing_stop_price': 0.0,
                'initial_stop_price': 0.0,
                'highest_price': 0.0,
                'lowest_price': 0.0,
                'stop_order_id': '',
                'entry_order_id': '',
            }

            logger.info(
                f"Position CLOSED: {side} @ exit=${actual_exit_price:.2f}, "
                f"P&L: ${pnl_usdt:.2f} / ₹{pnl_inr:.0f}. "
                f"Reason: {reason}. "
                f"{'PAPER' if self.exchange.is_paper_trading() else 'REAL'}"
            )

            return {
                'success': True,
                'exit_reason': reason,
                'exit_price': actual_exit_price,
                'pnl_usdt': pnl_usdt,
                'pnl_inr': pnl_inr,
                'trade': trade_record,
            }

        except Exception as e:
            logger.error(f"Error closing position: {e}")
            return {'success': False, 'reason': f'Error: {str(e)}'}

    def get_position_info(self, current_price: float = 0) -> dict:
        """Get current position info for dashboard."""
        if not self.has_open_position():
            return {
                'open': False,
                'side': '',
                'entry_price': 0,
                'current_price': current_price,
                'unrealized_pnl_usdt': 0,
                'unrealized_pnl_inr': 0,
                'trailing_stop_price': 0,
                'quantity': 0,
            }

        if current_price == 0:
            current_price = self.exchange.get_current_price(config.SYMBOL)

        entry_price = self.position['entry_price']
        quantity = self.position['quantity']
        side = self.position['side']

        # Unrealized P&L
        if side == 'long':
            unrealized_pnl_usdt = (current_price - entry_price) * quantity
        else:
            unrealized_pnl_usdt = (entry_price - current_price) * quantity

        unrealized_pnl_inr = unrealized_pnl_usdt * config.USD_INR_RATE

        return {
            'open': True,
            'side': side,
            'entry_price': entry_price,
            'current_price': current_price,
            'quantity': quantity,
            'trade_inr': self.position['trade_inr'],
            'trailing_stop_price': self.position['trailing_stop_price'],
            'initial_stop_price': self.position['initial_stop_price'],
            'highest_price': self.position['highest_price'],
            'lowest_price': self.position['lowest_price'],
            'unrealized_pnl_usdt': unrealized_pnl_usdt,
            'unrealized_pnl_inr': unrealized_pnl_inr,
            'paper': self.exchange.is_paper_trading(),
        }

    def get_trade_history(self, limit: int = 20) -> list:
        """Get recent trade history."""
        return self.trade_history[-limit:]