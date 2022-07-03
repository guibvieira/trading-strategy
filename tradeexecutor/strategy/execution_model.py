import abc
import datetime
from dataclasses import dataclass
from typing import List, Callable

from tradeexecutor.state.state import State
from tradeexecutor.state.trade import TradeExecution
from tradeexecutor.strategy.routing import RoutingModel, RoutingState


@dataclass
class ExecutionContext:
    """Information about the strategy execution environment"""

    #: True if we doing live trading or paper trading.
    #: False if we are operating on backtesting data.
    live_trading: bool

    #: Python context manager for timed tasks.
    #: Used for profiling the strategy code run-time performance.
    #: See :py:mod:`tradeexecutor.utils.timer`.
    timed_task_context_manager: Callable



class ExecutionModel(abc.ABC):
    """Define how trades are executed.

    See also :py:class:`tradeexecutor.strategy.mode.ExecutionMode`.
    """

    @abc.abstractmethod
    def preflight_check(self):
        """Check that we can start the trade executor

        :raise: AssertionError if something is a miss
        """

    @abc.abstractmethod
    def initialize(self):
        """Read any on-chain, etc., data to get synced.
        """

    @abc.abstractmethod
    def get_routing_state_details(self) -> object:
        """Get needed details to establish a routing state.

        TODO: API Unfinished
        """

    @abc.abstractmethod
    def execute_trades(self,
                       ts: datetime.datetime,
                       state: State,
                       trades: List[TradeExecution],
                       routing_model: RoutingModel,
                       routing_state: RoutingState,
                       max_slippage=0.005,
                       check_balances=False,
                       ):
        """Execute the trades determined by the algo on a designed Uniswap v2 instance.

        :param ts:
            Timestamp of the trade cycle.

        :param universe:
            Current trading universe for this cycle.

        :param state:
            State of the trade executor.

        :param trades:
            List of trades decided by the strategy.
            Will be executed and modified in place.

        :param routing_model:
            Routing model how to execute the trades

        :param routing_state:
            State of already made on-chain transactions and such on this cycle

        :param max_slippage:
            Max slippage % allowed on trades before trade execution fails.

        :param check_balances:
            Check that on-chain accounts have enough balance before creating transaction objects.
            Useful during unit tests to spot issues in trade routing.
        """
