"""Compare live operation counts against a recorded baseline JSON.

Usage:
    python benchmarks/misnest/misnest_work.py --json /tmp/now.json
    python benchmarks/misnest/compare_work.py benchmarks/misnest/baseline_n8.json /tmp/now.json
"""
from __future__ import print_function

import json
import sys

from html5lib.treebuilders import base


def main(argv):
    with open(argv[1], encoding="utf-8") as handle:
        before = json.load(handle)
    with open(argv[2], encoding="utf-8") as handle:
        after = json.load(handle)

    counters = base.WorkCounter.__slots__
    families = sorted(before)
    overall_ok = True
    for family in families:
        builder = sorted(k for k in before[family] if k != "input_bytes")[0]
        wb = before[family][builder]["work"]
        wa = after[family][builder]["work"]
        tree_same = (before[family][builder]["canonical"] ==
                     after[family][builder]["canonical"])
        errors_same = (before[family][builder]["errors"] ==
                       after[family][builder]["errors"])
        print("%s (%s): tree=%s errors=%s" %
              (family, builder, tree_same, errors_same))
        overall_ok &= tree_same and errors_same
        for counter in counters:
            old = wb.get(counter, 0)
            new = wa.get(counter, 0)
            if old != new:
                pct = "" if old == 0 else "  %+.1f%%" % (
                    100.0 * (new - old) / old)
                print("  %-16s %6d -> %6d%s" % (counter, old, new, pct))
        tb = sum(wb.values())
        ta = sum(wa.values())
        print("  %-16s %6d -> %6d  %+.1f%%" %
              ("TOTAL", tb, ta, 100.0 * (ta - tb) / tb))
    print("all compatible:", overall_ok)
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
