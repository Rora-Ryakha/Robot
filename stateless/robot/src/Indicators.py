import time
from abc import ABC, abstractmethod
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Set, Tuple, Union

import numpy as np
from packages import RenkoCandle
import pandas as pd


class IndicatorsProcessor():
    '''Потоковый калькулятор сигналов для поступающих свечек. В метод update подавать свечи в виде объектов класса RenkoCandle:
     
    Параметры:
    history_win_sizе: размер окна исторических свеч, по умолчанию 1 (только текущая свеча)
    do_not_populate_columns: столблцы, которые не нужно размножать в прошлое (актуально только для history_win_sizе>1)
    indicators_set: конфигурация вычисляемых индикаторов
    verbose: нужны ли принты инициализации
    normalize_curves: нужно ли нормализовать к максимуму цены окна history_win_sizе кривые, связанные с ценой (отладочное, в проде нормализация обязательна)
    time_stat_max_len: максимальная длина списка, в который пишется время обработки одной свечи

    Внутренние атрибуты:
    self.manager: менеджер инициализации и расчета индикаторов IndicatorManager
    self.transformer: класс нормализации и транспонирования индикаторов
    self.status: текущий статус класса
    
    self.candles_passed: сколько свечек обработано
    self.current_candle_indicators: набор индикаторов для текущей свечи
    self.last_window_indicators: наборы индикаторов для последних history_win_sizе свечек
    self.transposed_indicators: нормированный и транспонированный last_window_indicators набор
    
    self.time_stat: список для хранения времен обработки свечек
    self.time_stat_max_len: его предельная длина
    self.time_stat_mean_ms: среднее значение времени обработки одной свечи в мс
     
        '''
    
    
    def __init__(self, 
                 history_win_size=1, 
                 do_not_populate_columns=['buy', 'sell', 'o', 'h', 'l', 'c', 'v', 'ts'], 
                 indicators_set={'relaxation_rate': 0.2,
                                 'ema_cross_params': [(14, 7), (200, 50), (20, 9)],
                                 'rsi_params': [(168, 30, 70, [3, 6]), (42, 30, 70, [3, 6]), (84, 30, 70, [3, 6])],
                                 'cci_params': [(168, -100, 100, [3, 6]), (42, -100, 100, [3, 6]), (84, -100, 100, [3, 6])],
                                 'channel_params': [(168, 5), (42, 5), (84, 5)],
                                 'range_params': [(56, 5), (84, 5), (168, 5)],
                                 'macd_params': [(52, 24, 18, [3, 6])],
                                 'stoch_params': [(42, 6, 6, 20, 80, [3, 6])],
                                 'duration_params': [5],
                                 'volume_params': [5]
                 }, verbose=False, normalize_curves=True, time_stat_max_len=1000):
        
        self.history_win_size = history_win_size
        self.indicators_set = indicators_set
        self.do_not_populate_columns = do_not_populate_columns
        self.verbose = verbose
        self.normalize_curves = normalize_curves
        self.manager = IndicatorManager(**indicators_set, verbose=self.verbose)        
        self.transformer = FeatureTransformer(history_win_size=self.history_win_size, curves_to_normalize=self.manager.curves_to_normalize, do_not_populate_columns=self.do_not_populate_columns, verbose=self.verbose, normalize_curves=self.normalize_curves)
        self.status = 'init'
        self.candles_passed = 0
        self.last_window_indicators = []
        self.transposed_indicators = {}
        self.current_candle_indicators = {}
        self.time_stat = []
        self.time_stat_max_len = time_stat_max_len
        self.time_stat_mean_ms = 0
        
        
    def update(self, candle: RenkoCandle) -> Optional[Dict[str, Union[float, int, None]]]:
        tma = time.time()
        self.candles_passed+=1
        signals = self.manager.update(candle)
        if not self.manager.is_ready: 
            self.status = 'need more candles'            
        else:
            current_result = {'ts': candle.ts, 'c': candle.close, 'o': candle.open, 'h': candle.high, 'l': candle.low, 
                      'v':candle.v, 'cc': candle.cc,  'duration':candle.duration, 'buy':candle.buy, 'sell':candle.sell, **signals}            
            transposed_dict = self.transformer.update(current_result)
            self.current_candle_indicators = current_result
            self.last_window_indicators.append(current_result)
            self.last_window_indicators = self.last_window_indicators[-self.history_win_size:]
            self.transposed_indicators = transposed_dict
            self.status = 'wait for transformer' if transposed_dict is None else 'ready'
            self.time_stat.append(time.time()-tma)
            self.time_stat = self.time_stat[-self.time_stat_max_len:]
            self.time_stat_mean_ms = np.mean(self.time_stat)*1000

           

class IndicatorManager:
    """
    Менеджер для инициализации и потокового обновления всех EMA, RSI и CCI сигналов.
    
    Он:
    1. Инициализирует все генераторы сигналов.
    2. Рассчитывает минимально необходимую историю для корректной работы всех индикаторов.
    3. Потоково обновляет все индикаторы и суммирует релаксированные сигналы.
    """
    def __init__(self, 
                 ema_cross_params=None, # List[Tuple[int, int]], 
                 macd_params=None, # List[Tuple[int, int, int, List[int]]],
                 rsi_params=None, # List[Tuple[int, float, float, List[int]]],
                 cci_params=None, # List[Tuple[int, float, float, List[int]]],
                 channel_params=None, # List[Tuple[int, int]], 
                 stoch_params=None, # List[Tuple[int, int, int, float, float, List[int]]],
                 range_params=None,
                 duration_params=None,
                 volume_params=None,
                 relaxation_rate: float = 0.2, verbose=False):
        
        self.all_signals: List[Any] = []  # Список всех генераторов сигналов
        max_periods: List[int] = [] # Хранит максимальные требуемые периоды
        
        # --- Новый атрибут: Счетчики и статус ---
        self.candles_processed: int = 0
        self.is_ready: bool = False
        
        curves_to_normalize = []

        # --- Инициализация всех индикаторов (логика инициализации без изменений) ---

        # --- 1. Инициализация EMA-пересечений ---
        if ema_cross_params is not None:
            for slow, fast in ema_cross_params:
                # Предполагается, что EMACrossSignal определен
                cross_instance = EMACrossSignal(slow, fast, relaxation_rate) 
                self.all_signals.append(cross_instance)
                max_periods.append(max(slow, fast))
            for p in ema_cross_params: 
                curves_to_normalize.extend(['ema_'+str(v) for v in p])
        
        # --- 2. Инициализация RSI сигналов ---
        if rsi_params is not None:
            for period, lo, hi, lags in rsi_params:
                # Предполагается, что RSIAllSignals определен
                rsi_instance = RSIAllSignals(period, lo, hi, lags, relaxation_rate) 
                self.all_signals.append(rsi_instance)
                max_periods.append(period + max(lags) if lags else period)

        # --- 3. Инициализация CCI сигналов ---
        if cci_params is not None:
            for period, lo, hi, lags in cci_params:
                # Предполагается, что CCIAllSignals определен
                cci_instance = CCIAllSignals(period, lo, hi, lags, relaxation_rate) 
                self.all_signals.append(cci_instance)
                max_periods.append(period + max(lags) if lags else period)
                
        # --- 4. Инициализация Channel Signals ---
        if channel_params is not None:
            for period, slope_win in channel_params:
                # Предполагается, что ChannelSignalGenerator определен
                channel_instance = ChannelSignalGenerator(period, slope_win, relaxation_rate)
                self.all_signals.append(channel_instance)
                max_periods.append(period + slope_win) 
            for p in channel_params: 
                curves_to_normalize.extend(['channel_'+str(p[0])+'_lo', 'channel_'+str(p[0])+'_hi'])
                
        # --- 5. Инициализация Range Signals ---
        if range_params is not None:
            for period, slope_window in range_params:
                # Предполагается, что RangeAllSignals определен
                range_instance = RangeAllSignals(period, slope_window, relaxation_rate)
                self.all_signals.append(range_instance)
                max_periods.append(max(period, slope_window))
                
        # --- Инициализация MACD Signals ---
        if macd_params is not None:
            for k, d, signal, grads in macd_params:
                # Предполагается, что MACDSignalGenerator определен
                macd_instance = MACDSignalGenerator(k, d, signal, grads, relaxation_rate)
                self.all_signals.append(macd_instance)
                # Максимально необходимый период истории: k (для самой медленной EMA) + signal + max(grads)
                max_periods.append(k + signal + max(grads) + 1)                            
                curves_to_normalize.extend(['macd_'+str(k)+'_'+str(d)+'_'+str(signal),
                                           'macd_'+str(k)+'_'+str(d)+'_'+str(signal)+'_h',
                                           'macd_'+str(k)+'_'+str(d)+'_'+str(signal)+'_s'])
                
                
        # --- Инициализация Stochastic Signals ---
        if stoch_params is not None:
            for k, d, smooth_k, lo_thr, hi_thr, lags in stoch_params:
                # Предполагается, что StochasticSignalGenerator определен
                stoch_instance = StochasticSignalGenerator(k, d, smooth_k, lo_thr, hi_thr, lags, relaxation_rate)
                self.all_signals.append(stoch_instance)
                # Максимально необходимый период истории: k + smooth_k + d + max(lags)
                max_period = k + smooth_k + d + max(lags) if lags else k + smooth_k + d
                max_periods.append(max_period)
                
        # --- Инициализация Duration Signals ---
        if duration_params is not None:
            for v_reg_len in duration_params:
                # Предполагается, что DurationSignalGenerator определен
                duration_instance = DurationSignalGenerator(v_reg_len, relaxation_rate)
                self.all_signals.append(duration_instance)
                max_periods.append(max(v_reg_len, 10) + 1) # max_lag=10 для дельт
                
        # --- Инициализация Volume Signals ---
        if volume_params is not None:
            for v_reg_len in volume_params:
                # Предполагается, что VolumeSignalGenerator определен
                volume_instance = VolumeSignalGenerator(v_reg_len, relaxation_rate)
                self.all_signals.append(volume_instance)
                max_periods.append(max(v_reg_len, 10) + 1) # max_lag=10 для дельт


        # --- 4. Расчет минимально необходимой истории ---
        self.min_history_required: int = max(max_periods)+5 if max_periods else 0
        
        if self.min_history_required == 0:
            raise Exception('Вероятно, не указаны никакие индикаторы')
            
        self.curves_to_normalize = curves_to_normalize
        
        if verbose: print(f"✅ Менеджер инициализирован. Максимальный период истории: {self.min_history_required} свечей.\nКривые для нормализации:\n", str(self.curves_to_normalize))


    def update(self, new_candle: RenkoCandle) -> Optional[Dict[str, Union[float, int, None]]]:
        """
        Обрабатывает новую свечу и обновляет все индикаторы и сигналы.
        Возвращает словарь со всеми рассчитанными значениями и суммарным сигналом 
        только после того, как будет накоплена минимально необходимая история.
        """
        self.candles_processed += 1
        
        results: Dict[str, Union[float, int, None]] = {}
        
        # Обновление всех генераторов сигналов, независимо от статуса готовности
        for signal_generator in self.all_signals:
            current_result = signal_generator.update(new_candle)
            
            # Добавляем все сигналы и значения в общий словарь
            if current_result is not None:
                results.update(current_result)
        
        # Проверка статуса готовности
        if not self.is_ready and self.candles_processed >= self.min_history_required:
            self.is_ready = True
            
        # Возвращаем результаты только если готовы
        if self.is_ready:
            # Здесь можно было бы добавить расчет суммарного сигнала по всем индикаторам
            return results
        else:
            return None


