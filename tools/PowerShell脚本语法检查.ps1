$f = Get-ChildItem "$PSScriptRoot\*.ps1" | Where-Object { $_.Name -match 'launcher_main' } | Select -First 1 -ExpandProperty FullName
$c = [System.IO.File]::ReadAllText($f, [Text.Encoding]::UTF8)
$e = $null; $ast = [Management.Automation.Language.Parser]::ParseInput($c, [ref]$null, [ref]$e)
if ($e.Count -eq 0) { Write-Host "OK"; exit 0 }
else { foreach ($err in $e) { Write-Host "L$($err.Extent.StartLine): $($err.Message)" }; exit 1 }
