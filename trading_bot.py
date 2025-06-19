# trading_bot.py
# Main script to run the time-series mean reversion trading bot.

import os
import time # For potential delays if needed
from datetime import datetime
import json # For managing pending_entry_orders_today.json
import pandas as pd # For type hinting and z-score input preparation

import alpaca_trade_api as tradeapi # For Alpaca API client initialization
from dotenv import load_dotenv # To load environment variables from .env file

# Import custom modules
import config # Bot configuration and parameters
import data_fetcher # For fetching market data
import logger # For logging actions and trades
import order_manager # For placing and managing orders
import position_manager # For managing open positions and state
import signal_generator # For generating trading signals

# File to store IDs of entry orders placed in the current run.
# These are assumed to be 'day' orders and this file helps track their fill status within the same session.
PENDING_ENTRY_ORDERS_FILE = "pending_entry_orders_today.json" # Consistent with config.py or define in one place

def initialize_api_client():
    """
    Initializes and returns an Alpaca API client.
    Loads API keys from .env file.
    Returns None if initialization fails.
    """
    load_dotenv() # Ensures .env variables are loaded into environment
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")

    # Determine base URL based on paper/live trading mode from config
    base_url = "https://paper-api.alpaca.markets" if config.ALPACA_PAPER else "https://api.alpaca.markets"

    if not api_key or not secret_key:
        logger.log_action("CRITICAL: Alpaca API Key or Secret Key not found in .env. Bot cannot run.")
        return None
    try:
        client = tradeapi.REST(api_key, secret_key, base_url=base_url)
        account = client.get_account() # Test connection by fetching account info
        logger.log_action(
            f"Successfully connected to Alpaca. Account ID: {account.id}, "
            f"Status: {account.status}, Portfolio Value: {account.portfolio_value}, Cash: {account.cash}"
        )
        return client
    except Exception as e:
        logger.log_action(f"ERROR: Could not connect to Alpaca API: {e}")
        return None

def load_pending_entry_orders():
    """
    Loads pending entry orders from the JSON file.
    Returns an empty dictionary if the file doesn't exist or an error occurs.
    """
    if not os.path.exists(PENDING_ENTRY_ORDERS_FILE):
        return {}
    try:
        with open(PENDING_ENTRY_ORDERS_FILE, 'r') as f:
            content = f.read()
            if not content: # Handle empty file
                return {}
            return json.loads(content)
    except json.JSONDecodeError as e:
        logger.log_action(f"Error decoding JSON from {PENDING_ENTRY_ORDERS_FILE}: {e}. Returning empty dict.")
        return {}
    except Exception as e:
        logger.log_action(f"Error loading pending entry orders from {PENDING_ENTRY_ORDERS_FILE}: {e}")
        return {}

def save_pending_entry_orders(orders):
    """
    Saves the given dictionary of pending entry orders to the JSON file.
    """
    try:
        with open(PENDING_ENTRY_ORDERS_FILE, 'w') as f:
            json.dump(orders, f, indent=4)
    except Exception as e:
        logger.log_action(f"Error saving pending entry orders to {PENDING_ENTRY_ORDERS_FILE}: {e}")


