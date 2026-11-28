$ErrorActionPreference = 'Stop'
$stage = $env:BB_STAGE
$target = $env:BB_TARGET
$next = Join-Path $stage 'BoardBench.exe'
$backup = Join-Path $stage 'previous.exe'
$log = Join-Path $env:BB_DATA 'update-install.log'
function Launch-App([bool]$check) {
    $arguments = @('--port', $env:BB_PORT, '--data-dir', ('"' + $env:BB_DATA + '"'))
    if ($check) { $arguments += @('--update-receipt', ('"' + (Join-Path $stage 'started') + '"')) }
    Start-Process -PassThru -FilePath $target -ArgumentList $arguments
}
try {
    $old = Get-Process -Id $env:BB_PID -ErrorAction SilentlyContinue
    if ($old -and !$old.WaitForExit(120000)) { throw 'Old process is still running.' }
    # A PyInstaller onefile bootloader can hold the EXE briefly after its child exits.
    for ($i = 0; $i -lt 60; $i++) {
        try { [IO.File]::Replace($next, $target, $backup); break }
        catch [IO.IOException] { if ($i -eq 59) { throw }; Start-Sleep -Milliseconds 500 }
    }
    try {
        $app = Launch-App $true
        for ($i = 0; $i -lt 120; $i++) {
            if (Test-Path -LiteralPath (Join-Path $stage 'started')) { break }
            if ($app.HasExited) { throw 'The new window did not open.' }
            Start-Sleep -Seconds 1
        }
    } catch { [IO.File]::Replace($backup, $target, $next); Launch-App $false; throw }
    if (!(Test-Path -LiteralPath (Join-Path $stage 'started'))) { throw "No startup confirmation. Backup: $backup" }
    Add-Content -LiteralPath $log -Value "$(Get-Date) New version launched."
    Remove-Item -LiteralPath $stage -Recurse -Force
} catch {
    Add-Content -LiteralPath $log -Value "$(Get-Date) Update failed: $_"
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show("Update failed. See $log", 'BoardBench') | Out-Null
}
