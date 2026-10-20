# fossibot/config.py
# Adjust these to match your environment

# The Fossibot device's IP on its own AP network
FOSSIBOT_IP = "192.168.8.2"

# TCP port the device connects to (hardcoded in the app)
TCP_LISTEN_PORT = 8058

# Which interface/IP to bind the TCP server on
# Use the Pi's WiFi IP on the Fossibot AP network (e.g. 192.168.8.100)
# Leave as empty string to bind all interfaces
TCP_BIND_HOST = ""

# HTTP server — binds on all interfaces so LAN clients can reach it
HTTP_PORT = 8080

# How many seconds of silence before we consider the device disconnected
DEVICE_TIMEOUT_SECONDS = 10

# Number of events (connects, faults, AC loss, ...) kept in memory for /events
EVENT_HISTORY = 500

# If set, every raw byte received from the device is appended (hex, timestamped)
# to this file. Use a tmpfs path such as /run/fossibot/raw.log so it doesn't
# wear the SD card. Empty string disables capture.
RAW_CAPTURE_PATH = ""

# ACK frame sent back to device after every telemetry push
# Reverse-engineered from app source
ACK_FRAME = bytes.fromhex("0a001f00040004001f00")
