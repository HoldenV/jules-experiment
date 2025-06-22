import json
import os
from datetime import datetime, timedelta
import pandas as pd
import config
import logger
import order_manager
import signal_generator

# Alpaca API client is passed to functions needing it from trading_bot.py

def load_positions():
    """
    Loads current open positions from JSON, converting date strings to datetime objects.
    :return: Dict of open positions {ticker: {details}}, or {} if error/not found.
    """
    os.makedirs(os.path.dirname(config.POSITIONS_FILE), exist_ok=True)

    if not os.path.exists(config.POSITIONS_FILE):
        return {}
    try:
        with open(config.POSITIONS_FILE, 'r') as f:
            positions_raw = json.load(f)

        for ticker, details in positions_raw.items():
            if 'entry_date' in details and isinstance(details['entry_date'], str):
                details['entry_date'] = datetime.fromisoformat(details['entry_date'])
            if 'pending_exit_order_placed_at' in details and isinstance(details['pending_exit_order_placed_at'], str):
                details['pending_exit_order_placed_at'] = datetime.fromisoformat(details['pending_exit_order_placed_at'])
        return positions_raw
    except Exception as e:
        logger.log_action(f"Error loading positions from {config.POSITIONS_FILE}: {e}")
        return {}

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
    :param entry_date: Defaults to now if None.
    """
    positions = load_positions()
    if entry_date is None:
        entry_date = datetime.now()

    positions[ticker] = {
        "qty": qty,
        "entry_price": entry_price,
        "entry_date": entry_date,
        "type": position_type,       # "long" or "short"
        "status": "open",
        "entry_order_id": entry_order_id,
        "pnl": 0.0,
        "pending_exit_order_id": None,
        "pending_exit_order_placed_at": None,
        "exit_reason_for_order": None
    }
    save_positions(positions)
    logger.log_action(f"Position Manager: Added new 'open' position: {qty} {ticker} @ {entry_price} ({position_type}) on {entry_date.strftime('%Y-%m-%d')}. Entry Order ID: {entry_order_id}")

def remove_position(ticker, exit_price, exit_reason, exit_order_id=None):
    """
    Removes a position and records the trade.
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
        log_message = f"Position Manager: Closed position for {ticker}. Exit: {exit_price}, Reason: {exit_reason}, P&L: {profit_loss:.2f}."
        if exit_order_id:
            log_message += f" Exit Order ID: {exit_order_id}."
        logger.log_action(log_message)
    else:
        logger.log_action(f"Position Manager: Attempted to remove non-existent position for {ticker}.")

