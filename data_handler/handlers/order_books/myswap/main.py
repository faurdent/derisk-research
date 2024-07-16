import asyncio
import math
from decimal import Decimal
from pathlib import Path

from db.crud import DBConnector
from db.models import OrderBookModel
from handlers.blockchain_call import func_call
from handlers.order_books.abstractions import OrderBookBase

import pandas as pd

from handlers.order_books.commons import get_logger
from handlers.order_books.myswap.api_connection.api_connector import MySwapAPIConnector


MYSWAP_CL_MM_ADDRESS = "0x01114c7103e12c2b2ecbd3a2472ba9c48ddcbf702b1c242dd570057e26212111"

# The maximum tick value available in liqmap.json.gz from MySwap Data Service
MAX_MYSWAP_TICK = Decimal("1774532")


class MySwapOrderBook(OrderBookBase):
    """Class for MySwap order book."""
    DEX = "MySwap"
    MYSWAP_URL = "https://myswap-cl-charts.s3.amazonaws.com/data/pools/{pool_id}/liqmap.json.gz"

    def __init__(self, base_token: str, quote_token: str, apply_filtering: bool = False):
        """
        Initialize the MySwap order book.
        :param base_token: str - The base token address in hexadecimal.
        :param quote_token: str - The quote token address in hexadecimal.
        :param apply_filtering: bool - Apply filtering to the order book.
        """
        super().__init__(base_token, quote_token)
        self.connector = MySwapAPIConnector()
        self.apply_filtering = apply_filtering
        self.logger = get_logger("MySwap", Path.cwd().joinpath("./logs"))
        self.base_token_name = self.get_token_configs()[0].name
        self._decimals_diff = Decimal(10 ** (self.token_a_decimal - self.token_b_decimal))

    def _read_liquidity_data(self, pool_id: str) -> pd.DataFrame:
        """
        Read liquidity data from the MySwap data service.
        :param pool_id: str - The pool id in hexadecimal.
        :return: pd.DataFrame - The liquidity data.
        The structure of the data:
        Columns: tick(numpy.int64), liq(numpy.int64)
        """
        return pd.read_json(self.MYSWAP_URL.format(pool_id=pool_id), compression="gzip")

    async def _async_fetch_price_and_liquidity(self) -> None:
        """Fetch price and liquidity data from the MySwap CLMM service."""
        all_pools = self.connector.get_pools_data()
        filtered_pools = self._filter_pools_data(all_pools)
        for pool in filtered_pools:
            await self._calculate_order_book(pool["poolkey"])

    def fetch_price_and_liquidity(self) -> None:
        """Sync wrapper for the async fetch_price_and_liquidity method."""
        asyncio.run(self._async_fetch_price_and_liquidity())

    def _filter_pools_data(self, all_pools: dict) -> list:
        """
        Filter pools data based on the token pair.
        :param all_pools: dict - All pools data.
        :return: list - Pools for current pair.
        """
        return list(filter(
            lambda pool: pool["token0"]["address"] == self.token_a and pool["token1"]["address"] == self.token_b,
            all_pools["pools"]
        ))

    def _get_ticks_range(self) -> tuple[Decimal, Decimal]:
        """
        Get ticks range based on the current price range.
        return: tuple[Decimal, Decimal] - The minimum and maximum ticks.
        """
        price_range_from, price_range_to = self.calculate_price_range()
        return self._price_to_tick(price_range_from), self._price_to_tick(price_range_to)

    def _price_to_tick(self, price: Decimal) -> Decimal:
        """
        Convert price to MySwap tick.
        :param price: Decimal - The price to convert.
        :return: Decimal - The unsigned tick value.
        Signed tick calculation formula:
        round(log(price / (2 ** 128 * decimals_diff)) / log(1.0001))
        Formula was derived from provided in tick_to_price.
        """
        signed_tick = round(
            Decimal(math.log(
                price / (Decimal(2 ** 128) * self._decimals_diff))
            ) / Decimal(math.log(Decimal("1.0001")))
        )
        return Decimal(signed_tick) + MAX_MYSWAP_TICK

    async def _calculate_order_book(self, pool_id: str) -> None:
        # Obtain liquidity data and tick
        data = self._read_liquidity_data(pool_id)
        if data.empty:
            self.logger.info("No liquidity data for the pool.")
            return
        current_tick = await func_call(MYSWAP_CL_MM_ADDRESS, "current_tick", [pool_id])
        if not current_tick:
            self.logger.info("Couldn't get current tick.")
            return
        current_tick = current_tick[0]

        # Set prices boundaries in ticks
        self.current_price = self.tick_to_price(current_tick)
        min_tick, max_tick = self._get_ticks_range()

        # Prepare data for processing
        if self.apply_filtering:
            data = data.loc[data["tick"].between(min_tick, max_tick, inclusive="right")]
        asks, bids = data[data["tick"] >= current_tick], data[data["tick"] < current_tick]
        bids = bids.sort_values("tick", ascending=True)

        # Add asks and bids to the order book
        self.add_bids(pool_bids=bids)
        self.add_asks(pool_asks=asks, pool_liquidity=bids.iloc[0]["liq"])

    def add_asks(self, pool_asks: pd.DataFrame, pool_liquidity: Decimal) -> None:
        """
        Add asks data to the order book.
        :param pool_asks: pd.DataFrame - Asks in the pool.
        :param pool_liquidity: Decimal - The pool liquidity.
        """
        if pool_asks.empty:
            return
        local_asks = []
        next_tick = Decimal(pool_asks.iloc[0]['tick'].item())
        next_price = self.tick_to_price(next_tick)
        y = self._get_token_amount(
            current_liq=pool_liquidity,
            current_sqrt=self.current_price.sqrt(),
            next_sqrt=next_price.sqrt(),
            is_ask=False
        )
        local_asks.append((next_price, y))
        for index, bid_info in enumerate(pool_asks.iloc[::-1].itertuples(index=False)):
            if index == 0:
                continue
            current_tick = Decimal(pool_asks.iloc[index - 1]['tick'].item())
            current_price = self.tick_to_price(current_tick)
            y = self._get_token_amount(
                current_liq=Decimal(pool_asks.iloc[index - 1]['liq'].item()),
                current_sqrt=current_price.sqrt(),
                next_sqrt=Decimal(self.tick_to_price(pool_asks.iloc[index]['tick'].item())).sqrt(),
                is_ask=False
            )
            local_asks.append((current_price, y))
        self.asks.extend(local_asks)

    def add_bids(self, pool_bids: pd.DataFrame) -> None:
        """
        Add asks data to the order book.
        :param pool_bids: pd.DataFrame - Bids in the pool.
        """
        if pool_bids.empty:
            return
        local_bids = []
        next_tick = Decimal(pool_bids.iloc[0]['tick'].item())
        next_price = self.tick_to_price(next_tick)
        y = self._get_token_amount(
            current_liq=Decimal(pool_bids.iloc[0]['liq'].item()),
            current_sqrt=self.current_price.sqrt(),
            next_sqrt=next_price.sqrt(),
            is_ask=False
        )
        local_bids.append((next_price, y))
        for index, bid_info in enumerate(pool_bids.iloc[::-1].itertuples(index=False)):
            if index == 0:
                continue
            current_tick = Decimal(pool_bids.iloc[index - 1]['tick'].item())
            current_price = self.tick_to_price(current_tick)
            y = self._get_token_amount(
                current_liq=Decimal(pool_bids.iloc[index]['liq'].item()),
                current_sqrt=current_price.sqrt(),
                next_sqrt=Decimal(self.tick_to_price(pool_bids.iloc[index]['tick'].item())).sqrt(),
                is_ask=False
            )
            local_bids.append((current_price, y))
        self.bids.extend(local_bids)

    def _get_token_amount(
            self, current_liq: Decimal, current_sqrt: Decimal, next_sqrt: Decimal, is_ask: bool = True
    ) -> Decimal:
        """
        Calculate token amount based on liquidity data and current data processed(asks/bids).
        :param current_liq: Decimal - Current price liquidity
        :param current_sqrt: Decimal - Current square root of a price
        :param next_sqrt: Decimal - Next square root of a price
        :param is_ask: bool - True if an ask data
        :return: Decimal - token amount
        """
        if is_ask and (current_sqrt == 0 or next_sqrt == 0):
            raise ValueError("Square root of prices for asks can't be zero.")
        if not is_ask and next_sqrt == 0:
            return abs(current_liq / current_sqrt) / self._decimals_diff
        amount = abs(current_liq / next_sqrt - current_liq / current_sqrt)
        return amount / self._decimals_diff

    def tick_to_price(self, tick: Decimal) -> Decimal:
        """
        Convert tick value to price.
        :param tick: Decimal - Tick value
        Formula derived from base Uniswap V3 formula - 1.0001 ** tick. Ticks in MySwap are unsigned values,
        so we convert them to signed by subtracting max tick.
        """
        return Decimal("1.0001") ** (tick - MAX_MYSWAP_TICK) * Decimal(2 ** 128) * self._decimals_diff

    def calculate_liquidity_amount(self, tick, liquidity_pair_total) -> Decimal:
        sqrt_ratio = self.get_sqrt_ratio(tick)
        liquidity_delta = liquidity_pair_total / (sqrt_ratio / Decimal(2 ** 128))
        return liquidity_delta / 10 ** self.token_a_decimal


if __name__ == '__main__':
    order_book = MySwapOrderBook(
        "0x4718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d",
        "0x53c91253bc9682c04929ca02ed00b3e423f6710d2ee7e0d5ebb06f3ecf368a8",
        apply_filtering=True,
    )
    order_book.fetch_price_and_liquidity()
    order_book_serialized = order_book.serialize().model_dump()
    connector = DBConnector()
    connector.write_to_db(OrderBookModel(**order_book_serialized))