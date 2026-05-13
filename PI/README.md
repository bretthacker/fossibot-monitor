# Fossibot Telemetry Bridge

Exposes your Fossibot power station's telemetry as a local HTTP JSON API,
so any device on your LAN can monitor it without the cloud or the app.

## How it works

The Fossibot app runs a TCP *server* on port 8058 and the device connects
*to the phone* when they share a WiFi network. This service replicates that
exactly — your Pi listens on port 8058, the device connects automatically,
and pushes a full telemetry frame every second.

The service exposes a simple HTTP API on port 8080 (LAN-facing) that your
server, NAS, Home Assistant, or anything else can poll. A real-time web
dashboard is also included, served from the same port.

## Repository contents

| File | Description |
|---|---|
| `main.py` | Entry point — wires TCP and HTTP servers together |
| `config.py` | Configuration: ports, timeouts, ACK frame |
| `tcp_server.py` | TCP listener that receives device telemetry pushes |
| `parser.py` | Binary frame parser — maps register bytes to field values |
| `registers.py` | Full register map reverse-engineered from the Fossibot app |
| `state.py` | Thread-safe telemetry state store |
| `http_server.py` | HTTP server exposing the API and dashboard |
| `index.html` | Real-time web dashboard |
| `fossibot.service` | systemd unit file for the Pi |
| `fossibot-ups-monitor.ps1` | Hyper-V UPS monitor and graceful shutdown script |

## Prerequisites

### Raspberry Pi (telemetry bridge)
- Raspberry Pi with WiFi + Ethernet (dual-homed)
- Pi's **WiFi** connected to the Fossibot AP (SSID starts with `FOSS_`, password `FOSSIBOT`)
- Pi's **Ethernet** connected to your LAN
- Python 3.8+ (comes with Raspberry Pi OS)
- No pip packages required — stdlib only

### Hyper-V host (UPS monitor, optional)
- Windows Server with Hyper-V
- PowerShell 5.1+
- Network access to the Pi's LAN IP or hostname
- Local SMTP relay for email notifications

## Pi installation

1. Copy all files to `/opt/fossibot/` on the Pi

2. Connect the Pi's WiFi to the Fossibot AP:
   ```bash
   sudo nmcli dev wifi connect "FOSS_XXXXXXXX" password "FOSSIBOT"
   ```

3. Verify the Pi gets an IP on the 192.168.8.x network:
   ```bash
   ip addr show wlan0
   ```

4. Test manually first:
   ```bash
   cd /opt/fossibot
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

6. Check it is running:
   ```bash
   sudo journalctl -u fossibot -f
   ```

## Dashboard

A dark-themed real-time dashboard is served at:

```
http://<pi-ip>:8080/
```

It polls `/flat` every 2 seconds and displays:

- Battery SOC with colour-coded progress bar (green → amber → red)
- Estimated runtime remaining and charge state
- AC input, AC output, solar input, and DC+USB output power cards
- Total input/output watts, battery temperature, AC voltage and frequency
- Output switch states (AC, DC, USB, LED) as live indicators
- Connection status and last update timestamp

No external dependencies — the dashboard is a single self-contained HTML file
served directly by the bridge. Works in any browser on your LAN.

## HTTP API

All endpoints return JSON. Replace `<pi-ip>` with your Pi's LAN IP or hostname.

### GET /
Dashboard (HTML). Also served at `/dashboard` and `/index.html`.

### GET /api
Full telemetry — each field includes `value`, `unit`, and `register` number.
```
curl http://<pi-ip>:8080/api
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
  "ac_switch": { "value": 1, "unit": "", "register": 39 }
}
```

### GET /flat
Flat key→value dict — easier for scripts and monitoring tools.
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
  "ac_switch": 1
}
```

### GET /status
Connection status only — whether the device is currently pushing telemetry.

### GET /health
Returns `200 OK` if device is connected, `503` if not.
Suitable for use as a health check endpoint in monitoring systems.

## Key fields for UPS monitoring

| Field | Unit | Description |
|---|---|---|
| `soc` | % | Battery state of charge 0-100 |
| `ac_input_power` | W | AC input (grid charging) power |
| `ac_output_power` | W | AC output power to connected load |
| `total_input_power` | W | Total input (AC + solar) |
| `total_output_power` | W | Total output (AC + DC + USB) |
| `pv_charge_power` | W | Solar PV input power |
| `runtime_minutes` | min | Estimated runtime remaining |
| `battery_temp_max` | °C | Battery temperature |
| `output_voltage` | 0.1V | AC output voltage (divide by 10) |
| `output_frequency` | 0.1Hz | AC output frequency (divide by 10) |
| `charge_discharge_state` | | 0=idle, 1=charging |
| `ac_switch` | | AC output enabled: 1=on, 0=off |

