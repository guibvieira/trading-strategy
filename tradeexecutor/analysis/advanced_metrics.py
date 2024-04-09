"""Advanced metrics.

Use :term:`Quantstats` library to calculate various metrics about the strategy performance.

This  will generate metrics like:

- Sharpe

- Sortino

- Max drawdown

**Note**: These metrics are based on equity curve and returns - they do go down to the individual trade level.
Any consecutive wins and losses are measured in days, not in the trade or candle count.
"""

import enum
import warnings

import pandas as pd

from tradeexecutor.state.identifier import TradingPairIdentifier
from tradeexecutor.strategy.trading_strategy_universe import TradingStrategyUniverse, translate_trading_pair
from tradeexecutor.visual.equity_curve import calculate_returns, resample_returns
from tradeexecutor.visual.qs_wrapper import import_quantstats_wrapped
from tradingstrategy.types import TokenSymbol


class AdvancedMetricsMode(enum.Enum):
    """What we will make quantstats to spit out."""

    #: Less stats
    basic = "basic"

    #: More stats
    full = "full"


def calculate_advanced_metrics(
    returns: pd.Series,
    mode: AdvancedMetricsMode=AdvancedMetricsMode.basic,
    periods_per_year=365,
    convert_to_daily=False,
    benchmark: pd.Series | None = None,
    display=False,
) -> pd.DataFrame:
    """Calculate advanced strategy performance statistics using Quantstats.

    Calculates multiple metrics used to benchmark strategies for :term:`risk-adjusted returns`
    in one go.

    See :term:`Quantstats` for more information.

    Example:

    .. code-block:: python

        from tradeexecutor.visual.equity_curve import calculate_equity_curve, calculate_returns
        from tradeexecutor.analysis.advanced_metrics import calculate_advanced_metrics

        equity = calculate_equity_curve(state)
        returns = calculate_returns(equity)
        metrics = calculate_advanced_metrics(returns)

        # Each metric as a series. Index 0 is our performance,
        # index 1 is the benchmark.
        sharpe = metrics.loc["Sharpe"][0]
        assert sharpe == pytest.approx(-1.73)

    See also :py:func:`visualise_advanced_metrics`.

    :param returns:
        Returns series of the strategy.

        See :py:`tradeeexecutor.visual.equity_curve.calculate_returns`.

    :param mode:
        Full or basic stats

    :param periods_per_year:
        How often the trade decision cycle was run.

        This affects "trading periods per year" needed, to calculate
        metrics like Sharpe.

        The defaults to the daily trading cycle, trading 24/7.

    :param convert_to_daily:
        QuantStats metrics can only work on daily data, so force convert from 1h or 8h or so if needed.

    :return:
        DataFrame of metrics generated by quantstats.

        You can directly display this in your notebook,
        or extract individual metrics.
    """
    #  DeprecationWarning: Importing display from IPython.core.display is deprecated since IPython 7.14, please import from IPython display
    with warnings.catch_warnings():
        warnings.simplefilter(action='ignore', category=FutureWarning)  # yfinance: The default dtype for empty Series will be 'object' instead of 'float64' in a future version. Specify a dtype explicitly to silence this warning.
        warnings.simplefilter(action='ignore', category=RuntimeWarning)   # Divided by Nan
        qs = import_quantstats_wrapped()
        metrics = qs.reports.metrics
        stats = qs.stats

        result = metrics(
            returns,
            benchmark=benchmark,
            as_pct=display,  # QuantStats codebase is a mess
            periods_per_year=periods_per_year,
            mode=mode.value,
            display=False,
            internal=display,  # Internal sets the flag for percent output
        )

        assert result is not None, "metrics(): returned None"

        if convert_to_daily:
            returns = resample_returns(returns, "D")

        # Hack - see analyse_combination()
        # Communicative annualized growth return,
        # as compounded
        # Should say CAGR (raw), but is what it is for the legacy reasons
        if benchmark is None:
            result.loc["Annualised return (raw)"] = [stats.cagr(returns, 0., compounded=True)]
        return result


