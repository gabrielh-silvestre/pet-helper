#!/bin/sh
# Unit state and hook guards of the macro pipeline (/pre-code, /deliver, /pr-panel).
# State commands exit 0 or 1; guards read the hook payload on stdin and exit 0 (allow) or 2 (deny).
set -u

# shortcut: one unit in flight per clone; key the file by slug if units ever run in parallel
state_file() {
  d=$(git -C "$1" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || return 1
  printf '%s/pet-helper-flow.json\n' "$d"
}

fail() { printf '%s\n' "$*" >&2; exit 1; }
deny() { printf '%s\n' "$*" >&2; exit 2; }
field() { jq -r --arg k "$1" '.[$k]' "$f"; }
payload_get() { printf '%s' "$payload" | jq -r "$1"; }

cmd_start() {
  [ -n "${1:-}" ] || fail "usage: flow.sh start <slug>"
  if [ -f "$f" ]; then
    [ "$(field slug)" = "$1" ] && return 0
    fail "unit $(field slug) is in flight; finish it or run flow.sh clear"
  fi
  jq -n --arg s "$1" '{slug: $s, phase: "interview", approved: false, worktree: "", owner: ""}' >"$f"
}

cmd_set() {
  [ -f "$f" ] || fail "no unit in flight; run flow.sh start <slug>"
  case ${1:-} in phase | worktree | owner) ;; *) fail "unknown key: ${1:-}" ;; esac
  jq --arg k "$1" --arg v "${2:-}" '.[$k] = $v' "$f" >"$f.tmp" && mv "$f.tmp" "$f"
}

cmd_get() {
  [ -f "$f" ] || fail "no unit in flight"
  if [ -n "${1:-}" ]; then field "$1"; else cat "$f"; fi
}

# R6: the owner's approval is only recorded once the pre-code artifacts exist.
cmd_approve() {
  [ -f "$f" ] || fail "no unit in flight"
  slug=$(field slug)
  root=$(dirname "$(dirname "$f")")
  missing=""
  for doc in spec plan architecture; do
    [ -f "$root/docs/specs/$slug/$doc.md" ] || missing="${missing}docs/specs/$slug/$doc.md
"
  done
  grep -q '^## Ferramentas existentes' "$root/docs/specs/$slug/plan.md" 2>/dev/null ||
    missing="${missing}heading '## Ferramentas existentes' in docs/specs/$slug/plan.md
"
  for dir in specs plans; do
    ls "$root/.omc/$dir" 2>/dev/null | grep -qF -- "$slug" || missing="${missing}a file named after $slug in .omc/$dir/
"
  done
  if [ -n "$missing" ]; then
    printf '%s' "$missing" | sed 's/^/missing: /' >&2
    exit 1
  fi
  jq '.approved = true' "$f" >"$f.tmp" && mv "$f.tmp" "$f"
}

# R1: hexlog only takes the phase the macro declared, or an impasse.
guard_phase() {
  bad=$(printf '%s' "$payload" | jq -r --arg p "$(field phase)" \
    '[.tool_input.records[]?.type | select(. != $p and . != "impasse")] | first // empty')
  [ -z "$bad" ] || deny "hexlog phase is $(field phase), set by the macro with flow.sh set phase; got $bad"
}

# R2: after the hand-off only the /pr-panel session writes the worktree.
guard_owner() {
  printf '%s' "$payload" | jq -e --slurpfile s "$f" '
    $s[0] as $st | $st.worktree as $w
    | if $st.owner == "" or $w == "" or .session_id == $st.owner then false
      else ([.tool_input.file_path, .tool_input.notebook_path, .cwd] | map(strings)
            | any(. == $w or startswith($w + "/")))
           or ((.tool_input.command // "") | tostring | contains($w))
      end' >/dev/null &&
    deny "worktree $(field worktree) belongs to the /pr-panel session until it ends"
  return 0
}

# R4: the verdict goes on the pushed head, pinned to that SHA.
guard_verdict() {
  method=$(payload_get '.tool_input.method // empty')
  case $method in create | submit_pending) ;; *) return 0 ;; esac
  w=$(field worktree)
  [ -n "$w" ] || return 0
  head=$(git -C "$w" rev-parse HEAD 2>/dev/null)
  up=$(git -C "$w" rev-parse '@{u}' 2>/dev/null)
  [ -n "$head" ] && [ "$head" = "$up" ] || deny "push $w before posting the verdict; HEAD is not on its upstream"
  [ "$method" = submit_pending ] || [ "$(payload_get '.tool_input.commitID // empty')" = "$head" ] ||
    deny "pin the verdict with commitID $head, the pushed head"
}

# R5: ralph and team start only after the owner approved the unit.
guard_approval() {
  skill=$(payload_get '.tool_input.skill // empty')
  case ${skill##*:} in ralph | team) ;; *) return 0 ;; esac
  [ "$(field approved)" = true ] || deny "ralph and team run only after the owner approves the unit at the end of /pre-code"
}

# R3: the ralph PRD keeps the fields OMC reads and silent feedback commands.
guard_prd() {
  prd=$(payload_get '.tool_input.file_path // empty')
  case $prd in */.omc/*prd.json) ;; *) return 0 ;; esac
  jq -e '(.userStories | type == "array" and length > 0)
    and (.feedbackCommands | type == "array" and length > 0
         and all(.[]; (if type == "string" then . else .command end) | tostring | contains("out=$(")))' \
    "$prd" >/dev/null 2>&1 ||
    deny "$prd needs a non-empty userStories array and feedbackCommands silent on success; see .claude/skills/deliver/prd-template.json"
}

case ${1:-} in
start | set | get | approve | clear)
  f=$(state_file .) || fail "not inside a git repository"
  cmd=$1
  shift
  case $cmd in
  clear) rm -f "$f" ;;
  *) "cmd_$cmd" "$@" ;;
  esac
  ;;
guard)
  payload=$(cat)
  name=${2:-}
  [ "$name" = prd ] && { guard_prd; exit 0; }
  dir=$(payload_get '.cwd // empty')
  f=$(state_file "${dir:-.}") || f=$(state_file "${CLAUDE_PROJECT_DIR:-.}") || exit 0
  [ -f "$f" ] || exit 0
  case $name in
  phase | owner | verdict | approval) "guard_$name" ;;
  *) fail "unknown guard: $name" ;;
  esac
  ;;
*) fail "usage: flow.sh start|set|get|approve|clear|guard <name>" ;;
esac
exit 0