class FeatureTransformer:
    """
    Преобразует список последовательных словарей сигналов 
    в один плоский словарь с историей признаков (lagged features).
    """
    
    def __init__(self, 
                 history_win_size: int, 
                 curves_to_normalize: List[str],
                 do_not_populate_columns=['buy', 'sell', 'o', 'h', 'l', 'c', 'v'], verbose=False, normalize_curves=True):
        """
        :param history_win_size: Количество словарей истории, включая текущий (t, t-1, ...).
        :param curves_to_normalize: Список ключей (индикаторов) для деления на 'h' текущей свечи.
        :param do_not_populate_columns: Список колонок, которые НЕ НУЖНО размножать (добавлять суффиксы -1, -2 и т.д.).
        """
        self.history_win_size = history_win_size
        self.curves_to_normalize: Set[str] = set(curves_to_normalize) # Для быстрой проверки
        
        # Список колонок, которые не размножаются в прошлое (добавляются только один раз)
        # Добавляем 'index' по умолчанию, так как он должен быть только один.
        default_no_populate = ['index']
        if do_not_populate_columns:
            default_no_populate.extend(do_not_populate_columns)
            
        self.do_not_populate_columns: Set[str] = set(default_no_populate)
        
        # Хранилище для последних N словарей (включая текущий)
        self.history_deque: deque[Dict[str, Any]] = deque(maxlen=history_win_size)
        
        self.min_records_required = history_win_size
        
        self.normalize_curves = normalize_curves
        
        if verbose: 
            print('Трансформер:\n', f'{do_not_populate_columns=}\n')
            if normalize_curves: print(f'{curves_to_normalize=}\n')

    def update(self, new_signal_dict: Dict[str, Any]) -> Optional[Dict[str, Union[float, int, str, pd.Timestamp]]]:
        """
        Принимает новый словарь сигналов, преобразует его вместе с историей.
        
        :param new_signal_dict: Новый словарь, полученный от IndicatorManager.
        :return: Плоский словарь признаков с историей, или None, если история недостаточна.
        """
        
        # 1. Добавление нового словаря в историю
        self.history_deque.append(new_signal_dict)
        
        # 2. Проверка, достаточно ли данных для формирования окна
        if len(self.history_deque) < self.min_records_required:
            return None
        
        # 3. Получение делителя для нормализации
        current_dict = self.history_deque[-1]
        
        # Используем H (High) текущей свечи.
        normalize_divisor: float = float(current_dict.get('h', current_dict.get('c', 1.0)))
        
        if normalize_divisor == 0:
            normalize_divisor = 1.0
        
        final_features: Dict[str, Union[float, int, str, pd.Timestamp]] = {}

        # 4. Транспонирование и сглаживание
        
        # Итерируемся от самого старого элемента (lag = N-1) к самому новому (lag = 0)
        for i, historical_dict in enumerate(self.history_deque):
            
            # Лаг (запаздывание)
            lag: int = self.history_win_size - 1 - i
            
            # --- Обработка и нормализация ---
            processed_dict = historical_dict.copy() 
            
            for key, value in processed_dict.items():
                
                # Применяем нормализацию только к указанным числовым кривым
                if self.normalize_curves:
                    if key in self.curves_to_normalize:
                        try:
                            # Деление значения на 'h' текущего словаря
                            processed_dict[key] = float(value) / normalize_divisor
                        except (ValueError, TypeError):
                            continue
            
            # --- Сглаживание и добавление суффикса ---
            for key, value in processed_dict.items():
                
                # Правило 1: Колонки, которые не размножаются (только текущее значение)
                if key in self.do_not_populate_columns:
                    # Добавляем только если это текущая свеча (lag=0)
                    if lag == 0:
                        final_features[key] = value
                    # Игнорируем прошлое значение этой колонки (lag > 0)
                    continue
                
                # Правило 2: Все остальные колонки размножаются
                suffix: str = f"-{lag}" if lag > 0 else ""
                new_key = f"{key}{suffix}"
                final_features[new_key] = value

        return final_features


    
class BaseIndicator(ABC):
    """
    Абстрактный базовый класс для всех индикаторов. Управляет историей и готовностью.
    """
    def __init__(self, period: int):
        self.period = period
        self.history: Deque[float] = deque(maxlen=period)
        self.value: float | None = None
        self.previous_ema: float | None = None # Для потокового расчета EMA
        self.is_ready: bool = False

    @abstractmethod
    def _calculate(self, new_candle: RenkoCandle) -> float:
        pass

    def update(self, new_candle: RenkoCandle) -> float | None:
        """Обновляет историю и рассчитывает новое значение."""
        self.history.append(new_candle.close)

        if not self.is_ready and len(self.history) == self.period:
            self.is_ready = True

        if self.is_ready:
            # Сохраняем текущее значение как предыдущее перед новым расчетом
            self.previous_ema = self.value
            self.value = self._calculate(new_candle)
            return self.value

        return None
    
    
# class SignTransition:
#     """
#     Потоковый расчет смены знака (для сигналов пересечения).
#     Возвращает 1 или -1, когда fast-slow переходит через 0.
#     """
#     def __init__(self):
#         # Хранит знак предыдущей разницы (slow - fast)
#         self.previous_diff_sign: int = 0

#     def update(self, slow_diff_fast_current: float) -> int:
#         """Принимает разницу (slow - fast) и выдает сигнал пересечения."""

#         current_diff_sign = int(np.sign(slow_diff_fast_current))
        
#         transition = 0
        
#         # BUY (+1): slow_diff_fast переходит из - в + (Fast пересекает Slow вверх)
#         if self.previous_diff_sign < current_diff_sign:
#             transition = 1
#         # SELL (-1): slow_diff_fast переходит из + в - (Fast пересекает Slow вниз)
#         elif self.previous_diff_sign > current_diff_sign:
#             transition = -1
            
#         self.previous_diff_sign = current_diff_sign
#         return transition

class SignTransition:
    """
    Потоковый расчет смены знака (для сигналов разворота), 
    генерирующий сигнал только при фактическом пересечении нуля.
    """
    def __init__(self):
        # Хранит знак предыдущей разницы (слоп или скорость)
        self.previous_diff_sign: int = 0

    def update(self, current_value: float) -> int:
        """Принимает текущее значение (слоп/скорость) и выдает сигнал разворота."""

        current_diff_sign = int(np.sign(current_value))
        transition = 0
        
        # 1. Проверяем, что знак действительно изменился
        if current_diff_sign != self.previous_diff_sign:
            
            # BUY (+1): Переход строго из отрицательной зоны (-1) в положительную (+1).
            # Исключаем переходы в/из 0.
            if self.previous_diff_sign == -1 and current_diff_sign == 1:
                transition = 1
                
            # SELL (-1): Переход строго из положительной зоны (+1) в отрицательную (-1).
            elif self.previous_diff_sign == 1 and current_diff_sign == -1:
                transition = -1
                
        # Обновление предыдущего знака
        # Внимание: мы должны сохранять знак как -1, 0 или 1, 
        # чтобы правильно отслеживать зону, в которой находимся.
        self.previous_diff_sign = current_diff_sign
        
        return transition
    
