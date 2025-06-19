# --- Alpaca API Configuration ---
# These should ideally be set in the .env file and loaded via os.getenv()
# However, they are placed here as placeholders if not using .env for some reason.
# It's STRONGLY recommended to use .env for sensitive keys.
ALPACA_API_KEY = "YOUR_ALPACA_API_KEY_IN_ENV" # Placeholder if .env is not used
ALPACA_SECRET_KEY = "YOUR_ALPACA_SECRET_KEY_IN_ENV" # Placeholder if .env is not used

# Specifies whether to use Alpaca's paper trading environment.
# True for paper trading, False for live trading.
ALPACA_PAPER = True

# --- Trading Strategy Parameters ---

# The number of most liquid U.S. stocks to consider in the universe.
# Currently, the list of tickers is hardcoded. This parameter is for future use if a dynamic universe is implemented.
TOP_N_STOCKS = 50

# The target dollar amount for each new position.
# The actual number of shares will be calculated based on the current price.
POSITION_SIZE_USD = 100

# Maximum number of calendar days to hold a position before exiting, regardless of other signals.
MAX_HOLDING_PERIOD_DAYS = 5


# --- Z-Score Signal Parameters ---

# The rolling window period (in days, assuming daily data) for calculating moving average and standard deviation.
Z_SCORE_WINDOW = 30

# Z-score threshold to initiate a long position (current_z_score < Z_ENTRY_LONG).
Z_ENTRY_LONG = -1.5

# Z-score threshold to initiate a short position (current_z_score > Z_ENTRY_SHORT).
Z_ENTRY_SHORT = 1.5

# Z-score threshold to exit a long position when it reverts towards zero (current_z_score > Z_EXIT_LONG).
Z_EXIT_LONG = -0.1

# Z-score threshold to exit a short position when it reverts towards zero (current_z_score < Z_EXIT_SHORT).
Z_EXIT_SHORT = 0.1

# Z-score threshold for a stop-loss on a long position (current_z_score < Z_STOP_LOSS_LONG).
Z_STOP_LOSS_LONG = -3.0

# Z-score threshold for a stop-loss on a short position (current_z_score > Z_STOP_LOSS_SHORT).
Z_STOP_LOSS_SHORT = 3.0


# --- Stock Universe ---

# Hardcoded list of stock tickers to trade.
# In a more advanced system, this could be dynamically generated based on liquidity or other criteria.
TICKERS = [
    "AAPL", "MSFT", "GOOG", "AMZN", "TSLA", "NVDA", "BRK-A", "JPM", "JNJ", "V",
    "PG", "UNH", "HD", "MA", "BAC", "DIS", "PYPL", "ADBE", "CMCSA", "XOM",
    "NFLX", "T", "CSCO", "PEP", "CVX", "ABT", "MRK", "PFE", "NKE", "KO",
    "MCD", "WMT", "CRM", "INTC", "VZ", "LLY", "ABBV", "NEE", "MDT", "COST",
    "BMY", "HON", "LIN", "SBUX", "BLK", "AMT", "GS", "CAT", "AXP", "BA"
    # Note: BRK-A might have very high price, affecting position sizing. Consider BRK-B.
]


# --- File Paths for Logging and State ---

# Path to the main log file where bot actions and messages are recorded.
LOG_FILE = "bot.log"

# Path to the CSV file where details of completed trades are recorded.
TRADES_CSV_FILE = "trades.csv"

# Path to the JSON file where current open positions and their metadata are stored.
POSITIONS_FILE = "positions.json"

# Path to the JSON file for tracking entry orders placed within the current bot run.
# This is mainly for 'day' Time-In-Force orders to check their fill status before the bot session ends.
PENDING_ENTRY_ORDERS_TODAY_FILE = "pending_entry_orders_today.json" # Already defined in trading_bot.py, ensure consistency or move definition here.
# For now, trading_bot.py defines its own constant for this. This could be centralized.
