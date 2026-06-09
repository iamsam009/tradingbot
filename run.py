"""
Entry point script for the 5-Minute Bollinger Band Reversal Bot with Trailing Stop & INR Risk Limits.

Usage:
    python run.py          # Start the bot with dashboard (real BTC prices from sharkexchange.in)
    python run.py --test   # Force paper trading mode (real prices, simulated orders)
"""

import sys
import signal
import logging
import logging.handlers
import config

def main():
    """Main entry point."""
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

    logger = logging.getLogger(__name__)

    # Check for test mode flag
    test_mode = '--test' in sys.argv

    if test_mode:
        logger.info("Running in TEST/SIMULATION mode (no real exchange connection)")
        config.SANDBOX_MODE = True
        # In test mode, we'll use mock data if exchange fails

    # Import and run the Flask app
    from app import app, socketio, initialize_components, start_trading_loop, shutdown_event, bot_running, state_lock, trade_manager

    # Graceful shutdown handler
    def _graceful_shutdown(signum, frame):
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        with state_lock:
            bot_running = False
        shutdown_event.set()
        if trade_manager:
            trade_manager._save_history()
        logger.info("Shutdown complete")
        sys.exit(0)

    signal.signal(signal.SIGINT, _graceful_shutdown)
    signal.signal(signal.SIGTERM, _graceful_shutdown)

    # Initialize components
    success = initialize_components()
    if success:
        logger.info("All components initialized successfully")
        # Start trading loop
        start_trading_loop()
    else:
        logger.warning("Component initialization failed. Dashboard will run in offline mode.")

    # Start the web server
    logger.info(f"Starting dashboard on http://{config.DASHBOARD_HOST}:{config.DASHBOARD_PORT}")
    logger.info(f"Open http://localhost:{config.DASHBOARD_PORT} in your browser")

    socketio.run(
        app,
        host=config.DASHBOARD_HOST,
        port=config.DASHBOARD_PORT,
        debug=config.DASHBOARD_DEBUG
    )


if __name__ == '__main__':
    main()