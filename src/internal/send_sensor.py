"""Sense HAT のセンサー値を読み取り、CGI エンドポイントへ送信する。"""

from __future__ import annotations

import argparse
import contextlib
import math
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

DEFAULT_CGI_SCHEME = "http"
DEFAULT_CGI_PATH = "/cgi-bin/sensors.py"
DEFAULT_CGI_URL = f"{DEFAULT_CGI_SCHEME}://localhost{DEFAULT_CGI_PATH}"
DEFAULT_INTERVAL = 2.0
DEFAULT_TIMEOUT = 5.0
MAX_PAYLOAD_BYTES = 1024
MAX_PORT_NUMBER = 65535
MIN_QUOTED_LENGTH = 2
ALLOWED_SCHEMES = frozenset({"http", "https"})
DEFAULT_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


class _ColourSensorLike(Protocol):
    @property
    def brightness(self) -> float: ...


class SenseHatLike(Protocol):
    has_colour_sensor: bool
    colour: _ColourSensorLike

    def get_temperature(self) -> float: ...

    def get_humidity(self) -> float: ...

    def get_pressure(self) -> float: ...

    def get_accelerometer_raw(self) -> Mapping[str, float]: ...


class SensorSendError(RuntimeError):
    """センサー値の取得または CGI への送信に失敗したときの例外。"""


def _validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.netloc:
        msg = f"送信先 URL は http または https で指定してください: {url}"
        raise ValueError(msg)
    return url


def _validate_positive(name: str, value: float) -> float:
    if not math.isfinite(value) or value <= 0.0:
        msg = f"{name} は正の有限数で指定してください: {value}"
        raise ValueError(msg)
    return value


