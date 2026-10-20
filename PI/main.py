#!/usr/bin/env python3
# fossibot/main.py
# Entry point. Starts the TCP server and HTTP server, then waits.
#
# Usage:
#   python3 main.py
#   python3 main.py --debug       # verbose frame logging
#
# The Pi's WiFi interface must already be connected to the Fossibot AP
# before starting. The device will connect to us automatically once
# it sees a TCP listener on port 8058.

import argparse
import logging
import os
import signal
import sys
import time

import config
from tcp_server import TcpServer
from http_server import HttpServer
from state import device_state


def setup_logging(debug: bool):
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Quiet down the http.server logger even in debug mode
    logging.getLogger("http.server").setLevel(logging.WARNING)


def main():
    parser = argparse.ArgumentParser(description="Fossibot telemetry bridge")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    setup_logging(args.debug)
    log = logging.getLogger("main")

    log.info("=" * 60)
    log.info("Fossibot Telemetry Bridge")
    log.info(f"  TCP listen port : {config.TCP_LISTEN_PORT}")
    log.info(f"  HTTP port       : {config.HTTP_PORT}")
    log.info(f"  Device timeout  : {config.DEVICE_TIMEOUT_SECONDS}s")
    log.info("=" * 60)

    tcp = TcpServer()
    http = HttpServer()

    tcp.start()
    http.start()

    # Graceful shutdown on Ctrl-C or SIGTERM
    # os._exit bypasses the blocking http.server.shutdown() call
    def _shutdown(signum, frame):
        log.info("Shutting down...")
        tcp.stop()
        os._exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info("Running. Press Ctrl-C to stop.")
    log.info("Waiting for device to connect...")

    # Status heartbeat every 30 seconds
    last_status = 0
    while True:
        time.sleep(1)
        now = time.time()

        # If the TCP listener thread has died we are useless — exit so
        # systemd restarts us rather than serving stale data forever
        if not tcp.is_alive():
            log.error("TCP server thread died — exiting for restart")
            os._exit(1)

        if now - last_status >= 30:
            last_status = now
            age = device_state.seconds_since_update()
            connected = device_state.is_connected()
            soc = device_state.get("soc")
            input_w = device_state.get("total_input_power")
            output_w = device_state.get("total_output_power")

            if connected and soc is not None:
                log.info(
                    f"[heartbeat] connected  SOC={soc}%  "
                    f"in={input_w}W  out={output_w}W  "
                    f"age={age:.0f}s"
                )
            elif connected:
                log.info(f"[heartbeat] connected, no data yet  age={age}s")
            else:
                log.info("[heartbeat] waiting for device connection")


if __name__ == "__main__":
    main()