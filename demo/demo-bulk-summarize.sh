#!/usr/bin/env bash
# ThreadKeeper demo: bulk document summarization on the LOCAL granite worker.
# High token volume, trivial reasoning — the "Local Worker Loop" tile climbs
# live while cost stays $0. Watch the WebUI mesh dashboard while this runs.
#
#   sh ~/agents/agent_10/demo-bulk-summarize.sh        # 1 pass over the doc
#   sh ~/agents/agent_10/demo-bulk-summarize.sh 3      # 3 passes (more volume)
set -u
PASSES="${1:-1}"
echo ">> ThreadKeeper bulk-summarization demo — local granite worker"
echo ">> Watch the WebUI mesh: the Local Worker Loop tile + SAVED% climb live."
echo
docker exec agent_10 python3 \
  /PeTTa/repos/OmegaClaw-Core/src/demo_summarize.py "$PASSES"
echo
echo ">> Done. The Local Worker tile reflects the bulk run (all local, zero cost)."
