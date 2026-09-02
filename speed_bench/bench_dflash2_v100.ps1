# PowerShell 鍚姩鑴氭湰 (UTF-8 with BOM)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$PythonExe = "C:\Users\wanna402\AppData\Local\Programs\Python\Python313\python.exe"
$ScriptPy = Join-Path $PSScriptRoot "bench_dflash2_v100.py"

Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host "       Qwen3.8-27B DFlash 2 vs MTP vs Baseline 涓撶敤娴嬮€熻瘎浼板浠?(V100)" -ForegroundColor Cyan
Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "璇烽€夋嫨娴嬭瘎妯″紡锛?
Write-Host "  [1] 蹇€熸ā寮?(3缁勬牳蹇冨姣?+ 3绫讳唬琛ㄤ换鍔★紝绾?3~4 鍒嗛挓)" -ForegroundColor Yellow
Write-Host "  [2] 鍏ㄩ噺涓撲笟妯″紡 (7缁勫叏鍙傛暟缃戞牸 + 6澶х湡瀹炰换鍔″満鏅紝鍏ㄩ潰娣卞害鎶ュ憡)" -ForegroundColor Green
Write-Host ""

$choice = Read-Host "璇疯緭鍏ラ€夐」 (1 鎴?2锛岄粯璁?1)"
if ($choice -eq "2") {
    Write-Host "`n[INFO] 姝ｅ湪鍚姩鍏ㄩ噺涓撲笟娴嬭瘎妯″紡..." -ForegroundColor Green
    & $PythonExe $ScriptPy
} else {
    Write-Host "`n[INFO] 姝ｅ湪鍚姩蹇€熸祴璇勬ā寮?.." -ForegroundColor Yellow
    & $PythonExe $ScriptPy --quick
}

Write-Host "`n娴嬭瘎鎵ц瀹屾瘯锛? -ForegroundColor Cyan
