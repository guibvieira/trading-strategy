"""Default protocols support for EVM blockchains.

See :py:mod:`tradeexecutor.strategy.generic.pair_configurator`.
"""

from typing import Set

from web3 import Web3
from eth_defi.aave_v3.deployment import AaveV3Deployment, fetch_deployment as fetch_aave_deployment
from eth_defi.uniswap_v3.deployment import UniswapV3Deployment, fetch_deployment as fetch_uniswap_v3_deployment
from eth_defi.one_delta.deployment import fetch_deployment as fetch_1delta_deployment

from tradeexecutor.ethereum.one_delta.one_delta_live_pricing import OneDeltaLivePricing
from tradeexecutor.ethereum.one_delta.one_delta_routing import OneDeltaRouting
from tradeexecutor.ethereum.one_delta.one_delta_valuation import OneDeltaPoolRevaluator
from tradeexecutor.ethereum.routing_data import uniswap_v3_address_map, create_uniswap_v2_compatible_routing
from tradeexecutor.ethereum.uniswap_v2.uniswap_v2_live_pricing import UniswapV2LivePricing
from tradeexecutor.ethereum.uniswap_v2.uniswap_v2_valuation import UniswapV2PoolRevaluator
from tradeexecutor.ethereum.uniswap_v3.uniswap_v3_live_pricing import UniswapV3LivePricing
from tradeexecutor.ethereum.uniswap_v3.uniswap_v3_routing import UniswapV3Routing
from tradeexecutor.ethereum.uniswap_v3.uniswap_v3_valuation import UniswapV3PoolRevaluator
from tradeexecutor.state.identifier import TradingPairIdentifier
from tradeexecutor.strategy.default_routing_options import TradeRouting
from tradeexecutor.strategy.generic.pair_configurator import PairConfigurator, ProtocolRoutingId, ProtocolRoutingConfig
from tradeexecutor.strategy.generic.routing_function import UnroutableTrade
from tradeexecutor.strategy.reserve_currency import ReserveCurrency
from tradeexecutor.strategy.trading_strategy_universe import TradingStrategyUniverse
from tradingstrategy.chain import ChainId
from tradingstrategy.exchange import ExchangeUniverse, ExchangeType, ExchangeNotFoundError


def get_exchange_type(
    exchange_universe: ExchangeUniverse,
    pair: TradingPairIdentifier,
) -> ExchangeType:
    assert pair.exchange_address is not None, f"Pair missing exchange_address: {pair}"
    exchange = exchange_universe.get_by_chain_and_factory(ChainId(pair.chain_id), pair.exchange_address)
    assert exchange is not None, f"Exchange address {pair.exchange_address} for pair {pair}: data not loaded"
    return exchange.exchange_type


def create_uniswap_v2_adapter(
    web3: Web3,
    strategy_universe: TradingStrategyUniverse,
    routing_id: ProtocolRoutingId,
) -> ProtocolRoutingConfig:
    assert routing_id.router_name == "uniswap-v2"
    assert len(strategy_universe.data_universe.chains) == 1
    assert len(strategy_universe.reserve_assets) == 1

    reserve = strategy_universe.reserve_assets[0]
    assert reserve.token_symbol == "USDC"

    exchange_universe = strategy_universe.data_universe.exchange_universe
    chain_id = strategy_universe.get_single_chain()

    exchange = exchange_universe.get_by_chain_and_slug(chain_id, routing_id.exchange_slug)

    if exchange.exchange_slug == "quickswap":
        routing_model = create_uniswap_v2_compatible_routing(
            TradeRouting.quickswap_usdc,
            ReserveCurrency.usdc,
        )
    else:
        raise NotImplementedError(f"Exchange not yet supported: {exchange}")

    pricing_model = UniswapV2LivePricing(
        web3,
        strategy_universe.data_universe.pairs,
        routing_model,
    )

    valuation_model = UniswapV2PoolRevaluator(pricing_model)

    return ProtocolRoutingConfig(
        routing_id=routing_id,
        routing_model=routing_model,
        pricing_model=pricing_model,
        valuation_model=valuation_model,
    )


def create_uniswap_v3_adapter(
    web3: Web3,
    strategy_universe: TradingStrategyUniverse,
    routing_id: ProtocolRoutingId,
) -> ProtocolRoutingConfig:
    """Always the same."""

    assert routing_id.router_name == "uniswap-v3"
    assert len(strategy_universe.data_universe.chains) == 1
    assert len(strategy_universe.reserve_assets) == 1
    chain_id = strategy_universe.get_single_chain()
    reserve_asset = strategy_universe.get_reserve_asset()

    exchange_universe = strategy_universe.data_universe.exchange_universe

    # TODO: Add intermediate tokens
    routing_model = UniswapV3Routing(
        address_map=uniswap_v3_address_map,
        chain_id=chain_id,
        reserve_token_address=reserve_asset.address,
        allowed_intermediary_pairs={},
    )

    pricing_model = UniswapV3LivePricing(
        web3,
        strategy_universe.data_universe.pairs,
        routing_model,
    )

    valuation_model = UniswapV3PoolRevaluator(pricing_model)

    return ProtocolRoutingConfig(
        routing_id=routing_id,
        routing_model=routing_model,
        pricing_model=pricing_model,
        valuation_model=valuation_model,
    )


