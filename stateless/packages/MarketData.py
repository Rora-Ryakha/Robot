import math
import json
import struct
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Literal, List, Optional, Union

from .Orders import BybitMarketOrder, BybitUpdateOrder


@dataclass
class Trade:
    '''
        Объект сделки. Сделки можно сложить - на выходе получится сделка с суммированным объёмом складываемых и последней ценой.
        ------------------------------
        ts: unix-таймстамп сделки в миллисекундах
        p: цена сделки
        v: объём сделки
        s: тикер
    '''
    ts: int
    p: float
    v: float
    s: str

    def __str__(self) -> str:
        return f"{self.ts},{self.p},{self.v},{self.s}"
    
    def __add__(self, other: 'Trade') -> 'Trade':
        if self.ts != other.ts or self.s != other.s:
            return NotImplemented
        return Trade(ts=self.ts, p=other.p, v=self.v+other.v, s=self.s)
    
    def __lt__(self, other: 'Trade') -> bool:
        return self.p < other.p
    
    def __le__(self, other: 'Trade') -> bool:
        return self.p <= other.p
    
    def __bytes__(self) -> bytes:
        s_bytes = self.s.encode('utf-8')
        s_len = len(s_bytes)
        header = struct.pack('<QddH', self.ts, self.p, self.v, s_len)
        return header + s_bytes
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'Trade':
        header_fmt = '<QddH'
        header_size = struct.calcsize(header_fmt)
        ts, p, v, s_len = struct.unpack(header_fmt, data[:header_size])
        s = data[header_size:header_size + s_len].decode('utf-8')
        
        return cls(ts=ts, p=p, v=v, s=s)


