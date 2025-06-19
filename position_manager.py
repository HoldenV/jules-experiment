import json
import os
from datetime import datetime, timedelta
import pandas as pd # For type hinting if historical_data is DataFrame
import config
import logger
import order_manager # For placing exit orders
import signal_generator # For z-score and signal generation

# Note: Alpaca API client (api) is not initialized here.
# It's expected to be passed to functions like get_available_cash if needed,
# or relevant data (like cash) is fetched in the main bot and passed down.

def load_positions():
    """
    Loads current open positions from the positions JSON file.
    Converts date strings back to datetime objects.
    :return: Dictionary of open positions {ticker: {details}}, or empty {} if file not found/error.
    """
    if not os.path.exists(config.POSITIONS_FILE):
        return {}
    try:
        with open(config.POSITIONS_FILE, 'r') as f:
            positions_raw = json.load(f)

        # Convert date strings back to datetime objects
        for ticker, details in positions_raw.items():
            if 'entry_date' in details and isinstance(details['entry_date'], str):
                details['entry_date'] = datetime.fromisoformat(details['entry_date'])
            # Handle 'pending_exit_order_placed_at' if it exists
            if 'pending_exit_order_placed_at' in details and isinstance(details['pending_exit_order_placed_at'], str):
                details['pending_exit_order_placed_at'] = datetime.fromisoformat(details['pending_exit_order_placed_at'])
        return positions_raw
    except Exception as e:
        logger.log_action(f"Error loading positions from {config.POSITIONS_FILE}: {e}")
        return {}

def save_positions(positions):
    """
    Saves current open positions to the positions JSON file.
    Converts datetime objects to ISO format strings for JSON serialization.
    :param positions: Dictionary of open positions {ticker: {details}}.
    """
    try:
        positions_serializable = {}
        for ticker, details in positions.items():
            details_copy = details.copy()
            if 'entry_date' in details_copy and isinstance(details_copy['entry_date'], datetime):
                details_copy['entry_date'] = details_copy['entry_date'].isoformat()
            # Handle 'pending_exit_order_placed_at' for serialization
            if 'pending_exit_order_placed_at' in details_copy and isinstance(details_copy['pending_exit_order_placed_at'], datetime):
                details_copy['pending_exit_order_placed_at'] = details_copy['pending_exit_order_placed_at'].isoformat()
            positions_serializable[ticker] = details_copy

        with open(config.POSITIONS_FILE, 'w') as f:
            json.dump(positions_serializable, f, indent=4)
    except Exception as e:
        logger.log_action(f"Error saving positions to {config.POSITIONS_FILE}: {e}")

def add_position(ticker, qty, entry_price, position_type, entry_order_id, entry_date=None):
    """
    Adds a new position after an entry order is filled.
    :param ticker: Stock ticker.
    :param qty: Quantity of shares.
    :param entry_price: Price at which the position was entered.
    :param position_type: "long" or "short".
    :param entry_order_id: The ID of the order that opened this position.
    :param entry_date: Datetime object of entry. Defaults to now if None.
    """
    positions = load_positions()
    if entry_date is None:
        entry_date = datetime.now()

    positions[ticker] = {
        "qty": qty,
        "entry_price": entry_price,
        "entry_date": entry_date, # Stored as datetime object in memory
        "type": position_type, # "long" or "short"
        "pnl": 0.0 # Initial P&L
    }
    save_positions(positions)
    logger.log_action(f"Added/Updated position: {qty} {ticker} @ {entry_price} ({position_type}) on {entry_date.strftime('%Y-%m-%d')}")

def remove_position(ticker, exit_price, exit_reason):
    """
    Removes a position and records the trade.
    :param ticker: Stock ticker.
    :param exit_price: Price at which the position was exited.
    :param exit_reason: String explaining why position was closed (e.g., "signal", "stop-loss", "max_hold").
    """
    positions = load_positions()
    if ticker in positions:
        pos_details = positions.pop(ticker)
        save_positions(positions)

        profit_loss = 0
        if pos_details['type'] == 'long':
            profit_loss = (exit_price - pos_details['entry_price']) * pos_details['qty']
        elif pos_details['type'] == 'short':
            profit_loss = (pos_details['entry_price'] - exit_price) * pos_details['qty']

        logger.record_trade(
            ticker,
            pos_details['entry_date'].strftime('%Y-%m-%d %H:%M:%S') if isinstance(pos_details['entry_date'], datetime) else pos_details['entry_date'],
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            pos_details['entry_price'],
            exit_price,
            profit_loss,
            exit_reason
        )
        logger.log_action(f"Closed position for {ticker}. Exit price: {exit_price}, Reason: {exit_reason}, P&L: {profit_loss:.2f}")
    else:
        logger.log_action(f"Attempted to remove position for {ticker}, but it was not found.")

