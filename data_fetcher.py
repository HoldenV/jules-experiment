import alpaca_trade_api as tradeapi
import pandas as pd
import config
import os
import logger  # Import logger for logging actions
from dotenv import load_dotenv
from datetime import datetime, timedelta

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
        logger.log_action("CRITICAL: Data Fetcher - Alpaca API Key or Secret Key not found in .env file. Cannot initialize module client.")
        return None
    try:
        client = tradeapi.REST(api_key_env, secret_key_env, base_url=base_url_env)
        account = client.get_account() # Test connection
        logger.log_action(f"Data Fetcher: Successfully initialized and connected to Alpaca API. Account Status: {account.status}")
        _module_api_client = client
        return _module_api_client
    except Exception as e:
        logger.log_action(f"ERROR: Data Fetcher - Could not connect to Alpaca API: {e}")
        return None

def get_historical_data(tickers, timeframe='1Day', limit_per_ticker=200, api_client=None):
    """
    Fetches historical bar data for a list of tickers.
    Alpaca's get_bars can fetch for multiple symbols, but returns a flat list.
    For easier use (e.g. group by symbol), we'll fetch one by one or process the result.
    The SDK's `get_bars().df` method returns a multi-indexed DataFrame if multiple symbols are passed.

    :param tickers: List of stock tickers.
    :param timeframe: Alpaca API timeframe (e.g., '1Day', '1Hour', '1Min').
                      See Alpaca docs for tradeapi.TimeFrame enum or string options.
    :param limit_per_ticker: Number of bars to fetch per ticker. Max is 1000 for free, 10000 for paid.
    :param api_client: Optional initialized Alpaca API client. If None, uses module's client.
    :return: Pandas DataFrame with MultiIndex (symbol, timestamp) containing OHLCV data,
             or an empty DataFrame if an error occurs or no data.
    """
    current_api_client = api_client if api_client else _initialize_api_client()
    if not current_api_client:
        logger.log_action("Data Fetcher (get_historical_data): Alpaca API client not available.")
        return pd.DataFrame()
    if not tickers:
        logger.log_action("Data Fetcher (get_historical_data): No tickers provided.")
        return pd.DataFrame()

    try:
        # Calculate the end date as the end of the previous trading day
        end_date = datetime.now().date() - timedelta(days=1)
        # Calculate the start date based on the limit
        start_date = end_date - timedelta(days=limit_per_ticker + 5) # Add a buffer just in case

        logger.log_action(f"Data Fetcher: Fetching historical data for {len(tickers)} tickers: {', '.join(tickers)}. Timeframe: {timeframe}, Start Date: {start_date.isoformat()}, End Date: {end_date.isoformat()}")
        # The get_bars method can take a list of symbols.
        # The .df attribute will structure it into a multi-index DataFrame.
        # Pass start and end dates formatted as YYYY-MM-DD strings
        bars_df = current_api_client.get_bars(tickers, timeframe, start=start_date.isoformat(), end=end_date.isoformat()).df

        if bars_df.empty:
            logger.log_action(f"Data Fetcher: No historical data returned for tickers: {', '.join(tickers)}")
            return pd.DataFrame()

        if bars_df.empty:
             logger.log_action(f"Data Fetcher: No historical data returned for tickers: {', '.join(tickers)}")
             return pd.DataFrame() # Return empty DataFrame if still empty

        # Reset index to turn index levels into columns, then set a new MultiIndex
        bars_df = bars_df.reset_index()
        if 'symbol' in bars_df.columns and 'timestamp' in bars_df.columns:
            bars_df = bars_df.set_index(['symbol', 'timestamp'])
        else:
            logger.log_action("Error: Could not find 'symbol' or 'timestamp' columns after resetting index. DataFrame might be malformed.")
            return pd.DataFrame()


        logger.log_action(f"Data Fetcher: Successfully fetched historical data for {len(bars_df.index.get_level_values('symbol').unique())} tickers.")
        return bars_df

    except Exception as e:
        logger.log_action(f"Data Fetcher: Error fetching historical data for {tickers}: {e}")
        return pd.DataFrame()