class SignalRelaxation:
    """
    Потоковая реализация логики затухающего растяжения сигналов (modify_signal).
    Точно имитирует поведение: 1.0/-1.0 при активации, затем пошаговое затухание.
    """
    def __init__(self, relaxation_rate: float = 0.2):
        self.relaxation_rate = relaxation_rate
        self.previous_signal: float = 0.0 # Хранит последнее релаксированное значение

    def update(self, new_signal_raw: int) -> float:

            if abs(new_signal_raw) == 1:
                current_signal = float(new_signal_raw)
            else:
                prev = self.previous_signal
                rate = self.relaxation_rate

                if prev > 0:
                    current_signal = prev - rate
                    if current_signal < 0:
                        current_signal = 0.0

                elif prev < 0:
                    current_signal = prev + rate
                    if current_signal > 0:
                        current_signal = 0.0

                else:
                    current_signal = 0.0

                # ЭТОТ КУСОК КРИТИЧЕН ДЛЯ ПОЛУЧЕНИЯ 0.8, 0.6, 0.4 и т.д.
                current_signal = round(current_signal, 1)

            self.previous_signal = current_signal
            return current_signal


    
    
class EMA(BaseIndicator):
    """
    Потоковый расчет EMA с внутренней логикой для определения бинарного тренда 
    (имитация sign(diff).bfill()).
    """
    def __init__(self, period: int):
        super().__init__(period)
        self.alpha = 2 / (period + 1)

        # --- Для логики trend и bfill ---
        self.current_trend: int = 0
        self.first_non_zero_trend: int = 0 # Имитация bfill

    def _calculate(self, new_candle: RenkoCandle) -> float:
        """Выполняет расчет нового значения EMA и обновляет внутренний тренд."""
        current_price = new_candle.close

        if self.previous_ema is None:
            ema = sum(self.history) / self.period
        else:
            ema = (current_price * self.alpha) + (self.previous_ema * (1 - self.alpha))

        # --- ЛОГИКА ТРЕНДА (SIGN(DIFF).BFILL()) ---
        if self.previous_ema is not None and self.is_ready:
            trend_diff = ema - self.previous_ema
            trend_sign = int(np.sign(trend_diff))

            # bfill(): сохраняем первое ненулевое значение тренда
            if self.first_non_zero_trend == 0 and trend_sign != 0:
                self.first_non_zero_trend = trend_sign

            # Применение bfill(): если текущий тренд 0, используем первое ненулевое
            self.current_trend = trend_sign if trend_sign != 0 else self.first_non_zero_trend

        return ema
    
class EMACrossSignal:
    """
    Потоковый расчет сигналов EMA, имитирующий логику add_ema_signals.
    """
    def __init__(self, slow_period: int, fast_period: int, relaxation_rate: float = 0.2):
        
        self.slow_ema = EMA(slow_period)
        self.fast_ema = EMA(fast_period)

        # 1. Инструменты для разворота тренда (SignTransition)
        self.slow_trend_rev = SignTransition()
        self.fast_trend_rev = SignTransition()
        
        # 2. Постоянные экземпляры релаксации
        self.slow_reverse_relaxer = SignalRelaxation(relaxation_rate) # <-- НОВЫЙ
        self.fast_reverse_relaxer = SignalRelaxation(relaxation_rate) # <-- НОВЫЙ
        self.cross_relaxer = SignalRelaxation(relaxation_rate)         # <-- СТАРЫЙ, но теперь единственный для пересечения
        
        self.current_signals: Dict[str, Union[float, int, None]] = {}

    def _calculate_trend_reverse(self, ema_indicator: EMA, trend_reverser: SignTransition, relaxer: SignalRelaxation):
            """
            Вычисляет тренд и разворот. Использует предоставленный relaxer.
            """
            if not ema_indicator.is_ready:
                return 0.0, 0

            binary_trend = ema_indicator.current_trend
            reverse_signal_raw = trend_reverser.update(float(binary_trend))

            # Используем постоянный relaxer
            reverse_signal_rel = relaxer.update(reverse_signal_raw)

            return reverse_signal_rel, binary_trend

    def update(self, new_candle: RenkoCandle) -> Dict[str, Union[float, int, None]] | None:
        """Обновляет оба EMA, затем рассчитывает все необходимые сигналы."""
        slow_val = self.slow_ema.update(new_candle)
        fast_val = self.fast_ema.update(new_candle)

        if slow_val is None or fast_val is None:
            return None

        # --- 1. Тренд и Разворот для Медленной/Быстрой EMA ---
        # --- 1. Тренд и Разворот ---
        # Передаем постоянный релаксатор
        slow_rev_rel, slow_trend = self._calculate_trend_reverse(
        self.slow_ema, self.slow_trend_rev, self.slow_reverse_relaxer)
            
        fast_rev_rel, fast_trend = self._calculate_trend_reverse(
            self.fast_ema, self.fast_trend_rev, self.fast_reverse_relaxer)

        # Сохранение кривых, трендов и разворотов
        self.current_signals[f"ema_{self.slow_ema.period}"] = slow_val
        self.current_signals[f"ema_{self.slow_ema.period}_trend"] = slow_trend
        self.current_signals[f"ema_{self.slow_ema.period}_reverse_signal"] = slow_rev_rel

        self.current_signals[f"ema_{self.fast_ema.period}"] = fast_val
        self.current_signals[f"ema_{self.fast_ema.period}_trend"] = fast_trend
        self.current_signals[f"ema_{self.fast_ema.period}_reverse_signal"] = fast_rev_rel

        # --- 2. Сигнал пересечения (Cross Signal) ---
        # Исходная логика: сравнение знаков разницы (slow - fast) на t-1 и t
        slow_prev = self.slow_ema.previous_ema
        fast_prev = self.fast_ema.previous_ema
        
        # Проверка, чтобы избежать ошибок при первом расчете тренда
        if slow_prev is None or fast_prev is None:
            return None # Должно быть невозможно, если slow_val/fast_val не None, но для безопасности

        diff_curr = slow_val - fast_val
        diff_prev = slow_prev - fast_prev

        diff_y1_sign = int(np.sign(diff_prev)) # Знак (slow - fast) на t-1
        diff_y2_sign = int(np.sign(diff_curr)) # Знак (slow - fast) на t

        cross_signal_raw = 0
        if diff_y1_sign > diff_y2_sign: # Переход + -> - (медленная > быстрой, потом < быстрой) -> SELL
            cross_signal_raw = 1
        elif diff_y1_sign < diff_y2_sign: # Переход - -> + (медленная < быстрой, потом > быстрой) -> BUY
            cross_signal_raw = -1

        # Релаксация сигнала пересечения
        cross_signal_rel = self.cross_relaxer.update(cross_signal_raw)
        self.current_signals[f"ema_cross_{self.slow_ema.period}_{self.fast_ema.period}_signal"] = cross_signal_rel

        # --- 3. Сумма сигналов разворота ---
        self.current_signals[f"ema_sum_{self.slow_ema.period}_{self.fast_ema.period}_signal"] = slow_rev_rel + fast_rev_rel

        return self.current_signals
    
class RSI(BaseIndicator):
    """
    Потоковый расчет RSI с использованием сглаживания по Уайлдеру (Wilder's Smoothing).
    """
    def __init__(self, period: int):
        super().__init__(period)
        
        # Внутренние состояния RSI
        self.avg_gain: float | None = None
        self.avg_loss: float | None = None
        self.previous_close: float | None = None
        
        # История для хранения N изменений цены (для начального SMA)
        self.price_changes: Deque[float] = deque(maxlen=period)
        
    def update(self, new_candle: RenkoCandle) -> float | None:
        """Обновление RSI."""
        
        current_close = new_candle.close
        
        # 1. Сначала проверяем, есть ли предыдущая цена для расчета изменения
        if self.previous_close is None:
            self.previous_close = current_close
            return None # Нужна хотя бы одна свеча
            
        # 2. Рассчитываем изменение цены и добавляем в историю изменений
        change = current_close - self.previous_close
        self.price_changes.append(change)

        # Обновляем предыдущую цену
        self.previous_close = current_close
        
        # 3. Проверяем готовность (N свечей для SMA инициализации)
        # is_ready = True, когда накопилось N изменений цены
        if not self.is_ready and len(self.price_changes) == self.period:
            self.is_ready = True
            
        if self.is_ready:
            # Расчет
            self.value = self._calculate(change)
            return self.value
        
        return None

    def _calculate(self, current_change: float) -> float:
        
        if self.avg_gain is None or self.avg_loss is None:
            # --- Фаза 1: Инициализация (SMA по первым N точкам) ---
            
            initial_gains = [max(0, c) for c in self.price_changes]
            initial_losses = [abs(min(0, c)) for c in self.price_changes]
            
            # Note: Сумма (N) / N = SMA
            self.avg_gain = sum(initial_gains) / self.period
            self.avg_loss = sum(initial_losses) / self.period
            
        else:
            # --- Фаза 2: Сглаживание по Уайлдеру (Wilder's Smoothing) ---
            
            N = self.period
            current_gain = max(0, current_change)
            current_loss = abs(min(0, current_change))

            # Формула Уайлдера: Avg = (PrevAvg * (N-1) + CurrentChange) / N
            self.avg_gain = (self.avg_gain * (N - 1) + current_gain) / N
            self.avg_loss = (self.avg_loss * (N - 1) + current_loss) / N

        # 4. Расчет RSI
        if self.avg_loss == 0:
            rsi = 100.0
        else:
            rs = self.avg_gain / self.avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))

        return rsi
    
