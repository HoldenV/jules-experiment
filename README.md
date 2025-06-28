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
  - Avoids Pattern Day Trader (PDT) rule violations (basic check by querying Alpaca account's `daytrade_count`).
- **Risk Management**:
  - Max one open position per stock.
  - Checks available cash.
  - Max holding period of 5 calendar days (configurable).
- **Logging & State**:
  - Actions for each run logged to a dedicated file (e.g., `logs/runs/<timestamp>/bot.log`).
  - All trades cumulatively recorded to `logs/trades.csv`.
  - Open positions at the end of each run stored in a run-specific file (e.g., `logs/runs/<timestamp>/positions.json`).
  - Persistent tracking of pending orders across runs in `logs/pending_orders.json`, with a snapshot also saved for each run.
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
├── logger.py             # Handles logging to console, run-specific log file, and global trades CSV
├── requirements.txt      # Python dependencies
├── .env.example          # Example environment file for API keys
├── README.md             # This file
├── logs/                   # Directory for all persistent logs and run-specific data
│   ├── trades.csv          # Records all completed trades across all runs
│   ├── pending_orders.json # Stores the current state of all pending orders across bot runs
│   └── runs/               # Contains data for each individual bot execution
│       └── <YYYY-MM-DD_HH-MM-SS>/ # Timestamped directory for a single bot run
│           ├── bot.log           # Log file for this specific run
│           ├── positions.json    # Stores open positions at the end of this specific run
│           └── pending_orders.json # Snapshot of pending orders during this specific run
└── __pycache__/          # Python cache directory (usually ignored)
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
    - Create a `.env` file in the project root (you can copy `.env.example`).
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
2. Synchronize local state (`pending_orders.json`, `positions.json`) with Alpaca, treating Alpaca as the source of truth for open orders and positions, while preserving relevant local metadata.
3. Fetch necessary market data (historical prices for z-score calculation and latest prices for decision-making).
4. Evaluate all open positions for exit conditions (max holding period, stop-loss, z-score based exit signals) and place closing orders if required.
5. Evaluate new entry signals for the configured list of tickers.
6. If valid entry signals are found, check available cash and PDT rules (using `daytrade_count` from Alpaca), then place new entry limit orders.
7. Check the status of any new entry orders placed during the current run to see if they were filled. If filled, new positions are recorded.
8. Log all actions to a run-specific log file (e.g., `logs/runs/<timestamp>/bot.log`). Record any completed trades to `logs/trades.csv`. Maintain the state of open positions in a run-specific `logs/runs/<timestamp>/positions.json` and update the global `logs/pending_orders.json` (and its run-specific snapshot).

### Scheduling

The script `trading_bot.py` is designed for a single execution. To automate daily execution (e.g., at 12:00 PM Eastern Time as per the strategy's trade schedule), you'll need to use an external scheduler:

- **cron** on Linux/macOS.
- **Task Scheduler** on Windows.
- A cloud-based scheduler (e.g., AWS Lambda scheduled events, Google Cloud Scheduler) if deploying to a server.

**Example cron job to run daily at 12:00 PM system time:**
(Ensure paths are correct and your virtual environment is activated if needed, or call the python executable from the venv directly)

```cron
0 12 * * * /path/to/your/project/venv/bin/python /path/to/your/project/trading_bot.py >> /path/to/your/project/logs/cron.log 2>&1
```
(Note: Consider directing cron output to a persistent log file within the `logs` directory, e.g., `logs/cron.log`)

Adjust the time (e.g., `16:00` for 12:00 PM ET if your server is in UTC and ET is UTC-4, considering DST) and paths accordingly.

## Important Notes

- **Risk Disclaimer**: Trading financial instruments involves substantial risk of loss. This bot is provided for educational and illustrative purposes only and should **not** be used for live trading without thorough testing, understanding the complete codebase, and full awareness of the risks involved. Past performance is not indicative of future results.
- **Paper Trading**: It is **strongly recommended** to test this bot extensively in Alpaca's paper trading environment before even considering live trading.
- **PDT Rules**: The bot includes a basic check for Pattern Day Trader (PDT) rules by fetching the `daytrade_count` from the Alpaca account object before placing new trades. It's crucial to understand how these rules apply to your specific account and trading activity, as this check does not constitute full PDT protection.
- **Market Hours**: The bot does not explicitly check for market hours before placing orders. Alpaca's API might reject orders placed outside market hours or queue them depending on the order type and TIF. Ensure your scheduling aligns with market open times if immediate execution is desired for 'day' orders.
- **Error Handling**: The bot includes improved error logging and handling for API interactions. However, it does not implement comprehensive retry mechanisms for all transient API issues. Monitor run-specific log files (e.g., `logs/runs/<timestamp>/bot.log`) regularly for any issues or unexpected behavior.
- **Dependencies**: Ensure all Python dependencies listed in `requirements.txt` are installed in your environment.

## TODOs / Potential Enhancements

- **Dynamic Stock Universe**: Implement dynamic stock universe selection (e.g., based on daily liquidity, volume, or other factors instead of the hardcoded `TICKERS` list).
- **PDT Rule Management**:
    - Enhance PDT rule management beyond the current `daytrade_count` check from Alpaca. This could involve more sophisticated pre-trade checks or local tracking if needed.
    - The `get_pdt_trade_count()` function in `position_manager.py` is still a placeholder and could be developed for more detailed local PDT tracking if desired, though the bot currently relies on the direct Alpaca account status.
- **Advanced Order Execution**: Explore more advanced order execution logic, such as handling partial fills more robustly, using different Time-In-Force options, or considering algorithmic orders (e.g., TWAP/VWAP) if supported and appropriate.
- **Production Features**: For more production-like deployment:
    - Integrate with a robust scheduling system.
    - Implement comprehensive monitoring and alerting (e.g., for errors, large losses, system health).
- **Backtesting Framework**: Develop or integrate a dedicated backtesting framework to thoroughly test strategy parameters and variations against historical data.
- **Error Handling & Resilience**: Further improve error handling with retry mechanisms for transient API issues and more fault tolerance.
- **Reporting**: Create a simple web dashboard or regular email reports for monitoring bot status, open positions, and performance.
- **Code Comments**: Continue to review and refine code comments to ensure they primarily explain "why" rather than "what," or clarify complex logic.

### Recently Addressed / Reviewed:
- **Data Fetcher Accuracy (`data_fetcher.py`):**
    - Date calculations for `start_date` and `end_date` in `get_historical_data` have been significantly improved using market calendars for correctness.
    - Historical data fetching now precisely uses the `limit` parameter based on calculated trading days.
- **Position Management Robustness (`position_manager.py`):**
    - Appending `current_price` to historical data for z-score re-calculation in `check_and_manage_open_positions` is more robust, including DatetimeIndex checks and timezone awareness attempts.
- **State Management Strategy (`trading_bot.py`, `config.py`):**
    - The lifecycle and interaction of `positions.json` (now run-specific) and `pending_orders.json` (global and run-specific snapshot) are clarified.
    - Synchronization with Alpaca at the start of each run now clearly treats Alpaca as the source of truth for positions and orders, supplemented by local data.
- **Z-Score Signal Logic (`signal_generator.py`):**
    - Exit logic conditions (`EXIT_LONG`, `EXIT_SHORT`) have been reviewed and appear consistent with the intended mean-reversion strategy.
