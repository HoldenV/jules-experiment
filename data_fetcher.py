import alpaca_trade_api as tradeapi
import pandas as pd
import config
import os
import logger  # Import logger for logging actions
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Initialize Alpaca API client
# Ensure your .env file has ALPACA_API_KEY, ALPACA_SECRET_KEY
# ALPACA_PAPER_URL is for paper trading, for live use ALPACA_LIVE_URL
# or rely on SDK defaults if not using paper.
API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
BASE_URL = "https://paper-api.alpaca.markets" if config.ALPACA_PAPER else "https://api.alpaca.markets"

if not API_KEY or not SECRET_KEY:
    logger.log_action("CRITICAL: Alpaca API Key or Secret Key not found in .env file. Data fetching will fail.")
    # You might want to raise an exception here or handle it gracefully in the main bot logic
    api = None
else:
    try:
        api = tradeapi.REST(API_KEY, SECRET_KEY, base_url=BASE_URL)
        account = api.get_account() # Test connection
        logger.log_action(f"Successfully connected to Alpaca API. Account Status: {account.status}")
    except Exception as e:
        logger.log_action(f"ERROR: Could not connect to Alpaca API: {e}")
        api = None


def get_historical_data(tickers, timeframe='1Day', limit_per_ticker=200):
    """
    Fetches historical bar data for a list of tickers.
    Alpaca's get_bars can fetch for multiple symbols, but returns a flat list.
    For easier use (e.g. group by symbol), we'll fetch one by one or process the result.
    The SDK's `get_bars().df` method returns a multi-indexed DataFrame if multiple symbols are passed.

    :param tickers: List of stock tickers.
    :param timeframe: Alpaca API timeframe (e.g., '1Day', '1Hour', '1Min').
                      See Alpaca docs for tradeapi.TimeFrame enum or string options.
    :param limit_per_ticker: Number of bars to fetch per ticker. Max is 1000 for free, 10000 for paid.
    :return: Pandas DataFrame with MultiIndex (symbol, timestamp) containing OHLCV data,
             or an empty DataFrame if an error occurs or no data.
    """
    if not api:
        logger.log_action("Data Fetcher: Alpaca API client not initialized.")
        return pd.DataFrame()
    if not tickers:
        logger.log_action("Data Fetcher: No tickers provided for historical data.")
        return pd.DataFrame()

    try:
        logger.log_action(f"Fetching historical data for {len(tickers)} tickers: {', '.join(tickers)}. Timeframe: {timeframe}, Limit: {limit_per_ticker}")
        # The get_bars method can take a list of symbols.
        # The .df attribute will structure it into a multi-index DataFrame.
        bars_df = api.get_bars(tickers, timeframe, limit=limit_per_ticker).df

        if bars_df.empty:
            logger.log_action(f"No historical data returned for tickers: {', '.join(tickers)}")
            return pd.DataFrame()

        # Ensure the DataFrame has the 'symbol' column if it's not in the index already
        # For get_bars().df, 'symbol' is usually the first level of the MultiIndex.
        # If it's a single ticker, it might not have 'symbol' index level.
        if 'symbol' not in bars_df.index.names:
            if len(tickers) == 1 and not bars_df.empty:
                # If single ticker and df not empty, add symbol index level
                bars_df['symbol'] = tickers[0]
                bars_df = bars_df.set_index('symbol', append=True).reorder_levels(['symbol', 'timestamp'])
            else:
                logger.log_action("Warning: 'symbol' not in index and multiple tickers requested. DataFrame might be malformed.")

        logger.log_action(f"Successfully fetched historical data for {len(bars_df.index.get_level_values('symbol').unique())} tickers.")
        return bars_df

    except Exception as e:
        logger.log_action(f"Error fetching historical data for {tickers}: {e}")
        return pd.DataFrame()


def get_latest_prices(tickers):
    """
    Fetches the latest trade price for a list of tickers.
    :param tickers: List of stock tickers.
    :return: Dictionary {ticker: price} or empty {} if error.
    """
    if not api:
        logger.log_action("Data Fetcher: Alpaca API client not initialized.")
        return {}
    if not tickers:
        logger.log_action("Data Fetcher: No tickers provided for latest prices.")
        return {}

    latest_prices = {}
    try:
        # get_latest_trades is suitable for this. It returns a dict {symbol: trade_object}
        trades = api.get_latest_trades(tickers)
        for ticker in tickers:
            if ticker in trades and hasattr(trades[ticker], 'p'): # 'p' is price in trade object
                latest_prices[ticker] = trades[ticker].p
            else:
                logger.log_action(f"Could not get latest price for {ticker}. It might not be in trade response or trade object is malformed.")
        logger.log_action(f"Fetched latest prices for {len(latest_prices)}/{len(tickers)} tickers.")
        return latest_prices
    except Exception as e:
        logger.log_action(f"Error fetching latest prices for {tickers}: {e}")
        return {}

if __name__ == '__main__':
    # Example usage:
    # Ensure your .env file is populated with API keys for this to run.
    # Also, ensure logger.py and config.py are in the same directory or accessible.

    # Create a dummy logger.py if it doesn't exist for standalone testing
    if not os.path.exists("logger.py"):
        with open("logger.py", "w") as f:
            f.write("def log_action(message): print(f'DUMMY_LOG: {message}')\n")
            f.write("def record_trade(*args, **kwargs): print(f'DUMMY_TRADE: {args} {kwargs}')\n")
        import logger # re-import after creation

    if not api:
        print("Skipping data_fetcher examples as Alpaca API client is not initialized (check API keys in .env and messages in bot.log).")
    else:
        print(f"Using Alpaca API URL: {api._base_url}")

        # Test historical data fetching
        sample_tickers_hist = config.TICKERS[:2] # e.g., ["AAPL", "MSFT"]
        if sample_tickers_hist:
            print(f"\nFetching historical data for {sample_tickers_hist}...")
            historical_data = get_historical_data(sample_tickers_hist, timeframe='1Day', limit_per_ticker=5)
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
        sample_tickers_latest = config.TICKERS[2:5] # e.g., ["GOOG", "AMZN", "TSLA"]
        if sample_tickers_latest:
            print(f"\nFetching latest prices for {sample_tickers_latest}...")
            latest_prices_data = get_latest_prices(sample_tickers_latest)
            if latest_prices_data:
                print("Latest Prices:")
                for ticker, price in latest_prices_data.items():
                    print(f"{ticker}: {price}")
            else:
                print(f"No latest prices returned for {sample_tickers_latest}.")
        else:
            print("No tickers found in config.TICKERS to test latest price fetching.")

        # Test with an invalid ticker to see error handling
        print("\nFetching data for an invalid ticker (e.g., 'INVALIDTICKER')...")
        invalid_hist_data = get_historical_data(["INVALIDTICKERXYZ"], timeframe='1Day', limit_per_ticker=5)
        if invalid_hist_data.empty:
            print("Correctly returned empty DataFrame for invalid ticker historical data.")
        else:
            print("Unexpectedly received data for invalid ticker:", invalid_hist_data)

        invalid_latest_price = get_latest_prices(["INVALIDTICKERXYZ"])
        if not invalid_latest_price: # Empty dict
            print("Correctly returned empty dict for invalid ticker latest price.")
        else:
            print("Unexpectedly received price for invalid ticker:", invalid_latest_price)
