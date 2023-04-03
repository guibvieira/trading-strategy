"""Uniswap v2 routing model tests.

To run these tests, we need to connect to polygon Chain:

.. code-block::  shell

    export polygon_CHAIN_JSON_RPC="https://bsc-dataseed.binance.org/"
    pytest -k test_uniswap_v2_routing

"""

import datetime
import logging
import os
from decimal import Decimal

import flaky
import pytest
from eth_account import Account
from eth_defi.anvil import fork_network_anvil
from eth_defi.chain import install_chain_middleware
from eth_defi.abi import get_deployed_contract
from eth_defi.gas import estimate_gas_fees, node_default_gas_price_strategy
from eth_defi.confirmation import wait_transactions_to_complete
from eth_typing import HexAddress, HexStr
from web3 import Web3, HTTPProvider
from web3.contract import Contract

from eth_defi.hotwallet import HotWallet
from eth_defi.uniswap_v3.deployment import (
    UniswapV3Deployment,
    fetch_deployment,
)

from tradeexecutor.ethereum.tx import TransactionBuilder
from tradeexecutor.ethereum.uniswap_v3_routing import (
    UniswapV3RoutingState,
    UniswapV3SimpleRoutingModel,
    OutOfBalance,
)
from tradeexecutor.ethereum.uniswap_v3_execution import UniswapV3ExecutionModel
from tradeexecutor.ethereum.wallet import sync_reserves
from tradeexecutor.state.sync import apply_sync_events
from tradeexecutor.state.portfolio import Portfolio
from tradeexecutor.state.state import State
from tradeexecutor.state.identifier import AssetIdentifier, TradingPairIdentifier
from tradeexecutor.state.position import TradingPosition

from tradeexecutor.cli.log import setup_pytest_logging


# https://docs.pytest.org/en/latest/how-to/skipping.html#skip-all-test-functions-of-a-class-or-module
from tradeexecutor.strategy.trading_strategy_universe import (
    create_pair_universe_from_code,
)
from tradeexecutor.testing.pairuniversetrader import PairUniverseTestTrader
from tradingstrategy.chain import ChainId
from tradingstrategy.pair import PandasPairUniverse


pytestmark = pytest.mark.skipif(
    os.environ.get("JSON_RPC_POLYGON") is None,
    reason="Set JSON_RPC_POLYGON environment variable to Polygon node to run this test",
)


@pytest.fixture(scope="module")
def logger(request):
    """Setup test logger."""
    return setup_pytest_logging(request)


@pytest.fixture()
def large_usdc_holder() -> HexAddress:
    """A random account picked from Polygon chain that holds a lot of usdc.

    This account is unlocked on Ganache, so you have access to good usdc stash.

    `To find large holder accounts, use polygonscan <https://polygonscan.com/token/0x2791bca1f2de4661ed88a30c99a7a9449aa84174#balances>`_.
    """
    # Binance Hot Wallet 2
    return HexAddress(HexStr("0xe7804c37c13166fF0b37F5aE0BB07A3aEbb6e245"))


@pytest.fixture()
def anvil_polygon_chain_fork(logger, large_usdc_holder) -> str:
    """Create a testable fork of live polygon chain.

    :return: JSON-RPC URL for Web3
    """

    mainnet_rpc = os.environ["JSON_RPC_POLYGON"]

    launch = fork_network_anvil(mainnet_rpc, unlocked_addresses=[large_usdc_holder])
    try:
        yield launch.json_rpc_url
    finally:
        launch.close(log_level=logging.INFO)


@pytest.fixture
def web3(anvil_polygon_chain_fork: str):
    """Set up a local unit testing blockchain."""
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    web3 = Web3(HTTPProvider(anvil_polygon_chain_fork, request_kwargs={"timeout": 5}))
    install_chain_middleware(web3)
    return web3


@pytest.fixture
def chain_id(web3) -> int:
    """The test chain id (67)."""
    return web3.eth.chain_id


