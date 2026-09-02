import json

path = r"C:\Users\wanna402\.qwen\projects\e--ai123-aigame\chats\32e1f232-feea-4248-8212-24e08b03c1f5.jsonl"
with open(path, "r", encoding="utf-8") as f:
    for i, line in enumerate(f):
        if "find" in line.lower():
            try:
                data = json.loads(line)
                c_str = json.dumps(data, ensure_ascii=False)
                if "find " in c_str or "find.exe" in c_str:
                    print(f"--- Line {i} ---")
                    print("Timestamp:", data.get("timestamp"))
                    if "toolCalls" in data:
                        print("ToolCalls:", data["toolCalls"])
                    if "message" in data:
                        print("Message:", data["message"].get("content", "")[:150])
                        if "tool_calls" in data["message"]:
                            print("ToolCalls:", data["message"]["tool_calls"])
                    if "output" in data:
                        print("Output:", str(data["output"])[:150])
            except Exception:
                pass