class RSITrend:
    """
    Рассчитывает абсолютное изменение RSI и его знак за лаг (N периодов).
    """
    def __init__(self, lag: int):
        self.lag = lag
        # Храним ТОЛЬКО lag ПРЕДЫДУЩИХ значений RSI
        self.history_rsi: Deque[float] = deque(maxlen=lag)
        self.is_ready: bool = False
    
    def update(self, rsi_value: float) -> tuple[float | None, int | None]:
        
        # 1. Сначала проверяем готовность
        if not self.is_ready and len(self.history_rsi) == self.lag:
            self.is_ready = True

        if self.is_ready:
            # rsi_previous: значение lag периодов назад (Самое старое в deque)
            rsi_previous = self.history_rsi[0] 
            
            trend_diff = rsi_value - rsi_previous
            trend_sign = int(np.sign(trend_diff))
            
            # 2. Обновляем историю, сохраняя текущее значение как предыдущее для следующего шага
            self.history_rsi.append(rsi_value)
            
            return trend_diff, trend_sign
            
        # 3. Если не готов, просто добавляем значение в историю
        self.history_rsi.append(rsi_value)
        
        return None, None # Не готов
    
class RSIAllSignals:
    """
    Потоковый расчет всех сигналов RSI, включая тренды, over_signal и signal.
    """
    def __init__(self, period: int, lo_thr: float = 30.0, hi_thr: float = 70.0, lags: List[int] = [3, 6], relaxation_rate: float = 0.2):
        self.rsi = RSI(period)
        self.lo_thr = lo_thr
        self.hi_thr = hi_thr
        self.period = period
        
        # Релаксаторы для _over_signal и _signal
        self.over_signal_relaxer = SignalRelaxation(relaxation_rate)
        self.main_signal_relaxer = SignalRelaxation(relaxation_rate)
        
        # Постоянные объекты для трендов
        self.trend_generators = {lag: RSITrend(lag) for lag in lags}

        # Состояния для signal (Вход/Выход)
        self.prev_overbought: int = 0
        self.prev_oversold: int = 0
        
        self.current_signals: Dict[str, Union[float, int, None]] = {}
        
    def update(self, new_candle: RenkoCandle) -> Dict[str, Union[float, int, None]] | None:
        
        rsi_value = self.rsi.update(new_candle)
        prefix = f"rsi_{self.period}"
        
        # Если RSI не готов (RSI.update вернул None), мы ничего не можем посчитать
        if rsi_value is None:
            return None
        
        self.current_signals[prefix] = rsi_value
        
        # ... (Логика 1 и 2 для over_signal и main_signal остается прежней) ...
        current_overbought = 1 if rsi_value > self.hi_thr else 0
        current_oversold = -1 if rsi_value < self.lo_thr else 0
        over_signal_raw = current_overbought + current_oversold
        
        sell_sig = 0
        buy_sig = 0
        if current_overbought == 1 and self.prev_overbought == 0:
            sell_sig = -1
        if current_oversold == 0 and self.prev_oversold == -1:
            buy_sig = 1
        main_signal_raw = sell_sig + buy_sig
        
        self.prev_overbought = current_overbought
        self.prev_oversold = current_oversold
        
        # --- 3. Тренды и их знаки ---
        for lag, trend_gen in self.trend_generators.items():
            # Важно: RSI всегда возвращает float, тренды могут возвращать None до готовности
            diff, sign = trend_gen.update(rsi_value)
            
            # Проверяем, что тренд готов (не None), перед сохранением
            if diff is not None:
                self.current_signals[f"{prefix}_trend{lag}"] = diff
                self.current_signals[f"{prefix}_trend{lag}_signal"] = sign
            # Иначе, эти поля пропускаются, что эквивалентно NaN / отсутствию данных
        
        # --- 4. Релаксация и сохранение ---
        
        # _over_signal
        over_signal_rel = self.over_signal_relaxer.update(over_signal_raw)
        self.current_signals[f"{prefix}_over_signal"] = over_signal_rel
        
        # _signal
        main_signal_rel = self.main_signal_relaxer.update(main_signal_raw)
        self.current_signals[f"{prefix}_signal"] = main_signal_rel

        return self.current_signals
    

class CCI(BaseIndicator):
    """
    Потоковый расчет CCI. Готов после N свечей (для SMA и MD).
    Имитирует логику SMA и MD из исходного кода.
    """
    def __init__(self, period: int):
        super().__init__(period)
        
        # История для расчета SMA и MD (хранит N последних TP)
        self.tp_history: Deque[float] = deque(maxlen=period)
        
    def update(self, new_candle: RenkoCandle) -> float | None:
        """Обновление CCI."""
        
        # 1. Расчет Типичной Цены (TP)
        tp = (new_candle.high + new_candle.low + new_candle.close) / 3
        self.tp_history.append(tp)
        
        if not self.is_ready and len(self.tp_history) == self.period:
            self.is_ready = True
            
        if self.is_ready:
            self.value = self._calculate(tp)
            # Ограничение экстремальных значений (CCI = np.clip(cci, -1000, 1000))
            return np.clip(self.value, -1000, 1000)
        
        return None

    def _calculate(self, current_tp: float) -> float:
        
        # 2. SMA Типичной Цены
        tp_array = np.array(self.tp_history)
        tp_sma = np.mean(tp_array)
        
        # 3. Среднее Абсолютное Отклонение (MD)
        # MD = 1/N * Sum(|TP_i - SMA_TP|)
        mean_dev = np.mean(np.abs(tp_array - tp_sma))
        
        # 4. CCI
        
        # Числитель (raw)
        numerator = current_tp - tp_sma
        
        # Знаменатель: 0.015 * MD_N. 
        # Добавляем защиту от деления на ноль (eps=1e-8)
        denom = 0.015 * mean_dev
        
        eps = 1e-8
        if denom < eps:
            # Если знаменатель слишком мал, исходный код устанавливает 0 
            # (так как safe_denom становится NaN, а затем np.divide устанавливает 0)
            cci = 0.0
        else:
            cci = numerator / denom

        return cci
    
    
class CCITrend: # Идентичен RSITrend
    """
    Рассчитывает абсолютное изменение CCI и его знак за лаг (N периодов).
    """
    def __init__(self, lag: int):
        self.lag = lag
        self.history_cci: Deque[float] = deque(maxlen=lag)
        self.is_ready: bool = False
    
    def update(self, cci_value: float) -> tuple[float | None, int | None]:
        
        if not self.is_ready and len(self.history_cci) == self.lag:
            self.is_ready = True

        if self.is_ready:
            cci_previous = self.history_cci[0] 
            
            trend_diff = cci_value - cci_previous
            trend_sign = int(np.sign(trend_diff))
            
            self.history_cci.append(cci_value)
            
            return trend_diff, trend_sign
            
        self.history_cci.append(cci_value)
        
        return None, None
    
class CCIAllSignals:
    """
    Потоковый расчет всех сигналов CCI, включая тренды, over_signal и signal.
    """
    def __init__(self, period: int, lo_thr: float = -100.0, hi_thr: float = 100.0, lags: List[int] = [3, 6], relaxation_rate: float = 0.2):
        self.cci = CCI(period)
        self.lo_thr = lo_thr
        self.hi_thr = hi_thr
        self.period = period
        
        # Релаксаторы
        self.over_signal_relaxer = SignalRelaxation(relaxation_rate)
        self.main_signal_relaxer = SignalRelaxation(relaxation_rate)
        
        # Тренды (используем CCITrend)
        self.trend_generators = {lag: CCITrend(lag) for lag in lags}

        # Состояния для signal (Вход/Выход)
        self.prev_overbought: int = 0
        self.prev_oversold: int = 0
        
        self.current_signals: Dict[str, Union[float, int, None]] = {}
        
    def update(self, new_candle: RenkoCandle) -> Dict[str, Union[float, int, None]] | None:
        
        cci_value = self.cci.update(new_candle)
        prefix = f"cci_{self.period}"
        
        if cci_value is None:
            return None
        
        self.current_signals[prefix] = cci_value
        
        # --- 1. Сигналы по уровням (overbought, oversold) ---
        
        # overbought: 1, если cci > hi_thr; 0 иначе.
        current_overbought = 1 if cci_value > self.hi_thr else 0
        
        # oversold: -1, если cci < lo_thr; 0 иначе.
        current_oversold = -1 if cci_value < self.lo_thr else 0
        
        # over_signal: overbought + oversold (1, -1, 0)
        over_signal_raw = current_overbought + current_oversold
        
        # --- 2. Сигналы входа/выхода (signal) ---
        
        sell_sig = 0
        buy_sig = 0
        
        # SELL: если вошли в зону overbought (overbought == 1) И (предыдущий overbought == 0)
        if current_overbought == 1 and self.prev_overbought == 0:
            sell_sig = -1

        # BUY: если вышли из зоны oversold (oversold == 0) И (предыдущий oversold == -1)
        if current_oversold == 0 and self.prev_oversold == -1:
            buy_sig = 1
            
        main_signal_raw = sell_sig + buy_sig
        
        # --- Обновление состояний для следующего шага ---
        self.prev_overbought = current_overbought
        self.prev_oversold = current_oversold
        
        # --- 3. Тренды и их знаки ---
        for lag, trend_gen in self.trend_generators.items():
            diff, sign = trend_gen.update(cci_value)
            if diff is not None:
                self.current_signals[f"{prefix}_trend{lag}"] = diff
                self.current_signals[f"{prefix}_trend{lag}_signal"] = sign
        
        # --- 4. Релаксация и сохранение ---
        
        # _over_signal
        over_signal_rel = self.over_signal_relaxer.update(over_signal_raw)
        self.current_signals[f"{prefix}_over_signal"] = over_signal_rel
        
        # _signal
        main_signal_rel = self.main_signal_relaxer.update(main_signal_raw)
        self.current_signals[f"{prefix}_signal"] = main_signal_rel

        return self.current_signals
    
    
