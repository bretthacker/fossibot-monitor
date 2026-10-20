# fossibot/state.py
# Thread-safe store for the latest telemetry from the device.
# The TCP thread writes here; the HTTP thread reads from here.
#
# Besides the latest values, this keeps a small in-memory event log of
# things worth alerting on: connects/disconnects, fault codes changing,
# AC input appearing/disappearing, outputs switching and SOC jumps
# (e.g. a BMS recalibration from 100% to 0%).

import collections
import itertools
import threading
import time
import logging

import config
from registers import REGISTERS, register_unit

log = logging.getLogger(__name__)

# Fields whose every change is recorded as an event
WATCHED_FIELDS = (
    "bms_fault_code", "pcs_fault_code", "pv_fault_code",
    "ac_switch", "dc_switch", "usb_switch",
    "eco_ups_mode", "charge_discharge_state",
)
FAULT_FIELDS = ("bms_fault_code", "pcs_fault_code", "pv_fault_code")

# AC input above this many watts counts as "AC present"
AC_PRESENT_WATTS = 10

# A SOC change larger than this between consecutive frames is an anomaly
SOC_JUMP_PERCENT = 10


class DeviceState:
    def __init__(self):
        self._lock = threading.RLock()
        self._values = {}          # field_name -> raw value
        self._last_update = None   # epoch float
        self._connected = False
        self._client_addr = None
        self._token = None         # identifies the current connection
        self._tokens = itertools.count(1)
        self._connect_count = 0
        self._last_connect = None
        self._last_disconnect = None
        self._events = collections.deque(maxlen=config.EVENT_HISTORY)
        self._event_seq = itertools.count(1)
        self._started = time.time()

    # ------------------------------------------------------------------
    # Write side (TCP thread)
    # ------------------------------------------------------------------

    def update(self, parsed: dict, token=None):
        """Merge a parsed register dict into current state."""
        with self._lock:
            if token is not None and token != self._token:
                return  # data from a superseded connection
            self._detect_changes(parsed)
            self._values.update(parsed)
            self._last_update = time.time()

    def set_connected(self, addr):
        """Mark a new connection current. Returns its token."""
        with self._lock:
            self._token = next(self._tokens)
            self._connected = True
            self._client_addr = addr
            self._connect_count += 1
            self._last_connect = time.time()
            self._add_event("connected", f"Device connected from {addr}")
            return self._token

    def set_disconnected(self, token=None):
        with self._lock:
            if token is not None and token != self._token:
                return  # an older connection closing; the newer one is still live
            self._connected = False
            self._token = None
            self._last_disconnect = time.time()
            self._add_event("disconnected", "Device disconnected")

    def is_current(self, token) -> bool:
        with self._lock:
            return token == self._token

    def record_event(self, kind, message, data=None):
        with self._lock:
            self._add_event(kind, message, data)

    def _add_event(self, kind, message, data=None):
        event = {
            "seq": next(self._event_seq),
            "epoch": round(time.time(), 1),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "kind": kind,
            "message": message,
        }
        if data is not None:
            event["data"] = data
        self._events.append(event)
        level = logging.WARNING if kind in ("fault", "soc_jump", "ac_lost", "frame") else logging.INFO
        log.log(level, f"[event] {kind}: {message}")

    def _detect_changes(self, parsed: dict):
        """Compare an incoming frame with current values and record events."""
        if not self._values:
            # First frame since start — report any active faults
            for field in FAULT_FIELDS:
                if parsed.get(field):
                    self._add_event("fault", f"{field} active at startup: 0x{parsed[field]:08x}")
            return

        for field in WATCHED_FIELDS:
            if field not in parsed or field not in self._values:
                continue
            old, new = self._values[field], parsed[field]
            if old == new:
                continue
            if field in FAULT_FIELDS:
                kind = "fault" if new else "fault_cleared"
                self._add_event(kind, f"{field} 0x{old:08x} -> 0x{new:08x}")
            else:
                self._add_event("change", f"{field} {old} -> {new}")

        if "soc" in parsed and "soc" in self._values:
            old, new = self._values["soc"], parsed["soc"]
            if abs(new - old) > SOC_JUMP_PERCENT:
                self._add_event("soc_jump", f"SOC jumped {old}% -> {new}%")

        if "ac_input_power" in parsed and "ac_input_power" in self._values:
            was = self._values["ac_input_power"] > AC_PRESENT_WATTS
            now = parsed["ac_input_power"] > AC_PRESENT_WATTS
            if was and not now:
                self._add_event("ac_lost", f"AC input lost (SOC {parsed.get('soc')}%)")
            elif now and not was:
                self._add_event("ac_restored", f"AC input restored (SOC {parsed.get('soc')}%)")

    # ------------------------------------------------------------------
    # Read side (HTTP thread)
    # ------------------------------------------------------------------

    def is_connected(self) -> bool:
        """Connected AND receiving data recently."""
        with self._lock:
            if not self._connected:
                return False
            age = self.seconds_since_update()
            return age is not None and age <= config.DEVICE_TIMEOUT_SECONDS

    def last_update(self):
        with self._lock:
            return self._last_update

    def seconds_since_update(self):
        with self._lock:
            if self._last_update is None:
                return None
            return time.time() - self._last_update

    def events(self, since_seq=0) -> list:
        with self._lock:
            return [e for e in self._events if e["seq"] > since_seq]

    def _meta(self) -> dict:
        age = self.seconds_since_update()
        return {
            "connected": self.is_connected(),
            "client_addr": self._client_addr,
            "last_update_epoch": self._last_update,
            "seconds_since_update": round(age, 1) if age is not None else None,
            "connect_count": self._connect_count,
            "last_connect_epoch": self._last_connect,
            "last_disconnect_epoch": self._last_disconnect,
            "uptime_seconds": round(time.time() - self._started),
            "last_event_seq": self._events[-1]["seq"] if self._events else 0,
        }

    def as_dict(self) -> dict:
        """
        Return a snapshot suitable for JSON serialisation.
        Includes metadata fields alongside telemetry.
        """
        with self._lock:
            snap = {"_meta": self._meta()}

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
        Values are the last ones received; check _connected/_seconds_since_update
        before trusting them.
        """
        with self._lock:
            flat = {"_" + k: v for k, v in self._meta().items()}
            flat.update(self._values)
            return flat

    def get(self, field, default=None):
        with self._lock:
            return self._values.get(field, default)


# Singleton — import this anywhere
device_state = DeviceState()
