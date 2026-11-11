"""Operation-count benchmark for short mis-nested formatting inputs.

This does *not* rely on wall-clock time. A WorkCounter is attached to the
tree builder and every counter is incremented at the actual stack of open
elements / list of active formatting elements scan it describes.

Run::

    python benchmarks/misnest/misnest_work.py            # short default inputs
    python benchmarks/misnest/misnest_work.py --n 32     # vary family depth
    python benchmarks/misnest/misnest_work.py --json out.json
"""
from __future__ import print_function

import argparse
import io
import json
import os
import sys

sys.path[0:0] = [os.path.join(os.path.dirname(__file__), "..", "..")]

from html5lib import html5parser, treewalkers  # noqa: E402
from html5lib.treebuilders import base  # noqa: E402


def canonical_tree(builder, document):
    """Element-level logical tree, independent of the tree builder used.

    Doctype/comment representation differs between builders, so only start
    tags, end tags and characters are compared; attribute order is
    normalised through sorted tuples.
    """
    walker = treewalkers.getTreeWalker(builder)(document)
    events = []
    for event in walker:
        event_type = event["type"]
        if event_type in ("StartTag", "EndTag"):
            events.append((event_type, event["namespace"], event["name"],
                           tuple(sorted(event.get("data", {}).items()))))
        elif event_type == "Characters":
            events.append((event_type, event["data"]))
    return events


def family_crossed(n):
    """Interleaved formatting tags closed in reverse order.

    <b><i><b><i>...</i></b></i></b> style inputs force the adoption agency
    algorithm repeatedly (formatting element is not the current node).
    """
    fmt = ("b", "i")
    opens = "".join("<%s>" % fmt[k % 2] for k in range(n))
    closes = "".join("</%s>" % fmt[(n - 1 - k) % 2] for k in range(n))
    return "<!doctype html><body>" + opens + closes


def family_long_stack(n):
    """A long open-elements stack interleaved with active formatting rebuilds.

    Every <b> starts reconstructActiveFormattingElements against a stack that
    keeps growing, so each membership probe walks further.
    """
    head = "".join("<div>" for _ in range(n))
    middle = "".join("<b>x</b>" for _ in range(n))
    return "<!doctype html><body>" + head + middle


def family_table(n):
    """Table foster parenting on top of a long open-elements stack.

    A run of unclosed <div>s builds a long stack; each following
    <table><b>x</b></table> then triggers foster parent positioning and
    adoption agency work that has to walk past every div.
    """
    head = "".join("<div>" for _ in range(n))
    tables = "<table><b>x</b></table>" * n
    return "<!doctype html><body>" + head + tables


FAMILIES = {
    "crossed": family_crossed,
    "long_stack": family_long_stack,
    "table": family_table,
}


def run_one(markup, treebuilder="etree", strict=False):
    parser = html5parser.HTMLParser(tree=treebuilder, strict=strict)
    parser.tree.work = base.WorkCounter()
    if strict:
        # strict mode aborts on the first parse error; collect what happens
        # up to that point and record the raised error.
        try:
            document = parser.parse(markup)
            raised = None
        except Exception as exc:  # pragma: no cover - exercised in strict mode
            document = None
            raised = type(exc).__name__ + ": " + str(exc)
    else:
        document = parser.parse(markup)
        raised = None
    return {
        "work": parser.tree.work.as_dict(),
        "errors": [(code, datavars) for _pos, code, datavars in parser.errors],
        "tree": parser.tree.testSerializer(parser.tree.getDocument()),
        "canonical": canonical_tree(treebuilder, parser.tree.getDocument()),
        "raised": raised,
    }


def measure(n, builders):
    results = {}
    for family_name, factory in FAMILIES.items():
        markup = factory(n)
        results[family_name] = {"input_bytes": len(markup.encode("utf-8"))}
        for builder in builders:
            results[family_name][builder] = run_one(markup, treebuilder=builder)
    return results


COUNTER_ORDER = base.WorkCounter.__slots__


def print_table(results, n):
    print("n = %d; counters are element-level comparison counts, not time\n" % n)
    header = ("family", "builder") + tuple(COUNTER_ORDER) + ("TOTAL", "errors")
    rows = []
    for family_name, family in results.items():
        for builder, outcome in family.items():
            if builder == "input_bytes":
                continue
            work = outcome["work"]
            rows.append((family_name, builder) +
                        tuple(work[name] for name in COUNTER_ORDER) +
                        (sum(work.values()), len(outcome["errors"])))
    widths = [max(len(str(row[i])) for row in [header] + rows)
              for i in range(len(header))]
    fmt = "  ".join("%-" + str(w) + "s" for w in widths)
    print(fmt % header)
    for row in rows:
        print(fmt % tuple(str(x) for x in row))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=8,
                        help="depth/repeat count for the input families (default 8)")
    parser.add_argument("--builder", action="append", choices=("dom", "etree"),
                        help="tree builder to measure (default: both)")
    parser.add_argument("--json", dest="json_path", default=None,
                        help="also write full results (including trees) as JSON")
    parser.add_argument("--strict", action="store_true",
                        help="also exercise strict mode on one family")
    args = parser.parse_args(argv)
    builders = args.builder or ["dom", "etree"]

    results = measure(args.n, builders)
    print_table(results, args.n)

    if args.strict:
        markup = family_table(min(args.n, 4))
        strict_outcome = run_one(markup, treebuilder=builders[0], strict=True)
        print("\nstrict mode raised: %s" % strict_outcome["raised"])

    # Tree/error equivalence across builders.
    print("\nlogical tree / error-sequence equivalence across builders:")
    ok = True
    for family_name, family in results.items():
        ref_builder = builders[0]
        ref = family[ref_builder]
        for builder in builders[1:]:
            other = family[builder]
            same_tree = ref["canonical"] == other["canonical"]
            same_errors = ref["errors"] == other["errors"]
            ok &= same_tree and same_errors
            print("  %-11s %s vs %s: tree=%s errors=%s" %
                  (family_name, ref_builder, builder, same_tree, same_errors))
    print("all equivalent: %s" % ok)

    if args.json_path:
        with io.open(args.json_path, "w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=2, sort_keys=True)
        print("\nwrote %s" % args.json_path)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
