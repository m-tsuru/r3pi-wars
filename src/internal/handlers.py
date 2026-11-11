from .boundary_detection import BoundaryDetector
from .commands import Command, SendSensorPacket
from .sensors import read_sensor

MAX_SPEED = 1.0

_boundary_detector = BoundaryDetector()


def stop_robot() -> None:
    _boundary_detector.stop()


def update_robot() -> None:
    _boundary_detector.update()


def handle_command(command: Command) -> SendSensorPacket | None:
    if command.key == "Sensor":
        return read_sensor(str(command.value))

    match command.key:
        case "Stop":
            stop_robot()
            response = SendSensorPacket()
            response.push("Status", "Stopped")
            return response
        case "BoundaryDetection":
            _boundary_detector.set_enabled(enabled=bool(command.value))
        case "Forward" | "Backward" | "Left" | "Right" | "LeftMotor" | "RightMotor":
            speed = float(command.value) * MAX_SPEED
            match command.key:
                case "Forward":
                    _boundary_detector.drive(speed, speed)
                case "Backward":
                    _boundary_detector.drive(-speed, -speed)
                case "Left":
                    _boundary_detector.drive(-speed, speed)
                case "Right":
                    _boundary_detector.drive(speed, -speed)
                case "LeftMotor":
                    _boundary_detector.drive(left=speed)
                case "RightMotor":
                    _boundary_detector.drive(right=speed)
        case _:
            raise ValueError("存在しないコマンドです")
    return None
