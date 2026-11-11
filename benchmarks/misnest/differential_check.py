"""Differential check against a pristine pre-optimization html5lib copy.

Generates short, deterministic, deliberately mis-nested inputs and compares
the logical tree, parse-error sequence and strict-mode outcome between the
live tree builder and a reference package (default: /tmp/misnest_baseline).
The default run uses only short inputs and finishes within a few seconds.
"""
from __future__ import print_function

import argparse
import importlib.util
import os
import random
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[0:0] = [REPO_ROOT]


def load_package(root, alias):
    """Load an independent copy of the html5lib package under an alias."""
    init_path = os.path.join(root, "html5lib", "__init__.py")
    spec = importlib.util.spec_from_file_location(
        alias, init_path,
        submodule_search_locations=[os.path.dirname(init_path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module

TOKENS = [
    "<b>", "</b>", "<i>", "</i>", "<u>", "</u>", "<a>", "</a>",
    "<b>", "<i x='1'>", "<b x='2'>", "<b>", "<i>",
    "<p>", "</p>", "<div>", "</div>", "<button>", "</button>",
    "<table>", "</table>", "<tr>", "</tr>", "<td>", "</td>",
    "<table>", "<tbody>", "<caption>", "</caption>",
    "<object>", "</object>", "<marquee>", "</marquee>",
    "<select>", "</select>", "<option>",
    "x", " ", "<br>", "<img>", "<font>", "</font>", "<nobr>", "</nobr>",
    "<h1>", "</h1>", "<li>", "<ul>", "</ul>", "<form>", "</form>",
]


def make_inputs(seed, count, max_len):
    rng = random.Random(seed)
    inputs = []
    for _ in range(count):
        length = rng.randint(1, max_len)
        inputs.append("".join(rng.choice(TOKENS) for _ in range(length)))
    return inputs


def canonical(walker_mod, parser, builder):
    doc = parser.tree.getDocument()
    events = []
    for event in walker_mod.getTreeWalker(builder)(doc):
        event_type = event["type"]
        if event_type in ("StartTag", "EndTag"):
            events.append((event_type, event["namespace"], event["name"],
                           tuple(sorted(event.get("data", {}).items()))))
        elif event_type == "Characters":
            events.append((event_type, event["data"]))
    return events


def parse_once(parser_mod, walker_mod, markup, builder, strict, namespace,
               container=None):
    parser = parser_mod.HTMLParser(
        tree=builder, strict=strict, namespaceHTMLElements=namespace)
    raised = None
    try:
        if container is None:
            parser.parse(markup)
        else:
            parser.parseFragment(markup, container=container)
    except Exception as exc:  # strict mode may raise ParseError
        raised = (type(exc).__name__, str(exc))
    return (canonical(walker_mod, parser, builder),
            [(code, datavars) for _pos, code, datavars in parser.errors],
            raised)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="/tmp/misnest_baseline",
                        help="directory containing the reference html5lib package")
    parser.add_argument("--count", type=int, default=400)
    parser.add_argument("--max-len", type=int, default=24)
    parser.add_argument("--seed", type=int, default=20261111)
    args = parser.parse_args(argv)

    inputs = make_inputs(args.seed, args.count, args.max_len)
    configs = [(builder, strict, namespace, container)
               for builder in ("dom", "etree")
               for strict in (False, True)
               for namespace in (True, False)
               for container in (None, "div", "table", "select", "p")]

    import importlib
    ref_pkg = load_package(args.baseline, "_ref_html5lib")
    ref_parser = importlib.import_module("_ref_html5lib.html5parser")
    ref_walkers = importlib.import_module("_ref_html5lib.treewalkers")
    live_parser = importlib.import_module("html5lib.html5parser")
    live_walkers = importlib.import_module("html5lib.treewalkers")
    assert ref_pkg is not live_parser.__package__

    failures = 0
    for markup in inputs:
        for builder, strict, namespace, container in configs:
            ref = parse_once(ref_parser, ref_walkers, markup, builder,
                             strict, namespace, container)
            live = parse_once(live_parser, live_walkers, markup, builder,
                              strict, namespace, container)
            if ref != live:
                failures += 1
                print("MISMATCH builder=%s strict=%s ns=%s container=%s markup=%r" %
                      (builder, strict, namespace, container, markup))
                print("  ref :", ref)
                print("  live:", live)
                if failures >= 5:
                    print("stopping after 5 mismatches")
                    return 1
    print("checked %d inputs x %d configurations: %s failures" %
          (len(inputs), len(configs), failures))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
