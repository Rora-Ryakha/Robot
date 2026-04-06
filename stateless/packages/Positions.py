import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .Orders import BybitMarketOrder, BybitUpdateOrder

logger = logging.getLogger(__name__)


@dataclass
class Position(ABC):
    '''
        Базовый класс позиции. 
        -------------------------------
        ticker: str - тикер
        entry_order: BaseMarketOrder - ордер, инициировавший позицию
        exit_order: BaseMarketOrder - ордер, закрывший позицию
        history: list - история позиции, состоящая в хронологической последовательности связанных с ней ордеров
    '''
    ticker: str
    entry_order = None
    exit_order = None
    history: list = field(default_factory=list)

    @property
    def entry_time(self) -> Optional[datetime]:
        if not self.entry_order:
            return 0
        return self.entry_order.filled_time
    
    @property
    def entry_price(self) -> float:
        if not self.entry_order:
            return 0
        return self.entry_order.filled_price
    
    @property
    def exit_time(self) -> Optional[datetime]:
        if not self.exit_order:
            return 0
        if self.exit_order:
            return self.exit_order.filled_time
        
    @property
    def exit_price(self) -> Optional[float]:
        if not self.exit_order:
            return 0
        if self.exit_order:
            return self.exit_order.filled_price
        
    @property
    def pnl(self) -> Optional[float]:
        if not self.exit_order or not self.entry_order:
            return 0
        if self.exit_order:
            return self.exit_order.filled_price - self.entry_order.filled_price
        
    @abstractmethod
    def open(self, *args, **kwargs) -> None:
        ...

    @abstractmethod
    def update(self, *args, **kwargs) -> None:
        ...

    @abstractmethod
    def close(self, *args, **kwargs) -> None:
        ...


@dataclass
class Position_v1(Position):
    '''
        Текущий вариант позиции для этой стратегии. Как и с ордерами, всё может меняться.
        --------------------------------------------------------
        stop_loss: float - текущая цена SL
        status: OPEN/OPENORDERSENT/UPDATEORDERSENT/CLOSEORDERSENT/CLOSED - статус позиции
        init_stop_set - служебное поле для проверки установленности начального стопа
    '''
    stop_loss: float = 0
    status: str = 'OPENORDERSENT'
    init_stop_set: bool = False

    @property
    def size(self) -> float:
        if self.history[-1].is_market_order:
            return self.history[-1].size
        return 0
    
    def open(self, order: BybitMarketOrder) -> None:
        if self.entry_order:
            logger.error(f'Пришел ордер на открытие позиции по {self.ticker}, но он уже имеется')
            return
        
        self.entry_order = order
        self.history.append(order)
        self.status = 'OPEN'

    def update(self, update_order: BybitUpdateOrder) -> None:
        sl = update_order.new_sl
        if self.stop_loss < sl:
            self.stop_loss = max(sl, self.stop_loss)
            self.history.append(update_order)
            self.status = 'OPEN'

    def close(self, order: BybitMarketOrder) -> None:
        if not self.exit_order:
            self.exit_order = order
            order.is_final = True
            self.history.append(order)
            self.status = 'CLOSED'
        
        else:
            logger.error(f'Ошибка при закрытии позиции: ордер на закрытие уже существует {self.ticker}')

    def __repr__(self):
        return str((self.ticker, self.history, self.status))
