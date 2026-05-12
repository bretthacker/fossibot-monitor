# Fossibot Telemetry Bridge

Exposes your Fossibot power station's telemetry as a local HTTP JSON API,
so any device on your LAN can monitor it without the cloud or the app.

## How it works

The Fossibot app runs a TCP *server* on port 8058 and the device connects
*to the phone* when they share a WiFi network. This service replicates that
exactly — your Pi listens on port 8058, the device connects automatically,
and pushes a full telemetry frame every second.

The service exposes a simple HTTP API on port 8080 (LAN-facing) that your
server, NAS, Home Assistant, or anything else can poll.

## Prerequisites

- Raspberry Pi (any model with WiFi + Ethernet)
- Pi's **WiFi** connected to the Fossibot AP (SSID starts with `FOSS_`, password `FOSSIBOT`)
- Pi's **Ethernet** connected to your LAN
- Python 3.8+ (comes with Raspberry Pi OS)
- No pip packages required — stdlib only

## Installation

1. Copy the `fossibot/` folder to `/home/pi/fossibot/` on the Pi

2. Connect the Pi's WiFi to the Fossibot AP:
   ```bash
   sudo nmcli dev wifi connect "FOSS_XXXXXXXX" password "FOSSIBOT"
   ```
   Or use raspi-config / NetworkManager GUI.

3. Verify the Pi gets an IP on the 192.168.8.x network:
   ```bash
   ip addr show wlan0
   ```

4. Test manually first:
   ```bash
   cd /home/pi/fossibot
   python3 main.py --debug
   ```
   You should see the device connect within a few seconds and telemetry
   start flowing. Hit Ctrl-C when satisfied.

5. Install as a systemd service:
   ```bash
   sudo cp fossibot.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable fossibot
   sudo systemctl start fossibot
   ```

6. Check it's running:
   ```bash
   sudo journalctl -u fossibot -f
   ```

## HTTP API

All endpoints return JSON. Replace `<pi-ip>` with your Pi's LAN IP.

### GET /
Full telemetry — each field has `value`, `unit`, and `register` number.
```
curl http://<pi-ip>:8080/
```
```json
{
  "_meta": {
    "connected": true,
    "seconds_since_update": 0.8
  },
  "soc": { "value": 100, "unit": "%", "register": 1 },
  "total_input_power": { "value": 266, "unit": "W", "register": 34 },
  "total_output_power": { "value": 266, "unit": "W", "register": 35 },
  "ac_switch": { "value": 1, "unit": "", "register": 39 },
  ...
}
```

### GET /flat
Flat key→value — easier for simple scripts.
```
curl http://<pi-ip>:8080/flat
```
```json
{
  "_connected": true,
  "_seconds_since_update": 0.8,
  "soc": 100,
  "total_input_power": 266,
  "total_output_power": 266,
  "ac_switch": 1,
  ...
}
```

### GET /status
Connection status only.

### GET /health
Returns 200 OK if device is connected, 503 if not.
Useful as a health check endpoint for monitoring.

## Key fields for UPS monitoring

| Field | Description |
|---|---|
| `soc` | Battery % (0-100) |
| `total_input_power` | Total input watts (AC charging + solar) |
| `total_output_power` | Total output watts (AC + DC + USB) |
| `ac_input_power` | AC input (charging) watts |
| `ac_output_power` | AC output (to your load) watts |
| `charge_discharge_state` | 0=idle, 1=charging |
| `runtime_minutes` | Estimated runtime remaining |
| `battery_temp_max` | Battery temperature °C |
| `ac_switch` | AC output enabled (1) or disabled (0) |

## Troubleshooting

**Device doesn't connect:**
- Confirm Pi WiFi is on the Fossibot AP: `ip addr show wlan0`
- Confirm nothing else is on the AP (it only allows one client)
- Make sure port 8058 isn't blocked: `sudo ufw allow 8058/tcp` if using ufw

**Frames parse to 0 registers:**
- Run with `--debug` and paste the hex dump — the frame format may differ
  slightly on your firmware version. The BMS/PCS versions in Device Settings
  will help diagnose.

**HTTP not reachable from LAN:**
- Confirm the Pi's Ethernet IP: `ip addr show eth0`
- Allow port 8080: `sudo ufw allow 8080/tcp`

## Synology NAS integration

On your NAS, a simple polling script:
```bash
#!/bin/bash
curl -sf http://<pi-ip>:8080/flat | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f\"SOC: {d['soc']}%  Load: {d['total_output_power']}W  Input: {d['total_input_power']}W\")
"
```

Or point any SNMP/Modbus monitoring tool at the HTTP endpoint and translate
the JSON fields into whatever metric format you need.

FOSS_F180V012512C1750
