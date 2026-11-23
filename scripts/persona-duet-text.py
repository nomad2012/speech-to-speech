# Text-only re-implementation of the versus DuetEngine loop (no TTS).
import json, sys, urllib.request

LLM_URL = "http://localhost:8080/v1/chat/completions"
MODEL = "qwen3.8-flash-next-iq3_s"
PDIR = "/home/dennis/projects/speech-to-speech/Personas"

def load_prompt(name):
    text = open(f"{PDIR}/{name}/prompt.md").read()
    # strip YAML frontmatter
    if text.startswith("---"):
        text = text.split("---", 2)[2]
    return text.strip()

def say(persona, other, history):
    system = (load_prompt(persona) + "\n\nKeep your response brief — 100 words or less. "
              "Do not include reasoning, thinking traces, or internal monologue."
              f"\n\nYou are having a conversation with {other}.")
    payload = {"model": MODEL, "messages": [{"role":"system","content":system}]+history,
               "max_tokens": 256, "reasoning_effort": "none"}
    req = urllib.request.Request(LLM_URL, data=json.dumps(payload).encode(),
                                 headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.load(r)
    return d["choices"][0]["message"]["content"].strip()

if len(sys.argv) != 5:
    sys.exit("usage: persona-duet-text.py PERSONA_A PERSONA_B \"TOPIC\" TURNS")
a, b, topic, turns = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
hist_a, hist_b = [], []   # per-persona histories, mirroring DuetEngine
for i in range(turns):
    speaker, other = (a, b) if i % 2 == 0 else (b, a)
    history = hist_a if i % 2 == 0 else hist_b
    # mirror engine: each persona sees the OTHER's messages as user turns
    cross = hist_b if i % 2 == 0 else hist_a
    seed = topic if not cross else cross[-1]["content"]
    msgs = [{"role":"user","content":seed}] + [
        {"role":"assistant" if m in history else "user","content":m["content"]}
        for m in history]
    reply = say(speaker, other, msgs)
    print(f"\n=== {speaker} ===\n{reply}")
    (hist_a if i % 2 == 0 else hist_b).append({"role":"assistant","content":reply})
