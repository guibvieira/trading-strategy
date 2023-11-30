"""Test live routing of combined Uniswap v2, v3 spot and 1delta leveraged positions."""
import datetime
import os
import shutil
from decimal import Decimal

import pytest as pytest

from eth_defi.balances import fetch_erc20_balances_by_token_list
from eth_defi.token import TokenDetails
from tradeexecutor.ethereum.execution import EthereumExecution
from tradingstrategy.chain import ChainId
from web3 import Web3

from eth_defi.hotwallet import HotWallet
from tradeexecutor.strategy.pandas_trader.position_manager import PositionManager
from tradeexecutor.ethereum.hot_wallet_sync_model import HotWalletSyncModel
from tradeexecutor.state.identifier import AssetIdentifier, TradingPairIdentifier
from tradeexecutor.state.state import State
from tradeexecutor.strategy.generic.generic_router import GenericRouting
from tradeexecutor.strategy.generic.generic_pricing_model import GenericPricing
from tradeexecutor.strategy.trading_strategy_universe import TradingStrategyUniverse


pytestmark = pytest.mark.skipif(
    (os.environ.get("JSON_RPC_POLYGON") is None) or (shutil.which("anvil") is None),
    reason="Set JSON_RPC_POLYGON env install anvil command to run these tests",
)



def test_generic_routing_open_position_across_markets(
    web3: Web3,
    hot_wallet: HotWallet,
    strategy_universe: TradingStrategyUniverse,
    generic_routing_model: GenericRouting,
    generic_pricing_model: GenericPricing,
    asset_usdc: AssetIdentifier,
    asset_weth: AssetIdentifier,
    asset_wmatic: AssetIdentifier,
    wmatic_usdc_spot_pair: TradingPairIdentifier,
    weth_usdc_spot_pair: TradingPairIdentifier,
    weth_usdc_shorting_pair: TradingPairIdentifier,
    execution_model: EthereumExecution,
    weth: TokenDetails,
    vweth: TokenDetails,
    ausdc: TokenDetails,
):
    """Open Uniswap v2, v3 and 1delta position in the same state."""

    routing_model = generic_routing_model

    # Check we have data for both DEXes needed
    exchange_universe = strategy_universe.data_universe.pairs.exchange_universe
    assert exchange_universe.get_exchange_count() == 2
    quickswap = exchange_universe.get_by_chain_and_slug(ChainId.polygon, "quickswap")
    assert quickswap is not None
    uniswap_v3 = exchange_universe.get_by_chain_and_slug(ChainId.polygon, "uniswap-v3")
    assert uniswap_v3 is not None

    # Check that our preflight checks pass
    routing_model.perform_preflight_checks_and_logging(strategy_universe.data_universe.pairs)

    sync_model = HotWalletSyncModel(
        web3,
        hot_wallet,
    )

    state = State()
    sync_model.sync_initial(state)

    # Strategy has its reserve balances updated
    sync_model.sync_treasury(datetime.datetime.utcnow(), state, supported_reserves=[asset_usdc])

    assert state.portfolio.get_reserve_position(asset_usdc).quantity == Decimal('10_000')

    # Setup routing state for the approvals of this cycle
    routing_state_details = execution_model.get_routing_state_details()
    routing_state = routing_model.create_routing_state(strategy_universe, routing_state_details)

    position_manager = PositionManager(
        datetime.datetime.utcnow(),
        strategy_universe,
        state,
        generic_pricing_model
    )

    # Trade on Quickswap spot
    trades = position_manager.open_spot(
        wmatic_usdc_spot_pair,
        100.0,
    )
    execution_model.execute_trades(
        datetime.datetime.utcnow(),
        state,
        trades,
        routing_model,
        routing_state,
        check_balances=True,
    )
    assert all([t.is_success() for t in trades])

    # Trade on Uniswap v3 spot
    trades = position_manager.open_spot(
        weth_usdc_spot_pair,
        100.0,
    )
    execution_model.execute_trades(
        datetime.datetime.utcnow(),
        state,
        trades,
        routing_model,
        routing_state,
        check_balances=True,
    )
    assert all([t.is_success() for t in trades])

    # Trade 1delta + Aave short
    trades = position_manager.open_short(
        weth_usdc_spot_pair,
        300.0,
        leverage=2.0,
    )
    execution_model.execute_trades(
        datetime.datetime.utcnow(),
        state,
        trades,
        routing_model,
        routing_state,
        check_balances=True,
    )
    assert all([t.is_success() for t in trades])
    assert len(state.portfolio.open_positions) == 3

    # Check our wallet holds all tokens we expect.
    # Note that these are live prices from mainnet,
    # so we do a ranged check.
    asset_vweth = weth_usdc_shorting_pair.base
    asset_ausdc = weth_usdc_shorting_pair.quote
    balances = fetch_erc20_balances_by_token_list(
        web3,
        hot_wallet.address,
        {
            asset_usdc.address,
            asset_weth.address,
            asset_wmatic.address,
            asset_vweth.address,
            asset_ausdc.address,
        },
        decimalise=True,
    )
    assert 0 < balances[asset_wmatic.address] < 1000, f"Got balance: {balances}"
    assert 0 < balances[asset_weth.address] < 1000, f"Got balance: {balances}"
    assert 0 < balances[asset_vweth.address] < 1000, f"Got balance: {balances}"
    assert 0 < balances[asset_ausdc.address] < 10_000, f"Got balance: {balances}"
    assert balances[asset_usdc.address] == pytest.approx(9_500), f"Got balance: {balances}"