@pytest.fixture
def usdc_token(web3) -> Contract:
    """usdc with $4B supply."""
    # https://polygonscan.com/address/0x2791bca1f2de4661ed88a30c99a7a9449aa84174
    token = get_deployed_contract(
        web3, "ERC20MockDecimals.json", "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    )
    return token


@pytest.fixture
def eth_token(web3) -> Contract:
    """eth token."""
    # https://polygonscan.com//address/0x7ceb23fd6bc0add59e62ac25578270cff1b9f619
    # https://tradingstrategy.ai/trading-view/polygon/tokens/0x7ceb23fd6bc0add59e62ac25578270cff1b9f619
    token = get_deployed_contract(
        web3, "ERC20MockDecimals.json", "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619"
    )
    return token


@pytest.fixture()
def uniswap_v3(web3) -> UniswapV3Deployment:
    """Fetch live uniswap_v3 v3 deployment.

    See https://docs.uniswap_v3.exchange/concepts/protocol-overview/03-smart-contracts for more information
    """
    deployment = fetch_deployment(
        web3,
        "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        "0xE592427A0AEce92De3Edee1F18E0157C05861564",
        "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
        "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6",
    )
    return deployment


@pytest.fixture
def wmatic_token(uniswap_v3: UniswapV3Deployment) -> Contract:
    """WMATIC is native token of Polygon."""
    return uniswap_v3.weth


@pytest.fixture()
def usdc_asset(usdc_token, chain_id) -> AssetIdentifier:
    return AssetIdentifier(
        chain_id,
        usdc_token.address,
        usdc_token.functions.symbol().call(),
        usdc_token.functions.decimals().call(),
    )


@pytest.fixture
def matic_asset(wmatic_token, chain_id) -> AssetIdentifier:
    return AssetIdentifier(
        chain_id,
        wmatic_token.address,
        wmatic_token.functions.symbol().call(),
        wmatic_token.functions.decimals().call(),
    )


@pytest.fixture
def eth_asset(eth_token, chain_id) -> AssetIdentifier:
    return AssetIdentifier(
        chain_id,
        eth_token.address,
        eth_token.functions.symbol().call(),
        eth_token.functions.decimals().call(),
    )


@pytest.fixture
def eth_matic_trading_pair_address() -> HexAddress:
    """See https://tradingstrategy.ai/trading-view/polygon/uniswap-v3/matic-eth-fee-5"""
    return HexAddress(HexStr("0x86f1d8390222A3691C28938eC7404A1661E618e0"))


@pytest.fixture
def matic_usdc_trading_pair_address() -> HexAddress:
    """See https://tradingstrategy.ai/trading-view/polygon/uniswap-v3/matic-usdc-fee-5"""
    return HexAddress(HexStr("0xA374094527e1673A86dE625aa59517c5dE346d32"))


@pytest.fixture()
def hot_wallet(
    web3: Web3, usdc_token: Contract, large_usdc_holder: HexAddress
) -> HotWallet:
    """Our trading Ethereum account.

    Start with 10,000 USDC cash and 2 polygon.
    """
    account = Account.create()
    web3.eth.send_transaction(
        {"from": large_usdc_holder, "to": account.address, "value": 2 * 10**18}
    )
    tx_hash = usdc_token.functions.transfer(account.address, 10_000 * 10**6).transact(
        {"from": large_usdc_holder}
    )
    wait_transactions_to_complete(web3, [tx_hash])
    wallet = HotWallet(account)
    wallet.sync_nonce(web3)
    return wallet


@pytest.fixture
def eth_usdc_trading_pair(eth_asset, usdc_asset, uniswap_v3) -> TradingPairIdentifier:
    """eth-usdc pair representation in the trade executor domain."""
    return TradingPairIdentifier(
        eth_asset,
        usdc_asset,
        "0x45dDa9cb7c25131DF268515131f647d726f50608",  #  https://tradingstrategy.ai/trading-view/polygon/uniswap-v3/eth-usdc-fee-5
        internal_id=1000,  # random number
        internal_exchange_id=1000,  # random number
        exchange_address=uniswap_v3.factory.address,
        fee=0.0005
    )


@pytest.fixture
def matic_usdc_trading_pair(
    matic_asset, usdc_asset, uniswap_v3
) -> TradingPairIdentifier:
    return TradingPairIdentifier(
        matic_asset,
        usdc_asset,
        "0xA374094527e1673A86dE625aa59517c5dE346d32",  #  https://tradingstrategy.ai/trading-view/polygon/uniswap-v3/matic-usdc-fee-5
        internal_id=1001,  # random number
        internal_exchange_id=1000,  # random number
        exchange_address=uniswap_v3.factory.address,
        fee=0.0005
    )


@pytest.fixture
def eth_matic_trading_pair(eth_asset, matic_asset, uniswap_v3) -> TradingPairIdentifier:
    """eth-usdc pair representation in the trade executor domain."""
    return TradingPairIdentifier(
        eth_asset,
        matic_asset,
        "0x86f1d8390222A3691C28938eC7404A1661E618e0",  #  https://tradingstrategy.ai/trading-view/polygon/uniswap-v3/matic-eth-fee-5
        internal_id=1002,  # random number
        internal_exchange_id=1000,  # random number
        exchange_address=uniswap_v3.factory.address,
        fee=0.0005
    )


@pytest.fixture
def pair_universe(
    eth_usdc_trading_pair, matic_usdc_trading_pair, eth_matic_trading_pair
) -> PandasPairUniverse:
    """Pair universe needed for the trade routing."""
    return create_pair_universe_from_code(
        ChainId.bsc,
        [eth_usdc_trading_pair, matic_usdc_trading_pair, eth_matic_trading_pair],
    )


@pytest.fixture()
def routing_model(usdc_asset):

    # for uniswap v3
    # same addresses for Mainnet, Polygon, Optimism, Arbitrum, Testnets Address
    # only celo different
    address_map = {
        "factory": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        "router": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
        "position_manager": "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
        "quoter": "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6"
        # "router02":"0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
        # "quoterV2":"0x61fFE014bA17989E743c5F6cB21bF9697530B21e"
    }

    allowed_intermediary_pairs = {
        # Route WMATIC through USDC:WMATIC fee 0.05% pool,
        # https://tradingstrategy.ai/trading-view/polygon/uniswap-v3/matic-usdc-fee-5
        "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270": "0xa374094527e1673a86de625aa59517c5de346d32",
        # Route WETH through USDC:WETH fee 0.05% pool,
        # https://tradingstrategy.ai/trading-view/polygon/uniswap-v3/eth-usdc-fee-5
        "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619": "0x45dda9cb7c25131df268515131f647d726f50608",
    }

    return UniswapV3SimpleRoutingModel(
        address_map,
        allowed_intermediary_pairs,
        reserve_token_address=usdc_asset.address,
    )


@pytest.fixture()
def execution_model(web3, hot_wallet) -> UniswapV3ExecutionModel:
    return UniswapV3ExecutionModel(web3, hot_wallet)


@pytest.fixture()
def portfolio(web3, hot_wallet, usdc_asset) -> Portfolio:
    """A portfolio synced to the hot wallet, starting with 10_000 usdc."""
    portfolio = Portfolio()
    events = sync_reserves(
        web3, datetime.datetime.utcnow(), hot_wallet.address, [], [usdc_asset]
    )
    assert len(events) > 0
    apply_sync_events(portfolio, events)
    reserve_currency, exchange_rate = portfolio.get_default_reserve_currency()
    assert reserve_currency == usdc_asset
    return portfolio


@pytest.fixture
def state(portfolio) -> State:
    """State used in the tests."""
    state = State(portfolio=portfolio)
    return state


# Flaky because Ganache hangs
@flaky.flaky()
def test_simple_routing_one_leg(
    web3,
    hot_wallet,
    usdc_asset,
    eth_token,
    routing_model,
    eth_usdc_trading_pair,
    pair_universe,
):
    """Make 1x two way trade usdc -> eth.

    - Buy eth with usdc
    """

    # Get live fee structure from polygon Chain
    fees = estimate_gas_fees(web3)

    # Prepare a transaction builder
    tx_builder = TransactionBuilder(
        web3,
        hot_wallet,
        fees,
    )

    # Create
    routing_state = UniswapV3RoutingState(pair_universe, tx_builder)

    txs = routing_model.trade(
        routing_state,
        eth_usdc_trading_pair,
        usdc_asset,
        100 * 10**6,  # Buy eth worth of 100 usdc,
        check_balances=True,
    )

    # We should have 1 approve, 1 swap
    assert len(txs) == 2

    # Execute
    tx_builder.broadcast_and_wait_transactions_to_complete(
        web3, txs, revert_reasons=True
    )

    # Check all transactions succeeded
    for tx in txs:
        assert tx.is_success(), f"Transaction failed: {tx}"

    # We received the tokens we bought
    assert eth_token.functions.balanceOf(hot_wallet.address).call() > 0


# Flaky because get_block("latest") issue on Anvil
@flaky.flaky()
def test_simple_routing_buy_sell(
    web3,
    hot_wallet,
    usdc_asset,
    eth_asset,
    eth_token,
    usdc_token,
    routing_model: UniswapV3SimpleRoutingModel,
    eth_usdc_trading_pair,
    pair_universe,
):
    """Make 2x two way trade usdc -> eth -> usdc."""

    # Get live fee structure from polygon Chain
    fees = estimate_gas_fees(web3)

    # Prepare a transaction builder
    tx_builder = TransactionBuilder(
        web3,
        hot_wallet,
        fees,
    )

    # Create
    routing_state = UniswapV3RoutingState(pair_universe, tx_builder)

    txs = routing_model.trade(
        routing_state,
        eth_usdc_trading_pair,
        usdc_asset,
        100 * 10**6,  # Buy eth worth of 100 usdc,
        check_balances=True,
    )

    # We should have 1 approve, 1 swap
    assert len(txs) == 2

    # Execute
    tx_builder.broadcast_and_wait_transactions_to_complete(
        web3, txs, revert_reasons=True
    )

    assert all(tx.is_success() for tx in txs)

    # We received the tokens we bought
    eth_balance = eth_token.functions.balanceOf(hot_wallet.address).call()

    # Sell eth we received
    txs = routing_model.trade(
        routing_state,
        eth_usdc_trading_pair,
        eth_asset,
        eth_balance,  # Sell all eth
        check_balances=True,
    )
    assert len(txs) == 2
    # Execute
    tx_builder.broadcast_and_wait_transactions_to_complete(
        web3, txs, revert_reasons=True
    )
    assert all(tx.is_success() for tx in txs)

    # We started with 10_000 usdc
    balance = usdc_token.functions.balanceOf(hot_wallet.address).call()
    assert balance == pytest.approx(9999900025)


def test_simple_routing_not_enough_balance(
    web3,
    hot_wallet,
    usdc_asset,
    routing_model,
    eth_usdc_trading_pair,
):
    """Try to buy, but does not have cash."""

    # Get live fee structure from polygon Chain
    fees = estimate_gas_fees(web3)

    # Prepare a transaction builder
    tx_builder = TransactionBuilder(
        web3,
        hot_wallet,
        fees,
    )

    # Create
    routing_state = UniswapV3RoutingState(pair_universe, tx_builder)

    with pytest.raises(OutOfBalance):
        routing_model.trade(
            routing_state,
            eth_usdc_trading_pair,
            usdc_asset,
            1_000_000_000 * 10**6,  # Buy eth worth of 10B usdc,
            check_balances=True,
        )


def test_simple_routing_three_leg(
    web3,
    hot_wallet,
    usdc_asset,
    matic_asset,
    eth_asset,
    eth_token,
    routing_model: UniswapV3SimpleRoutingModel,
    eth_matic_trading_pair,
    matic_usdc_trading_pair,
    pair_universe,
):
    """Make 1x two way trade usdc -> polygon -> eth."""

    # Get live fee structure from polygon Chain
    fees = estimate_gas_fees(web3)

    # Prepare a transaction builder
    tx_builder = TransactionBuilder(
        web3,
        hot_wallet,
        fees,
    )

    routing_state = UniswapV3RoutingState(pair_universe, tx_builder)

    txs = routing_model.trade(
        routing_state,
        eth_matic_trading_pair,
        usdc_asset,
        100 * 10**6,  # Buy eth worth of 100 usdc,
        check_balances=True,
        intermediary_pair=matic_usdc_trading_pair,
    )

    # We should have 1 approve, 1 swap
    assert len(txs) == 2

    # Execute
    tx_builder.broadcast_and_wait_transactions_to_complete(
        web3, txs, revert_reasons=True
    )

    # Check all transactions succeeded
    for tx in txs:
        assert tx.is_success(), f"Transaction failed: {tx}"

    # We received the tokens we bought
    assert eth_token.functions.balanceOf(hot_wallet.address).call() > 0


# web3.exceptions.BlockNotFound: Block with id: 'latest' not found.
@flaky.flaky()
def test_three_leg_buy_sell(
    web3,
    hot_wallet,
    usdc_asset,
    matic_asset,
    eth_asset,
    eth_token,
    usdc_token,
    routing_model: UniswapV3SimpleRoutingModel,
    eth_matic_trading_pair,
    matic_usdc_trading_pair,
    pair_universe,
):
    """Make trades usdc -> polygon -> eth and eth -> polygon -> usdc."""

    # We start without eth
    balance = eth_token.functions.balanceOf(hot_wallet.address).call()
    assert balance == 0

    # Get live fee structure from polygon Chain
    fees = estimate_gas_fees(web3)

    # Prepare a transaction builder
    tx_builder = TransactionBuilder(
        web3,
        hot_wallet,
        fees,
    )

    routing_state = UniswapV3RoutingState(pair_universe, tx_builder)

    txs = routing_model.trade(
        routing_state,
        eth_matic_trading_pair,
        usdc_asset,
        100 * 10**6,  # Buy eth worth of 100 usdc,
        check_balances=True,
        intermediary_pair=matic_usdc_trading_pair,
    )

    # We should have 1 approve, 1 swap
    assert len(txs) == 2

    # # Check for three legs
    # buy_tx = txs[1]
    # path = buy_tx.args[2]
    # assert len(path) == 3

    # Execute
    tx_builder.broadcast_and_wait_transactions_to_complete(
        web3, txs, revert_reasons=True
    )

    # Check all transactions succeeded
    for tx in txs:
        assert tx.is_success(), f"Transaction failed: {tx}"

    # We received the tokens we bought
    balance = eth_token.functions.balanceOf(hot_wallet.address).call()
    assert balance > 0

    txs = routing_model.trade(
        routing_state,
        eth_matic_trading_pair,
        eth_asset,
        balance,
        check_balances=True,
        intermediary_pair=matic_usdc_trading_pair,
    )

    # We should have 1 approve, 1 swap
    assert len(txs) == 2

    # Check for three legs
    # sell_tx = txs[1]
    # path = sell_tx.args[2]
    # assert len(path) == 3, f"Bad sell tx {sell_tx}"

    # Execute
    tx_builder.broadcast_and_wait_transactions_to_complete(
        web3, txs, revert_reasons=True
    )

    # Check all transactions succeeded
    for tx in txs:
        assert tx.is_success(), f"Transaction failed: {tx}"

    # We started with 10_000 usdc
    balance = usdc_token.functions.balanceOf(hot_wallet.address).call()
    assert balance == pytest.approx(9999800150)


def test_three_leg_buy_sell_twice_on_chain(
    web3,
    hot_wallet,
    usdc_asset,
    matic_asset,
    eth_asset,
    eth_token,
    usdc_token,
    routing_model,
    eth_matic_trading_pair,
    matic_usdc_trading_pair,
    pair_universe,
):
    """Make trades 2x usdc -> polygon -> eth and eth -> polygon -> usdc.

    Because we do the round trip 2x, we should not need approvals
    on the second time and we need one less transactions.

    We reset the routing state between, forcing
    the routing state to read the approval information
    back from the chain.
    """

    # Get live fee structure from polygon Chain
    fees = estimate_gas_fees(web3)

    # Prepare a transaction builder
    tx_builder = TransactionBuilder(
        web3,
        hot_wallet,
        fees,
    )

    routing_state = None

    def trip():

        txs = routing_model.trade(
            routing_state,
            eth_matic_trading_pair,
            usdc_asset,
            100 * 10**6,  # Buy eth worth of 100 usdc,
            check_balances=True,
            intermediary_pair=matic_usdc_trading_pair,
        )

        # Execute
        tx_builder.broadcast_and_wait_transactions_to_complete(
            web3, txs, revert_reasons=True
        )

        # Check all transactions succeeded
        for tx in txs:
            assert tx.is_success(), f"Transaction failed: {tx}"

        # We received the tokens we bought
        balance = eth_token.functions.balanceOf(hot_wallet.address).call()
        assert balance > 0

        txs2 = routing_model.trade(
            routing_state,
            eth_matic_trading_pair,
            eth_asset,
            balance,
            check_balances=True,
            intermediary_pair=matic_usdc_trading_pair,
        )

        # Execute
        tx_builder.broadcast_and_wait_transactions_to_complete(
            web3, txs2, revert_reasons=True
        )

        # Check all transactions succeeded
        for tx in txs2:
            assert tx.is_success(), f"Transaction failed: {tx}"

        return txs + txs2

    routing_state = UniswapV3RoutingState(pair_universe, tx_builder)
    txs_1 = trip()
    assert len(txs_1) == 4
    routing_state = UniswapV3RoutingState(pair_universe, tx_builder)
    txs_2 = trip()
    assert len(txs_2) == 2


def test_three_leg_buy_sell_twice(
    web3,
    hot_wallet,
    usdc_asset,
    matic_asset,
    eth_asset,
    eth_token,
    usdc_token,
    routing_model,
    eth_matic_trading_pair,
    matic_usdc_trading_pair,
    pair_universe,
):
    """Make trades 2x usdc -> polygon -> eth and eth -> polygon -> usdc.

    Because we do the round trip 2x, we should not need approvals
    on the second time and we need one less transactions.
    """

    # Get live fee structure from polygon Chain
    fees = estimate_gas_fees(web3)

    # Prepare a transaction builder
    tx_builder = TransactionBuilder(
        web3,
        hot_wallet,
        fees,
    )

    routing_state = UniswapV3RoutingState(pair_universe, tx_builder)

    def trip():

        txs = routing_model.trade(
            routing_state,
            eth_matic_trading_pair,
            usdc_asset,
            100 * 10**1,  # Buy eth worth of 100 usdc,
            check_balances=True,
            intermediary_pair=matic_usdc_trading_pair,
        )

        # Execute
        tx_builder.broadcast_and_wait_transactions_to_complete(
            web3, txs, revert_reasons=True
        )

        # Check all transactions succeeded
        for tx in txs:
            assert tx.is_success(), f"Transaction failed: {tx}"

        # We received the tokens we bought
        balance = eth_token.functions.balanceOf(hot_wallet.address).call()
        assert balance > 0

        txs2 = routing_model.trade(
            routing_state,
            eth_matic_trading_pair,
            eth_asset,
            balance,
            check_balances=True,
            intermediary_pair=matic_usdc_trading_pair,
        )

        # Execute
        tx_builder.broadcast_and_wait_transactions_to_complete(
            web3, txs2, revert_reasons=True
        )

        # Check all transactions succeeded
        for tx in txs2:
            assert tx.is_success(), f"Transaction failed: {tx}"

        return txs + txs2

    txs_1 = trip()
    assert len(txs_1) == 4
    txs_2 = trip()
    assert len(txs_2) == 2


# Flaky becaues Ganache hangs
@flaky.flaky()
def test_stateful_routing_three_legs(
    web3,
    pair_universe,
    hot_wallet,
    usdc_asset,
    matic_asset,
    eth_asset,
    eth_token,
    routing_model,
    eth_matic_trading_pair,
    matic_usdc_trading_pair,
    state: State,
    execution_model: UniswapV3ExecutionModel,
):
    """Perform 3-leg buy/sell using RoutingModel.execute_trades().

    This also shows how blockchain native transactions
    and state management integrate.
    """

    # Get live fee structure from polygon Chain
    fees = estimate_gas_fees(web3)

    # Prepare a transaction builder
    tx_builder = TransactionBuilder(web3, hot_wallet, fees)

    routing_state = UniswapV3RoutingState(pair_universe, tx_builder)

    trader = PairUniverseTestTrader(state)

    reserve = pair_universe.get_token(usdc_asset.address)
    if not reserve:
        all_tokens = pair_universe.get_all_tokens()
        assert (
            reserve
        ), f"Reserve asset {usdc_asset.address} missing in the universe {usdc_asset}, we have {all_tokens}"

    # Buy eth via usdc -> polygon pool for 100 USD
    trades = [trader.buy(eth_matic_trading_pair, Decimal(100))]

    t = trades[0]
    assert t.is_buy()
    assert t.reserve_currency == usdc_asset
    assert t.pair == eth_matic_trading_pair

    state.start_trades(datetime.datetime.utcnow(), trades)
    routing_model.execute_trades_internal(
        pair_universe, routing_state, trades, check_balances=True
    )
    execution_model.broadcast_and_resolve(state, trades, stop_on_execution_failure=True)

    # Check all all trades and transactions completed
    for t in trades:
        assert t.is_success()
        for tx in t.blockchain_transactions:
            assert tx.is_success()

    # We received the tokens we bought
    assert eth_token.functions.balanceOf(hot_wallet.address).call() > 0

    eth_position: TradingPosition = state.portfolio.open_positions[1]
    assert eth_position

    # Buy eth via usdc -> polygon pool for 100 USD
    trades = [trader.sell(eth_matic_trading_pair, eth_position.get_quantity())]

    t = trades[0]
    assert t.is_sell()
    assert t.reserve_currency == usdc_asset
    assert t.pair == eth_matic_trading_pair
    assert t.planned_quantity == -eth_position.get_quantity()

    state.start_trades(datetime.datetime.utcnow(), trades)
    routing_model.execute_trades_internal(
        pair_universe, routing_state, trades, check_balances=True
    )
    execution_model.broadcast_and_resolve(state, trades, stop_on_execution_failure=True)

    # Check all all trades and transactions completed
    for t in trades:
        assert t.is_success()
        for tx in t.blockchain_transactions:
            assert tx.is_success()

    # On-chain balance is zero after the sell
    assert eth_token.functions.balanceOf(hot_wallet.address).call() == 0


def test_stateful_routing_two_legs(
    web3,
    pair_universe,
    hot_wallet,
    usdc_asset,
    matic_asset,
    eth_asset,
    eth_token,
    routing_model,
    eth_usdc_trading_pair,
    state: State,
    execution_model: UniswapV3ExecutionModel,
):
    """Perform 2-leg buy/sell using RoutingModel.execute_trades().

    This also shows how blockchain native transactions
    and state management integrate.

    Routing is abstracted away - this test is not different from one above,
    except for the trading pair that we have changed.
    """

    # Get live fee structure from polygon Chain
    fees = estimate_gas_fees(web3)

    # Prepare a transaction builder
    tx_builder = TransactionBuilder(web3, hot_wallet, fees)

    routing_state = UniswapV3RoutingState(pair_universe, tx_builder)

    trader = PairUniverseTestTrader(state)

    # Buy eth via usdc -> polygon pool for 100 USD
    trades = [trader.buy(eth_usdc_trading_pair, Decimal(100))]

    t = trades[0]
    assert t.is_buy()
    assert t.reserve_currency == usdc_asset
    assert t.pair == eth_usdc_trading_pair

    state.start_trades(datetime.datetime.utcnow(), trades)
    routing_model.execute_trades_internal(
        pair_universe, routing_state, trades, check_balances=True
    )
    execution_model.broadcast_and_resolve(state, trades, stop_on_execution_failure=True)

    # Check all all trades and transactions completed
    for t in trades:
        assert t.is_success()
        for tx in t.blockchain_transactions:
            assert tx.is_success()

    # We received the tokens we bought
    assert eth_token.functions.balanceOf(hot_wallet.address).call() > 0

    eth_position: TradingPosition = state.portfolio.open_positions[1]
    assert eth_position

    # Buy eth via usdc -> polygon pool for 100 USD
    trades = [trader.sell(eth_usdc_trading_pair, eth_position.get_quantity())]

    t = trades[0]
    assert t.is_sell()
    assert t.reserve_currency == usdc_asset
    assert t.pair == eth_usdc_trading_pair
    assert t.planned_quantity == -eth_position.get_quantity()

    state.start_trades(datetime.datetime.utcnow(), trades)
    routing_model.execute_trades_internal(
        pair_universe, routing_state, trades, check_balances=True
    )
    execution_model.broadcast_and_resolve(state, trades, stop_on_execution_failure=True)

    # Check all all trades and transactions completed
    for t in trades:
        assert t.is_success()
        for tx in t.blockchain_transactions:
            assert tx.is_success()

    # On-chain balance is zero after the sell
    assert eth_token.functions.balanceOf(hot_wallet.address).call() == 0