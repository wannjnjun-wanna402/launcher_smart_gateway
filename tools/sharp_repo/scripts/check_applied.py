#!/usr/bin/env python3
"""Is the Sharp template actually applied to this model? Check, don't guess.

    python3 scripts/check_applied.py /path/to/model-dir
    python3 scripts/check_applied.py model.gguf

A model directory can carry the chat template in TWO places -- `chat_template.jinja` and the
`chat_template` key inside `tokenizer_config.json` -- and runtimes disagree about which one
wins. Recent transformers prefers the .jinja file; oMLX and several others read the embedded
copy and ignore the file entirely. So dropping in a new .jinja can appear to do nothing, with
no error anywhere, and the only symptom is that the model still writes preamble.

This reports every source it finds, renders each one, and tells you whether they agree. If they
disagree, it says so loudly -- that mismatch is the actual bug, and it is invisible otherwise.

Exit code is 0 only if every template source present has the terseness prompt applied.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

MARKER = "Never: open with preamble"
THINK_PROBE = "kept-thought-4f2a"
SYSTEM_PROBE = "Be a pirate."


def render(src: str, msgs: list[dict], **kw) -> str:
    try:
        from jinja2 import Environment
    except ImportError:
        sys.exit("needs jinja2:  pip install jinja2")
    return Environment().from_string(src).render(
        messages=msgs, add_generation_prompt=True, **kw)


def render_probe(src: str) -> tuple:
    """What this template actually produces, across the cases the differences would show up in."""
    user = [{"role": "user", "content": "hi"}]
    cases = [
        (user, {}),
        ([{"role": "system", "content": SYSTEM_PROBE}] + user, {}),
        (user, {"enable_thinking": False}),
        (user, {"reasoning_effort": "low"}),
        ([{"role": "user", "content": "Q1"},
          {"role": "assistant", "content": f"<think>{THINK_PROBE}</think>A1"},
          {"role": "user", "content": "Q2"}], {}),
    ]
    out = []
    for msgs, kw in cases:
        try:
            out.append(render(src, msgs, **kw))
        except Exception as e:                 # a template that throws differs from one that does not
            out.append(f"__ERROR__{type(e).__name__}")
    return tuple(out)


def think_kept(rendered: str) -> bool:
    """Did last turn's reasoning survive into this prompt?

    Two shapes count. froggeric <= v22.1 passes the assistant's `<think>` tags through
    verbatim; v22.2+ extracts in-content reasoning and re-emits it as a canonical
    `<think>\\n...\\n</think>` block (that extraction is the fix for duplicated tags). Both
    retain the thought, which is the thing being measured -- so match on the probe text
    living inside a think block, not on either literal tag layout. A stock template drops
    the reasoning entirely and fails both.
    """
    return any(THINK_PROBE in blk
               for blk in re.findall(r"<think>(.*?)</think>", rendered, re.DOTALL))


def describe(src: str) -> dict:
    """Render the cases that matter and report what the template does."""
    user = [{"role": "user", "content": "hi"}]
    with_sys = [{"role": "system", "content": SYSTEM_PROBE}, {"role": "user", "content": "hi"}]
    multi = [{"role": "user", "content": "Q1"},
             {"role": "assistant", "content": f"<think>{THINK_PROBE}</think>A1"},
             {"role": "user", "content": "Q2"}]
    try:
        plain, sysd, mt = render(src, user), render(src, with_sys), render(src, multi)
    except Exception as e:                       # a template that won't render is its own answer
        return {"error": f"{type(e).__name__}: {e}"}
    return {
        "terse_count": plain.count(MARKER),
        "keeps_system": SYSTEM_PROBE in sysd,
        "retains_think": think_kept(mt),
        "identity": next((n for n in ("Nail-35b-a3b", "Dagger-27b") if n in plain), None),
        "bytes": len(src),
    }


def report(label: str, src: str) -> bool:
    d = describe(src)
    print(f"\n  [{label}]  {d.get('bytes', 0)} bytes")
    if "error" in d:
        print(f"     FAILS TO RENDER — {d['error']}")
        return False
    n = d["terse_count"]
    ok = n == 1
    print(f"     terseness prompt ......... {'yes' if n == 1 else f'NO (found {n}x)'}")
    print(f"     keeps your system prompt . {'yes' if d['keeps_system'] else 'NO'}")
    # Heuristic: a template that simply echoes message content will "pass" this without
    # implementing retention at all. Reliable as a NO, only suggestive as a yes.
    print(f"     retains thinking* ........ {'yes' if d['retains_think'] else 'no'}"
          f"{'' if d['retains_think'] else '  (stock behaviour, not froggeric-fixed)'}")
    if d["identity"]:
        print(f"     WARNING: names a specific model ({d['identity']}) — you probably want the")
        print(f"              model-agnostic template from this repo instead")
    return ok


def from_gguf(path: pathlib.Path) -> str | None:
    try:
        from gguf import GGUFReader
    except ImportError:
        sys.exit("reading a .gguf needs the gguf package:  pip install gguf")
    r = GGUFReader(str(path))
    f = r.fields.get("tokenizer.chat_template")
    if f is None:
        return None
    v = f.contents()
    return v if isinstance(v, str) else None


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[2].strip())
    target = pathlib.Path(sys.argv[1]).expanduser()
    if not target.exists():
        sys.exit(f"no such path: {target}")

    print(f"checking {target}")
    sources: dict[str, str] = {}

    if target.is_file() and target.suffix == ".gguf":
        tpl = from_gguf(target)
        if tpl is None:
            print("\n  no tokenizer.chat_template embedded — llama.cpp will fall back to a\n"
                  "  built-in template, and the terseness prompt is NOT applied.")
            return 1
        sources["embedded in .gguf"] = tpl
    else:
        j = target / "chat_template.jinja"
        if j.is_file():
            sources["chat_template.jinja"] = j.read_text()
        tc = target / "tokenizer_config.json"
        if tc.is_file():
            key = json.loads(tc.read_text()).get("chat_template")
            if isinstance(key, str):
                sources["tokenizer_config.json"] = key
        if not sources:
            print("\n  no chat template found at all — nothing is applied.")
            return 1

    ok = all([report(name, src) for name, src in sources.items()])

    if len(sources) > 1:
        # Compare what the sources DO, not how they are spelled. The documented way to patch both
        # places is to paste chat_template_oneline.txt into tokenizer_config.json, which is the
        # minified form of the same template -- byte-different by construction, behaviourally
        # identical. Comparing raw text flagged that recommended state as broken; comparing
        # renderings asks the question that actually matters: does the prompt depend on which
        # source your runtime picked?
        names = list(sources)
        renders = {n: render_probe(sources[n]) for n in names}
        base = renders[names[0]]
        differing = [n for n in names[1:] if renders[n] != base]
        print()
        if not differing:
            same_text = len({sources[n] for n in names}) == 1
            print("  Both sources render the SAME prompts — whichever your runtime prefers,")
            print("  you get the same behaviour" + ("." if same_text else
                  " (they differ only as full vs. minified text)."))
        else:
            ok = False
            print("  *** THE TWO SOURCES DISAGREE ***")
            print("  Recent transformers uses chat_template.jinja; oMLX and others read the")
            print("  copy embedded in tokenizer_config.json. Right now those RENDER DIFFERENTLY,")
            print("  so what you get depends on your runtime. Patch both to the same template.")

    print("\n  * retention is inferred from the rendered output; a template that merely echoes"
          "\n    message content passes it without implementing retention. Trust the 'no'.")
    print("\n" + ("APPLIED" if ok else "NOT APPLIED (or inconsistent) — see above"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
