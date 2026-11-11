"""Cross-regressions for table error correction and formatting-element
correction.

The tree-construction specification has two separate recovery mechanisms that
interact on broken pages:

* ``in table`` recovery: non-table content is reported as a parse error and
  *foster parented* before the ``table`` element in the DOM.
* *active formatting element* recovery: the adoption agency algorithm, the
  list of active formatting elements, scope markers, and element
  reconstruction.

This module pins the interaction between the two.  Expected trees are derived
by hand from the specification's insertion-mode steps, NOT from running this
(or any other) html5lib build, so a regression in either mechanism is caught
even if it is self-consistent across builders.

Every case is run through two treebuilder implementations.  ``etree`` is
configured with ``fullTree=True`` so that, exactly like ``support.py`` does
upstream, it exposes a document root; the DOM builder already does.  After
``convert(3)`` / ``convertTreeDump`` both serializers emit byte-identical
"logical tree" text (see ``test_builders_share_one_logical_tree``).  That is
the mapping used here to compare the builders to one another.

Inputs are kept at their shortest failing form; each ``source`` is the
minimal string that still exercises the target interaction.  Assertion
messages always echo ``source``/``container``/builder/namespace so a failure
records the shortest reproducing input.
"""

from __future__ import absolute_import, division, unicode_literals

import textwrap

import xml.etree.ElementTree as ElementTree

import pytest

from html5lib import HTMLParser
from html5lib.constants import namespaces
from html5lib.treebuilders import getTreeBuilder

from .tree_construction import convertTreeDump

# Treebuilders under test.  ``fullTree=True`` matches the upstream convention
# in support.py and gives the etree tree the same document root the DOM tree
# has, so the two can be compared as one logical tree.
TREE_TYPES = {
    "DOM": getTreeBuilder("dom"),
    "ElementTree": getTreeBuilder("etree", ElementTree, fullTree=True),
}

HTML_NS = namespaces["html"]


def _t(text):
    """Dedent an embedded expected-tree literal to column 0."""
    return textwrap.dedent(text).strip("\n")


def _strip_html_ns(tree):
    """Map a namespaced logical tree to its non-namespaced sibling.

    The serializer prints a namespaced HTML element as ``<html name>`` and the
    non-namespaced one as ``<name>``.  This is a string mapping over the
    spec-derived expected tree; it is not a second parse.
    """
    return "\n".join(
        line.replace("<html ", "<", 1) if line.lstrip().startswith("<html ")
        else line
        for line in tree.splitlines()
    )


def _parser(tree_class, namespace_html):
    return HTMLParser(tree=tree_class, namespaceHTMLElements=namespace_html)


def _parse(parser, source, container):
    if container is None:
        return parser.parse(source)
    return parser.parseFragment(source, container=container)


def _logical_tree(tree_class, source, container, namespace_html):
    parser = _parser(tree_class, namespace_html)
    document = _parse(parser, source, container)
    return convertTreeDump(parser.tree.testSerializer(document))


class Case(object):
    def __init__(self, name, source, container, tree, errors):
        self.name = name
        self.source = source
        self.container = container
        self.tree = tree
        self.errors = errors

    def __repr__(self):
        return "Case(%r)" % (self.name,)


