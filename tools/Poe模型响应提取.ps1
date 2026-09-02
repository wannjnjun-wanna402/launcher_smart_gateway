[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ErrorActionPreference = 'Stop'
try {
    $r = Invoke-WebRequest -Uri 'https://poe.ninja.com/api/data/leagues' -UseBasicParsing -TimeoutSec 30
    [IO.File]::WriteAllText('E:\llama-win-cuda-12.4-x64\leagues.json', $r.Content)
    Write-Output ("OK " + $r.StatusCode + " len=" + $r.Content.Length)
} catch {
    Write-Output ("ERR " + $_.Exception.Message)
}
