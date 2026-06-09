/**
 * Dashboard JavaScript for the 5-Minute BB Reversal Bot.
 * 
 * Handles:
 *   - SocketIO connection for real-time updates
 *   - REST API calls for control actions
 *   - Canvas chart rendering with Bollinger Bands
 *   - INR P&L display
 *   - IST session status
 *   - Next trade execution time display
 *   - All pair prices from sharkexchange.in
 */

// ─── SocketIO Connection ───
const socket = io();

socket.on('connect', () => {
    console.log('Connected to server');
    requestInitialData();
});

// BUG3: Also load initial data immediately on script load (not just on SocketIO connect)
// This ensures the dashboard shows data even before SocketIO connects
requestInitialData();

socket.on('disconnect', () => {
    console.log('Disconnected from server');
});

socket.on('bot_status', (data) => {
    updateBotStatus(data.running);
});

socket.on('direction_update', (data) => {
    updateDirectionButtons(data.direction);
});

socket.on('position_update', (data) => {
    updatePositionDisplay(data);
});

socket.on('market_update', (data) => {
    updateMarketDisplay(data);
});

socket.on('strategy_update', (data) => {
    updateStrategyDisplay(data);
});

socket.on('risk_update', (data) => {
    updateRiskDisplay(data);
});

socket.on('next_execution_update', (data) => {
    updateNextExecutionDisplay(data);
});

socket.on('trade_history_update', (data) => {
    updateTradeHistory(data.trades || []);
});

// ─── State ───
let chartData = { candles: [], bb: {}, current_price: 0 };
let lastPrice = 0;
let updateInterval = null;
let pricesInterval = null;
let countdownInterval = null;
let nextExecData = null;
let manualTradeSide = 'long';
let configPanelOpen = false;
let logPanelOpen = false;

// ─── Initial Data Load ───
function requestInitialData() {
    fetch('/api/status')
        .then(r => r.json())
        .then(data => {
            updateBotStatus(data.bot_running);
            updatePositionDisplay(data.position || {});
            updateStrategyDisplay(data.strategy || {});
            updateRiskDisplay(data.risk || {});
            updateSessionDisplay(data.session || {});
            updateTradingMode(data.trading_mode || 'PAPER');
            updateConfigDisplay(data.config || {});
            updateExchangeStatus(data.exchange_connected, data.trading_mode);
            if (data.strategy && data.strategy.bollinger_bands) {
                updateBBDisplay(data.strategy.bollinger_bands);
            }
            if (data.position && data.position.current_price) {
                updatePriceDisplay(data.position.current_price);
            }
            if (data.next_execution) {
                updateNextExecutionDisplay(data.next_execution);
            }
        })
        .catch(err => console.error('Status fetch error:', err));

    fetch('/api/trade_history')
        .then(r => r.json())
        .then(data => updateTradeHistory(data.trades || []))
        .catch(err => console.error('Trade history fetch error:', err));

    fetch('/api/chart_data')
        .then(r => r.json())
        .then(data => {
            chartData = data;
            renderChart();
        })
        .catch(err => console.error('Chart data fetch error:', err));

    fetchAllPrices();
}

// ─── Fetch All Pair Prices ───
function fetchAllPrices() {
    fetch('/api/all_prices')
        .then(r => r.json())
        .then(data => {
            if (data.prices && data.prices.length > 0) {
                renderAllPrices(data.prices);
                document.getElementById('prices-update-time').textContent =
                    `Updated: ${new Date().toLocaleTimeString()}`;
            } else if (data.error) {
                document.getElementById('prices-grid').innerHTML =
                    `<div class="prices-error">Error: ${data.error}</div>`;
            }
        })
        .catch(err => {
            console.error('All prices fetch error:', err);
            document.getElementById('prices-grid').innerHTML =
                `<div class="prices-error">Failed to fetch prices</div>`;
        });
}

