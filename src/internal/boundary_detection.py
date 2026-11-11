"""黒を検出したら通知音を鳴らし、走行を1秒間停止する。"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

BLACK_CHECK = 2000
CONTROL_INTERVAL = 0.01
SENSOR_TIMEOUT = 0.05
STOP_DURATION = 1.0
BOUNDARY_SOUND = "L16 >crcrcr"


class BoundaryDetectionError(RuntimeError):
    pass


class BoundaryDetector:
    def __init__(self, robot: ModuleType | None = None) -> None:
        self._robot = robot
        self.enabled = False
        self._was_on_black = False
        self._stopped_until = 0.0
        self._next_update = 0.0
        self._left_speed = 0.0
        self._right_speed = 0.0

    def _get_robot(self) -> ModuleType:
        if self._robot is None:
            from . import r3pi  # noqa: PLC0415 -- UARTは利用時に初期化する。

            self._robot = r3pi
        return self._robot

    def set_enabled(self, *, enabled: bool) -> None:
        if self.enabled == enabled:
            return
        self.enabled = enabled
        self._was_on_black = False
        self._next_update = 0.0

    def stop(self) -> None:
        # Stopや通信断では、停止期間が終わっても走行を再開しない。
        self._left_speed = 0.0
        self._right_speed = 0.0
        robot = self._get_robot()
        robot.PID_stop()
        robot.stop()

    def drive(self, left: float | None = None, right: float | None = None) -> None:
        # 停止中も最新の操作を保持し、1秒経過後に反映する。
        if left is not None:
            self._left_speed = left
        if right is not None:
            self._right_speed = right
        if self._stopped_until:
            return
        robot = self._get_robot()
        if left is not None:
            robot.left_motor(left)
        if right is not None:
            robot.right_motor(right)

    def update(self) -> None:
        now = time.monotonic()
        if self._stopped_until and now < self._stopped_until:
            return
        if not self._stopped_until and (not self.enabled or now < self._next_update):
            return
        robot = self._get_robot()
        try:
            if self._stopped_until:
                self._stopped_until = 0.0
                robot.left_motor(self._left_speed)
                robot.right_motor(self._right_speed)
            if not self.enabled:
                return
            values = robot.get_line_position_values(5, timeout=SENSOR_TIMEOUT)
            is_on_black = any(value >= BLACK_CHECK for value in values)
            if is_on_black and not self._was_on_black:
                robot.PID_stop()
                robot.stop()
                self._stopped_until = time.monotonic() + STOP_DURATION
                robot.play(BOUNDARY_SOUND)
            self._was_on_black = is_on_black
            self._next_update = time.monotonic() + CONTROL_INTERVAL
        except (OSError, ValueError) as error:
            self.enabled = False
            self.stop()
            message = f"境界検出を停止しました ({error})"
            raise BoundaryDetectionError(message) from error