## Hyper-V UPS monitor

`fossibot-ups-monitor.ps1` is a PowerShell script for Windows Server / Hyper-V
hosts that polls the bridge API and initiates a graceful shutdown when the
battery reaches a critical level.

### Features

- Polls `/flat` every 30 seconds (configurable)
- Detects AC input loss and starts a configurable grace period timer
- Sends email notification on power outage and power restoration via local SMTP
- Retries failed email sends up to 3 times with 15-second gaps
- Saves all running Hyper-V VMs before shutdown, preserving their state
- Shuts down the host gracefully when SOC reaches the configured threshold
- Logs all events to the Windows Application event log and a local log file
- Runs as a persistent loop under a scheduled task with auto-restart on failure

### Configuration

Edit the variables at the top of the script before deploying:

```powershell
$PiAddress                   = "fossibot.itmj.net"  # Pi hostname or LAN IP
$ShutdownSocThreshold        = 20    # Shut down when SOC <= this %
$InputLostSocThreshold       = 80    # Shut down on AC loss if SOC <= this %
$WarnSocThreshold            = 30    # Log warning at this %
$InputLostGracePeriodSeconds = 30    # Seconds before acting on AC loss
$SmtpServer                  = "your-smtp-relay"
$EmailTo                     = "you@example.com"
$EmailFrom                   = "fossibot-ups@example.com"
$LogFile                     = "C:\Utils\fossibot-ups.log"
```

### Installation

1. Save to `C:\Utils\fossibot-ups-monitor.ps1`

2. Set `$WhatIf = $true` and run manually to verify behaviour:
   ```
   powershell -file C:\Utils\fossibot-ups-monitor.ps1
   ```

3. Once satisfied, set `$WhatIf = $false` and register as a scheduled task
   (run PowerShell as Administrator):
   ```powershell
   $action   = New-ScheduledTaskAction -Execute "powershell.exe" `
               -Argument "-NonInteractive -WindowStyle Hidden -File C:\Utils\fossibot-ups-monitor.ps1"
   $trigger  = New-ScheduledTaskTrigger -AtStartup
   $settings = New-ScheduledTaskSettingsSet `
               -RestartCount 99 `
               -RestartInterval (New-TimeSpan -Minutes 1) `
               -ExecutionTimeLimit ([TimeSpan]::Zero)
   Register-ScheduledTask -TaskName "FossibotUPSMonitor" `
       -Action $action -Trigger $trigger -Settings $settings `
       -RunLevel Highest -User "SYSTEM" -Force
   ```

4. Start immediately without rebooting:
   ```powershell
   Start-ScheduledTask -TaskName "FossibotUPSMonitor"
   ```

### Shutdown logic

Two independent conditions trigger shutdown:

- **SOC threshold**: battery drops to or below `$ShutdownSocThreshold` (default 20%)
- **AC loss + SOC**: AC input absent for more than `$InputLostGracePeriodSeconds`
  AND SOC is at or below `$InputLostSocThreshold` (default 80%) — handles the
  case where power just failed but the battery has not drained yet

In both cases: a shutdown email is sent, all running VMs are saved, then the
host powers off.

## Troubleshooting

**Device does not connect:**
- Confirm Pi WiFi is associated: `ip addr show wlan0` should show `state UP`
- Confirm nothing else is connected to the Fossibot AP — it only allows one client at a time
- Check port 8058 is not blocked: `sudo ufw allow 8058/tcp`

**Telemetry connects but shows no data:**
- Run with `--debug` to see raw frame bytes
- Frame format may vary slightly across firmware versions — BMS/PCS version
  numbers from the device Settings screen help diagnose mismatches

**HTTP not reachable from LAN:**
- Confirm the Pi's Ethernet IP: `ip addr show eth0`
- Allow port 8080: `sudo ufw allow 8080/tcp`

**PowerShell script not sending restore email:**
- Check `C:\Utils\fossibot-ups.log` for retry attempt details
- The script retries 3 times with 15-second gaps — the SMTP relay may need a
  moment to recover after a power event before it can accept connections

## Protocol notes

The Fossibot device connects outbound to a TCP server on port 8058 — replicating
the role the phone app plays. Each telemetry frame is exactly 224 bytes:

- 6-byte header (`e0 00 0b 00 da 00`)
- 36 x 6-byte register entries (2-byte address little-endian + 4-byte value)
- 2-byte CRC

The bridge ACKs each frame with a fixed 10-byte response. All 58 registers
in the device's data model are parsed, covering battery state, power flows,
temperatures, switch states, and device settings.

This protocol was reverse-engineered by decompiling the Fossibot Android APK
(`uni.app.UNI620F75F`) and analysing the JavaScript bundle inside. There is
no official API or documentation.

## License

MIT