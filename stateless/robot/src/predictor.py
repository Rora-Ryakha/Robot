from typing import Tuple

from packages import RenkoCandle

from Model import LightModel


class Predictor:
    '''
        Запускает модели и выдаёт их предикты.
        ---------------------------------------
        buy_model, self_model: LightModel - классы ML - моделей. Должны иметь метод predict_proba_single_line, 
        возвращающий список вида [вероятность False, вероятность True]
    '''
    def __init__(self, buy_model: LightModel, sell_model: LightModel):
        self._buy_model = buy_model
        self._sell_model = sell_model

    def predict(self, candle_with_indicators: RenkoCandle) -> Tuple[list, list]:
        try:
            predict = self._buy_model.predict_proba_single_line(predict_data=candle_with_indicators)
            buy_proba = [predict[0], predict[1]]
            predict = self._sell_model.predict_proba_single_line(predict_data=candle_with_indicators)
            sell_proba = [predict[0], predict[1]]
        
        except:
            buy_proba = [0, 0]
            sell_proba = [0, 0]

        return buy_proba, sell_proba
