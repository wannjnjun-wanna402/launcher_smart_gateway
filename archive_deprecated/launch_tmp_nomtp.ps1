# Temporary MID-HIGH server with MTP disabled (spec-type none) on port 8084.
# Purpose: verify whether MTP speculative decoding causes the UNC backslash loss.
$srvArgs = @(
    '-m', 'E:\models\Qwen3.8-27B-NVFP4-MTP-MID-HIGH.gguf',
    '-ngl', '99', '--cache-type-k', 'q8_0', '--cache-type-v', 'q8_0',
    '-c', '98304', '-b', '2048', '--ubatch-size', '2048', '-t', '6',
    '--parallel', '1',
    '--flash-attn', 'on',
    '--image-min-tokens', '1024',
    '--ctx-checkpoints', '4',
    '--spec-type', 'none',
    '--reasoning', 'auto', '--reasoning-budget', '2048', '--reasoning-effort', 'medium',
    '--reasoning-format', 'deepseek', '--reasoning-preserve',
    '--no-mmproj-offload', '--no-warmup',
    '--temp', '0.3', '--top-p', '0.95', '--top-k', '20', '--min-p', '0.05',
    '--dry-multiplier', '0.0', '--dry-base', '1.75', '--dry-allowed-length', '2', '--dry-penalty-last-n', '256',
    '--repeat-penalty', '1.05', '--presence-penalty', '0.0',
    '--jinja', '--chat-template-file', 'E:\llama-win-cuda-12.4-x64\chat_template_qwen_fixed.jinja',
    '--alias', 'Qwen3.8-27B-MID-HIGH-NOMTP', '--port', '8084', '--host', '127.0.0.1', '--api-key', 'llamacpp'
)
$p = Start-Process -FilePath 'E:\llama-win-cuda-12.4-x64\llama-server.exe' -ArgumentList $srvArgs -PassThru -WindowStyle Hidden
Write-Output ("TMP_PID=" + $p.Id)
