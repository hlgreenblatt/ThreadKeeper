# OmegaClaw / ThreadKeeper — Memory Persistence, Disaster Recovery & Migration

> Practical reference for: *"If I run a local OmegaClaw agent, let it learn
> and train, can I back it up and migrate its learned memory — ideally just
> the data, not the whole container — to another host or provider?"*
>
> Short answer: **yes, the learned state is two on-disk files and is fully
> portable — but there are three traps that will silently lose or corrupt
> memory if you don't set up persistence correctly *before* training, and one
> hard rule about embedding providers that governs whether a migration
> succeeds at all.**
>
> Findings verified against OmegaClaw-Core at upstream `main` (#175) +
> `patham9/petta_lib_chromadb@master`, 2026-06-18.

---

## 1. What actually holds an agent's state

OmegaClaw keeps **two memory stores**, both plain files on disk. There is no
hidden server-side or in-RAM-only state that matters for recovery.

| Store | Role | Path (default) | Format | Written by |
|---|---|---|---|---|
| **Episodic history** | Raw, timestamped turn log (`HUMAN_MESSAGE:` / response lines). The agent's "what happened" record. | `memory/history.metta` | plain UTF-8 text (append-only) | `src/memory.metta` → `appendToHistory` |
| **Semantic memory** | The *learned* memories — what `remember` stores and `query` retrieves. Vector store. | `chroma_db/chroma.sqlite3` (+ index files) | ChromaDB SQLite DB | `petta_lib_chromadb` (`remember`/`query`) |

The container itself is **reproducible from the image**. The *only*
irreplaceable artifacts after first boot are these two paths. So:

> **Migrating "the learned agent" = copying `memory/history.metta` and the
> `chroma_db/` directory. You do not need to migrate the container.**

The semantic store details that matter for migration (from
`lib_chromadb.py`):
- `chromadb.PersistentClient(path="./chroma_db")` — local, file-backed.
- Collection name: `"memories"`, `embedding_function=None` — i.e.
  **OmegaClaw computes embeddings itself and stores them precomputed.**
  Chroma never calls an embedder; it just stores the vectors it's handed.

---

## 2. The three disaster-recovery traps

### Trap 1 — No volume by default: memory dies with the container ⚠️

The Dockerfile creates `memory/chroma_db` *inside the image layer* and
**declares no `VOLUME`**. Nothing is host-mounted unless you do it explicitly.

```
# Dockerfile (paraphrased)
ENV MEMORY_DIR=/PeTTa/repos/OmegaClaw-Core/memory
RUN mkdir -p ${MEMORY_DIR}/chroma_db && ln -s ${MEMORY_DIR}/chroma_db ./chroma_db
# ...no VOLUME, no mount...
```

**Consequence:** if an agent trains for a week and the container is later
`docker rm`'d (or rebuilt, or the host is reimaged) **without a host
bind-mount, all learned memory is lost.**

**Fix (do this on FIRST boot, before any training):** bind-mount the memory
directory to the host.

```bash
docker run -d --name nexi \
  -v /srv/nexi/memory:/PeTTa/repos/OmegaClaw-Core/memory \
  <image> <args...>
# chroma_db lives under memory/ via the symlink, so this one mount covers
# BOTH stores. Confirm chroma_db resolves inside the mount, not beside it.
```

> Note the in-container owner is `nobody` (UID 65534). Match host dir perms
> so the mounted dir is writable by that UID, or the agent can't persist.

### Trap 2 — History and vectors can drift if snapshotted live 🔄

`history.metta` (episodic) and `chroma_db` (semantic) are written by two
independent code paths at different moments. A backup taken while the agent
is mid-turn can capture a vector whose source line isn't yet in history (or
the reverse).

**Fix:** snapshot **both together while the agent is quiesced** (idle between
turns, or briefly stopped). For a running-system backup, stop the container,
copy both, restart — they're small. Treat the pair as one atomic unit.

### Trap 3 — `import-kb` seeding is provider-keyed and one-directional

On boot, `entrypoint.sh` optionally runs `scripts/import_knowledge.sh`, which
seeds `knowledge-priors/*.md` into the vector store. It writes a **sentinel
keyed by embedding provider**:

- local embeddings → `chroma_db/.import-kb.local.done`
- OpenAI embeddings → `chroma_db/.import-kb.openai.done`

This is a tell: **upstream already treats local-embedded and cloud-embedded
stores as different things** — but provides only seeding sentinels, **no
converter between them.** See Trap 4 for why that matters.

---

## 3. Trap 4 — The embedding-provider rule (the migration killer) 🔴

This is the single most important fact for any local→cloud (or
cross-provider) move, and the thing most likely to be under-thought.

**OmegaClaw stores precomputed embedding vectors. A vector is only meaningful
to the *exact same embedding model* that produced it.**

| Provider | Model | Dimension |
|---|---|---|
| `Local` (default) | `intfloat/e5-large-v2` | **1024** |
| `OpenAI` | OpenAI embedding model | **1536** (different model *and* size) |