// ─── Render All Prices Grid ───
function renderAllPrices(prices) {
    const grid = document.getElementById('prices-grid');

    // BUG4: Prioritize major pairs (BTC, ETH, SOL, etc.) first
    const MAJOR_SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'ADAUSDT', 'DOGEUSDT', 'DOTUSDT', 'MATICUSDT', 'AVAXUSDT'];

    const sorted = prices.sort((a, b) => {
        // Major symbols get highest priority (rank 0)
        const aMajorIdx = MAJOR_SYMBOLS.indexOf(a.symbol);
        const bMajorIdx = MAJOR_SYMBOLS.indexOf(b.symbol);
        const aMajor = aMajorIdx >= 0 ? 0 : 99;
        const bMajor = bMajorIdx >= 0 ? 0 : 99;
        if (aMajor !== bMajor) return aMajor - bMajor;
        // Within major group, sort by index order; within non-major, USDT first then alphabetical
        if (aMajorIdx >= 0 && bMajorIdx >= 0) return aMajorIdx - bMajorIdx;
        const aUsdt = a.symbol.endsWith('USDT') ? 0 : (a.symbol.endsWith('INR') ? 1 : 2);
        const bUsdt = b.symbol.endsWith('USDT') ? 0 : (b.symbol.endsWith('INR') ? 1 : 2);
        if (aUsdt !== bUsdt) return aUsdt - bUsdt;
        return a.symbol.localeCompare(b.symbol);
    });

    // Show top 40 pairs (most relevant)
    const topPairs = sorted.slice(0, 40);

    grid.innerHTML = topPairs.map(p => {
        const changeClass = p.change_24h >= 0 ? 'price-up' : 'price-down';
        const changeSign = p.change_24h >= 0 ? '+' : '';
        const isBtc = p.symbol === 'BTCUSDT';
        const highlightClass = isBtc ? 'price-item-highlight' : '';

        return `<div class="price-item ${highlightClass}">
            <span class="price-item-symbol">${p.symbol}</span>
            <span class="price-item-value">${formatPrice(p.price, p.symbol)}</span>
            <span class="price-item-change ${changeClass}">${changeSign}${p.change_24h.toFixed(2)}%</span>
        </div>`;
    }).join('');
}

function formatPrice(price, symbol) {
    // BUG4: Handle edge cases — null/undefined/NaN/zero prices
    if (price === null || price === undefined || isNaN(price) || price === 0) {
        return '—';
    }
    if (symbol.endsWith('INR')) {
        return `₹${price.toFixed(2)}`;
    } else if (price > 1000) {
        return `$${price.toFixed(2)}`;
    } else if (price > 1) {
        return `$${price.toFixed(4)}`;
    } else {
        return `$${price.toFixed(6)}`;
    }
}

// Start periodic updates
function startPeriodicUpdates() {
    if (updateInterval) clearInterval(updateInterval);
    updateInterval = setInterval(() => {
        fetch('/api/status')
            .then(r => r.json())
            .then(data => {
                updatePositionDisplay(data.position || {});
                updateRiskDisplay(data.risk || {});
                updateSessionDisplay(data.session || {});
                updateExchangeStatus(data.exchange_connected, data.trading_mode);
                if (data.strategy && data.strategy.bollinger_bands) {
                    updateBBDisplay(data.strategy.bollinger_bands);
                }
                if (data.position && data.position.current_price) {
                    updatePriceDisplay(data.position.current_price);
                }
                if (data.next_execution) {
                    updateNextExecutionDisplay(data.next_execution);
                }
            })
            .catch(err => console.error('Periodic update error:', err));

        fetch('/api/chart_data')
            .then(r => r.json())
            .then(data => {
                chartData = data;
                renderChart();
            })
            .catch(err => console.error('Chart update error:', err));

        fetch('/api/trade_history')
            .then(r => r.json())
            .then(data => updateTradeHistory(data.trades || []))
            .catch(err => console.error('Trade history update error:', err));
    }, 10000); // Update every 10 seconds

    // Update all prices every 30 seconds
    if (pricesInterval) clearInterval(pricesInterval);
    pricesInterval = setInterval(fetchAllPrices, 30000);

    // Update IST clock every second
    startISTClock();
}

startPeriodicUpdates();

// ─── IST Clock ───
function startISTClock() {
    if (countdownInterval) clearInterval(countdownInterval);
    countdownInterval = setInterval(() => {
        updateISTClock();
        updateCountdown();
    }, 1000);
}

function updateISTClock() {
    // IST = UTC+5:30
    const now = new Date();
    const istTime = new Date(now.getTime() + (5.5 * 60 * 60 * 1000));
    const hours = istTime.getUTCHours().toString().padStart(2, '0');
    const mins = istTime.getUTCMinutes().toString().padStart(2, '0');
    const secs = istTime.getUTCSeconds().toString().padStart(2, '0');
    document.getElementById('ist-clock').textContent = `IST: ${hours}:${mins}:${secs}`;
}

