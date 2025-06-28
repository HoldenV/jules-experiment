import json
import os
from datetime import datetime, timedelta
import pandas as pd
import config
import logger
import order_manager
import signal_generator

# Alpaca API client is passed to functions needing it from trading_bot.py

def load_positions_from_file():
    """
    Loads current open positions from the JSON file.
    This function now simply loads raw data without date conversions.
    Date conversions and sync logic will be handled by the synchronization function.
    :return: Dict of open positions {ticker: {details}}, or {} if error/not found/invalid JSON.
    """
    os.makedirs(os.path.dirname(config.POSITIONS_FILE), exist_ok=True)

    if not os.path.exists(config.POSITIONS_FILE):
        logger.log_action(f"Position Manager: Positions file not found at {config.POSITIONS_FILE}. Returning empty data.")
        return {}
    try:
        with open(config.POSITIONS_FILE, 'r') as f:
            content = f.read()
            if not content.strip(): # Handle empty file
                logger.log_action(f"Position Manager: Positions file {config.POSITIONS_FILE} is empty. Returning empty data.")
                return {}
            positions_raw = json.loads(content)
        return positions_raw
    except json.JSONDecodeError as e:
        logger.log_action(f"Position Manager: Error decoding JSON from {config.POSITIONS_FILE}: {e}. Returning empty data.")
        return {}
    except Exception as e:
        logger.log_action(f"Position Manager: Error loading positions from {config.POSITIONS_FILE}: {e}. Returning empty data.")
        return {}

def sync_positions_from_alpaca(alpaca_positions_map, local_positions_data):
    """
    Synchronizes positions based on Alpaca as the source of truth, supplemented by local data.
    Converts relevant date strings from local_positions_data to datetime objects.

    :param alpaca_positions_map: Dict {ticker: AlpacaPositionObject} from Alpaca.
    :param local_positions_data: Dict {ticker: {details}} loaded from local positions.json.
    :return: Dict of synchronized open positions {ticker: {details}}.
    """
    synced_positions = {}
    now_datetime = datetime.now()

    for ticker, alpaca_pos in alpaca_positions_map.items():
        local_data = local_positions_data.get(ticker, {})

        # Convert date strings from local_data to datetime objects if they exist
        entry_date_str = local_data.get('entry_date')
        entry_date_dt = None
        if entry_date_str:
            try:
                entry_date_dt = datetime.fromisoformat(entry_date_str)
            except (TypeError, ValueError):
                logger.log_action(f"Position Manager (sync): Invalid entry_date format '{entry_date_str}' for {ticker}. Using current time.")
                entry_date_dt = now_datetime

        pending_exit_placed_at_str = local_data.get('pending_exit_order_placed_at')
        pending_exit_placed_at_dt = None
        if pending_exit_placed_at_str:
            try:
                pending_exit_placed_at_dt = datetime.fromisoformat(pending_exit_placed_at_str)
            except (TypeError, ValueError):
                 logger.log_action(f"Position Manager (sync): Invalid pending_exit_order_placed_at format '{pending_exit_placed_at_str}' for {ticker}.")
                 # This field might be None if no pending exit, so None is acceptable.

        synced_pos_details = {
            "qty": abs(float(alpaca_pos.qty)), # Ensure positive qty, type determined by 'side'
            "entry_price": float(alpaca_pos.avg_entry_price),
            "type": alpaca_pos.side, # 'long' or 'short'
            "status": local_data.get('status', 'open'), # Default to 'open', can be 'pending_exit'
            "entry_date": entry_date_dt or now_datetime, # Use local if valid, else Alpaca sync time
            "entry_order_id": local_data.get('entry_order_id', f"ALPACA_SYNC_{now_datetime.strftime('%Y%m%d%H%M%S')}"),
            "pnl": float(alpaca_pos.unrealized_pl), # Use Alpaca's P&L
            "pending_exit_order_id": local_data.get('pending_exit_order_id'),
            "pending_exit_order_placed_at": pending_exit_placed_at_dt,
            "exit_reason_for_order": local_data.get('exit_reason_for_order')
        }

        # Log if Alpaca qty/price significantly differs from a previously known local one (if any)
        if local_data:
            if abs(float(local_data.get('qty', 0)) - synced_pos_details['qty']) > 0.001: # tolerance for float comparison
                 logger.log_action(f"Position Manager (sync): Discrepancy in qty for {ticker}. Alpaca: {synced_pos_details['qty']}, Local: {local_data.get('qty')}. Using Alpaca.")
            if abs(float(local_data.get('entry_price', 0)) - synced_pos_details['entry_price']) > 0.01: # price tolerance
                 logger.log_action(f"Position Manager (sync): Discrepancy in entry_price for {ticker}. Alpaca: {synced_pos_details['entry_price']:.2f}, Local: {local_data.get('entry_price')}. Using Alpaca.")

        synced_positions[ticker] = synced_pos_details
        logger.log_action(f"Position Manager (sync): Synced position for {ticker} from Alpaca. Qty: {synced_pos_details['qty']}, Entry: {synced_pos_details['entry_price']:.2f}, Type: {synced_pos_details['type']}")

    # Log positions that were in local_positions_data but not in Alpaca (meaning they were likely closed)
    for ticker, local_details in local_positions_data.items():
        if ticker not in alpaca_positions_map:
            logger.log_action(f"Position Manager (sync): Local position for {ticker} not found in Alpaca live positions. Assumed closed/external action.")
            # These positions are effectively removed by not being added to synced_positions

    return synced_positions

