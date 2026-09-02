# ============================================================
#  基准测试数据 —— 由 merge_benchmark.py 自动生成
#  Speed   : 保留启动器原有实测 tok/s
#  Accuracy: 推理类正确率%（专项认知测评）
#  Halluc  : 抗幻分 = 100 - 记忆依赖率%
#  Score   : 综合质量分 = 推理正确率*0.5 + 记忆正确率*0.2 + 推理链完整性*15 + 原创性*15
#  Rank    : 按 Score 降序
#  BestFor : 保留启动器原有场景标签
# ============================================================
$BENCHMARK_DATA = @{
    "Qwen3VL-8B" = [PSCustomObject]@{
        Speed     = $null    # 保留实测
        Accuracy  = 92.5
        Halluc    = 100.0
        Score     = 85.7
        Rank      = 1
        BestFor   = ""
    }
    "Gemma-4-E4B" = [PSCustomObject]@{
        Speed     = $null    # 保留实测
        Accuracy  = 15.0
        Halluc    = 100.0
        Score     = 32.0
        Rank      = 2
        BestFor   = ""
    }
    "Qwen3.6-35B-MTP" = [PSCustomObject]@{
        Speed     = $null    # 保留实测
        Accuracy  = 0.0
        Halluc    = 100.0
        Score     = 26.9
        Rank      = 3
        BestFor   = ""
    }
    "Qwen3.6-35B" = [PSCustomObject]@{
        Speed     = $null    # 保留实测
        Accuracy  = 0.0
        Halluc    = 100.0
        Score     = 26.9
        Rank      = 4
        BestFor   = ""
    }
    "Qwen3.6-27B" = [PSCustomObject]@{
        Speed     = $null    # 保留实测
        Accuracy  = 2.5
        Halluc    = 100.0
        Score     = 26.2
        Rank      = 5
        BestFor   = ""
    }
    "Ornith-1.0-35B-UD" = [PSCustomObject]@{
        Speed     = $null    # 保留实测
        Accuracy  = 2.5
        Halluc    = 100.0
        Score     = 24.0
        Rank      = 6
        BestFor   = ""
    }
    "Qwen3.6-27B-MTP-IQ4" = [PSCustomObject]@{
        Speed     = $null    # 保留实测
        Accuracy  = 0.0
        Halluc    = 100.0
        Score     = 22.4
        Rank      = 7
        BestFor   = ""
    }
}
