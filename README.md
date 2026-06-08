# BTC 15min Breakout Trading Bot with Trailing Stop

A Python-based automated trading bot for BTCUSDT that implements a 15-minute breakout/breakdown strategy with 5-minute trailing stop management and a real-time web dashboard.

## Strategy Overview

**Strategy Name:** BTC_15min_Breakout_Trailing_Stop

| Parameter | Value |
|-----------|-------|
| Asset | BTCUSDT |
| Entry Timeframe | 15 minutes |
| Trailing Timeframe | 5 minutes |
| Breakout Period | 20 candles |
| Initial Stop Loss | 200 points |
| Take Profit | 800 points (4:1 R:R) |
| Trailing Candles | 5 (5-min) |
| Risk Per Trade | 2% of capital |
| Max Daily Loss | 10% of capital |

### Entry Rules

- **LONG:** 15-min candle closes above `Highest(High, 20)[1]` (breakout above resistance)
- **SHORT:** 15-min candle closes below `Lowest(Low, 20)[1]` (breakdown below support)

### Trailing Stop Rules

- **LONG:** Trail = `max(previous_stop, Lowest(Low, 5)[1])` — stop only moves **UP**
- **SHORT:** Trail = `min(previous_stop, Highest(High, 5)[1])` — stop only moves **DOWN**
- Updated on each 5-min candle close

### Exit Conditions

1. Trailing stop hit (price touches/crosses trailing stop level)
2. Take-profit reached (entry ± 800 points)

### Daily Direction Selector

The dashboard provides BUY/SELL toggle buttons. Once a direction is selected, the bot only takes trades in that direction for the remainder of the day.

## Project Structure

```
tradingbot/
├── config.py           # Strategy parameters and configuration
├── exchange.py         # Binance exchange connection (CCXT)
├── data_manager.py     # Candle data fetching and processing
├── strategy.py         # Breakout detection + trailing stop logic
├── risk_manager.py     # Risk controls and position sizing
├── trade_manager.py    # Position lifecycle and order execution
├── app.py              # Flask + SocketIO web server
├── run.py              # Entry point script
├── requirements.txt    # Python dependencies
├── .env.example        # Environment variable template
├── .gitignore          # Git ignore rules
├── templates/
│   └── index.html      # Dashboard HTML
├── static/
│   ├── style.css       # Dashboard CSS
│   └── app.js          # Dashboard JavaScript
└── README.md           # This file
```

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure API Keys

Copy `.env.example` to `.env` and add your Binance API credentials:

```bash
copy .env.example .env
```

Edit `.env`:
```
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_api_secret_here
```

### 3. Configure Strategy Parameters

Edit `config.py` to adjust:
- Breakout period, trailing candles, SL/TP points
- Risk management settings
- Dashboard port and host

### 4. Run the Bot

```bash
# Normal mode (connects to Binance)
python run.py

# Test/simulation mode
python run.py --test
```

### 5. Open Dashboard

Navigate to `http://localhost:5000` in your browser.

## Dashboard Features

- **Direction Selector:** BUY/SELL toggle buttons to restrict trade direction
- **Live PnL:** Unrealized, realized today, and total PnL
- **Position Info:** Entry price, trailing stop, take profit, distance to stop
- **Strategy Levels:** Current resistance and support levels
- **Risk Management:** Capital, risk per trade, daily loss limits
- **Trailing Stop Chart:** Real-time price chart with trailing stop overlay
- **Trade History:** Recent completed trades
- **Activity Log:** Real-time bot activity messages

## Architecture

```
Dashboard (Browser)
    ↕ SocketIO
Flask Server (app.py)
    ↕
Trading Loop (background thread)
    ↕
TradeManager → StrategyEngine → DataManager → ExchangeManager
    ↕
RiskManager
```

## Safety Notes

- **Sandbox Mode:** By default, `config.SANDBOX_MODE = True` uses Binance testnet
- **Max 1 Position:** Only one concurrent trade is allowed
- **Daily Loss Limit:** Bot stops trading if daily loss exceeds 10%
- **Manual Close:** Position can be manually closed from the dashboard

## Disclaimer

This trading bot is for educational and research purposes only. Trading cryptocurrencies involves significant risk. Always test thoroughly in sandbox mode before using real funds. The authors are not responsible for any financial losses.