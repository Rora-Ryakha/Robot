import os
import os.path as osp
import pickle

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier


class LightModel():
    
    def __init__(self, model_save_path_w_fname, verbose=True):
        '''
        Упрощенный класс с прогнозной моделью.
        Для инициализации указать путь к файлу со словарем модели.

        Пример кода инициализации:
        m = LightModel('outputs/18/146_buy_v2_25_0.6_0.1_shift_12_hard_from_42.model')
        
        Пример входного словаря для прогноза - в атрибуте input_data_example.
        
        Перечень нужных индикаторов - в атрибуте X_features.

        _
        '''
        
        assert model_save_path_w_fname.split('.')[-1]=='model', 'Файл должен иметь расширение "model"'
        
        with open(osp.join(model_save_path_w_fname), 'rb') as f:
            model_dict = pickle.load(f)
            f.close()

        assert set(model_dict.keys()) == {'model', 'model_code', 'source_notebook', 'test_data', 'test_result'}, "В словаре модели должны быть ключи {'model', 'model_code', 'source_notebook', 'test_data', 'test_result'}"
            
        self.model = model_dict['model']    
        self.model_code = model_dict['model_code']    
        self.X_features = self.model.feature_names_
        self.test_data = model_dict['test_data']
        self.test_result = model_dict['test_result']
        self.model_info = model_dict['source_notebook']
        self.input_data_example = model_dict['test_data'].to_dict(orient='records')[0]
        
        assert np.all(self.model.predict_proba(model_dict['test_data'])==model_dict['test_result']), 'Модель не проходит тестовый предикт'
        if verbose: print('Тестовый предикт пройден:\n', self.model.predict_proba(model_dict['test_data']), '==', model_dict['test_result'], '\n', 'Инициализация успешна. Пример входного словаря для прогноза можно взять в атрибуте input_data_example.')
                
        
    def predict_proba_single_line(self, predict_data):
        '''Прогноз одной строки данных - для одной свечи.
        Входные данные - словарь индикаторов вида:
        {'duration': 2.0, 'ema_200': 0.960198163986206, 'ema_200_reverse_signal': 0.0, ...., 'cc': 'g'}
        В ключах словаря должны быть все ключи атрибута self.input_data_example

        На выходе метода: вероятность, что событие 0 и вероятность, что событие 1, например: array([0.85981166, 0.14018834]) - вероятность нуля 0.85.

        !!! ДЛЯ НАШИХ ЦЕЛЕЙ БРАТЬ ВТОРОЕ ЗНАЧЕНИЕ, в примере это 0.14018834.

        _   
        '''
                
        assert type(predict_data)==dict, "Входные данные - словарь индикаторов вида: {'duration': 2.0, 'ema_200': 0.960198163986206, 'ema_200_reverse_signal': 0.0, ...., 'cc': 'g'}, а у вас "+str(type(predict_data))
        predict_vector = []
        for k in self.input_data_example: 
            assert k in predict_data.keys(), 'Во входном словаре отсутствует ключ '+k
            predict_vector.append(predict_data[k])
        return self.model.predict_proba(predict_vector)

class Model():
    
    def __init__(self, model_save_path_w_fname, verbose=False):
        '''Базовый класс с прогнозной моделью.
        Инициализация словарём с ключами ['dataset_params', 'train_data_cfg', 'optimization_data', 'production_model', 'indicators_cfg'] из файла с расширением .model по адресу model_save_path_w_fname'''
        assert model_save_path_w_fname.split('.')[-1]=='model', 'Файл должен иметь расширение "model"'
        with open(osp.join(model_save_path_w_fname), 'rb') as f:
            model_dict = pickle.load(f)
            f.close()
        for k in model_dict.keys(): assert k in ['dataset_params', 'train_data_cfg', 'optimization_data', 'production_model', 'indicators_cfg'], 'В загруженном словаре модели нет ключа '+k
        assert type(model_dict), 'Загруженный файл должен быть словарем'
        self.model = model_dict['production_model']['model']        
        self.f1 = model_dict['production_model']['f1']
        self.X_features = model_dict['production_model']['X_features']        
        assert np.all(self.model.predict_proba(model_dict['production_model']['test_data'])==model_dict['production_model']['test_result']), 'Модель не проходит тестовый предикт'
        if verbose: print('Тестовый предикт пройден:\n', self.model.predict_proba(model_dict['production_model']['test_data']), model_dict['production_model']['test_result'])
        self.test_data = model_dict['production_model']['test_data']
        self.test_result = model_dict['production_model']['test_result']
        self.model_theshold = model_dict['production_model']['threshold']
        self.input_data_example = model_dict['production_model']['test_data'].to_dict(orient='records')[0]        
        self.full_model_history = model_dict        
        
    def predict_proba_single_line(self, predict_data):
        '''Прогноз одной строки данных - для одной свечи.
        Входные данные - словарь индикаторов вида:
        {'duration': 2.0, 'ema_200': 0.960198163986206, 'ema_200_reverse_signal': 0.0, ...., 'cc': 'g'}
        В ключах словаря должны быть все ключи атрибута self.input_data_example'''
        
        assert type(predict_data)==dict, "Входные данные - словарь индикаторов вида: {'duration': 2.0, 'ema_200': 0.960198163986206, 'ema_200_reverse_signal': 0.0, ...., 'cc': 'g'}, а у вас "+str(type(predict_data))
        predict_vector = []
        for k in self.input_data_example: 
            assert k in predict_data.keys(), 'Во входном словаре отсутствует ключ '+k
            predict_vector.append(predict_data[k])
        return self.model.predict_proba(predict_vector)