from datetime import datetime, timedelta
from typing import List, Tuple, Optional

from packages import ClassicCandle, RenkoCandle, RenkoList, TickerInfo, Trade

from candle_worker import BybitCandleWorker


class Initializer:
    '''
        Модуль для инициализации тикера или обновления информации о нём.
        -----------------------------------------------------------------------
        ticker: str - тикер
        candle_worker: BybitCandleWorker - модуль для отправки запросов к бирже, должен иметь методы get_ticker_info и get_candles_until_trade.
        category: str - linear/spot/inverse/anything else
    '''
    def __init__(self, ticker: str, candle_worker: BybitCandleWorker, category: str):
        self._candle_worker = candle_worker
        self._last_nodata_update = None
        self._ticker = ticker
        self._category = category

    def _build_renko_from_classic(self, classic_candles: List[ClassicCandle], last_trade: Trade) -> RenkoList:
        total_trades = []
        for candle in classic_candles:
            total_trades.extend(candle.trades)

        total_trades.append(last_trade)
        first_trade = total_trades[0]
        current_renko = RenkoCandle(
            datetime=datetime.fromtimestamp(first_trade.ts / 1000), 
            prev_l=first_trade.p, 
            prev_h=first_trade.p, 
            last_trade=first_trade, 
            symbol=self._ticker
            )
        
        result_renko = RenkoList([])

        for trade in total_trades:
            new_candles = current_renko + trade
            new_candles.share_volume_and_duration()
            current_renko = new_candles[-1]
            result_renko.extend(new_candles[:-1])
        
        result_renko.append(current_renko)
        return result_renko
    
    async def initialize(self, last_trade: Trade) -> Tuple[RenkoList | str, RenkoCandle | str, Optional[TickerInfo]]:
        if self._last_nodata_update:
            if datetime.now() - self._last_nodata_update <= timedelta(hours=72):
                return 'NODATA', 'NODATA', None
            
        ticker_info = await self.get_ticker_info()
        classic_candles = await self._candle_worker.get_candles_until_trade(ticker_info=ticker_info, last_trade=last_trade)
        historical_renko = self._build_renko_from_classic(classic_candles=classic_candles, last_trade=last_trade)
        del classic_candles

        if len(historical_renko) > 0:
            return historical_renko[:-1], historical_renko[-1], ticker_info
        
        else:
            self._last_nodata_update = datetime.now()
            return 'NODATA', 'NODATA', ticker_info
        
    async def get_ticker_info(self) -> TickerInfo:
        ticker_info = await self._candle_worker.get_ticker_info(ticker=self._ticker, category=self._category)
        return ticker_info
