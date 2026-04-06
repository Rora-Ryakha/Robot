import logging
import traceback
from typing import Any

from packages import RenkoList, PullbackTrailingStop, Trade

from Indicators import IndicatorsProcessor
from predictor import Predictor
from renko_builder import RenkoBuilder

logger = logging.getLogger(__name__)

class TickerProcessor:
    '''
        Запускает генерацию новых ренко новой сделкой, вызывает подсчет индикаторов для каждой сгенерированной свечи, вызывает модуль для генерации предиктов.
        Обновляет трейлинг-стоп. На выходе вероятности, последняя свеча и трейлинг для последней свечи.
        ---------------------------------------------------------------------------------------
        ticker: str - тикер
        renko_builder - модуль для генерации ренко-свечек из сделок. Должен иметь метод generate_renko
        predictor - модуль для генерации предиктов
        indicator_processor - модуль для генерации индикаторов. Его писал не я, см. Indicators.IndicatorsProcessor
        initial_renko - искучтвенно сгенерированные из классических свечек ренко
        trailing - объект трейлинг-стопа
    '''
    def __init__(self, ticker: str, renko_builder: RenkoBuilder, predictor: Predictor, indicator_processor: IndicatorsProcessor, initial_renko: RenkoList, trailing: PullbackTrailingStop):
        self.ticker = ticker
        self._trailing = trailing
        self._renko_builder = renko_builder
        self._predictor = predictor
        self._indicator_processor = indicator_processor
        if len(initial_renko) > 0:
            self.cycle(initial_renko)

    def cycle(self, renkolist: RenkoList) -> None: 
        for renko in renkolist:            
            self._indicator_processor.update(renko)

        self._trailing.update(renko=renko)

    def process_trade(self, trade: Trade) -> Any:
        try:
            new_renko = self._renko_builder.generate_renko(trade=trade)
            if len(new_renko) > 0:
                self.cycle(new_renko)

                if self._indicator_processor.status == 'ready':
                    candle_with_indicators = self._indicator_processor.transposed_indicators
                    buy_prediction, sell_prediction = self._predictor.predict(candle_with_indicators=candle_with_indicators)

                    return buy_prediction, sell_prediction, self._trailing, new_renko
        
        except Exception as e:
            logger.error(f'Ошибка в обработке сделки: {e}')
            logger.error(traceback.format_exc())
            
        return None, None, self._trailing, None

