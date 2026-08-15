#!/usr/bin/env python3

import os
import sys

os.environ.setdefault("ANTHROPIC_API_KEY", "sk-DUMMY-in-cell")

MODEL = os.environ.get("BIOMNI_LLM", "claude-haiku-4-5")

PATH = os.environ.get("BIOMNI_PATH", "/root/bd")

BEGIN, END = "BIOMNI_OUT_BEGIN", "BIOMNI_OUT_END"

def main():
    prompt = sys.argv[1] if len(sys.argv) > 1 else ""
    if not prompt.strip():
        print(BEGIN); print("(leerer Prompt)"); print(END)
        return
    from biomni.config import default_config
    default_config.use_tool_retriever = False
    default_config.commercial_mode = True
    default_config.source = "Anthropic"
    from biomni.agent import A1
    a = A1(path=PATH, llm=MODEL, source="Anthropic",
           expected_data_lake_files=[], commercial_mode=True, use_tool_retriever=False)
    r = a.go(prompt)
    ans = r[-1] if isinstance(r, (list, tuple)) and r else r
    print(BEGIN)
    print(ans)
    print(END)

if __name__ == "__main__":
    main()
