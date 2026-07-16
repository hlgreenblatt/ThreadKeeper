"""ThreadKeeper demo: bulk document summarization on the LOCAL granite worker.
Runs inside the agent_10 container. Usage: python3 demo_summarize.py [passes] [doc]"""
import sys, os, re
sys.path.insert(0, '/PeTTa/repos/OmegaClaw-Core/src')
sys.path.insert(0, '/PeTTa/repos/OmegaClaw-Core')
os.environ.setdefault('OMEGACLAW_SUBAGENT_PERSONA_DIR',
                      '/PeTTa/repos/OmegaClaw-Core/memory/personas-subagent')
os.environ.setdefault('OLLAMA_API_KEY', 'ollama')
import subagent

passes = int(sys.argv[1]) if len(sys.argv) > 1 else 1
doc_path = sys.argv[2] if len(sys.argv) > 2 else \
    '/PeTTa/repos/OmegaClaw-Core/media/demo-doc.txt'

text = open(doc_path, encoding='utf-8').read()
chunks = [c.strip() for c in re.split(r'\nSection \d+\.', text) if c.strip()]
print(f">> document: {len(text):,} chars, {len(chunks)} chunks, {passes} pass(es)\n",
      flush=True)

done = 0
for p in range(passes):
    for i, ch in enumerate(chunks, 1):
        goal = "Summarize the following passage in one concise sentence: " + ch
        r = subagent.dispatch(goal, 'read-file', 'researcher', 1)
        done += 1
        snip = (r or '').replace('\n', ' ')[:70]
        print(f"   [pass {p+1} chunk {i:>2}/{len(chunks)}]  worker -> {snip}", flush=True)
print(f"\n>> {done} local-worker summarizations complete. Cost on local granite: $0.",
      flush=True)
