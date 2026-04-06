import asyncio
from collections import deque
from datetime import datetime, timedelta

class RateLimiter:
    '''
        Ограничитель запросов к бирже. Ограничивает их до limit запросов в limit_timeframe_sec секунд. Встраивается в ExtendedAsyncHTTP
        -------------------------------------------------------------------------------------------------
        limit: int - максимальное число запросов, разрешённое в limit_timeframe_sec секунд
        limit_timeframe_sec: int - время, в течение которого разрешено отправить limit запросов
    '''
    def __init__(self, limit: int, limit_timeframe_sec: int):
        self._limit = limit
        self._timeframe = limit_timeframe_sec
        self._timestamps = deque(maxlen=limit)
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        async with self._lock:
            if len(self._timestamps) >= self._limit:
                wait_time = (self._timestamps[0] + timedelta(seconds=self._timeframe) - datetime.now()).total_seconds()
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
        
            self._timestamps.append(datetime.now())
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass