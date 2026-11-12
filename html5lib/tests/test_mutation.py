from __future__ import absolute_import, division, unicode_literals

import os

import pytest

import xml.etree.ElementTree as ElementTree

from .support import TestData, test_dir
from html5lib import HTMLParser
from html5lib import mutation
from html5lib.treewalkers import getTreeWalker
from html5lib.treebuilders import getTreeBuilder

_etree_builder = getTreeBuilder("etree", ElementTree, fullTree=True)
_dom_builder = getTreeBuilder("dom")

def _tree_construction_files():
    root = os.path.join(test_dir, "tree-construction")
    return sorted(name for name in os.listdir(root) if name.endswith(".dat"))


def _normalize(tokens):
    """Turn walker tokens into a comparable, backend-independent form."""
    raw = []
    for token in tokens:
        if token.get("type") == "SerializeError":
            continue
        token = dict(token)
        if token.get("type") == "Doctype":
            # etree returns "" for absent name/ids; treat that as None.
            token["name"] = token.get("name") or None
            token["publicId"] = token.get("publicId") or None
            token["systemId"] = token.get("systemId") or None
        if isinstance(token.get("data"), dict):
            token["data"] = tuple(sorted(token["data"].items()))
        raw.append(token)
    # DOM keeps every insertText as a separate text node while etree merges
    # into text/tail; merge adjacent text tokens so both compare equal.
    merged = []
    for token in raw:
        if token.get("type") in ("Characters", "SpaceCharacters") and \
                merged and \
                merged[-1].get("type") in ("Characters", "SpaceCharacters") and \
                set(merged[-1]) == set(token):
            previous = merged.pop()
            token["data"] = previous["data"] + token["data"]
            token["type"] = "Characters" if token["data"].strip() \
                else "SpaceCharacters"
        merged.append(token)
    return [tuple(sorted(token.items())) for token in merged]


def _parse_with_observer(builder, source, fragment=None, **kwargs):
    recorder = mutation.EventRecorder()
    parser = HTMLParser(tree=builder, mutationObserver=recorder)
    if fragment is not None:
        tree = parser.parseFragment(source, container=fragment, **kwargs)
    else:
        tree = parser.parse(source, **kwargs)
    return parser, recorder, tree


def _replay(events):
    replay_tree = mutation.ReplayTree()
    for event in events:
        replay_tree.on_event(event)
    return replay_tree


def _assert_replay_matches(builder, walker, source, fragment=None, **kwargs):
    parser, recorder, tree = _parse_with_observer(builder, source, fragment,
                                                  **kwargs)
    actual = _normalize(getTreeWalker(*walker)(tree))

    # Replaying only the final generation must reproduce the final tree.
    replay_tree = _replay(recorder.events)
    replayed = _normalize(replay_tree.walk_tokens())
    assert actual == replayed, "\n%s\n!=\n%s" % (actual, replayed)

    # Replaying every generation in order must also end on the final tree,
    # because the reset boundary discards the abandoned generation.
    all_events = [event
                  for events in recorder.events_by_generation.values()
                  for event in events]
    replayed_all = _normalize(_replay(all_events).walk_tokens())
    assert actual == replayed_all
    return parser, recorder, tree


@pytest.mark.parametrize("builder,walker", [
    (_etree_builder, ("etree", ElementTree)),
    (_dom_builder, ("dom",)),
])
@pytest.mark.parametrize("source", [
    "<html><head><title>t</title></head>"
    "<body><p>hi <b>bold</b> text</p></body></html>",
    "<table><tr><td>cell</td></tr><p>foster me</p></table>",
    "<div>a<table><tr><td>b</td></tr><tr><td><b>c</b></td></tr></table>1c</div>",
    "<p>1<b>2<i>3</b>4</i>5",
    "<a><p></a></p>",
    "<a>1<p>2</a>3</p>",
    "<b><p>x</b>y</p>",
    "<table><b><tr><td>x</td></tr></b></table>",
    "<a><a><a>x",
    "<p><div>nested</div>after",
    "<select><option>a<option>b</select>",
    "<textarea>a&lt;b</textarea><script>if(a<b){}</script>",
    "<ul><li>1<li>2</ul><p>x</p>",
    "<svg><foreignObject><p>x</p></foreignObject></svg>",
    "<!-- c --><!DOCTYPE html><p>x</p>",
    "<!DOCTYPE html PUBLIC '-//W3C//DTD HTML 4.01//EN' "
    "'http://www.w3.org/TR/html4/strict.dtd'><p>x",
])
def test_replay_minimal_documents(builder, walker, source):
    _assert_replay_matches(builder, walker, source)


@pytest.mark.parametrize("builder,walker", [
    (_etree_builder, ("etree", ElementTree)),
    (_dom_builder, ("dom",)),
])
@pytest.mark.parametrize("source,fragment", [
    ("<b>frag</b> text", "div"),
    ("<table><tr><td>x</td></tr>", "div"),
    ("<p>1<b>2</b>3", "div"),
    ("<a><p>x</a>y", "div"),
    ("<td>cell</td>", "tr"),
])
def test_replay_fragments(builder, walker, source, fragment):
    _assert_replay_matches(builder, walker, source, fragment=fragment)


