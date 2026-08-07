#!/usr/bin/env bash
#
# Reject committed environment files. Only `.env.example` may be tracked.
#
# The original CI check used `grep -E '^\.env$|^\.env\.(?!example)'`. Negative
# lookahead is a PCRE construct that POSIX ERE does not support, so `grep -E`
# treated `(?!example)` as literal text and the second alternative matched
# nothing. `.env.production` passed silently — exactly the file the rule exists
# to catch.
#
# This implementation uses no regex: list tracked files, keep those whose
# basename starts with `.env`, and allow precisely one name.
set -euo pipefail

ALLOWED=".env.example"
offenders=""

while IFS= read -r path; do
  base="${path##*/}"
  case "$base" in
    .env*)
      if [ "$base" != "$ALLOWED" ]; then
        offenders="${offenders}${path}"$'\n'
      fi
      ;;
  esac
done < <(git ls-files)

if [ -n "$offenders" ]; then
  echo "::error::Environment files are committed. Only ${ALLOWED} may be tracked (ADR-015)."
  printf '%s' "$offenders" | sed 's/^/  /'
  exit 1
fi

echo "No committed environment files besides ${ALLOWED}."
