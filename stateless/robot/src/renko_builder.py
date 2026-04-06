from packages import RenkoCandle, RenkoList, Trade


class RenkoBuilder:
    '''
        Создаёт ренко-свечи, складывая текущую незавершённую ренко-свечу со сделкой. Логика описана в докстринге для RenkoCandle. 
        После формирования новых свечек распределяет между ними объёмы и длительности, если несколько свечек образовано одной сделкой.
        ---------------------------------------------------------------
        current_renko: RenkoCandle - текущая незавершённая ренко-свеча на момент создания объекта
    '''
    def __init__(self, current_renko: RenkoCandle):
        self._current_renko = current_renko

    def generate_renko(self, trade: Trade) -> RenkoList:
        new_renko = self._current_renko + trade
        new_renko.share_volume_and_duration()
        self._current_renko = new_renko[-1]
        return new_renko[:-1]
