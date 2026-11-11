from dataclasses import dataclass, field
from math import isfinite


def is_float(s: str) -> bool:
    try:
        float(s)
    except ValueError:
        return False
    return True


@dataclass
class Command:
    key: str
    value: str | float | int | bool

    def __init__(self, raw_command: str, delimiter: str = "=") -> None:
        commands = raw_command.split(delimiter)

        if len(commands) != 2:
            msg = "Invalid Command"
            raise ValueError(msg)

        self.key = commands[0].strip()
        raw_value = commands[1].strip()

        match self.key:
            case "Forward" | "Backward" | "Left" | "Right":
                self.value = float(raw_value)
                if not 0.0 <= self.value <= 1.0:
                    raise ValueError("値が不正です")
            case "LeftMotor" | "RightMotor":
                self.value = float(raw_value)
                if not -1.0 <= self.value <= 1.0:
                    raise ValueError("値が不正です")
            case "Stop":
                self.value = int(raw_value)
            case "BoundaryDetection":
                if raw_value.lower() not in ("false", "true", "0", "1"):
                    raise ValueError("値が不正です")
                self.value = raw_value.lower() in ("true", "1")
            case "Sensor":
                if raw_value not in ("TP", "BR", "DT", "BT"):
                    raise ValueError("存在しないセンサーです")
                self.value = raw_value
            case _:
                raise ValueError("存在しないコマンドです")


@dataclass
class ReceiveCommandPacket:
    commands: list[Command]

    def __init__(self, raw_commands: str, delimiter: str = ",") -> None:
        coms = raw_commands.split(delimiter)
        self.commands = [Command(c) for c in coms]


# None は整数または文字列、それ以外は送信時の小数桁数。
_SENSOR_FORMATS: dict[str, int | None] = {
    "Time": 1,
    "SeqNum": None,
    "Status": None,
    "RandVal": None,
    "IP": None,
    "Brightness": None,
    "Distance": 2,
    "Battery": 2,
    "AccelX": 2,
    "AccelY": 2,
    "AccelZ": 2,
    "LinePosition": 2,
    "Temperature": 1,
    "Humidity": 1,
    "Speed": 1,
    "Count": None,
    "Laptime": 2,
}


@dataclass
class SensorValue:
    key: str
    value: str | float | int

    def __post_init__(self) -> None:
        if self.key not in _SENSOR_FORMATS:
            raise ValueError("存在しないセンサー項目です")
        if isinstance(self.value, bool):
            raise ValueError("値の型が不正です")

        match self.key:
            case "Status":
                # Status の定義が未指定のため、statusToString() の結果を受け取る。
                if not isinstance(self.value, str):
                    raise ValueError("Status は文字列で指定してください")
                if not self.value or any(c in self.value for c in ":=\r\n"):
                    raise ValueError("Status の値が不正です")
            case "IP":
                # C++ の unsigned char と同じく、IP の1オクテットを扱う。
                if not isinstance(self.value, int) or not 0 <= self.value <= 255:
                    raise ValueError("IP は 0～255 の整数で指定してください")
            case "SeqNum" | "RandVal" | "Brightness" | "Count":
                if not isinstance(self.value, int):
                    raise ValueError("整数で指定してください")
            case _:
                if not isinstance(self.value, (int, float)):
                    raise ValueError("実数で指定してください")
                self.value = float(self.value)
                if not isfinite(self.value):
                    raise ValueError("有限の実数で指定してください")

    def to_string(self) -> str:
        digits = _SENSOR_FORMATS[self.key]
        if digits is not None:
            return f"{self.key}={self.value:.{digits}f}"
        return f"{self.key}={self.value}"

    def __str__(self) -> str:
        return self.to_string()


@dataclass
class SendSensorPacket:
    values: dict[str, SensorValue] = field(default_factory=dict)

    def push(self, key: str, value: str | float) -> None:
        # 検証に成功してから更新し、同じ項目は最後の値を使用する。
        self.values[key] = SensorValue(key, value)

    def clear(self) -> None:
        self.values.clear()

    def to_string(self) -> str:
        elements = []
        # 登録順にかかわらず、Elements::toString() と同じ順序にする。
        for key in _SENSOR_FORMATS:
            if key not in self.values:
                continue
            sensor = self.values[key]
            if key == "Status" and sensor.value == "UDPSend":
                continue
            elements.append(sensor.to_string())
        return ":".join(elements)

    def to_bytes(self) -> bytes:
        return self.to_string().encode("utf-8")

    def __str__(self) -> str:
        return self.to_string()