def check_and_manage_positions(current_prices, all_historical_data):
    """
    Manages open positions: checks for max holding period, stop-loss, exit signals.
    This function would typically be called daily.
    :param current_prices: Dict {ticker: current_price}
    :param all_historical_data: Dict {ticker: pd.DataFrame of historical prices} for signal re-evaluation
    """
    positions = load_positions()
    if not positions:
        return

    logger.log_action("Checking and managing open positions...")
    today = datetime.now()

    for ticker, details in list(positions.items()): # Iterate over a copy for safe removal
        current_price = current_prices.get(ticker)
        if current_price is None:
            logger.log_action(f"Could not get current price for open position {ticker}. Skipping management for now.")
            continue

        # 1. Check Max Holding Period
        entry_date = details['entry_date']
        if isinstance(entry_date, str): # Ensure it's datetime
            entry_date = datetime.fromisoformat(entry_date)

        if (today - entry_date).days >= config.MAX_HOLDING_PERIOD_DAYS:
            logger.log_action(f"Position {ticker} hit max holding period of {config.MAX_HOLDING_PERIOD_DAYS} days.")
            # TODO: Implement order placement to close position
            # qty_to_close = details['qty']
            # side = 'sell' if details['type'] == 'long' else 'buy'
            # order = order_manager.place_limit_order(ticker, qty_to_close, current_price, side)
            # if order:
            #    logger.log_action(f"Placed order to close {ticker} due to max holding period.")
            #    remove_position(ticker, current_price, "max_hold_period") # Assuming order fills instantly for now
            # else:
            #    logger.log_action(f"Failed to place closing order for {ticker} (max hold). Will retry.")
            remove_position(ticker, current_price, "max_hold_period_mock_close") # Mock closing
            continue # Move to next position

        # 2. Check Stop-Loss and Exit Signals using current z-score
        # This requires historical data up to 'today' to calculate the most recent z-score
        ticker_historical_data = all_historical_data.get(ticker)
        if ticker_historical_data is None:
            logger.log_action(f"No historical data for {ticker} to re-evaluate z-score. Skipping stop/exit check.")
            continue

        # Append current price to historical data for up-to-date z-score
        # This is a simplification; ideally, the data fetcher provides data up to the point of decision.
        # For now, we assume historical_data includes the bar that resulted in current_price.
        # z_scores = signal_generator.calculate_zscore(ticker_historical_data)
        # if z_scores is None or z_scores.empty:
        #     logger.log_action(f"Could not calculate z-score for {ticker}. Skipping stop/exit check.")
        #     continue
        # current_z_score = z_scores.iloc[-1]

        # Let's assume signal_generator can take current price and derive z-score or we pass z-score directly
        # For simplicity, let's call generate_signals which internally might calculate z-score
        # This part needs careful handling of data for z-score calculation relative to current price.
        # The `signal_generator.generate_signals` expects historical_data_df for z-score calc.
        # We need to ensure this df is appropriate (e.g., ends just before current_price or includes it).

        # Simplified: Assume current_z_score is available or calculated by signal_generator
        # from the provided historical_data_df (which should be up-to-date).
        # current_signal = signal_generator.generate_signals(ticker, ticker_historical_data, current_z_score=None) # Recalculates z-score

        # TODO: This logic needs to be more robust.
        # We need a clear way to get the *current* z-score based on the *current* price.
        # The `generate_signals` function might need adjustment or a dedicated function for this.
        # For now, let's simulate this part.
        # Placeholder for z-score based exit/stop-loss
        # if details['type'] == 'long':
        #     if current_z_score > config.Z_EXIT_LONG and current_z_score < 0: # Exit long
        #         # place order, remove position
        #     elif current_z_score < config.Z_STOP_LOSS_LONG: # Stop loss long
        #         # place order, remove position
        # elif details['type'] == 'short':
        #     if current_z_score < config.Z_EXIT_SHORT and current_z_score > 0: # Exit short
        #         # place order, remove position
        #     elif current_z_score > config.Z_STOP_LOSS_SHORT: # Stop loss short
        #         # place order, remove position
        pass # End of loop

    # save_positions(positions) # Save any P&L updates if we were tracking that live