function updateCountdown() {
    if (!nextExecData) return;

    // Use UTC timestamps for countdown — JS Date parses ISO UTC strings correctly
    const nowUtcMs = Date.now();

    // Calculate countdown to next event using UTC timestamps
    let targetMs = null;
    let label = '';

    if (nextExecData.can_trade_now) {
        // We can trade now — countdown to next candle close
        if (nextExecData.next_candle_close_utc) {
            targetMs = new Date(nextExecData.next_candle_close_utc).getTime();
            label = 'next candle close';
        }
    } else {
        // Can't trade now — countdown to next session start
        if (nextExecData.next_session_start_utc) {
            targetMs = new Date(nextExecData.next_session_start_utc).getTime();
            label = 'session start';
        }
    }

    if (targetMs && targetMs > nowUtcMs) {
        const diffMs = targetMs - nowUtcMs;
        const diffMins = Math.floor(diffMs / 60000);
        const diffSecs = Math.floor((diffMs % 60000) / 1000);
        const countdownEl = document.getElementById('next-exec-countdown');
        countdownEl.textContent = `${diffMins}m ${diffSecs}s until ${label}`;
        countdownEl.className = 'next-exec-value countdown countdown-active';
    } else if (targetMs) {
        const countdownEl = document.getElementById('next-exec-countdown');
        countdownEl.textContent = 'NOW!';
        countdownEl.className = 'next-exec-value countdown countdown-now';
    } else {
        const countdownEl = document.getElementById('next-exec-countdown');
        countdownEl.textContent = '—';
        countdownEl.className = 'next-exec-value countdown';
    }
}

// ─── Next Trade Execution Display ───
function updateNextExecutionDisplay(data) {
    if (!data) return;
    nextExecData = data;

    // Current session
    const sessionEl = document.getElementById('next-exec-session');
    if (data.current_session) {
        sessionEl.textContent = data.current_session;
        sessionEl.className = 'next-exec-value ' + (data.can_trade_now ? 'exec-active' : 'exec-inactive');
    } else {
        sessionEl.textContent = 'No Session';
        sessionEl.className = 'next-exec-value exec-inactive';
    }

    // Can trade now?
    const canTradeEl = document.getElementById('next-exec-can-trade');
    if (data.can_trade_now) {
        canTradeEl.textContent = '✅ YES';
        canTradeEl.className = 'next-exec-value exec-active';
    } else {
        canTradeEl.textContent = '❌ NO';
        canTradeEl.className = 'next-exec-value exec-inactive';
    }

    // Next candle close — display IST string directly (no JS Date parsing needed)
    const candleEl = document.getElementById('next-exec-candle');
    if (data.next_candle_close_ist) {
        candleEl.textContent = data.next_candle_close_ist;
        candleEl.className = 'next-exec-value exec-time';
    } else {
        candleEl.textContent = '—';
        candleEl.className = 'next-exec-value';
    }

    // Waiting for
    const waitingEl = document.getElementById('next-exec-waiting');
    if (data.waiting_for) {
        waitingEl.textContent = data.waiting_for;
        waitingEl.className = 'next-exec-value exec-waiting';
    } else {
        waitingEl.textContent = '—';
        waitingEl.className = 'next-exec-value';
    }

    // Next session starts — display IST string directly
    const nextSessionEl = document.getElementById('next-exec-next-session');
    if (data.next_session_start_ist) {
        nextSessionEl.textContent = data.next_session_start_ist;
        nextSessionEl.className = 'next-exec-value exec-time';
    } else {
        nextSessionEl.textContent = '—';
        nextSessionEl.className = 'next-exec-value';
    }

    // Countdown will be updated by the 1-second interval
    updateCountdown();
}

// ─── Display Update Functions ───

function updatePriceDisplay(price) {
    if (price && price > 0 && !isNaN(price)) {
        lastPrice = price;
        document.getElementById('current-price').textContent = `$${Number(price).toFixed(2)}`;
        const usdInrRate = window._usdInrRate || 83.5;
        const inrPrice = price * usdInrRate;
        document.getElementById('current-price-inr').textContent = `₹${Number(inrPrice).toFixed(0)}`;
    }
}

function updateSessionDisplay(session) {
    const statusEl = document.getElementById('session-status');
    const timeEl = document.getElementById('session-time');

    if (session.active) {
        statusEl.textContent = session.session_name || 'Active';
        statusEl.className = 'session-value active';
    } else {
        statusEl.textContent = session.session_name || 'Closed';
        statusEl.className = 'session-value closed';
    }
    timeEl.textContent = session.ist_time || '';
}

function updateTradingMode(mode) {
    const el = document.getElementById('trading-mode');
    el.textContent = mode;
    el.className = 'mode-badge ' + (mode === 'PAPER' ? 'paper' : 'real');
}

