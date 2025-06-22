import os
from datetime import datetime

# --- Alpaca API Configuration ---
# API keys are loaded from .env file in the bot. These are placeholders.
ALPACA_API_KEY = "YOUR_ALPACA_API_KEY_IN_ENV"
ALPACA_SECRET_KEY = "YOUR_ALPACA_SECRET_KEY_IN_ENV"

# True for paper trading, False for live trading.
ALPACA_PAPER = True

# --- Trading Strategy Parameters ---

# Number of most liquid U.S. stocks to consider (for future dynamic universe implementation).
TOP_N_STOCKS = 50

# Target dollar amount for each new position.
POSITION_SIZE_USD = 100

# Maximum calendar days to hold a position.
MAX_HOLDING_PERIOD_DAYS = 5


# --- Z-Score Signal Parameters ---

# Rolling window (days) for MA and STD calculation.
Z_SCORE_WINDOW = 30

# Z-score threshold for long entry.
Z_ENTRY_LONG = -1.5

# Z-score threshold for short entry.
Z_ENTRY_SHORT = 1.5

# Z-score threshold for long exit (reversion to mean).
Z_EXIT_LONG = -0.1

# Z-score threshold for short exit (reversion to mean).
Z_EXIT_SHORT = 0.1

# Z-score threshold for long position stop-loss.
Z_STOP_LOSS_LONG = -3.0

# Z-score threshold for short position stop-loss.
Z_STOP_LOSS_SHORT = 3.0


# --- Stock Universe ---

# List of stock tickers to trade.
# TODO: Implement dynamic generation based on liquidity or other criteria.
TICKERS = [
    "AAPL", "MSFT", "GOOG", "AMZN", "TSLA", "NVDA", "JPM", "JNJ", "V",
    "PG", "UNH", "HD", "MA", "BAC", "DIS", "PYPL", "ADBE", "CMCSA", "XOM",
    "NFLX", "T", "CSCO", "PEP", "CVX", "ABT", "MRK", "PFE", "NKE", "KO",
    "MCD", "WMT", "CRM", "INTC", "VZ", "LLY", "ABBV", "NEE", "MDT", "COST",
    "BMY", "HON", "LIN", "SBUX", "BLK", "AMT", "GS", "CAT", "AXP", "BA", "SOFI"
]


# --- File Paths for Logging and State ---

# Directory for all logs and run outputs
LOGS_DIR = os.path.join(os.path.dirname(__file__), 'logs')
RUNS_DIR = os.path.join(LOGS_DIR, 'runs')

# Generate a timestamped run directory (e.g., logs/runs/2025-06-22_12-34-56)
RUN_TIMESTAMP = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
CURRENT_RUN_DIR = os.path.join(RUNS_DIR, RUN_TIMESTAMP)

# Path to the main log file for the current run
LOG_FILE = os.path.join(CURRENT_RUN_DIR, 'bot.log')

# Path to the JSON file for current open positions for the current run
POSITIONS_FILE = os.path.join(CURRENT_RUN_DIR, 'positions.json')

# Path to the CSV file for completed trades (keep at top-level logs directory for summary, or move to run dir if desired)
TRADES_CSV_FILE = os.path.join(LOGS_DIR, 'trades.csv')

# Path to the JSON file for tracking all pending orders (entry and exit) across bot runs
# This file maintains the current state of pending orders across sessions.
PENDING_ORDERS_FILE = os.path.join(LOGS_DIR, 'pending_orders.json')

# Path to the JSON file for a snapshot of pending orders for the current run
# This provides a historical record of pending orders at the time of each bot run.
RUN_PENDING_ORDERS_FILE = os.path.join(CURRENT_RUN_DIR, 'pending_orders.json')
