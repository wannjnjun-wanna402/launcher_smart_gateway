#!/usr/bin/env python3
"""Diagnostic utility to verify which chat template is active on a model directory or GGUF file.

Usage:
    python3 scripts/check_applied.py [path_to_model_dir_or_gguf]

Examples:
    python3 scripts/check_applied.py .
    python3 scripts/check_applied.py /models/Qwen3.8-27B-Instruct
    python3 scripts/check_applied.py /models/qwen3.8-27b.gguf
"""

import sys
import os
import re
import json

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def extract_template_version(content: str) -> str:
    if not content:
        return "None"
    m = re.search(r'template_version\s*=\s*["\']([^"\']+)["\']', content)
    if m:
        return m.group(1)
    if "qwen" in content.lower() or "im_start" in content:
        return "Stock / Unknown Qwen Template"
    return "Unknown"


def inspect_gguf(path: str):
    try:
        import gguf
        reader = gguf.GGUFReader(path)
        for field in reader.fields.values():
            if field.name == "tokenizer.chat_template":
                parts = field.parts
                for idx in field.data:
                    val = str(parts[idx])
                    if "im_start" in val:
                        return val
        return None
    except ImportError:
        # Fallback: simple binary scan for template string
        try:
            with open(path, "rb") as f:
                # Read first 10MB of GGUF header
                buf = f.read(10 * 1024 * 1024).decode("utf-8", errors="ignore")
                m = re.search(r'template_version\s*=\s*["\']([^"\']+)["\']', buf)
                if m:
                    return f'{{%- set template_version = "{m.group(1)}" %}}'
        except Exception:
            pass
        return None
    except Exception as e:
        return None


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "."

    print(f"\n{BOLD}{CYAN}=== Qwen Chat Template Diagnostic Utility ==={RESET}")
    print(f"Target: {os.path.abspath(target)}\n")

    sources = {}

    if os.path.isfile(target):
        if target.endswith(".gguf"):
            gguf_template = inspect_gguf(target)
            sources["gguf"] = gguf_template
        elif target.endswith(".jinja"):
            with open(target, "r", encoding="utf-8") as f:
                sources["chat_template.jinja"] = f.read()
        elif target.endswith(".json"):
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
                sources["tokenizer_config.json"] = data.get("chat_template", "")
    elif os.path.isdir(target):
        jinja_path = os.path.join(target, "chat_template.jinja")
        json_path = os.path.join(target, "tokenizer_config.json")

        if os.path.exists(jinja_path):
            with open(jinja_path, "r", encoding="utf-8") as f:
                sources["chat_template.jinja"] = f.read()

        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    sources["tokenizer_config.json"] = data.get("chat_template", "")
            except Exception as e:
                sources["tokenizer_config.json"] = f"Error reading JSON: {e}"

    if not sources:
        print(f"{RED}❌ No template files found in {target}{RESET}")
        print("Expected `chat_template.jinja`, `tokenizer_config.json`, or a `.gguf` file.\n")
        sys.exit(1)

    versions = {}
    for name, content in sources.items():
        ver = extract_template_version(content)
        versions[name] = ver
        status_color = GREEN if "froggeric" in ver else YELLOW
        print(f"  [{name}]")
        print(f"    Version detected: {status_color}{ver}{RESET}")
        print(f"    Size: {len(content) if content else 0} characters")

    # Check for mismatches
    unique_versions = set(versions.values())
    print("\n" + "-" * 50)

    if len(sources) > 1 and len(unique_versions) > 1:
        print(f"{YELLOW}⚠️  WARNING: TEMPLATE SOURCE MISMATCH DETECTED!{RESET}")
        print("Your runtime may pick a different template depending on precedence:")
        print("  - Transformers ≥ 4.51 / LM Studio: prefers `chat_template.jinja`")
        print("  - oMLX / legacy engines: reads `tokenizer_config.json`")
        print(f"\n{BOLD}Recommendation:{RESET} Overwrite both sources with `chat_template.jinja` and `chat_template_oneline.txt`.\n")
        sys.exit(1)
    else:
        active_ver = list(unique_versions)[0]
        if "froggeric-v22.5" in active_ver:
            print(f"{GREEN}✅ SUCCESS: Verified v22.5 template is cleanly installed!{RESET}\n")
            sys.exit(0)
        elif "froggeric" in active_ver:
            print(f"{CYAN}ℹ️  INFO: Found earlier fixed template version ({active_ver}). Upgrade to v22.5 recommended.{RESET}\n")
            sys.exit(0)
        else:
            print(f"{YELLOW}⚠️  Stock / unpatched template detected.{RESET}\n")
            sys.exit(1)


if __name__ == "__main__":
    main()