@dataclass
class RenkoCandle:
    '''
        Объект Ренко-свечи. Может быть в незавершённом виде. Завершается при h = 1.01 * o или l = 0.99 * o. Можно складывать с объектом Trade. На выходе получим
        RenkoList, содержащий обновленную незавершённую свечу, либо содержащий получившиеся в результате сделки новые свечи и незавершённую.
        ---------------------------------------------------------------
        datetime: datetime начала свечи
        prev_l: float - low предыдущей свечи
        prev_h: float - high предыдущей свечи
        last_trade: Trade - последняя сделка в свече
        symbol: str - тикер
        o, h, l, c, v в представлении не нуждаются, float
        size: float - размер свечи. Если size = 0.01, то свеча закрывается при достижении +- 1% от цены открытия
        duration: float - сколько минут продлилась свеча
        buy, sell: bool - бинарный предикт, покупать или продавать по закрытию свечи
        buy_proba, sell_proba: list - вероятности вида [вероятность False, вероятность True] для покупки и продажи соответственно
    '''
    datetime: datetime
    prev_l: float
    prev_h: float
    last_trade: Trade
    symbol: str
    o: float = 0
    h: float = 0
    l: float = 0
    c: float = 0
    v: float = 0
    size: float = 0.01
    duration: float = 0
    buy: bool = False
    sell: bool = False
    buy_proba: float = 0.0
    sell_proba: float = 0.0

    @property
    def ts(self) -> Any:
        return self.datetime

    @property
    def open(self) -> float:
        return self.o
    
    @property
    def close(self) -> float:
        return self.c
    
    @property
    def low(self) -> float:
        return self.l
    
    @property
    def high(self) -> float:
        return self.h
    
    @property
    def volume(self) -> float:
        return self.v
    
    @property
    def color(self) -> str:
        return self.cc

    @property
    def cc(self) -> Optional[Literal["g", "r"]]:
        return "g" if self.o <= self.c else "r"

    @property
    def is_complete(self) -> bool:
        return (self.h >= self.prev_h * (1 + self.size)) or (self.l <= self.prev_l * (1 - self.size))

    def __add__(self, trade: Trade) -> 'RenkoList':
        result = []
        self.last_trade = trade
        self.v += trade.v
        if (trade.p < (self.prev_h * (1 + self.size))) and (trade.p > (self.prev_l * (1 - self.size))):
            result.append(self)

        elif trade.p >= self.prev_h * (1 + self.size):
            self.o = self.l = self.prev_h
            self.c = self.h = self.prev_h * (1 + self.size)
            result.append(self)
            new_candles = RenkoCandle(datetime=datetime.fromtimestamp(trade.ts / 1000.0), prev_l=self.l, prev_h=self.h, symbol=trade.s, last_trade=trade) + trade
            result.extend(new_candles)
        
        elif trade.p <= self.prev_l * (1 - self.size):
            self.o = self.h = self.prev_l
            self.c = self.l = self.prev_l * (1 - self.size)
            result.append(self)
            new_candles = RenkoCandle(datetime=datetime.fromtimestamp(trade.ts / 1000.0), prev_l=self.l, prev_h=self.h, symbol=trade.s, last_trade=trade) + trade
            result.extend(new_candles)

        return RenkoList(result)
    
    def __getitem__(self, key: str) -> Any:
        if hasattr(self.__class__, key) and isinstance(getattr(self.__class__, key), property):
            return getattr(self, key)
        
        if hasattr(self, key):
            return getattr(self, key)
        
        raise KeyError(f"'{key}' не найден в RenkoCandle. Доступные ключи: {list(self.keys())}")
   
    def __bytes__(self) -> bytes:
        ts = int(self.datetime.timestamp() * 1000)
        
        symbol_bytes = self.symbol.encode('utf-8')
        symbol_len = len(symbol_bytes)
        
        trade_bytes = bytes(self.last_trade)
        trade_len = len(trade_bytes)

        header = struct.pack(
            '<QdddddddddddHH',
            ts,
            self.prev_l,
            self.prev_h,
            self.o,
            self.h,
            self.l,
            self.c,
            self.v,
            self.size,
            self.duration,
            self.buy_proba,
            self.sell_proba,
            symbol_len,
            trade_len
        )
        
        return header + symbol_bytes + trade_bytes

    @classmethod
    def from_bytes(cls, data: bytes) -> 'RenkoCandle':
        header_fmt = '<QdddddddddddHH'
        header_size = struct.calcsize(header_fmt)
        
        (
            ts, prev_l, prev_h, o, h, l, c, v, size, duration, buy_proba, sell_proba,
            symbol_len, trade_len
        ) = struct.unpack(header_fmt, data[:header_size])
        
        symbol_start = header_size
        symbol_end = symbol_start + symbol_len
        symbol = data[symbol_start:symbol_end].decode('utf-8')
        
        trade_data = data[symbol_end:symbol_end + trade_len]
        last_trade = Trade.from_bytes(trade_data)
        
        dt = datetime.fromtimestamp(ts / 1000.0)
        
        return cls(
            datetime=dt,
            prev_l=prev_l,
            prev_h=prev_h,
            last_trade=last_trade,
            symbol=symbol,
            o=o,
            h=h,
            l=l,
            c=c,
            v=v,
            size=size,
            duration=duration,
            buy_proba=buy_proba,
            sell_proba=sell_proba
        )


