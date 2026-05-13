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
$MaxConsecutiveFailures      = 3     # API failures before logging an alert
$PollIntervalSeconds         = 30    # Seconds between polls

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
            Write-UpsEvent -EventId 1002 -EntryType Warning -Message "Fossibot telemetry bridge unreachable for $consecutiveFailures polls."
        }
        Start-Sleep -Seconds $PollIntervalSeconds
        continue
    }

    # Check device connected
    if (-not $data._connected) {
        Write-Warning "Bridge reachable but Fossibot device not connected"
        Start-Sleep -Seconds $PollIntervalSeconds
        continue
    }

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
                         "If AC is not restored and SOC drops below $InputLostSocThreshold%, the host will shut down."

            Send-UpsEmail -Subject "POWER OUTAGE -- AC input lost (SOC $soc%)" -Body $emailBody
        }

        $lostSeconds = [int]((Get-Date) - $inputLostSince).TotalSeconds

        if ($lostSeconds -ge $InputLostGracePeriodSeconds -and $soc -le $InputLostSocThreshold) {
            Invoke-GracefulShutdown -Reason "AC absent ${lostSeconds}s, SOC=$soc% (<= $InputLostSocThreshold%)"
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