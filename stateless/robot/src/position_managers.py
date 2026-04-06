import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional

from packages import BybitMarketOrder, BybitUpdateOrder, JetStreamPublisher, Position, Position_v1, PullbackTrailingStop, RenkoCandle, TickerInfo


logger = logging.getLogger(__name__)


class BasePositionManager(ABC):
    '''
        Базовый класс для управления позициями. Здесь реализована логика отправки ордеров в брокер для БД
        ---------------------------------------------------
        publisher: JetStreamPublisher - модуль для отправки сообщений в NATS JetStream. Должен иметь запущенный стрим
    '''
    def __init__(self, publisher: JetStreamPublisher):
        self._positions = {}
        self._publisher = publisher
        self._total_positions = 0
    
    def get_position(self, ticker: str) -> Optional[Position]:
        return self._positions.get(ticker)
    
    @abstractmethod
    async def prepare_order(self, *args, **kwargs):
        ...

    @abstractmethod
    async def create_position(self, *args, **kwargs) -> None:
        ...

    @abstractmethod
    async def update_position(self, *args, **kwargs) -> None:
        ...

    @abstractmethod
    async def close_position(self, *args, **kwargs) -> None:
        ...

    async def _publish(self, position: Position) -> None:
        tasks = []
        for order in position.history:
            order.position_id = self._total_positions
            tasks.append(asyncio.create_task(self._publisher.publish('orders', bytes(order))))
        
        await asyncio.gather(*tasks)

    
class PositionManager_v1(BasePositionManager):
    '''
        Логика управления позициями для текущей стратегии. Управляет локальными позициями (после получения подтверждения об исполнении ордера на бирже),
        умеет подготавливать ордера к отправке.
        -------------------------------------------------------------------
        publisher: JetStreamPublisher - отправляет сообщения в NATS JetStream. Передаётся в базовый класс.
        category: str - linear/spot/inverse/anything else 
        default_size: float - Размер позиции в USDT по умолчанию. Этот размер будет включать в себя плечи 
        (т.е. для плеча 5 реальная доля собственных средств в позиции будет 600 USDT).
    '''
    def __init__(self, publisher: JetStreamPublisher, category: str = 'linear', default_size: float = 3000):
        super().__init__(publisher=publisher)
        self._default_size = default_size
        self._category = category

    def update_position(self, ticker: str, update_order: BybitUpdateOrder) -> bool:
        if not self._positions.get(ticker):
            logger.error(f'Хотел обновить позу по {ticker}, но ее не существует')
            return False
        
        if self._positions[ticker].status in ['OPENORDERSENT', 'CLOSEORDERSENT']:
            logger.warning(f'Позиция по тикеру {ticker} имеет статус {self._positions[ticker].status}, а я ее обновляю')
            return False
        
        self._positions[ticker].update(update_order=update_order)
        return True

    def create_position(self, ticker: str, entry_order: BybitMarketOrder) -> bool:
        if not self._positions.get(ticker):
            logger.error(f'Ошибка при создании позиции по {ticker}: ордер пришел, а поза не забронировалась')
            return False
        
        self._positions[ticker].open(order=entry_order)
        return True

    async def close_position(self, ticker: str, close_order: BybitMarketOrder) -> bool:
        if not self._positions.get(ticker):
            logger.error(f'Нет позиции по тикеру {ticker}, но пришел закрывающий ордер')
            return False
        
        if self._positions[ticker].status not in ['OPEN', 'UPDATEORDERSENT']:
            logger.error(f'По тикеру {ticker} пришёл закрывающий ордер на еще не открытую позицию')
            return False
        
        self._positions[ticker].close(order=close_order)
        await self._publish(position=self._positions[ticker])
        del self._positions[ticker]
        self._total_positions += 1
        return True
    
    def _process_buy(self, renko: Optional[RenkoCandle], ticker: str, ticker_info: TickerInfo, stop_loss_pct) -> Optional[BybitMarketOrder]:
        if renko is None:
            logger.error('В менеджер позиции передал решение о покупке и не передал свечу')
            return
        
        if len(self._positions.keys()) < 5:
            self._positions[ticker] = Position_v1(ticker=ticker, stop_loss=0)
            return ticker_info.prepare_buy_order(requested_qty=self._default_size, requested_price=renko.c, stop_loss_pct=stop_loss_pct)

    def _process_update(self, ticker: str, ticker_info: TickerInfo, stop_loss_pct: float, trailing: Optional[PullbackTrailingStop]) -> Optional[BybitUpdateOrder]:
        if not self._positions.get(ticker):
            logger.error(f'Пришло решение обновить несуществующую позицию {ticker}')
            return

        if self._positions[ticker].status != 'OPEN':
            return
        
        elif round(self._positions[ticker].entry_order.filled_price * (1 - stop_loss_pct / 100), ticker_info.scale) != self._positions[ticker].stop_loss and not self._positions[ticker].init_stop_set:
            self._positions[ticker].status = 'UPDATEORDERSENT'
            self._positions[ticker].init_stop_set = True
            return ticker_info.prepare_update_order(new_price=self._positions[ticker].entry_order.filled_price * (1 - stop_loss_pct / 100))
        
        elif trailing.current_stop is None:
            return
        
        elif round(trailing.current_stop, ticker_info.scale) <= round(self._positions[ticker].stop_loss, ticker_info.scale):
            return
            
        self._positions[ticker].status = 'UPDATEORDERSENT'
        return ticker_info.prepare_update_order(new_price=trailing.current_stop)

    def _process_sell(self, renko: Optional[RenkoCandle], ticker: str, ticker_info: TickerInfo) -> Optional[BybitMarketOrder]:
        if renko is None:
            logger.error('В менеджер позиции отдал решение о продаже и не отдал свечу')
            return
        
        self._positions[ticker].status = 'CLOSEORDERSENT'
        return ticker_info.prepare_sell_order(requested_qty=self._positions[ticker].size, requested_price=renko.c)

    def prepare_order(self, decision: str, ticker_info: TickerInfo, stop_loss_pct: float = 2.0, renko: Optional[RenkoCandle] = None, trailing: Optional[PullbackTrailingStop] = None):
        ticker = ticker_info.name

        if decision == 'Buy':
            return self._process_buy(renko=renko, ticker=ticker, ticker_info=ticker_info, stop_loss_pct=stop_loss_pct)
        
        elif decision == 'Update':
            return self._process_update(ticker=ticker, ticker_info=ticker_info, stop_loss_pct=stop_loss_pct, trailing=trailing)
            
        elif decision == 'Ignore':
            return
        
        elif decision == 'Sell' and self._positions.get(ticker):
            return self._process_sell(renko=renko, ticker=ticker, ticker_info=ticker_info)

        elif decision == 'Delete':
            del self._positions[ticker]
        
        else:
            logger.error(f'Не сработало ни одно условие в подготовке ордера: {ticker, decision, trailing, renko}')
