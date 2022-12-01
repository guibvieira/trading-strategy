"""Summary statistics are displayed on the summary tiles of the strategies."""
import datetime
from typing import Optional

import pandas as pd

from tradeexecutor.state.state import State
from tradeexecutor.state.statistics import calculate_naive_profitability
from tradeexecutor.strategy.execution_context import ExecutionMode
from tradeexecutor.strategy.summary import StrategySummaryStatistics


def calculate_summary_statistics(
        state: State,
        execution_mode: ExecutionMode,
        time_window = pd.Timedelta(days=90),
        now_: Optional[pd.Timestamp] = None
) -> StrategySummaryStatistics:
    """Preprocess the strategy statistics for the summary card.

    :param state:
        Strategy state from which we calculate the summary

    :param execution_mode:
        If we need to skip calculations during backtesting

    :param time_window:
        How long we look back for the summary statistics

    :param now_:
        Override current time for unit testing

    :return:
        Summary calculations for the summary tile,
        or empty `StrategySummaryStatistics` if cannot be calculated.
    """

    portfolio = state.portfolio

    # We can alway get the current value even if there are no trades
    current_value = portfolio.get_total_equity()

    first_trade, last_trade = portfolio.get_first_and_last_executed_trade()
    if first_trade is None:
        # No trades
        # Cannot calculate anything
        return StrategySummaryStatistics(current_value=current_value)

    first_trade_at = first_trade.executed_at
    last_trade_at = last_trade.executed_at

    if not now_:
        now_ = pd.Timestamp.utcnow().tz_localize(None)
    start_at = now_ - time_window

    stats = state.stats
    total_equity_time_series = stats.get_portfolio_statistics_dataframe("total_equity")

    if len(total_equity_time_series) > 0:
        profitability_90_days, time_window = calculate_naive_profitability(total_equity_time_series, look_back=time_window)
        enough_data = total_equity_time_series.index[0] <= start_at

        start_idx = total_equity_time_series.index.get_indexer([start_at], method="nearest")
        start_val = float(total_equity_time_series.iloc[start_idx])
        index: pd.Timestamp

        last_90_days_ts = total_equity_time_series.loc[start_at:]
        performance_chart_90_days = [(index.to_pydatetime(), (value - start_val) / start_val) for index, value in last_90_days_ts.items()]
    else:
        profitability_90_days = None
        enough_data = False
        performance_chart_90_days = None

    return StrategySummaryStatistics(
        first_trade_at=first_trade_at,
        last_trade_at=last_trade_at,
        enough_data=enough_data,
        current_value=current_value,
        profitability_90_days=profitability_90_days,
        performance_chart_90_days=performance_chart_90_days,
    )