def get_latest_prices(tickers, api_client=None):
    """
    Fetches the latest trade price for a list of tickers.
    :param tickers: List of stock tickers.
    :param api_client: Optional initialized Alpaca API client. If None, uses module's client.
    :return: Dictionary {ticker: price} or empty {} if error.
    """
    current_api_client = api_client if api_client else _initialize_api_client()
    if not current_api_client:
        logger.log_action("Data Fetcher (get_latest_prices): Alpaca API client not available.")
        return {}
    if not tickers:
        logger.log_action("Data Fetcher (get_latest_prices): No tickers provided.")
        return {}

    latest_prices = {}
    try:
        # get_latest_trades is suitable for this. It returns a dict {symbol: trade_object}
        trades = current_api_client.get_latest_trades(tickers)
        for ticker in tickers:
            if ticker in trades and hasattr(trades[ticker], 'p'): # 'p' is price in trade object
                latest_prices[ticker] = trades[ticker].p
            else:
                logger.log_action(f"Data Fetcher: Could not get latest price for {ticker}. It might not be in trade response or trade object is malformed.")
        logger.log_action(f"Data Fetcher: Fetched latest prices for {len(latest_prices)}/{len(tickers)} tickers.")
        return latest_prices
    except Exception as e:
        logger.log_action(f"Data Fetcher: Error fetching latest prices for {tickers}: {e}")
        return {}

if __name__ == '__main__':
    # Ensure logger.py and config.py are accessible for standalone testing
    # This might require adjusting Python path or ensuring they are in the same directory.
    # For simplicity, assume they are.

    # Example usage:
    # Ensure your .env file is populated with API keys for this to run.
    # Also, ensure logger.py and config.py are in the same directory or accessible.

    # Create a dummy logger.py if it doesn't exist for standalone testing
    if not os.path.exists("logger.py"):
        with open("logger.py", "w") as f:
            f.write("def log_action(message): print(f'DUMMY_LOG: {message}')\n")
            f.write("def record_trade(*args, **kwargs): print(f'DUMMY_TRADE: {args} {kwargs}')\n")
        import logger # re-import after creation

    # For __main__ testing, explicitly initialize and use the module's client.
    # In real bot operation, the client might be passed from trading_bot.py.
    test_api_client = _initialize_api_client()

    if not test_api_client:
        print("Skipping data_fetcher examples as Alpaca API client could not be initialized (check API keys in .env and log messages).")
    else:
        print(f"Using Alpaca API URL: {test_api_client._base_url}")

        # Test historical data fetching (passing the client explicitly for testing)
        sample_tickers_hist = config.TICKERS[:2] if config.TICKERS else [] # e.g., ["AAPL", "MSFT"]
        if sample_tickers_hist:
            print(f"\nFetching historical data for {sample_tickers_hist}...")
            # Pass the test_api_client for standalone testing
            historical_data = get_historical_data(sample_tickers_hist, timeframe='1Day', limit_per_ticker=5, api_client=test_api_client)
            if not historical_data.empty:
                print("Historical Data:")
                print(historical_data)
                # Example: Accessing data for one ticker
                if not historical_data.empty and 'AAPL' in historical_data.index.get_level_values('symbol'):
                    print("\nAAPL Data:")
                    print(historical_data.loc['AAPL'])
            else:
                print(f"No historical data returned for {sample_tickers_hist}.")
        else:
            print("No tickers found in config.TICKERS to test historical data fetching.")

        # Test latest prices fetching
        sample_tickers_latest = config.TICKERS[2:5] if len(config.TICKERS) > 2 else [] # e.g., ["GOOG", "AMZN", "TSLA"]
        if sample_tickers_latest:
            print(f"\nFetching latest prices for {sample_tickers_latest}...")
            # Pass the test_api_client for standalone testing
            latest_prices_data = get_latest_prices(sample_tickers_latest, api_client=test_api_client)
            if latest_prices_data:
                print("Latest Prices:")
                for ticker, price in latest_prices_data.items():
                    print(f"{ticker}: {price}")
            else:
                print(f"No latest prices returned for {sample_tickers_latest}.")
        else:
            print("No tickers found in config.TICKERS to test latest price fetching.")

        # Test with an invalid ticker to see error handling
        print("\nFetching data for an invalid ticker (e.g., 'INVALIDTICKERXYZ')...")
        invalid_hist_data = get_historical_data(["INVALIDTICKERXYZ"], timeframe='1Day', limit_per_ticker=5, api_client=test_api_client)
        if invalid_hist_data.empty:
            print("Correctly returned empty DataFrame for invalid ticker historical data.")
        else:
            print("Unexpectedly received data for invalid ticker:", invalid_hist_data)

        invalid_latest_price = get_latest_prices(["INVALIDTICKERXYZ"], api_client=test_api_client)
        if not invalid_latest_price: # Empty dict
            print("Correctly returned empty dict for invalid ticker latest price.")
        else:
            print("Unexpectedly received price for invalid ticker:", invalid_latest_price)
