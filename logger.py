import csv
import os
from datetime import datetime
import config

def log_action(message):
    """
    Logs an action message to the bot's log file with a timestamp.
    :param message: The message string to log.
    """
    os.makedirs(os.path.dirname(config.LOG_FILE), exist_ok=True)

    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f"{timestamp} - {message}\n"
    try:
        with open(config.LOG_FILE, 'a') as f:
            f.write(log_entry)
    except Exception as e:
        # Fallback to print if logging to file fails
        print(f"CRITICAL: Error writing to log file {config.LOG_FILE}: {e}. Log message: {log_entry.strip()}")

def record_trade(ticker, entry_date, exit_date, entry_price, exit_price, profit_loss, reason_for_exit):
    """
    Records a completed trade to the trades CSV file.
    :param ticker: Stock ticker.
    :param entry_date: Entry date/time (string or datetime).
    :param exit_date: Exit date/time (string or datetime).
    :param entry_price: Entry price.
    :param exit_price: Exit price.
    :param profit_loss: Profit or loss from the trade.
    :param reason_for_exit: Reason for closing the trade.
    """
    os.makedirs(os.path.dirname(config.TRADES_CSV_FILE), exist_ok=True)

    file_exists = os.path.isfile(config.TRADES_CSV_FILE)
    fieldnames = ['Ticker', 'EntryDate', 'ExitDate', 'EntryPrice', 'ExitPrice', 'ProfitLoss', 'ExitReason']

    entry_date_str = entry_date.strftime('%Y-%m-%d %H:%M:%S') if isinstance(entry_date, datetime) else str(entry_date)
    exit_date_str = exit_date.strftime('%Y-%m-%d %H:%M:%S') if isinstance(exit_date, datetime) else str(exit_date)

    row = {
        'Ticker': ticker,
        'EntryDate': entry_date_str,
        'ExitDate': exit_date_str,
        'EntryPrice': f"{entry_price:.2f}",
        'ExitPrice': f"{exit_price:.2f}",
        'ProfitLoss': f"{profit_loss:.2f}",
        'ExitReason': reason_for_exit
    }

    try:
        with open(config.TRADES_CSV_FILE, 'a', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            if not file_exists or os.path.getsize(config.TRADES_CSV_FILE) == 0:
                writer.writeheader()
            writer.writerow(row)
        log_action(f"Recorded trade for {ticker}: P&L {profit_loss:.2f}, Exit Reason: {reason_for_exit}")
    except Exception as e:
        # Fallback to print if logging to file fails
        error_message = f"CRITICAL: Error writing to trades CSV {config.TRADES_CSV_FILE}: {e}. Trade data: {row}"
        log_action(error_message) # Attempt to log the error itself
        print(error_message)


if __name__ == '__main__':
    # Example Usage and Test
    if os.path.exists(config.LOG_FILE):
        os.remove(config.LOG_FILE)
    if os.path.exists(config.TRADES_CSV_FILE):
        os.remove(config.TRADES_CSV_FILE)

    log_action("Bot session started.")
    log_action("Fetching data for AAPL.")

    record_trade(
        ticker="AAPL",
        entry_date=datetime(2023, 1, 10, 9, 30, 0),
        exit_date=datetime(2023, 1, 12, 15, 0, 0),
        entry_price=150.25,
        exit_price=155.75,
        profit_loss=550.00,
        reason_for_exit="signal"
    )

    record_trade(
        ticker="MSFT",
        entry_date="2023-02-01 10:00:00",
        exit_date="2023-02-05 14:30:00",
        entry_price=280.50,
        exit_price=275.00,
        profit_loss=-275.00,
        reason_for_exit="stop-loss"
    )
    log_action("Bot session ended.")

    print(f"Log file '{config.LOG_FILE}' and CSV file '{config.TRADES_CSV_FILE}' created with example entries.")

    print(f"\n--- Contents of {config.LOG_FILE} ---")
    if os.path.exists(config.LOG_FILE):
        with open(config.LOG_FILE, 'r') as f:
            print(f.read())
    else:
        print("Log file not found.")

    print(f"\n--- Contents of {config.TRADES_CSV_FILE} ---")
    if os.path.exists(config.TRADES_CSV_FILE):
        with open(config.TRADES_CSV_FILE, 'r') as f:
            print(f.read())
    else:
        print("Trades CSV file not found.")
