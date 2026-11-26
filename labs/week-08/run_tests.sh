#!/bin/sh
# Run every test intent through validate.py: good*.yaml must be accepted, bad-*.yaml rejected.
cd "$(dirname "$0")"
fail=0
for f in tests/*.yaml; do
  python3 validate.py "$f" > /tmp/verdict.txt 2>&1
  got=$?
  case "$(basename "$f")" in
    good*) want=0 ;;
    *)     want=1 ;;
  esac
  if [ "$got" -eq "$want" ]; then result=ok; else result=WRONG; fail=1; fi
  printf '%-5s %-45s %s\n' "$result" "$(basename "$f")" "$(grep -m1 'libyang err' /tmp/verdict.txt | sed 's/libyang err : //' | cut -c1-90)"
done
[ "$fail" -eq 0 ] && echo "All tests behaved as expected." || echo "Some tests did NOT behave as expected."
exit $fail