function updateExchangeStatus(connected, mode) {
    const el = document.getElementById('trading-mode');
    if (mode === 'REAL' && connected) {
        el.textContent = 'LIVE';
        el.className = 'mode-badge real';
    } else if (mode === 'PAPER' || !connected) {
        el.textContent = 'PAPER';
        el.className = 'mode-badge paper';
    }
}

function updateBBDisplay(bb) {
    if (!bb) return;
    const safe = (v) => (v != null && !isNaN(v)) ? `$${Number(v).toFixed(0)}` : '—';
    if (bb.upper != null) document.getElementById('bb-upper').textContent = safe(bb.upper);
    if (bb.middle != null) document.getElementById('bb-middle').textContent = safe(bb.middle);
    if (bb.lower != null) document.getElementById('bb-lower').textContent = safe(bb.lower);
}

function updatePositionDisplay(pos) {
    if (!pos) return;

    const sideEl = document.getElementById('pos-side');
    const entryEl = document.getElementById('pos-entry');
    const currentEl = document.getElementById('pos-current');
    const stopEl = document.getElementById('pos-stop');
    const qtyEl = document.getElementById('pos-qty');
    const pnlEl = document.getElementById('pos-pnl');

    const closeBtn = document.getElementById('btn-close-pos');
    const safeNum = (v, dp) => (v != null && !isNaN(v)) ? Number(v).toFixed(dp) : '—';

    if (pos.open) {
        sideEl.textContent = pos.side === 'long' ? 'LONG' : 'SHORT';
        sideEl.className = pos.side === 'long' ? 'trade-long' : 'trade-short';
        entryEl.textContent = `$${safeNum(pos.entry_price, 2)}`;
        currentEl.textContent = `$${safeNum(pos.current_price, 2)}`;
        stopEl.textContent = `$${safeNum(pos.trailing_stop_price, 2)}`;
        qtyEl.textContent = `${safeNum(pos.quantity, 6)} BTC`;

        const pnlInr = pos.unrealized_pnl_inr || 0;
        pnlEl.textContent = `₹${Number(pnlInr).toFixed(0)}`;
        pnlEl.className = pnlInr >= 0 ? 'pnl-positive' : 'pnl-negative';

        if (pos.current_price > 0) {
            updatePriceDisplay(pos.current_price);
        }
        if (closeBtn) closeBtn.disabled = false;
    } else {
        sideEl.textContent = 'None';
        sideEl.className = '';
        entryEl.textContent = '—';
        currentEl.textContent = '—';
        stopEl.textContent = '—';
        qtyEl.textContent = '—';
        pnlEl.textContent = '₹0';
        pnlEl.className = '';
        if (closeBtn) closeBtn.disabled = true;
    }
}

function updateRiskDisplay(risk) {
    if (!risk) return;

    const pnlInr = risk.daily_pnl_inr || 0;
    document.getElementById('risk-pnl').textContent = `₹${pnlInr.toFixed(0)}`;
    document.getElementById('risk-pnl').className = pnlInr >= 0 ? 'pnl-positive' : 'pnl-negative';

    document.getElementById('risk-trades').textContent = `${risk.daily_trade_count || 0} / ${risk.max_trades_per_day || 30}`;
    document.getElementById('risk-loss-limit').textContent = `₹${risk.max_daily_loss_inr || 3000}`;
    document.getElementById('risk-remaining').textContent = `₹${risk.remaining_loss_inr || 3000}`;
    document.getElementById('risk-remaining-trades').textContent = `${risk.remaining_trades || 30}`;
}

function updateStrategyDisplay(strategy) {
    if (!strategy) return;

    const usdInrRate = strategy.usd_inr_rate || 83.5;
    window._usdInrRate = usdInrRate;

    document.getElementById('strat-bb-period').textContent = strategy.bb_period || 20;
    document.getElementById('strat-bb-std').textContent = strategy.bb_std || 2;
    document.getElementById('strat-threshold').textContent = `${((strategy.near_threshold || 0.002) * 100).toFixed(1)}%`;
    document.getElementById('strat-trail').textContent = `${((strategy.trail_pct || 0.005) * 100).toFixed(1)}%`;
    document.getElementById('strat-trade-size').textContent = `₹${Number(strategy.trade_inr || 20000).toLocaleString()}`;
    document.getElementById('strat-usd-inr').textContent = usdInrRate;

    if (strategy.session) {
        updateSessionDisplay(strategy.session);
    }
    if (strategy.bollinger_bands) {
        updateBBDisplay(strategy.bollinger_bands);
    }
}

