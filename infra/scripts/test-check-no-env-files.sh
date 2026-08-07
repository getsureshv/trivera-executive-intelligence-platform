#!/usr/bin/env bash
#
# Tests for check-no-env-files.sh.
#
# The check this exercises was previously broken and silently passing: it used
# `grep -E '^\.env$|^\.env\.(?!example)'`, and because POSIX ERE has no negative
# lookahead, `.env.production` was never matched. A committed production
# environment file would have sailed through CI.
#
# The lesson generalises: a guard nobody has watched fail is not a guard. Each
# case below builds a throwaway git repository, commits a file, and asserts the
# check's exit status.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECK="${SCRIPT_DIR}/check-no-env-files.sh"

pass=0
fail=0

run_case() {
  local description="$1" filename="$2" expected="$3"
  local workdir
  workdir="$(mktemp -d)"

  (
    cd "$workdir"
    git init --quiet
    git config user.email "test@example.invalid"
    git config user.name "Test"
    mkdir -p nested
    printf 'PLACEHOLDER=1\n' > "$filename"
    git add -A
    git commit --quiet -m "add $filename"
  ) >/dev/null 2>&1

  local actual=0
  (cd "$workdir" && bash "$CHECK") >/dev/null 2>&1 || actual=$?

  if [ "$actual" -eq "$expected" ]; then
    printf '  ok   %-46s (exit %s)\n' "$description" "$actual"
    pass=$((pass + 1))
  else
    printf '  FAIL %-46s (expected %s, got %s)\n' "$description" "$expected" "$actual"
    fail=$((fail + 1))
  fi

  rm -rf "$workdir"
}

echo "check-no-env-files.sh"

# Must be rejected.
run_case ".env is rejected"                     ".env"                1
run_case ".env.production is rejected"          ".env.production"     1
run_case ".env.local is rejected"               ".env.local"          1
run_case ".env.staging is rejected"             ".env.staging"        1
run_case ".env.example.bak is rejected"         ".env.example.bak"    1
run_case "nested .env is rejected"              "nested/.env"         1
run_case "nested .env.production is rejected"   "nested/.env.production" 1

# Must be allowed.
run_case ".env.example is allowed"              ".env.example"        0
run_case "an unrelated file is allowed"         "README.md"           0
run_case "environment.md is allowed"            "environment.md"      0

echo
if [ "$fail" -ne 0 ]; then
  echo "FAILED: ${fail} case(s), ${pass} passed"
  exit 1
fi
echo "PASSED: ${pass} case(s)"