# ---------------------------------------------------------------------------
# Spec-derived cases.
#
# Each ``tree`` literal below was derived with pencil and paper from the
# WHATWG tree-construction steps (in-table foster parenting, the list of
# active formatting elements, cell/caption markers, the adoption agency and
# the fragment-context reset algorithm).  The ``errors`` lists are the
# corresponding parse-error *categories*, not positions.
# --------------------------------------------------------------------------
CASES = [
    # 1. ``<b>`` is opened in table context: foster-parented before the
    #    table (parse error) and kept on the active formatting list.  The
    #    cell pushes a marker; ``</table>`` closes the cell, and the text
    #    ``y`` after the table is reconstructed as a fresh ``<b>`` clone.
    Case(
        "foster_reconstruct",
        "<table><b><tr><td>x</table>y",
        None,
        _t("""
            <html html>
              <html head>
              <html body>
                <html b>
                <html table>
                  <html tbody>
                    <html tr>
                      <html td>
                        "x"
                <html b>
                  "y"
        """),
        [
            "expected-doctype-but-got-start-tag",
            "unexpected-start-tag-implies-table-voodoo",
            "expected-closing-tag-but-got-eof",
        ],
    ),
    # 2. ``<b>`` is already open (and on the stack) before the table, so
    #    nothing is reconstructed; the table nests inside ``<b>`` and the
    #    trailing text simply becomes ``table.tail``.
    Case(
        "afe_open_before_table",
        "<b><table><td>x</table>y",
        None,
        _t("""
            <html html>
              <html head>
              <html body>
                <html b>
                  <html table>
                    <html tbody>
                      <html tr>
                        <html td>
                          "x"
                  "y"
        """),
        [
            "expected-doctype-but-got-start-tag",
            "unexpected-cell-in-table-body",
            "expected-closing-tag-but-got-eof",
        ],
    ),
    # 3. The ``<b>`` lives *inside* the cell, after the marker.  Closing the
    #    table via implied end tags clears active formatting elements up to
    #    that marker, so the trailing ``y`` must NOT be reconstructed.
    Case(
        "marker_blocks_cell",
        "<table><td><b>x</table>y",
        None,
        _t("""
            <html html>
              <html head>
              <html body>
                <html table>
                  <html tbody>
                    <html tr>
                      <html td>
                        <html b>
                          "x"
                "y"
        """),
        [
            "expected-doctype-but-got-start-tag",
            "unexpected-cell-in-table-body",
            "unexpected-cell-end-tag",
        ],
    ),
    # 4. A nested ``<table>`` start tag implies ``</table>``; the inner table
    #    is then closed.  The active ``<b>`` keeps sitting between the two
    #    table siblings, and the trailing ``z`` is inside it.
    Case(
        "nested_table_end",
        "<b>x<table><table><td>y</table></table>z",
        None,
        _t("""
            <html html>
              <html head>
              <html body>
                <html b>
                  "x"
                  <html table>
                  <html table>
                    <html tbody>
                      <html tr>
                        <html td>
                          "y"
                  "z"
        """),
        [
            "expected-doctype-but-got-start-tag",
            "unexpected-start-tag-implies-end-tag",
            "unexpected-cell-in-table-body",
            "unexpected-end-tag",
            "expected-closing-tag-but-got-eof",
        ],
    ),
    # 5. ``<b>`` is fostered before the table; the adoption agency runs for
    #    ``</b>`` while the cell is on the stack.  Scope blocks it, so the
    #    original ``<b>`` is dropped, the cell text is not reformatted and
    #    the cell ends with the plain ``xy``.
    Case(
        "adoption_foster",
        "<table><b><td>x</b>y</table>",
        None,
        _t("""
            <html html>
              <html head>
              <html body>
                <html b>
                <html table>
                  <html tbody>
                    <html tr>
                      <html td>
                        "xy"
        """),
        [
            "expected-doctype-but-got-start-tag",
            "unexpected-start-tag-implies-table-voodoo",
            "unexpected-cell-in-table-body",
            "unexpected-end-tag",
        ],
    ),
    # 6. Fragment whose context is ``table``: the leading ``<b>`` is foster
    #    parented before the implied ``tbody``; ``</table>`` has no table in
    #    table scope, so it is the innerHTML "scope" parse error.
    Case(
        "frag_table_context",
        "<b><tr><td>x</table>",
        "table",
        _t("""
            <html b>
            <html tbody>
              <html tr>
                <html td>
                  "x"
        """),
        [
            "unexpected-start-tag-implies-table-voodoo",
            "XXX-undefined-error",
        ],
    ),
    # 7. Fragment whose context is ``tr``: a ``td`` is legal, and ``</b>``
    #    inside the cell is a normal cell-scoped close.  The ``y`` after it
    #    stays as a direct child of the cell.  No errors at all.
    Case(
        "frag_row_context",
        "<td><b>x</b>y",
        "tr",
        _t("""
            <html td>
              <html b>
                "x"
              "y"
        """),
        [],
    ),
    # 8. Fragment whose context is ``div``: a table started inside an open
    #    ``<b>``; the ``<td>`` implies ``tbody``/``tr``.  Same shape as a
    #    whole-document parse, but rooted at the fragment.
    Case(
        "frag_div_table",
        "<b>x<table><td>y",
        "div",
        _t("""
            <html b>
              "x"
              <html table>
                <html tbody>
                  <html tr>
                    <html td>
                      "y"
        """),
        [
            "unexpected-cell-in-table-body",
            "expected-closing-tag-but-got-eof",
        ],
    ),
]

CASE_IDS = [case.name for case in CASES]


def _failure(parser, tree_name, case, namespace_html, expected, actual):
    return (
        "\n\nInput: %r\nContainer: %r\nTreebuilder: %s\n"
        "namespaceHTMLElements: %s\nExpected:\n%s\nReceived:\n%s\n"
        "Errors: %r"
        % (case.source, case.container, tree_name, namespace_html,
           expected, actual, [code for _, code, _ in parser.errors])
    )


@pytest.mark.parametrize("tree_name", sorted(TREE_TYPES))
@pytest.mark.parametrize("namespace_html", [True, False],
                         ids=["namespaced", "no-namespace"])