function updateConfigDisplay(config) {
    if (!config) return;
    // Config is already shown in strategy card
}

function updateMarketDisplay(market) {
    if (!market) return;
    if (market.current_price > 0) {
        updatePriceDisplay(market.current_price);
    }
    if (market.bollinger_bands) {
        updateBBDisplay(market.bollinger_bands);
    }
}

function updateBotStatus(running) {
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');

    if (running) {
        dot.className = 'status-indicator running';
        text.textContent = 'Running';
    } else {
        dot.className = 'status-indicator stopped';
        text.textContent = 'Stopped';
    }
}

function updateDirectionButtons(direction) {
    document.querySelectorAll('.btn-dir').forEach(btn => btn.classList.remove('active'));
    if (direction === 'long') {
        document.querySelector('.btn-long').classList.add('active');
    } else if (direction === 'short') {
        document.querySelector('.btn-short').classList.add('active');
    } else {
        document.querySelector('.btn-both').classList.add('active');
    }
}

function updateTradeHistory(trades) {
    const tbody = document.getElementById('trade-history-body');

    if (!trades || trades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" class="empty-row">No trades yet</td></tr>';
        return;
    }

    const safe = (v, dp) => (v != null && !isNaN(v)) ? Number(v).toFixed(dp) : '—';

    tbody.innerHTML = [...trades].reverse().map(trade => {
        const time = new Date(trade.exit_time).toLocaleString();
        const sideClass = trade.side === 'long' ? 'trade-long' : 'trade-short';
        const pnlClass = trade.pnl_usdt >= 0 ? 'trade-profit' : 'trade-loss';
        const modeText = trade.paper ? 'PAPER' : 'REAL';

        return `<tr>
            <td>${time}</td>
            <td class="${sideClass}">${(trade.side || '').toUpperCase()}</td>
            <td>$${safe(trade.entry_price, 2)}</td>
            <td>$${safe(trade.exit_price, 2)}</td>
            <td>${safe(trade.quantity, 6)}</td>
            <td class="${pnlClass}">$${safe(trade.pnl_usdt, 2)}</td>
            <td class="${pnlClass}">₹${safe(trade.pnl_inr, 0)}</td>
            <td>${trade.exit_reason || '—'}</td>
            <td class="trade-paper">${modeText}</td>
        </tr>`;
    }).join('');
}

// ─── Control Actions ───

function startBot() {
    fetch('/api/start', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                updateBotStatus(true);
            }
        })
        .catch(err => console.error('Start error:', err));
}

function stopBot() {
    fetch('/api/stop', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                updateBotStatus(false);
            }
        })
        .catch(err => console.error('Stop error:', err));
}

function setDirection(direction) {
    fetch('/api/direction', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ direction })
    })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                updateDirectionButtons(data.direction);
            }
        })
        .catch(err => console.error('Direction error:', err));
}

function closePosition() {
    // BUG5: Disable button immediately to prevent repeated calls
    const btn = document.getElementById('btn-close-pos');
    if (btn) btn.disabled = true;

    fetch('/api/close_position', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.no_position) {
                // No position to close — just update display
                console.log('No open position to close');
            } else {
                console.log('Close position result:', data);
            }
            requestInitialData();
        })
        .catch(err => {
            console.error('Close position error:', err);
            requestInitialData();
        });
}

// ─── User Interaction: Manual Trade ───

function setManualSide(side) {
    manualTradeSide = side;
    document.getElementById('manual-btn-long').classList.remove('active');
    document.getElementById('manual-btn-short').classList.remove('active');
    if (side === 'long') {
        document.getElementById('manual-btn-long').classList.add('active');
    } else {
        document.getElementById('manual-btn-short').classList.add('active');
    }
}

function setManualPriceMarket() {
    document.getElementById('manual-price').value = lastPrice > 0 ? lastPrice.toFixed(2) : '';
    document.getElementById('manual-price').placeholder = lastPrice > 0 ? `$${lastPrice.toFixed(2)}` : 'Current price';
}

