# fossibot/state.py
# Thread-safe store for the latest telemetry from the device.
# The TCP thread writes here; the HTTP thread reads from here.

import threading
import time
import logging
from registers import REGISTERS, register_unit

log = logging.getLogger(__name__)


class DeviceState:
    def __init__(self):
        self._lock = threading.RLock()
        self._values = {}          # field_name -> raw value
        self._last_update = None   # epoch float
        self._connected = False
        self._client_addr = None

    # ------------------------------------------------------------------
    # Write side (TCP thread)
    # ------------------------------------------------------------------

    def update(self, parsed: dict):
        """Merge a parsed register dict into current state."""
        with self._lock:
            self._values.update(parsed)
            self._last_update = time.time()

    def set_connected(self, addr):
        with self._lock:
            self._connected = True
            self._client_addr = addr
            log.info(f"Device connected from {addr}")

    def set_disconnected(self):
        with self._lock:
            self._connected = False
            log.info("Device disconnected")

    # ------------------------------------------------------------------
    # Read side (HTTP thread)
    # ------------------------------------------------------------------

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def last_update(self):
        with self._lock:
            return self._last_update

    def seconds_since_update(self):
        with self._lock:
            if self._last_update is None:
                return None
            return time.time() - self._last_update

    def as_dict(self) -> dict:
        """
        Return a snapshot suitable for JSON serialisation.
        Includes metadata fields alongside telemetry.
        """
        with self._lock:
            age = self.seconds_since_update()
            snap = {
                "_meta": {
                    "connected": self._connected,
                    "client_addr": self._client_addr,
                    "last_update_epoch": self._last_update,
                    "seconds_since_update": round(age, 1) if age is not None else None,
                }
            }

            # Emit each known register with value and unit
            for key, (field, dtype, unit, desc) in REGISTERS.items():
                if field in self._values:
                    snap[field] = {
                        "value": self._values[field],
                        "unit": unit,
                        "register": key,
                    }

            return snap

    def as_flat_dict(self) -> dict:
        """
        Flat key->value dict — easier for simple monitoring scripts to consume.
        """
        with self._lock:
            age = self.seconds_since_update()
            flat = {
                "_connected": self._connected,
                "_seconds_since_update": round(age, 1) if age is not None else None,
            }
            flat.update(self._values)
            return flat

    def get(self, field, default=None):
        with self._lock:
            return self._values.get(field, default)


# Singleton — import this anywhere
device_state = DeviceState()