def get_pdt_trade_count(lookback_days=5):
    """
    Counts day trades within the last `lookback_days` business days.
    This is a simplified version. Real PDT tracking is complex and depends on broker's definition.
    It requires accurate trade execution times and distinguishing opening/closing trades of the same day.
    :return: Number of day trades.
    """
    # TODO: Implement more robust PDT tracking. This requires:
    # 1. Access to all trades (from trades.csv or Alpaca API).
    # 2. Identifying trades that are opened and closed on the same day for the same symbol.
    # For now, returns a placeholder.
    logger.log_action("PDT check: Simplified implementation. Always returns 0 for now.")
    return 0

def get_available_cash(api_client=None):
    """
    Gets available cash from Alpaca account.
    :param api_client: Initialized Alpaca API client.
    :return: Available cash as float, or a default high value if API client is None.
    """
    if api_client:
        try:
            account_info = api_client.get_account()
            return float(account_info.buying_power) # Or 'cash' or 'non_marginable_buying_power'
        except Exception as e:
            logger.log_action(f"Error fetching account info for cash: {e}")
            return 0.0 # Or raise error
    logger.log_action("Mock: Returning default high cash value as API client is not provided.")
    return 100000.0 # Default mock value

if __name__ == '__main__':
    # Setup dummy logger for testing this module standalone
    class DummyLogger:
        def log_action(self, message): print(f"LOG: {message}")
        def record_trade(self, ticker, entry_dt, exit_dt, entry_p, exit_p, pnl, reason):
            print(f"TRADE: {ticker}, Entry: {entry_dt} @ {entry_p}, Exit: {exit_dt} @ {exit_p}, PnL: {pnl}, Reason: {reason}")
    logger = DummyLogger() # Override module's logger

    # Clean up test files if they exist
    if os.path.exists(config.POSITIONS_FILE): os.remove(config.POSITIONS_FILE)
    if os.path.exists(config.TRADES_CSV_FILE): os.remove(config.TRADES_CSV_FILE) # Assuming logger.record_trade creates it

    # Test adding positions
    add_position("AAPL", 10, 150.00, "long", datetime(2023, 10, 1))
    add_position("MSFT", 5, 300.00, "short", datetime(2023, 10, 5))
    positions = load_positions()
    print("Current Positions:", positions)
    assert "AAPL" in positions
    assert positions["AAPL"]["qty"] == 10
    assert isinstance(positions["AAPL"]["entry_date"], datetime)

    # Test removing a position
    remove_position("AAPL", 155.00, "signal")
    positions = load_positions()
    print("Positions after AAPL removal:", positions)
    assert "AAPL" not in positions
    assert "MSFT" in positions

    # Test max holding period (will only log for now as order placement is TODO)
    # To test, we'd need to mock datetime.now() or set entry_date far in the past.
    # Let's simulate MSFT hitting max hold
    old_msft_pos = {
        "MSFT": {
            "qty": 5, "entry_price": 300.00, "type": "short",
            "entry_date": (datetime.now() - timedelta(days=config.MAX_HOLDING_PERIOD_DAYS + 1))
        }
    }
    save_positions(old_msft_pos) # Save this old position
    print(f"Simulating MSFT position older than {config.MAX_HOLDING_PERIOD_DAYS} days.")

    # Dummy data for check_and_manage_positions
    current_dummy_prices = {"MSFT": 290.00, "GOOG": 140.00}
    # In a real scenario, historical_data would be a DataFrame. Here, it's just for presence check.
    dummy_historical_data = {"MSFT": "some_data", "GOOG": "some_data"}

    check_and_manage_positions(current_dummy_prices, dummy_historical_data)
    positions_after_manage = load_positions()
    print("Positions after management (MSFT should be closed for max_hold_period_mock_close):", positions_after_manage)
    assert "MSFT" not in positions_after_manage # Because remove_position was called with mock close

    # Test PDT count (placeholder)
    print("PDT Trade Count:", get_pdt_trade_count())

    # Test available cash (placeholder)
    print("Available Cash:", get_available_cash())

    # Clean up test file
    if os.path.exists(config.POSITIONS_FILE): os.remove(config.POSITIONS_FILE)
