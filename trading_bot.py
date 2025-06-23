# Main script for the mean reversion trading bot.

import os
from datetime import datetime
import json
import pandas as pd

import alpaca_trade_api as tradeapi
from dotenv import load_dotenv

import config
import data_fetcher
import logger
import order_manager
import position_manager
import signal_generator

# PENDING_ORDERS_FILE path is from config.py

def initialize_api_client():
    """Initializes and returns Alpaca API client. Keys from .env."""
    load_dotenv()
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    base_url = "https://paper-api.alpaca.markets" if config.ALPACA_PAPER else "https://api.alpaca.markets"

    if not api_key or not secret_key:
        logger.log_action("CRITICAL: Alpaca API Key/Secret not in .env. Bot cannot run.")
        return None
    try:
        client = tradeapi.REST(api_key, secret_key, base_url=base_url)
        account = client.get_account()
        logger.log_action(
            f"Successfully connected to Alpaca. Account: {account.id}, "
            f"Status: {account.status}, Portfolio: {account.portfolio_value}, Cash: {account.cash}"
        )
        return client
    except Exception as e:
        logger.log_action(f"ERROR: Could not connect to Alpaca API: {e}")
        return None

def load_pending_orders():
    """Loads all pending orders from JSON file specified in config."""
    os.makedirs(os.path.dirname(config.PENDING_ORDERS_FILE), exist_ok=True)
    if not os.path.exists(config.PENDING_ORDERS_FILE):
        return {}
    try:
        with open(config.PENDING_ORDERS_FILE, 'r') as f:
            content = f.read()
            if not content: return {} # Handle empty file
            return json.loads(content)
    except json.JSONDecodeError as e:
        logger.log_action(f"Error decoding JSON from {config.PENDING_ORDERS_FILE}: {e}. Returning empty.")
        return {}
    except Exception as e:
        logger.log_action(f"Error loading pending orders from {config.PENDING_ORDERS_FILE}: {e}")
        return {}

def save_pending_orders(orders):
    """Saves pending orders dictionary to JSON file specified in config."""
    os.makedirs(os.path.dirname(config.PENDING_ORDERS_FILE), exist_ok=True)
    try:
        with open(config.PENDING_ORDERS_FILE, 'w') as f:
            json.dump(orders, f, indent=4)
    except Exception as e:
        logger.log_action(f"Error saving pending orders to {config.PENDING_ORDERS_FILE}: {e}")

def save_run_pending_orders_snapshot(orders):
    """Saves a snapshot of current pending orders to a run-specific JSON file."""
    os.makedirs(os.path.dirname(config.RUN_PENDING_ORDERS_FILE), exist_ok=True)
    try:
        with open(config.RUN_PENDING_ORDERS_FILE, 'w') as f:
            json.dump(orders, f, indent=4)
        logger.log_action(f"Saved pending orders snapshot to {config.RUN_PENDING_ORDERS_FILE}")
    except Exception as e:
        logger.log_action(f"Error saving run-specific pending orders snapshot: {e}")


