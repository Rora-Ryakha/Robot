from abc import ABC, abstractmethod
from dataclasses import dataclass

from .MarketData import RenkoCandle


@dataclass
class BaseTrailingStop(ABC):
    current_stop = None

    @abstractmethod
    def update(self, *args, **kwargs) -> None:
        ...


@dataclass
class PullbackTrailingStop(BaseTrailingStop):
    tolerance: float = 0.1
    last_high = None
    lowest_pullback = None
    temp_high = None
    prev_stop = None

    def reset(self):
        self.current_stop = None
        self.last_high = None
        self.lowest_pullback = None
        self.temp_high = None

    def update(self, renko: RenkoCandle) -> None:
        self.prev_stop = self.current_stop
        tolerance = (renko.h - renko.l) * self.tolerance

        if self.current_stop:
            if renko.l + tolerance < self.current_stop:
                self.reset()
                return

        if renko.cc == 'g':
            self.temp_high = renko.h

            if self.last_high:
                if self.lowest_pullback and renko.h > self.last_high:
                    new_min = self.lowest_pullback

                    if self.current_stop:
                        if new_min + tolerance < self.current_stop:
                            self.reset()
                            return
                    
                    self.current_stop = new_min
                    self.last_high = renko.h
                    self.lowest_pullback = 0

        if renko.cc == 'r':
            if self.temp_high:
                self.last_high = self.temp_high
                self.temp_high = 0
                self.lowest_pullback = renko.l

            if self.lowest_pullback:
                self.lowest_pullback = min(renko.l, self.lowest_pullback)
            else:
                self.lowest_pullback = renko.l