class RegressionTracker:
    """
    Потоковый расчет параметров линейной регрессии (y ~ x) на скользящем окне.
    Регрессия выполняется на P-1 точках для соответствия исходному коду.
    """
    def __init__(self, period: int):
        self.period = period
        # Храним цены закрытия, включая текущую (P элементов)
        self.prices: Deque[float] = deque(maxlen=period)
        self.is_ready: bool = False

    def update(self, new_price: float) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """
        Обновляет цены и возвращает (slope, intercept, std_resid)
        """
        self.prices.append(new_price)
        P = self.period # P = period
        
        if not self.is_ready and len(self.prices) == P:
            self.is_ready = True

        if self.is_ready:
            # P_fit = P - 1 (количество точек для регрессии)
            P_fit = P - 1 
            
            # Y_fit: Цены C_{t-P+1} до C_{t-1} (все, кроме последней)
            Y_fit = np.array(list(self.prices)[:-1]) 
            
            # X_fit: Индексы 0 до P-2
            X_fit = np.arange(P_fit) 
            
            # --- 1. Расчет статистики для окна P-1 ---
            mean_X = X_fit.mean()
            mean_Y = Y_fit.mean()
            
            # S_xy = Sum((Y_i - mean_Y) * (X_i - mean_X))
            S_xy = (Y_fit - mean_Y) @ (X_fit - mean_X)
            
            # S_xx = Sum((X_i - mean_X)^2)
            S_xx = ((X_fit - mean_X)**2).sum() 
            
            # 2. Slope и Intercept (подогнаны под P-1 точку)
            slope = S_xy / S_xx if S_xx != 0 else 0.0
            intercept = mean_Y - slope * mean_X
            
            # 3. Стандартное отклонение остатков
            # Прогноз для P-1 точек, использованных для подгонки
            predicted_Y_fit = slope * X_fit + intercept
            
            # Остатки для P-1 точек
            residuals = Y_fit - predicted_Y_fit
            std_resid = np.std(residuals)
            
            # Возвращаем статистику, основанную на подгонке P-1
            return slope, intercept, std_resid
            
        return None, None, None
    
    
class ChannelSlopeCalculator:
    """
    Рассчитывает угловой коэффициент регрессии (Slope) на скользящем окне 
    средней линии канала, включая нормировку на среднюю цену.
    """
    def __init__(self, window: int):
        self.window = window
        # История средних цен (Mid-Channel)
        self.mid_history: Deque[float] = deque(maxlen=window)
        
        # Константы для X (индексов)
        X = np.arange(window)
        self.X_mean = X.mean()
        self.X_diff = X - self.X_mean
        self.Denom = np.sum(self.X_diff**2) # X-дисперсия
        
    def update(self, mid_channel_price: float) -> float | None:
        
        self.mid_history.append(mid_channel_price)
        
        if len(self.mid_history) < self.window:
            return None
        
        Y_window = np.array(self.mid_history) # Средние цены канала
        
        # 1. Расчет Y_mean
        Y_mean = Y_window.mean()
        
        # 2. Расчет числителя (Sxy)
        Numerator = np.sum((Y_window - Y_mean) * self.X_diff)
        
        # 3. Нормированный наклон (Slope)
        
        # Защита от деления на ноль
        if self.Denom == 0:
            return 0.0
            
        # Защита от деления на среднее значение цены
        if Y_mean == 0:
            # Соответствует np.nan в исходнике, но для потока лучше 0.0
            return 0.0
        
        # Slope = (Sxy / Sxx) / Y_mean
        slope = (Numerator / self.Denom) / Y_mean
        
        # Исходник использует fillna(0) для первых значений, но мы просто возвращаем None/0
        return slope
    
class ChannelSignalGenerator:
    """
    Потоковый расчет канала регрессии (аналог Полос Кельтнера/Боллинджера) и сигналов.
    """
    def __init__(self, period: int, slope_win: int, relaxation_rate: float = 0.2):
        self.period = period
        self.slope_win = slope_win
        
        self.regressor = RegressionTracker(period) # Регрессия для Hi/Lo
        self.slope_calculator = ChannelSlopeCalculator(slope_win) # <-- НОВЫЙ
        self.signal_relaxer = SignalRelaxation(relaxation_rate)
        
        # Переменные для хранения последних границ канала
        self.last_hi: Optional[float] = None
        self.last_lo: Optional[float] = None
        
        self.prefix = f'channel_{period}'
        

        
    def update(self, new_candle: RenkoCandle) -> Dict[str, Union[float, int, None]] | None:
        
        results: Dict[str, Union[float, int, None]] = {}
        
        # 1. Расчет регрессии
        reg_results = self.regressor.update(new_candle.close)
        
        # Проверяем, что хотя бы первый элемент кортежа (slope) не None
        # Если regressor не готов, он вернет (None, None, None)
        if reg_results[0] is None: 
            return None 

        slope, intercept, std_resid = reg_results
        
        # 2. Расчет границ канала
        # Trend Line (на последней точке): slope * (period-1) + intercept
        
        # В вашем исходном коде, последняя точка окна регрессии (index = period-1)
        x_last = self.period - 1
        trend_line_value = slope * x_last + intercept
        
        # Границы канала (2 * StdDev остатков)
        current_hi = trend_line_value + 2 * std_resid
        current_lo = trend_line_value - 2 * std_resid
        
        # Сохраняем границы для следующих шагов (имитация bfill)
        self.last_hi = current_hi
        self.last_lo = current_lo
        
        # 3. Генерация сырого сигнала пробоя
        ch_sig_raw = 0
        
        # Пробой вверх (close > upper)
        if new_candle.close > current_hi:
            ch_sig_raw = 1
        # Пробой вниз (close < lower)
        elif new_candle.close < current_lo:
            ch_sig_raw = -1
        
        # 4. Расчет Slope (наклон средней линии)
        mid_channel_price = (current_hi + current_lo) / 2
        
        # Здесь мы используем новый калькулятор
        slope_value = self.slope_calculator.update(mid_channel_price)

        
        # 5. Релаксация и сохранение
        
        # Channel Signal (с релаксацией)
        channel_signal_rel = self.signal_relaxer.update(ch_sig_raw)
        results[f"{self.prefix}_signal"] = channel_signal_rel
        
        # Границы канала (имитация bfill: если не готовы, используем предыдущие готовые)
        results[f"{self.prefix}_hi"] = self.last_hi
        results[f"{self.prefix}_lo"] = self.last_lo
        
        # Слопы (Сложность: имитация flat_25, flat_50, flat_75)
        # Мы можем рассчитать только абсолютный слоп
        if slope_value is not None:
            results[f"{self.prefix}_slope_{self.slope_win}"] = slope_value
            
            # Элементы flat_XX требуют порогов, которые здесь жестко закодированы
            results[f"{self.prefix}_slope_{self.slope_win}_flat_25"] = 1 if slope_value < 1.5e-4 else 0
            results[f"{self.prefix}_slope_{self.slope_win}_flat_50"] = 1 if slope_value < 3.4e-4 else 0
            results[f"{self.prefix}_slope_{self.slope_win}_flat_75"] = 1 if slope_value < 7e-4 else 0

        return results
    
class RangeWidth(BaseIndicator):
    """
    Потоковый расчет ширины ценового коридора в процентах (Max-Min)/Max * 100.
    """
    def __init__(self, period: int):
        super().__init__(period)
        self.price_history: Deque[float] = deque(maxlen=period)
    
    def _calculate(self) -> float:
        """Реализация абстрактного метода: расчет ширины коридора."""
        prices = np.array(self.price_history)
        max_val = prices.max()
        min_val = prices.min()
        
        # (Max - Min) / Max * 100
        if max_val != 0:
            width = (max_val - min_val) / max_val * 100
            return round(width, 3) # Округление до 3 знаков
        else:
            return 0.0

    def update(self, new_candle: RenkoCandle) -> float | None:
        """Обновление истории и вызов расчета."""
        
        self.price_history.append(new_candle.close)
        
        if not self.is_ready and len(self.price_history) == self.period:
            self.is_ready = True
            
        if self.is_ready:
            self.value = self._calculate()
            return self.value
        
        return None
    
class VolumeSlopeCalculator:
    """
    Рассчитывает нормированный угловой коэффициент регрессии по объему.
    Идентичен ChannelSlopeCalculator по логике, но работает с volume.
    """
    def __init__(self, window: int):
        self.window = window
        self.volume_history: Deque[float] = deque(maxlen=window)
        
        X = np.arange(window)
        self.X_mean = X.mean()
        self.X_diff = X - self.X_mean
        self.Denom = np.sum(self.X_diff**2)
        
    def update(self, new_volume: float) -> float | None:
        
        self.volume_history.append(new_volume)
        
        if len(self.volume_history) < self.window:
            return None
        
        Y_window = np.array(self.volume_history)
        
        Y_mean = Y_window.mean()
        
        # Sxy
        Numerator = np.sum((Y_window - Y_mean) * self.X_diff)
        
        if self.Denom == 0 or Y_mean == 0:
            return 0.0
        
        # Slope = (Sxy / Sxx) / Y_mean
        slope = (Numerator / self.Denom) / Y_mean
        
        return slope
    
