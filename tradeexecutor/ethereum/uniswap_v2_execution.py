"""Execution model where trade happens directly on Uniswap v2 style exchange."""

import datetime
from decimal import Decimal
from typing import List, Tuple
import logging

from eth_defi.hotwallet import HotWallet
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment
from tradeexecutor.ethereum.execution import approve_tokens, prepare_swaps, confirm_approvals, broadcast, \
    wait_trades_to_complete, resolve_trades, broadcast_and_resolve
from tradeexecutor.ethereum.tx import TransactionBuilder
from tradeexecutor.ethereum.uniswap_v2_routing import UniswapV2SimpleRoutingModel, UniswapV2RoutingState
from tradeexecutor.state.freeze import freeze_position_on_failed_trade
from tradeexecutor.state.state import State
from tradeexecutor.state.trade import TradeExecution
from tradeexecutor.strategy.execution_model import ExecutionModel
from tradeexecutor.strategy.trading_strategy_universe import TradingStrategyUniverse

logger = logging.getLogger(__name__)


class UniswapV2RoutingInstructions:
    """Helper class to router Uniswap trades.

    - Define allowed routes to use

    - Define routing for three way trades
    """

    def __init__(self, routing_table: dict):
        """

        :param routing_table: Exchange factory address -> router address data
        """


class UniswapV2ExecutionModelVersion(ExecutionModel):
    """Run order execution on a single Uniswap v2 style exchanges.

    TODO: This model was used in the first prototype and later discarded.
    """

    def __init__(self,
                 uniswap: UniswapV2Deployment,
                 hot_wallet: HotWallet,
                 min_balance_threshold=Decimal("0.5"),
                 confirmation_block_count=6,
                 confirmation_timeout=datetime.timedelta(minutes=5),
                 stop_on_execution_failure=True):
        """
        :param state:
        :param uniswap:
        :param hot_wallet:
        :param min_balance_threshold: Abort execution if our hot wallet gas fee balance drops below this
        :param confirmation_block_count: How many blocks to wait for the receipt confirmations to mitigate unstable chain tip issues
        :param confirmation_timeout: How long we wait transactions to clear
        :param stop_on_execution_failure: Raise an exception if any of the trades fail top execute
        """
        self.web3 = uniswap.web3
        self.uniswap = uniswap
        self.hot_wallet = hot_wallet
        self.stop_on_execution_failure = stop_on_execution_failure
        self.min_balance_threshold = min_balance_threshold
        self.confirmation_block_count = confirmation_block_count
        self.confirmation_timeout = confirmation_timeout

    @property
    def chain_id(self) -> int:
        """Which chain the live execution is connected to."""
        return self.web3.eth.chain_id

    def preflight_check(self):
        """Check that we can connect to the web3 node"""

        # Check JSON-RPC works
        assert self.web3.eth.block_number > 1

        # Check we have money for gas fees
        balance = self.hot_wallet.get_native_currency_balance(self.web3)
        assert balance > self.min_balance_threshold, f"At least {self.min_balance_threshold} native currency need, our wallet {self.hot_wallet.address} has {balance:.8f}"

        # Check Uniswap v2 instance is valid.
        # Different factories (Sushi, Pancake) share few common public accessors we can call here.
        try:
            self.uniswap.factory.functions.allPairsLength().call()
        except Exception as e:
            raise AssertionError(f"Uniswap does not function at chain {self.chain_id}, factory address {self.uniswap.factory.address}") from e

    def initialize(self):
        """Set up the wallet"""
        logger.info("Initialising Uniswap v2 execution model")
        self.hot_wallet.sync_nonce(self.web3)
        balance = self.hot_wallet.get_native_currency_balance(self.web3)
        logger.info("Our hot wallet is %s with nonce %d and balance %s", self.hot_wallet.address, self.hot_wallet.current_nonce, balance)

    def execute_trades(self,
                       ts: datetime.datetime,
                       universe: TradingStrategyUniverse,
                       state: State,
                       trades: List[TradeExecution],
                       routing_model: UniswapV2SimpleRoutingModel):
        """Execute the trades determined by the algo on a designed Uniswap v2 instance.

        :return: Tuple List of succeeded trades, List of failed trades
        """
        assert isinstance(ts, datetime.datetime)
        assert isinstance(routing_model, UniswapV2SimpleRoutingModel)
        assert isinstance(universe, TradingStrategyUniverse)

        tx_builder = TransactionBuilder(self.hot_wallet)
        routing_state = UniswapV2RoutingState(tx_builder)
        routing_model.execute_trades(universe, routing_state, trades)
        broadcast_and_resolve(trades)

        # Clean up failed trades
        freeze_position_on_failed_trade(ts, state, trades)

