---
license: apache-2.0
base_model: unsloth/Qwen3.8-27B-NVFP4
pipeline_tag: text-generation
library_name: gguf
description: "Family of 8 GGUFs of Qwen3.8-27B (NVFP4): ORIG (source-preserving NVFP4 MLP + BF16 attention), VERY-LOW / COMPACT-LOW / LOW / MEDIUM / HIGH / VERY-HIGH (448-tensor byte-identical NVFP4 backbone + per-tier heads), HIGHEST (NVFP4 MLP + Q8_0 attention/lm_head + BF16 embeddings). Native VLM (vision+video), MTP speculative decoding, 262,144 native context. Blackwell sm_120."
tags:
  - gguf
  - nvfp4
  - qwen3.8
  - qwen3.5
  - blackwell
  - mtp
  - speculative-decoding
  - vision
  - multimodal
  - llama.cpp
language:
  - en
  - multilingual
---

# Qwen3.8-27B-NVFP4-MTP-GGUF

A **family of eight GGUF files** of `Qwen3.8-27B` (the native vision-language 27B dense model, Gated DeltaNet + Gated Attention hybrid layout, 262,144-token native context, MTP speculative head), converted from [unsloth/Qwen3.8-27B-NVFP4](https://huggingface.co/unsloth/Qwen3.8-27B-NVFP4). The **MTP (multi-token prediction) speculative head is baked into every file** — no separate drafter needed.

- **`ORIG`** — the source-preserving conversion: native **NVFP4 MLP backbone** + **BF16 attention/embeddings** (the source's F8 attention is dequantized to BF16 because GGML has no F8 tensor type). This is the largest file and the one all tiers are derived from.
- **`VERY-LOW` / `COMPACT-LOW` / `LOW` / `MEDIUM` / `HIGH` / `VERY-HIGH`** — a compact family sharing a **byte-identical 448-tensor NVFP4 backbone** (all attention + MLP re-quantized to NVFP4), differing only in the 10 "extra" tensors (LM head, token embedding, MTP draft head). `COMPACT-LOW` fills the gap between `VERY-LOW` and `LOW` — slightly smaller than `LOW` while keeping a materially stronger LM head than `VERY-LOW` (Q4_K vs Q3_K).
- **`HIGHEST`** — the top tier: keeps the source's native **NVFP4 MLP** (layers 0-55) exactly as in `ORIG`, restores **Q8_0** for attention + the late MLP layers + the LM head, and keeps the **token embedding + MTP head in BF16**. The closest compact approximation of the source layout, for high-end GPUs.

The goal: **keep native NVFP4 density across the whole model for Blackwell**, and offer a size/precision ladder for the tensors that most affect output quality and decode speed. On our dual 16 GB Blackwell setup every tier fits and runs (see notes before treating any numbers as meaningful).

**Vision works.** The model is a native VLM (images and video). Pair any of these GGUFs with the **`mmproj-BF16.gguf`** included in this repo (byte-identical to [unsloth/Qwen3.8-27B-GGUF](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF)'s projector) via `--mmproj`.

> For 16 GB VRAM users who want **NVFP4 as small as possible**: the same NVFP4 backbone, squeezed further — the MTP head stripped out and the LM head / embeddings trimmed to the absolute minimum — is published as `BUDGET` / `STARVED` in [esatapedico/Qwen3.8-27B-NVFP4-BUDGET-GGUF](https://huggingface.co/esatapedico/Qwen3.8-27B-NVFP4-BUDGET-GGUF).

## Follow along & support

I post updates on new conversions, benchmarks, and what I'm working on over on Ko-fi. Follow along there to keep up with new releases and the work in progress. If you'd like to support more of it, a coffee is always welcome. I do this on consumer hardware and like seeing how far it goes. More is on the way.

☕ [ko-fi.com/esatapedico](https://ko-fi.com/esatapedico). Updates, work-in-progress, and an optional coffee.

## The eight files

| File | Size | lm_head (`output.weight`) | token_embd | MTP head (blk.64) | Attention |
|---|---|---|---|---|---|
| **`Qwen3.8-27B-NVFP4-MTP-ORIG.gguf`** | 33.13 GB | `BF16` | `BF16` | `BF16` | **`BF16`** (source F8 dequant) |
| **`Qwen3.8-27B-NVFP4-MTP-VERY-LOW.gguf`** | 14.86 GB | `Q3_K` | `Q2_K` | `Q2_K` | `NVFP4` |
| **`Qwen3.8-27B-NVFP4-MTP-COMPACT-LOW.gguf`** | 14.12 GB | `Q4_K` | `Q3_K` | `Q2_K` | `NVFP4` |
| **`Qwen3.8-27B-NVFP4-MTP-LOW.gguf`** | 15.53 GB | `Q5_0` | `IQ4_XS` | `IQ4_XS` | `NVFP4` |
| **`Qwen3.8-27B-NVFP4-MTP-MEDIUM.gguf`** | 16.38 GB | `Q8_0` | `Q6_K` | `IQ4_XS` | `NVFP4` |
| **`Qwen3.8-27B-NVFP4-MTP-HIGH.gguf`** | 17.57 GB | `BF16` | `Q6_K` | `IQ4_XS` | `NVFP4` |
| **`Qwen3.8-27B-NVFP4-MTP-VERY-HIGH.gguf`** | 19.69 GB | `BF16` | `BF16` | `BF16` | `NVFP4` |
| **`Qwen3.8-27B-NVFP4-MTP-HIGHEST.gguf`** | 23.19 GB | `Q8_0` | `BF16` | `BF16` | **`Q8_0`** |
| **`mmproj-BF16.gguf`** | 931 MB | — (vision projector) | | | |

### Tensor layout

**ORIG** (1,202 tensors): the direct `--outtype auto` conversion of the pre-processed source checkpoint.

| GGML type | Tensors | Size | Component |
|---|---|---|---|
| `NVFP4` | 168 | 8.42 GB | MLP gate/up/down of layers 0-55 (source NVFP4) |
| `BF16` | 338 | 24.69 GB | attention (self_attn + linear_attn, source F8 dequantized), MLP layers 56-63, LM head, embeddings, MTP head |
| `F32` | 696 | 0.01 GB | norms, scales |

**VERY-LOW–VERY-HIGH** (1,202 tensors): the 6 compact tiers share a **byte-identical 448-tensor NVFP4 backbone** (all 64 layers' attention + MLP, 13.69 GB) and differ only in the 10 extra tensors:

| GGML type | Tensors | Component |
|---|---|---|
| `NVFP4` | 448 | all 64 transformer blocks (attention QKV/output + FFN gate/up/down) — identical in all tiers |
| `F32` | 744 | norms, gates, scales |
| tier-dependent | 10 | `output.weight`, `token_embd.weight`, `blk.64.*` MTP draft block (incl. `nextn.eh_proj`) |

**HIGHEST** (1,202 tensors): mirrors the source layout — the **NVFP4 MLP of layers 0-55 is byte-identical to `ORIG`**, the BF16 tensors (attention, MLP 56-63, LM head) are restored to **Q8_0**, and the token embedding + MTP head stay **BF16**.

| GGML type | Tensors | Size | Component |
|---|---|---|---|
| `NVFP4` | 168 | 8.42 GB | MLP gate/up/down of layers 0-55 (byte-identical to ORIG) |
| `Q8_0` | 281 | 11.30 GB | attention (self_attn + linear_attn), MLP layers 56-63, LM head |
| `BF16` | 9 | 3.39 GB | token_embd, MTP head (blk.64) |
| `F32` | 744 | 0.01 GB | norms, gates, scales |

The **MTP draft head is embedded** in every GGUF (`blk.64.nextn.*`), so no separate drafter file is needed. Enable it in llama.cpp with `--spec-type draft-mtp`.

## Why the ORIG file exists, and why the tiers are the way they are

The source checkpoint, [unsloth/Qwen3.8-27B-NVFP4](https://huggingface.co/unsloth/Qwen3.8-27B-NVFP4), is a **mixed-precision** compressed-tensors model:

- **group_0 — F8 float-quantized** (`float-quantized`, 8-bit): `self_attn.q/k/v/o_proj`, `linear_attn.in_proj_qkv/in_proj_z/out_proj`, `lm_head`, and the MLP of layers **56-63**
- **group_1 — NVFP4** (`nvfp4-pack-quantized`, 4-bit): the MLP of layers **0-55**

llama.cpp's converter does not yet accept a compressed-tensors checkpoint with more than one config group unless all are NVFP4 (it raises `NotImplementedError`). To convert this model we therefore:

1. **Pre-processed** the checkpoint: dequantized the 233 F8 tensors to BF16 in place (per-channel `weight × scale`), removed their `weight_scale` tensors, kept the NVFP4-packed MLP tensors untouched, and pinned the config to a single `nvfp4-pack-quantized` group.
2. **Converted** with `convert_hf_to_gguf.py --outtype auto` → `ORIG` (NVFP4 MLP preserved natively, F8 attention materialized as BF16).
3. **Built the 7 tiers** with `llama-quantize --tensor-type-file <overrides>` from the ORIG parent: the 6 compact tiers re-quantize the BF16 attention **to NVFP4** for a compact uniform backbone and pin the extra tensors per tier; `HIGHEST` instead restores attention/MLP56-63/lm_head to **Q8_0** while keeping the NVFP4 MLP and BF16 embedding/MTP head.

**Honest trade-off note:** the compact tiers re-quantize attention from BF16 (which was F8 in the source) down to NVFP4. That is a second quantization step on those tensors (F8 → BF16 → NVFP4) and does lose some attention precision vs the source. `HIGHEST` keeps the same NVFP4 MLP as ORIG and uses Q8_0 (instead of BF16) for the attention path — much closer to the source's effective precision at ~10 GB less than ORIG. The `ORIG` file preserves the source's attention quality at the cost of a 33 GB file. If you want the highest-fidelity version, use `ORIG` or `HIGHEST`; if you need the compact footprint, the tiers are ~2× smaller.

## Attribution & provenance

This is a **derivative work** built entirely from existing Apache-2.0 artifacts. Nothing here was trained or fine-tuned. Credit belongs to:

1. **Alibaba / Qwen team** for the base model, [Qwen/Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B) (Apache-2.0): 27B dense, 64 layers, Gated DeltaNet + Gated Attention hybrid, native vision-language, native 262,144-token context, MTP head.
2. **Unsloth** for the **NVFP4 quantization** [unsloth/Qwen3.8-27B-NVFP4](https://huggingface.co/unsloth/Qwen3.8-27B-NVFP4) (Apache-2.0) and the **vision projector** [unsloth/Qwen3.8-27B-GGUF](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF) (Apache-2.0, `mmproj-BF16.gguf`).
3. **This repo's author** for the F8→BF16 pre-processing, the GGUF conversion, and the tier splicing.

The NVFP4 tensors are **native GGML type 40**, preserved from the source checkpoint through GGUF conversion (for the MLP backbone) with no re-quantization round trip.

## How this was made

1. `unsloth/Qwen3.8-27B-NVFP4` (safetensors, mixed F8+NVFP4) was **pre-processed** by dequantizing F8→BF16 and keeping the NVFP4-packed tensors (see above) — this is the only step that touches values beyond type conversion, and only on the 233 F8 tensors.
2. Converted to GGUF with `convert_hf_to_gguf.py --outtype auto` → `ORIG` (33.13 GB).
3. Each tier produced with `llama-quantize --tensor-type-file <overrides>` from the ORIG parent. The per-tier override maps are in this repo (`overrides-very-low.txt`, `overrides-compact-low.txt`, `overrides-low.txt`, `overrides-medium.txt`, `overrides-high.txt`, `overrides-very-high.txt`, `overrides-highest.txt`, 1,202 entries each).

The **NVFP4 backbone is byte-identical** across the family: the 448-tensor backbone of the six compact tiers is the same set of bytes in every tier, and `HIGHEST`'s 168 NVFP4 MLP tensors are byte-identical to `ORIG`'s (verified: per-tensor SHA-256).

## Repository contents

- **`Qwen3.8-27B-NVFP4-MTP-ORIG.gguf`** (33.13 GB) — source-preserving conversion
- **`Qwen3.8-27B-NVFP4-MTP-VERY-LOW.gguf`** (14.86 GB)
- **`Qwen3.8-27B-NVFP4-MTP-COMPACT-LOW.gguf`** (14.12 GB)
- **`Qwen3.8-27B-NVFP4-MTP-LOW.gguf`** (15.53 GB)
- **`Qwen3.8-27B-NVFP4-MTP-MEDIUM.gguf`** (16.38 GB)
- **`Qwen3.8-27B-NVFP4-MTP-HIGH.gguf`** (17.57 GB)
- **`Qwen3.8-27B-NVFP4-MTP-VERY-HIGH.gguf`** (19.69 GB)
- **`Qwen3.8-27B-NVFP4-MTP-HIGHEST.gguf`** (23.19 GB)
- **`mmproj-BF16.gguf`** (931 MB) — vision projector (byte-identical to unsloth's)
- **`overrides-very-low.txt`**, **`overrides-compact-low.txt`**, **`overrides-low.txt`**, **`overrides-medium.txt`**, **`overrides-high.txt`**, **`overrides-very-high.txt`**, **`overrides-highest.txt`** — per-tensor quantization maps (1,202 entries each) for reproduction

### SHA-256

```
f18098dfc32ca398f63093c113d48bd962684c78a3e93d4158f8f440699f7cae  Qwen3.8-27B-NVFP4-MTP-ORIG.gguf
74ea17ea05e0e0241af8d5b29cdea38b3f4509f66d9b96c1ab05f0e1f0e537d9  Qwen3.8-27B-NVFP4-MTP-VERY-LOW.gguf
ac0ef9c5eceb5a5dc9b266eacc9372508158713c6c35618cf735675451fdd3ac  Qwen3.8-27B-NVFP4-MTP-COMPACT-LOW.gguf
ce66a629d4a3516bba27ca91de29372f086f90f72ddb92fe298de67b8bb88bbc  Qwen3.8-27B-NVFP4-MTP-LOW.gguf
f0b4c538c75037f026bde3b650f0ca639d382c128a4572769dce1183db86253a  Qwen3.8-27B-NVFP4-MTP-MEDIUM.gguf
d57008707b0558bde05ce61d7402e4e668ffd45a4c97d03d2ad97db73f98d403  Qwen3.8-27B-NVFP4-MTP-HIGH.gguf
3e52d6280ee650520a2d901002121c11cf9d23ac75f23c52a362bf285d561d81  Qwen3.8-27B-NVFP4-MTP-VERY-HIGH.gguf
6a202c2faf67f79d4c8c61ec940da7a62bd59a87608508fe8048131630cc4ba6  Qwen3.8-27B-NVFP4-MTP-HIGHEST.gguf
83ee4f4f205fa514161778c41df1ea14144faa0f713510893b63c2395f5c2d53  mmproj-BF16.gguf
```

## First observations (naive, single-run, not a benchmark)

We did not run a proper benchmark. What follows are informal first impressions from single-stream runs, included only so others know what to expect. **Do not treat these as claims.**

**On reading the numbers:** prefill t/s is a mean over the run and **degrades as context grows**, so figures from different context settings are **not directly comparable** — the 180k-context runs (~645-650 t/s), the 32k-context runs (~1250-2290 t/s) and the community RTX 5090 numbers all sit at different context sizes, hardware, and prompts. Compare numbers only within the same context and harness.

### Our run (180k payload, one fresh pod per tier)

- **Hardware:** 2x NVIDIA Blackwell 16 GB (RTX 5070 Ti + RTX 5060 Ti), `split-mode: tensor`, flash attention on, quantized KV cache.
- **Prompt:** one 180,052-token payload to a ~1.2k-2k-token essay, `max_tokens` 20,000 (repetition_analysis.py). This payload was **highly repetitive** (the same boilerplate unit ~2,000 times), which likely inflates MTP acceptance and speedups.
- **Sampling:** temperature 0.6, top_p 0.95, top_k 20, min_p 0. We previously recommended DRY anti-loop sampling for this family; we no longer do — it interferes with verbatim reproduction of long strings (paths, identifiers, tool arguments), which matters for coding and tool use.
- **MTP:** `--spec-type draft-mtp`, `spec_n_max 6`, `spec_p_min 0.75`.
- **Context:** 307,200 (LOW, MEDIUM) / 221,184 (HIGH) / 204,800 (VERY-HIGH) — the larger HIGH/VERY-HIGH weights don't fit a 307,200-token KV cache on the 16 GB-per-GPU split.

| Tier | Prefill t/s | Decode t/s | n_dec | MTP acc | MTP len | req s |
|---|---|---|---|---|---|---|
| LOW (Q5_0 / IQ4_XS / IQ4_XS) | 645.6 | 18.44 | 1,151 | 0.726 | 2.88 | 361 |
| MEDIUM (Q8_0 / Q6_K / IQ4_XS) | 649.3 | 18.48 | 1,907 | 0.749 | 2.91 | 401 |
| HIGH (BF16 / Q6_K / IQ4_XS) | 647.6 | 15.54 | 1,795 | 0.751 | 2.65 | 416 |
| VERY-HIGH (BF16 / BF16 / BF16) | 650.4 | 15.42 | 2,007 | 0.729 | 2.77 | 429 |

Naive first impressions:

- **Prefill is effectively identical across tiers** (~645-650 t/s) — expected: the 448-tensor NVFP4 backbone is byte-identical in all four files.
- **Decode splits into two groups**: LOW/MEDIUM (Q5_0/Q8_0 lm_head) at ~18.5 t/s vs HIGH/VERY-HIGH (BF16 lm_head) at ~15.5 t/s — the BF16 LM head costs more per MTP verification pass, the same pattern a community tester later measured on an RTX 5090. Tokens generated differ per tier (1.2k-2k), so exact numbers are indicative.
- **MTP acceptance 0.73-0.75** on this long repetitive payload — higher than short-prompt runs (see community numbers below); the draft head exploits the document's repetitive passages. Lower than our 711-family numbers on the same harness (0.87-0.88) — different model, not apples-to-apples.
- **All "suspicious" repetition flags are reasoning-segment only**; content is clean on every tier (distinct-5-gram ≥ 0.99, no adjacent-dup stutter) under the sampling preset used for this run. MEDIUM (Q8_0 head) shows the worst reasoning repetition (a 5-gram repeated 6×); HIGH is the cleanest compact tier.

### New tiers at 32k context (one fresh pod per tier)

The newest tiers were exercised at 32,768 context with quantized `q4_0` KV, on the same dual-16GB box, with a 28k-token payload (`source-28k.txt`) and `max_tokens` 2000 (COMPACT-LOW ran the same harness at `max_tokens` 20000).

| Tier | Prefill t/s | Decode t/s | MTP acc | MTP len | req s |
|---|---|---|---|---|---|
| VERY-LOW (Q3_K / Q2_K / Q2_K) | — | — | — | — | not yet benchmarked |
| **COMPACT-LOW (Q4_K / Q3_K / Q2_K)** | **2281.1** | **25.52** | — | — | **94.8** |
| HIGHEST (Q8_0 attn / BF16 embd / BF16 MTP) | 1250.4 | 23.49 | 0.765 | 2.88 | 122.6 |

First impressions:

- **COMPACT-LOW** runs at the same prefill speed as the compact tiers (~2281 vs ~2288 t/s) and slightly lower decode than LOW (25.5 vs ~27 t/s — Q4_K vs Q5_0 lm_head is the only decode-relevant difference, and the difference is small). Output was clean (content distinct-5-gram 0.978, no adjacent-dup stutter); the run stopped naturally (`finish_reason: stop`). MTP was left disabled in the deployed config for this run.
- **A note on the COMPACT-LOW numbers:** at 32k context the 14.1 GB model fits comfortably on a **single** 16 GB card, so this run likely exercised one GPU — which is consistent with its prefill matching the single-card BUDGET/STARVED numbers (~2287-2289 t/s) rather than a dual-GPU-split result. Treat these as single-GPU figures at 32k context.
- **HIGHEST** prefill is ~1.8× slower than the compact tiers at the same context (1250 vs ~2288 t/s) and decode ~15% slower (23.5 vs ~27 t/s) — the price of Q8_0 attention/lm_head + BF16 token_embd. MTP acceptance 0.765 / mean len 2.88, comparable to the compact tiers.
- HIGHEST is too large to pair with a big KV cache on 16 GB-per-GPU splits; it serves comfortably at 131,072 context and below. At 32k context it fits a **single** 16 GB card (BUDGET/STARVED of the sibling repo peak ~15.7-15.9 GiB).
- The `BUDGET` / `STARVED` ultra-compact tiers in the sibling repo (MTP head stripped to fit 16 GB) hit 2287-2289 t/s prefill and 27.0-27.4 t/s decode at 32k context.

### Community benchmark on RTX 5090 (llama.cpp b10434) — by PierpaoloPernici

A community user benchmarked LOW / MEDIUM / VERY-HIGH on a single RTX 5090 32 GB (9 short prompts, seed 42, `--spec-draft-n-max 4`, 192K ctx, KV q8_0, temp 1.0). These are their numbers, not ours — see the [full gist](https://gist.github.com/PierpaoloPernici/2e6f6f42965d531b364f39ab0e8a52ad) for the complete results (per-prompt breakdown, method, configs).

| Tier | MTP acc | Avg tok/s | Peak tok/s | VRAM @192K |
|---|---|---|---|---|
| **LOW** (Q5_0 / IQ4_XS / IQ4_XS) | 49.0% | **148.3** | **203** | 25.0 GiB |
| **MEDIUM** (Q8_0 / Q6_K / IQ4_XS) | 44.3% | 133.7 | 182 | 25.3 GiB |
| **VERY-HIGH** (BF16 / BF16 / BF16) | 49.5% | 111.9 | 139 | 26.9 GiB |
| Qwen3.6-27B NVFP4-MTP ref (michaelw9999) | 69.7% | 113.8 | 133 | — |

Their main points:

- **LOW is the throughput winner** (~148 avg, up to 203 tok/s): the small Q5_0 LM head makes every target verification + MTP draft pass cheaper while acceptance stays ~49%.
- **VERY-HIGH is the odd one out**: it accepts ~5pp more drafts than MEDIUM but is *slower* — the BF16 LM head costs more on each verification pass than the acceptance gain saves. BF16 buys output quality, not speed.
- **No quality cliff on LOW** in their spot-checks (coherent outputs — e.g. answers a "two sentences" prompt with exactly 2 sentences).
- **All three tiers beat the Qwen3.6 NVFP4 baseline** on throughput despite lower acceptance.
- **Long context (64K) raises acceptance**: LOW 49→56%, MEDIUM 44→53% — the draft head exploits the document's repetitive passages.
- **`--spec-draft-n-max` sweep (LOW)**: wall-time peak at n-max 4 (11.5 s / 9 prompts); n-max 2 is the most draft-efficient (68% accepted) but ~10% slower in wall time; n-max 6/8 collapse acceptance (38%/28%) and get slower. Our deployed config uses `spec_n_max 6` — on their hardware 4 was fastest; try 1-6 and keep whatever is fastest on yours.
- **Gotcha:** a stale HF cache produced **0% MTP acceptance** (mean len 1.00) until the file was re-downloaded — verify the file SHA before debugging anything else. They confirmed the re-uploaded hashes (LOW `ce66a629…`, MEDIUM `f0b4c538…`, VERY-HIGH `3e52d628…`) match this repo's current files.

Benchmark tool: they published `mtp-bench.py` (9 prompts, seed 42, accept-rate / tok/s / wall, `--long-context`, `--diff`).

## Usage

### llama.cpp / llama-server

```bash
llama-server \
  --model Qwen3.8-27B-NVFP4-MTP-MEDIUM.gguf \
  --mmproj mmproj-BF16.gguf \
  --ctx-size 262144 \
  --flash-attn on \
  --spec-type draft-mtp \
  --spec-draft-n-max 6 \
  --spec-draft-p-min 0.75 \
  --temp 0.7 --top-p 0.95 --top-k 20
```

- **Requires** a recent llama.cpp with NVFP4 (GGML type 40) CUDA kernels and `sm_120` support (Blackwell).
- **Requires** the `draft-mtp` spec path (merged upstream as `LLAMA_CONTEXT_TYPE_MTP`).
- For vision input, pass `--mmproj mmproj-BF16.gguf`.
- Qwen3.8's official sampling presets: thinking mode `temp 1.0 / top_p 0.95 / top_k 20`; instruct mode `temp 0.7 / top_p 0.80 / top_k 20`. Pick per use case.
- MTP performance is hardware-dependent: try `--spec-draft-n-max` values 1 through 6 and keep whatever is fastest on your system.

## Notes

- **Hardware:** our test box is 2× NVIDIA Blackwell 16 GB (RTX 5070 Ti + RTX 5060 Ti). All tiers fit a 16 GB card at reasonable context; the larger tiers prefer a split or smaller context.
- **First observations and a community RTX 5090 benchmark are in the section above** (naive, single-run — do not treat as claims).
- The `ORIG` file is 33 GB — it fits a single 16 GB GPU only with heavy context reduction or CPU offload. It is included for fidelity and as the tier-derivation source, not as the recommended serving file.
- For **ultra-compact NVFP4 tiers aimed at 16 GB VRAM** (everything trimmed to the minimum, including removing the MTP head), see the sibling repository [esatapedico/Qwen3.8-27B-NVFP4-BUDGET-GGUF](https://huggingface.co/esatapedico/Qwen3.8-27B-NVFP4-BUDGET-GGUF).
- Non-NVFP4 (K-quant) GGUFs of the base model are available in Unsloth's original repository, [unsloth/Qwen3.8-27B-GGUF](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF) — they run on any GPU but drop the native NVFP4 density this family is built around.

## License

**Apache-2.0**, identical to every upstream artifact. The base model license governs; GGUF conversion and quantization are transformations, not new training. When redistributing, please retain attribution to Qwen (Alibaba) and Unsloth as above.

"Qwen" is a trademark of Alibaba. Trademarks are used here only to identify upstream models; this repository is not affiliated with, sponsored by, or endorsed by Alibaba or Unsloth.

## Note on this card

This model card was written by an AI assistant at the request of the repository author, who did the engineering. As with any AI-generated text, there may be errors; please verify anything important (hashes, sizes, commands) against the file itself before relying on it.
