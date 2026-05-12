# fossibot/tcp_server.py
# Listens for the Fossibot device to connect over TCP and push telemetry frames.
#
# Architecture note:
#   The phone app runs a TCP *server* and the device connects *to it*.
#   We replicate that here — we listen on TCP_LISTEN_PORT and the device
#   connects to us once it's on the same WiFi network.
#
#   The device sends a telemetry frame roughly every second.
#   We ACK each frame, then parse it into state.py.
#
#   Only one device connection is handled at a time (matching the app behaviour).

import socket
import threading
import time
import logging

import config
from parser import parse_frame
from state import device_state

log = logging.getLogger(__name__)

# How long to wait for data before checking the stop event
RECV_TIMEOUT = 2.0

# Receive buffer — frames are typically 200-400 bytes but allow headroom
RECV_BUFFER = 4096


def _handle_client(conn: socket.socket, addr):
    """Runs in its own thread for each connected device."""
    log.info(f"Device connected: {addr}")
    device_state.set_connected(str(addr))

    conn.settimeout(RECV_TIMEOUT)
    buf = bytearray()

    try:
        while True:
            try:
                chunk = conn.recv(RECV_BUFFER)
            except socket.timeout:
                # Check if device has gone silent too long
                age = device_state.seconds_since_update()
                if age is not None and age > config.DEVICE_TIMEOUT_SECONDS:
                    log.warning(f"Device silent for {age:.0f}s — closing connection")
                    break
                continue
            except OSError as e:
                log.warning(f"Socket error: {e}")
                break

            if not chunk:
                log.info("Device closed connection")
                break

            buf.extend(chunk)

            frames_parsed = _extract_frames(buf)
            for frame in frames_parsed:
                parsed = parse_frame(frame)
                if parsed:
                    device_state.update(parsed)
                    log.debug(f"Updated {len(parsed)} registers")
                else:
                    log.warning("Frame parsed 0 registers — possible format mismatch")

                # Send ACK back to device
                try:
                    conn.sendall(config.ACK_FRAME)
                except OSError as e:
                    log.warning(f"Failed to send ACK: {e}")

    finally:
        conn.close()
        device_state.set_disconnected()
        log.info(f"Connection from {addr} closed")


FRAME_MARKER = bytes([0xe0, 0x00])
FRAME_LENGTH = 224  # observed fixed frame size from device


def _extract_frames(buf: bytearray) -> list:
    """
    Extract complete frames from the receive buffer (in place).
    Returns list of complete frame bytes; modifies buf to remove them.

    Frame start marker: e0 00 (first two bytes).
    Each frame is exactly 224 bytes.
    """
    frames = []

    while True:
        start = buf.find(FRAME_MARKER)
        if start == -1:
            if len(buf) > RECV_BUFFER // 2:
                log.debug(f"No frame marker, discarding {len(buf)} bytes")
                buf.clear()
            break

        if start > 0:
            log.debug(f"Discarding {start} leading bytes")
            del buf[:start]

        if len(buf) < FRAME_LENGTH:
            # Not enough data yet — wait for more
            break

        # Extract exactly one frame
        frame = bytes(buf[:FRAME_LENGTH])
        del buf[:FRAME_LENGTH]
        frames.append(frame)

    return frames


class TcpServer:
    def __init__(self):
        self._stop_event = threading.Event()
        self._thread = None
        self._server_sock = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="tcp-server")
        self._thread.start()
        log.info(f"TCP server starting on port {config.TCP_LISTEN_PORT}")

    def stop(self):
        self._stop_event.set()
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self._server_sock.bind((config.TCP_BIND_HOST, config.TCP_LISTEN_PORT))
                self._server_sock.listen(1)
                self._server_sock.settimeout(2.0)
                log.info(f"Listening on {config.TCP_BIND_HOST or '*'}:{config.TCP_LISTEN_PORT}")

                while not self._stop_event.is_set():
                    try:
                        conn, addr = self._server_sock.accept()
                    except socket.timeout:
                        continue
                    except OSError:
                        break

                    # Handle client in its own thread (only one at a time expected)
                    t = threading.Thread(
                        target=_handle_client,
                        args=(conn, addr),
                        daemon=True,
                        name=f"device-{addr}"
                    )
                    t.start()

            except OSError as e:
                log.error(f"TCP server error: {e} — retrying in 5s")
                time.sleep(5)
            finally:
                if self._server_sock:
                    try:
                        self._server_sock.close()
                    except OSError:
                        pass