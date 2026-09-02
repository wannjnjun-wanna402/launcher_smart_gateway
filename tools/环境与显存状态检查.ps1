try {
  [void][xml](Get-Content -Raw 'E:\llama-win-cuda-12.4-x64\pelican-riding.svg')
  Set-Content -Path 'E:\llama-win-cuda-12.4-x64\_check.txt' -Value 'XML_OK'
} catch {
  Set-Content -Path 'E:\llama-win-cuda-12.4-x64\_check.txt' -Value ('ERR: ' + $_.Exception.Message)
}
