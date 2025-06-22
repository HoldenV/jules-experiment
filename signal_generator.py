import pandas as pd
import numpy as np
import config
import logger

def calculate_zscore(prices_df):
    """
    Calculates z-scores for a price series.
    :param prices_df: Pandas DataFrame with a 'close' column, or a Pandas Series of prices.
                      Assumes index is time-sorted.
    :return: Pandas Series of z-scores, or None if input is insufficient/malformed.
    """
    if prices_df is None or len(prices_df) < config.Z_SCORE_WINDOW:
        # Logger call already exists in original, good for debugging
        logger.log_action(f"Z-score calculation: Insufficient data. Need {config.Z_SCORE_WINDOW}, got {len(prices_df) if prices_df is not None else 0}.")
        return None

    if isinstance(prices_df, pd.Series):
        prices = prices_df
    elif 'close' in prices_df.columns:
        prices = prices_df['close']
    elif len(prices_df.columns) == 1: # Fallback: assume single column is price data
        prices = prices_df.iloc[:, 0]
    else:
        logger.log_action("Error: 'close' column not found or ambiguous input for z-score prices_df.")
        return None

    if not isinstance(prices, pd.Series): # Should be caught above, but defensive check
        logger.log_action("Error: Price data for z-score is not a Pandas Series after extraction.")
        return None

    moving_avg = prices.rolling(window=config.Z_SCORE_WINDOW).mean()
    rolling_std = prices.rolling(window=config.Z_SCORE_WINDOW).std()

    # Replace std=0 with NaN before division to avoid inf/-inf, then forward fill.
    z_scores = (prices - moving_avg) / rolling_std.replace(0, np.nan)
    return z_scores.ffill()

def generate_signals(ticker, historical_data_df, current_z_score=None):
    """
    Generates trading signals based on z-score.
    :param ticker: Stock ticker string (for logging/context, not directly used in logic here).
    :param historical_data_df: Pandas DataFrame with historical prices ('close' column).
                               Used if current_z_score is None.
    :param current_z_score: Most recent z-score. If None, calculated from historical_data_df.
    :return: Signal string (e.g., "BUY", "SELL", "EXIT_LONG", "NO_SIGNAL").
    """
    if current_z_score is None:
        if historical_data_df is None or historical_data_df.empty:
            return "NO_SIGNAL"
        z_scores = calculate_zscore(historical_data_df) # Expects 'close' or Series
        if z_scores is None or z_scores.empty:
            return "NO_SIGNAL"
        current_z_score = z_scores.iloc[-1]

    if pd.isna(current_z_score): # Check after potential calculation
        return "NO_SIGNAL"

    # Entry signals
    if current_z_score < config.Z_ENTRY_LONG:
        return "BUY"
    if current_z_score > config.Z_ENTRY_SHORT:
        return "SELL" # Represents a short sell signal

    # Exit signals (Position manager determines if a position is actually open)
    # TODO: Review these conditions to ensure they fully capture intended logic (see README TODOs)
    if config.Z_ENTRY_LONG < current_z_score < config.Z_EXIT_LONG :
        return "EXIT_LONG"
    if config.Z_ENTRY_SHORT > current_z_score > config.Z_EXIT_SHORT:
        return "EXIT_SHORT"

    # Stop-loss signals
    if current_z_score < config.Z_STOP_LOSS_LONG: # Applied by position manager if long
        return "STOP_LOSS_LONG"
    if current_z_score > config.Z_STOP_LOSS_SHORT: # Applied by position manager if short
        return "STOP_LOSS_SHORT"

    # Default if no other signal conditions are met
    return "NO_SIGNAL"

if __name__ == '__main__':
    # Standalone testing block
    dummy_prices = pd.DataFrame({
        'close': np.concatenate([
            np.linspace(100, 90, 15),
            np.linspace(90, 80, 15),   # Z < -1.5 potential
            np.linspace(80, 85, 5),
            np.linspace(85, 110, 10),
            np.linspace(110, 125, 15), # Z > 1.5 potential
            np.linspace(125, 115, 5)
        ])
    }, index=pd.date_range(start='2023-01-01', periods=65, freq='B'))

    z_scores_test = calculate_zscore(dummy_prices)
    if z_scores_test is not None:
        print("Calculated Z-Scores (tail):")
        print(z_scores_test.tail(10))

        print("\nSignal Generation Examples (using direct z-score values):")
        print(f"Z = -2.0: {generate_signals('TEST', None, current_z_score=-2.0)}")
        print(f"Z = 2.0: {generate_signals('TEST', None, current_z_score=2.0)}")
        print(f"Z = -0.5 (assuming was long, Z_ENTRY_LONG=-1.5, Z_EXIT_LONG=-0.1): {generate_signals('TEST', None, current_z_score=-0.5)}")
        print(f"Z = 0.5 (assuming was short, Z_ENTRY_SHORT=1.5, Z_EXIT_SHORT=0.1): {generate_signals('TEST', None, current_z_score=0.5)}")
        print(f"Z = -3.5 (stop for long): {generate_signals('TEST', None, current_z_score=-3.5)}")
        print(f"Z = 3.5 (stop for short): {generate_signals('TEST', None, current_z_score=3.5)}")
        print(f"Z = 0.0 (no signal zone): {generate_signals('TEST', None, current_z_score=0.0)}")

        print(f"Insufficient data signal: {generate_signals('TEST_INSUFF', dummy_prices.head(10))}") # Uses historical_data_df

        latest_signal_from_df = generate_signals('TEST_DF', dummy_prices) # Calculates from DF
        print(f"Latest signal from dummy_prices DF (Z={z_scores_test.iloc[-1]:.2f}): {latest_signal_from_df}")
    else:
        print("Could not calculate z-scores from dummy_prices for testing.")

    constant_prices_df = pd.DataFrame({'close': [100.0] * (config.Z_SCORE_WINDOW + 5)}) # Ensure enough data
    z_constant_test = calculate_zscore(constant_prices_df['close']) # Pass Series
    print(f"\nZ-scores for constant prices (expect NaNs or ffilled NaNs): \n{z_constant_test.tail()}")
    signal_constant_test = generate_signals("TEST_CONST", None, current_z_score=z_constant_test.iloc[-1] if z_constant_test is not None and not z_constant_test.empty else np.nan)
    print(f"Signal for constant prices (Z={z_constant_test.iloc[-1] if z_constant_test is not None and not z_constant_test.empty else 'NaN'}): {signal_constant_test}")