@dataclass
class RenkoList:
    '''
        Объект списка RenkoCandle. Помимо хранения свечек в списке, умеет распределять объёмы, длительности и таймстампы между свечами,
        сгенерированными одной сделкой (т.е. одна сделка пробила цену настолько сильно, что сгенерировала сразу несколько свечей в результате
        ее сложения с незавершённой).
    '''
    candles: List[RenkoCandle] = field(default_factory=list)
    
    def __getitem__(self, index: Union[int, slice]) -> Union[RenkoCandle, 'RenkoList']:
        if isinstance(index, slice):
            return RenkoList(self.candles[index])
        
        return self.candles[index]
    
    def __len__(self) -> int:
        return len(self.candles)
    
    def __iter__(self):
        return iter(self.candles)
    
    def __contains__(self, item: RenkoCandle) -> bool:
        return item in self.candles
    
    def append(self, candle: RenkoCandle):
        self.candles.append(candle)
    
    def extend(self, candles: Union[List[RenkoCandle], 'RenkoList']):
        self.candles.extend(candles)
    
    def insert(self, index: int, candle: RenkoCandle):
        self.candles.insert(index, candle)
    
    def pop(self, index: int = -1) -> RenkoCandle:
        return self.candles.pop(index)
    
    def share_volume_and_duration(self):
        if len(self.candles) == 1:
            return 
        start = self.candles[0].datetime
        end = self.candles[-1].datetime
        volume_per_renko = self.candles[-1].last_trade.v / len(self.candles)
        datetime_step = (end - start) / (len(self.candles) - 1)
        
        for i, candle in enumerate(self.candles):
            self.candles[i].v = candle.v - candle.last_trade.v + volume_per_renko
            if candle.is_complete:
                self.candles[i].datetime = start + datetime_step * i
                if i == len(self.candles) - 2:
                    self.candles[i].duration = (self.candles[-1].datetime - self.candles[-2].datetime).total_seconds() / 60
    
    def to_json(self) -> str:
        data = self._to_full_dict()
        return json.dumps(data, default=str)
    
    def _to_full_dict(self) -> Dict[str, Any]:
        return {'candles': [candle.to_dict() for candle in self.candles]}
    
    def __bytes__(self) -> bytes:
        candles_bytes = []
        for candle in self.candles:
            candle_bytes = bytes(candle)
            candle_len = len(candle_bytes)
            candles_bytes.append(struct.pack('<I', candle_len) + candle_bytes)
    
        count_bytes = struct.pack('<I', len(self.candles))
        return count_bytes + b''.join(candles_bytes)
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'RenkoList':
        count = struct.unpack('<I', data[:4])[0]
        offset = 4
        
        candles = []
        
        for _ in range(count):
            candle_len = struct.unpack('<I', data[offset:offset + 4])[0]
            offset += 4

            candle_bytes = data[offset:offset + candle_len]
            offset += candle_len
            
            candle = RenkoCandle.from_bytes(candle_bytes)
            candles.append(candle)
        
        return cls(candles=candles)


@dataclass
class ClassicCandle:
    '''
        Объект обычной свечи. Из неё можно нагенерировать несколько синтетических сделок, которые симулировали бы колебания цены внутри этой свечи. Нужно 
        для построения ренко-свечек по историческим данным.
        -----------------------------------------------------------
        datetime: datetime начала свечи
        o, h, l, c, v: float
        symbol: str - тикер
        size_min: int - размер свечи в минутах (1, 5, 15, 30 и т.д.)
    '''
    datetime: datetime
    o: float
    h: float
    l: float
    c: float
    v: float
    symbol: str
    size_min: int = 1

    @property
    def cc(self) -> Optional[Literal["g", "r"]]:
        return "g" if self.o <= self.c else "r"

    @property
    def trades(self) -> List[Trade]:
        trades = []
        if self.cc == 'g':
            trades.extend([
                Trade(ts=int(self.datetime.timestamp()) * 1000, p=self.o, v=self.v / 4, s=self.symbol),
                Trade(ts=int(self.datetime.timestamp()) * 1000 + self.size_min * 15000, p=self.l, v=self.v / 4, s=self.symbol),
                Trade(ts=int(self.datetime.timestamp()) * 1000 + self.size_min * 30000, p=self.h, v=self.v / 4, s=self.symbol),
                Trade(ts=int(self.datetime.timestamp()) * 1000 + self.size_min * 45000, p=self.c, v=self.v / 4, s=self.symbol)
                ]) # время делим на 4 сделки
            
        elif self.cc == 'r':
            trades.extend([
                Trade(ts=int(self.datetime.timestamp()) * 1000, p=self.o, v=self.v / 4, s=self.symbol),
                Trade(ts=int(self.datetime.timestamp()) * 1000 + self.size_min * 15000, p=self.h, v=self.v / 4, s=self.symbol),
                Trade(ts=int(self.datetime.timestamp()) * 1000 + self.size_min * 30000, p=self.l, v=self.v / 4, s=self.symbol),
                Trade(ts=int(self.datetime.timestamp()) * 1000 + self.size_min * 45000, p=self.c, v=self.v / 4, s=self.symbol)
                ])
            
        return trades