def main():
    """Main execution function for the trading bot's daily cycle."""
    os.makedirs(config.CURRENT_RUN_DIR, exist_ok=True) # Ensure run directory exists
    run_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    logger.log_action(f"===== Trading Bot session started at {run_timestamp} =====")

    api = initialize_api_client()
    if not api:
        logger.log_action("Exiting: API client initialization failure.")
        logger.log_action(f"===== Bot session ended prematurely at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====")
        return

    # Initial synchronization of pending_orders.json with Alpaca
    # TODO: Review lifecycle of PENDING_ORDERS_FILE (see README TODOs)
    logger.log_action("Synchronizing pending_orders.json with live Alpaca open orders...")
    local_pending_orders = load_pending_orders()
    alpaca_live_open_orders = order_manager.get_open_orders(api_client=api)
    synchronized_pending_orders = {}

    for alpaca_order in alpaca_live_open_orders:
        order_id = alpaca_order.id
        if order_id in local_pending_orders:
            order_details = local_pending_orders[order_id]
            order_details['status'] = alpaca_order.status
            synchronized_pending_orders[order_id] = order_details
        else: # New order on Alpaca not tracked locally
            placed_at_iso = alpaca_order.submitted_at.isoformat() if alpaca_order.submitted_at else datetime.now().isoformat()
            inferred_type = f"alpaca_external_{alpaca_order.side}"
            synchronized_pending_orders[order_id] = {
                "ticker": alpaca_order.symbol, "qty": float(alpaca_order.qty),
                "side": alpaca_order.side, "limit_price": float(alpaca_order.limit_price) if alpaca_order.limit_price else None,
                "type": inferred_type, "placed_at": placed_at_iso,
                "z_at_placement": None, "status": alpaca_order.status
            }
        logger.log_action(f"Synchronized: Order {order_id} ({alpaca_order.symbol}) status: {alpaca_order.status}")
    save_pending_orders(synchronized_pending_orders)
    logger.log_action(f"Synchronization complete. {len(synchronized_pending_orders)} pending orders in {config.PENDING_ORDERS_FILE}.")

    # Step 1: Manage Existing Positions and Orders
    logger.log_action("Step 1: Managing existing positions & checking pending orders...")
    
    # Fetch open orders from Alpaca
    alpaca_open_orders_list = order_manager.get_open_orders(api_client=api, tickers=config.TICKERS)
    alpaca_open_orders_map = {} # Ticker -> [AlpacaOrder]
    for order in alpaca_open_orders_list:
        alpaca_open_orders_map.setdefault(order.symbol, []).append(order)
        logger.log_action(f"Found open Alpaca order: ID {order.id}, {order.symbol}, {order.side}, Qty {order.qty}, Price {order.limit_price or 'N/A'}, Status {order.status}")
    if not alpaca_open_orders_list: logger.log_action("No open orders on Alpaca for configured tickers.")

    # Fetch open positions from Alpaca
    alpaca_open_positions_map = data_fetcher.get_alpaca_open_positions(api_client=api)
    for ticker, pos in alpaca_open_positions_map.items():
        logger.log_action(f"Found open Alpaca position: {pos.symbol}, Qty {pos.qty}, Avg Entry {pos.avg_entry_price}")

    # Reconcile positions.json with Alpaca's open exit orders
    current_positions = position_manager.load_positions()
    for ticker, details in list(current_positions.items()):
        if details.get('status') == 'pending_exit':
            known_exit_order_id = details.get('pending_exit_order_id')
            if known_exit_order_id:
                logger.log_action(f"Checking known pending exit order {known_exit_order_id} for {ticker}.")
                order_status_obj = order_manager.get_order_status(known_exit_order_id, api_client=api)
                if order_status_obj:
                    if order_status_obj.status == 'filled':
                        try:
                            fill_price = float(order_status_obj.filled_avg_price)
                            fill_qty = float(order_status_obj.filled_qty)
                            logger.log_action(f"Known exit order {known_exit_order_id} for {ticker} FILLED. Qty: {fill_qty}, Price: ${fill_price}.")
                            exit_reason = details.get('exit_reason_for_order', 'automated_exit_filled')
                            position_manager.remove_position(ticker, fill_price, exit_reason, known_exit_order_id)
                            if ticker in alpaca_open_orders_map: # Remove from map if it was there
                                alpaca_open_orders_map[ticker] = [o for o in alpaca_open_orders_map[ticker] if o.id != known_exit_order_id]
                                if not alpaca_open_orders_map[ticker]: del alpaca_open_orders_map[ticker]
                        except (ValueError, TypeError) as conv_err: # Catch specific conversion errors
                            logger.log_action(f"Error converting fill data for known order {known_exit_order_id} ({ticker}): {conv_err}.")
                        except Exception as ex: # Catch any other unexpected error
                            logger.log_action(f"Unexpected error processing filled known order {known_exit_order_id} ({ticker}): {ex}")
                    elif order_status_obj.status in ['canceled', 'expired', 'rejected', 'done_for_day']:
                        logger.log_action(f"Known exit order {known_exit_order_id} for {ticker} is {order_status_obj.status}. Reverting position to 'open'.")
                        current_positions[ticker].update({'status': 'open', 'pending_exit_order_id': None, 'pending_exit_order_placed_at': None, 'exit_reason_for_order': None})
                    else:
                        logger.log_action(f"Known exit order {known_exit_order_id} for {ticker} is still '{order_status_obj.status}'.")
                else: # Could not get status
                    logger.log_action(f"Could not get status for known pending exit {known_exit_order_id} ({ticker}). Assuming inactive, reverting to 'open'.")
                    current_positions[ticker].update({'status': 'open', 'pending_exit_order_id': None, 'pending_exit_order_placed_at': None, 'exit_reason_for_order': None})
            else: # 'pending_exit' but no order_id in positions.json (unusual)
                logger.log_action(f"Position {ticker} 'pending_exit' but no order_id in positions.json. Checking Alpaca for open exit.")
                if ticker in alpaca_open_orders_map:
                    expected_exit_side = 'sell' if details.get('type', 'long') == 'long' else 'buy'
                    for open_order in alpaca_open_orders_map[ticker]:
                        if open_order.side == expected_exit_side:
                            logger.log_action(f"Found matching open exit {open_order.id} on Alpaca for {ticker}. Updating positions.json.")
                            current_positions[ticker]['pending_exit_order_id'] = open_order.id
                            break
    position_manager.save_positions(current_positions)
    current_positions = position_manager.load_positions() # Reload for consistent state

    # Step 2: Fetch Market Data
    logger.log_action("Step 2: Fetching market data...")
    historical_data_multi_df = data_fetcher.get_historical_data(
        config.TICKERS, timeframe='1Day',
        limit_per_ticker=config.Z_SCORE_WINDOW + 20, # Buffer for NaNs
        api_client=api
    )
    latest_prices = data_fetcher.get_latest_prices(config.TICKERS, api_client=api)

    if historical_data_multi_df.empty and not latest_prices:
        logger.log_action("CRITICAL: Failed to fetch market data. Bot exiting.")
        logger.log_action(f"===== Bot session ended due to data error at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====")
        return
    if historical_data_multi_df.empty: logger.log_action("WARNING: Failed to fetch historical data.")
    if not latest_prices: logger.log_action("WARNING: Failed to fetch latest prices.")

    historical_data_map_for_pm = {} # Ticker -> DataFrame
    if not historical_data_multi_df.empty:
        for ticker_sym in config.TICKERS:
            try:
                if ticker_sym in historical_data_multi_df.index.get_level_values('symbol'):
                    historical_data_map_for_pm[ticker_sym] = historical_data_multi_df.xs(ticker_sym, level='symbol')
                else:
                    logger.log_action(f"No historical data for {ticker_sym} in multi-ticker DF.")
            except KeyError:
                 logger.log_action(f"KeyError accessing hist data for {ticker_sym}.")

    # Step 3: Manage Open Positions (Check for Exits)
    logger.log_action("Step 3: Managing open positions for potential exits...")
    position_manager.check_and_manage_open_positions(
        latest_prices, historical_data_map_for_pm, api, alpaca_open_orders_map, alpaca_open_positions_map
    )
    current_positions = position_manager.load_positions() # Reload after potential status changes

    # Step 4: Evaluate New Entry Signals
    logger.log_action("Step 4: Evaluating new entry signals...")
    available_cash = position_manager.get_available_cash(api)
    logger.log_action(f"Available cash for new entries: ${available_cash:.2f}")
    pdt_count = 0
    try:
        pdt_count = int(api.get_account().daytrade_count)
        logger.log_action(f"PDT count from Alpaca API: {pdt_count}")
    except Exception as e:
        logger.log_action(f"Could not get PDT count from Alpaca API: {e}. Using CSV method.")
        pdt_count = position_manager.get_pdt_trade_count()

    pending_orders = load_pending_orders() # Load current state of bot-tracked pending orders

    for ticker_symbol in config.TICKERS:
        current_price = latest_prices.get(ticker_symbol)
        if not isinstance(current_price, (int, float)) or current_price <= 0:
            logger.log_action(f"Invalid/missing price for {ticker_symbol} ({current_price}); skipping entry.")
            continue
        if ticker_symbol in current_positions and current_positions[ticker_symbol].get('status') in ['open', 'pending_exit']:
            logger.log_action(f"Active or pending_exit position for {ticker_symbol}. Skipping new entry.")
            continue
        if ticker_symbol in alpaca_open_orders_map: # Avoid duplicate entry if Alpaca shows open order
            logger.log_action(f"Existing open Alpaca order(s) for {ticker_symbol}. Skipping new entry.")
            continue

        ticker_hist_data_df = historical_data_map_for_pm.get(ticker_symbol)
        if ticker_hist_data_df is None or ticker_hist_data_df.empty or 'close' not in ticker_hist_data_df.columns:
            logger.log_action(f"Insufficient/invalid historical data for {ticker_symbol}. Skipping entry.")
            continue

        z_scores = signal_generator.calculate_zscore(ticker_hist_data_df['close'])
        if z_scores is None or z_scores.empty or pd.isna(z_scores.iloc[-1]):
            logger.log_action(f"Z-score for {ticker_symbol} NaN or uncalculable. Skipping entry.")
            continue
        current_z_score = z_scores.iloc[-1]
        signal = signal_generator.generate_signals(ticker_symbol, None, current_z_score=current_z_score)
        logger.log_action(f"Eval New Entry: {ticker_symbol}, Price={current_price:.2f}, Z={current_z_score:.2f}, Signal={signal}")

        if signal in ["BUY", "SELL"]:
            if pdt_count >= 3:
                logger.log_action(f"PDT limit ({pdt_count}) reached. No new opening trade for {ticker_symbol}.")
                continue
            try:
                qty = int(config.POSITION_SIZE_USD / current_price)
            except ZeroDivisionError:
                logger.log_action(f"Price for {ticker_symbol} is zero. Cannot calc qty.")
                continue
            if qty <= 0:
                logger.log_action(f"Calculated qty <= 0 for {ticker_symbol}. Skipping.")
                continue
            if (qty * current_price) > available_cash:
                logger.log_action(f"Insufficient cash for {ticker_symbol}. Need ${qty*current_price:.2f}, have ${available_cash:.2f}. Skipping.")
                continue

            order_side = 'buy' if signal == "BUY" else 'sell'
            logger.log_action(f"Attempting {order_side} order: {qty} {ticker_symbol} @ limit ${current_price:.2f}")
            entry_order = order_manager.place_limit_order(ticker_symbol, qty, current_price, order_side, api_client=api)

            if entry_order and hasattr(entry_order, 'id'):
                logger.log_action(f"Entry order {entry_order.id} ({order_side} {qty} {ticker_symbol}) placed. Status: {entry_order.status}")
                pending_orders[entry_order.id] = {
                    "ticker": ticker_symbol, "qty": qty, "side": order_side, "limit_price": current_price,
                    "type": "entry_long" if signal == "BUY" else "entry_short",
                    "placed_at": datetime.now().isoformat(), "z_at_placement": current_z_score,
                    "status": entry_order.status
                }
                available_cash -= (qty * current_price)
            else:
                logger.log_action(f"Failed to place entry order for {ticker_symbol}.")

    # Step 5: Reconcile all bot-tracked pending_orders with Alpaca
    logger.log_action("Step 5: Reconciling all pending_orders.json with Alpaca...")
    filled_any_new_entries = False
    alpaca_current_open_orders_list = order_manager.get_open_orders(api_client=api) # Fresh fetch
    alpaca_current_open_orders_map_by_id = {order.id: order for order in alpaca_current_open_orders_list}
    orders_to_remove_from_pending_file = []

    for order_id, order_details in list(pending_orders.items()):
        ticker = order_details['ticker']
        order_type = order_details['type']

        if order_id in alpaca_current_open_orders_map_by_id: # Still open on Alpaca
            order_status_obj = alpaca_current_open_orders_map_by_id[order_id]
            logger.log_action(f"Pending order {order_id} ({ticker}, {order_type}) still '{order_status_obj.status}' on Alpaca.")
            pending_orders[order_id]['status'] = order_status_obj.status # Update local status
        else: # Not in Alpaca's open list; must be filled, cancelled, expired, etc.
            logger.log_action(f"Pending order {order_id} ({ticker}, {order_type}) not in Alpaca open orders. Checking final status...")
            order_status_obj = order_manager.get_order_status(order_id, api_client=api)

            if order_status_obj:
                if order_status_obj.status == 'filled':
                    try:
                        fill_price = float(order_status_obj.filled_avg_price)
                        fill_qty = float(order_status_obj.filled_qty if order_status_obj.filled_qty is not None else order_details['qty'])
                        logger.log_action(f"Pending order {order_id} ({ticker}, {order_type}) FILLED. Qty: {fill_qty}, Price: ${fill_price}.")

                        if order_type.startswith('entry') or order_type.startswith('alpaca_external_'):
                            entry_fill_time = pd.to_datetime(order_status_obj.filled_at).to_pydatetime() if hasattr(order_status_obj, 'filled_at') and order_status_obj.filled_at else datetime.now()
                            pos_type = 'long' if order_type == 'entry_long' or order_type == 'alpaca_external_buy' else ('short' if order_type == 'entry_short' or order_type == 'alpaca_external_sell' else None)
                            if pos_type:
                                position_manager.add_position(ticker, fill_qty, fill_price, pos_type, order_id, entry_fill_time)
                                filled_any_new_entries = True
                            else:
                                logger.log_action(f"WARNING: Unknown position type for filled order {order_id} (type: {order_type}).")
                        elif order_type.startswith('exit'): # Should be handled by position_manager now, but as a safeguard
                            exit_reason = order_details.get('exit_reason_for_order', 'reconciled_exit_filled')
                            position_manager.remove_position(ticker, fill_price, exit_reason, order_id)
                        orders_to_remove_from_pending_file.append(order_id)
                    except (ValueError, TypeError) as conv_err:
                        logger.log_action(f"Error converting fill data for pending {order_id} ({ticker}, {order_type}): {conv_err}. Data: {order_status_obj}")
                    except Exception as ex:
                        logger.log_action(f"Unexpected error processing filled pending {order_id} ({ticker}, {order_type}): {ex}")
                elif order_status_obj.status in ['expired', 'canceled', 'rejected', 'done_for_day']:
                    logger.log_action(f"Pending order {order_id} ({ticker}, {order_type}) is {order_status_obj.status}. Removing.")
                    orders_to_remove_from_pending_file.append(order_id)
                else:
                    logger.log_action(f"Pending order {order_id} ({ticker}, {order_type}) still '{order_status_obj.status}'.") # e.g. 'new', 'accepted'
            else: # Could not get status
                logger.log_action(f"Could not get status for pending {order_id} ({ticker}, {order_type}). Assuming inactive, removing.")
                orders_to_remove_from_pending_file.append(order_id)

    for oid in orders_to_remove_from_pending_file:
        if oid in pending_orders: del pending_orders[oid]

    save_pending_orders(pending_orders)
    save_run_pending_orders_snapshot(pending_orders)

    # TODO: Clarify this logic based on PENDING_ORDERS_FILE lifecycle (see README)
    if os.path.exists(config.RUN_PENDING_ORDERS_FILE):
        try:
            os.remove(config.PENDING_ORDERS_FILE)
            logger.log_action(f"Removed {config.PENDING_ORDERS_FILE} after snapshot.")
        except OSError as e:
            logger.log_action(f"Error removing {config.PENDING_ORDERS_FILE}: {e}")

    if filled_any_new_entries:
        logger.log_action("New positions opened. Re-running position management for immediate exit checks...")
        position_manager.check_and_manage_open_positions(latest_prices, historical_data_map_for_pm, api)

    logger.log_action(f"===== Trading Bot session finished at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====\n")

if __name__ == "__main__":
    # Script designed for single daily execution. Scheduling is external.
    main()
