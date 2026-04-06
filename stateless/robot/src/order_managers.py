import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional

from packages import BybitMarketOrder, BybitUpdateOrder
from pybit.asyncio.unified_trading import AsyncHTTP

logger = logging.getLogger(__name__)


class BaseOrderManager(ABC):
    @abstractmethod
    async def execute(self, *args, **kwargs) -> None:
        ...


class BybitOrderManager(BaseOrderManager):
    '''
        Отправляет ордера байбита и устанавливает плечи.
        -------------------------------------------
        session: AsyncHTTP - сессия байбита со встроенным ограничителем запросов
    '''
    def __init__(self, session: AsyncHTTP):
        self._session = session

    async def execute(self, order: Optional[BybitMarketOrder | BybitUpdateOrder]) -> None:
        result = await order.execute(session=self._session)
        if not result:
            logger.error('Не вышло отправить ордер')

    async def set_leverage(self, ticker: str, category: str, leverage: float) -> bool | dict:
        for attempt in range(100):
            result = {}
            try:
                result = await self._session.set_leverage(category=category, symbol=ticker, buyLeverage=str(leverage), sellLeverage=str(leverage))
                if result.get('retMsg') == 'OK':
                    return True
                if result.get('retCode') != 10002:
                    logger.error(f'Не дал поставить плечо {ticker} {result}')

            except asyncio.TimeoutError:
                logger.error(f'Ошибка: Не дал поставить плечо {ticker} Timeout')
                
            except Exception as e:
                if 'leverage not modified' in str(e):
                    return True
                logger.error(f'Ошибка: Не дал поставить плечо {ticker} {e}')

            wait_time = 5 * (2 ** min(attempt, 8))
            await asyncio.sleep(wait_time)
        
        return result
