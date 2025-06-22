import alpaca_trade_api as tradeapi
import config
import logger
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Store the module-level API client, initialized on first need if not provided.
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
        logger.log_action("CRITICAL: Order Manager - Alpaca API Key or Secret Key not found in .env file. Cannot initialize module client.")
        return None
    try:
        client = tradeapi.REST(api_key_env, secret_key_env, base_url=base_url_env)
        # Test connection by fetching account info or a light-weight call
        client.get_account()
        logger.log_action(f"Order Manager: Successfully initialized and connected to Alpaca API at {base_url_env}.")
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
    :param api_client: Optional initialized Alpaca API client. If None, uses module's client.
    :return: Order object from Alpaca, or None if error.
    """
    current_api_client = api_client if api_client else _initialize_api_client()
    if not current_api_client:
        logger.log_action("Order Manager (place_limit_order): Alpaca API client not available. Cannot place order.")
        return None
    try:
        order = current_api_client.submit_order(
            symbol=ticker,
            qty=qty,
            side=side,
            type='limit',
            time_in_force='day',  # Good till day's close, as per common practice
            limit_price=str(round(float(limit_price), 2)) # Ensure price is string and rounded
        )
        logger.log_action(f"Placed {side} limit order for {qty} {ticker} @ {limit_price}. Order ID: {order.id}, Status: {order.status}")
        return order
    except tradeapi.rest.APIError as e:
        logger.log_action(f"Alpaca API Error placing {side} order for {qty} {ticker} @ {limit_price}: {e}")
        # Log specific details from the exception if available
        # For example, e.response.json() might give more context if it's an HTTP error
        logger.log_action(f"Error details: {e._error}") # _error often contains the message from Alpaca
        return None
    except Exception as e:
        logger.log_action(f"Order Manager: Generic error placing {side} order for {qty} {ticker} @ {limit_price}: {e}")
        return None

def get_order_status(order_id, api_client=None):
    """
    Checks the status of an order.
    :param order_id: The ID of the order to check.
    :param api_client: Optional initialized Alpaca API client. If None, uses module's client.
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
        if e.code == 404: # HTTP 404 Not Found
             logger.log_action(f"Order {order_id} not found.")
        return None # Or re-raise depending on desired error handling
    except Exception as e:
        logger.log_action(f"Order Manager: Generic error getting status for order {order_id}: {e}")
        return None

def cancel_order(order_id, api_client=None):
    """
    Cancels an open order.
    :param order_id: The ID of the order to cancel.
    :param api_client: Optional initialized Alpaca API client. If None, uses module's client.
    :return: True if cancellation was successful (or order already uncancelable), False otherwise.
    """
    current_api_client = api_client if api_client else _initialize_api_client()
    if not current_api_client:
        logger.log_action("Order Manager (cancel_order): Alpaca API client not available. Cannot cancel order.")
        return False
    try:
        # First, get the order to check its status, using the same api_client
        order_to_cancel = get_order_status(order_id, api_client=current_api_client)
        if not order_to_cancel: # If order not found or error fetching status
            logger.log_action(f"Order Manager: Cannot cancel order {order_id} as its status could not be retrieved or it does not exist.")
            return False

        if order_to_cancel.status in ['filled', 'canceled', 'expired', 'rejected', 'done_for_day']:
            logger.log_action(f"Order Manager: Order {order_id} is already in status '{order_to_cancel.status}', no cancellation needed/possible.")
            return True # Considered "successful" as the order is no longer open for cancellation

        current_api_client.cancel_order(order_id)
        logger.log_action(f"Order Manager: Successfully requested cancellation for order {order_id}.")
        return True
    except tradeapi.rest.APIError as e:
        # Alpaca might return 422 if order cannot be canceled (e.g. already filled)
        # or 404 if order_id is wrong
        logger.log_action(f"Alpaca API Error cancelling order {order_id}: {e} (Code: {e.code})")
        if e.code == 404:
            logger.log_action(f"Order {order_id} not found for cancellation.")
        elif e.code == 422: # Unprocessable Entity - often means order is not cancelable
            logger.log_action(f"Order Manager: Order {order_id} could not be cancelled (likely already filled or otherwise finalized).")
            # Check status again to confirm, using the same api_client
            updated_order = get_order_status(order_id, api_client=current_api_client)
            if updated_order and updated_order.status in ['filled', 'canceled', 'expired']:
                return True # Effectively, it's no longer open.
        return False
    except Exception as e:
        logger.log_action(f"Order Manager: Generic error cancelling order {order_id}: {e}")
        return False

