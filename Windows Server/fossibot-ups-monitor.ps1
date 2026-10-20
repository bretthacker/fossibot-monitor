# fossibot-ups-monitor.ps1
# Polls the Fossibot telemetry bridge and initiates graceful shutdown
# of the Hyper-V host when battery drops below threshold.
#
# This script runs as a CONTINUOUS LOOP (polls every $PollIntervalSeconds).
# It must be installed as a persistent scheduled task, not a one-shot job.
#
# INSTALLATION (run PowerShell as Administrator):
#
#   1. Save to C:\Utils\fossibot-ups-monitor.ps1
#
#   2. Set $WhatIf = $true and run manually to verify behaviour:
#      powershell -file C:\Utils\fossibot-ups-monitor.ps1
#
#   3. Once satisfied, set $WhatIf = $false, then register the scheduled task:
#
#      $action   = New-ScheduledTaskAction -Execute "powershell.exe" `
#                  -Argument "-NonInteractive -WindowStyle Hidden -File C:\Utils\fossibot-ups-monitor.ps1"
#      $trigger  = New-ScheduledTaskTrigger -AtStartup
#      $settings = New-ScheduledTaskSettingsSet `
#                  -RestartCount 99 `
#                  -RestartInterval (New-TimeSpan -Minutes 1) `
#                  -ExecutionTimeLimit ([TimeSpan]::Zero)
#      Register-ScheduledTask -TaskName "FossibotUPSMonitor" `
#          -Action $action -Trigger $trigger -Settings $settings `
#          -RunLevel Highest -User "SYSTEM" -Force
#
#   4. Start it immediately without rebooting:
#      Start-ScheduledTask -TaskName "FossibotUPSMonitor"
#
#   5. Verify it is running:
#      Get-ScheduledTask -TaskName "FossibotUPSMonitor" | Select-Object State
#
#   Notes:
#   - ExecutionTimeLimit of zero removes the default 72-hour kill timer
#   - RestartCount 99 with 1-minute interval auto-restarts on any crash
#   - Runs as SYSTEM which has rights to write the Application event log

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

$PiAddress                   = "[pi-LAN-ip-address]"
$Port                        = 8080
$ApiUrl                      = "http://${PiAddress}:${Port}/flat"

$ShutdownSocThreshold        = 20    # Shut down when SOC <= this %
$InputLostSocThreshold       = 80    # Shut down on AC loss if SOC <= this %
$WarnSocThreshold            = 30    # Log warning (no shutdown) at this %
$InputLostGracePeriodSeconds = 30    # Seconds to wait before acting on AC loss
$MaxOnBatteryMinutes         = 10    # Shut down after this long on battery REGARDLESS of SOC.
                                     # The BMS SOC can stick at 100% while the pack is empty
                                     # (seen 2026-10-19), so SOC alone can't be trusted.
$MaxConsecutiveFailures      = 3     # API failures before logging an alert
$MaxDisconnectedPolls        = 3     # Polls with the device disconnected before alerting
$PollIntervalSeconds         = 30    # Seconds between polls
$AlertRepeatMinutes          = 60    # Re-send an ongoing problem alert this often

$WhatIf                      = $false  # Set $true to simulate without shutting down

# ---------------------------------------------------------------------------
# Email configuration
# ---------------------------------------------------------------------------

$SmtpServer         = "[smtp-server-name]"
$SmtpPort           = 25
$EmailTo            = "[to-email-address]"
$EmailFrom          = "[from-email-address]"
$EmailSubjectPrefix = "[Fossibot UPS]"

# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

function Write-UpsEvent {
    param([int]$EventId, [string]$EntryType, [string]$Message)
    try {
        Write-EventLog -LogName Application -Source "Fossibot-UPS" `
            -EventId $EventId -EntryType $EntryType -Message $Message `
            -ErrorAction Stop
    } catch {
        # Silently ignore — requires admin rights; scheduled task as SYSTEM will succeed
    }
}