function executeManualTrade() {
    const priceInput = document.getElementById('manual-price');
    const price = parseFloat(priceInput.value) || lastPrice || 0;
    const feedback = document.getElementById('manual-trade-feedback');
    const btn = document.getElementById('btn-manual-trade');

    if (price <= 0) {
        feedback.innerHTML = '<span style="color:#ff4444;">❌ No valid price available. Wait for market data.</span>';
        return;
    }

    btn.disabled = true;
    btn.textContent = '⏳ Placing...';
    feedback.innerHTML = '';

    fetch('/api/manual_trade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            side: manualTradeSide,
            price: price,
        })
    })
        .then(r => r.json())
        .then(data => {
            btn.disabled = false;
            btn.textContent = '⚡ Execute Trade';
            if (data.success) {
                feedback.innerHTML = `<span style="color:#00ff88;">✅ ${manualTradeSide.toUpperCase()} trade placed at $${price.toFixed(2)}</span>`;
                requestInitialData();
            } else {
                feedback.innerHTML = `<span style="color:#ff4444;">❌ ${data.error || data.reason || 'Trade failed'}</span>`;
            }
        })
        .catch(err => {
            btn.disabled = false;
            btn.textContent = '⚡ Execute Trade';
            feedback.innerHTML = `<span style="color:#ff4444;">❌ Network error: ${err.message}</span>`;
        });
}

// ─── User Interaction: Config Panel ───

function toggleConfigPanel() {
    configPanelOpen = !configPanelOpen;
    const panel = document.getElementById('config-panel');
    const btn = document.getElementById('config-toggle-btn');
    if (configPanelOpen) {
        panel.style.display = 'block';
        btn.textContent = 'Hide';
        fetchConfig();
    } else {
        panel.style.display = 'none';
        btn.textContent = 'Show';
    }
}

function fetchConfig() {
    fetch('/api/config')
        .then(r => r.json())
        .then(data => {
            if (data.success && data.config) {
                renderConfig(data.config);
            }
        })
        .catch(err => console.error('Config fetch error:', err));
}

function renderConfig(cfg) {
    const grid = document.getElementById('config-grid');

    const fields = [
        { key: 'trade_amount_inr', label: 'Trade Amount (₹)', type: 'number', min: 1000, max: 100000, step: 1000 },
        { key: 'max_daily_loss_inr', label: 'Max Daily Loss (₹)', type: 'number', min: 500, max: 50000, step: 500 },
        { key: 'max_trades_per_day', label: 'Max Trades/Day', type: 'number', min: 1, max: 100, step: 1 },
        { key: 'trail_pct', label: 'Trail %', type: 'number', min: 0.001, max: 0.05, step: 0.001 },
        { key: 'near_threshold', label: 'BB Near Threshold %', type: 'number', min: 0.0005, max: 0.02, step: 0.0005 },
        { key: 'cooldown_minutes', label: 'Cooldown (min)', type: 'number', min: 1, max: 60, step: 1 },
        { key: 'close_on_session_end', label: 'Close on Session End', type: 'checkbox' },
    ];

    // Also show read-only fields
    const roFields = [
        { key: 'symbol', label: 'Symbol' },
        { key: 'timeframe', label: 'Timeframe' },
        { key: 'trading_mode', label: 'Trading Mode' },
        { key: 'usd_inr_rate', label: 'USD/INR Rate' },
        { key: 'bb_period', label: 'BB Period' },
        { key: 'bb_std', label: 'BB Std Dev' },
    ];

    let html = '';
    fields.forEach(f => {
        const val = cfg[f.key] !== undefined ? cfg[f.key] : '';
        if (f.type === 'checkbox') {
            html += `<div class="config-row">
                <span class="config-label">${f.label}</span>
                <label class="config-switch">
                    <input type="checkbox" id="cfg-${f.key}" ${val ? 'checked' : ''}>
                    <span class="config-slider"></span>
                </label>
            </div>`;
        } else {
            html += `<div class="config-row">
                <span class="config-label">${f.label}</span>
                <input type="${f.type}" id="cfg-${f.key}" value="${val}" min="${f.min || ''}" max="${f.max || ''}" step="${f.step || ''}" class="config-input">
            </div>`;
        }
    });

    roFields.forEach(f => {
        html += `<div class="config-row">
            <span class="config-label">${f.label} <small>(read-only)</small></span>
            <input type="text" value="${cfg[f.key] || '—'}" disabled class="config-input config-readonly">
        </div>`;
    });

    grid.innerHTML = html;
}

