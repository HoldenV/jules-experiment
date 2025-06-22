import alpaca_trade_api as tradeapi
import pandas as pd
import config
import os
import logger
from dotenv import load_dotenv
from datetime import datetime, timedelta

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
        logger.log_action("CRITICAL: Data Fetcher - Alpaca API Key or Secret Key not found in .env file.")
        return None
    try:
        client = tradeapi.REST(api_key_env, secret_key_env, base_url=base_url_env)
        account = client.get_account()
        logger.log_action(f"Data Fetcher: Successfully initialized Alpaca API. Account Status: {account.status}")
        _module_api_client = client
        return _module_api_client
    except Exception as e:
        logger.log_action(f"ERROR: Data Fetcher - Could not connect to Alpaca API: {e}")
        return None

def get_historical_data(tickers, timeframe='1Day', limit_per_ticker=200, api_client=None):
    """
    Fetches historical bar data for multiple tickers.
    The Alpaca SDK's `get_bars().df` method returns a multi-indexed DataFrame.

    :param tickers: List of stock tickers.
    :param timeframe: Alpaca API timeframe (e.g., '1Day', '1Hour').
    :param limit_per_ticker: Number of bars to fetch per ticker.
    :param api_client: Optional initialized Alpaca API client.
    :return: Pandas DataFrame with MultiIndex (symbol, timestamp) or empty DataFrame on error.
    """
    current_api_client = api_client if api_client else _initialize_api_client()
    if not current_api_client:
        logger.log_action("Data Fetcher (get_historical_data): Alpaca API client not available.")
        return pd.DataFrame()
    if not tickers:
        logger.log_action("Data Fetcher (get_historical_data): No tickers provided.")
        return pd.DataFrame()

    try:
        # TODO: Review date calculation logic for robustness (see README TODOs)
        end_date = datetime.now().date() - timedelta(days=1)
        start_date = end_date - timedelta(days=limit_per_ticker + 5) # Buffer for non-trading days

        logger.log_action(f"Data Fetcher: Fetching historical data for {len(tickers)} tickers: {', '.join(tickers)}. Timeframe: {timeframe}, Start: {start_date.isoformat()}, End: {end_date.isoformat()}")

        bars_df = current_api_client.get_bars(
            tickers,
            timeframe,
            start=start_date.isoformat(),
            end=end_date.isoformat()
        ).df

        if bars_df.empty:
            logger.log_action(f"Data Fetcher: No historical data returned for tickers: {', '.join(tickers)}")
            return pd.DataFrame()

        # Ensure DataFrame is correctly indexed
        bars_df = bars_df.reset_index()
        if 'symbol' in bars_df.columns and 'timestamp' in bars_df.columns:
            bars_df = bars_df.set_index(['symbol', 'timestamp'])
        else:
            logger.log_action("Error: 'symbol' or 'timestamp' columns missing after reset_index. DataFrame malformed.")
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
    :param api_client: Optional initialized Alpaca API client.
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
        trades = current_api_client.get_latest_trades(tickers)
        for ticker in tickers:
            if ticker in trades and hasattr(trades[ticker], 'p'): # 'p' is price
                latest_prices[ticker] = trades[ticker].p
            else:
                logger.log_action(f"Data Fetcher: Could not get latest price for {ticker}.")
        logger.log_action(f"Data Fetcher: Fetched latest prices for {len(latest_prices)}/{len(tickers)} tickers.")
        return latest_prices
    except Exception as e:
        logger.log_action(f"Data Fetcher: Error fetching latest prices for {tickers}: {e}")
        return {}
