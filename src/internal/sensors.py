"""センサーを要求されたときだけ読み取り、Android向けの応答を作る。"""
from .commands import SendSensorPacket

UART_TIMEOUT = 0.05


class SensorReadError(Exception):
    pass


class SensorReader:
    def __init__(self, sense=None, robot=None):
        self._sense = sense
        self._robot = robot

    def _get_sense(self):
        if self._sense is None:
            from sense_hat import SenseHat

            self._sense = SenseHat()
        return self._sense

    def _get_robot(self):
        if self._robot is None:
            from . import r3pi

            self._robot = r3pi
        return self._robot

    def read(self, code: str) -> SendSensorPacket:
        response = SendSensorPacket()
        try:
            match code:
                case "TP":
                    response.push("Temperature", self._get_sense().get_temperature())
                case "BR":
                    # Sense HAT V2のClear光量。luxへの換算はしない。
                    response.push("Brightness", int(self._get_sense().colour.brightness))
                case "DT":
                    # この実験の距離センサーはADCの生値を使用する。
                    response.push("Distance", self._get_robot().pot_voltage(timeout=UART_TIMEOUT))
                case "BT":
                    response.push("Battery", self._get_robot().battery(timeout=UART_TIMEOUT))
                case _:
                    raise ValueError("存在しないセンサーです")
        except (ImportError, OSError, AttributeError, RuntimeError, ValueError) as error:
            # 未接続やタイムアウトを、値0として扱わず要求元へ知らせる。
            detail = str(error).replace(":", " ").replace("\r", " ").replace("\n", " ")
            raise SensorReadError(f"{code} の取得に失敗しました ({detail})") from error
        return response


_reader = SensorReader()


def read_sensor(code: str) -> SendSensorPacket:
    return _reader.read(code)