function saveConfig() {
    const feedback = document.getElementById('config-feedback');
    const payload = {};

    // Collect editable fields
    const fields = ['trade_amount_inr', 'max_daily_loss_inr', 'max_trades_per_day', 'trail_pct', 'near_threshold', 'cooldown_minutes'];
    fields.forEach(key => {
        const el = document.getElementById('cfg-' + key);
        if (el) {
            payload[key] = el.type === 'number' ? parseFloat(el.value) : el.value;
        }
    });

    // Checkbox
    const cbEl = document.getElementById('cfg-close_on_session_end');
    if (cbEl) {
        payload['close_on_session_end'] = cbEl.checked;
    }

    feedback.innerHTML = '<span style="color:#ffd700;">⏳ Saving...</span>';

    fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                feedback.innerHTML = `<span style="color:#00ff88;">✅ Saved: ${data.updated.join(', ') || 'no changes'}</span>`;
                if (data.ignored && data.ignored.length > 0) {
                    feedback.innerHTML += `<br><span style="color:#ff9800;">⚠️ Ignored: ${data.ignored.join('; ')}</span>`;
                }
            } else {
                feedback.innerHTML = `<span style="color:#ff4444;">❌ ${data.error}</span>`;
            }
        })
        .catch(err => {
            feedback.innerHTML = `<span style="color:#ff4444;">❌ Network error: ${err.message}</span>`;
        });
}

// ─── User Interaction: Log Viewer ───

function toggleLogPanel() {
    logPanelOpen = !logPanelOpen;
    const panel = document.getElementById('log-panel');
    const btn = document.getElementById('log-toggle-btn');
    if (logPanelOpen) {
        panel.style.display = 'block';
        btn.textContent = 'Hide';
        refreshLogs();
    } else {
        panel.style.display = 'none';
        btn.textContent = 'Show';
    }
}

function refreshLogs() {
    const viewer = document.getElementById('log-viewer');
    viewer.innerHTML = '<div class="log-loading">Loading logs...</div>';

    fetch('/api/logs')
        .then(r => r.json())
        .then(data => {
            if (data.success && data.logs) {
                if (data.logs.length === 0) {
                    viewer.innerHTML = '<div class="log-empty">No log entries yet.</div>';
                } else {
                    viewer.innerHTML = data.logs.map(line => {
                        let cls = 'log-line';
                        if (line.includes('ERROR') || line.includes('CRITICAL')) cls += ' log-error';
                        else if (line.includes('WARNING')) cls += ' log-warn';
                        else if (line.includes('SUCCESS') || line.includes('profit')) cls += ' log-success';
                        const escaped = line.replace(/</g, '<').replace(/>/g, '>');
                        return `<div class="${cls}">${escaped}</div>`;
                    }).join('');
                }
            } else {
                viewer.innerHTML = `<div class="log-error">Error: ${data.error || 'Unknown'}</div>`;
            }
        })
        .catch(err => {
            viewer.innerHTML = `<div class="log-error">Network error: ${err.message}</div>`;
        });
}

// ─── Chart Rendering ───

