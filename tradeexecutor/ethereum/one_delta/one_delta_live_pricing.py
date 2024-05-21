"""1delta live pricing.

Currently subclass Uniswap v3 live pricing.
"""
import logging
import datetime
from decimal import Decimal
from typing import Optional, Dict

from web3 import Web3
from tradingstrategy.pair import PandasPairUniverse
from eth_defi.provider.broken_provider import get_block_tip_latency
from eth_defi.one_delta.price import OneDeltaPriceHelper, estimate_buy_received_amount, estimate_sell_received_amount
from eth_defi.one_delta.deployment import OneDeltaDeployment, fetch_deployment

from tradeexecutor.ethereum.one_delta.one_delta_execution import OneDeltaExecution
from tradeexecutor.ethereum.one_delta.one_delta_routing import OneDeltaRouting, get_one_delta
from tradeexecutor.ethereum.eth_pricing_model import EthereumPricingModel, LP_FEE_VALIDATION_EPSILON
from tradeexecutor.ethereum.uniswap_v3.uniswap_v3_routing import route_tokens
from tradeexecutor.ethereum.uniswap_v3.uniswap_v3_live_pricing import get_onchain_price
from tradeexecutor.state.identifier import TradingPairIdentifier
from tradeexecutor.strategy.execution_model import ExecutionModel
from tradeexecutor.state.types import USDollarAmount
from tradeexecutor.strategy.trading_strategy_universe import TradingStrategyUniverse
from tradeexecutor.strategy.trade_pricing import TradePricing

logger = logging.getLogger(__name__)