def visualise_advanced_metrics(
    returns: pd.Series,
    mode: AdvancedMetricsMode=AdvancedMetricsMode.basic,
    benchmark: pd.Series | None = None,
    name: str | None = None,
    convert_to_daily=False,
) -> pd.DataFrame:
    """Calculate advanced strategy performance statistics using Quantstats.

    Calculates multiple metrics used to benchmark strategies for :term:`risk-adjusted returns`
    in one go.

    See :term:`Quantstats` for more information.

    Example:

    .. code-block:: python

        from tradeexecutor.visual.equity_curve import calculate_equity_curve, calculate_returns
        from tradeexecutor.analysis.advanced_metrics import visualise_advanced_metrics

        equity = calculate_equity_curve(state)
        returns = calculate_returns(equity)
        df = visualise_advanced_metrics(returns)
        display(df)

    Example with benchmarking against buy and hold ETH:

    .. code-block:: python

        from tradeexecutor.visual.equity_curve import calculate_equity_curve, calculate_returns, generate_buy_and_hold_returns
        from tradeexecutor.analysis.advanced_metrics import visualise_advanced_metrics, AdvancedMetricsMode

        equity = calculate_equity_curve(state)
        returns = calculate_returns(equity)
        benchmark_returns = generate_buy_and_hold_returns(benchmark_indexes["ETH"])
        benchmark_returns.attrs["name"] = "Buy and hold ETH"

        metrics = visualise_advanced_metrics(
            returns,
            mode=AdvancedMetricsMode.full,
            benchmark=benchmark_returns,
        )

        display(metrics)

    When dealing with 1h or 8h data:

    .. code-block:: python

        from tradeexecutor.analysis.advanced_metrics import visualise_advanced_metrics

        visualise_advanced_metrics(
            best_result.returns,
            benchmark=benchmark_indexes["ETH"],
            convert_to_daily=True,
        )

    See also :py:func:`calculate_advanced_metrics`.

    :param returns:
        Returns series of the strategy.

        See :py:`tradeeexecutor.visual.equity_curve.calculate_returns`.        

    :param mode:
        Full or basic stats

    :param benchmark:
        Benchmark portfolio or buy and hold asset.

        If this series as `series.attrs["name"]` name set, it is used as a title instead of "Benchmark".

    :param name:
        Title oif the primary performance series instead of "Strategy".

    :param convert_to_daily:
        QuantStats metrics can only work on daily data, so force convert from 1h or 8h or so if needed.

    :return:
        A DataFrame ready to display a table of comparable merics.

        Return empty DataFrame if `returns` is all zeroes.

    """

    with warnings.catch_warnings():
        warnings.simplefilter(action='ignore', category=FutureWarning)  # yfinance: The default dtype for empty Series will be 'object' instead of 'float64' in a future version. Specify a dtype explicitly to silence this warning.
        warnings.simplefilter(action='ignore', category=RuntimeWarning)   # Divided by Nan
        qs = import_quantstats_wrapped()
        metrics = qs.reports.metrics

        if not returns.any():
            # Cannot calculate any metrics, because
            # there has not been any trades (all returns are zero)
            return pd.DataFrame()
        
        if convert_to_daily:
            returns = resample_returns(returns, "D")

            if benchmark is not None:
                benchmark = resample_returns(calculate_returns(benchmark), "D")

        # Internal sets the flag for percent output
        df = metrics(
            returns,
            benchmark=benchmark,
            periods_per_year=365,
            mode=mode.value,
            internal=True,
            display=False
        )

        # Set the label
        if benchmark is not None:
            benchmark_name = benchmark.attrs.get("name")
            if benchmark_name:
                df = df.rename({"Benchmark": benchmark_name}, axis="columns")

        if name is not None:
            df = df.rename({"Strategy": name}, axis="columns")

        return df





