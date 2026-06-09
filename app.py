"""
Flask web server with SocketIO for the 5-Minute BB Reversal Bot dashboard.

Provides:
  - Web dashboard with live BTC price from sharkexchange.in, Bollinger Bands, P&L in INR
  - Direction selector (BUY/SELL/NONE buttons)
  - Real-time updates via SocketIO
  - REST API endpoints for control and data
  - Trading session status (IST)
  - Next trade execution time display
  - All pair prices from sharkexchange.in

All data sourced exclusively from sharkexchange.in — NO Binance.
"""

import logging
import logging.handlers
import json
import os
import signal
import sys
import config
import threading
import time
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
from datetime import datetime, timezone, timedelta

from exchange import ExchangeManager
from data_manager import DataManager
from strategy import StrategyEngine, TradeDirection
from risk_manager import RiskManager
from trade_manager import TradeManager

logger = logging.getLogger(__name__)

# IST timezone offset
IST_OFFSET = timedelta(hours=5, minutes=30)

# ─── Initialize Flask App ───
app = Flask(__name__)
app.config['SECRET_KEY'] = 'trading-bot-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ─── Initialize Components ───
exchange_manager = None
data_manager = None
strategy_engine = None
risk_manager = None
trade_manager = None
bot_running = False
state_lock = threading.Lock()
shutdown_event = threading.Event()


def get_next_trade_execution_time() -> dict:
    """Calculate when the next trade will execute based on IST sessions and BB signals.

    Returns dict with:
      - next_session_start: IST time string of next trading session
      - next_session_name: Name of next session
      - next_candle_close: IST time string of next 5m candle close
      - waiting_for: What we're waiting for (session, signal, candle_close)
      - can_trade_now: Whether we can trade right now
    """
    now_utc = datetime.now(timezone.utc)
    now_ist = now_utc + IST_OFFSET

    # Find current/next session
    current_session = None
    next_session = None
    next_session_start_ist = None

    for session in config.TRADING_SESSIONS:
        start_time = now_ist.replace(hour=session['start_hour'], minute=session['start_min'], second=0, microsecond=0)
        end_time = now_ist.replace(hour=session['end_hour'], minute=session['end_min'], second=0, microsecond=0)

        if start_time <= now_ist <= end_time:
            current_session = session
            break

    if not current_session:
        # Find next session today or tomorrow
        for session in config.TRADING_SESSIONS:
            start_time = now_ist.replace(hour=session['start_hour'], minute=session['start_min'], second=0, microsecond=0)
            if start_time > now_ist:
                next_session = session
                next_session_start_ist = start_time
                break

    if not next_session:
        # Next session is tomorrow (first session)
        tomorrow_ist = now_ist + timedelta(days=1)
        first_session = config.TRADING_SESSIONS[0]
        next_session_start_ist = tomorrow_ist.replace(
            hour=first_session['start_hour'], minute=first_session['start_min'], second=0, microsecond=0
        )
        next_session = first_session

    if next_session_start_ist is None:
        # Fallback: no next session found at all
        next_session_start_ist = now_ist + timedelta(minutes=1)
        next_session = config.TRADING_SESSIONS[0]

    # Calculate next 5m candle close time
    # 5m candles close at :00, :05, :10, :15, :20, :25, :30, :35, :40, :45, :50, :55
    current_minute = now_ist.minute
    minutes_to_next_close = 5 - (current_minute % 5)
    if minutes_to_next_close == 0:
        minutes_to_next_close = 5
    next_candle_close_ist = now_ist.replace(second=0, microsecond=0) + timedelta(minutes=minutes_to_next_close)

    # Determine what we're waiting for
    has_position = trade_manager and trade_manager.has_open_position()
    can_trade_now = current_session is not None

    if not can_trade_now:
        waiting_for = "session_start"
        next_action_time = next_session_start_ist
        next_action_name = next_session['name'] + " session"
    elif has_position:
        waiting_for = "exit_signal"
        next_action_time = next_candle_close_ist
        next_action_name = "trailing stop or exit signal"
    else:
        waiting_for = "bb_signal"
        next_action_time = next_candle_close_ist
        next_action_name = "Bollinger Band entry signal"

    # Convert IST datetime objects to UTC ISO timestamps for JS countdown parsing
    # Use strftime to produce clean ISO 8601 UTC format (no timezone double-spec like +00:00Z)
    next_candle_close_utc = (next_candle_close_ist - IST_OFFSET).strftime('%Y-%m-%dT%H:%M:%SZ')
    next_session_start_utc = None
    if next_session_start_ist:
        next_session_start_utc = (next_session_start_ist - IST_OFFSET).strftime('%Y-%m-%dT%H:%M:%SZ')
    next_action_time_utc = None
    if next_action_time:
        next_action_time_utc = (next_action_time - IST_OFFSET).strftime('%Y-%m-%dT%H:%M:%SZ')

    return {
        'current_session': current_session['name'] if current_session else None,
        'next_session_start_ist': next_session_start_ist.strftime('%I:%M %p IST') if next_session_start_ist else None,
        'next_session_start_utc': next_session_start_utc,
        'next_session_name': next_session['name'] if next_session else None,
        'next_candle_close_ist': next_candle_close_ist.strftime('%I:%M:%S %p IST'),
        'next_candle_close_utc': next_candle_close_utc,
        'waiting_for': waiting_for,
        'next_action_time_ist': next_action_time.strftime('%I:%M %p IST') if next_action_time else None,
        'next_action_time_utc': next_action_time_utc,
        'next_action_name': next_action_name,
        'can_trade_now': can_trade_now,
        'current_ist_time': now_ist.strftime('%I:%M:%S %p IST'),
    }