def check_and_manage_open_positions(current_prices, all_historical_data, api_client, alpaca_open_orders_map=None):
    """
    Manages open positions: checks max hold, stop-loss, exit signals.
    :param all_historical_data: Dict {ticker: pd.DataFrame} for signal re-evaluation.
    :param alpaca_open_orders_map: Optional Dict {ticker: [AlpacaOrder]} from Alpaca.
    """
    positions = load_positions()
    if not positions:
        logger.log_action("Position Manager: No open positions to manage.")
        return

    alpaca_open_orders_map = alpaca_open_orders_map if alpaca_open_orders_map is not None else {}
    logger.log_action("Position Manager: Checking and managing open positions...")
    today = datetime.now()
    positions_updated = False

    for ticker, details in list(positions.items()): # Iterate copy for safe modification
        # Only manage 'open' positions here. 'pending_exit' is handled by trading_bot.py reconciliation.
        if details.get('status') != 'open':
            logger.log_action(f"Position Manager: Skipping {ticker}, status '{details.get('status', 'unknown')}' (not 'open').")
            continue

        current_price = current_prices.get(ticker)
        if current_price is None or not isinstance(current_price, (int, float)) or current_price <= 0:
            logger.log_action(f"Position Manager: Invalid/missing current price for open position {ticker} ({current_price}). Skipping.")
            continue

        qty_to_close = details['qty']
        position_type = details['type']
        exit_order_placed = False
        exit_reason = None

        # 1. Check Max Holding Period
        entry_date = details['entry_date']
        if isinstance(entry_date, str): entry_date = datetime.fromisoformat(entry_date)

        if (today - entry_date).days >= config.MAX_HOLDING_PERIOD_DAYS:
            logger.log_action(f"Position Manager: {ticker} ({position_type}) hit max hold ({config.MAX_HOLDING_PERIOD_DAYS} days).")
            exit_reason = f"max_hold_{config.MAX_HOLDING_PERIOD_DAYS}_days"

        # 2. Check Z-Score Based Exit/Stop-Loss (if not already exiting due to max hold)
        if not exit_reason:
            ticker_hist_data_for_z = all_historical_data.get(ticker)
            if ticker_hist_data_for_z is None or ticker_hist_data_for_z.empty:
                logger.log_action(f"Position Manager: No historical data for {ticker} to re-eval z-score for exit.")
            else:
                # TODO: Review robustness of appending current price (see README TODOs)
                temp_hist_data = ticker_hist_data_for_z.copy()
                if 'close' not in temp_hist_data.columns:
                    logger.log_action(f"Position Manager: 'close' column missing in hist data for {ticker} (exit eval).")
                else:
                    try:
                        if isinstance(temp_hist_data.index, pd.DatetimeIndex):
                            current_price_row = pd.DataFrame({'close': [current_price]}, index=[pd.Timestamp.now(tz=temp_hist_data.index.tz)])
                            for col in temp_hist_data.columns: # Ensure column match for concat
                                if col not in current_price_row.columns: current_price_row[col] = pd.NA
                            temp_hist_data_with_current = pd.concat([temp_hist_data, current_price_row[['close']]])
                        else:
                            logger.log_action(f"Position Manager: Hist data for {ticker} non-DatetimeIndex. Appending current price may be inexact.")
                            last_idx = temp_hist_data.index[-1]
                            new_idx = last_idx + pd.Timedelta(days=1) if isinstance(last_idx, pd.Timestamp) else (temp_hist_data.index.max() + 1 if pd.api.types.is_numeric_dtype(temp_hist_data.index) else len(temp_hist_data))
                            current_price_row = pd.DataFrame({'close': [current_price]}, index=[new_idx])
                            temp_hist_data_with_current = pd.concat([temp_hist_data, current_price_row])

                        current_z_score_series = signal_generator.calculate_zscore(temp_hist_data_with_current['close'])
                        if current_z_score_series is not None and not current_z_score_series.empty and not pd.isna(current_z_score_series.iloc[-1]):
                            current_z_score = current_z_score_series.iloc[-1]
                            logger.log_action(f"Position Manager: Z-score for {ticker} ({position_type}) is {current_z_score:.2f} (exit eval).")
                            signal = signal_generator.generate_signals(ticker, None, current_z_score=current_z_score)

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
                    if order.side == side_to_close:
                        logger.log_action(f"Position Manager: Found existing Alpaca order {order.id} for {ticker} matching exit side '{side_to_close}'.")
                        already_existing_exit_order = order
                        break

            if already_existing_exit_order:
                logger.log_action(f"Position Manager: Using existing Alpaca order {already_existing_exit_order.id} for {ticker} exit.")
                if positions[ticker].get('pending_exit_order_id') != already_existing_exit_order.id:
                    positions[ticker]['status'] = 'pending_exit'
                    positions[ticker]['pending_exit_order_id'] = already_existing_exit_order.id
                    submitted_at_iso = datetime.now().isoformat()
                    if hasattr(already_existing_exit_order, 'submitted_at') and already_existing_exit_order.submitted_at:
                        try:
                            submitted_at_iso = pd.to_datetime(already_existing_exit_order.submitted_at).isoformat()
                        except Exception as e_ts:
                            logger.log_action(f"Position Manager: Could not format submitted_at for order {already_existing_exit_order.id}: {e_ts}")
                    positions[ticker]['pending_exit_order_placed_at'] = submitted_at_iso
                    positions[ticker]['exit_reason_for_order'] = exit_reason
                    positions_updated = True
                exit_order_placed = True
            else:
                logger.log_action(f"Position Manager: Attempting {side_to_close} order for {qty_to_close} {ticker} @ {current_price} due to: {exit_reason}")
                exit_order_obj = order_manager.place_limit_order(ticker, qty_to_close, current_price, side_to_close, api_client=api_client)

                if exit_order_obj and hasattr(exit_order_obj, 'id'):
                    logger.log_action(f"Position Manager: Exit order {exit_order_obj.id} placed for {ticker}. Status: {exit_order_obj.status}")
                    positions[ticker]['status'] = 'pending_exit'
                    positions[ticker]['pending_exit_order_id'] = exit_order_obj.id
                    positions[ticker]['pending_exit_order_placed_at'] = datetime.now().isoformat()
                    positions[ticker]['exit_reason_for_order'] = exit_reason
                    exit_order_placed = True
                    positions_updated = True
                else:
                    logger.log_action(f"Position Manager: Failed to place exit order for {ticker} (Reason: {exit_reason}). Will retry next run.")

        if exit_order_placed:
            continue

    if positions_updated:
        logger.log_action("Position Manager: Saving updated positions after management cycle.")
        save_positions(positions)
    else:
        logger.log_action("Position Manager: No positions updated in this management cycle.")


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