class OneDeltaLivePricing(EthereumPricingModel):
    """1delta live pricing."""

    def __init__(
        self,
        web3: Web3,
        pair_universe: PandasPairUniverse,
        routing_model: OneDeltaRouting,
        very_small_amount: float = Decimal("0.10"),
        epsilon: float | None = LP_FEE_VALIDATION_EPSILON,
    ):
        assert isinstance(routing_model, OneDeltaRouting)

        super().__init__(
            web3,
            pair_universe,
            routing_model,
            very_small_amount,
            epsilon
        )

        self.one_delta_deployment = get_one_delta(web3, routing_model.address_map)

    def get_uniswap(self, target_pair: TradingPairIdentifier):
        # not in use but this is abstract method
        return None

    def get_sell_price(
        self,
        ts: datetime.datetime,
        pair: TradingPairIdentifier,
        quantity: Optional[Decimal],
    ) -> TradePricing:
        """Get live price on Uniswap."""

        assert pair.is_spot()

        if quantity is None:
            quantity = Decimal(self.very_small_amount)

        assert isinstance(quantity, Decimal)

        target_pair, intermediate_pair = self.routing_model.route_pair(self.pair_universe, pair)

        base_addr, quote_addr, intermediate_addr = route_tokens(target_pair, intermediate_pair)

        # In three token trades, be careful to use the correct reserve token
        quantity_raw = target_pair.base.convert_to_raw_amount(quantity)

        reverse_token_order = target_pair.has_reverse_token_order()
        target_pair_fee = int(target_pair.fee * 1_000_000)

        if intermediate_pair:
            path = [base_addr, intermediate_addr, quote_addr] 
            fees = [intermediate_pair.fee, target_pair.fee]
            total_fee_pct = 1 - (1-fees[0]) * (1-fees[1])

            received_raw = estimate_sell_received_amount(
                one_delta_deployment=self.one_delta_deployment,
                base_token_address=base_addr,
                quote_token_address=quote_addr,
                quantity=quantity_raw,
                target_pair_fee=target_pair_fee,
                intermediate_token_address=intermediate_addr,
                intermediate_pair_fee=int(intermediate_pair.fee * 1_000_000) if intermediate_pair else None,
                block_identifier=block_number,
            )

            block_number = None
        else:
            path = [base_addr, quote_addr]
            fees = [target_pair.fee]
            total_fee_pct = 1 - (1 - target_pair.fee)

            web3 = self.web3
            block_number = max(1, web3.eth.block_number - get_block_tip_latency(web3))
            try:
                received_raw = estimate_sell_received_amount(
                    one_delta_deployment=self.one_delta_deployment,
                    base_token_address=base_addr,
                    quote_token_address=quote_addr,
                    quantity=quantity_raw,
                    target_pair_fee=target_pair_fee,
                    block_identifier=block_number,
                )
            except Exception as e:
                # Add more helpful debug context
                raise RuntimeError(f"Could not get valid price for {target_pair}\n{base_addr}-{quote_addr} with intermediate {intermediate_addr}, quantity:{quantity} fee:{target_pair_fee}") from e
        
        if intermediate_pair:
            received = intermediate_pair.quote.convert_to_decimal(received_raw)
            path = [intermediate_pair, target_pair]
            
        else:
            received = target_pair.quote.convert_to_decimal(received_raw)
            price = float(received / quantity)
            path = [target_pair]
        
        price = float(received / quantity)
        
        if intermediate_pair:
            mid_price = price / (1 - self.get_pair_fee(ts, target_pair)) / (1 - self.get_pair_fee(ts, intermediate_pair))
        else:
            # Read mid-price at the mid point of Uni v3 liquidity,
            # at our block number
            mid_price = get_onchain_price(
                self.web3,
                target_pair.pool_address,
                block_identifier=block_number,
                reverse_token_order=reverse_token_order,
            )
            mid_price = float(mid_price)
        
        assert price <= mid_price, f"Bad pricing: {price}, {mid_price}"

        lp_fee = float(quantity) * total_fee_pct

        # self.validate_mid_price_for_sell(lp_fee, mid_price, price, quantity)
        
        return TradePricing(
            price=price,
            mid_price=mid_price,
            lp_fee=[lp_fee],
            pair_fee=fees,
            side=False,
            path=path,
            read_at=datetime.datetime.utcnow(),
            block_number=block_number,
            token_in=quantity,
            token_out=received,
        )

    def get_buy_price(
        self,
        ts: datetime.datetime,
        pair: TradingPairIdentifier,
        reserve: Optional[Decimal],
    ) -> TradePricing:
        """Get live price on Uniswap.

        :param reserve:
            The buy size in quote token e.g. in dollars

        :return: Price for one reserve unit e.g. a dollar
        """

        assert pair.is_spot()

        if reserve is None:
            reserve = Decimal(self.very_small_amount)
        else:
            assert isinstance(reserve, Decimal), f"Reserve must be decimal, got {reserve.__class__}: {reserve}"

        target_pair, intermediate_pair = self.routing_model.route_pair(self.pair_universe, pair)

        base_addr, quote_addr, intermediate_addr = route_tokens(target_pair, intermediate_pair)

        reverse_token_order = target_pair.has_reverse_token_order()

        # In three token trades, be careful to use the correct reserve token
        if intermediate_pair is not None:
            reserve_raw = intermediate_pair.quote.convert_to_raw_amount(reserve)
            self.check_supported_quote_token(intermediate_pair)
            
            path = [quote_addr, intermediate_addr, base_addr]
            
            fees = [intermediate_pair.fee, target_pair.fee]
            
            total_fee_pct = 1 - (1 - fees[0]) * (1-fees[1])

            block_number = None
            token_raw_received = estimate_buy_received_amount(
                one_delta_deployment=self.one_delta_deployment,
                base_token_address=base_addr,
                quote_token_address=quote_addr,
                quantity=reserve_raw,
                target_pair_fee=int(target_pair.fee * 1_000_000),
                intermediate_token_address=intermediate_addr,
                intermediate_pair_fee=int(intermediate_pair.fee * 1_000_000) if intermediate_pair else None,
            )

        else:

            web3 = self.web3
            block_number = max(1, web3.eth.block_number - get_block_tip_latency(web3))

            reserve_raw = target_pair.quote.convert_to_raw_amount(reserve)
            self.check_supported_quote_token(pair)

            token_raw_received = estimate_buy_received_amount(
                one_delta_deployment=self.one_delta_deployment,
                base_token_address=base_addr,
                quote_token_address=quote_addr,
                quantity=reserve_raw,
                target_pair_fee=int(target_pair.fee * 1_000_000),
                intermediate_token_address=intermediate_addr,
                intermediate_pair_fee=int(intermediate_pair.fee * 1_000_000) if intermediate_pair else None,
                block_identifier=block_number,
            )

            path = [quote_addr, base_addr] 
            
            fees = [target_pair.fee]
            
            total_fee_pct = 1 - (1 - fees[0])

        token_received = target_pair.base.convert_to_decimal(token_raw_received)
        
        fee = self.get_pair_fee(ts, pair)
        assert fee is not None, "Uniswap v3 fee data missing"

        price = float(reserve / token_received)

        lp_fee = float(reserve) * total_fee_pct

        if intermediate_pair:        
            mid_price = price * (1 - self.get_pair_fee(ts, intermediate_pair)) * (1 - self.get_pair_fee(ts, target_pair))
            
            path = [intermediate_pair, target_pair]
        else:
            # Read mid-price at the mid point of Uni v3 liquidity,
            # at our block number
            mid_price = get_onchain_price(
                self.web3,
                target_pair.pool_address,
                block_identifier=block_number,
                reverse_token_order=reverse_token_order,
            )
            mid_price = float(mid_price)
            path = [target_pair]

        assert price >= mid_price, f"Bad pricing: {price}, {mid_price}"

        # self.validate_mid_price_for_buy(lp_fee, price, mid_price, reserve)
        
        return TradePricing(
            price=float(price),
            mid_price=float(mid_price),
            lp_fee=[lp_fee],
            pair_fee=fees,
            market_feed_delay=datetime.timedelta(seconds=0),
            side=True,
            path=path,
            read_at=datetime.datetime.utcnow(),
            block_number=block_number,
            token_in=reserve,
            token_out=token_received,
        )


def one_delta_live_pricing_factory(
    execution_model: ExecutionModel,
    universe: TradingStrategyUniverse,
    routing_model: OneDeltaRouting,
) -> OneDeltaLivePricing:
    assert isinstance(universe, TradingStrategyUniverse)
    assert isinstance(execution_model, OneDeltaExecution), f"Execution model not compatible with this execution model. Received {execution_model}"
    assert isinstance(routing_model, OneDeltaRouting), f"This pricing method only works with 1delta routing model, we received {routing_model}"

    return OneDeltaLivePricing(
        execution_model.web3,
        universe.data_universe.pairs,
        routing_model,
    )