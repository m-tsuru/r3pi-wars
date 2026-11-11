import socket
import time

from internal import r3pi
from internal.commands import ReceiveCommandPacket
from internal.handlers import handle_command, stop_robot, update_robot
from internal.linetrace import CONTROL_INTERVAL, LineTraceError
from internal.sensors import SensorReadError

HOST = "0.0.0.0"
PORT = 50000

WATCHDOG_TIMEOUT = 0.25


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    robot_initialized = False
    try:
        sock.bind((HOST, PORT))
        sock.settimeout(CONTROL_INTERVAL)

        robot_initialized = True
        r3pi.init()
        # init() が送信バッファーをクリアした後で PID 停止を確実に送る。
        stop_robot()
        last_received = time.monotonic()
        watchdog_stopped = False
        last_address = None

        while True:
            try:
                data, addr = sock.recvfrom(1024)
            except socket.timeout:
                data = None

            try:
                if data is not None:
                    message = data.decode("utf-8")
                    c = ReceiveCommandPacket(message)
                    last_address = addr
                    print(c)
                    for command in c.commands:
                        response = handle_command(command)
                        if response is not None:
                            sock.sendto(response.to_bytes(), addr)
                    last_received = time.monotonic()
                    watchdog_stopped = False
            except SensorReadError as e:
                print("Sensor Error:", e)
                sock.sendto(f"Error={e}".encode("utf-8"), addr)
            except LineTraceError as e:
                print("Line Trace Error:", e)
                last_received = time.monotonic()
                watchdog_stopped = False
                detail = str(e).replace(":", " ").replace("\n", " ")
                sock.sendto(f"Status=Stopped:Error={detail}".encode("utf-8"), addr)
            except (ValueError, UnicodeDecodeError) as e:
                print("Invalid Command:", e)

            if (
                not watchdog_stopped
                and time.monotonic() - last_received >= WATCHDOG_TIMEOUT
            ):
                stop_robot()
                watchdog_stopped = True

            try:
                update_robot()
            except LineTraceError as e:
                print("Line Trace Error:", e)
                if last_address is not None:
                    detail = str(e).replace(":", " ").replace("\n", " ")
                    sock.sendto(f"Status=Stopped:Error={detail}".encode("utf-8"), last_address)

    finally:
        try:
            if robot_initialized:
                stop_robot()
        finally:
            sock.close()


if __name__ == "__main__":
    main()
