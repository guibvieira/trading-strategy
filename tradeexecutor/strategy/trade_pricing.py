import datetime
from logging import getLogger
from dataclasses import dataclass
from typing import Optional, List

from tradeexecutor.state.identifier import TradingPairIdentifier
from tradeexecutor.state.types import USDollarAmount, BPS, USDollarPrice
from dataclasses_json import dataclass_json


logger = getLogger(__name__)


@dataclass_json
@dataclass(slots=True, frozen=True)
class TradePricing:
    """Describe price results for a price query.

    - Each price result is tied to quantiy/amount

    - Each price result gets a split that describes liquidity provider fees

    A helper class to deal with problems of accounting and estimation of prices on Uniswap like exchange.
    """

    #: The price we expect this transaction to clear.
    #:
    #: This price has LP fees already deducted away from it.
    #: It may or may not include price impact if liquidity data was available
    #: for the pricing model.
    price: USDollarPrice

    #: The "fair" market price during the transaction.
    #:
    #: This is the mid price - no LP fees, price impact,
    #: etc. included.
    mid_price: USDollarPrice

    #: How much liquidity provider fees we are going to pay on this trade.
    #:
    #: Set to None if data is not available.
    lp_fee: Optional[list[USDollarAmount]] = None

    #: What was the LP fee % used as the base of the calculations.
    #:
    pair_fee: Optional[list[BPS]] = None

    #: How old price data we used for this estimate
    #:
    market_feed_delay: Optional[datetime.timedelta] = None

    #: Is this buy or sell trade.
    #:
    #:
    #: True for buy.
    #: False for sell.
    #: None for Unknown.
    side: Optional[bool] = None
    
    #: Path of the trade
    #: One trade can have multiple swaps if there is an intermediary pair.
    path: Optional[List[TradingPairIdentifier]] = None

    def __repr__(self):
        fee_list = [fee or 0 for fee in self.pair_fee]
        return f"<TradePricing:{self.price} mid:{self.mid_price} fee:{format_fees_percentage(fee_list)}>"
    
    def __post_init__(self):
        """Validate parameters.

        Make sure we don't slip in e.g. NumPy types.
        """
        assert type(self.price) == float
        assert type(self.mid_price) == float
        
        if type(self.lp_fee) != list:
            object.__setattr__(self, 'lp_fee', [self.lp_fee])
        
        if type(self.pair_fee) != list:
            object.__setattr__(self, 'pair_fee', [self.pair_fee])
        
        assert [type(_lp_fee) in {float, type(None)} for _lp_fee in self.lp_fee], f"lp_fee must be provided as type list with float or NoneType elements. Got Got lp_fee: {self.lp_fee} {type(self.lp_fee)}"
        
        assert [type(_pair_fee) in {float, int, type(None)} for _pair_fee in self.pair_fee], f"pair_fee must be provided as a list with float, int, or NoneType elements. Got fee: {self.pair_fee} {type(self.pair_fee)} "
        
        if self.market_feed_delay is not None:
            assert isinstance(self.market_feed_delay, datetime.timedelta)

        # Do safety checks for the price calculation
        if self.side is not None:
            if self.side:
                assert self.price >= self.mid_price, f"Got bad buy pricing: {self.price} > {self.mid_price}"
            if not self.side:
                assert self.price <= self.mid_price, f"Got bad sell pricing: {self.price} < {self.mid_price}"
                
        if self.path:
            assert [type(address) == TradingPairIdentifier for address in self.path], "path must be provided as a list of TradePairIdentifier" 
    
    def get_total_lp_fees(self):
        """Returns the total lp fees paid (dollars) for the trade."""
        return sum(filter(None,self.lp_fee))
    
    def get_fee_percentage(self):
        """Returns a single decimal value for the percentage of fees paid. 
        This calculation represents the average of all the pair fees"""
        # TODO verify calculation

        if all(self.pair_fee):
            return sum(self.pair_fee)/len(self.pair_fee)
        else:
            return 0 


def format_fees_percentage(fees: list[BPS]) -> str:
    """Returns string of formatted fees
    
    e.g. fees = [0.03, 0.005]
    => 0.3000% 0.0500%
    
    :param fees:
        list of lp fees in float (multiplier) format
        
    :returns:
        formatted str
    """
    _fees = [fee or 0 for fee in fees]
    strFormat = len(_fees) * '{:.4f}% '
    return strFormat.format(*_fees)
    
    
def format_fees_dollars(fees: list[USDollarAmount] | USDollarAmount) -> str:
    """Returns string of formatted fees
    
    :param fees:
        Can either be a list of fees or a single fee
    
    e.g. fees = [30, 50]
    => $30.00 $50.00
    
    :param fees:
        list of fees paid in absolute value (dollars)
    
    :returns:
        formatted str
    """
    
    if type(fees) != list:
        return f"${fees:.2f}"
    
    _fees = [fee or 0 for fee in fees]
    strFormat = len(_fees) * '${:.2f} '
    return strFormat.format(*_fees)