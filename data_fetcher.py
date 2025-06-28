import alpaca_trade_api as tradeapi
import pandas as pd
import config
import os
import logger
from dotenv import load_dotenv
from datetime import datetime, timedelta
import pandas_market_calendars as mcal

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

def get_historical_data(tickers, timeframe='1Day', trading_days_limit=200, api_client=None):
    """
    Fetches historical bar data for multiple tickers for a specified number of trading days.
    The Alpaca SDK's `get_bars().df` method returns a multi-indexed DataFrame.

    :param tickers: List of stock tickers.
    :param timeframe: Alpaca API timeframe (e.g., '1Day', '1Hour').
    :param trading_days_limit: Number of trading days of data to fetch.
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
        nyse = mcal.get_calendar('NYSE')

        # Determine the end_date: the last valid trading day.
        # We want data up to the close of the most recent trading day.
        # Alpaca's end_date is inclusive for daily bars.
        now_utc = pd.Timestamp('now', tz='UTC')
        schedule_today = nyse.schedule(start_date=now_utc.date() - timedelta(days=5), end_date=now_utc.date()) # Check last few days

        if not schedule_today.empty and now_utc >= schedule_today.iloc[-1]['market_close']:
            # If market is closed for today (or it's past market close), last trading day is today
            end_date_dt = schedule_today.index[-1].date()
        elif not schedule_today.empty and now_utc < schedule_today.iloc[-1]['market_open']:
            # If market is not yet open today, last trading day is previous valid day
            # or if today is not a trading day at all.
             all_valid_days = nyse.valid_days(start_date=now_utc.date() - timedelta(days=10), end_date=now_utc.date() - timedelta(days=1))
             if not all_valid_days.empty:
                end_date_dt = all_valid_days[-1].date()
             else: # Should not happen in normal circumstances
                logger.log_action("Data Fetcher: Could not determine a valid recent end_date_dt.")
                return pd.DataFrame()
        else: # Market is currently open, or some other edge case
             all_valid_days = nyse.valid_days(start_date=now_utc.date() - timedelta(days=10), end_date=now_utc.date() - timedelta(days=1))
             if not all_valid_days.empty:
                end_date_dt = all_valid_days[-1].date()
             else:
                logger.log_action("Data Fetcher: Could not determine a valid recent end_date_dt (market likely open).")
                # Fallback to yesterday if all else fails, Alpaca will handle no data if it's not a trading day
                end_date_dt = (now_utc - timedelta(days=1)).date()


        # Determine the start_date: 'trading_days_limit' trading days before end_date_dt.
        # We need a wider window for valid_days to ensure we can find enough past trading days.
        # Add a buffer (e.g., *1.5) to account for weekends/holidays in the initial date range.
        potential_start_range_days = int(trading_days_limit * 1.7) + 30 # Generous buffer
        earliest_possible_start = end_date_dt - timedelta(days=potential_start_range_days)

        trading_schedule = nyse.schedule(start_date=earliest_possible_start, end_date=end_date_dt)

        if len(trading_schedule) < trading_days_limit:
            logger.log_action(f"Data Fetcher: Not enough trading days in the looked-up schedule to satisfy trading_days_limit={trading_days_limit}. Found {len(trading_schedule)} days. Adjusting limit or date range may be needed.")
            # Fallback: use all available data if not enough days, or adjust start_date to earliest possible
            if trading_schedule.empty:
                 logger.log_action(f"Data Fetcher: No trading days found up to {end_date_dt}. Cannot fetch data.")
                 return pd.DataFrame()
            start_date_dt = trading_schedule.index[0].date()
            actual_limit = len(trading_schedule)
        else:
            start_date_dt = trading_schedule.index[-trading_days_limit].date()
            actual_limit = trading_days_limit

        # Convert to ISO format strings for Alpaca API
        start_date_iso = start_date_dt.isoformat()
        end_date_iso = end_date_dt.isoformat()

        logger.log_action(f"Data Fetcher: Fetching historical data for {len(tickers)} tickers: {', '.join(tickers)}. Timeframe: {timeframe}, Trading Days: {actual_limit}, Start: {start_date_iso}, End: {end_date_iso}")

        bars_df = current_api_client.get_bars(
            tickers,
            timeframe,
            start=start_date_iso,
            end=end_date_iso, # Alpaca's end is inclusive for daily bars
            limit=actual_limit # Add limit to ensure we don't get more than needed if date range is slightly off
        ).df

        # Filter results to ensure we only use data within the exact date range of trading days
        # This is important because get_bars might return data for non-trading days if they fall within start/end
        # and also to precisely trim to 'actual_limit' days per ticker.
        if not bars_df.empty:
            bars_df = bars_df.reset_index()
            # Ensure timestamp is timezone-aware (UTC, as Alpaca returns) then convert to date for filtering
            if not pd.api.types.is_datetime64_any_dtype(bars_df['timestamp']):
                 bars_df['timestamp'] = pd.to_datetime(bars_df['timestamp'])
            if bars_df['timestamp'].dt.tz is None:
                bars_df['timestamp'] = bars_df['timestamp'].dt.tz_localize('UTC')

            # Get the list of actual trading days we care about
            valid_trading_days_for_period = nyse.trading_days(start_date_dt, end_date_dt).date # Get as numpy array of dates

            # Filter the DataFrame
            bars_df['trade_date'] = bars_df['timestamp'].dt.normalize().dt.date # Extract date part for comparison
            bars_df = bars_df[bars_df['trade_date'].isin(valid_trading_days_for_period)]

            # Group by symbol and take the last 'actual_limit' days for each
            bars_df = bars_df.groupby('symbol').tail(actual_limit)

            # Drop helper column and set index
            bars_df = bars_df.drop(columns=['trade_date'])
            if 'symbol' in bars_df.columns and 'timestamp' in bars_df.columns:
                 bars_df = bars_df.set_index(['symbol', 'timestamp'])
            else: # Should not happen
                 logger.log_action("Error: 'symbol' or 'timestamp' columns missing after filtering/processing.")
                 return pd.DataFrame()

        if bars_df.empty:
            logger.log_action(f"Data Fetcher: No historical data returned for tickers: {', '.join(tickers)}")
            return pd.DataFrame()

        # Final check on index (already set if filtering occurred, but good fallback)
        if not isinstance(bars_df.index, pd.MultiIndex):
            bars_df = bars_df.reset_index()
            if 'symbol' in bars_df.columns and 'timestamp' in bars_df.columns:
                bars_df = bars_df.set_index(['symbol', 'timestamp'])
            else:
                logger.log_action("Error: 'symbol' or 'timestamp' columns missing before final return. DataFrame malformed.")
                return pd.DataFrame()

        logger.log_action(f"Data Fetcher: Successfully fetched historical data for {len(bars_df.index.get_level_values('symbol').unique())} tickers.")
        return bars_df

    except Exception as e:
        logger.log_action(f"Data Fetcher: Error fetching historical data for {tickers}: {e}")
        return pd.DataFrame()


def get_alpaca_open_positions(api_client=None):
    """
    Fetches all currently open positions from Alpaca.
    :param api_client: Optional initialized Alpaca API client.
    :return: Dictionary {ticker: AlpacaPositionObject} or empty {} if error.
    """
    current_api_client = api_client if api_client else _initialize_api_client()
    if not current_api_client:
        logger.log_action("Data Fetcher (get_alpaca_open_positions): Alpaca API client not available.")
        return {}

    try:
        alpaca_positions = current_api_client.list_positions()
        positions_map = {pos.symbol: pos for pos in alpaca_positions}
        logger.log_action(f"Data Fetcher: Successfully fetched {len(positions_map)} open positions from Alpaca.")
        return positions_map
    except Exception as e:
        logger.log_action(f"Data Fetcher: Error fetching open positions from Alpaca: {e}")
        return {}

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