function renderChart() {
    const canvas = document.getElementById('price-chart');
    const ctx = canvas.getContext('2d');

    // Set canvas size
    canvas.width = canvas.parentElement.clientWidth - 32;
    canvas.height = 400;

    const candles = chartData.candles || [];
    const bb = chartData.bb || {};
    const currentPrice = chartData.current_price || 0;

    if (candles.length === 0) {
        ctx.fillStyle = '#7a8ba8';
        ctx.font = '16px Segoe UI';
        ctx.textAlign = 'center';
        ctx.fillText('Loading chart data from sharkexchange.in...', canvas.width / 2, canvas.height / 2);
        return;
    }

    // Calculate price range
    const prices = candles.map(c => [c.high, c.low]).flat();
    if (bb.upper) prices.push(...bb.upper.filter(v => v !== null));
    if (bb.lower) prices.push(...bb.lower.filter(v => v !== null));
    if (currentPrice) prices.push(currentPrice);

    const minPrice = Math.min(...prices) * 0.999;
    const maxPrice = Math.max(...prices) * 1.001;
    const priceRange = maxPrice - minPrice;

    // Chart dimensions
    const padding = { top: 20, right: 80, bottom: 40, left: 10 };
    const chartW = canvas.width - padding.left - padding.right;
    const chartH = canvas.height - padding.top - padding.bottom;

    // Clear canvas
    ctx.fillStyle = '#0d1520';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // Helper functions
    const priceToY = (price) => padding.top + chartH * (1 - (price - minPrice) / priceRange);
    const indexToX = (i) => padding.left + (i / (candles.length - 1)) * chartW;

    // ─── Draw Bollinger Bands ───
    if (bb.middle && bb.upper && bb.lower) {
        // Fill between bands
        ctx.beginPath();
        let started = false;
        for (let i = 0; i < bb.upper.length; i++) {
            if (bb.upper[i] !== null) {
                const x = indexToX(i);
                const y = priceToY(bb.upper[i]);
                if (!started) { ctx.moveTo(x, y); started = true; }
                else ctx.lineTo(x, y);
            }
        }
        for (let i = bb.lower.length - 1; i >= 0; i--) {
            if (bb.lower[i] !== null) {
                const x = indexToX(i);
                const y = priceToY(bb.lower[i]);
                ctx.lineTo(x, y);
            }
        }
        ctx.closePath();
        ctx.fillStyle = 'rgba(0, 212, 255, 0.05)';
        ctx.fill();

        // Upper band line
        ctx.beginPath();
        ctx.strokeStyle = '#ff9800';
        ctx.lineWidth = 1.5;
        started = false;
        for (let i = 0; i < bb.upper.length; i++) {
            if (bb.upper[i] !== null) {
                const x = indexToX(i);
                const y = priceToY(bb.upper[i]);
                if (!started) { ctx.moveTo(x, y); started = true; }
                else ctx.lineTo(x, y);
            }
        }
        ctx.stroke();

        // Middle band line (SMA)
        ctx.beginPath();
        ctx.strokeStyle = '#00d4ff';
        ctx.lineWidth = 1.5;
        started = false;
        for (let i = 0; i < bb.middle.length; i++) {
            if (bb.middle[i] !== null) {
                const x = indexToX(i);
                const y = priceToY(bb.middle[i]);
                if (!started) { ctx.moveTo(x, y); started = true; }
                else ctx.lineTo(x, y);
            }
        }
        ctx.stroke();

        // Lower band line
        ctx.beginPath();
        ctx.strokeStyle = '#ff9800';
        ctx.lineWidth = 1.5;
        started = false;
        for (let i = 0; i < bb.lower.length; i++) {
            if (bb.lower[i] !== null) {
                const x = indexToX(i);
                const y = priceToY(bb.lower[i]);
                if (!started) { ctx.moveTo(x, y); started = true; }
                else ctx.lineTo(x, y);
            }
        }
        ctx.stroke();
    }

    // ─── Draw Candles ───
    const candleWidth = Math.max(2, chartW / candles.length * 0.6);

    candles.forEach((candle, i) => {
        const x = indexToX(i);
        const isGreen = candle.close >= candle.open;
        const bodyTop = priceToY(Math.max(candle.open, candle.close));
        const bodyBottom = priceToY(Math.min(candle.open, candle.close));
        const bodyHeight = Math.max(1, bodyBottom - bodyTop);

        // Wick
        ctx.strokeStyle = isGreen ? '#00ff88' : '#ff4444';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x, priceToY(candle.high));
        ctx.lineTo(x, priceToY(candle.low));
        ctx.stroke();

        // Body
        ctx.fillStyle = isGreen ? '#00ff88' : '#ff4444';
        ctx.fillRect(x - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
    });

    // ─── Draw Current Price Line ───
    if (currentPrice > 0) {
        const y = priceToY(currentPrice);
        ctx.strokeStyle = '#00d4ff';
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(canvas.width - padding.right, y);
        ctx.stroke();
        ctx.setLineDash([]);

        // Price label
        ctx.fillStyle = '#00d4ff';
        ctx.font = 'bold 12px Segoe UI';
        ctx.textAlign = 'left';
        ctx.fillText(`$${currentPrice.toFixed(0)}`, canvas.width - padding.right + 4, y + 4);
    }

    // ─── Price Scale (right side) ───
    ctx.fillStyle = '#7a8ba8';
    ctx.font = '11px Segoe UI';
    ctx.textAlign = 'left';
    const scaleSteps = 6;
    for (let i = 0; i <= scaleSteps; i++) {
        const price = minPrice + (priceRange * i / scaleSteps);
        const y = priceToY(price);
        ctx.fillText(`$${price.toFixed(0)}`, canvas.width - padding.right + 4, y);

        // Grid line
        ctx.strokeStyle = '#1a2332';
        ctx.lineWidth = 0.5;
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(canvas.width - padding.right, y);
        ctx.stroke();
    }

    // ─── Data source label ───
    ctx.fillStyle = '#7a8ba8';
    ctx.font = '10px Segoe UI';
    ctx.textAlign = 'right';
    ctx.fillText('Data: sharkexchange.in', canvas.width - padding.right, canvas.height - 8);

    // ─── Update timestamp ───
    document.getElementById('last-update').textContent = `Last update: ${new Date().toLocaleTimeString()}`;
}