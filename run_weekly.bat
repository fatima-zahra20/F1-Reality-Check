@echo off
cd /d "C:\Users\Fatima zahra\Projects\F1-Reality-Check"

rem --publish is deliberate. Everything before s06 writes only to this machine;
rem s06 replaces the data behind a public URL. That was kept manual until two
rem things were true: the pipeline actually rebuilds every bundled table
rem (NOTES_LOG #55), and s05c reproduces itself so a republish means something
rem (NOTES_LOG #59). Publishing arbitrary variation twice a week, unattended,
rem would have been worse than not publishing at all.
rem
rem The safety chain already handles a bad week: a failed gate skips the serving
rem layers, and no fresh serving layer skips the publish. An automated run that
rem finds broken data publishes nothing and leaves the live dashboard on the
rem last good bundle.
rem
rem GITHUB_TOKEN is read from the User environment, not from any file here.
"C:\Users\Fatima zahra\anaconda3\python.exe" pipeline\run_pipeline.py --execute --publish
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