def main():
    """
    Main execution function for the trading bot.
    This function orchestrates the daily trading cycle.
    """
    run_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    logger.log_action(f"===== Trading Bot session started at {run_timestamp} =====")

    # Initialize Alpaca API client
    api = initialize_api_client()
    if not api:
        logger.log_action("Exiting due to API client initialization failure.")
        logger.log_action(f"===== Trading Bot session ended prematurely at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        return

    # --- Step 1: Manage Existing Positions and Orders ---
    # This includes checking statuses of pending exit orders from previous runs.
    logger.log_action("Step 1: Managing existing positions and checking pending exit orders...")
    current_positions = position_manager.load_positions()

    for ticker, details in list(current_positions.items()): # Use list() for safe iteration if modifying dict
        if details.get('status') == 'pending_exit' and details.get('pending_exit_order_id'):
            order_id = details['pending_exit_order_id']
            logger.log_action(f"Checking status of pending exit order {order_id} for {ticker}.")

            order_status_obj = order_manager.get_order_status(order_id)

            if order_status_obj:
                if order_status_obj.status == 'filled':
                    try:
                        fill_price = float(order_status_obj.filled_avg_price)
                        fill_qty = float(order_status_obj.filled_qty) # Alpaca returns string
                        logger.log_action(f"Exit order {order_id} for {ticker} FILLED. Qty: {fill_qty}, Avg Price: ${fill_price}.")
                        exit_reason = details.get('exit_reason_for_order', 'automated_exit_filled')
                        position_manager.remove_position(ticker, fill_price, exit_reason, order_id)
                    except ValueError as ve:
                        logger.log_action(f"Error converting fill data for order {order_id} ({ticker}): {ve}. Data: {order_status_obj}")
                    except Exception as ex:
                        logger.log_action(f"Unexpected error processing filled exit order {order_id} ({ticker}): {ex}")
                elif order_status_obj.status in ['canceled', 'expired', 'rejected']:
                    logger.log_action(f"Exit order {order_id} for {ticker} is {order_status_obj.status}. Reverting position to 'open'.")
                    current_positions[ticker]['status'] = 'open'
                    current_positions[ticker]['pending_exit_order_id'] = None
                    current_positions[ticker]['pending_exit_order_placed_at'] = None
                else: # e.g., 'new', 'accepted', 'partially_filled', 'pending_cancel'
                    logger.log_action(f"Exit order {order_id} for {ticker} is still '{order_status_obj.status}'. Will check again next run.")
            else:
                logger.log_action(f"Could not get status for pending exit order {order_id} (ticker {ticker}). Order may not exist or API error. Will retry next run.")

    position_manager.save_positions(current_positions) # Save any changes (like reverted statuses)
    current_positions = position_manager.load_positions() # Reload for a consistent state

    # --- Step 2: Fetch Market Data ---
    logger.log_action("Step 2: Fetching market data...")
    all_tickers = config.TICKERS

    # Fetch historical data for z-score calculation (window + buffer)
    # data_fetcher initializes its own Alpaca API client based on .env settings.
    historical_data_multi_df = data_fetcher.get_historical_data(
        all_tickers,
        timeframe='1Day', # Assuming daily z-score calculation
        limit_per_ticker=config.Z_SCORE_WINDOW + 20 # +20 as buffer for initial NaNs
    )
    latest_prices = data_fetcher.get_latest_prices(all_tickers)

    # Basic check if critical data was fetched
    if historical_data_multi_df.empty and not latest_prices:
        logger.log_action("CRITICAL: Failed to fetch both historical and latest price data. Bot cannot proceed. Exiting this run.")
        logger.log_action(f"===== Trading Bot session ended due to data error at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        return
    if historical_data_multi_df.empty:
        logger.log_action("WARNING: Failed to fetch historical market data. Signal generation may be impacted.")
    if not latest_prices:
        logger.log_action("WARNING: Failed to fetch latest prices. Order placement and position management may be impacted.")

    # Prepare historical_data_map for position_manager and signal_generator: {ticker: DataFrame_for_one_ticker}
    historical_data_map_for_pm = {}
    if not historical_data_multi_df.empty:
        for ticker_symbol in all_tickers:
            try:
                if ticker_symbol in historical_data_multi_df.index.get_level_values('symbol'):
                    historical_data_map_for_pm[ticker_symbol] = historical_data_multi_df.xs(ticker_symbol, level='symbol')
                else:
                    # This log is helpful if a ticker from config.TICKERS doesn't get data
                    logger.log_action(f"No historical data returned for {ticker_symbol} in the fetched multi-ticker DataFrame.")
            except KeyError: # Should be caught by the 'in' check, but as a safeguard
                 logger.log_action(f"KeyError accessing historical data for {ticker_symbol}. It might not be in the fetched data.")


    # --- Step 3: Manage Open Positions (Check for Exits) ---
    # This function iterates through 'open' positions, checks exit conditions (max hold, z-score based exits/stops),
    # places closing orders if necessary, and updates their status to 'pending_exit'.
    logger.log_action("Step 3: Managing currently open positions for potential exits...")
    position_manager.check_and_manage_open_positions(latest_prices, historical_data_map_for_pm)

    current_positions = position_manager.load_positions() # Reload as statuses might have changed to 'pending_exit'

    # --- Step 4: Evaluate New Entry Signals ---
    logger.log_action("Step 4: Evaluating new entry signals...")
    available_cash = position_manager.get_available_cash(api) # Pass the initialized API client
    logger.log_action(f"Available cash for new entries: ${available_cash:.2f}")

    # Determine Pattern Day Trader (PDT) count
    pdt_count = 0
    try:
        account_info = api.get_account()
        pdt_count = int(account_info.daytrade_count)
        logger.log_action(f"PDT count from Alpaca API: {pdt_count}")
    except Exception as e:
        logger.log_action(f"Could not get PDT count from Alpaca API: {e}. Falling back to CSV method for estimation.")
        pdt_count = position_manager.get_pdt_trade_count() # Uses trades.csv as fallback

    entry_orders_placed_this_run = {} # To store details of entry orders placed in this session

    for ticker_symbol in all_tickers:
        # Ensure we have a current price for the ticker
        if ticker_symbol not in latest_prices:
            logger.log_action(f"No current price available for {ticker_symbol}; skipping entry evaluation.")
            continue
        current_price = latest_prices[ticker_symbol]
        if not isinstance(current_price, (int, float)) or current_price <= 0:
            logger.log_action(f"Invalid current price ({current_price}) for {ticker_symbol}; skipping entry evaluation.")
            continue


        # Skip if already holding an open or pending_exit position for this ticker
        if ticker_symbol in current_positions and current_positions[ticker_symbol].get('status') in ['open', 'pending_exit']:
            logger.log_action(f"Already have an active or pending_exit position for {ticker_symbol}. Skipping new entry evaluation.")
            continue

        # Ensure historical data is available for z-score calculation
        ticker_hist_data_df = historical_data_map_for_pm.get(ticker_symbol)
        if ticker_hist_data_df is None or ticker_hist_data_df.empty:
            logger.log_action(f"No historical data for {ticker_symbol} for z-score calculation. Skipping entry evaluation.")
            continue
        if 'close' not in ticker_hist_data_df.columns:
            logger.log_action(f"Historical data for {ticker_symbol} is missing 'close' column. Skipping entry.")
            continue

        # Generate trading signal based on z-score
        z_scores = signal_generator.calculate_zscore(ticker_hist_data_df['close'])
        if z_scores is None or z_scores.empty or pd.isna(z_scores.iloc[-1]):
            # calculate_zscore logs reasons for failure (e.g. insufficient data)
            logger.log_action(f"Z-score for {ticker_symbol} is NaN or could not be calculated. Skipping entry.")
            continue

        current_z_score = z_scores.iloc[-1]
        # Pass current_z_score directly to avoid recalculation if historical_data_df is just for z-score
        signal = signal_generator.generate_signals(ticker_symbol, None, current_z_score=current_z_score)
        logger.log_action(f"Eval New Entry: Ticker={ticker_symbol}, Price={current_price:.2f}, Z-Score={current_z_score:.2f}, Signal={signal}")

        if signal in ["BUY", "SELL"]: # "BUY" for long entry, "SELL" for short entry
            # Check PDT rule: Do not exceed 3 day trades in 5 business days.
            # If pdt_count is 3 or more, no new day trades can be initiated.
            if pdt_count >= 3:
                logger.log_action(f"PDT limit ({pdt_count} day trades) reached or exceeded. Cannot place new opening trade for {ticker_symbol}.")
                # Consider breaking loop if no more trades allowed for the day at all. For now, just skips this ticker.
                continue

            # Calculate quantity and check if affordable
            try:
                qty = int(config.POSITION_SIZE_USD / current_price)
            except ZeroDivisionError:
                logger.log_action(f"Error: Current price for {ticker_symbol} is zero. Cannot calculate quantity.")
                continue

            if qty <= 0:
                logger.log_action(f"Calculated quantity is {qty} (non-positive) for {ticker_symbol} at price {current_price}. Skipping.")
                continue

            estimated_cost = qty * current_price
            if estimated_cost > available_cash:
                logger.log_action(f"Insufficient cash for {ticker_symbol}. Need ${estimated_cost:.2f}, have ${available_cash:.2f}. Skipping.")
                continue

            # Place the order
            order_side = 'buy' if signal == "BUY" else 'sell'
            logger.log_action(f"Attempting to place {order_side} order: {qty} shares of {ticker_symbol} @ limit ${current_price:.2f}")
            entry_order = order_manager.place_limit_order(ticker_symbol, qty, current_price, order_side)

            if entry_order and hasattr(entry_order, 'id'):
                logger.log_action(f"Entry order {entry_order.id} ({order_side} {qty} {ticker_symbol}) placed. Status: {entry_order.status}")
                entry_orders_placed_this_run[entry_order.id] = {
                    "ticker": ticker_symbol, "qty": qty, "side": order_side,
                    "limit_price": current_price, "type": "long" if signal == "BUY" else "short",
                    "entry_time": datetime.now().isoformat(), # Record placement time
                    "z_at_entry": current_z_score # Store z-score for future analysis/reference
                }
                available_cash -= estimated_cost # Notionally update available cash for subsequent checks in this loop
            else:
                logger.log_action(f"Failed to place entry order for {ticker_symbol}.")

    save_pending_entry_orders(entry_orders_placed_this_run) # Save orders placed in this run

    # --- Step 5: Check Status of Entry Orders Placed in THIS Run ---
    # For 'day' TIF orders, this checks if they filled shortly after placement.
    logger.log_action("Step 5: Checking status of new entry orders placed in this run...")
    pending_entry_orders_today = load_pending_entry_orders() # Load what was just saved
    filled_any_new_entries = False

    # Optional: Short delay before checking, to allow orders to propagate/fill.
    # if pending_entry_orders_today: time.sleep(5) # e.g., 5 seconds

    for order_id, order_details in list(pending_entry_orders_today.items()): # Use list() for safe modification
        logger.log_action(f"Checking status of new entry order {order_id} for {order_details['ticker']}.")
        order_status_obj = order_manager.get_order_status(order_id)
        if order_status_obj:
            if order_status_obj.status == 'filled':
                try:
                    fill_price = float(order_status_obj.filled_avg_price)
                    # Use filled_qty from order status; if None (should not happen for filled), fallback to ordered qty.
                    fill_qty = float(order_status_obj.filled_qty if order_status_obj.filled_qty is not None else order_details['qty'])

                    logger.log_action(f"New entry order {order_id} for {order_details['ticker']} FILLED. Qty: {fill_qty}, Avg Price: ${fill_price}.")

                    # Determine entry_date from Alpaca's filled_at if available
                    entry_fill_time = datetime.now()
                    if hasattr(order_status_obj, 'filled_at') and order_status_obj.filled_at:
                        entry_fill_time = pd.to_datetime(order_status_obj.filled_at).to_pydatetime() # Convert Panda Timestamp to python datetime

                    position_manager.add_position(
                        ticker=order_details['ticker'],
                        qty=fill_qty,
                        entry_price=fill_price,
                        position_type=order_details['type'], # 'long' or 'short'
                        entry_order_id=order_id,
                        entry_date=entry_fill_time
                    )
                    filled_any_new_entries = True
                    del pending_entry_orders_today[order_id] # Remove from pending list
                except ValueError as ve:
                     logger.log_action(f"Error converting fill data for new entry order {order_id} ({order_details['ticker']}): {ve}. Data: {order_status_obj}")
                except Exception as ex:
                    logger.log_action(f"Unexpected error processing filled new entry order {order_id} ({order_details['ticker']}): {ex}")


            elif order_status_obj.status in ['expired', 'canceled', 'rejected', 'done_for_day']:
                logger.log_action(f"New entry order {order_id} for {order_details['ticker']} is {order_status_obj.status}. Removing from today's pending list.")
                del pending_entry_orders_today[order_id]
            else: # e.g. 'new', 'accepted', 'partially_filled'
                logger.log_action(f"New entry order {order_id} ({order_details['ticker']}) is still '{order_status_obj.status}'. (Day order - will likely expire if not filled by EOD).")
        else:
            logger.log_action(f"Could not get status for new entry order {order_id} ({order_details['ticker']}). Will assume it's still pending or failed for this run.")

    save_pending_entry_orders(pending_entry_orders_today) # Save changes to the pending list

    # If any new positions were opened, re-run management in case of immediate exit conditions (e.g., rapid reversal)
    if filled_any_new_entries:
        logger.log_action("New positions were opened. Re-running position management for potential immediate exits...")
        # latest_prices and historical_data_map_for_pm are from earlier in this run.
        # For maximum accuracy, could re-fetch, but for an immediate check, existing data might be sufficient.
        position_manager.check_and_manage_open_positions(latest_prices, historical_data_map_for_pm)

    logger.log_action(f"===== Trading Bot session finished at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====\n")

if __name__ == "__main__":
    # This script is designed to be run once per day as per the "Trade Schedule".
    # Actual scheduling (e.g., via cron or Windows Task Scheduler) is external.

    # Clear the pending entry orders file from any previous (e.g., test) runs.
    # In a robust production environment, one might inspect this file for orders
    # from a crashed previous session instead of blindly deleting.
    if os.path.exists(PENDING_ENTRY_ORDERS_FILE):
        logger.log_action(f"Clearing previous pending entry orders file: {PENDING_ENTRY_ORDERS_FILE}")
        try:
            os.remove(PENDING_ENTRY_ORDERS_FILE)
        except OSError as e:
            logger.log_action(f"Error removing {PENDING_ENTRY_ORDERS_FILE}: {e}")

    main()
