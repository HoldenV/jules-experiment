import pandas as pd
import numpy as np
import config
import logger # Added logger

def calculate_zscore(prices_df):
    """
    Calculates the z-score for a series of prices.
    :param prices_df: Pandas DataFrame with a 'close' column for prices, or a Pandas Series of prices.
                      Assumes index is sorted by time.
    :return: Pandas Series of z-scores, or None if input is insufficient or malformed.
    """
    if prices_df is None or len(prices_df) < config.Z_SCORE_WINDOW:
        logger.log_action(f"Z-score calculation: Insufficient data. Need {config.Z_SCORE_WINDOW} periods, got {len(prices_df) if prices_df is not None else 0}.")
        return None

    # Ensure 'close' column exists or handle Series input
    if isinstance(prices_df, pd.Series):
        prices = prices_df
    elif 'close' in prices_df.columns:
        prices = prices_df['close']
    elif len(prices_df.columns) == 1: # Assume single column is the price data
        prices = prices_df.iloc[:, 0]
    else:
        logger.log_action("Error: 'close' column not found in prices_df for z-score calculation, and not a single-column DataFrame or Series.")
        return None

    if not isinstance(prices, pd.Series):
        logger.log_action("Error: Price data for z-score calculation is not a Pandas Series after extraction.")
        return None

    moving_avg = prices.rolling(window=config.Z_SCORE_WINDOW).mean()
    rolling_std = prices.rolling(window=config.Z_SCORE_WINDOW).std()

    # Avoid division by zero if std is 0 (e.g., prices are constant)
    z_scores = (prices - moving_avg) / rolling_std.replace(0, np.nan)
    return z_scores.ffill() # Forward fill NaNs that might occur at the start or due to std=0

def generate_signals(ticker, historical_data_df, current_z_score=None):
    """
    Generates trading signals based on z-score.
    :param ticker: Stock ticker string.
    :param historical_data_df: Pandas DataFrame with historical prices (must contain 'close').
                               This is used to calculate the initial z-score if current_z_score is None.
    :param current_z_score: The most recent z-score. If None, it's calculated from historical_data_df.
    :return: Signal string ("BUY", "SELL", "HOLD_LONG", "HOLD_SHORT", "EXIT_LONG", "EXIT_SHORT", "NO_SIGNAL")
    """
    if current_z_score is None:
        if historical_data_df is None or historical_data_df.empty:
            return "NO_SIGNAL" # Not enough data
        z_scores = calculate_zscore(historical_data_df)
        if z_scores is None or z_scores.empty:
            return "NO_SIGNAL"
        current_z_score = z_scores.iloc[-1]

    if pd.isna(current_z_score):
        return "NO_SIGNAL" # Z-score could not be calculated

    # Entry signals
    if current_z_score < config.Z_ENTRY_LONG:
        return "BUY"
    if current_z_score > config.Z_ENTRY_SHORT:
        return "SELL" # Sell means short sell

    # Exit signals for existing positions (logic for knowing if a position is open is in position_manager)
    # This function just provides the signal based on z-score
    if config.Z_EXIT_LONG > current_z_score > config.Z_ENTRY_LONG : # Was long, crossed back towards zero
        return "EXIT_LONG" # Signal to close a long position
    if config.Z_EXIT_SHORT < current_z_score < config.Z_ENTRY_SHORT: # Was short, crossed back towards zero
        return "EXIT_SHORT" # Signal to close a short position

    # Stop-loss signals (can be combined with exit signals by position_manager)
    if current_z_score < config.Z_STOP_LOSS_LONG: # For an existing long position
        return "STOP_LOSS_LONG"
    if current_z_score > config.Z_STOP_LOSS_SHORT: # For an existing short position
        return "STOP_LOSS_SHORT"

    # If no specific entry/exit/stop, it's a hold or no signal
    # The distinction between HOLD_LONG, HOLD_SHORT, and NO_SIGNAL will be made by position_manager
    # based on whether a position is currently open.
    return "NO_SIGNAL" # Default if no other conditions met

if __name__ == '__main__':
    # Example usage:
    # Create dummy price data
    dummy_prices = pd.DataFrame({
        'close': np.concatenate([
            np.linspace(100, 90, 15),  # Trend down
            np.linspace(90, 80, 15),   # Trend down further (Z < -1.5)
            np.linspace(80, 85, 5),    # Reversal towards mean
            np.linspace(85, 110, 10),  # Trend up
            np.linspace(110, 125, 15), # Trend up further (Z > 1.5)
            np.linspace(125, 115, 5)   # Reversal towards mean
        ])
    }, index=pd.date_range(start='2023-01-01', periods=65, freq='B'))

    z_scores = calculate_zscore(dummy_prices)
    if z_scores is not None:
        print("Calculated Z-Scores:")
        print(z_scores.tail(10))

        print("\nSignal Generation Examples:")
        # Simulate different z-score scenarios
        print(f"Z = -2.0: {generate_signals('AAPL', None, current_z_score=-2.0)}")  # BUY
        print(f"Z = 2.0: {generate_signals('AAPL', None, current_z_score=2.0)}")    # SELL (short)
        print(f"Z = -0.5 (was long): {generate_signals('AAPL', None, current_z_score=-0.5)}") # EXIT_LONG (assuming it was between Z_ENTRY_LONG and Z_EXIT_LONG)
        print(f"Z = 0.5 (was short): {generate_signals('AAPL', None, current_z_score=0.5)}")   # EXIT_SHORT (assuming it was between Z_ENTRY_SHORT and Z_EXIT_SHORT)
        print(f"Z = -3.5 (was long): {generate_signals('AAPL', None, current_z_score=-3.5)}") # STOP_LOSS_LONG
        print(f"Z = 3.5 (was short): {generate_signals('AAPL', None, current_z_score=3.5)}")   # STOP_LOSS_SHORT
        print(f"Z = 0.0: {generate_signals('AAPL', None, current_z_score=0.0)}")      # NO_SIGNAL (or EXIT if near zero based on config)

        # Test with insufficient data
        print(f"Insufficient data: {generate_signals('MSFT', dummy_prices.head(10))}")

        # Test with the dummy_prices DataFrame to get the latest signal
        latest_signal = generate_signals('TSLA', dummy_prices)
        print(f"Latest signal for TSLA based on dummy data (Z={z_scores.iloc[-1]:.2f}): {latest_signal}")

    else:
        print("Could not calculate z-scores from dummy data.")

    # Test case for z_score when std is zero
    constant_prices = pd.DataFrame({'close': [100.0] * 40})
    z_constant = calculate_zscore(constant_prices)
    print(f"\nZ-scores for constant prices (should be NaN or filled with previous if any): \n{z_constant}")
    signal_constant = generate_signals("XYZ", constant_prices)
    print(f"Signal for constant prices: {signal_constant}")
