import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

from packages import Position, RenkoCandle, PullbackTrailingStop

logger = logging.getLogger(__name__)


class BaseDecisionMaker(ABC):
    @abstractmethod
    async def decide(self, *args, **kwargs) -> Any:
        ...


class DecisionMaker_v1(BaseDecisionMaker):
    '''
        Модуль принятия решений для текущей стратегии. Выдаёт решения: Ignore, Buy, Sell, Update, Delete (если ордер не исполнился,
        а позицию локально мы завели).
        ------------------------------------------------------------
        buy_threshold: float - порог вероятности, выше которого идём в сделку
        sell_threshold: float - порог вероятности, выше которого продаём
        min_volume: float - минимальный объём последней свечи, ниже которого сигнал игнорируется 
    '''
    def __init__(self, buy_threshold: float, sell_threshold: float, min_volume: float = 10000):
        self._min_volume = min_volume
        self._prev_renko = None
        self._buy_threshold = buy_threshold
        self._sell_threshold = sell_threshold

    async def decide(self, ticker: str, position: Optional[Position], buy_predict: list, sell_predict: list, renko: RenkoCandle, trailing: PullbackTrailingStop) -> str:
        if not position:
            if renko.duration == 0 or renko.v*renko.c < self._min_volume or buy_predict[1] < self._buy_threshold or sell_predict[1] >= self._sell_threshold or renko.cc == 'r':
                return 'Ignore'
            
            if buy_predict[1] >= self._buy_threshold:
                return 'Buy'
            
            else:
                logger.error(f'Позиции нет, при этом ни одно из условий не выполнилось {ticker, position, buy_predict, sell_predict, renko, trailing}')
                return 'Ignore'
            
        # if ((trailing.prev_stop and trailing.current_stop is None) or sell_predict[1] >= self._sell_threshold) and position.status == 'OPEN':
        #     return 'Sell'
        
        # elif ((trailing.prev_stop and trailing.current_stop is None) or sell_predict[1] >= self._sell_threshold) and position.status != 'OPEN':
        #     return 'Delete'
        if sell_predict[1] >= self._sell_threshold and position.status == 'OPEN':
            return 'Sell'
        
        elif sell_predict[1] >= self._sell_threshold and position.status != 'OPEN':
            return 'Delete'

        return 'Update'
