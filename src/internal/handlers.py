from .commands import Command, SendSensorPacket
from .linetrace import LineTracer
from .sensors import read_sensor

MAX_SPEED = 1.0

_line_tracer = LineTracer()


def stop_robot() -> None:
    from . import r3pi

    _line_tracer.stop()
    r3pi.PID_stop()
    r3pi.stop()


def update_robot() -> None:
    _line_tracer.update()


def _trace_status() -> SendSensorPacket:
    response = SendSensorPacket()
    response.push("Status", "Tracing" if _line_tracer.running else "Stopped")
    return response


def handle_command(command: Command) -> SendSensorPacket | None:
    if command.key == "Sensor":
        return read_sensor(str(command.value))

    # UART の初期化は、実際にコマンドを処理するときまで遅延する。
    from . import r3pi

    match command.key:
        case "Stop":
            stop_robot()
            return _trace_status()
        case "Linetrace":
            if command.value == 1:
                _line_tracer.start()
            else:
                stop_robot()
            return _trace_status()
        case "Forward" | "Backward" | "Left" | "Right" | "LeftMotor" | "RightMotor":
            # 手動操作へ切り替えた後はPD制御がモーター値を上書きしない。
            if _line_tracer.running:
                _line_tracer.stop()
            speed = float(command.value) * MAX_SPEED
            match command.key:
                case "Forward":
                    r3pi.forward(speed)
                case "Backward":
                    r3pi.backward(speed)
                case "Left":
                    r3pi.left(speed)
                case "Right":
                    r3pi.right(speed)
                case "LeftMotor":
                    r3pi.left_motor(speed)
                case "RightMotor":
                    r3pi.right_motor(speed)
        case _:
            raise ValueError("存在しないコマンドです")