@pytest.mark.parametrize("builder,walker", [
    (_etree_builder, ("etree", ElementTree)),
    (_dom_builder, ("dom",)),
])
def test_replay_tree_construction_files(builder, walker):
    """Replay the whole correction suite and compare the final trees."""
    for filename in _tree_construction_files():
        path = os.path.join(test_dir, "tree-construction", filename)
        for test in TestData(path, "data"):
            container = test["document-fragment"]
            _assert_replay_matches(builder, walker, test["data"],
                                   fragment=container or None,
                                   scripting="script-on" in test)


def test_replay_encoding_reparse():
    source = "<title>Café</title><!--a--><meta charset='utf-8'>".encode("utf-8")
    pad = 10240 - len(source) + 1
    source = source.replace(b"-a-", b"-" + (b"a" * pad) + b"-")

    parser, recorder, tree = _parse_with_observer(
        _etree_builder, source, useChardet=False)
    assert parser.documentEncoding == "utf-8"

    # The reset boundary must be explicit and attributed to the reparse.
    assert recorder.reparse_boundaries
    assert any("encoding-reparse" in (event.reason or "")
               for event in recorder.reparse_boundaries)
    assert len(recorder.events_by_generation) >= 2

    actual = _normalize(getTreeWalker("etree", ElementTree)(tree))
    assert _normalize(_replay(recorder.events).walk_tokens()) == actual
    # Only final-generation nodes survive when all events are replayed.
    all_events = [event
                  for events in recorder.events_by_generation.values()
                  for event in events]
    assert _normalize(_replay(all_events).walk_tokens()) == actual


def test_move_events_from_adoption_agency():
    parser, recorder, tree = _parse_with_observer(
        _etree_builder, "<a>1<p>2</a>3</p>")
    moves = [event for event in recorder.events
             if event.type == mutation.MUTATION_MOVE]
    assert moves
    move = moves[0]
    assert move.parent_id is not None
    assert move.old_parent_id is not None
    assert move.old_parent_id != move.parent_id


def test_late_attribute_events():
    parser, recorder, tree = _parse_with_observer(
        _etree_builder,
        "<html><head></head><body><html lang='en'><body class='x'><p>x")
    attributes = [event for event in recorder.events
                  if event.type == mutation.MUTATION_ATTRIBUTE]
    names = sorted((event.name, event.value) for event in attributes)
    assert names == [("class", "x"), ("lang", "en")]


def test_event_sequence_is_ordered():
    parser, recorder, tree = _parse_with_observer(
        _etree_builder, "<p>a<b>b</b>c</p>")
    sequences = [event.sequence for event in recorder.events]
    assert sequences == list(range(len(sequences)))
    for event in recorder.events:
        assert event.generation == recorder.generation
    # Node ids are stable and unique for the lifetime of the generation.
    created = [event.node_id for event in recorder.events
               if event.type == mutation.MUTATION_CREATE]
    assert len(created) == len(set(created))


def test_reference_receiver_normal_vs_corrected():
    """The small reference receiver replays a clean and a corrected page.

    The clean page only produces creates/inserts/text; the misnested table
    page additionally produces foster-parenting text placement, and both
    final logical trees match the treebuilder output.
    """
    normal = "<p>hello <b>world</b></p>"
    corrected = "<table><p>foster me</p><tr><td>x</td></tr></table>"

    normal_recorder = mutation.EventRecorder()
    HTMLParser(tree=_etree_builder,
               mutationObserver=normal_recorder).parse(normal)
    normal_types = {event.type for event in normal_recorder.events}
    assert "move" not in normal_types
    assert "remove" not in normal_types

    corrected_recorder = mutation.EventRecorder()
    corrected_parser = HTMLParser(tree=_etree_builder,
                                  mutationObserver=corrected_recorder)
    corrected_tree = corrected_parser.parse(corrected)
    replay_tree = _replay(corrected_recorder.events)
    actual = _normalize(
        getTreeWalker("etree", ElementTree)(corrected_tree))
    assert _normalize(replay_tree.walk_tokens()) == actual
    # Foster parenting moved the paragraph out in front of the table.
    assert any(event.type == mutation.MUTATION_TEXT
               for event in corrected_recorder.events)


def test_no_observer_keeps_no_log():
    parser = HTMLParser(tree=_etree_builder)
    parser.parse("<p>a<b>b</b>c</p>")
    assert parser.tree.recorder is None
    assert not hasattr(parser.tree.document, "_mutation_wrapped")


def test_unknown_treebuilder_without_keyword_still_works():
    class LegacyBuilder(_etree_builder):
        def __init__(self, namespaceHTMLElements):
            _etree_builder.__init__(self, namespaceHTMLElements)

    parser = HTMLParser(tree=LegacyBuilder)
    assert parser.parse("<p>x</p>") is not None
