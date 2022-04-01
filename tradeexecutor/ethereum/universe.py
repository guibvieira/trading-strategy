"""Reverse engineering Trading Strategy trading universe from the local EVM tester Uniswap v2 deployment."""
from typing import List

import pandas as pd
from web3 import Web3

from eth_defi.uniswap_v2.deployment import UniswapV2Deployment
from tradeexecutor.state.state import TradingPairIdentifier
from tradingstrategy.chain import ChainId
from tradingstrategy.exchange import ExchangeUniverse, Exchange, ExchangeType
from tradingstrategy.pair import DEXPair, PandasPairUniverse, PairType


def create_pair_universe(web3: Web3, exchange: Exchange, pairs: List[TradingPairIdentifier]) -> PandasPairUniverse:
    """Creates a PairUniverse from Trade Executor test data.

    PairUniverse is used by QSTrader based tests, so we need to support it.
    """

    chain_id = ChainId(web3.eth.chain_id)

    data = []
    for p in pairs:
        dex_pair = DEXPair(
            pair_id=int(p.get_identifier(), 16),
            chain_id=chain_id,
            exchange_id=exchange.exchange_id,
            address=p.get_identifier(),
            dex_type=PairType.uniswap_v2,
            base_token_symbol=p.base.token_symbol,
            quote_token_symbol=p.quote.token_symbol,
            token0_symbol=p.base.token_symbol,
            token1_symbol=p.quote.token_symbol,
            token0_address=p.base.address,
            token1_address=p.quote.address,
            flag_inactive=False,
            flag_blacklisted_manually=False,
            flag_unsupported_quote_token=False,
            flag_unknown_exchange=False,
        )
        data.append(dex_pair.to_dict())
    df = pd.DataFrame(data)
    return PandasPairUniverse(df)


def create_exchange_universe(web3: Web3, uniswaps: List[UniswapV2Deployment]) -> ExchangeUniverse:
    """Create an exchange universe with a list of Uniswap v2 deployments."""

    exchanges = {}
    chain_id = ChainId(web3.eth.chain_id)
    for u in uniswaps:
        e = Exchange(
            chain_id=chain_id,
            chain_slug="tester",
            exchange_id=int(u.factory.address, 16),
            exchange_slug="uniswap_tester",
            address=u.factory,
            exchange_type=ExchangeType.uniswap_v2,
            pair_count=99999,
        )
        exchanges[e.exchange_id] = e
    return ExchangeUniverse(exchanges=exchanges)