def initialize_components(force=False):
    """Initialize all trading bot components.
    
    If force=False and components already exist, skip re-initialization
    to preserve state (position, trade history, risk counters) across
    bot stop/restart cycles.
    """
    global exchange_manager, data_manager, strategy_engine, risk_manager, trade_manager

    # Don't re-initialize if components already exist (preserves state on restart)
    if not force and exchange_manager and data_manager and strategy_engine and risk_manager and trade_manager:
        logger.info("Components already initialized — preserving existing state")
        return True

    try:
        exchange_manager = ExchangeManager()
        data_manager = DataManager(exchange_manager)
        strategy_engine = StrategyEngine(data_manager)
        risk_manager = RiskManager()
        trade_manager = TradeManager(exchange_manager, strategy_engine, risk_manager)

        # Log connection status
        if exchange_manager.is_paper_trading():
            logger.info("Running in PAPER TRADING mode — real sharkexchange.in prices, simulated orders")
        else:
            logger.info("Connected to sharkexchange.in — real trading enabled")

        logger.info("All components initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize components: {e}")
        return False


# ─── Web Routes ───

@app.route('/')
def index():
    """Render the main dashboard page."""
    return render_template('index.html')


@app.route('/api/status')
def get_status():
    """Get full bot status for dashboard."""
    try:
        current_price = 0.0
        if data_manager:
            current_price = data_manager.get_current_price()

        session = strategy_engine.is_trading_session() if strategy_engine else {'active': False, 'session_name': 'Unknown'}
        next_execution = get_next_trade_execution_time()

        status = {
            'bot_running': bot_running,
            'timestamp': str(datetime.now(timezone.utc)),
            'trading_mode': 'PAPER' if (exchange_manager and exchange_manager.is_paper_trading()) else 'REAL',
            'exchange_connected': exchange_manager.is_connected() if exchange_manager else False,
            'data_source': 'sharkexchange.in',
            'session': session,
            'next_execution': next_execution,
            'strategy': strategy_engine.get_strategy_summary() if strategy_engine else {},
            'position': trade_manager.get_position_info(current_price) if trade_manager else {},
            'risk': risk_manager.get_daily_status() if risk_manager else {},
            'config': {
                'symbol': config.SYMBOL,
                'timeframe': config.TIMEFRAME,
                'bb_period': config.BB_PERIOD,
                'bb_std': config.BB_STD_DEV,
                'near_threshold': config.NEAR_THRESHOLD,
                'trail_pct': config.TRAIL_PCT,
                'trade_inr': config.TRADE_INR,
                'usd_inr_rate': config.USD_INR_RATE,
                'max_daily_loss_inr': config.MAX_DAILY_LOSS_INR,
                'max_trades_per_day': config.MAX_TRADES_PER_DAY,
            }
        }
        return jsonify(status)
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/direction', methods=['POST'])
def set_direction():
    """Set the trade direction filter."""
    try:
        data = request.get_json()
        direction = data.get('direction', 'none')

        if strategy_engine:
            strategy_engine.set_direction(direction)
            socketio.emit('direction_update', {
                'direction': strategy_engine.get_direction()
            })
            return jsonify({
                'success': True,
                'direction': strategy_engine.get_direction()
            })
        return jsonify({'success': False, 'reason': 'Strategy engine not initialized'}), 400
    except Exception as e:
        logger.error(f"Error setting direction: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/start', methods=['POST'])
def start_bot():
    """Start the trading bot."""
    global bot_running
    try:
        with state_lock:
            if not bot_running:
                if not initialize_components():
                    return jsonify({'success': False, 'reason': 'Failed to initialize'}), 500
                bot_running = True
                shutdown_event.clear()
            else:
                return jsonify({'success': True, 'running': True, 'reason': 'Already running'})
        start_trading_loop()
        socketio.emit('bot_status', {'running': True})
        logger.info("Trading bot STARTED")
        return jsonify({'success': True, 'running': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/stop', methods=['POST'])
def stop_bot():
    """Stop the trading bot."""
    global bot_running
    try:
        with state_lock:
            bot_running = False
        shutdown_event.set()
        socketio.emit('bot_status', {'running': False})
        logger.info("Trading bot STOPPED")
        return jsonify({'success': True, 'running': False})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/close_position', methods=['POST'])
def close_position_manual():
    """Manually close the current position."""
    try:
        if trade_manager and trade_manager.has_open_position():
            current_price = data_manager.get_current_price()
            result = trade_manager.close_position('manual', current_price)
            socketio.emit('position_update', trade_manager.get_position_info(data_manager.get_current_price()))
            return jsonify(result)
        # Return 200 with info message instead of 400 — frontend handles gracefully
        return jsonify({'success': False, 'reason': 'No open position', 'no_position': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/trade_history')
def get_trade_history():
    """Get recent trade history."""
    try:
        if trade_manager:
            history = trade_manager.get_trade_history(limit=20)
            return jsonify({'trades': history})
        return jsonify({'trades': []})
    except Exception as e:
        logger.error(f"Error getting trade history: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/connection_test')
def connection_test():
    """Test API connectivity to sharkexchange.in."""
    results = {
        'market_data': False,
        'exchange_info': False,
        'auth': None,
        'errors': [],
    }

    try:
        if not exchange_manager:
            results['errors'].append('Exchange manager not initialized')
            return jsonify(results)

        # Test public endpoint (ticker)
        ticker = exchange_manager.fetch_ticker('BTCUSDT')
        results['market_data'] = ticker.get('price', 0) > 0

        # Test exchange info
        info = exchange_manager.fetch_exchange_info()
        results['exchange_info'] = bool(info)

        # Test auth if connected
        if exchange_manager.is_connected():
            balance = exchange_manager.fetch_balance()
            results['auth'] = balance is not None
        else:
            results['auth'] = False
            results['errors'].append('API keys not configured or invalid')

    except Exception as e:
        results['errors'].append(str(e))

    return jsonify(results)


@app.route('/api/chart_data')
def get_chart_data():
    """Get candle + BB data for chart rendering."""
    try:
        if data_manager:
            chart_data = data_manager.get_chart_data(limit=100)
            return jsonify(chart_data)
        return jsonify({'candles': [], 'bb': {}})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/all_prices')
def get_all_prices():
    """Get all pair prices from sharkexchange.in."""
    try:
        if exchange_manager:
            tickers = exchange_manager.fetch_all_tickers()
            # Convert dict to list for frontend, filter USDT and INR pairs
            price_list = []
            for sym, info in tickers.items():
                if sym.endswith('USDT') or sym.endswith('INR'):
                    price_list.append({
                        'symbol': sym,
                        'price': info.get('price', 0),
                        'change_24h': info.get('priceChangePercent', 0),
                        'volume': info.get('volume', 0),
                    })
            return jsonify({'prices': price_list, 'source': 'sharkexchange.in'})
        return jsonify({'prices': [], 'source': 'unavailable'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─── Manual Trade Endpoint ───

@app.route('/api/manual_trade', methods=['POST'])
def manual_trade():
    """Place a manual trade (for user interaction system)."""
    try:
        body = request.get_json()
        if not body:
            return jsonify({'success': False, 'error': 'Missing JSON body'}), 400

        side = body.get('side', '').lower()
        if side not in ('long', 'short', 'buy', 'sell'):
            return jsonify({'success': False, 'error': 'Invalid side. Use long/short or buy/sell'}), 400

        # Allow overriding the entry price (optional)
        entry_price = body.get('price', 0)
        if entry_price <= 0:
            entry_price = data_manager.get_current_price()

        if entry_price <= 0:
            return jsonify({'success': False, 'error': 'Could not determine entry price'}), 400

        # Map to internal signal format the trade_manager expects
        if side in ('long', 'buy'):
            signal = {
                'direction': 'long',
                'entry_price': entry_price,
                'reason': 'manual_trade',
                'bb': data_manager.calculate_bollinger_bands() if data_manager else {},
            }
        else:
            signal = {
                'direction': 'short',
                'entry_price': entry_price,
                'reason': 'manual_trade',
                'bb': data_manager.calculate_bollinger_bands() if data_manager else {},
            }

        result = trade_manager.open_position(signal)
        socketio.emit('position_update', trade_manager.get_position_info(data_manager.get_current_price()))
        return jsonify(result)

    except Exception as e:
        logger.error(f"Manual trade error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ─── Config Management Endpoint ───

@app.route('/api/config', methods=['GET'])
def get_config():
    """Get current bot configuration."""
    try:
        cfg = {
            'symbol': config.SYMBOL,
            'timeframe': config.TIMEFRAME,
            'trading_mode': os.getenv('TRADING_MODE', 'PAPER'),
            'trade_amount_inr': config.TRADE_INR,
            'usd_inr_rate': config.USD_INR_RATE,
            'max_daily_loss_inr': config.MAX_DAILY_LOSS_INR,
            'max_trades_per_day': config.MAX_TRADES_PER_DAY,
            'bb_period': config.BB_PERIOD,
            'bb_std': config.BB_STD_DEV,
            'near_threshold': config.NEAR_THRESHOLD,
            'trail_pct': config.TRAIL_PCT,
            'cooldown_minutes': getattr(config, 'COOLDOWN_MINUTES', 5),
            'close_on_session_end': getattr(config, 'CLOSE_ON_SESSION_END', False),
        }
        return jsonify({'success': True, 'config': cfg})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/config', methods=['POST'])
def update_config():
    """Update bot configuration at runtime."""
    try:
        body = request.get_json()
        if not body:
            return jsonify({'success': False, 'error': 'Missing JSON body'}), 400

        updated = []
        ignored = []

        # Only allow safe runtime-configurable values
        safe_keys = {
            'trade_amount_inr': (int, 1000, 100000, 'TRADE_INR'),
            'max_daily_loss_inr': (int, 500, 50000, 'MAX_DAILY_LOSS_INR'),
            'max_trades_per_day': (int, 1, 100, 'MAX_TRADES_PER_DAY'),
            'trail_pct': (float, 0.001, 0.05, 'TRAIL_PCT'),
            'near_threshold': (float, 0.0005, 0.02, 'NEAR_THRESHOLD'),
            'cooldown_minutes': (int, 1, 60, 'COOLDOWN_MINUTES'),
            'close_on_session_end': (bool, None, None, 'CLOSE_ON_SESSION_END'),
        }

        for key, value in body.items():
            if key in safe_keys:
                expected_type, vmin, vmax, attr_name = safe_keys[key]
                try:
                    if expected_type == bool:
                        cast_val = bool(value)
                    else:
                        cast_val = expected_type(value)

                    if vmin is not None and cast_val < vmin:
                        ignored.append(f'{key}: value {cast_val} below min {vmin}')
                        continue
                    if vmax is not None and cast_val > vmax:
                        ignored.append(f'{key}: value {cast_val} above max {vmax}')
                        continue

                    setattr(config, attr_name, cast_val)
                    updated.append(key)
                except (ValueError, TypeError):
                    ignored.append(f'{key}: invalid type, expected {expected_type.__name__}')
            else:
                ignored.append(f'{key}: not runtime-configurable')

        return jsonify({
            'success': True,
            'updated': updated,
            'ignored': ignored,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ─── Bot Logs Endpoint ───

@app.route('/api/logs')
def get_bot_logs():
    """Get recent bot log entries (last 100 lines)."""
    try:
        log_file = 'trading_bot.log'
        if not os.path.exists(log_file):
            return jsonify({'success': True, 'logs': [], 'message': 'No log file yet'})

        with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()

        # Return last 200 lines, newest first
        recent = lines[-200:] if len(lines) > 200 else lines
        recent.reverse()

        return jsonify({
            'success': True,
            'logs': [line.rstrip('\n\r') for line in recent],
            'total_lines': len(lines),
            'source': log_file,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ─── SocketIO Events ───

@socketio.on('connect')
def handle_connect():
    """Handle client connection — send full state for page refresh persistence."""
    logger.info('Dashboard client connected')
    emit('bot_status', {'running': bot_running})
    if strategy_engine:
        emit('direction_update', {'direction': strategy_engine.get_direction()})
    if trade_manager and data_manager:
        current_price = data_manager.get_current_price()
        emit('position_update', trade_manager.get_position_info(current_price))
        emit('market_update', data_manager.get_candle_summary())
        emit('strategy_update', strategy_engine.get_strategy_summary())
        emit('risk_update', risk_manager.get_daily_status())
        emit('next_execution_update', get_next_trade_execution_time())
    if trade_manager:
        emit('trade_history_update', {'trades': trade_manager.get_trade_history(limit=20)})


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnect."""
    logger.info('Dashboard client disconnected')


@socketio.on('set_direction')
def handle_set_direction(data):
    """Handle direction change from dashboard."""
    try:
        direction = data.get('direction', 'none')
        if strategy_engine:
            strategy_engine.set_direction(direction)
            emit('direction_update', {'direction': strategy_engine.get_direction()})
            logger.info(f"Direction set to {direction} via SocketIO")
    except Exception as e:
        logger.error(f"Error handling direction change: {e}")


@socketio.on('request_update')
def handle_request_update():
    """Handle request for full status update from dashboard."""
    try:
        if data_manager and trade_manager:
            current_price = data_manager.get_current_price()
            emit('position_update', trade_manager.get_position_info(current_price))
            emit('market_update', data_manager.get_candle_summary())
            emit('strategy_update', strategy_engine.get_strategy_summary())
            emit('risk_update', risk_manager.get_daily_status())
            emit('next_execution_update', get_next_trade_execution_time())
    except Exception as e:
        logger.error(f"Error handling update request: {e}")


# ─── Background Trading Loop ───

def trading_loop():
    """
    Main trading loop running in background thread.

    Cycle (every 5 seconds):
      1. Update candle data from sharkexchange.in
      2. Check IST trading session
      3. Check for entry signals (if no position and session active)
      4. Update trailing stop (if position open)
      5. Check exit conditions (if position open)
      6. Check daily risk limits
      7. Emit updates to dashboard (including next execution time)
    """
    logger.info("Trading loop started")
    consecutive_errors = 0

    while not shutdown_event.is_set():
        try:
            with state_lock:
                if not bot_running:
                    break

            time.sleep(config.LOOP_INTERVAL_SECONDS)

            if shutdown_event.is_set():
                break

            if not data_manager or not trade_manager or not strategy_engine:
                logger.warning("Components not ready, skipping cycle")
                continue

            # 1. Update candle data (real BTC prices from sharkexchange.in)
            data_manager.update_candles()
            current_price = data_manager.get_current_price()

            # Reset error counter on successful data fetch
            if current_price > 0:
                consecutive_errors = 0

            # 2. Check IST trading session
            session = strategy_engine.is_trading_session()

            # Only run trading logic when we have a valid price
            if current_price > 0.0:
                # 6. Check daily risk limits — close position if limit reached
                risk_close = risk_manager.should_close_position()
                if risk_close.get('should_close') and trade_manager.has_open_position():
                    close_result = trade_manager.close_position(risk_close['reason'], current_price)
                    if close_result['success']:
                        logger.info(f"Position closed due to risk limit: {close_result['exit_reason']}")

                # 3. Check entry signals (if no position and session active)
                if not trade_manager.has_open_position() and session.get('active'):
                    signal = strategy_engine.check_entry_signal()
                    if signal.get('signal'):
                        result = trade_manager.open_position(signal)
                        if result['success']:
                            logger.info(f"Trade opened: {result['reason']}")

                # 4. Update trailing stop (if position open)
                if trade_manager.has_open_position():
                    trail_result = trade_manager.update_trailing_stop()
                    if trail_result.get('updated'):
                        logger.info(f"Trailing stop updated: {trail_result['reason']}")

                # 5. Check exit conditions (if position open)
                if trade_manager.has_open_position():
                    exit_check = trade_manager.check_exit_conditions(current_price)
                    if exit_check.get('exit'):
                        close_result = trade_manager.close_position(
                            exit_check['reason'], exit_check.get('exit_price', current_price)
                        )
                        if close_result['success']:
                            logger.info(f"Trade closed: {close_result['exit_reason']}, P&L=Rs.{close_result['pnl_inr']:.0f}")

            # 7. Emit updates to dashboard (always, even when price is 0)
            position_info = trade_manager.get_position_info(current_price)
            market_info = data_manager.get_candle_summary()
            strategy_info = strategy_engine.get_strategy_summary()
            risk_info = risk_manager.get_daily_status()
            next_execution = get_next_trade_execution_time()

            socketio.emit('bot_status', {'running': bot_running})
            socketio.emit('position_update', position_info)
            socketio.emit('market_update', market_info)
            socketio.emit('strategy_update', strategy_info)
            socketio.emit('risk_update', risk_info)
            socketio.emit('next_execution_update', next_execution)

        except Exception as e:
            consecutive_errors += 1
            logger.error(f"Error in trading loop (attempt {consecutive_errors}): {e}")
            # If too many errors in a row, log more aggressively
            if consecutive_errors >= 5:
                logger.error(f"Trading loop has failed {consecutive_errors} consecutive times. Check network/API keys.")
            time.sleep(10)

    logger.info("Trading loop stopped")


def start_trading_loop():
    """Start the trading loop in a background (non-daemon) thread."""
    global bot_running
    with state_lock:
        bot_running = True
    shutdown_event.clear()
    thread = threading.Thread(target=trading_loop, daemon=False, name="TradingLoop")
    thread.start()
    logger.info(f"Trading loop thread started (alive={thread.is_alive()})")
    return thread


# ─── Main Entry ───

if __name__ == '__main__':
    # Setup logging with rotation (10MB x 5 backup files)
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.handlers.RotatingFileHandler(
                config.LOG_FILE,
                maxBytes=10 * 1024 * 1024,  # 10 MB
                backupCount=5,
            ),
            logging.StreamHandler()
        ]
    )

    # Register graceful shutdown handler
    def _graceful_shutdown(signum, frame):
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        global bot_running
        with state_lock:
            bot_running = False
        shutdown_event.set()
        # Save trade history before exit
        if trade_manager:
            trade_manager._save_history()
        logger.info("Shutdown complete")
        sys.exit(0)

    signal.signal(signal.SIGINT, _graceful_shutdown)
    signal.signal(signal.SIGTERM, _graceful_shutdown)

    # Initialize components
    if initialize_components():
        bot_running = True
        start_trading_loop()
        logger.info(f"Starting dashboard on http://{config.DASHBOARD_HOST}:{config.DASHBOARD_PORT}")
        socketio.run(
            app,
            host=config.DASHBOARD_HOST,
            port=config.DASHBOARD_PORT,
            debug=config.DASHBOARD_DEBUG,
            allow_unsafe_werkzeug=True
        )
    else:
        logger.error("Failed to initialize. Starting dashboard in offline mode.")
        socketio.run(
            app,
            host=config.DASHBOARD_HOST,
            port=config.DASHBOARD_PORT,
            debug=config.DASHBOARD_DEBUG,
            allow_unsafe_werkzeug=True
        )