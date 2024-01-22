"""Test live short-only strategy on 1delta using forked Polygon"""
import datetime
import os
import shutil
from decimal import Decimal
from typing import List

import pytest
import pandas as pd
from web3 import Web3
from web3.contract import Contract
import flaky

from eth_defi.uniswap_v3.deployment import UniswapV3Deployment
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import fork_network_anvil, mine
from tradingstrategy.exchange import ExchangeUniverse
from tradingstrategy.pair import PandasPairUniverse
from tradingstrategy.chain import ChainId
from tradingstrategy.timebucket import TimeBucket
from tradingstrategy.lending import LendingProtocolType

from tradeexecutor.ethereum.one_delta.one_delta_live_pricing import OneDeltaLivePricing
from tradeexecutor.ethereum.one_delta.one_delta_routing import OneDeltaRouting
from tradeexecutor.state.state import State
from tradeexecutor.state.trade import TradeExecution
from tradeexecutor.strategy.cycle import snap_to_next_tick
from tradeexecutor.strategy.pandas_trader.position_manager import PositionManager
from tradeexecutor.strategy.pricing_model import PricingModel
from tradeexecutor.strategy.execution_context import python_script_execution_context, unit_test_execution_context
from tradeexecutor.strategy.universe_model import default_universe_options
from tradeexecutor.strategy.trading_strategy_universe import translate_trading_pair, TradingStrategyUniverse, load_partial_data
from tradeexecutor.strategy.execution_context import ExecutionMode
from tradeexecutor.strategy.run_state import RunState
from tradeexecutor.ethereum.universe import create_exchange_universe, create_pair_universe
from tradeexecutor.testing.simulated_execution_loop import set_up_simulated_execution_loop_one_delta
from tradeexecutor.utils.blockchain import get_latest_block_timestamp
from tradeexecutor.strategy.account_correction import check_accounts


pytestmark = pytest.mark.skipif(
    (os.environ.get("JSON_RPC_POLYGON") is None) or (shutil.which("anvil") is None),
    reason="Set JSON_RPC_POLYGON env install anvil command to run these tests",
)


#: How much values we allow to drift.
#: A hack fix receiving different decimal values on Github CI than on a local
APPROX_REL = 0.001
APPROX_REL_DECIMAL = Decimal("0.001")


def test_one_delta_live_credit_supply(
    logger,
    web3: Web3,
    hot_wallet: HotWallet,
    trading_strategy_universe: TradingStrategyUniverse,
    one_delta_routing_model: OneDeltaRouting,
    uniswap_v3_deployment: UniswapV3Deployment,
    usdc: Contract,
    weth: Contract,
    asset_usdc,
):
    """Live 1delta trade.

    - Trade ETH/USDC 0.3% pool

    - Sets up a simple strategy that open a 2x short position then close in next cycle

    - Start the strategy, check that the trading account is funded

    - Advance to cycle 1 and make sure the short position on ETH is opened

    - Advance to cycle 2 and make sure the short position is closed
    """

    def decide_trades(
        timestamp: pd.Timestamp,
        strategy_universe: TradingStrategyUniverse,
        state: State,
        pricing_model: PricingModel,
        cycle_debug_data: dict
    ) -> List[TradeExecution]:
        """Opens a 2x short position and closes in next trade cycle."""
        
        pair = strategy_universe.universe.pairs.get_single()

        # Open for 1,000 USD
        position_size = 1000.00

        trades = []

        position_manager = PositionManager(timestamp, strategy_universe, state, pricing_model)

        if not position_manager.is_any_credit_supply_position_open():
            trades += position_manager.open_credit_supply_position_for_reserves(position_size)
        # else:
        #     trades += position_manager.close_all()

        return trades

    routing_model = one_delta_routing_model

    # Sanity check for the trading universe
    # that we start with 1631 USD/ETH price
    pair_universe = trading_strategy_universe.data_universe.pairs
    pricing_method = OneDeltaLivePricing(web3, pair_universe, routing_model)

    weth_usdc = pair_universe.get_single()
    pair = translate_trading_pair(weth_usdc)

    # Check that our preflight checks pass
    routing_model.perform_preflight_checks_and_logging(pair_universe)

    price_structure = pricing_method.get_buy_price(datetime.datetime.utcnow(), pair, None)
    assert price_structure.price == pytest.approx(2239.420956551886, rel=APPROX_REL)

    # Set up an execution loop we can step through
    state = State()
    loop = set_up_simulated_execution_loop_one_delta(
        web3=web3,
        decide_trades=decide_trades,
        universe=trading_strategy_universe,
        state=state,
        wallet_account=hot_wallet.account,
        routing_model=routing_model,
    )
    loop.runner.run_state = RunState()  # Needed for visualisations

    ts = get_latest_block_timestamp(web3)

    loop.tick(
        ts,
        loop.cycle_duration,
        state,
        cycle=1,
        live=True,
    )

    loop.update_position_valuations(
        ts,
        state,
        trading_strategy_universe,
        ExecutionMode.real_trading
    )

    loop.runner.check_accounts(trading_strategy_universe, state)

    assert len(state.portfolio.open_positions) == 1

    # After the first tick, we should have synced our reserves and opened the first position
    mid_price = pricing_method.get_mid_price(ts, pair)
    assert mid_price == pytest.approx(2238.0298724242684, rel=APPROX_REL)

    usdc_id = f"{web3.eth.chain_id}-{usdc.address.lower()}"
    assert state.portfolio.reserves[usdc_id].quantity == 9000
    assert state.portfolio.open_positions[1].get_quantity() == pytest.approx(Decimal(-0.893495022670441332))
    assert state.portfolio.open_positions[1].get_value() == pytest.approx(1000.0140651703407, rel=APPROX_REL)

    # mine a few block before running next tick
    # for i in range(1, 10):
    #     mine(web3)

    # # trade another cycle to close the short position
    # ts = get_latest_block_timestamp(web3)
    # strategy_cycle_timestamp = snap_to_next_tick(ts, loop.cycle_duration)

    # loop.tick(
    #     ts,
    #     loop.cycle_duration,
    #     state,
    #     cycle=2,
    #     live=True,
    #     strategy_cycle_timestamp=strategy_cycle_timestamp,
    # )

    # loop.update_position_valuations(
    #     ts,
    #     state,
    #     trading_strategy_universe,
    #     ExecutionMode.real_trading
    # )

    # loop.runner.check_accounts(trading_strategy_universe, state)

    # assert len(state.portfolio.open_positions) == 0
    # assert len(state.portfolio.closed_positions) == 1
    # assert state.portfolio.reserves[usdc_id].quantity == 10000