def load_dotenv(env_path: Path | None = None) -> dict[str, str]:
    """プロジェクトルートの .env ファイルを読み込む (標準ライブラリのみ)。"""
    path = DEFAULT_ENV_PATH if env_path is None else env_path
    values: dict[str, str] = {}
    if not path.is_file():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        value = raw_value.strip()
        if not key:
            continue
        if (
            len(value) >= MIN_QUOTED_LENGTH
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def _get_config_value(key: str, dotenv_values: Mapping[str, str]) -> str:
    env_value = os.environ.get(key, "").strip()
    if env_value:
        return env_value
    return dotenv_values.get(key, "").strip()


def _parse_positive_float_setting(
    key: str,
    dotenv_values: Mapping[str, str],
    default: float,
) -> float:
    raw = _get_config_value(key, dotenv_values)
    if not raw:
        return default
    try:
        return _validate_positive(key, float(raw))
    except ValueError as error:
        msg = f"設定 {key} の値が不正です ({error})"
        raise SensorSendError(msg) from error


def _build_url_from_ip(
    host: str,
    *,
    scheme: str,
    port_raw: str,
    path: str,
) -> str:
    formatted_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    netloc = formatted_host
    if port_raw:
        try:
            port = int(port_raw)
        except ValueError as error:
            msg = f"GRAPH_SERVER_PORT は整数で指定してください: {port_raw}"
            raise SensorSendError(msg) from error
        if not 1 <= port <= MAX_PORT_NUMBER:
            msg = f"GRAPH_SERVER_PORT の範囲が不正です: {port}"
            raise SensorSendError(msg)
        netloc = f"{formatted_host}:{port}"

    normalized_path = path if path.startswith("/") else f"/{path}"
    url = f"{scheme}://{netloc}{normalized_path}"
    try:
        return _validate_url(url)
    except ValueError as error:
        msg = f"送信先 URL の設定が不正です ({error})"
        raise SensorSendError(msg) from error


def resolve_sensor_config(
    env_path: Path | None = None,
    *,
    require_target: bool = False,
) -> tuple[str, float, float]:
    """環境変数および .env から送信先 URL・送信間隔・タイムアウトを解決する。"""
    dotenv_values = load_dotenv(env_path)

    explicit_url = _get_config_value("SENSOR_CGI_URL", dotenv_values)
    server_ip = _get_config_value("GRAPH_SERVER_IP", dotenv_values)

    if explicit_url:
        try:
            url = _validate_url(explicit_url)
        except ValueError as error:
            msg = f"SENSOR_CGI_URL の設定が不正です ({error})"
            raise SensorSendError(msg) from error
    elif server_ip:
        scheme = (
            _get_config_value("GRAPH_SERVER_SCHEME", dotenv_values)
            or DEFAULT_CGI_SCHEME
        )
        port_raw = _get_config_value("GRAPH_SERVER_PORT", dotenv_values)
        path = _get_config_value("GRAPH_SERVER_PATH", dotenv_values) or DEFAULT_CGI_PATH
        url = _build_url_from_ip(
            server_ip,
            scheme=scheme,
            port_raw=port_raw,
            path=path,
        )
    elif require_target:
        msg = ".env に GRAPH_SERVER_IP または SENSOR_CGI_URL を設定してください"
        raise SensorSendError(msg)
    else:
        url = DEFAULT_CGI_URL

    interval = _parse_positive_float_setting(
        "GRAPH_SEND_INTERVAL",
        dotenv_values,
        DEFAULT_INTERVAL,
    )
    timeout = _parse_positive_float_setting(
        "GRAPH_SEND_TIMEOUT",
        dotenv_values,
        DEFAULT_TIMEOUT,
    )
    return url, interval, timeout


class SensorSender:
    """Sense HAT のセンサー値を CGI (cgi/sensors.py) へ POST 送信するクライアント。"""

    def __init__(
        self,
        url: str = DEFAULT_CGI_URL,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        sense: SenseHatLike | None = None,
    ) -> None:
        self.url = _validate_url(url)
        self.timeout = _validate_positive("timeout", timeout)
        self._sense = sense
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        """バックグラウンド送信スレッドが動作中かどうかを返す。"""
        return self._thread is not None and self._thread.is_alive()

    def _get_sense(self) -> SenseHatLike:
        if self._sense is None:
            from sense_hat import SenseHat  # noqa: PLC0415  # ty: ignore[import-not-found]

            self._sense = SenseHat()
        return self._sense

    def collect(self) -> dict[str, float | int]:
        """Sense HAT から各種センサー値と現在時刻(UNIX秒)を取得する。"""
        try:
            sense = self._get_sense()
            raw_values: dict[str, float | int] = {
                "Time": time.time(),
                "Temperature": float(sense.get_temperature()),
                "Humidity": float(sense.get_humidity()),
                "Pressure": float(sense.get_pressure()),
            }

            accel = sense.get_accelerometer_raw()
            raw_values["AccelX"] = float(accel["x"])
            raw_values["AccelY"] = float(accel["y"])
            raw_values["AccelZ"] = float(accel["z"])

            if getattr(sense, "has_colour_sensor", False):
                with contextlib.suppress(
                    AttributeError,
                    OSError,
                    RuntimeError,
                    TypeError,
                    ValueError,
                ):
                    raw_values["Brightness"] = int(sense.colour.brightness)
        except (
            ImportError,
            KeyError,
            OSError,
            AttributeError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as error:
            msg = f"センサー値の取得に失敗しました ({error})"
            raise SensorSendError(msg) from error

        payload: dict[str, float | int] = {}
        for key, value in raw_values.items():
            if not math.isfinite(float(value)):
                msg = f"センサー値 {key} が有限数ではありません: {value}"
                raise SensorSendError(msg)
            payload[key] = value
        return payload

    def send(self, payload: Mapping[str, float | int] | None = None) -> str:
        """センサー値を application/x-www-form-urlencoded 形式で CGI へ POST 送信する。"""
        data_map = self.collect() if payload is None else dict(payload)
        encoded = urlencode(data_map).encode("utf-8")
        if len(encoded) > MAX_PAYLOAD_BYTES:
            msg = f"送信データサイズが上限 ({MAX_PAYLOAD_BYTES} bytes) を超えています: {len(encoded)} bytes"
            raise SensorSendError(msg)

        request = Request(  # noqa: S310 -- _validate_url で http/https のみに制限済み。
            self.url,
            data=encoded,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 -- _validate_url で http/https のみに制限済み。
                return response.read().decode("utf-8", errors="replace")
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace").strip()
            msg = (
                f"CGI サーバーからエラー応答を受信しました (HTTP {error.code}: {body})"
            )
            raise SensorSendError(msg) from error
        except (URLError, TimeoutError, OSError) as error:
            msg = f"CGI サーバーへの送信に失敗しました ({error})"
            raise SensorSendError(msg) from error

    def run_forever(
        self,
        interval: float = DEFAULT_INTERVAL,
        *,
        max_count: int | None = None,
    ) -> None:
        """一定間隔でセンサー値を継続送信する。"""
        validated_interval = _validate_positive("interval", interval)
        if max_count is not None and max_count <= 0:
            msg = f"max_count は 1 以上で指定してください: {max_count}"
            raise ValueError(msg)

        self._stop_event.clear()
        sent_count = 0
        while not self._stop_event.is_set():
            started_at = time.monotonic()
            try:
                self.send()
            except SensorSendError as error:
                print(f"Sensor Send Error: {error}")

            sent_count += 1
            if max_count is not None and sent_count >= max_count:
                break

            wait_seconds = validated_interval - (time.monotonic() - started_at)
            if wait_seconds > 0.0 and self._stop_event.wait(wait_seconds):
                break

    def start_background(self, interval: float = DEFAULT_INTERVAL) -> None:
        """バックグラウンドスレッドで定期送信を開始する。"""
        if self.is_running:
            return
        validated_interval = _validate_positive("interval", interval)
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self.run_forever,
            args=(validated_interval,),
            daemon=True,
        )
        self._thread.start()

    def stop_background(self, timeout: float = 2.0) -> None:
        """バックグラウンド送信を停止する。"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None


def send_sensor_data(
    url: str = DEFAULT_CGI_URL,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    sense: SenseHatLike | None = None,
) -> str:
    """センサー値を1回取得して CGI へ送信するヘルパー関数。"""
    sender = SensorSender(url=url, timeout=timeout, sense=sense)
    return sender.send()


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    default_url, default_interval, default_timeout = resolve_sensor_config()
    parser = argparse.ArgumentParser(
        description="Sense HAT のセンサー値を CGI (sensors.py) へ送信します。",
    )
    parser.add_argument(
        "--url",
        default=default_url,
        help=f"送信先 CGI の URL (.env の GRAPH_SERVER_IP / SENSOR_CGI_URL でも指定可, 既定: {default_url})",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=default_interval,
        help=f"送信間隔 [秒] (既定: {default_interval})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=default_timeout,
        help=f"HTTP タイムアウト [秒] (既定: {default_timeout})",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="1回だけ送信して終了する",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="指定回数だけ送信して終了する",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    sender = SensorSender(url=args.url, timeout=args.timeout)

    if args.once:
        response = sender.send()
        print(response)
        return

    try:
        sender.run_forever(interval=args.interval, max_count=args.count)
    except KeyboardInterrupt:
        sender.stop_background()


if __name__ == "__main__":
    main()