@dataclass
class TickerInfo:
    '''
        Структура данных о тикере. Содержит информацию о премаркете, минимальном/максимальном размере ордера и т.п. Умеет считать размер позиции, округляя
        его до правильных значений, не выходя за ограничения минимального/максимального размера ордера. Умеет создавать объекты ордеров по этому тикеру.
        --------------------------------------------------------
        name: str - тикер
        category: str - linear/spot/inverse/anything else - поле для байбита, хотя может и на других биржах такое есть
        min_order_qty: float - минимальный размер ордера
        max_market_order_qty: float - максимальный размер тейкер-ордера
        max_order_qty: float - максимальный размер мейкер-ордера
        scale: int - число знаков после запятой в цене тикера
        is_prelisting: bool - на премаркете ли тикер. Надо для байбита, так как на демо-счёте нельзя торговать в премаркет
    '''
    name: str
    category: str
    min_order_qty: float
    max_market_order_qty: float
    max_order_qty: float
    scale: int
    is_prelisting: bool
    time_updated: datetime = datetime.now()

    def calculate_position_size(self, requested_qty: float, requested_price: float) -> int | float:
        min_qty_str = str(self.min_order_qty)

        if '.' in min_qty_str:
            decimal_places = len(min_qty_str.split('.')[1])
        else:
            decimal_places = 0
        
        raw_position_size = (requested_qty / requested_price) / self.min_order_qty
        position_size_rounded = math.ceil(raw_position_size) * self.min_order_qty
        pos_size = round(position_size_rounded, decimal_places)

        if pos_size > self.max_market_order_qty:
            return self.max_market_order_qty
        
        elif pos_size < self.min_order_qty:
            return self.min_order_qty
        
        return pos_size
    
    def calculate_limit_position_size(self, requested_qty: float, requested_price: float) -> int | float:
        min_qty_str = str(self.min_order_qty)

        if '.' in min_qty_str:
            decimal_places = len(min_qty_str.split('.')[1])
        else:
            decimal_places = 0
        
        raw_position_size = (requested_qty / requested_price) / self.min_order_qty
        position_size_rounded = math.ceil(raw_position_size) * self.min_order_qty
        pos_size = round(position_size_rounded, decimal_places)

        if pos_size > self.max_order_qty:
            return self.max_order_qty
        
        elif pos_size < self.min_order_qty:
            return self.min_order_qty
        
        return pos_size

    def edit_price(self, price: float) -> float:
        return round(price, self.scale)
    
    def prepare_buy_order(self, requested_qty: float, requested_price: float, stop_loss_pct: float):
        size = self.calculate_position_size(requested_price=requested_price, requested_qty=requested_qty)
        stop_loss = self.edit_price(requested_price * (1 - stop_loss_pct / 100))

        order = BybitMarketOrder(
            category=self.category,
            ticker=self.name,
            side='Buy',
            size=size,
            request_price=requested_price,
            stop_loss=stop_loss
        )
        return order

    def prepare_sell_order(self, requested_qty: float, requested_price: float):
        order = BybitMarketOrder(
            category=self.category,
            ticker=self.name,
            side='Sell',
            size=requested_qty,
            request_price=requested_price,
            stop_loss=0
        )
        return order

    def prepare_update_order(self, new_price: float):
        order = BybitUpdateOrder(
            ticker=self.name, 
            new_sl=self.edit_price(new_price),
            category=self.category
        )
        return order
    