@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_logical_tree(tree_name, namespace_html, case):
    """Final parent/child shape and text placement match the spec tree."""
    expected = case.tree if namespace_html else _strip_html_ns(case.tree)
    parser = _parser(TREE_TYPES[tree_name], namespace_html)
    document = _parse(parser, case.source, case.container)
    actual = convertTreeDump(parser.tree.testSerializer(document))
    assert actual == expected, _failure(parser, tree_name, case,
                                        namespace_html, expected, actual)


@pytest.mark.parametrize("tree_name", sorted(TREE_TYPES))
@pytest.mark.parametrize("namespace_html", [True, False],
                         ids=["namespaced", "no-namespace"])
@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_error_categories(tree_name, namespace_html, case):
    """Parse-error *categories* are the spec-mandated ones, nothing more."""
    parser = _parser(TREE_TYPES[tree_name], namespace_html)
    _parse(parser, case.source, case.container)
    actual = [code for _, code, _ in parser.errors]
    assert actual == case.errors, _failure(parser, tree_name, case,
                                           namespace_html, case.errors, actual)


def test_builders_share_one_logical_tree():
    """Both treebuilders map to the same logical tree in both namespace modes.

    ``fullTree=True`` for etree plus ``convertTreeDump`` is the mapping: it
    normalizes the document root (``#document`` / ``DOMDocument``) and the
    serializer indent so the two builders are directly comparable.  This test
    pins that the mapping holds for every cross-regression case.
    """
    for namespace_html in (True, False):
        for case in CASES:
            trees = {}
            errors = {}
            for tree_name in sorted(TREE_TYPES):
                parser = _parser(TREE_TYPES[tree_name], namespace_html)
                document = _parse(parser, case.source, case.container)
                trees[tree_name] = convertTreeDump(
                    parser.tree.testSerializer(document))
                errors[tree_name] = [code for _, code, _ in parser.errors]
            unique_trees = set(trees.values())
            assert len(unique_trees) == 1, (
                "builders disagree on %r (namespace=%s):\n%r"
                % (case.source, namespace_html, trees))
            assert len(set(map(tuple, errors.values()))) == 1, (
                "builders disagree on errors for %r (namespace=%s):\n%r"
                % (case.source, namespace_html, errors))


def _q(name):
    return "{%s}%s" % (HTML_NS, name)


def test_parent_child_text_and_tail():
    """Explicit parent/child, text and tail assertions on the etree tree.

    ``<table><b><tr><td>x</table>y`` is the canonical interaction: the
    foster-parented ``<b>`` must be an element sibling *before* the table
    (with no text), the cell text must be ``x``, and the reconstructed ``<b>``
    after the table must own the ``y``.
    """
    parser = _parser(TREE_TYPES["ElementTree"], True)
    document = parser.parse("<table><b><tr><td>x</table>y")

    html_element = document[0]
    head, body = html_element[0], html_element[1]
    assert (head.tag, body.tag) == (_q("head"), _q("body"))

    # body children in order: fostered <b>, <table>, reconstructed <b>.
    assert [child.tag for child in body] == [_q("b"), _q("table"), _q("b")]
    fostered_b, table, reconstructed_b = body

    # Foster-parented b is empty; the cell owns "x"; the clone owns "y".
    assert fostered_b.text is None
    assert fostered_b.tail is None
    assert table[0].tag == _q("tbody")
    assert table[0][0].tag == _q("tr")
    td = table[0][0][0]
    assert td.tag == _q("td")
    assert td.text == "x"
    assert td.tail is None
    assert reconstructed_b.text == "y"
    assert reconstructed_b.tail is None


def test_text_becomes_tail_after_reconstruction():
    """Reconstruction after ``</table>`` appends text as the table's tail.

    With ``<b>`` still open before the table, the trailing ``y`` belongs to
    the open ``<b>`` *after* the table node, i.e. ``table.tail == "y"`` in the
    ElementTree representation.
    """
    parser = _parser(TREE_TYPES["ElementTree"], True)
    document = parser.parse("<b><table><td>x</table>y")
    body = document[0][1]
    b = body[0]
    assert [child.tag for child in b] == [_q("table")]
    assert b.text is None
    assert b[0].tail == "y"


def test_html_namespace_assignment():
    """Foster parenting and reconstruction keep HTML elements in the HTML ns."""
    for namespace_html, expected_tag in ((True, _q("b")), (False, "b")):
        parser = _parser(TREE_TYPES["ElementTree"], namespace_html)
        document = parser.parse("<table><b><tr><td>x</table>y")
        body = document[0][1]
        fostered_b, reconstructed_b = body[0], body[2]
        assert fostered_b.tag == expected_tag
        assert reconstructed_b.tag == expected_tag
