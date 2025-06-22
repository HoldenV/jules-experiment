# Mean Reversion Trading Bot

This project implements a mean reversion trading bot that trades U.S. stocks based on z-score signals. It is designed to run once per day and interacts with the Alpaca API for market data and order execution.

## Features

- **Strategy**: Time-series mean reversion using z-scores.
- **Universe**: Top 50 most liquid U.S. stocks (configurable, currently hardcoded).
- **Signal Generation**:
  - Z-score: `(current_price - 30-day moving average) / 30-day rolling standard deviation`
  - Long entry: z < -1.5
  - Short entry: z > 1.5
  - Exit: z-score crosses back near zero (configurable thresholds).
  - Stop-loss: z-score exceeds outer thresholds (configurable).
- **Order Management**:
  - Uses limit orders at the current price.
  - Tracks order fills.
  - Manages one position per stock.
  - $100 position size per trade (configurable).
  - Avoids Pattern Day Trader (PDT) rule violations (basic check).
- **Risk Management**:
  - Max one open position per stock.
  - Checks available cash.
  - Max holding period of 5 calendar days (configurable).
- **Logging & State**:
  - Actions logged to `bot.log`.
  - Trades recorded to `trades.csv`.
  - Open positions stored in `positions.json`.
- **Code Structure**: Modular Python code with separate components for data, signals, orders, positions, and logging.
- **API**: Uses the Alpaca API via the `alpaca-trade-api` Python SDK.

## Project Structure

```md
.
├── trading_bot.py        # Main script to run the bot
├── config.py             # Configuration parameters (API keys, strategy params)
├── data_fetcher.py       # Fetches historical and real-time market data
├── signal_generator.py   # Calculates z-scores and generates trading signals
├── order_manager.py      # Handles order placement and status checking
├── position_manager.py   # Manages open positions, P&L, and state
├── logger.py             # Handles logging to console, log file, and trades CSV
├── requirements.txt      # Python dependencies
├── .env.example          # Example environment file for API keys (create .env from this)
├── README.md             # This file
└── positions.json        # Stores current open positions (created at runtime)
└── trades.csv            # Records completed trades (created at runtime)
└── bot.log               # General log file (created at runtime)
└── pending_entry_orders_today.json # Temp file for orders placed in current run (managed by bot)
```

## Setup

1. **Clone the repository:**

    ```bash
    git clone <repository-url>
    cd <repository-name>
    ```

