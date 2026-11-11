"""Short, fast checks for the mis-nested formatting work accounting.

These tests never rely on wall-clock time: they assert operation counters
and exact logical-tree / parse-error compatibility. The default inputs are
small so the whole module completes in well under a second.
"""
from __future__ import absolute_import, unicode_literals

import json
import os

import pytest

from html5lib import html5parser, treewalkers
from html5lib.treebuilders import base

from misnest_work import (FAMILIES, canonical_tree, family_crossed,
                          family_long_stack, family_table, run_one)

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.parametrize("family", sorted(FAMILIES))
@pytest.mark.parametrize("builder", ["dom", "etree"])
def test_counters_are_deterministic_and_bounded(family, builder):
    markup = FAMILIES[family](6)
    first = run_one(markup, treebuilder=builder)["work"]
    second = run_one(markup, treebuilder=builder)["work"]
    assert first == second
    # Counters must always be present and non-negative; every count
    # describes real linear scan work.
    assert all(value >= 0 for value in first.values())


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_builders_agree_on_tree_and_errors(family):
    markup = FAMILIES[family](6)
    dom = run_one(markup, treebuilder="dom")
    etree = run_one(markup, treebuilder="etree")
    assert dom["canonical"] == etree["canonical"]
    assert dom["errors"] == etree["errors"]


def test_work_counter_disabled_by_default():
    parser = html5parser.HTMLParser(tree="etree")
    assert parser.tree.work is None
    parser.parse(family_crossed(4))


def _from_json(value):
    # JSON turns tuples (including nested attribute tuples) into lists.
    if isinstance(value, list):
        return tuple(_from_json(item) for item in value)
    return value


def test_optimization_does_not_increase_recorded_baseline():
    baseline_path = os.path.join(HERE, "baseline_n8.json")
    with open(baseline_path, encoding="utf-8") as handle:
        baseline = json.load(handle)
    for family, factory in (("crossed", family_crossed),
                            ("long_stack", family_long_stack),
                            ("table", family_table)):
        live = run_one(factory(8), treebuilder="dom")
        recorded = baseline[family]["dom"]
        assert live["canonical"] == [
            _from_json(event) for event in recorded["canonical"]]
        assert live["errors"] == [
            tuple(error) for error in recorded["errors"]]
        for counter, old_value in recorded["work"].items():
            assert live["work"].get(counter, 0) <= old_value, (family, counter)


def test_strict_mode_error_is_first_recorded_error():
    markup = family_table(2)
    parser = html5parser.HTMLParser(tree="etree", strict=True)
    with pytest.raises(html5parser.ParseError):
        parser.parse(markup)
    assert len(parser.errors) == 1


def test_reconstruct_snapshot_path_stays_compatible():
    # This input forces reconstruction across several formatting entries.
    markup = "<p><b><i>foo</p>bar"
    reference = run_one(markup, treebuilder="etree")
    assert reference["work"]["stack_snapshot"] > 0
    assert reference["work"]["stack_contains"] > 0