class RangeAllSignals:
    """
    Расчет ширины коридора, наклона объемов, скорости и сигналов разворота.
    """
    def __init__(self, period: int, slope_window: int, relaxation_rate: float = 0.2):
        self.period = period
        self.slope_window = slope_window
        self.prefix = f'range_{period}'
        
        # Индикатор ширины (остается прежним)
        self.range_width_gen = RangeWidth(period)
        
        # Калькулятор наклона (остается прежним)
        self.volume_slope_calc = VolumeSlopeCalculator(slope_window)
        
        # История наклона для расчета скорости (diff)
        self.prev_slope: float = 0.0
        
        # Трекеры разворота (Используем ВАШ класс SignTransition)
        self.slope_rev_tracker = SignTransition()
        self.speed_rev_tracker = SignTransition()
        
        # Релаксаторы сигналов (остаются прежними)
        self.slope_rev_relaxer = SignalRelaxation(relaxation_rate)
        self.speed_rev_relaxer = SignalRelaxation(relaxation_rate)
        
        self.current_signals: Dict[str, Union[float, int, None]] = {}
        
    def update(self, new_candle: RenkoCandle) -> Dict[str, Union[float, int, None]] | None:
        
        results: Dict[str, Union[float, int, None]] = {}
        
        # 1. Расчет ширины коридора (Range Width)
        # Передаем весь объект свечи (new_candle) для доступа к цене закрытия.
        range_width_value = self.range_width_gen.update(new_candle) 
        
        if range_width_value is None:
            # Выходим, если RangeWidth еще не набрал достаточно данных (период, например, 14)
            return None 

        width_col = f'{self.prefix}_width' # 'range_14_width'
        results[width_col] = range_width_value
        
        # 2. Расчет наклона объема (Slope)
        # Передаем только объем, как это настроено в volume_slope_calc
        slope_value = self.volume_slope_calc.update(new_candle.volume)
        
        if slope_value is None:
            # Выходим, если SlopeCalculator не готов (например, из-за slope_window)
            return None

        slope_col = f'{self.prefix}_slope_{self.slope_window}'
        results[slope_col] = slope_value
        
        # 3. Расчет скорости (Speed = diff)
        speed_value = slope_value - self.prev_slope
        speed_col = f'{slope_col}_speed'
        results[speed_col] = speed_value
        
        # 4. Расчет сырых сигналов разворота
        
        # Разворот наклона
        # Принимает текущее значение, генерирует +1/-1/-0
        slope_rev_raw = self.slope_rev_tracker.update(slope_value) 
        
        # Разворот скорости
        speed_rev_raw = self.speed_rev_tracker.update(speed_value)
        
        # 5. Релаксация сигналов
        
        # Slope Reverse Signal
        slope_rev_rel = self.slope_rev_relaxer.update(slope_rev_raw)
        slope_rev_col = f'{slope_col}_reverse_signal'
        results[slope_rev_col] = slope_rev_rel
        
        # Speed Reverse Signal
        speed_rev_rel = self.speed_rev_relaxer.update(speed_rev_raw)
        speed_rev_col = f'{speed_col}_reverse_signal'
        results[speed_rev_col] = speed_rev_rel
        
        # 6. Сумма релаксированных сигналов
        # (df['..._reverse_sum_signal'] = df['..._reverse_signal'] + df['..._speed_reverse_signal'])
        sum_rev_col = f'{slope_col}_reverse_sum_signal'
        results[sum_rev_col] = slope_rev_rel + speed_rev_rel
        
        # Обновление состояния для следующего шага
        self.prev_slope = slope_value
        
        return results
    
    
class EMATracker:
    """
    Потоковый расчет Экспоненциальной Скользящей Средней (EMA).
    """
    def __init__(self, period: int):
        self.period = period
        # Коэффициент сглаживания: 2 / (period + 1)
        self.alpha = 2 / (period + 1)
        self.inv_alpha = 1 - self.alpha # 1 - alpha

        # История цен для начального расчета (SMA)
        self.price_history: deque[float] = deque(maxlen=period)
        self.ema: Optional[float] = None
        self.is_ready: bool = False

    def update(self, new_price: float) -> Optional[float]:
        """
        Обрабатывает новую цену и возвращает текущее значение EMA.
        
        Начальный расчет (инициализация) выполняется как простая скользящая средняя (SMA).
        """
        
        # Накопление данных для инициализации
        if not self.is_ready:
            self.price_history.append(new_price)
            
            if len(self.price_history) == self.period:
                # Инициализация: EMA = SMA первых N цен
                self.ema = sum(self.price_history) / self.period
                self.is_ready = True
                
        # Основной расчет
        elif self.ema is not None:
            # EMA_t = (New_Price * alpha) + (EMA_{t-1} * (1 - alpha))
            self.ema = (new_price * self.alpha) + (self.ema * self.inv_alpha)

        return self.ema

class MACDLineGenerator:
    """
    Потоковый расчет трех основных линий MACD: MACD, Signal, Histogram.
    """
    def __init__(self, k: int, d: int, signal: int):
        # 1. EMA для цены (k - длинная, d - короткая)
        self.ema_k = EMATracker(k)
        self.ema_d = EMATracker(d)
        
        # 2. EMA для MACD Line (Signal Line)
        self.ema_s = EMATracker(signal)

        self.is_ready: bool = False

    def update(self, price: float) -> Tuple[float, float, float] | None:
        """
        Обновляет цены и возвращает (macd_line, signal_line, hist_line).
        """
        ema_k_val = self.ema_k.update(price)
        ema_d_val = self.ema_d.update(price)
        
        # MACD Line (короткая - длинная)
        if ema_k_val is not None and ema_d_val is not None:
            macd_line = ema_d_val - ema_k_val
            self.is_ready = True
        else:
            return None

        # Signal Line (EMA от MACD Line)
        signal_line = self.ema_s.update(macd_line)
        
        if signal_line is None:
            return None
        
        # Histogram
        hist_line = macd_line - signal_line
        
        return macd_line, signal_line, hist_line
    
class MACDSignalGenerator:
    """
    Генератор всех сигналов MACD (пересечения, градиенты, тренды).
    """
    def __init__(self, k: int, d: int, signal: int, grads: List[int], relaxation_rate: float):
        self.k, self.d, self.signal = k, d, signal
        self.prefix = f"macd_{k}_{d}_{signal}"
        self.grads = grads
        
        # Линии MACD
        self.macd_lines = MACDLineGenerator(k, d, signal)
        
        # --- Трекеры сигналов ---
        self.cross_tracker = SignTransition() # Пересечение MACD/Signal
        
        # Для MACD Gradient (long trend)
        self.prev_macd_line: float = 0.0
        self.prev_grad_macd: int = 0 # -1, 0, 1
        self.long_trend_relaxer = SignalRelaxation(relaxation_rate)

        # Для Histogram Gradient (macd_gradX_signal)
        self.hist_grad_generators = {}
        for g in grads:
            self.hist_grad_generators[g] = {
                'hist_history': deque(maxlen=g + 1), # H_t-g до H_t
                'grad_history': deque(maxlen=g),     # Градиенты Grad_{t-g} до Grad_{t-1}
                'grad_relaxer': SignalRelaxation(relaxation_rate)
            }
            
        # Для релаксации
        self.cross_relaxer = SignalRelaxation(relaxation_rate)
        self.hist_signal_relaxer = SignalRelaxation(relaxation_rate)
        
        # Предыдущие значения для hist_signal
        self.prev_hist_line: float = 0.0
        self.prev_signal_line: float = 0.0

    def update(self, new_candle: RenkoCandle) -> Dict[str, Union[float, int, None]] | None:
        
        macd_results = self.macd_lines.update(new_candle.close)
        if macd_results is None:
            return None
        
        macd_line, signal_line, hist_line = macd_results
        results: Dict[str, Union[float, int, None]] = {}

        # 1. Основные линии MACD (если self.keep_curves = True)
        results[self.prefix] = macd_line
        results[f"{self.prefix}_s"] = signal_line
        results[f"{self.prefix}_h"] = hist_line

        # --- 2. Пересечение MACD и Signal Line (cross_signal) ---
        # MACD > Signal -> diff > 0. Используем SignTransition.
        cross_raw = self.cross_tracker.update(macd_line - signal_line)
        results[f"{self.prefix}_cross_signal"] = self.cross_relaxer.update(cross_raw)

# 3. Пересечение Histogram и Signal Line (hist_signal)
        hist_raw = 0
        
        # Разница для проверки пересечения 
        diff_h = hist_line - signal_line
        prev_diff_h = self.prev_hist_line - self.prev_signal_line
        
        sign_current = int(np.sign(diff_h))
        sign_prev = int(np.sign(prev_diff_h))

        # Используем исправленный SignTransition для чистого пересечения
        # (Hist - Signal) пересекает 0
        if sign_prev == -1 and sign_current == 1: # Пересечение снизу вверх
            # BUY: Вверх, когда Hist Line отрицательна (hist_line < 0)
            if hist_line < 0:
                hist_raw = 1
        elif sign_prev == 1 and sign_current == -1: # Пересечение сверху вниз
            # SELL: Вниз, когда Hist Line положительна (hist_line > 0)
            if hist_line > 0:
                hist_raw = -1
                
        results[f"{self.prefix}_hist_signal"] = self.hist_signal_relaxer.update(hist_raw)
        
        # --- 4. Градиент MACD (long_trend_signal) ---
        long_trend_raw = 0
        
        # grad_macd = sign(macd_line - macd_line_prev)
        current_grad_macd = int(np.sign(macd_line - self.prev_macd_line))
        
        # (grad_macd != grad_prev) & (grad_macd > 0) -> 1
        # (grad_macd != grad_prev) & (grad_macd < 0) -> -1
        if current_grad_macd != self.prev_grad_macd:
            if current_grad_macd == 1:
                long_trend_raw = 1
            elif current_grad_macd == -1:
                long_trend_raw = -1
        
        results[f"{self.prefix}_long_trend_signal"] = self.long_trend_relaxer.update(long_trend_raw)
        
        # --- 5. Градиенты Гистограммы (gradX_signal) ---
