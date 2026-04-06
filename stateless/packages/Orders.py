import logging
import struct
import traceback
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, Optional 

logger = logging.getLogger(__name__)


@dataclass
class BaseMarketOrder(ABC):
    '''
        Базовый объект рыночного ордера. Может ещё 100 раз поменяться, так как я не совсем продумал, какие общие для разных бирж должны быть поля.
        ----------------------------------------------
        ticker: str - тикер
        side: str - Buy или Sell
        size: float - размер ордера в USDT или другой парной валюте
        request_price: float - Какова была цена в момент создания ордера
        stop_loss: float - какой хотим SL
        market: bool - рыночный ли ордер (название само отвечает на этот вопрос, но опять же, поля могут 100 раз измениться)
        create_time: datetime - текущее время, т.е. врвемя создания ордера
        filled_time: datetime - время исполнения ордера
        filled_price: float - средняя цена исполнения ордера
    '''
    ticker: str
    side: str
    size: float
    request_price: float
    stop_loss: float
    market: bool = True
    create_time: datetime = datetime.now()
    filled_time: Optional[datetime] = None
    filled_price: float = 0
    
    def dict(self) -> Dict:
        return asdict(self)
    
    @abstractmethod
    async def execute(self, *args, **kwargs):
        ...

    @abstractmethod
    def __bytes__(self):
        ...

    @abstractmethod
    def from_bytes(self):
        ...

    @property
    def is_market_order(self) -> bool:
        return True


@dataclass
class BybitMarketOrder(BaseMarketOrder):
    '''
        Рыночный ордер для байбита. Содержит логику отправки ордера.
        --------------------------------------------------------
        category: str - linear/spot/inverse/anything else
        is_leverage: bool - с плечами ли ордер. На байбите плечи настраиваются отдельно для каждого тикера и один раз
        slippage_tolerance: float - допустимое проскальзывание в процентах
        position_id: int - порядковый номер позиции, служебное поле для идентификации ордеров в database-worker и последующей их сборки в сделку
        is_final: bool - закрывает ли сделку ордер
    '''
    category: str = 'linear'
    is_leverage: bool = True
    slippage_tolerance: float = 0.3
    position_id: int = -1
    is_final: bool = False
    
    async def execute(self, session: Any) -> bool:
        if self.side == 'Buy':
            order = {
                'category': self.category,
                'symbol': self.ticker,
                'side': self.side,
                'orderType': 'Market' if self.market else 'Limit',
                'qty': self.size,
                'slippageToleranceType': 'percent',
                'slippageTolerance': str(self.slippage_tolerance),
                # 'stopLoss': str(self.stop_loss),
                # 'slTriggerBy': 'LastPrice',
                'isLeverage': 1 if self.is_leverage else 0,
                # 'tpslMode': 'Full'
            }

        elif self.side == 'Sell':
            order = {
                'category': self.category,
                'symbol': self.ticker,
                'side': self.side,
                'orderType': 'Market' if self.market else 'Limit',
                'qty': self.size,
                'isLeverage': 1 if self.is_leverage else 0,
                'reduceOnly': True
            }

        try:
            result = await session.place_order(**order)
            if result.get('retMsg')== 'OK':
                return True
            else:
                logger.error(f'Ошибка при отправке ордера по тикеру {order["symbol"]}: {result}')
                return False
        
        except Exception as e:
            logger.error(f'Произошла ошибка при отправке ордера по тикеру {self.ticker}: {e}')
            logger.error(traceback.format_exc())
            return False

    def __bytes__(self) -> bytes:
        ts = int(self.create_time.timestamp() * 1000)
        filled_ts = int(self.filled_time.timestamp() * 1000) if self.filled_time else 0
        
        ticker_bytes = self.ticker.encode('utf-8')
        side_bytes = self.side.encode('utf-8')
        category_bytes = self.category.encode('utf-8')
        
        ticker_len = len(ticker_bytes)
        side_len = len(side_bytes)
        category_len = len(category_bytes)
        
        header = struct.pack(
            '<QQdddd???ddHHH',
            ts,
            filled_ts,
            self.size,
            self.request_price,
            self.stop_loss,
            self.filled_price,
            self.market,
            self.is_leverage,
            self.is_final,
            self.slippage_tolerance,
            self.position_id,
            ticker_len,
            side_len,
            category_len
        )
        
        return header + ticker_bytes + side_bytes + category_bytes
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'BybitMarketOrder':
        header_fmt = '<QQdddd???ddHHH'
        header_size = struct.calcsize(header_fmt)
        
        unpacked = struct.unpack(header_fmt, data[:header_size])
        
        (
            ts,
            filled_ts,
            size,
            request_price,
            stop_loss,
            filled_price,
            market,
            is_leverage,
            is_final,
            slippage_tolerance,
            position_id,
            ticker_len,
            side_len,
            category_len
        ) = unpacked
        
        pos = header_size
        
        ticker_bytes = data[pos:pos + ticker_len]
        pos += ticker_len
        ticker = ticker_bytes.decode('utf-8')
        
        side_bytes = data[pos:pos + side_len]
        pos += side_len
        side = side_bytes.decode('utf-8')
        
        category_bytes = data[pos:pos + category_len]
        category = category_bytes.decode('utf-8')
        
        create_time = datetime.fromtimestamp(ts / 1000.0)
        filled_time = datetime.fromtimestamp(filled_ts / 1000.0) if filled_ts else None
        
        return cls(
            ticker=ticker,
            side=side,
            size=size,
            request_price=request_price,
            stop_loss=stop_loss,
            market=market,
            create_time=create_time,
            filled_time=filled_time,
            filled_price=filled_price,
            category=category,
            is_leverage=is_leverage,
            is_final=is_final,
            slippage_tolerance=slippage_tolerance,
            position_id=position_id
        )