if __name__ == '__main__':
    # Example usage:
    # IMPORTANT: These tests will interact with your Alpaca Paper Trading account if API keys are valid.
    # Ensure you understand the implications. Orders will be placed and potentially filled.

    # Create a dummy logger.py if it doesn't exist for standalone testing
    if not os.path.exists("logger.py"):
        with open("logger.py", "w") as f:
            f.write("def log_action(message): print(f'DUMMY_LOG: {message}')\n")
            f.write("def record_trade(*args, **kwargs): print(f'DUMMY_TRADE: {args} {kwargs}')\n")
        # Must re-import or reload logger if it was created dynamically
        import importlib
        import logger as logger_module
        importlib.reload(logger_module)

    # For __main__ testing, explicitly initialize and use the module's client.
    test_api_client = _initialize_api_client()

    if not test_api_client:
        print("Skipping order_manager examples as Alpaca API client could not be initialized (check API keys in .env and log messages).")
    else:
        print(f"Order Manager: Using Alpaca API URL: {test_api_client._base_url}")

        # Test placing a limit order (use a low-priced stock for paper trading, small qty)
        # Ensure the stock is tradable and market is open or order will be rejected/queued.
        # For testing, it's better to use a symbol that is unlikely to fill immediately
        # or a price that is far from the current market price.
        test_ticker = "SNAP" # Example, pick a stock you are okay trading in paper
        test_qty = 1
        # Place a buy order far below market or sell order far above market to avoid instant fill
        # For this test, let's assume current SNAP price is around $10-$15.
        # A buy limit at $1.00 is unlikely to fill.
        buy_limit_price = 1.00

        print(f"\nAttempting to place a BUY limit order for {test_qty} {test_ticker} @ ${buy_limit_price}...")
        # Pass the test_api_client for standalone testing
        buy_order = place_limit_order(test_ticker, test_qty, buy_limit_price, "buy", api_client=test_api_client)

        order_id_to_check = None
        if buy_order and hasattr(buy_order, 'id'):
            print(f"BUY Order placed: ID {buy_order.id}, Status {buy_order.status}")
            order_id_to_check = buy_order.id

            # Test getting order status
            if order_id_to_check:
                print(f"\nChecking status for order {order_id_to_check}...")
                status = get_order_status(order_id_to_check, api_client=test_api_client)
                if status:
                    print(f"Order {status.id} current status: {status.status}")

            # Test cancelling the order (if it's still open)
            if order_id_to_check:
                print(f"\nAttempting to cancel order {order_id_to_check}...")
                cancel_success = cancel_order(order_id_to_check, api_client=test_api_client)
                if cancel_success:
                    print(f"Cancellation request for order {order_id_to_check} processed.")
                    final_status = get_order_status(order_id_to_check, api_client=test_api_client)
                    if final_status:
                         print(f"Order {final_status.id} status after cancellation attempt: {final_status.status} (expected: canceled or similar)")
                else:
                    print(f"Failed to cancel order {order_id_to_check} or it was already finalized.")
        else:
            print(f"Failed to place BUY order for {test_ticker} or order object was None.")
            # Assuming logger is available and configured for __main__ scenario
            if 'logger' in globals():
                 logger.log_action(f"Test Case: Failed to place BUY order for {test_ticker}. Check logs for API errors.")

        # Test with an invalid order ID
        print("\nAttempting to get status for an invalid order ID (e.g., 'invalid-id')...")
        invalid_status = get_order_status("invalid-order-id-123", api_client=test_api_client)
        if invalid_status is None:
            print("Correctly returned None for invalid order ID status check.")
        else:
            print(f"Unexpectedly received status for invalid order ID: {invalid_status}")

        print("\nAttempting to cancel an invalid order ID...")
        invalid_cancel = cancel_order("invalid-order-id-123", api_client=test_api_client)
        if not invalid_cancel:
            print("Correctly failed to cancel invalid order ID.")
        else:
            print("Unexpectedly succeeded in cancelling an invalid order ID.")

        print("\nOrder manager tests finished. Check main bot log (if configured) or console for detailed logs.")