# --- 5. Градиенты Гистограммы (gradX_signal) ---
        all_hist_grad_signals = []

        for g, data in self.hist_grad_generators.items():
            hist_history = data['hist_history']
            grad_history = data['grad_history']
            relaxer = data['grad_relaxer']
            
            hist_history.append(hist_line)
            hist_grad_raw = 0

            # Шаг 1: Если hist_history готова (g+1 элемент), рассчитываем Grad_t.
            if len(hist_history) == g + 1:
                
                hist_before_g = hist_history[0] # H_{t-g}
                current_grad = hist_line - hist_before_g # Grad_t

                # Шаг 2: Только если grad_history содержит g элементов, 
                # мы можем получить Grad_{t-g} и сгенерировать сигнал.
                # NOTE: grad_history достигает длины g только после g+1 шага цикла.
                
                # Проверка: На этом шаге grad_history содержит Grad_{t-g+1} .. Grad_{t-1}
                # (длина g-1) перед append, и Grad_{t-g} .. Grad_{t-1} (длина g) после первого полного цикла.
                
                if len(grad_history) == g:
                    
                    # 2.1. Получение градиента g периодов назад (Grad_{t-g})
                    grad_before_g = grad_history[0] 
                    
                    current_grad_sign = int(np.sign(current_grad))
                    prev_grad_sign_g = int(np.sign(grad_before_g)) # Знак Grad_{t-g}

                    # 2.2. Логика сигнала
                    if current_grad_sign != prev_grad_sign_g:
                        
                        if current_grad_sign == 1 and hist_line < 0: 
                            hist_grad_raw = 1
                        
                        elif current_grad_sign == -1 and hist_line > 0: 
                            hist_grad_raw = -1
                            
                    # 2.3. Удаляем Grad_{t-g} для скользящего окна
                    grad_history.popleft()

                # Шаг 3: Обновляем историю:
                # В конце этого шага grad_history должна содержать Grad_{t-g+1} ... Grad_{t}
                grad_history.append(current_grad)
                
                # Удаляем H_{t-g}
                hist_history.popleft() 
                
            results[f"{self.prefix}_grad{g}_signal"] = relaxer.update(hist_grad_raw)
            all_hist_grad_signals.append(results[f"{self.prefix}_grad{g}_signal"])

        # --- 6. Сумма сигналов ---
        # signal_columns = [x for x in macd_df.columns if '_signal' in x]
        # macd_df[prefix+'_sig_sum'] = macd_df[signal_columns].sum(axis=1).fillna(0)
        
        sum_signals = results[f"{self.prefix}_cross_signal"] + results[f"{self.prefix}_hist_signal"] + results[f"{self.prefix}_long_trend_signal"]
        for s in all_hist_grad_signals:
             sum_signals += s

        results[f"{self.prefix}_sig_sum"] = sum_signals
        
        # 7. Обновление предыдущих значений для следующего шага
        self.prev_macd_line = macd_line
        self.prev_grad_macd = current_grad_macd
        self.prev_hist_line = hist_line
        self.prev_signal_line = signal_line
        
        return results
    
    
class SMATracker:
    """
    Потоковый расчет Simple Moving Average (SMA).
    """
    def __init__(self, period: int):
        self.period = period
        self.history: Deque[float] = deque(maxlen=period)
        self.current_sum: float = 0.0
        self.is_ready: bool = False

    def update(self, new_value: float) -> Optional[float]:
        # 1. Обновление скользящей суммы
        if len(self.history) == self.period:
            # Вычитаем самый старый элемент, если окно полное
            old_value = self.history.popleft()
            self.current_sum -= old_value
            self.is_ready = True
        
        # Добавляем новый элемент
        self.history.append(new_value)
        self.current_sum += new_value
        
        # 2. Расчет SMA
        if self.is_ready:
            return self.current_sum / self.period
        else:
            return None
        
class StochasticRangeTracker:
    """
    Потоковый расчет H_max, L_min и сырого %K.
    Использует очередь для O(1) добавления и O(k) поиска Max/Min.
    """
    def __init__(self, k: int):
        self.k = k
        self.h_history: Deque[float] = deque(maxlen=k)
        self.l_history: Deque[float] = deque(maxlen=k)
        self.is_ready: bool = False

    def update(self, high: float, low: float, close: float) -> Optional[float]:
        
        self.h_history.append(high)
        self.l_history.append(low)
        
        if len(self.h_history) < self.k:
            return None
        
        if not self.is_ready:
            self.is_ready = True
            
        # Находим Max(High) и Min(Low) в текущем окне
        # В отличие от SMA, для Max/Min нужно сканировать deque (O(k)),
        # если не использовать специализированную структуру (например, Deque с Max/Min).
        Hmax = max(self.h_history)
        Lmin = min(self.l_history)
        
        # Расчет сырого %K: 100 * (Close - Lmin) / (Hmax - Lmin + 1e-9)
        denominator = Hmax - Lmin
        if denominator == 0:
            K_raw = 0.0 if close == Lmin else 100.0
        else:
            K_raw = 100.0 * (close - Lmin) / denominator
            
        # Ограничиваем значение от 0 до 100 (хотя оно должно быть в этом диапазоне)
        return max(0.0, min(100.0, K_raw))
    
class StochasticSignalGenerator:
    
    def __init__(self, k: int, d: int, smooth_k: int, lo_thr: float, hi_thr: float, lags: List[int], relaxation_rate: float):
        
        self.k, self.d, self.smooth_k = k, d, smooth_k
        self.lo_thr, self.hi_thr = lo_thr, hi_thr
        self.lags = lags
        self.prefix = f'stoch_{k}_{d}_{smooth_k}'
        
        # 1. Сырой %K (k-период)
        self.k_range_tracker = StochasticRangeTracker(k)
        
        # 2. Сглаживание %K (smooth_k-период)
        # Если smooth_k = 1, то K_smooth = K_raw
        self.k_smoother = SMATracker(smooth_k) if smooth_k > 1 else None
        
        # 3. Расчет %D (d-период SMA от %K_smooth)
        # Если d = 1, то %D = %K_smooth
        self.d_calculator = SMATracker(d) if d > 1 else None
        
        # 4. Релаксация основного сигнала
        self.signal_relaxer = SignalRelaxation(relaxation_rate)
        
        # 5. Хранение истории для тренда по лагам
        max_lag = max(lags) if lags else 1
        # Хранение истории для тренда по лагам: max_lag + 1 элемент (для t и t-max_lag)
        self.k_history_for_lags: Deque[float] = deque(maxlen=max_lag + 1)
        
        # Предыдущее значение %K для генерации основного сигнала
        self.prev_k_value: Optional[float] = None


    def update(self, new_candle: RenkoCandle) -> Dict[str, Union[float, int, None]] | None:
        
        results: Dict[str, Union[float, int, None]] = {}
        
        # --- 1. Расчет K_raw ---
        k_raw = self.k_range_tracker.update(new_candle.high, new_candle.low, new_candle.close)
        
        if k_raw is None:
            return None
        
        # --- 2. K_smooth ---
        if self.k_smoother:
            k_smooth = self.k_smoother.update(k_raw)
            if k_smooth is None:
                return None
        else:
            k_smooth = k_raw # smooth_k = 1
            
        results[f"{self.prefix}_k"] = k_smooth
        
        # --- 3. %D ---
        if self.d_calculator:
            d_value = self.d_calculator.update(k_smooth)
            if d_value is None:
                return None
        else:
            d_value = k_smooth # d = 1
            
        results[f"{self.prefix}_d"] = d_value
        
        # --- 4. Основной сигнал (пересечение %K с порогами) ---
        raw_signal = 0
        current_k = k_smooth
        
        if self.prev_k_value is not None:
            
            # +1: %K пересекает lo_thr снизу вверх (сигнал на покупку)
            # cross_up_lo: (ks[:-1] < lo_thr) & (ks[1:] >= lo_thr)
            if self.prev_k_value < self.lo_thr and current_k >= self.lo_thr:
                raw_signal = 1
            
            # -1: %K пересекает hi_thr снизу вверх (сигнал на продажу)
            # cross_up_hi: (ks[:-1] < hi_thr) & (ks[1:] >= hi_thr)
            if self.prev_k_value < self.hi_thr and current_k >= self.hi_thr:
                # Приоритет имеет сигнал -1
                raw_signal = -1
        
        # Модификация и сохранение основного сигнала
        results[f"{self.prefix}_signal"] = self.signal_relaxer.update(raw_signal)
        
        # --- 5. Тренды по лагам от %K ---
        self.k_history_for_lags.append(k_smooth) # K_t
        current_k = k_smooth # t
        
        for lag in self.lags:
            trend_col = f"{self.prefix}_trend{lag}"
            trend_signal_col = f"{self.prefix}_trend{lag}_signal"
            
            trend_signal = 0
            
            # Проверяем, что в истории достаточно элементов для лага (t-lag)
            if len(self.k_history_for_lags) > lag:
                
                # k_at_lag - это K_{t-lag}. Индекс -(lag + 1) указывает на элемент
                # который был добавлен 'lag' шагов назад (с учетом maxlen=max_lag+1)
                k_at_lag = self.k_history_for_lags[-(lag + 1)] 
                
                trend_value = current_k - k_at_lag
                results[trend_col] = trend_value
                
                # Используем np.sign, как в оригинале (предполагая, что np импортирован)
                trend_signal = int(np.sign(trend_value))
                results[trend_signal_col] = trend_signal
            
            else:
                results[trend_col] = None
                results[trend_signal_col] = None
                
        # --- 6. Обновление состояния для следующего шага ---
        self.prev_k_value = current_k
        
        return results
    
    
