#!/usr/bin/env bash
# factory_gate_domain_router.sh — run the engineering-hygiene factory gate for
# the domain-router, alert if the routing table stops verifying.
#
# Mirrors msb-v3's factory_gate_daily.sh (same launchd/StartCalendarInterval
# pattern). The domain-router is a pure CLI — no server to boot — so this just
# rebuilds the registry + re-runs the unit tests through the factory and alerts
# on anything that is not a clean PASS.
#
#   run manually:  bash scripts/factory_gate_domain_router.sh
#   log:           $HOME/Library/Logs/domain-router-factory-gate.log
#
# Exit codes: 0 = gate PASS | 1 = gate FAILED/BLOCKED (alert sent)
set -uo pipefail

REPO="/Users/lordwilson/.hermes/domain-router"
FACTORY="/Users/lordwilson/.hermes/skills/engineering/engineering-hygiene-factory/scripts/run_factory.py"
PY="/opt/homebrew/Caskroom/miniforge/base/bin/python"
LOG="$HOME/Library/Logs/domain-router-factory-gate.log"
GATE="$REPO/artifacts/hygiene/factory_gate.json"

mkdir -p "$(dirname "$LOG")"
log() { echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] $*" | tee -a "$LOG"; }

log "running domain-router factory gate..."

# 0. Canary: prove the zero-spend env scrub holds BEFORE spending a full gate
# run. The self-test injects sentinel credentials and asserts no subprocess
# the gate spawns can see them (enforced via the shared _spawn choke point).
if ! "$PY" "$FACTORY" --self-test > /tmp/factory_gate_self_test.log 2>>"$LOG"; then
  log "ERROR: zero-spend self-test FAILED — scrub broken; gate not run"
  osascript -e 'display notification "Factory zero-spend self-test failed — see log" with title "domain-router-gate" sound name "Sosumi"' 2>/dev/null || true
  exit 1
fi
log "zero-spend self-test OK"

"$PY" "$FACTORY" --project "$REPO" > /tmp/factory_gate_domain_router_run.json 2>>"$LOG"
rc=$?
if [ "$rc" -ne 0 ]; then
  log "ERROR: factory crashed (exit $rc) — see /tmp/factory_gate_domain_router_run.json"
  osascript -e 'display notification "Domain-router factory crashed — see log" with title "domain-router-gate" sound name "Sosumi"' 2>/dev/null || true
  exit 1
fi

VERDICT=$(python3 -c '
import json
try:
    d = json.load(open("'"$GATE"'"))
    print(d["RELEASE_VERDICT"]["release_verdict"])
except Exception:
    print("UNKNOWN")
' 2>/dev/null)
UNKNOWNS=$(python3 -c '
import json
try:
    d = json.load(open("'"$GATE"'"))
    print(len(d["RELEASE_VERDICT"].get("unresolved_unknowns", [])))
except Exception:
    print(-1)
' 2>/dev/null)

log "gate verdict=$VERDICT unknowns=$UNKNOWNS"

if [ "$VERDICT" != "PASS" ]; then
  log "ALERT: gate is $VERDICT ($UNKNOWNS unknowns) — routing table not verified"
  osascript -e "display notification \"Domain-router gate is $VERDICT ($UNKNOWNS unknowns) — not PASS\" with title \"domain-router-gate\" sound name \"Sosumi\"" 2>/dev/null || true
  exit 1
fi

log "gate PASS — routing table + unit suite verified"
exit 0
