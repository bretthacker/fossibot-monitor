# fossibot/tcp_server.py
# Listens for the Fossibot device to connect over TCP and push telemetry frames.
#
# Architecture note:
#   The phone app runs a TCP *server* and the device connects *to it*.
#   We replicate that here — we listen on TCP_LISTEN_PORT and the device
#   connects to us once it's on the same WiFi network.
#
#   The device sends a telemetry frame roughly every second.
#   We ACK each telemetry frame, then parse it into state.py.
#
#   If the device reconnects (e.g. after a WiFi drop) the newest connection
#   wins; the stale connection's thread is not allowed to mark the device
#   disconnected (see DeviceState connection tokens).
#
# Wire format (all little-endian uint16):
#   [total_len][frame_type][payload_len][payload ...]
#   total_len == payload_len + 6. Telemetry is type 0x000b, 224 bytes.
#   Any other type is logged with a hex dump and recorded as an event so we
#   can learn what the device sends (alarms, state changes) instead of
#   silently discarding it.

import socket
import threading
import time
import logging

import config
from parser import parse_frame, frame_to_hex_dump
from state import device_state

log = logging.getLogger(__name__)

# How long to wait for data before checking the stop event
RECV_TIMEOUT = 2.0

# Receive buffer — frames are typically 200-400 bytes but allow headroom
RECV_BUFFER = 4096

HEADER_LEN = 6
MAX_FRAME_LEN = 2048
TELEMETRY_TYPE = 0x000B


def _capture(direction: str, data: bytes):
    """Append raw bytes to RAW_CAPTURE_PATH (if set) for protocol discovery."""
    if not config.RAW_CAPTURE_PATH:
        return
    try:
        with open(config.RAW_CAPTURE_PATH, "a") as f:
            f.write(f"{time.time():.3f} {direction} {data.hex()}\n")
    except OSError as e:
        log.debug(f"Raw capture write failed: {e}")


def _handle_client(conn: socket.socket, addr):
    """Runs in its own thread for each connected device."""
    log.info(f"Device connected: {addr}")
    token = device_state.set_connected(str(addr))

    conn.settimeout(RECV_TIMEOUT)
    buf = bytearray()
    last_rx = time.time()

    try:
        while True:
            try:
                chunk = conn.recv(RECV_BUFFER)
            except socket.timeout:
                # Check if this connection has gone silent too long
                silent = time.time() - last_rx
                if silent > config.DEVICE_TIMEOUT_SECONDS:
                    log.warning(f"Device silent for {silent:.0f}s — closing connection")
                    break
                if not device_state.is_current(token):
                    log.info(f"Superseded by a newer connection — closing {addr}")
                    break
                continue
            except OSError as e:
                log.warning(f"Socket error: {e}")
                break

            if not chunk:
                log.info("Device closed connection")
                break

            last_rx = time.time()
            _capture("rx", chunk)
            buf.extend(chunk)

            for ftype, frame in _extract_frames(buf):
                if ftype != TELEMETRY_TYPE:
                    log.warning(
                        f"Non-telemetry frame type 0x{ftype:04x} ({len(frame)} bytes):\n"
                        + frame_to_hex_dump(frame)
                    )
                    device_state.record_event("frame", f"type 0x{ftype:04x}", frame.hex())
                    continue

                parsed = parse_frame(frame)
                if parsed:
                    device_state.update(parsed, token)
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
        device_state.set_disconnected(token)
        log.info(f"Connection from {addr} closed")


def _extract_frames(buf: bytearray) -> list:
    """
    Extract complete length-prefixed frames from the receive buffer (in place).
    Returns list of (frame_type, frame_bytes); modifies buf to remove them.

    A header is valid when total_len == payload_len + 6 and total_len is sane.
    On an invalid header we drop one byte and rescan, so the stream resyncs
    after garbage or a partial frame.
    """
    frames = []

    while len(buf) >= HEADER_LEN:
        total_len = buf[0] | (buf[1] << 8)
        ftype = buf[2] | (buf[3] << 8)
        payload_len = buf[4] | (buf[5] << 8)

        if total_len != payload_len + HEADER_LEN or not HEADER_LEN < total_len <= MAX_FRAME_LEN:
            log.debug(f"Bad header {bytes(buf[:HEADER_LEN]).hex()}, resyncing")
            del buf[0]
            continue

        if len(buf) < total_len:
            # Not enough data yet — wait for more
            break

        frames.append((ftype, bytes(buf[:total_len])))
        del buf[:total_len]

    if len(buf) > MAX_FRAME_LEN * 2:
        log.debug(f"Discarding {len(buf)} unframed bytes")
        buf.clear()

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

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

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

                    # Enable keepalive so a half-open connection is noticed by the kernel too
                    conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

                    t = threading.Thread(
                        target=_handle_client,
                        args=(conn, addr),
                        daemon=True,
                        name=f"device-{addr}"
                    )
                    t.start()

            except Exception as e:
                log.error(f"TCP server error: {e} — retrying in 5s")
                time.sleep(5)
            finally:
                if self._server_sock:
                    try:
                        self._server_sock.close()
                    except OSError:
                        pass