def test_generic_routing_close_position_across_markets(
    web3: Web3,
    hot_wallet: HotWallet,
    strategy_universe: TradingStrategyUniverse,
    generic_routing_model: GenericRouting,
    generic_pricing_model: GenericPricing,
    asset_usdc: AssetIdentifier,
    asset_weth: AssetIdentifier,
    asset_wmatic: AssetIdentifier,
    wmatic_usdc_spot_pair: TradingPairIdentifier,
    weth_usdc_spot_pair: TradingPairIdentifier,
    weth_usdc_shorting_pair: TradingPairIdentifier,
    execution_model: EthereumExecution,
    weth: TokenDetails,
):
    """Close Uniswap v2, v3 and 1delta position in the same state."""

    routing_model = generic_routing_model

    sync_model = HotWalletSyncModel(
        web3,
        hot_wallet,
    )

    state = State()
    sync_model.sync_initial(state)

    # Strategy has its reserve balances updated
    sync_model.sync_treasury(datetime.datetime.utcnow(), state, supported_reserves=[asset_usdc])

    assert state.portfolio.get_reserve_position(asset_usdc).quantity == Decimal('10_000')

    # Setup routing state for the approvals of this cycle
    routing_state_details = execution_model.get_routing_state_details()
    routing_state = routing_model.create_routing_state(strategy_universe, routing_state_details)

    # Open all positions
    position_manager = PositionManager(
        datetime.datetime.utcnow(),
        strategy_universe,
        state,
        generic_pricing_model
    )
    trades = position_manager.open_spot(
        wmatic_usdc_spot_pair,
        100.0,
    )
    trades += position_manager.open_spot(
        weth_usdc_spot_pair,
        100.0,
    )
    trades += position_manager.open_short(
        weth_usdc_spot_pair,
        300.0,
        leverage=2.0,
    )
    execution_model.execute_trades(
        datetime.datetime.utcnow(),
        state,
        trades,
        routing_model,
        routing_state,
        check_balances=True,
    )
    assert all([t.is_success() for t in trades])

    # Close all positions
    position_manager = PositionManager(
        datetime.datetime.utcnow(),
        strategy_universe,
        state,
        generic_pricing_model
    )
    trades = position_manager.close_all()
    execution_model.execute_trades(
        datetime.datetime.utcnow(),
        state,
        trades,
        routing_model,
        routing_state,
        check_balances=True,
    )
    assert all([t.is_success() for t in trades])

    # Check our wallet holds all tokens we expect.
    # Note that these are live prices from mainnet,
    # so we do a ranged check.
    asset_vweth = weth_usdc_shorting_pair.base
    asset_ausdc = weth_usdc_shorting_pair.quote
    balances = fetch_erc20_balances_by_token_list(
        web3,
        hot_wallet.address,
        {
            asset_usdc.address,
            asset_weth.address,
            asset_wmatic.address,
            asset_vweth.address,
            asset_ausdc.address,
        },
        decimalise=True,
    )
    assert balances[asset_wmatic.address] == 0, f"Got balance: {balances}"
    assert balances[asset_weth.address] == 0, f"Got balance: {balances}"
    assert balances[asset_vweth.address] == 0, f"Got balance: {balances}"
    assert balances[asset_ausdc.address] == 0, f"Got balance: {balances}"
    assert balances[asset_usdc.address] == pytest.approx(Decimal(9992.900326), rel=Decimal(0.03)), f"Got balance: {balances}"