class RollingSlopeTracker:
    
    def __init__(self, N: int):
        self.N = N
        self.duration_history: Deque[float] = deque(maxlen=N)
        
        # 1. Центрированные индексы X (x_i - x_mean)
        x = np.arange(N, dtype=np.float64)
        x_mean = x.mean() # 2.0 для N=5
        self.x_indices: List[float] = (x - x_mean).tolist() # [-2.0, -1.0, 0.0, 1.0, 2.0]
        
        # 2. Знаменатель (np.sum(x_diff**2))
        self.denom: float = float(np.sum((x - x_mean)**2)) # 10.0
        
        if self.denom == 0:
            self.safe_denom = np.nan
        else:
            self.safe_denom = self.denom
        
        # Динамические переменные
        self.sum_y: float = 0.0     
        self.is_ready: bool = False

    def update(self, new_duration: float) -> Optional[float]:
        
        # ... (Обновление duration_history) ...
        old_duration: float = 0.0
        if len(self.duration_history) == self.N:
            old_duration = self.duration_history.popleft()
            self.is_ready = True
        
        self.duration_history.append(new_duration)
        
        if len(self.duration_history) < self.N:
            return None
        
        # 1. Обновление sum_y
        self.sum_y += new_duration
        if self.is_ready:
            self.sum_y -= old_duration
            
        # 2. Расчет sum_xy (O(N) с X = -2, -1, 0, ...)
        sum_xy: float = 0.0
        # Используем numpy для быстрого скалярного произведения, если доступно, 
        # или продолжаем использовать цикл для точности float.
        for i, y in enumerate(self.duration_history):
             # Умножаем центрированный индекс на текущее значение y
             sum_xy += self.x_indices[i] * y
        
        # 3. Расчет OLS Slope: ols_slope = sum_xy / denom
        # Защита от деления на ноль для safe_denom:
        if self.safe_denom == np.nan:
             return 0.0 # Т.к. в исходном коде: denom = np.nan, slope = nan, fillna(0)
             
        ols_slope = sum_xy / self.safe_denom
        
        # 4. Расчет y_mean (bar{y})
        y_mean = self.sum_y / self.N
        
        # 5. Применение нормализации (slope = ols_slope / safe_y_mean)
        if abs(y_mean) < 1e-9: # Использование порога для сравнения с нулем
             # Защита от деления на ноль и np.where(y_mean == 0, np.nan, y_mean)
             final_slope = 0.0 
        else:
             final_slope = ols_slope / y_mean

        # 6. Заполнение нулями (исходный код использует fillna(0))
        if np.isnan(final_slope):
             return 0.0
             
        return final_slope
    
    
class DurationSignalGenerator:
    
    def __init__(self, v_reg_len: int, relaxation_rate: float):
        
        self.v_reg_len = v_reg_len
        self.prefix = 'duration_slope_' + str(v_reg_len)
        
        # 1. Расчет угла наклона
        self.slope_tracker = RollingSlopeTracker(v_reg_len)
        
        # 2. Трекеры сигналов разворота
        self.slope_transition = SignTransition()
        self.speed_transition = SignTransition()
        
        # 3. Релаксация
        self.slope_relaxer = SignalRelaxation(relaxation_rate)
        self.speed_relaxer = SignalRelaxation(relaxation_rate)
        
        # 4. История для расчета скорости (diff(slope))
        self.prev_slope: Optional[float] = None
        
        # 5. История для расчета дельт (duration.shift(1, 3, 5, 10))
        # Максимально необходимый лаг: 10
        max_lag = 10
        self.duration_history: Deque[float] = deque(maxlen=max_lag + 1)
        self.lags = [1, 3, 5, 10]


    def update(self, new_candle: RenkoCandle) -> Dict[str, Union[float, int, None]] | None:
        
        duration = new_candle.duration 
        results: Dict[str, Union[float, int, None]] = {}
        
        # ИСПРАВЛЕНИЕ 1: Добавляем 'duration' в результат
        results['duration'] = duration
        
        # --- 1. Угол наклона (Slope) ---
        current_slope = self.slope_tracker.update(duration)
        
        if current_slope is None:
            return None
            
        results[self.prefix] = current_slope
        
        # --- 2. Скорость изменения угла наклона (Speed) ---
        slope_speed = None
        if self.prev_slope is not None:
            slope_speed = current_slope - self.prev_slope
        
        # Если скорость не NaN, добавляем ее в результаты
        if slope_speed is not None:
            speed_col = self.prefix + '_speed'
            results[speed_col] = slope_speed
            
            # --- 3. Сигнал разворота скорости ---
            speed_raw = self.speed_transition.update(slope_speed)
            results[speed_col + '_reverse_signal'] = self.speed_relaxer.update(speed_raw)
        
        # --- 4. Сигнал разворота угла наклона ---
        slope_raw = self.slope_transition.update(current_slope)
        results[self.prefix + '_reverse_signal'] = self.slope_relaxer.update(slope_raw)

        # --- 5. Разностные сигналы (Deltas) ---
        self.duration_history.append(duration)
        
        for lag in self.lags:
            delta_col = f"duration_delta_{lag}"
            
            if len(self.duration_history) > lag:
                # duration.shift(lag) - это элемент t-lag
                duration_at_lag = self.duration_history[-(lag + 1)] 
                
                # df.duration - df.duration.shift(lag)
                delta_value = duration - duration_at_lag
                results[delta_col] = delta_value
            else:
                results[delta_col] = None
        
        # --- 6. Сумма сигналов ---
        # NOTE: Нужно использовать .get() так как speed_reverse_signal может быть None
        sum_signal = results.get(self.prefix + '_reverse_signal', 0.0) + results.get(self.prefix + '_speed_reverse_signal', 0.0)
        results[self.prefix + '_reverse_sum_signal'] = sum_signal
        
        # --- 7. Обновление состояния ---
        self.prev_slope = current_slope
        
        return results
    

class VolumeSignalGenerator:
    
    def __init__(self, v_reg_len: int, relaxation_rate: float):
        
        self.v_reg_len = v_reg_len
        self.prefix = 'v_slope_' + str(v_reg_len)
        
        # 1. Расчет угла наклона (OLS с нормализацией на y_mean)
        self.slope_tracker = RollingSlopeTracker(v_reg_len)
        
        # 2. Трекеры сигналов разворота
        self.slope_transition = SignTransition()
        self.speed_transition = SignTransition()
        
        # 3. Релаксация
        self.slope_relaxer = SignalRelaxation(relaxation_rate)
        self.speed_relaxer = SignalRelaxation(relaxation_rate)
        
        # 4. История для расчета скорости (diff(slope))
        self.prev_slope: Optional[float] = None
        
        # 5. История для расчета дельт (v.shift(1, 3, 5, 10))
        max_lag = 10
        self.volume_history: Deque[float] = deque(maxlen=max_lag + 1)
        self.lags = [1, 3, 5, 10]


    def update(self, new_candle: RenkoCandle) -> Dict[str, Union[float, int, None]] | None:
        
        # Входные данные: v - объем
        volume = new_candle.v 
        results: Dict[str, Union[float, int, None]] = {}
        
        # --- 1. Угол наклона (Slope) ---
        current_slope = self.slope_tracker.update(volume)
        
        if current_slope is None:
            return None
            
        results[self.prefix] = current_slope
        
        # --- 2. Скорость изменения угла наклона (Speed) ---
        slope_speed = None
        if self.prev_slope is not None:
            # np.diff(df['v_slope_X'])
            slope_speed = current_slope - self.prev_slope
        
        # Если скорость не NaN, добавляем ее в результаты
        if slope_speed is not None:
            speed_col = self.prefix + '_speed'
            results[speed_col] = slope_speed
            
            # --- 3. Сигнал разворота скорости ---
            speed_raw = self.speed_transition.update(slope_speed)
            results[speed_col + '_reverse_signal'] = self.speed_relaxer.update(speed_raw)
        
        # --- 4. Сигнал разворота угла наклона ---
        slope_raw = self.slope_transition.update(current_slope)
        results[self.prefix + '_reverse_signal'] = self.slope_relaxer.update(slope_raw)

        # --- 5. Разностные сигналы (Deltas) ---
        self.volume_history.append(volume)
        
        for lag in self.lags:
            delta_col = f"v_delta_{lag}"
            
            if len(self.volume_history) > lag:
                # v.shift(lag) - это элемент t-lag
                volume_at_lag = self.volume_history[-(lag + 1)] 
                
                # df.v - df.v.shift(lag)
                delta_value = volume - volume_at_lag
                results[delta_col] = delta_value
            else:
                results[delta_col] = None
        
        # --- 6. Сумма сигналов ---
        sum_signal = results.get(self.prefix + '_reverse_signal', 0.0) + results.get(self.prefix + '_speed_reverse_signal', 0.0)
        results[self.prefix + '_reverse_sum_signal'] = sum_signal
        
        # --- 7. Обновление состояния ---
        self.prev_slope = current_slope
        
        return results