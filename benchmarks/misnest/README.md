# Mis-nested formatting: operation-count benchmark

Short, deterministic workloads for measuring how much linear scan work the
tree construction algorithm performs on malformed inputs. Nothing here uses
wall-clock time as evidence: every counter is incremented at the actual
stack-of-open-elements / list-of-active-formatting-elements scan it
describes, via `html5lib.treebuilders.base.WorkCounter` (attached to a tree
builder only when requested; default parsing has no bookkeeping).

## Input families (`misnest_work.py`)

- `crossed(n)`: interleaved `<b>/<i>` tags closed out of order — repeated
  adoption agency invocations.
- `long_stack(n)`: `n` unclosed `<div>`s followed by `<b>x</b>` repeats —
  `reconstructActiveFormattingElements` membership probes against a growing
  open-elements stack.
- `table(n)`: long `<div>` stack followed by `<table><b>x</b></table>`
  repeats — foster parent positioning and table error recovery walking past
  every open element.

All families use only short tags; the default `n=8` run finishes in well
under a second.

## Counters

| counter | what it counts |
| --- | --- |
| `scope_probes` | element comparisons inside `elementInScope` scans |
| `stack_contains` | comparisons for open-elements membership tests |
| `stack_index` / `stack_remove` | comparisons for index lookup / removal on the stack |
| `afe_probes` | entries walked in the active formatting list (searches, Noah's Ark) |
| `afe_index` / `afe_contains` / `afe_remove` | index/membership/removal work on the active formatting list |
| `foster_probes` | stack entries scanned to find the foster-parent table |
| `agency_probes` | stack entries scanned for the adoption agency furthest block |
| `stack_snapshot` | entries included in a one-shot identity snapshot used by reconstruct |

## Usage

```
python benchmarks/misnest/misnest_work.py                 # table + tree/error equivalence
python benchmarks/misnest/misnest_work.py --n 16
python benchmarks/misnest/misnest_work.py --strict
python benchmarks/misnest/compare_work.py \
    benchmarks/misnest/baseline_n8.json /tmp/now.json    # before/after delta
python benchmarks/misnest/differential_check.py          # baseline package vs live package
python -m pytest benchmarks/misnest/                      # fast checks (short inputs)
```

`differential_check.py` loads an independent pre-optimization package copy
(default `/tmp/misnest_baseline`) under an alias and compares the logical
tree (treewalker events, attribute order normalised), the full parse-error
sequence and strict-mode exceptions across the `dom` and `etree` builders,
both namespace settings, strict/non-strict mode, document and fragment
(`div`, `table`, `select`, `p`) entry points.

## Optimizations applied (all behavior-preserving)

1. Adoption agency step 4 performs one stack position lookup plus one scope
   check instead of two membership tests and two scope checks; the position
   is reused as the furthest-block anchor, and furthest-block bookkeeping in
   steps 9/15 is kept in sync with a local index instead of rescanning.
2. Open-elements membership/index/removal searches run from the top of the
   stack; the looked-up elements are always currently open, so results are
   identical while the typical scan length collapses.
3. `reconstructActiveFormattingElements` takes one identity snapshot of the
   stack when more than one active formatting entry may need checking,
   instead of a full stack walk per entry.
4. The Noah's Ark clause scan in `ActiveFormattingElements.append` is no
   longer duplicated: `addFormattingElement` already performs that exact
   scan before appending, so it calls the new `appendChecked`. The public
   `append` still enforces the clause for any other caller.
5. The duplicate active-formatting index lookup inside adoption agency
   step 9 is merged into one.

The spec-mandated bounds are untouched: the adoption agency outer loop
still stops after 8 iterations, its inner loop after 3, and every
`elementInScope` / foster / furthest-block scan required by the algorithm
still runs (the remaining quadratic term in the `table` family is exactly
the required `<p>` scope check on each `<table>` start tag). No nodes are
dropped early and no supported nesting depth is reduced.