def create_1delta_adapter(
    web3: Web3,
    strategy_universe: TradingStrategyUniverse,
    routing_id: ProtocolRoutingId,
) -> ProtocolRoutingConfig:

    assert routing_id.router_name == "1delta"
    assert routing_id.lending_protocol_slug == "aave"
    assert routing_id.exchange_slug == "uniswap-v3"

    assert len(strategy_universe.data_universe.chains) == 1
    assert len(strategy_universe.reserve_assets) == 1
    chain_id = strategy_universe.get_single_chain()
    reserve_asset = strategy_universe.get_reserve_asset()

    uniswap_v3_deployment = fetch_uniswap_v3_deployment(
        web3,
        "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        "0xE592427A0AEce92De3Edee1F18E0157C05861564",
        "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
        "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6",
    )

    aave_v3_deployment = fetch_aave_deployment(
        web3,
        pool_address="0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        data_provider_address="0x69FA688f1Dc47d4B5d8029D5a35FB7a548310654",
        oracle_address="0xb023e699F5a33916Ea823A16485e259257cA8Bd1",
    )

    one_delta_deployment = fetch_1delta_deployment(
        web3,
        flash_aggregator_address="0x74E95F3Ec71372756a01eB9317864e3fdde1AC53",
        broker_proxy_address="0x74E95F3Ec71372756a01eB9317864e3fdde1AC53",
    )

    address_map = {
        "one_delta_broker_proxy": one_delta_deployment.broker_proxy.address,
        "aave_v3_pool": aave_v3_deployment.pool.address,
        "aave_v3_data_provider": aave_v3_deployment.data_provider.address,
        "aave_v3_oracle": aave_v3_deployment.oracle.address,
        "factory": uniswap_v3_deployment.factory.address,
        "router": uniswap_v3_deployment.swap_router.address,
        "position_manager": uniswap_v3_deployment.position_manager.address,
        "quoter": uniswap_v3_deployment.quoter.address
    }

    # TODO: Add intermediate tokens
    routing_model = OneDeltaRouting(
        address_map=address_map,
        chain_id=chain_id,
        reserve_token_address=reserve_asset.address,
        allowed_intermediary_pairs={},
    )

    pricing_model = OneDeltaLivePricing(
        web3,
        strategy_universe.data_universe.pairs,
        routing_model,
    )

    valuation_model = OneDeltaPoolRevaluator(pricing_model)

    return ProtocolRoutingConfig(
        routing_id=routing_id,
        routing_model=routing_model,
        pricing_model=pricing_model,
        valuation_model=valuation_model,
    )


class EthereumPairConfigurator(PairConfigurator):

    def get_supported_routers(self) -> Set[ProtocolRoutingId]:
        exchanges = self.strategy_universe.data_universe.exchange_universe
        assert exchanges.get_exchange_count() < 5, "Exchanges might not be configured correctly"
        configs = set()
        for xc in exchanges.exchanges.values():
            configs.add(
                ProtocolRoutingId(
                    router_name="uniswap-v2" if xc.exchange_type == ExchangeType.uniswap_v2 else "uniswap-v3",
                    exchange_slug=xc.exchange_slug,
                )
            )

        configs.add(
            ProtocolRoutingId(
                router_name="1delta",
                exchange_slug="uniswap-v3",
                lending_protocol_slug="aave",
            )
        )
        return configs

    def create_config(self, routing_id: ProtocolRoutingId):
        if routing_id.router_name == "1delta":
            return create_1delta_adapter(self.web3, self.strategy_universe, routing_id)
        elif routing_id.router_name == "uniswap-v2":
            return create_uniswap_v2_adapter(self.web3, self.strategy_universe, routing_id)
        elif routing_id.router_name == "uniswap-v3":
            return create_uniswap_v3_adapter(self.web3, self.strategy_universe, routing_id)
        else:
            raise NotImplementedError(f"Cannot route exchange {routing_id}")

    def match_router(self, pair: TradingPairIdentifier) -> ProtocolRoutingId:

        assert isinstance(pair, TradingPairIdentifier)

        if pair.is_leverage() or pair.is_credit_supply():
            return ProtocolRoutingId(
                router_name="1delta",
                exchange_slug="uniswap-v3",
                lending_protocol_slug="aave"
            )

        pair_universe = self.strategy_universe.data_universe.pairs

        try:
            exchange = pair_universe.exchange_universe.get_by_chain_and_factory(
                ChainId(pair.chain_id),
                pair.exchange_address
            )
        except ExchangeNotFoundError as e:
            raise UnroutableTrade(
                f"Could not find exchange for pair: {pair}, exchange address {pair.exchange_address}.\n"
                f"We have data for {pair_universe.exchange_universe.get_exchange_count()} exchanges.\n"
            ) from e

        assert exchange is not None, \
            f"Loaded exchange data does not have exchange for pair {pair}, exchange address {pair.exchange_address}\n" \
            f"We have data for {pair_universe.exchange_universe.get_exchange_count()} exchanges"

        return ProtocolRoutingId(
            router_name="uniswap-v2" if exchange.exchange_type == ExchangeType.uniswap_v2 else "uniswap-v3",
            exchange_slug=exchange.exchange_slug,
        )