function Send-UpsEmail {
    param(
        [string]$Subject,
        [string]$Body
    )
    try {
        Send-MailMessage `
            -SmtpServer $SmtpServer `
            -Port       $SmtpPort `
            -To         $EmailTo `
            -From       $EmailFrom `
            -Subject    ("$EmailSubjectPrefix $Subject") `
            -Body       $Body `
            -ErrorAction Stop
        Write-Host "Email sent: $Subject"
    } catch {
        Write-Warning "Failed to send email: $_"
    }
}

# Tracks ongoing problems (bridge down, device disconnected) so each one
# emails when it starts, repeats every $AlertRepeatMinutes, and emails
# once more when it clears.
$script:Problems = @{}

function Update-Problem {
    param(
        [string]$Name,
        [bool]$Active,
        [int]$EventId,
        [string]$Detail
    )
    $now = Get-Date
    $p   = $script:Problems[$Name]

    if ($Active) {
        if ($null -eq $p) {
            $p = @{ Since = $now; LastAlert = $null }
            $script:Problems[$Name] = $p
        }
        $due = ($null -eq $p.LastAlert) -or (($now - $p.LastAlert).TotalMinutes -ge $AlertRepeatMinutes)
        if ($due) {
            $mins = [math]::Round(($now - $p.Since).TotalMinutes, 1)
            Write-UpsEvent -EventId $EventId -EntryType Warning -Message "Fossibot: $Name. $Detail"
            Send-UpsEmail -Subject "PROBLEM -- $Name" -Body ("$Detail" + "`r`n`r`n" +
                "Since:  " + $p.Since.ToString("yyyy-MM-dd HH:mm:ss") + " ($mins min)" + "`r`n" +
                "UPS shutdown protection is NOT active while this persists.")
            $p.LastAlert = $now
        }
    } elseif ($null -ne $p) {
        $mins = [math]::Round(($now - $p.Since).TotalMinutes, 1)
        $script:Problems.Remove($Name)
        if ($null -ne $p.LastAlert) {
            Write-UpsEvent -EventId ($EventId + 100) -EntryType Information -Message "Fossibot: $Name cleared after $mins min."
            Send-UpsEmail -Subject "RESOLVED -- $Name" -Body "$Name cleared after $mins minutes."
        }
    }
}

function Invoke-GracefulShutdown {
    param([string]$Reason)

    $msg = "FOSSIBOT UPS: Initiating shutdown. Reason: $Reason"
    Write-Warning $msg
    Write-UpsEvent -EventId 1001 -EntryType Warning -Message $msg

    $emailBody = "The Hyper-V host is initiating a graceful shutdown." + "`r`n`r`n" +
                 "Time:   " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "`r`n" +
                 "Reason: $Reason" + "`r`n`r`n" +
                 "All running VMs will be saved before shutdown."

    Send-UpsEmail -Subject "SHUTTING DOWN -- $Reason" -Body $emailBody

    $vms = Get-VM -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Running" }
    if ($vms) {
        Write-Host ("Saving " + $vms.Count + " running VMs...")
        foreach ($vm in $vms) {
            Write-Host ("  Saving VM: " + $vm.Name)
            if (-not $WhatIf) {
                Save-VM -VM $vm -ErrorAction SilentlyContinue
            }
        }
        if (-not $WhatIf) {
            Start-Sleep -Seconds 10
        }
    }

    if ($WhatIf) {
        Write-Warning "[WHATIF] Would now call: Stop-Computer -Force"
    } else {
        Stop-Computer -Force
    }
}

# ---------------------------------------------------------------------------
# Event log source setup
# ---------------------------------------------------------------------------

try {
    if (-not [System.Diagnostics.EventLog]::SourceExists("Fossibot-UPS")) {
        New-EventLog -LogName Application -Source "Fossibot-UPS" -ErrorAction SilentlyContinue
    }
} catch { }

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

$consecutiveFailures = 0
$disconnectedPolls   = 0
$lastEventSeq        = -1    # -1 = not yet synced; skip history on first poll
$inputLostSince      = $null
$shutdownInitiated   = $false

Write-Host "Fossibot UPS Monitor starting"
Write-Host "  API:         $ApiUrl"
Write-Host "  Shutdown at: SOC <= $ShutdownSocThreshold%"
Write-Host "  Warn at:     SOC <= $WarnSocThreshold%"
Write-Host "  WhatIf:      $WhatIf"
Write-Host ""

while (-not $shutdownInitiated) {

    # Poll API
    $data = $null
    try {
        $response = Invoke-WebRequest -Uri $ApiUrl -TimeoutSec 10 -UseBasicParsing
        $data = $response.Content | ConvertFrom-Json
        $consecutiveFailures = 0
    } catch {
        $consecutiveFailures++
        Write-Warning ("API poll failed (" + $consecutiveFailures + "/" + $MaxConsecutiveFailures + "): " + $_)
        if ($consecutiveFailures -ge $MaxConsecutiveFailures) {
            Write-Warning "Fossibot bridge unreachable for $consecutiveFailures consecutive polls"
            Update-Problem -Name "Telemetry bridge unreachable" -Active $true -EventId 1002 `
                -Detail "The Fossibot telemetry bridge at $ApiUrl has not answered for $consecutiveFailures polls. Last error: $_"
            # Fail safe: if we were already on battery, don't keep running blind
            if ($null -ne $inputLostSince) {
                Invoke-GracefulShutdown -Reason "Telemetry bridge lost while on battery (AC absent since $($inputLostSince.ToString('HH:mm:ss')))"
                $shutdownInitiated = $true
                break
            }
        }
        Start-Sleep -Seconds $PollIntervalSeconds
        continue
    }
    Update-Problem -Name "Telemetry bridge unreachable" -Active $false -EventId 1002

    # Forward device events (faults, SOC jumps, unknown frames) from the bridge.
    # Older bridges have no /events endpoint; ignore failures here.
    if ($null -ne $data._last_event_seq) {
        if ($lastEventSeq -lt 0 -or [int]$data._last_event_seq -lt $lastEventSeq) {
            $lastEventSeq = [int]$data._last_event_seq   # first poll or bridge restarted
        } elseif ([int]$data._last_event_seq -gt $lastEventSeq) {
            try {
                $evUrl  = "http://${PiAddress}:${Port}/events?since=$lastEventSeq"
                $events = (Invoke-WebRequest -Uri $evUrl -TimeoutSec 10 -UseBasicParsing).Content | ConvertFrom-Json
                foreach ($ev in $events.events) {
                    $lastEventSeq = [math]::Max($lastEventSeq, [int]$ev.seq)
                    Write-Host ("Bridge event: " + $ev.time + " " + $ev.kind + " " + $ev.message)
                    if ($ev.kind -in @("fault", "soc_jump", "frame")) {
                        Write-UpsEvent -EventId 1006 -EntryType Warning -Message ("Fossibot " + $ev.kind + ": " + $ev.message)
                        Send-UpsEmail -Subject ("DEVICE " + $ev.kind.ToUpper() + " -- " + $ev.message) `
                            -Body ("Time:    " + $ev.time + "`r`nKind:    " + $ev.kind + "`r`nMessage: " + $ev.message + "`r`nData:    " + $ev.data)
                    }
                }
            } catch {
                Write-Warning "Failed to read bridge events: $_"
            }
        }
    }

    # Check device connected (bridge reports false when data is stale too)
    if (-not $data._connected) {
        $disconnectedPolls++
        Write-Warning "Bridge reachable but Fossibot device not connected ($disconnectedPolls)"
        if ($disconnectedPolls -ge $MaxDisconnectedPolls) {
            Update-Problem -Name "Fossibot device not connected" -Active $true -EventId 1007 `
                -Detail ("The bridge is up but the Fossibot has not sent data for " + $data._seconds_since_update +
                         " seconds. The power station may be off, faulted, or off WiFi.")
            # Fail safe: if we were already on battery, don't keep running blind
            if ($null -ne $inputLostSince) {
                Invoke-GracefulShutdown -Reason "Fossibot telemetry lost while on battery (AC absent since $($inputLostSince.ToString('HH:mm:ss')))"
                $shutdownInitiated = $true
                break
            }
        }
        Start-Sleep -Seconds $PollIntervalSeconds
        continue
    }
    $disconnectedPolls = 0
    Update-Problem -Name "Fossibot device not connected" -Active $false -EventId 1007

    $soc         = [int]$data.soc
    $acInput     = [int]$data.ac_input_power
    $acOutput    = [int]$data.ac_output_power
    $runtimeMins = [int]$data.runtime_minutes
    $timestamp   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $runtimeHrs  = [math]::Round($runtimeMins / 60, 1)

    Write-Host ("$timestamp  SOC=$soc%  AC-in=${acInput}W  AC-out=${acOutput}W  Runtime=${runtimeHrs}h")

    # AC input loss / restore detection
    $acPresent = ($acInput -gt 10)

    if (-not $acPresent) {
        if ($null -eq $inputLostSince) {
            $inputLostSince = Get-Date
            Write-Warning "AC input lost at SOC $soc% -- starting $InputLostGracePeriodSeconds s grace period"
            Write-UpsEvent -EventId 1003 -EntryType Warning -Message "Fossibot: AC input lost. SOC=$soc%."

            $emailBody = "AC input to the Fossibot power station has been lost." + "`r`n`r`n" +
                         "Time:              $timestamp" + "`r`n" +
                         "SOC:               $soc%" + "`r`n" +
                         "AC output load:    ${acOutput}W" + "`r`n" +
                         "Runtime remaining: ${runtimeHrs} hours" + "`r`n`r`n" +
                         "Grace period: $InputLostGracePeriodSeconds seconds." + "`r`n" +
                         "If AC is not restored, the host will shut down when SOC drops below $InputLostSocThreshold%" + "`r`n" +
                         "or after $MaxOnBatteryMinutes minutes on battery, whichever comes first."

            Send-UpsEmail -Subject "POWER OUTAGE -- AC input lost (SOC $soc%)" -Body $emailBody
        }

        $lostSeconds = [int]((Get-Date) - $inputLostSince).TotalSeconds

        if ($lostSeconds -ge $InputLostGracePeriodSeconds -and $soc -le $InputLostSocThreshold) {
            Invoke-GracefulShutdown -Reason "AC absent ${lostSeconds}s, SOC=$soc% (<= $InputLostSocThreshold%)"
            $shutdownInitiated = $true
            break
        }

        if ($lostSeconds -ge ($MaxOnBatteryMinutes * 60)) {
            Invoke-GracefulShutdown -Reason "AC absent ${lostSeconds}s (>= $MaxOnBatteryMinutes min on battery), SOC reads $soc%"
            $shutdownInitiated = $true
            break
        }

    } else {
        if ($null -ne $inputLostSince) {
            $lostSeconds = [int]((Get-Date) - $inputLostSince).TotalSeconds
            $lostMins    = [math]::Round($lostSeconds / 60, 1)
            Write-Host "AC input restored after ${lostSeconds}s"
            Write-UpsEvent -EventId 1004 -EntryType Information -Message "Fossibot: AC input restored after ${lostSeconds}s. SOC=$soc%."

            # Brief delay — network may still be settling after power restore
            Start-Sleep -Seconds 5

            $emailBody = "AC input to the Fossibot power station has been restored." + "`r`n`r`n" +
                         "Time:         $timestamp" + "`r`n" +
                         "SOC:          $soc%" + "`r`n" +
                         "Outage:       $lostMins minutes ($lostSeconds seconds)" + "`r`n" +
                         "AC input now: ${acInput}W" + "`r`n`r`n" +
                         "The Hyper-V host will continue running normally."

            # Retry once if first attempt fails (SMTP may not be ready yet)
            $sent = $false
            for ($attempt = 1; $attempt -le 2 -and -not $sent; $attempt++) {
                try {
                    Send-MailMessage `
                        -SmtpServer $SmtpServer `
                        -Port       $SmtpPort `
                        -To         $EmailTo `
                        -From       $EmailFrom `
                        -Subject    ("$EmailSubjectPrefix POWER RESTORED -- AC back after $lostMins min (SOC $soc%)") `
                        -Body       $emailBody `
                        -ErrorAction Stop
                    Write-Host "Email sent: POWER RESTORED"
                    $sent = $true
                } catch {
                    Write-Warning ("Restore email attempt $attempt failed: $_")
                    if ($attempt -lt 2) { Start-Sleep -Seconds 10 }
                }
            }
        }
        $inputLostSince = $null
    }

    # SOC threshold checks
    if ($soc -le $ShutdownSocThreshold) {
        Invoke-GracefulShutdown -Reason "SOC=$soc% at or below shutdown threshold of $ShutdownSocThreshold%"
        $shutdownInitiated = $true
        break
    }

    if ($soc -le $WarnSocThreshold) {
        Write-Warning "LOW BATTERY: SOC=$soc% (warn threshold: $WarnSocThreshold%)"
        Write-UpsEvent -EventId 1005 -EntryType Warning -Message "Fossibot: Low battery warning. SOC=$soc%."
    }

    Start-Sleep -Seconds $PollIntervalSeconds
}