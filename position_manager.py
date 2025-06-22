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
    # Ensure the positions directory exists before reading (for new runs)
    os.makedirs(os.path.dirname(config.POSITIONS_FILE), exist_ok=True)

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
    # Ensure the positions directory exists before writing
    os.makedirs(os.path.dirname(config.POSITIONS_FILE), exist_ok=True)

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
        "status": "open", # Initialize status as 'open'
        "entry_order_id": entry_order_id, # Store the entry order ID
        "pnl": 0.0, # Initial P&L
        "pending_exit_order_id": None, # Initialize placeholder for exit order
        "pending_exit_order_placed_at": None,
        "exit_reason_for_order": None
    }
    save_positions(positions)
    logger.log_action(f"Position Manager: Added new 'open' position: {qty} {ticker} @ {entry_price} ({position_type}) on {entry_date.strftime('%Y-%m-%d')}. Entry Order ID: {entry_order_id}")

def remove_position(ticker, exit_price, exit_reason, exit_order_id=None):
    """
    Removes a position from the open positions file and records the trade.
    :param ticker: Stock ticker.
    :param exit_price: Price at which the position was exited.
    :param exit_reason: String explaining why position was closed.
    :param exit_order_id: Optional ID of the order that closed the position.
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
        log_message = f"Position Manager: Closed position for {ticker}. Exit price: {exit_price}, Reason: {exit_reason}, P&L: {profit_loss:.2f}."
        if exit_order_id:
            log_message += f" Exit Order ID: {exit_order_id}."
        logger.log_action(log_message)
    else:
        logger.log_action(f"Position Manager: Attempted to remove position for {ticker}, but it was not found.")

def check_and_manage_open_positions(current_prices, all_historical_data, api_client): # Added api_client
    """
    Manages open positions: checks for max holding period, stop-loss, exit signals.
    This function would typically be called daily.
    :param current_prices: Dict {ticker: current_price}
    :param all_historical_data: Dict {ticker: pd.DataFrame of historical prices} for signal re-evaluation
    :param api_client: Initialized Alpaca API client for placing orders.
    :param alpaca_open_orders_map: Optional dict of {ticker: [AlpacaOrder]} from Alpaca.
    """
    positions = load_positions()
    if not positions:
        logger.log_action("Position Manager: No open positions to manage.")
        return

    if alpaca_open_orders_map is None: # Ensure it's a dict for safe access
        alpaca_open_orders_map = {}

    logger.log_action("Position Manager: Checking and managing open positions...")
    today = datetime.now()
    positions_updated = False # Flag to track if any position was changed

    for ticker, details in list(positions.items()): # Iterate over a copy for safe removal/modification
        # Skip if position is already pending an exit (unless we want to re-verify that order with alpaca_open_orders_map)
        # For now, if positions.json says pending_exit, we trust it and trading_bot.py handles its status.
        # This function's role is to decide IF an 'open' position needs an exit.
        if details.get('status') != 'open': # Only manage 'open' positions
            logger.log_action(f"Position Manager: Position for {ticker} is '{details.get('status', 'unknown_status')}', not 'open'. Skipping management decision here.")
            continue

        current_price = current_prices.get(ticker)
        if current_price is None:
            logger.log_action(f"Position Manager: Could not get current price for open position {ticker}. Skipping management for this ticker.")
            continue

        if not isinstance(current_price, (int, float)) or current_price <= 0:
            logger.log_action(f"Position Manager: Invalid current price ({current_price}) for open position {ticker}. Skipping management.")
            continue

        qty_to_close = details['qty']
        position_type = details['type'] # 'long' or 'short'
        exit_order_placed = False
        exit_reason = None

        # 1. Check Max Holding Period
        entry_date = details['entry_date']
        if isinstance(entry_date, str): # Ensure it's datetime
            entry_date = datetime.fromisoformat(entry_date)

        if (today - entry_date).days >= config.MAX_HOLDING_PERIOD_DAYS:
            logger.log_action(f"Position Manager: Position {ticker} (type: {position_type}) hit max holding period of {config.MAX_HOLDING_PERIOD_DAYS} days.")
            exit_reason = f"max_hold_{config.MAX_HOLDING_PERIOD_DAYS}_days"
            # Don't continue here; proceed to place order below, then skip z-score check if order placed

        # 2. Check Z-Score Based Exit/Stop-Loss (only if not already exiting due to max hold)
        if not exit_reason:
            ticker_hist_data_for_z = all_historical_data.get(ticker)
            if ticker_hist_data_for_z is None or ticker_hist_data_for_z.empty:
                logger.log_action(f"Position Manager: No historical data for {ticker} to re-evaluate z-score. Skipping z-score based exit/stop check.")
            else:
                # Prepare data for current z-score calculation:
                # Append current price to historical data.
                # Ensure DataFrame has a datetime index and 'close' column.
                # The historical data should already be like this.
                # Create a new row for the current price.
                # We need to be careful with the index for the new row.
                # Assuming historical data is daily, and index is Date.
                # For simplicity, if historical data has 'close', use it.

                # Create a copy to avoid modifying the original dict entry
                temp_hist_data = ticker_hist_data_for_z.copy()

                # Ensure 'close' column exists
                if 'close' not in temp_hist_data.columns:
                    logger.log_action(f"Position Manager: 'close' column missing in historical data for {ticker}. Cannot calculate z-score for exit.")
                else:
                    # Append current price. For daily data, use today's date as index.
                    # This might need adjustment if historical data has time component.
                    # Using pd.Timestamp.now() for a generic approach, assuming 'close' is the target column.
                    # If index is not datetime, this might cause issues.
                    # A robust way is to ensure historical_data has a proper timeseries index.
                    try:
                        # Create a new DataFrame for the current price with a matching index type
                        # Assuming the index of temp_hist_data is a DatetimeIndex
                        if isinstance(temp_hist_data.index, pd.DatetimeIndex):
                            current_price_row = pd.DataFrame({'close': [current_price]}, index=[pd.Timestamp.now(tz=temp_hist_data.index.tz)])
                            # Ensure columns match for concatenation
                            for col in temp_hist_data.columns:
                                if col not in current_price_row.columns:
                                     current_price_row[col] = pd.NA # Or np.nan
                            temp_hist_data_with_current = pd.concat([temp_hist_data, current_price_row[['close']]]) # Only use 'close' for z-score relevant part
                        else: # Fallback if index is not datetime (less ideal)
                             # This branch might indicate an issue with how historical_data is structured/indexed
                            logger.log_action(f"Position Manager: Historical data for {ticker} does not have a DatetimeIndex. Appending current price might be inexact.")
                            # Attempt to append anyway, assuming 'close' is the primary data for z-score
                            last_index = temp_hist_data.index[-1]
                            # Create a compatible index for the new row
                            new_index = last_index + pd.Timedelta(days=1) if isinstance(last_index, pd.Timestamp) else (temp_hist_data.index.max() + 1  if pd.api.types.is_numeric_dtype(temp_hist_data.index) else len(temp_hist_data))

                            current_price_row = pd.DataFrame({'close': [current_price]}, index=[new_index])
                            temp_hist_data_with_current = pd.concat([temp_hist_data, current_price_row])


                        current_z_score_series = signal_generator.calculate_zscore(temp_hist_data_with_current['close'])
                        if current_z_score_series is not None and not current_z_score_series.empty and not pd.isna(current_z_score_series.iloc[-1]):
                            current_z_score = current_z_score_series.iloc[-1]
                            logger.log_action(f"Position Manager: Current Z-score for {ticker} (pos type: {position_type}) is {current_z_score:.2f} for exit eval.")

                            signal = signal_generator.generate_signals(ticker, None, current_z_score=current_z_score)

                            if position_type == 'long':
                                if signal == "EXIT_LONG":
                                    exit_reason = "exit_long_signal"
                                elif signal == "STOP_LOSS_LONG":
                                    exit_reason = "stop_loss_long_signal"
                            elif position_type == 'short':
                                if signal == "EXIT_SHORT":
                                    exit_reason = "exit_short_signal"
                                elif signal == "STOP_LOSS_SHORT":
                                    exit_reason = "stop_loss_short_signal"

                            if exit_reason:
                                logger.log_action(f"Position Manager: Signal '{signal}' triggered exit for {ticker} ({position_type}). Reason: {exit_reason}")
                        else:
                            logger.log_action(f"Position Manager: Could not calculate current z-score for {ticker} for exit evaluation.")
                    except Exception as e:
                        logger.log_action(f"Position Manager: Error during z-score calculation or signal generation for {ticker} exit: {e}")


        # 3. Place Exit Order if a reason was determined
        if exit_reason:
            side_to_close = 'sell' if position_type == 'long' else 'buy'

            # Check Alpaca for existing open orders for this ticker that match the exit side
            already_existing_exit_order = None
            if ticker in alpaca_open_orders_map:
                for order in alpaca_open_orders_map[ticker]:
                    if order.side == side_to_close: # This is a potential pre-existing exit order
                        logger.log_action(f"Position Manager: Found existing open Alpaca order for {ticker} (ID: {order.id}, Side: {order.side}, Qty: {order.qty}) that matches required exit side '{side_to_close}'.")
                        already_existing_exit_order = order
                        break # Found a suitable existing order

            if already_existing_exit_order:
                logger.log_action(f"Position Manager: Using existing Alpaca order {already_existing_exit_order.id} as the exit order for {ticker}. No new order will be placed.")
                # Ensure this existing order is tracked in positions.json
                if positions[ticker].get('pending_exit_order_id') != already_existing_exit_order.id:
                    positions[ticker]['status'] = 'pending_exit'
                    positions[ticker]['pending_exit_order_id'] = already_existing_exit_order.id
                    # Use submitted_at from order if available, else fallback
                    submitted_at_iso = datetime.now().isoformat()
                    if hasattr(already_existing_exit_order, 'submitted_at') and already_existing_exit_order.submitted_at:
                        try:
                            # AlpacaPy returns timezone-aware datetime, convert to ISO string
                            submitted_at_iso = pd.to_datetime(already_existing_exit_order.submitted_at).isoformat()
                        except Exception as e_ts:
                            logger.log_action(f"Position Manager: Could not format submitted_at for order {already_existing_exit_order.id}: {e_ts}")

                    positions[ticker]['pending_exit_order_placed_at'] = submitted_at_iso
                    positions[ticker]['exit_reason_for_order'] = exit_reason # Update reason if new one triggered this
                    positions_updated = True
                exit_order_placed = True # Effectively, yes, as we are using an existing one.
            else:
                # No suitable existing order found, proceed to place a new one
                logger.log_action(f"Position Manager: Attempting to place {side_to_close} order for {qty_to_close} {ticker} @ limit {current_price} due to: {exit_reason}")
                exit_order_obj = order_manager.place_limit_order(
                    ticker, qty_to_close, current_price, side_to_close, api_client=api_client
                )

                if exit_order_obj and hasattr(exit_order_obj, 'id'):
                    logger.log_action(f"Position Manager: Exit order {exit_order_obj.id} placed for {ticker}. Status: {exit_order_obj.status}")
                    positions[ticker]['status'] = 'pending_exit'
                    positions[ticker]['pending_exit_order_id'] = exit_order_obj.id
                    positions[ticker]['pending_exit_order_placed_at'] = datetime.now().isoformat() # Or use order.created_at
                    positions[ticker]['exit_reason_for_order'] = exit_reason
                    exit_order_placed = True
                    positions_updated = True
                else:
                    logger.log_action(f"Position Manager: Failed to place exit order for {ticker} for reason: {exit_reason}. Will retry next run.")

        # If an exit order was placed or adopted, this iteration for the ticker is done.
        if exit_order_placed:
            continue # Move to the next ticker in the loop

    # Save all updated positions to file if any changes were made
    if positions_updated:
        logger.log_action("Position Manager: Saving updated positions after management cycle.")
        save_positions(positions)
    else:
        logger.log_action("Position Manager: No positions were updated in this management cycle.")


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