def save_positions(positions):
    """
    Saves current open positions to JSON, converting datetime objects to ISO strings.
    :param positions: Dict of open positions {ticker: {details}}.
    """
    os.makedirs(os.path.dirname(config.POSITIONS_FILE), exist_ok=True)

    try:
        positions_serializable = {}
        for ticker, details in positions.items():
            details_copy = details.copy()
            if 'entry_date' in details_copy and isinstance(details_copy['entry_date'], datetime):
                details_copy['entry_date'] = details_copy['entry_date'].isoformat()
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
    This function now expects `positions` dict as an argument and returns the modified dict.
    It also expects `entry_date` to be a datetime object.

    :param positions: The current dictionary of positions.
    :param ticker: Stock ticker.
    :param qty: Quantity of shares.
    :param entry_price: Price at which the position was entered.
    :param position_type: "long" or "short".
    :param entry_order_id: ID of the order that opened this position.
    :param entry_date: datetime object of when the entry occurred.
    :return: Updated positions dictionary.
    """
    # Ensure positions is a mutable copy if it's being passed around and modified
    current_positions = positions.copy()

    if entry_date is None: # Should ideally be provided by caller based on fill time
        logger.log_action(f"Position Manager (add_position): entry_date not provided for {ticker}. Using current time.")
        entry_date = datetime.now()
    elif not isinstance(entry_date, datetime):
        logger.log_action(f"Position Manager (add_position): entry_date for {ticker} is not a datetime object. Type: {type(entry_date)}. Attempting conversion or using now.")
        try:
            entry_date = datetime.fromisoformat(str(entry_date))
        except: # pylint: disable=bare-except
            entry_date = datetime.now()


    current_positions[ticker] = {
        "qty": qty,
        "entry_price": entry_price,
        "entry_date": entry_date, # Expected to be datetime object
        "type": position_type,       # "long" or "short"
        "status": "open",
        "entry_order_id": entry_order_id,
        "pnl": 0.0, # Initial P&L is 0, can be updated from Alpaca later if needed
        "pending_exit_order_id": None,
        "pending_exit_order_placed_at": None,
        "exit_reason_for_order": None
    }
    # The save_positions call is removed from here; it should be managed by the calling function (e.g., in trading_bot.py after all updates)
    logger.log_action(f"Position Manager: Staged new 'open' position for save: {qty} {ticker} @ {entry_price} ({position_type}) on {entry_date.strftime('%Y-%m-%d %H:%M:%S')}. Entry Order ID: {entry_order_id}")
    return current_positions

def remove_position(positions, ticker, exit_price, exit_reason, exit_order_id=None):
    """
    Removes a position from the provided dictionary and records the trade.
    This function now expects `positions` dict as an argument and returns the modified dict.

    :param positions: The current dictionary of positions.
    :param ticker: Stock ticker of the position to remove.
    :param exit_price: Price at which the position was exited.
    :param exit_reason: Reason for the exit.
    :param exit_order_id: ID of the order that closed this position (optional).
    :return: Updated positions dictionary.
    """
    current_positions = positions.copy()
    if ticker in current_positions:
        pos_details = current_positions.pop(ticker)
        # save_positions(positions) # Removed, saving handled by caller

        profit_loss = 0
        # Ensure entry_date is datetime for strftime
        entry_date_for_trade_record = pos_details['entry_date']
        if isinstance(entry_date_for_trade_record, str):
            try:
                entry_date_for_trade_record = datetime.fromisoformat(entry_date_for_trade_record)
            except ValueError:
                logger.log_action(f"Position Manager (remove_position): Could not parse entry_date string '{pos_details['entry_date']}' for trade record. Using placeholder.")
                entry_date_for_trade_record = datetime.now() # Fallback, though ideally should always be datetime
        elif not isinstance(entry_date_for_trade_record, datetime):
             logger.log_action(f"Position Manager (remove_position): entry_date '{pos_details['entry_date']}' is not datetime or string. Using placeholder.")
             entry_date_for_trade_record = datetime.now()


        entry_price = float(pos_details['entry_price'])
        qty = float(pos_details['qty'])

        if pos_details['type'] == 'long':
            profit_loss = (exit_price - entry_price) * qty
        elif pos_details['type'] == 'short':
            profit_loss = (entry_price - exit_price) * qty
        else:
            logger.log_action(f"Position Manager (remove_position): Unknown position type '{pos_details['type']}' for {ticker}. P&L calculation may be incorrect.")


        logger.record_trade(
            ticker,
            entry_date_for_trade_record.strftime('%Y-%m-%d %H:%M:%S') if isinstance(entry_date_for_trade_record, datetime) else str(entry_date_for_trade_record),
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            entry_price,
            exit_price,
            profit_loss,
            exit_reason
        )
        log_message = f"Position Manager: Staged position removal for save: {ticker}. Exit: {exit_price}, Reason: {exit_reason}, P&L: {profit_loss:.2f}."
        if exit_order_id:
            log_message += f" Exit Order ID: {exit_order_id}."
        logger.log_action(log_message)
    else:
        logger.log_action(f"Position Manager: Attempted to remove non-existent position for {ticker} from provided dict.")
    return current_positions

def check_and_manage_open_positions(current_positions_arg, current_prices, all_historical_data, api_client, alpaca_open_orders_map=None, alpaca_open_positions_map=None):
    """
    Manages open positions: checks max hold, stop-loss, exit signals.
    It now takes current_positions_arg as an argument instead of loading from file.
    The initial reconciliation with Alpaca (Phase 1) is assumed to have been done by the caller.

    :param current_positions_arg: Dict of current open positions, already synced with Alpaca.
    :param current_prices: Dict of latest prices {ticker: price}.
    :param all_historical_data: Dict {ticker: pd.DataFrame} for signal re-evaluation.
    :param api_client: Initialized Alpaca API client.
    :param alpaca_open_orders_map: Optional Dict {ticker: [AlpacaOrder]} from Alpaca, for checking existing exit orders.
    :param alpaca_open_positions_map: Optional Dict {ticker: AlpacaPosition} from Alpaca (can be used for verification or if needed, but primary position data is from current_positions_arg).
    :return: Updated positions dictionary.
    """
    # positions = load_positions_from_file() # Removed: current_positions_arg is now an argument
    positions_to_manage = current_positions_arg.copy() # Work on a copy

    alpaca_open_orders_map = alpaca_open_orders_map if alpaca_open_orders_map is not None else {}
    # alpaca_open_positions_map is available if needed for cross-check, but current_positions_arg is the primary source

    logger.log_action("Position Manager: Starting strategy-based management of open positions...")
    positions_updated_in_cycle = False # Tracks if any position's state changed in this function
    today = datetime.now()

    # --- Phase 1: Reconciliation with Alpaca's open positions ---
    # This section is SIGNIFICANTLY REDUCED as the main sync is now upstream in trading_bot.py.
    # We trust `current_positions_arg` is already synced.
    # We might add some assertions here later if desired.
    
    # Verification: Log if a position in `current_positions_arg` is not in `alpaca_open_positions_map` (if provided and maps are up-to-date)
    # This could indicate a discrepancy that should have been caught by the upstream sync or a stale map.
    if alpaca_open_positions_map is not None:
        for ticker, details in positions_to_manage.items():
            if ticker not in alpaca_open_positions_map and details.get('status') == 'open':
                logger.log_action(f"Position Manager (check_and_manage): WARNING - Position {ticker} is 'open' in argument but not in provided Alpaca positions map. Upstream sync or map might be misaligned.")

    if not positions_to_manage:
        logger.log_action("Position Manager: No open positions to manage from argument.")
        return positions_to_manage # Return the (empty) copied dictionary

    logger.log_action(f"Position Manager: Managing {len(positions_to_manage)} positions based on strategy...")
    
    # --- Phase 2: Apply strategy-based management to 'open' local positions ---
    for ticker, details in list(positions_to_manage.items()):
        if details.get('status') != 'open':
            # logger.log_action(f"Position Manager: Skipping {ticker}, status '{details.get('status', 'unknown')}' (not 'open').") # Can be verbose
            continue

        current_price = current_prices.get(ticker)
        if current_price is None or not isinstance(current_price, (int, float)) or current_price <= 0:
            logger.log_action(f"Position Manager: Invalid/missing current price for open position {ticker} ({current_price}). Skipping management for this ticker.")
            continue

        # Ensure entry_date is a datetime object for calculations
        entry_date = details['entry_date'] # Should be datetime from sync_positions_from_alpaca
        if not isinstance(entry_date, datetime): # Defensive check
            logger.log_action(f"Position Manager: Entry_date for {ticker} is not a datetime object (Type: {type(entry_date)}). Attempting conversion.")
            try:
                entry_date = datetime.fromisoformat(str(entry_date))
                positions_to_manage[ticker]['entry_date'] = entry_date
            except (ValueError, TypeError):
                logger.log_action(f"Position Manager: Invalid entry_date format for {ticker} ('{details['entry_date']}') during management. Skipping age check.")
                continue


        qty_to_close = float(details['qty'])
        position_type = details['type']
        exit_order_placed_this_cycle = False
        exit_reason = None

        # 1. Check Max Holding Period
        if isinstance(entry_date, datetime): # Redundant due to check above, but safe
            if (today - entry_date).days >= config.MAX_HOLDING_PERIOD_DAYS:
                logger.log_action(f"Position Manager: {ticker} ({position_type}) hit max hold ({config.MAX_HOLDING_PERIOD_DAYS} days). Entry: {entry_date.strftime('%Y-%m-%d')}, Today: {today.strftime('%Y-%m-%d')}")
                exit_reason = f"max_hold_{config.MAX_HOLDING_PERIOD_DAYS}_days"
        # else: # Already logged above
            # logger.log_action(f"Position Manager: Cannot check max hold for {ticker} due to invalid entry_date type: {type(entry_date)}")


        # 2. Check Z-Score Based Exit/Stop-Loss (if not already exiting due to max hold)
        if not exit_reason:
            ticker_hist_data_for_z = all_historical_data.get(ticker)
            if ticker_hist_data_for_z is None or ticker_hist_data_for_z.empty:
                logger.log_action(f"Position Manager: No historical data for {ticker} to re-eval z-score for exit.")
            else:
                temp_hist_data = ticker_hist_data_for_z.copy()
                if 'close' not in temp_hist_data.columns:
                    logger.log_action(f"Position Manager: 'close' column missing in hist data for {ticker} (exit eval).")
                else:
                    try:
                        if not isinstance(temp_hist_data.index, pd.DatetimeIndex):
                            logger.log_action(f"Position Manager: Historical data for {ticker} does not have a DatetimeIndex. Cannot reliably append current price for Z-score.")
                        else:
                            # Create a new row for the current price, ensuring timezone compatibility
                            current_price_timestamp = pd.Timestamp.now()
                            if temp_hist_data.index.tz:
                                current_price_timestamp = current_price_timestamp.tz_localize(temp_hist_data.index.tz)
                            else:
                                current_price_timestamp = current_price_timestamp.tz_localize(None) # Ensure no tz if original has none

                            current_price_row = pd.DataFrame({'close': [current_price]}, index=[current_price_timestamp])

                            # Align columns for concatenation
                            for col_header in temp_hist_data.columns:
                                if col_header not in current_price_row.columns: current_price_row[col_header] = pd.NA
                            # Ensure all columns from current_price_row are in temp_hist_data for concat
                            # This might add new columns to temp_hist_data if current_price_row has unique ones not related to 'close'
                            for col_header in current_price_row.columns:
                                if col_header not in temp_hist_data.columns: temp_hist_data[col_header] = pd.NA

                            # Ensure order of columns is the same to avoid performance warning or use specific columns
                            # Only select columns that are in both, or ensure temp_hist_data has all from current_price_row
                            common_cols = temp_hist_data.columns.intersection(current_price_row.columns)
                            aligned_current_price_row = current_price_row[common_cols]
                            # If temp_hist_data is missing columns that aligned_current_price_row has (shouldn't happen if common_cols is used right)
                            # For safety, reindex temp_hist_data to include all columns from aligned_current_price_row, filled with NA
                            temp_hist_data_reindexed = temp_hist_data.reindex(columns=temp_hist_data.columns.union(aligned_current_price_row.columns), fill_value=pd.NA)

                            temp_hist_data_with_current = pd.concat([temp_hist_data_reindexed, aligned_current_price_row])


                            current_z_score_series = signal_generator.calculate_zscore(temp_hist_data_with_current['close'])
                            if current_z_score_series is not None and not current_z_score_series.empty and not pd.isna(current_z_score_series.iloc[-1]):
                                current_z_score = current_z_score_series.iloc[-1]
                                logger.log_action(f"Position Manager: Z-score for {ticker} ({position_type}) is {current_z_score:.2f} (exit eval).")
                                signal = signal_generator.generate_signals(ticker, position_type, current_z_score=current_z_score)

                                if position_type == 'long' and signal in ["EXIT_LONG", "STOP_LOSS_LONG"]:
                                    exit_reason = f"{signal.lower()}_signal"
                                elif position_type == 'short' and signal in ["EXIT_SHORT", "STOP_LOSS_SHORT"]:
                                    exit_reason = f"{signal.lower()}_signal"

                                if exit_reason:
                                    logger.log_action(f"Position Manager: Signal '{signal}' triggered exit for {ticker} ({position_type}). Reason: {exit_reason}")
                            else:
                                logger.log_action(f"Position Manager: Could not calculate current z-score for {ticker} (exit eval).")
                    except Exception as e:
                        logger.log_action(f"Position Manager: Error during z-score/signal gen for {ticker} exit: {e}")

        # 3. Place Exit Order if reason determined
        if exit_reason:
            side_to_close = 'sell' if position_type == 'long' else 'buy'
            already_existing_exit_order = None
            if ticker in alpaca_open_orders_map:
                for order in alpaca_open_orders_map[ticker]:
                    if order.side == side_to_close and abs(float(order.qty) - qty_to_close) < 0.001 :
                        logger.log_action(f"Position Manager: Found existing Alpaca order {order.id} for {ticker} matching exit side '{side_to_close}' and quantity.")
                        already_existing_exit_order = order
                        break

            if already_existing_exit_order:
                logger.log_action(f"Position Manager: Using existing Alpaca order {already_existing_exit_order.id} for {ticker} exit.")
                if positions_to_manage[ticker].get('pending_exit_order_id') != already_existing_exit_order.id:
                    positions_to_manage[ticker]['status'] = 'pending_exit'
                    positions_to_manage[ticker]['pending_exit_order_id'] = already_existing_exit_order.id
                    submitted_at_dt = datetime.now()
                    if hasattr(already_existing_exit_order, 'submitted_at') and already_existing_exit_order.submitted_at:
                        try:
                            submitted_at_val = already_existing_exit_order.submitted_at
                            if isinstance(submitted_at_val, str):
                                submitted_at_dt = pd.to_datetime(submitted_at_val).to_pydatetime()
                            elif isinstance(submitted_at_val, datetime): # Handle if it's already datetime
                                submitted_at_dt = submitted_at_val
                            # Alpaca SDK might use pendulum, convert if so
                            elif hasattr(submitted_at_val, 'strftime'): # Duck-typing for datetime-like
                                # Attempt to convert to standard datetime if it's a custom datetime-like object (e.g. pendulum)
                                submitted_at_dt = datetime.fromisoformat(submitted_at_val.isoformat()) if hasattr(submitted_at_val, 'isoformat') else submitted_at_dt
                        except Exception as e_ts:
                            logger.log_action(f"Position Manager: Could not parse/convert submitted_at for order {already_existing_exit_order.id}: {e_ts}")
                    positions_to_manage[ticker]['pending_exit_order_placed_at'] = submitted_at_dt # Store as datetime
                    positions_to_manage[ticker]['exit_reason_for_order'] = exit_reason
                    positions_updated_in_cycle = True
                exit_order_placed_this_cycle = True
            else:
                logger.log_action(f"Position Manager: Attempting {side_to_close} order for {int(qty_to_close)} {ticker} @ limit {current_price:.2f} due to: {exit_reason}")
                # Ensure qty_to_close is int for Alpaca if it expects int
                exit_order_obj = order_manager.place_limit_order(ticker, int(qty_to_close), current_price, side_to_close, api_client=api_client)

                if exit_order_obj and hasattr(exit_order_obj, 'id'):
                    logger.log_action(f"Position Manager: Exit order {exit_order_obj.id} placed for {ticker}. Status: {exit_order_obj.status}")
                    positions_to_manage[ticker]['status'] = 'pending_exit'
                    positions_to_manage[ticker]['pending_exit_order_id'] = exit_order_obj.id
                    positions_to_manage[ticker]['pending_exit_order_placed_at'] = datetime.now() # Store as datetime
                    positions_to_manage[ticker]['exit_reason_for_order'] = exit_reason
                    exit_order_placed_this_cycle = True
                    positions_updated_in_cycle = True
                else:
                    logger.log_action(f"Position Manager: Failed to place exit order for {ticker} (Reason: {exit_reason}). Will retry next run if applicable.")

        if exit_order_placed_this_cycle:
            continue

    # No save_positions(positions_to_manage) here. The calling function in trading_bot.py will handle saving the state.
    if positions_updated_in_cycle:
        logger.log_action("Position Manager: Positions dictionary was updated during management cycle.")
    else:
        logger.log_action("Position Manager: No positions were updated during management cycle.")

    return positions_to_manage # Return the (potentially modified) dictionary


def get_pdt_trade_count(lookback_days=5):
    """
    Simplified PDT counter. Real tracking is complex.
    :return: Number of day trades (currently placeholder).
    """
    # TODO: Implement robust PDT tracking (see README TODOs).
    logger.log_action("PDT check: Simplified placeholder implementation. Returns 0.")
    return 0

def get_available_cash(api_client):
    """
    Gets available buying power from Alpaca account.
    :param api_client: Initialized Alpaca API client.
    :return: Available cash as float, or 0.0 if error.
    """
    if not api_client: # Should not happen if called from trading_bot
        logger.log_action("Position Manager (get_available_cash): API client is None. Returning 0.0 cash.")
        return 0.0
    try:
        account_info = api_client.get_account()
        return float(account_info.buying_power)
    except Exception as e:
        logger.log_action(f"Error fetching account info for cash: {e}")
        return 0.0
