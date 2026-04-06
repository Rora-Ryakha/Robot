import asyncio
import logging
import time
from abc import ABC
from collections import defaultdict
from functools import wraps
from typing import Any, Callable, Dict, Optional

from .Publishers import NATSPublisher

logger = logging.getLogger(__name__)


class BaseMonitor(ABC):
    '''
        Абстрактный класс для монитора. Содержит логику отправки сообщений в NATS Core. 
        -----------------------------------
        publisher: NATSPublisher - объект паблишера, занимающийся непосредственно отправкой сообщений. В нём уже должен быть запущен стрим.
    '''
    _publisher = None

    def add_publisher(self, publisher: NATSPublisher):
        self._publisher = publisher

    async def _publish(self, metric: Any, topic: str) -> None:
        if self._publisher is None:
            return
        
        try:
            asyncio.create_task(self._publisher.publish(topic, str(metric).encode()))

        except TypeError:
            logger.error(f'Не могу преобразовать метрику для топика {topic} в строку.')
            raise


class TimeMonitor(BaseMonitor):
    '''
        Мониторинг всего связанного со временем выполнения. Умеет замерять среднее время работы корутины каждые update_interval вызовов,
        замерять разовое время выполнения корутины в наносекундах. Методы вызываются через декораторы. В качестве топика следует указывать 
        metrics.latency.<module>.<submodule>, так как в таком виде по умолчанию настроен сервис metrics_adapter.
    '''
    def __init__(self):
        self._stats: Dict[str, Dict[str, Any]] = defaultdict(
                lambda: {"total_time": 0, "call_count": 0}
            )
    
    def measure_average_time(self, topic: str, update_interval: int = 1000) -> Callable:
        def wrapper(func: Callable):
            @wraps(func)
            async def inner(self_instance, *args, **kwargs):
                start_time = time.perf_counter_ns()
                result = await func(self_instance, *args, **kwargs)
                end_time = time.perf_counter_ns()
                
                execution_time = end_time - start_time
                key = f"{func.__name__}"
                self._stats[key]["total_time"] += execution_time
                self._stats[key]["call_count"] += 1
                
                if self._stats[key]['call_count'] >= update_interval:
                    avg_time = self._stats[key]['total_time'] // self._stats[key]['call_count']
                    await self._publish(metric=avg_time, topic=topic)
                    self._stats[key]['call_count'] = 0
                    self._stats[key]['total_time'] = 0
                
                return result
            return inner
        return wrapper
    
    def measure_time(self, topic: str) -> Callable:
        def wrapper(func: Callable):
            @wraps(func)
            async def inner(self_instance, *args, **kwargs):
                start_time = time.perf_counter_ns()
                result = await func(self_instance, *args, **kwargs)
                end_time = time.perf_counter_ns()
                execution_time = end_time - start_time
                await self._publish(metric=execution_time, topic=topic)
                return result
            return inner
        return wrapper
        

class SpeedMonitor(BaseMonitor):
    '''
        Мониторинг всего связанного со скоростью выполнения. Умеет замерять среднюю скорость работы корутины каждые update_interval вызовов,
        замерять разовую скорость выполнения корутины. Методы вызываются через декораторы. Под скоростью подразумевается число вызовов в секунду.
        В качестве топика следует указывать metrics.speed.<module>.<submodule>, так как в таком виде по умолчанию настроен сервис metrics_adapter.
    '''
    def __init__(self):
        self._stats: Dict[str, Dict[str, Any]] = defaultdict(
                lambda: {"start_time": time.perf_counter_ns(), "call_count": 0}
            )
   
    def measure_average_speed(self, topic: str, num_calls: int = 1000) -> Callable:
        def wrapper(func: Callable):
            @wraps(func)
            async def inner(self_instance, *args, **kwargs):
                key = f"{func.__name__}"
                result = await func(self_instance, *args, **kwargs)
                self._stats[key]["call_count"] += 1
                
                if self._stats[key]['call_count'] >= num_calls:
                    now = time.perf_counter_ns()
                    time_for_num_calls = int(now - self._stats[key]["start_time"]) / 1000000000 # время на num_calls в секундах
                    speed = num_calls / time_for_num_calls if time_for_num_calls != 0 else 0
                    await self._publish(metric=speed, topic=topic)
                    self._stats[key]['call_count'] = 0
                    self._stats[key]['start_time'] = time.perf_counter_ns()
                
                return result
            return inner
        return wrapper
    