2. **Create a virtual environment (recommended):**

    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```

3. **Install dependencies:**

    ```bash
    pip install -r requirements.txt
    ```

4. **Configure API Keys:**
    - Create a `.env` file in the project root (you can copy `.env.example` if it exists, or create a new one).
    - Open the `.env` file and add your Alpaca API key and secret key:

        ```env
        ALPACA_API_KEY="YOUR_PAPER_API_KEY"
        ALPACA_SECRET_KEY="YOUR_PAPER_SECRET_KEY"
        ```

        (Replace with your actual Alpaca API keys. Use paper trading keys for testing.)

5. **Review Configuration (`config.py`):**
    - Open `config.py`.
    - Set `ALPACA_PAPER = True` for paper trading or `False` for live trading. This setting determines which Alpaca API endpoint is used.
    - Adjust trading parameters (e.g., `POSITION_SIZE_USD`, `Z_SCORE_WINDOW`, `TICKERS`) as needed.
    - Note: The `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` variables in `config.py` are just placeholders; the actual keys are loaded from the `.env` file by the bot.

## Usage

To run the trading bot for a single daily execution:

```bash
python trading_bot.py
```

The bot will perform the following steps:

1. Connect to the Alpaca API using credentials from `.env`.
2. Manage existing open positions, including checking the status of any pending exit orders from previous runs.
3. Fetch necessary market data (historical prices for z-score calculation and latest prices for decision-making).
4. Evaluate all open positions for exit conditions (max holding period, stop-loss, z-score based exit signals) and place closing orders if required.
5. Evaluate new entry signals for the configured list of tickers.
6. If valid entry signals are found, check available cash and PDT rules, then place new entry limit orders.
7. Check the status of any new entry orders placed during the current run to see if they were filled. If filled, new positions are recorded.
8. Log all actions to `bot.log` and record any completed trades to `trades.csv`. Open positions are maintained in `positions.json`.

### Scheduling

The script `trading_bot.py` is designed for a single execution. To automate daily execution (e.g., at 12:00 PM Eastern Time as per the strategy's trade schedule), you'll need to use an external scheduler:

- **cron** on Linux/macOS.
- **Task Scheduler** on Windows.
- A cloud-based scheduler (e.g., AWS Lambda scheduled events, Google Cloud Scheduler) if deploying to a server.

**Example cron job to run daily at 12:00 PM system time:**
(Ensure paths are correct and your virtual environment is activated if needed, or call the python executable from the venv directly)

```cron
0 12 * * * /path/to/your/project/venv/bin/python /path/to/your/project/trading_bot.py >> /path/to/your/project/cron.log 2>&1
```

Adjust the time (e.g., `16:00` for 12:00 PM ET if your server is in UTC and ET is UTC-4, considering DST) and paths accordingly.

## Important Notes

- **Risk Disclaimer**: Trading financial instruments involves substantial risk of loss. This bot is provided for educational and illustrative purposes only and should **not** be used for live trading without thorough testing, understanding the complete codebase, and full awareness of the risks involved. Past performance is not indicative of future results.
- **Paper Trading**: It is **strongly recommended** to test this bot extensively in Alpaca's paper trading environment before even considering live trading.
- **PDT Rules**: The bot includes a basic check for Pattern Day Trader (PDT) rules, primarily relying on the `daytrade_count` from the Alpaca API. It's crucial to understand how these rules apply to your specific account and trading activity.
- **Market Hours**: The bot does not explicitly check for market hours before placing orders. Alpaca's API might reject orders placed outside market hours or queue them depending on the order type and TIF. Ensure your scheduling aligns with market open times if immediate execution is desired for 'day' orders.
- **Error Handling**: The bot includes basic error handling. Monitor `bot.log` regularly for any issues or unexpected behavior.
- **Dependencies**: Ensure all Python dependencies listed in `requirements.txt` are installed in your environment.

## TODOs / Potential Enhancements

- Dynamic stock universe selection (e.g., based on daily liquidity, volume, or other factors).
- More sophisticated PDT rule management and pre-trade checks.
- Advanced order execution logic (e.g., handling partial fills more robustly, using different Time-In-Force options, or algorithmic orders like TWAP/VWAP if supported and desired).
- Integration with a more robust scheduling, monitoring, and alerting system for production use.
- A dedicated backtesting framework to thoroughly test strategy parameters and variations.
- More comprehensive error handling, including retry mechanisms for transient API issues.
- A simple web dashboard or regular email reports for monitoring bot status, open positions, and performance.
- **Review `data_fetcher.py` - `get_historical_data`:**
  - Clarify and potentially adjust date calculations for `start_date` and `end_date` to ensure correctness regardless of when the bot is run (e.g., intraday vs. post-market).
  - Evaluate if the `+5` day buffer for `start_date` is always sufficient for `limit_per_ticker` actual trading days, or if using Alpaca's `limit` parameter directly is more reliable.
  - Remove duplicate empty DataFrame check.
- **Review `position_manager.py` - `check_and_manage_open_positions`:**
  - Refine the method for appending `current_price` to historical data for z-score calculation to ensure robustness, especially if data frequencies or timings change.
  - Improve fallback logic for `new_index` creation when historical data doesn't have a `DatetimeIndex`.
- **Implement robust PDT tracking in `position_manager.py` - `get_pdt_trade_count`:**
  - The current implementation is a placeholder. Full PDT tracking requires careful handling of trade times and definitions.
- **Clarify `pending_orders.json` management in `trading_bot.py`:**
  - Define the intended lifecycle of `pending_orders.json`. If it's for persistent tracking across runs, the current deletion logic after snapshotting to `RUN_PENDING_ORDERS_FILE` needs review.
  - Ensure the state synchronization with Alpaca at the start of each run is the primary source of truth for open orders.
- **Review Z-Score signal generation in `signal_generator.py`:**
  - Double-check the logic for `EXIT_LONG` and `EXIT_SHORT` conditions (e.g., `Z_EXIT_LONG > current_z_score > Z_ENTRY_LONG`) to ensure they behave as expected under all z-score configurations.
- **State Management Strategy:**
  - Solidify the strategy for how `positions.json` and `pending_orders.json` interact and how their state is reconciled with Alpaca, especially concerning persistence versus derivation from the broker.
- **Code Comments:**
  - Perform a pass to remove overly verbose or obvious comments and ensure remaining comments explain "why" rather than "what," or clarify complex logic.
