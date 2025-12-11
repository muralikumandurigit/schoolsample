import sys

# LLM import (gpt4all)
try:
    from gpt4all import GPT4All
    GPT4ALL_AVAILABLE = True
except Exception:
    GPT4ALL_AVAILABLE = False

print("GPT4All available:", GPT4ALL_AVAILABLE)