@echo off
cd /d "C:\Users\Fatima zahra\Projects\F1-Reality-Check"

"C:\Users\Fatima zahra\anaconda3\python.exe" pipeline\run_pipeline.py --execute
set CODE=%ERRORLEVEL%

powershell -NoProfile -Command ^
  "$log = Get-ChildItem 'logs\pipeline_*.log' | Sort-Object LastWriteTime -Descending | Select-Object -First 1;" ^
  "$text = if ($log) { Get-Content $log.FullName -Raw } else { '' };" ^
  "$rows = if ($text -match 'new rows:\s*([\d,]+)') { $matches[1] } else { '?' };" ^
  "$rebuild = if ($text -match 'rebuild:\s*(\w+)') { $matches[1] } else { '?' };" ^
  "$gate = if ($text -match 'gate:\s*(\w+)') { $matches[1] } else { '?' };" ^
  "$code = %CODE%;" ^
  "$status = switch ($code) { 0 { 'Formula 1' } 1 { 'STEP FAILED' } 2 { 'GATE FAILED' } default { \"exit $code\" } };" ^
  "$msg = \"$status`n`nNew rows: $rows`nRebuild: $rebuild`nGate: $gate`n`nLog: $($log.Name)\";" ^
  "[void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms');" ^
  "[System.Windows.Forms.MessageBox]::Show($msg, 'F1 Reality Check Pipeline')"

exit /b %CODE%