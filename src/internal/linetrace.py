"""ex2_a2.py のPD制御と周回検出を、UDP受信と併用できる形に移植する。"""
import time

MTR_SPEED_MAX = 0.3
MTR_SPEED_MIN = 0.0
P_TERM = 1.0
D_TERM = 20.0
BLACK_CHECK = 2000
CONTROL_INTERVAL = 0.01
SENSOR_TIMEOUT = 0.05
CALIBRATION_TIMEOUT = 5.0


class LineTraceError(RuntimeError):
    pass


def calculate_speeds(previous_position: float, current_position: float) -> tuple[float, float]:
    proportional = current_position
    derivative = current_position - previous_position
    power = proportional * P_TERM + derivative * D_TERM
    left = max(MTR_SPEED_MIN, min(MTR_SPEED_MAX + power, MTR_SPEED_MAX))
    right = max(MTR_SPEED_MIN, min(MTR_SPEED_MAX - power, MTR_SPEED_MAX))
    return left, right


class LineTracer:
    """start/stopで切り替え、updateで1回分だけ制御する。UARTは別スレッドで操作しない。"""

    def __init__(self, robot=None):
        self._robot = robot
        self.running = False
        self.lap_count = 0
        self.last_lap_time = None
        self._previous_position = 0.0
        self._was_on_circle = False
        self._last_circle_time = None
        self._next_update = 0.0
        self._failure = None

    def _get_robot(self):
        if self._robot is None:
            from . import r3pi

            self._robot = r3pi
        return self._robot

    def start(self) -> None:
        if self.running:
            # アプリからの継続通知では再校正・周回数のリセットをしない。
            return
        if self._failure is not None:
            # 失敗後の継続通知で校正を繰り返さず、終了指示で解除する。
            raise LineTraceError(self._failure)
        robot = self._get_robot()
        try:
            robot.PID_stop()
            robot.stop()
            robot.sensor_auto_calibrate(timeout=CALIBRATION_TIMEOUT)
            self._previous_position = robot.line_position(timeout=SENSOR_TIMEOUT)
            self.lap_count = 0
            self.last_lap_time = None
            self._was_on_circle = False
            self._last_circle_time = None
            self._next_update = time.monotonic()
            self.running = True
        except (OSError, ValueError) as error:
            self.stop()
            self._failure = f"ライントレースを開始できませんでした ({error})"
            raise LineTraceError(self._failure) from error

    def stop(self) -> None:
        self.running = False
        self._failure = None
        if self._robot is not None:
            self._robot.stop()

    def update(self) -> None:
        if not self.running or time.monotonic() < self._next_update:
            return
        robot = self._get_robot()
        try:
            values = robot.get_line_position_values(5, timeout=SENSOR_TIMEOUT)
            is_on_circle = BLACK_CHECK in values
            if is_on_circle and not self._was_on_circle:
                circle_time = time.monotonic()
                self.lap_count += 1
                if self._last_circle_time is None:
                    print(f"ラップ数: {self.lap_count}, ラップタイム: --")
                else:
                    self.last_lap_time = circle_time - self._last_circle_time
                    print(f"ラップ数: {self.lap_count}, ラップタイム: {self.last_lap_time:.3f} 秒")
                self._last_circle_time = circle_time
            self._was_on_circle = is_on_circle

            position = robot.line_position(timeout=SENSOR_TIMEOUT)
            left, right = calculate_speeds(self._previous_position, position)
            robot.left_motor(left)
            robot.right_motor(right)
            self._previous_position = position
            self._next_update = time.monotonic() + CONTROL_INTERVAL
        except (OSError, ValueError) as error:
            self.stop()
            self._failure = f"ライントレースを停止しました ({error})"
            raise LineTraceError(self._failure) from error