@dataclass
class BybitUpdateOrder:
    '''
        Ордер для обновления позиции на байбите. Используется для управления стоп-лоссом.
        ------------------------------------------------------------
        ticker: str - тикер
        category: str - linear/spot/inverse/anything else
        new_sl: float - новое значение стоп-лосса. Не может быть меньше текущего, в противном случае игнорируется
        position_id: int - порядковый номер позиции, служебное поле для идентификации ордеров в database-worker и последующей их сборки в сделку
    '''
    ticker: str
    category: str
    new_sl: float
    position_id: int = -1
    is_final: bool = False

    async def execute(self, session: Any) -> bool:
        order = {
            'category': self.category,
            'symbol': self.ticker,
            'tpslMode': 'Full',
            'positionIdx': 0,
            'stopLoss': str(self.new_sl),
            'slTriggerBy': 'LastPrice'
        }

        try:
            result = await session.set_trading_stop(**order)
            if result.get('retMsg') == 'OK':
                return True
            else:
                logger.error(f'Не обновил стоп {result}')
                return False
        
        except Exception as e:
            if 'zero position' in str(e):
                return True
            logger.error(f'Ошибка при отправке ордера на обновление позиции по тикеру {self.ticker}: {e}')
            return False
        
    def __bytes__(self) -> bytes:
        ticker_bytes = self.ticker.encode('utf-8')
        category_bytes = self.category.encode('utf-8')
        
        ticker_len = len(ticker_bytes)
        category_len = len(category_bytes)
        
        header = struct.pack(
            '<ddHH',
            self.new_sl,
            self.position_id,
            ticker_len,
            category_len
        )
        
        return header + ticker_bytes + category_bytes
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'BybitUpdateOrder':
        header_fmt = '<ddHH'
        header_size = struct.calcsize(header_fmt)
        
        unpacked = struct.unpack(header_fmt, data[:header_size])
        
        (
            new_sl,
            position_id,
            ticker_len,
            category_len
        ) = unpacked
        
        pos = header_size
        
        ticker_bytes = data[pos:pos + ticker_len]
        pos += ticker_len
        ticker = ticker_bytes.decode('utf-8')
                
        category_bytes = data[pos:pos + category_len]
        category = category_bytes.decode('utf-8')
        
        return cls(
            ticker=ticker,
            position_id=position_id,
            new_sl=new_sl,
            category=category
        )

    @property
    def is_market_order(self) -> bool:
        return False
