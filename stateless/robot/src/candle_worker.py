import asyncio
import logging
import traceback
from datetime import datetime
from typing import List

from packages import ClassicCandle, TickerInfo, Trade
from pybit.asyncio.unified_trading import AsyncHTTP

logger = logging.getLogger(__name__)


class BybitCandleWorker:
    '''
        Собирает классические свечи и информацию о тикерах с байбита, устанавливает margin mode (Isolated/Cross/Portfolio)
        -------------------------------------------------------------------------------------------
        session: AsyncHTTP - сессия со встроенным ограничителем запросов к бирже
        timeframe: int - длительность свечек в минутах
        num_requests: int - сколько запросов по num_candles_per_request сделать. Произведение этих параметров даст необходимое число свечек
        num_candles_per_request: int - сколько свечек брать за один запрос. Байбит отдаёт не более 1000
    '''
    def __init__(self, session: AsyncHTTP, timeframe: int = 1, num_requests: int = 30, num_candles_per_request: int = 1000):
        self._session = session
        self._timeframe = timeframe
        self._num_requests = num_requests
        self._num_candles_per_request = num_candles_per_request
        self._timeframe_map = {1440: 'D', 10080: 'W', 40320: 'M'}
        if self._timeframe not in [1, 3, 5, 15, 30, 60, 120, 240, 360, 720, 1440, 10080, 40320]:
            raise Exception(f'Таймфрейм должен быть одним из следующих чисел: {[1, 3, 5, 15, 30, 60, 120, 240, 360, 720, 1440, 10080, 40320]}')
    
    async def get_ticker_info(self, ticker: str, category: str) -> TickerInfo:
        try:
            for attempt in range(100):
                try:
                    result = await self._session.get_instruments_info(category=category, symbol=ticker)
                    if result.get('retMsg') == 'OK':
                        break
                    logger.error(f'Не добыл инфу по {ticker} {result.get("retMsg")}')

                except asyncio.TimeoutError:
                    logger.error(f'Ошибка: Не добыл инфу по {ticker} Timeout')
                
                except Exception as e:
                    logger.error(f'Ошибка: Не добыл инфу по {ticker} {e}')
                    logger.error(traceback.format_exc())

                wait_time = 5 * (2 ** min(attempt, 8))
                await asyncio.sleep(wait_time)

            data = result.get('result', {}).get('list', [])[0]
            min_order_qty = data.get('lotSizeFilter', {}).get('minOrderQty', 0)
            max_market_order_qty = data.get('lotSizeFilter', {}).get('maxMktOrderQty', 0)
            max_order_qty = data.get('lotSizeFilter', {}).get('maxOrderQty', 0)
            scale = data.get('priceScale', 0)
            is_prelisting = data.get('isPreListing')

            ticker_info = TickerInfo(
                name=ticker,
                category=category,
                min_order_qty=float(min_order_qty),
                max_market_order_qty=float(max_market_order_qty),
                max_order_qty=float(max_order_qty),
                scale=int(scale),
                is_prelisting=is_prelisting
            )

            return ticker_info
        
        except Exception as e:
            logger.error(f'Ошибка при добыче инфы по тикеру {ticker}: {e}')
            logger.error(traceback.format_exc())

    async def get_candles_until_trade(self, ticker_info: TickerInfo, last_trade: Trade) -> List[ClassicCandle]:
        try:
            last_candle_ts = last_trade.ts - (last_trade.ts % (self._timeframe * 60000)) - self._timeframe * 60000
            timeframe_str = str(self._timeframe) if self._timeframe not in [1440, 10080, 40320] else self._timeframe_map[self._timeframe]

            tasks = []

            for i in range(self._num_requests):
                end = last_candle_ts - i * self._num_candles_per_request * self._timeframe * 60000
                start = end - self._timeframe * self._num_candles_per_request * 60000
                tasks.append(self.request_candles(start=start, end=end, ticker_info=ticker_info, timeframe_str=timeframe_str))

            candles = []

            results = await asyncio.gather(*tasks)

            for result in results:
                candles.extend(result)
            
            candles.sort(key=lambda x: x.datetime)
            return candles
        
        except Exception as e:
            logger.error(f'Ошибка при добыче свечек по тикеру {ticker_info.name}: {e}')
            logger.error(traceback.format_exc())

    async def request_candles(self, start: int, end: int, ticker_info: TickerInfo, timeframe_str: str):
        for attempt in range(100):
            try:
                result = await self._session.get_kline(
                    category=ticker_info.category,
                    symbol=ticker_info.name,
                    interval=str(timeframe_str),
                    start=start,
                    end=end,
                    limit=self._num_candles_per_request
                )
                if result.get('retMsg') == 'OK':
                    break
                logger.error(f'Не пришли свечки {ticker_info.name} {result.get("retMsg")}')

            except asyncio.TimeoutError:
                logger.error('Ошибка: Не пришли свечки Timeout')

            except Exception as e:
                logger.error(f'Ошибка: Не пришли свечки {ticker_info.name} {e}')
                logger.error(traceback.format_exc())

            wait_time = 5 * (2 ** min(attempt, 8))
            await asyncio.sleep(wait_time)
            
        data = result.get('result', {}).get('list', [])
        candles = [
            ClassicCandle(
                *[
                    datetime.fromtimestamp(int(candle[0]) / 1000),
                    float(candle[1]),
                    float(candle[2]),
                    float(candle[3]),
                    float(candle[4]),
                    float(candle[5])
                ], symbol=ticker_info.name
            ) for candle in data
        ]
        return candles
    
    async def set_margin_mode(self, mode: str) -> bool | dict:
        for attempt in range(100):
            try:
                result = await self._session.set_margin_mode(setMarginMode=mode)
                if result.get('retMsg') == 'Request accepted':
                    return True
                logger.error(f'Не поставил маржу {result.get("retMsg")}')

            except asyncio.TimeoutError:
                logger.error('Ошибка: Не поставил маржу Timeout')
                
            except Exception as e:
                logger.error(f'Ошибка: Не поставил маржу {e}')
                logger.error(traceback.format_exc())

            wait_time = 5 * (2 ** min(attempt, 8))
            await asyncio.sleep(wait_time)
        
        logger.error('За 100 попыток ничего')
        return result
    