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
    # --- Bot State Initialization ---
    # 1. Synchronize Pending Orders with Alpaca
    logger.log_action("Initializing Bot State: Synchronizing pending orders with Alpaca...")
    local_pending_orders_from_file = load_pending_orders() # Load what bot thought was pending
    alpaca_live_open_orders = order_manager.get_open_orders(api_client=api)
    
    # Create a map of Alpaca live orders by ID for efficient lookup
    alpaca_live_open_orders_map_by_id = {order.id: order for order in alpaca_live_open_orders}

    current_pending_orders = {} # This will be the source of truth for pending orders this run

    # Sync based on Alpaca's view
    for order_id, alpaca_order in alpaca_live_open_orders_map_by_id.items():
        ticker = alpaca_order.symbol
        qty = float(alpaca_order.qty)
        side = alpaca_order.side
        limit_price = float(alpaca_order.limit_price) if alpaca_order.limit_price else None
        status = alpaca_order.status

        # Try to get supplementary data from local file
        local_details = local_pending_orders_from_file.get(order_id, {})
        order_type = local_details.get('type', f"alpaca_sync_{side}") # Infer type if not known
        placed_at_str = local_details.get('placed_at')
        z_at_placement = local_details.get('z_at_placement')

        # Ensure placed_at is valid ISO format or use Alpaca's submission time
        final_placed_at = datetime.now().isoformat() # Fallback
        if hasattr(alpaca_order, 'submitted_at') and alpaca_order.submitted_at:
            try:
                # alpaca_order.submitted_at could be datetime or str depending on SDK version/settings
                if isinstance(alpaca_order.submitted_at, datetime):
                    final_placed_at = alpaca_order.submitted_at.isoformat()
                else: # Assume string
                    final_placed_at = pd.to_datetime(alpaca_order.submitted_at).isoformat()
            except Exception as e_ts:
                logger.log_action(f"Trading Bot (pending_order_sync): Error parsing submitted_at for order {order_id}: {e_ts}. Using fallback.")
        elif placed_at_str: # Use local if Alpaca's is missing and local is present
             final_placed_at = placed_at_str


        current_pending_orders[order_id] = {
            "ticker": ticker, "qty": qty, "side": side, "limit_price": limit_price,
            "type": order_type, "placed_at": final_placed_at,
            "z_at_placement": z_at_placement, "status": status
        }
        logger.log_action(f"Trading Bot (pending_order_sync): Synced/Verified pending order {order_id} ({ticker}) from Alpaca. Status: {status}")

    # Log orders that were in local file but not in Alpaca's open orders (they might have filled/cancelled)
    for order_id, local_details in local_pending_orders_from_file.items():
        if order_id not in current_pending_orders:
            logger.log_action(f"Trading Bot (pending_order_sync): Local pending order {order_id} ({local_details.get('ticker')}) not found in Alpaca open orders. Will be re-checked in Step 5.")
            # These will be handled in the main reconciliation loop (Step 5) to confirm final status

    save_pending_orders(current_pending_orders) # Save the Alpaca-centric view
    logger.log_action(f"Trading Bot (pending_order_sync): Synchronization complete. {len(current_pending_orders)} pending orders tracked.")

    # 2. Synchronize Positions with Alpaca
    logger.log_action("Initializing Bot State: Synchronizing positions with Alpaca...")
    alpaca_live_positions_map = data_fetcher.get_alpaca_open_positions(api_client=api)
    local_positions_from_file = position_manager.load_positions_from_file() # Load raw local data

    current_positions = position_manager.sync_positions_from_alpaca(alpaca_live_positions_map, local_positions_from_file)
    position_manager.save_positions(current_positions) # Save the authoritative, synced state
    logger.log_action(f"Trading Bot (position_sync): Synchronization complete. {len(current_positions)} open positions tracked.")
    # `current_positions` is now the source of truth for positions for this run.
    # It contains datetime objects where appropriate.

    # --- Step 1: Pre-computation & Data Fetching ---
    # (Renamed from "Manage Existing Positions and Orders" to better reflect its new role)
    logger.log_action("Step 1: Fetching supporting data (orders, market data)...")

    # Fetch all open orders from Alpaca again (or use `alpaca_live_open_orders` if fresh enough)
    # This is used by position_manager.check_and_manage_open_positions to see if exit orders already exist.
    alpaca_open_orders_list_for_pm = order_manager.get_open_orders(api_client=api, tickers=config.TICKERS)
    alpaca_open_orders_map_for_pm = {} # Ticker -> [AlpacaOrder]
    for order in alpaca_open_orders_list_for_pm:
        alpaca_open_orders_map_for_pm.setdefault(order.symbol, []).append(order)
    if not alpaca_open_orders_list_for_pm: logger.log_action("No open orders on Alpaca for configured tickers (for PM check).")
    else: logger.log_action(f"Found {len(alpaca_open_orders_list_for_pm)} open Alpaca orders for PM check.")


    # Initial check for filled/cancelled known exit orders in `current_positions`
    # This updates status from 'pending_exit' to 'open' or removes the position.
    positions_after_exit_check = current_positions.copy() # Work on a copy
    any_positions_changed_by_exit_check = False
    for ticker, details in list(positions_after_exit_check.items()): # Use list for safe removal
        if details.get('status') == 'pending_exit':
            known_exit_order_id = details.get('pending_exit_order_id')
            if known_exit_order_id:
                logger.log_action(f"Trading Bot (initial_exit_check): Checking known pending exit order {known_exit_order_id} for {ticker}.")
                order_status_obj = order_manager.get_order_status(known_exit_order_id, api_client=api)
                if order_status_obj:
                    if order_status_obj.status == 'filled':
                        try:
                            fill_price = float(order_status_obj.filled_avg_price)
                            # fill_qty = float(order_status_obj.filled_qty) # Qty from position details is authority
                            logger.log_action(f"Trading Bot (initial_exit_check): Known exit order {known_exit_order_id} for {ticker} FILLED. Price: ${fill_price}.")
                            exit_reason = details.get('exit_reason_for_order', 'automated_exit_filled_at_startup')
                            # Use remove_position which now takes positions dict
                            positions_after_exit_check = position_manager.remove_position(positions_after_exit_check, ticker, fill_price, exit_reason, known_exit_order_id)
                            any_positions_changed_by_exit_check = True
                            # Also remove from current_pending_orders if it was there (shouldn't be if exit order)
                            if known_exit_order_id in current_pending_orders:
                                del current_pending_orders[known_exit_order_id]
                        except Exception as ex:
                            logger.log_action(f"Trading Bot (initial_exit_check): Error processing filled known exit order {known_exit_order_id} ({ticker}): {ex}")
                    elif order_status_obj.status in ['canceled', 'expired', 'rejected', 'done_for_day']:
                        logger.log_action(f"Trading Bot (initial_exit_check): Known exit order {known_exit_order_id} for {ticker} is {order_status_obj.status}. Reverting position to 'open'.")
                        positions_after_exit_check[ticker].update({'status': 'open', 'pending_exit_order_id': None, 'pending_exit_order_placed_at': None, 'exit_reason_for_order': None})
                        any_positions_changed_by_exit_check = True
                        if known_exit_order_id in current_pending_orders: # Should also be removed from pending
                             del current_pending_orders[known_exit_order_id]
                    # else: status is still open-like, leave as 'pending_exit'
                else: # Could not get status
                    logger.log_action(f"Trading Bot (initial_exit_check): Could not get status for known pending exit {known_exit_order_id} ({ticker}). Assuming inactive for now, reverting to 'open'.")
                    positions_after_exit_check[ticker].update({'status': 'open', 'pending_exit_order_id': None, 'pending_exit_order_placed_at': None, 'exit_reason_for_order': None})
                    any_positions_changed_by_exit_check = True
                    if known_exit_order_id in current_pending_orders:
                        del current_pending_orders[known_exit_order_id]
            else: # 'pending_exit' but no order_id (should be rare after sync)
                logger.log_action(f"Trading Bot (initial_exit_check): Position {ticker} 'pending_exit' but no order_id. Checking Alpaca for open exit order.")
                # This case should ideally be resolved by sync_positions_from_alpaca or earlier checks
                # If still here, might revert to 'open' or try to find matching order
                # For now, let check_and_manage_open_positions handle it if it persists.

    current_positions = positions_after_exit_check # Update current_positions with results of this check
    if any_positions_changed_by_exit_check:
        position_manager.save_positions(current_positions) # Save if changes were made
        save_pending_orders(current_pending_orders) # Save if changes were made


    # --- Step 2: Fetch Market Data ---
    logger.log_action("Step 2: Fetching market data (historical and latest prices)...")
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

    # --- Step 3: Manage Open Positions (Check for Exits) ---
    logger.log_action("Step 3: Managing open positions for potential exits...")
    # Pass the authoritative `current_positions` and `alpaca_live_positions_map` (which is alpaca_open_positions_map)
    # Also pass `alpaca_open_orders_map_for_pm` for checking existing exit orders
    positions_after_management = position_manager.check_and_manage_open_positions(
        current_positions, # This is the synced and authoritative set of positions
        latest_prices,
        historical_data_map_for_pm,
        api,
        alpaca_open_orders_map_for_pm, # Map of open orders by ticker
        alpaca_live_positions_map      # Map of live Alpaca positions by ticker
    )
    # If check_and_manage_open_positions modified the dictionary, it returned a new one.
    # Update current_positions and save if it changed.
    if id(positions_after_management) != id(current_positions) or positions_after_management != current_positions:
        logger.log_action("Trading Bot: Positions dictionary updated by check_and_manage_open_positions. Saving.")
        current_positions = positions_after_management
        position_manager.save_positions(current_positions)
    else:
        logger.log_action("Trading Bot: No changes to positions dictionary from check_and_manage_open_positions.")


    # --- Step 4: Evaluate New Entry Signals ---
    logger.log_action("Step 4: Evaluating new entry signals...")
    available_cash = position_manager.get_available_cash(api)
    logger.log_action(f"Available cash for new entries: ${available_cash:.2f}")
    pdt_count = 0
    try:
        pdt_count = int(api.get_account().daytrade_count)
        logger.log_action(f"PDT count from Alpaca API: {pdt_count}")
    except Exception as e:
        logger.log_action(f"Could not get PDT count from Alpaca API: {e}. Using CSV method.")
        pdt_count = position_manager.get_pdt_trade_count() # This is a placeholder in position_manager

    # `current_pending_orders` is already up-to-date from initial sync.
    # `current_positions` is also up-to-date.

    for ticker_symbol in config.TICKERS:
        current_price = latest_prices.get(ticker_symbol)
        if not isinstance(current_price, (int, float)) or current_price <= 0:
            logger.log_action(f"Invalid/missing price for {ticker_symbol} ({current_price}); skipping entry.")
            continue
        if ticker_symbol in current_positions and current_positions[ticker_symbol].get('status') in ['open', 'pending_exit']:
            logger.log_action(f"Trading Bot (new_entry_eval): Active or pending_exit position for {ticker_symbol}. Skipping new entry.")
            continue

        # Check against `alpaca_open_orders_map_for_pm` (which is up-to-date) instead of the older `alpaca_open_orders_map`
        if ticker_symbol in alpaca_open_orders_map_for_pm:
            # More specific check: is there an OPEN BUY/SELL order (not an exit for a short/long)
            # This check might be too broad if `alpaca_open_orders_map_for_pm` includes exit orders for other strategies.
            # For now, assume any open order for the ticker means no new entry.
            logger.log_action(f"Trading Bot (new_entry_eval): Existing open Alpaca order(s) for {ticker_symbol} in alpaca_open_orders_map_for_pm. Skipping new entry.")
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
                logger.log_action(f"Trading Bot: Entry order {entry_order.id} ({order_side} {qty} {ticker_symbol}) placed. Status: {entry_order.status}")
                # Add to current_pending_orders immediately and save
                current_pending_orders[entry_order.id] = {
                    "ticker": ticker_symbol, "qty": qty, "side": order_side, "limit_price": current_price,
                    "type": "entry_long" if signal == "BUY" else "entry_short",
                    "placed_at": datetime.now().isoformat(), "z_at_placement": current_z_score,
                    "status": entry_order.status # Initial status from Alpaca
                }
                save_pending_orders(current_pending_orders) # Save updated pending orders
                available_cash -= (qty * current_price) # Decrement available cash (approximate)
            else:
                logger.log_action(f"Trading Bot: Failed to place entry order for {ticker_symbol}.")

    # --- Step 5: Final Reconciliation of Pending Orders and Positions ---
    logger.log_action("Step 5: Final reconciliation of all pending orders and resulting positions...")
    any_new_entries_filled_this_cycle = False

    # Re-fetch live open orders to get the very latest status from Alpaca
    final_alpaca_live_open_orders_list = order_manager.get_open_orders(api_client=api)
    final_alpaca_live_open_orders_map_by_id = {order.id: order for order in final_alpaca_live_open_orders_list}

    orders_to_remove_from_current_pending = []

    for order_id, order_details in list(current_pending_orders.items()): # Iterate copy for safe modification
        ticker = order_details['ticker']
        order_type = order_details['type'] # e.g. "entry_long", "entry_short", "alpaca_sync_buy"

        if order_id in final_alpaca_live_open_orders_map_by_id: # Still open on Alpaca
            alpaca_order_obj = final_alpaca_live_open_orders_map_by_id[order_id]
            if current_pending_orders[order_id]['status'] != alpaca_order_obj.status:
                logger.log_action(f"Trading Bot (final_recon): Pending order {order_id} ({ticker}, {order_type}) status updated on Alpaca to '{alpaca_order_obj.status}'. Was '{current_pending_orders[order_id]['status']}'.")
                current_pending_orders[order_id]['status'] = alpaca_order_obj.status
            # else: status is the same, no action needed other than keeping it in current_pending_orders
        else: # Not in Alpaca's latest open list; must be filled, cancelled, expired, etc.
            logger.log_action(f"Trading Bot (final_recon): Pending order {order_id} ({ticker}, {order_type}) not in Alpaca's latest open orders. Checking final status...")
            final_status_obj = order_manager.get_order_status(order_id, api_client=api)

            if final_status_obj:
                logger.log_action(f"Trading Bot (final_recon): Final status for order {order_id} ({ticker}) is '{final_status_obj.status}'.")
                if final_status_obj.status == 'filled':
                    try:
                        fill_price = float(final_status_obj.filled_avg_price)
                        # Use original order_details['qty'] as authority for intended quantity
                        fill_qty = float(order_details['qty'])
                        if hasattr(final_status_obj, 'filled_qty') and final_status_obj.filled_qty is not None:
                             # Log if Alpaca's filled_qty differs, but proceed with original order's qty for position sizing
                            if abs(float(final_status_obj.filled_qty) - fill_qty) > 0.001 :
                                logger.log_action(f"Trading Bot (final_recon): Filled qty discrepancy for order {order_id}. Alpaca: {final_status_obj.filled_qty}, Expected: {fill_qty}. Using expected qty for position.")

                        logger.log_action(f"Trading Bot (final_recon): Order {order_id} ({ticker}, {order_type}) FILLED. Qty: {fill_qty}, Price: ${fill_price:.2f}.")

                        # Determine position type based on original order intention
                        pos_type_from_order = None
                        if order_type.startswith('entry_long') or (order_type.startswith('alpaca_sync') and order_details['side'] == 'buy'):
                            pos_type_from_order = 'long'
                        elif order_type.startswith('entry_short') or (order_type.startswith('alpaca_sync') and order_details['side'] == 'sell'):
                            pos_type_from_order = 'short'

                        if pos_type_from_order:
                            entry_fill_time = datetime.now() # Default
                            if hasattr(final_status_obj, 'filled_at') and final_status_obj.filled_at:
                                try:
                                    filled_at_val = final_status_obj.filled_at
                                    if isinstance(filled_at_val, str): entry_fill_time = pd.to_datetime(filled_at_val).to_pydatetime()
                                    elif isinstance(filled_at_val, datetime): entry_fill_time = filled_at_val
                                    elif hasattr(filled_at_val, 'isoformat'): entry_fill_time = datetime.fromisoformat(filled_at_val.isoformat())
                                except Exception as e_ts:
                                     logger.log_action(f"Trading Bot (final_recon): Error parsing filled_at for order {order_id}: {e_ts}. Using current time.")

                            # Add to our `current_positions` dictionary
                            current_positions = position_manager.add_position(current_positions, ticker, fill_qty, fill_price, pos_type_from_order, order_id, entry_fill_time)
                            any_new_entries_filled_this_cycle = True
                            logger.log_action(f"Trading Bot (final_recon): Added new position for {ticker} from filled order {order_id}.")
                        else:
                            logger.log_action(f"Trading Bot (final_recon): WARNING - Unknown effective position type for filled order {order_id} (type: {order_type}, side: {order_details['side']}). Not adding position automatically.")

                        orders_to_remove_from_current_pending.append(order_id)
                    except Exception as ex:
                        logger.log_action(f"Trading Bot (final_recon): Error processing filled order {order_id} ({ticker}, {order_type}): {ex}. Order details: {vars(final_status_obj)}")
                elif final_status_obj.status in ['expired', 'canceled', 'rejected', 'done_for_day']:
                    logger.log_action(f"Trading Bot (final_recon): Order {order_id} ({ticker}, {order_type}) is '{final_status_obj.status}'. Removing from pending list.")
                    orders_to_remove_from_current_pending.append(order_id)
                else: # e.g. 'new', 'accepted', 'pending_cancel' - should ideally not happen if not in open list, but log
                    logger.log_action(f"Trading Bot (final_recon): Order {order_id} ({ticker}, {order_type}) has status '{final_status_obj.status}' but was not in open list. Keeping in pending for now.")
                    current_pending_orders[order_id]['status'] = final_status_obj.status # Update status
            else: # Could not get status from Alpaca for an order not in the open list
                logger.log_action(f"Trading Bot (final_recon): Could not get final status for pending order {order_id} ({ticker}, {order_type}). Assuming inactive, removing from pending list.")
                orders_to_remove_from_current_pending.append(order_id)

    # Process removals from current_pending_orders
    if orders_to_remove_from_current_pending:
        for oid in orders_to_remove_from_current_pending:
            if oid in current_pending_orders:
                del current_pending_orders[oid]
        logger.log_action(f"Trading Bot (final_recon): Removed {len(orders_to_remove_from_current_pending)} orders from active pending list.")

    # Save the final state of pending orders and positions for this run
    save_pending_orders(current_pending_orders)
    save_run_pending_orders_snapshot(current_pending_orders) # Snapshot for this run
    if any_new_entries_filled_this_cycle: # Also save positions if new ones were added
        position_manager.save_positions(current_positions)


    # Optional: Clean up the main PENDING_ORDERS_FILE if snapshotting is the primary goal for historicals
    # This depends on desired inter-run persistence strategy. For now, PENDING_ORDERS_FILE holds the latest known state.
    # if os.path.exists(config.RUN_PENDING_ORDERS_FILE):
    #     try:
    #         # os.remove(config.PENDING_ORDERS_FILE) # Or clear it, or let it be overwritten next run's start
    #         logger.log_action(f"Trading Bot: Main pending orders file {config.PENDING_ORDERS_FILE} retained with latest state.")
    #     except OSError as e:
    #         logger.log_action(f"Error related to {config.PENDING_ORDERS_FILE}: {e}")

    if any_new_entries_filled_this_cycle:
        logger.log_action("Trading Bot: New positions were opened. Re-running position management for immediate exit checks on these new positions...")
        # Re-fetch latest prices if there could have been a significant delay
        # latest_prices_for_final_check = data_fetcher.get_latest_prices(config.TICKERS, api_client=api)
        # For simplicity, using existing latest_prices. In a real scenario, might re-fetch.

        # Re-fetch open orders map as well, as new exits might have been placed by other logic if run concurrently (unlikely here)
        final_alpaca_open_orders_list_for_pm_rerun = order_manager.get_open_orders(api_client=api, tickers=config.TICKERS)
        final_alpaca_open_orders_map_for_pm_rerun = {order.symbol: [] for order in final_alpaca_open_orders_list_for_pm_rerun}
        for order in final_alpaca_open_orders_list_for_pm_rerun:
            final_alpaca_open_orders_map_for_pm_rerun[order.symbol].append(order)

        # Re-fetch live positions map
        final_alpaca_live_positions_map_rerun = data_fetcher.get_alpaca_open_positions(api_client=api)

        positions_after_final_mgmt = position_manager.check_and_manage_open_positions(
            current_positions, # Pass the latest `current_positions` which includes newly added ones
            latest_prices, # Or latest_prices_for_final_check
            historical_data_map_for_pm,
            api,
            final_alpaca_open_orders_map_for_pm_rerun,
            final_alpaca_live_positions_map_rerun
        )
        if id(positions_after_final_mgmt) != id(current_positions) or positions_after_final_mgmt != current_positions:
            logger.log_action("Trading Bot: Positions dictionary updated by final post-fill management. Saving.")
            current_positions = positions_after_final_mgmt
            position_manager.save_positions(current_positions)

    logger.log_action(f"===== Trading Bot session finished at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====\n")

if __name__ == "__main__":
    # Script designed for single daily execution. Scheduling is external.
    main()
