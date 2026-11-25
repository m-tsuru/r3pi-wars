from .boundary_detection import BoundaryDetector
from .commands import Command, SendSensorPacket
from .send_sensor import SensorSender, resolve_sensor_config
from .sensors import read_sensor

MAX_SPEED = 1.0

_boundary_detector = BoundaryDetector()
_graph_sender: SensorSender | None = None


def stop_robot() -> None:
    _boundary_detector.stop()


def update_robot() -> None:
    _boundary_detector.update()


def set_graph_sending(*, enabled: bool) -> None:
    global _graph_sender
    if enabled:
        if _graph_sender is not None and _graph_sender.is_running:
            return
        url, interval, timeout = resolve_sensor_config(require_target=True)
        sender = SensorSender(url=url, timeout=timeout)
        sender.start_background(interval=interval)
        _graph_sender = sender
        print(f"GraphSend started: {url} (interval={interval}s)")
        return

    stop_graph_send()


def stop_graph_send() -> None:
    global _graph_sender
    if _graph_sender is not None:
        _graph_sender.stop_background()
        _graph_sender = None
        print("GraphSend stopped")


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
        case "GraphSend":
            enabled = bool(command.value)
            set_graph_sending(enabled=enabled)
            response = SendSensorPacket()
            response.push(
                "Status",
                "GraphSendStarted" if enabled else "GraphSendStopped",
            )
            return response
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
