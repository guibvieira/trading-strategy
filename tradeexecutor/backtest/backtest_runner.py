import datetime
import runpy
from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from queue import Queue
from typing import Optional, Callable, Tuple

from tradeexecutor.backtest.backtest_execution import BacktestExecutionModel
from tradeexecutor.backtest.backtest_pricing import BacktestSimplePricingModel
from tradeexecutor.backtest.backtest_routing import BacktestRoutingModel
from tradeexecutor.backtest.backtest_sync import BacktestSyncer
from tradeexecutor.backtest.backtest_valuation import BacktestValuationModel
from tradeexecutor.backtest.simulated_wallet import SimulatedWallet
from tradeexecutor.cli.loop import ExecutionLoop
from tradeexecutor.state.state import State
from tradeexecutor.state.store import NoneStore
from tradeexecutor.strategy.approval import UncheckedApprovalModel, ApprovalModel
from tradeexecutor.strategy.cycle import CycleDuration
from tradeexecutor.strategy.description import StrategyExecutionDescription
from tradeexecutor.strategy.execution_model import ExecutionContext
from tradeexecutor.strategy.factory import make_runner_for_strategy_mod
from tradeexecutor.strategy.pandas_trader.runner import PandasTraderRunner
from tradeexecutor.strategy.strategy_module import parse_strategy_module, StrategyModuleInformation
from tradeexecutor.strategy.trading_strategy_universe import TradingStrategyUniverse
from tradeexecutor.strategy.universe_model import StaticUniverseModel
from tradingstrategy.client import Client


@dataclass
class BacktestSetup:
    """Describe backtest setup, ready to run."""
    start_at: datetime.datetime
    end_at: datetime.datetime
    cycle_duration: CycleDuration
    universe: Optional[TradingStrategyUniverse]
    wallet: SimulatedWallet
    state: State
    pricing_model: BacktestSimplePricingModel
    routing_model: BacktestRoutingModel
    execution_model: BacktestExecutionModel
    sync_method: BacktestSyncer
    strategy_module: StrategyModuleInformation

    def backtest_static_universe_strategy_factory(
            self,
            *ignore,
            execution_model: BacktestExecutionModel,
            execution_context: ExecutionContext,
            sync_method: BacktestSyncer,
            pricing_model_factory: Callable,
            valuation_model_factory: Callable,
            client: Client,
            timed_task_context_manager: AbstractContextManager,
            approval_model: ApprovalModel,
            **kwargs) -> StrategyExecutionDescription:
        """Create a strategy description and runner based on backtest parameters in this setup."""

        assert self.universe is not None, "Only static universe models supported for now"
        assert not execution_context.live_trading, f"This can be only used for backtesting strategies. execution context is {execution_context}"

        universe_model = StaticUniverseModel(self.universe)

        runner = PandasTraderRunner(
            timed_task_context_manager=timed_task_context_manager,
            execution_model=execution_model,
            approval_model=approval_model,
            valuation_model_factory=valuation_model_factory,
            sync_method=sync_method,
            pricing_model_factory=pricing_model_factory,
            routing_model=self.routing_model,
            decide_trades=self.strategy_module.decide_trades,
        )

        return StrategyExecutionDescription(
            universe_model=universe_model,
            runner=runner,
            trading_strategy_engine_version=self.strategy_module.trading_strategy_engine_version,
            cycle_duration=self.cycle_duration,
        )


def setup_backtest_for_universe(
        strategy_path: Path,
        start_at: datetime.datetime,
        end_at: datetime.datetime,
        cycle_duration: CycleDuration,
        initial_deposit: int,
        universe: TradingStrategyUniverse,
        routing_model: BacktestRoutingModel,
        max_slippage=0.01,
        validate_strategy_module=False,
    ):
    """High-level entry point for running a single backtest.

    The trading universe creation from the strategy is skipped,
    instead of you can pass your own universe e.g. synthetic universe.

    :param cycle_duration:
        Override the default strategy cycle duration
    """

    assert initial_deposit > 0

    wallet = SimulatedWallet()

    deposit_syncer = BacktestSyncer(wallet, Decimal(initial_deposit))

    # Create the initial state
    state = State()
    events = deposit_syncer(state.portfolio, start_at, universe.reserve_assets)
    assert len(events) == 1
    token, usd_exchange_rate = state.portfolio.get_default_reserve_currency()
    assert usd_exchange_rate == 1
    assert state.portfolio.get_current_cash() == initial_deposit

    # Set up execution and pricing
    pricing_model = BacktestSimplePricingModel(universe, routing_model)
    execution_model = BacktestExecutionModel(wallet, max_slippage)

    # Load strategy Python file
    strategy_mod_exports: dict = runpy.run_path(strategy_path)
    strategy_module = parse_strategy_module(strategy_mod_exports)

    if validate_strategy_module:
        # Allow partial strategies to be used in unit testing
        strategy_module.validate()

    return BacktestSetup(
        start_at,
        end_at,
        cycle_duration,
        wallet=wallet,
        state=state,
        universe=universe,
        pricing_model=pricing_model,
        execution_model=execution_model,
        routing_model=routing_model,
        sync_method=deposit_syncer,
        strategy_module=strategy_module,
    )


def run_backtest(setup: BacktestSetup, client: Optional[Client]=None) -> Tuple[State, dict]:
    """Run a strategy backtest.

    :return:
        Tuple(the final state of the backtest, debug dump)
    """

    # State is pristine and not used yet
    assert len(list(setup.state.portfolio.get_all_trades())) == 0

    store = NoneStore(setup.state)

    def pricing_model_factory(execution_model, universe, routing_model):
        return setup.pricing_model

    def valuation_model_factory(pricing_model):
        return BacktestValuationModel(setup.pricing_model)

    main_loop = ExecutionLoop(
        name="backtest",
        command_queue=Queue(),
        execution_model=setup.execution_model,
        sync_method=setup.sync_method,
        approval_model=UncheckedApprovalModel(),
        pricing_model_factory=pricing_model_factory,
        valuation_model_factory=valuation_model_factory,
        store=store,
        client=client,
        strategy_factory=setup.backtest_static_universe_strategy_factory,
        cycle_duration=setup.cycle_duration,
        stats_refresh_frequency=None,
        max_data_delay=None,
        debug_dump_file=None,
        backtest_start=setup.start_at,
        backtest_end=setup.end_at,
        tick_offset=datetime.timedelta(seconds=1),
        trade_immediately=True,
    )

    debug_dump = main_loop.run()

    return setup.state, debug_dump