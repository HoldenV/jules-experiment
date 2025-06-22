import alpaca_trade_api as tradeapi
import config
import logger
import os
from dotenv import load_dotenv

load_dotenv()

_module_api_client = None

def _initialize_api_client():
    """Initializes and returns a module-level Alpaca API client."""
    global _module_api_client
    if _module_api_client:
        return _module_api_client

    api_key_env = os.getenv("ALPACA_API_KEY")
    secret_key_env = os.getenv("ALPACA_SECRET_KEY")
    base_url_env = "https://paper-api.alpaca.markets" if config.ALPACA_PAPER else "https://api.alpaca.markets"

    if not api_key_env or not secret_key_env:
        logger.log_action("CRITICAL: Order Manager - Alpaca API Key or Secret Key not found in .env file.")
        return None
    try:
        client = tradeapi.REST(api_key_env, secret_key_env, base_url=base_url_env)
        client.get_account() # Test connection
        logger.log_action(f"Order Manager: Successfully initialized Alpaca API at {base_url_env}.")
        _module_api_client = client
        return _module_api_client
    except Exception as e:
        logger.log_action(f"ERROR: Order Manager - Could not connect to Alpaca API: {e}")
        return None

def place_limit_order(ticker, qty, limit_price, side, api_client=None):
    """
    Places a limit order.
    :param ticker: Stock ticker.
    :param qty: Quantity of shares.
    :param limit_price: Limit price for the order.
    :param side: 'buy' or 'sell'.
    :param api_client: Optional initialized Alpaca API client.
    :return: Order object from Alpaca, or None if error.
    """
    current_api_client = api_client if api_client else _initialize_api_client()
    if not current_api_client:
        logger.log_action("Order Manager (place_limit_order): Alpaca API client not available.")
        return None
    try:
        order = current_api_client.submit_order(
            symbol=ticker,
            qty=qty,
            side=side,
            type='limit',
            time_in_force='day',
            limit_price=str(round(float(limit_price), 2))
        )
        logger.log_action(f"Placed {side} limit order for {qty} {ticker} @ {limit_price}. Order ID: {order.id}, Status: {order.status}")
        return order
    except tradeapi.rest.APIError as e:
        # Alpaca APIError often has a ._error attribute with the message from Alpaca
        logger.log_action(f"Alpaca API Error placing {side} order for {qty} {ticker} @ {limit_price}: {e}. Details: {getattr(e, '_error', 'N/A')}")
        return None
    except Exception as e:
        logger.log_action(f"Order Manager: Generic error placing {side} order for {qty} {ticker} @ {limit_price}: {e}")
        return None

def get_order_status(order_id, api_client=None):
    """
    Checks the status of an order.
    :param order_id: The ID of the order to check.
    :param api_client: Optional initialized Alpaca API client.
    :return: Order object from Alpaca, or None if error.
    """
    current_api_client = api_client if api_client else _initialize_api_client()
    if not current_api_client:
        logger.log_action("Order Manager (get_order_status): Alpaca API client not available.")
        return None
    try:
        order = current_api_client.get_order(order_id)
        logger.log_action(f"Order Manager: Checked status for order ID {order_id}: Status {order.status}")
        return order
    except tradeapi.rest.APIError as e:
        logger.log_action(f"Alpaca API Error getting status for order {order_id}: {e}")
        if e.code == 404:
             logger.log_action(f"Order {order_id} not found.")
        return None
    except Exception as e:
        logger.log_action(f"Order Manager: Generic error getting status for order {order_id}: {e}")
        return None

def cancel_order(order_id, api_client=None):
    """
    Cancels an open order.
    :param order_id: The ID of the order to cancel.
    :param api_client: Optional initialized Alpaca API client.
    :return: True if cancellation was successful or order already uncancelable, False otherwise.
    """
    current_api_client = api_client if api_client else _initialize_api_client()
    if not current_api_client:
        logger.log_action("Order Manager (cancel_order): Alpaca API client not available.")
        return False
    try:
        order_to_cancel = get_order_status(order_id, api_client=current_api_client)
        if not order_to_cancel:
            logger.log_action(f"Order Manager: Cannot cancel order {order_id}, status unknown or DNE.")
            return False

        if order_to_cancel.status in ['filled', 'canceled', 'expired', 'rejected', 'done_for_day']:
            logger.log_action(f"Order Manager: Order {order_id} status '{order_to_cancel.status}', no cancellation needed/possible.")
            return True

        current_api_client.cancel_order(order_id)
        logger.log_action(f"Order Manager: Successfully requested cancellation for order {order_id}.")
        return True
    except tradeapi.rest.APIError as e:
        logger.log_action(f"Alpaca API Error cancelling order {order_id}: {e} (Code: {e.code})")
        if e.code == 404:
            logger.log_action(f"Order {order_id} not found for cancellation.")
        elif e.code == 422: # Unprocessable Entity (e.g., order already filled)
            logger.log_action(f"Order Manager: Order {order_id} could not be cancelled (likely finalized).")
            updated_order = get_order_status(order_id, api_client=current_api_client)
            if updated_order and updated_order.status in ['filled', 'canceled', 'expired']:
                return True # Effectively, no longer open.
        return False
    except Exception as e:
        logger.log_action(f"Order Manager: Generic error cancelling order {order_id}: {e}")
        return False

def get_open_orders(api_client=None, tickers: list[str] = None):
    """
    Retrieves all open orders, optionally filtered by a list of tickers.
    :param api_client: Optional initialized Alpaca API client.
    :param tickers: Optional list of stock tickers to filter by.
    :return: List of Order objects from Alpaca, or an empty list if error/no open orders.
    """
    current_api_client = api_client if api_client else _initialize_api_client()
    if not current_api_client:
        logger.log_action("Order Manager (get_open_orders): Alpaca API client not available.")
        return []

    try:
        params = {'status': 'open'}
        if tickers:
            params['symbols'] = tickers

        open_orders = current_api_client.list_orders(**params)
        count = len(open_orders)
        logger.log_action(f"Order Manager: Found {count} open order(s) for {', '.join(tickers) if tickers else 'all symbols'}.")
        return open_orders
    except tradeapi.rest.APIError as e:
        logger.log_action(f"Alpaca API Error getting open orders: {e}")
        return []
    except Exception as e:
        logger.log_action(f"Order Manager: Generic error getting open orders: {e}")
        return []