Vectors from one are **not interchangeable** with the other:
- Different dimensions → a cross-provider query is, at best, a hard error;
- Even at equal dimensions, different models place concepts in different
  vector spaces → silently wrong recall (the worst failure: it "works" but
  retrieves nonsense).

**There is no in-place re-embedding tool in the codebase today.** So:

### Migration rules that actually work

1. **Same-embedder migration (recommended, lossless).** Keep the embedding
   provider identical across the move. Copy `history.metta` + `chroma_db/`
   to the new host and ensure the new host runs the **same embedder**.
   - This is where ThreadKeeper's thesis helps: if your embedder is a
     **local model you control** (not a cloud API), it travels with the
     agent to *any* host. The vectors stay valid because the model is the
     same binary. Local-first embedding = portable memory.

2. **Cross-embedder migration (lossy, requires a rebuild).** If you must
   change embedder (e.g. local → OpenAI), the vectors cannot be converted.
   The recoverable path is to **replay the episodic history through the new
   embedder**: `history.metta` is plain text and contains the source
   content, so a re-embedding pass can rebuild a fresh `chroma_db` under the
   new model. (This is a small script OmegaClaw doesn't ship yet — a clean
   contribution opportunity, and a natural ThreadKeeper add-on.)

3. **Never** copy a `chroma_db` from a local-embedded agent onto a host
   configured for OpenAI embeddings (or vice versa) and expect recall to
   work. The sentinel files won't save you — they only gate *seeding*.

---

## 4. The recommended setup for Esther's Nexi

Goal: build Nexi local on your hardware, let Esther train it via Telegram,
and be able to migrate the **learned memory** (not just the container) later.

1. **Pin the embedding provider to a LOCAL model from boot one** and never
   change it for this agent's lifetime. This makes the memory portable to any
   host without a rebuild, and aligns with ThreadKeeper's local-inference
   thesis. (Set `embeddingprovider=Local`; it's the default.)
2. **Bind-mount `memory/` to the host on first run** (Trap 1). This is the
   single most important step — do it *before* training starts.
3. **Back up `memory/` on a schedule** while quiesced (Trap 2). It's small;
   a nightly `tar` of the mounted dir is enough. This is your real DR.
4. **Migration = ship the backup.** To move Nexi to another host/provider:
   stand up the same image, restore `memory/` into the mount, keep the same
   local embedder. Done — learned memory intact, no container image transfer
   needed beyond the (reproducible) base image.
5. **If a cloud provider must host it later:** run the same local embedder
   *on the cloud box* (ThreadKeeper makes this a config line), OR plan a
   one-time re-embedding rebuild from `history.metta`.

### A note on the broader point Larry raised

Cloud-hosted OmegaClaw setups today typically bind to a **single API
provider**, and their persistence/migration story is thin (no volume by
default, no cross-embedder memory migration). ThreadKeeper's architecture —
local control/worker loops, configurable per-node providers, local embeddings
you own — is precisely what makes an agent's *learned memory* portable and
provider-independent. **Memory portability is a concrete, demonstrable
benefit of the ThreadKeeper approach, not just a cost argument** — and it's
directly an ethics/governance concern (data ownership, auditability,
no-lock-in), which is squarely in NexiClaw's lane.

---

## 5. Quick reference — backup & restore

```bash
# --- one-time: run with a host mount so memory survives the container ---
docker run -d --name nexi \
  -v /srv/nexi/memory:/PeTTa/repos/OmegaClaw-Core/memory \
  <image> commchannel=telegram embeddingprovider=Local <...>

# --- backup (quiesce first for an atomic snapshot) ---
docker stop nexi
tar czf nexi-memory-$(date -Iseconds).tgz -C /srv/nexi memory
docker start nexi
# the tarball contains BOTH history.metta and chroma_db/ — the whole brain.

# --- restore / migrate to a new host (SAME local embedder) ---
mkdir -p /srv/nexi/memory && tar xzf nexi-memory-*.tgz -C /srv/nexi
docker run -d --name nexi \
  -v /srv/nexi/memory:/PeTTa/repos/OmegaClaw-Core/memory \
  <same-image> embeddingprovider=Local <...>
```

---

## 6. Gaps worth raising upstream (and candidate ThreadKeeper contributions)

1. **No `VOLUME` / documented persistence contract** — easy to lose memory by
   accident. A `VOLUME` declaration + a one-paragraph DR doc would prevent it.
2. **No cross-embedder memory migration tool** — `history.metta` is replayable;
   a `rebuild-memory --from-history --embedder X` script would close the
   local↔cloud gap. Natural ThreadKeeper add-on.
3. **No atomic snapshot helper** — a `omegaclaw backup` / `restore` that
   quiesces, copies both stores, and checksums them.

These three are small, high-value, and directly serve both the ThreadKeeper
(portability/governance) and NexiClaw (data-ownership ethics) stories.
