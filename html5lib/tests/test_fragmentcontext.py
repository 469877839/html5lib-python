from __future__ import absolute_import, division, unicode_literals

import pytest

from . import support  # noqa

from html5lib import (
    HTMLParser, ElementContext, FragmentContext, FragmentContextError,
    parseFragment,
)
from html5lib.constants import namespaces


def parse_as(fragment, context, scripting=False, **parser_kwargs):
    parser = HTMLParser(**parser_kwargs)
    tree = parser.parseFragment(fragment, context, scripting=scripting)
    return parser.tree.testSerializer(tree)


def test_same_fragment_in_div_table_svg_mathml_contexts():
    fragment = "<path></path><b>&amp;</b><tr><td>cell</td></tr>"

    assert parse_as(fragment, FragmentContext("div")) == "\n".join([
        "|<DOCUMENT_FRAGMENT>",
        "|  <html path>",
        "|  <html b>",
        '|    "&"',
        '|  "cell"',
    ])

    assert parse_as(fragment, FragmentContext("table")) == "\n".join([
        "|<DOCUMENT_FRAGMENT>",
        "|  <html path>",
        "|  <html b>",
        '|    "&"',
        "|  <html tbody>",
        "|    <html tr>",
        "|      <html td>",
        '|        "cell"',
    ])

    assert parse_as(
        fragment, FragmentContext("tbody", ancestors=["table"])) == "\n".join([
        "|<DOCUMENT_FRAGMENT>",
        "|  <html path>",
        "|  <html b>",
        '|    "&"',
        "|  <html tr>",
        "|    <html td>",
        '|      "cell"',
    ])

    assert parse_as(
        fragment, FragmentContext("svg", namespace=namespaces["svg"])) == "\n".join([
        "|<DOCUMENT_FRAGMENT>",
        "|  <html b>",
        '|    "&"',
        '|  "cell"',
        "|  <svg path>",
    ])

    assert parse_as(
        fragment, FragmentContext("math", namespace=namespaces["mathml"])) == "\n".join([
        "|<DOCUMENT_FRAGMENT>",
        "|  <html b>",
        '|    "&"',
        '|  "cell"',
        "|  <math path>",
    ])


def test_character_references_depend_on_context_namespace_and_state():
    fragment = "&amp;&#x41;&notit;&#x0;"

    # The data state in HTML, SVG and MathML contexts decodes references.
    for context in (
        FragmentContext("div"),
        FragmentContext("textarea"),
        FragmentContext("svg", namespace=namespaces["svg"]),
        FragmentContext("math", namespace=namespaces["mathml"]),
    ):
        assert parse_as(fragment, context) == "\n".join([
            "|<DOCUMENT_FRAGMENT>",
            '|  "&A¬it;�"',
        ])

    # Raw text elements do not decode character references.
    for context in (FragmentContext("script"), FragmentContext("style")):
        assert parse_as(fragment, context) == "\n".join([
            "|<DOCUMENT_FRAGMENT>",
            '|  "%s"' % fragment,
        ])


def test_raw_text_and_rcdata_tokenizer_states():
    fragment = "<b>&amp;</b>"

    # RCDATA: references are decoded but tags are not parsed.
    for name in ("title", "textarea"):
        assert parse_as(fragment, FragmentContext(name)) == "\n".join([
            "|<DOCUMENT_FRAGMENT>",
            '|  "<b>&</b>"',
        ])

    # Raw text: neither references nor tags are parsed.
    for name in ("style", "script"):
        assert parse_as(fragment, FragmentContext(name)) == "\n".join([
            "|<DOCUMENT_FRAGMENT>",
            '|  "<b>&amp;</b>"',
        ])

    # noscript is raw text only when scripting is enabled.
    assert parse_as(fragment, FragmentContext("noscript")) == "\n".join([
        "|<DOCUMENT_FRAGMENT>",
        "|  <html b>",
        '|    "&"',
    ])
    assert parse_as(fragment, FragmentContext("noscript"), scripting=True) == "\n".join([
        "|<DOCUMENT_FRAGMENT>",
        '|  "<b>&amp;</b>"',
    ])

    # plaintext consumes everything as text.
    assert parse_as(fragment, FragmentContext("plaintext")) == "\n".join([
        "|<DOCUMENT_FRAGMENT>",
        '|  "<b>&amp;</b>"',
    ])


def test_namespace_switching_at_integration_points():
    # foreignObject is an HTML integration point inside SVG.
    assert parse_as(
        "<foreignObject><div><b>x</b></div></foreignObject><circle />",
        FragmentContext("svg", namespace=namespaces["svg"]),
    ) == "\n".join([
        "|<DOCUMENT_FRAGMENT>",
        "|  <svg foreignObject>",
        "|    <html div>",
        "|      <html b>",
        '|        "x"',
        "|  <svg circle>",
    ])

    # mi/mol/mn/ms/mtext are MathML text integration points; mglyph and
    # malignmark stay in the MathML namespace.
    assert parse_as(
        "<b>x</b><mglyph /><malignmark />",
        FragmentContext("mi", namespace=namespaces["mathml"]),
    ) == "\n".join([
        "|<DOCUMENT_FRAGMENT>",
        "|  <html b>",
        '|    "x"',
        "|  <math mglyph>",
        "|  <math malignmark>",
    ])

    # annotation-xml is an HTML integration point only with the right
    # encoding attribute; namespaced attributes are described as tuples.
    assert parse_as(
        "<div>x</div>",
        FragmentContext(
            "annotation-xml", namespace=namespaces["mathml"],
            attributes={"encoding": "text/html"}),
    ) == "\n".join([
        "|<DOCUMENT_FRAGMENT>",
        "|  <html div>",
        '|    "x"',
    ])

    # An svg start tag still switches from MathML to SVG.
    assert parse_as(
        "<svg><circle /></svg>",
        FragmentContext("annotation-xml", namespace=namespaces["mathml"]),
    ) == "\n".join([
        "|<DOCUMENT_FRAGMENT>",
        "|  <svg svg>",
        "|    <svg circle>",
    ])

    # Without the encoding attribute annotation-xml is not an integration
    # point, so <foo> stays in the MathML namespace.
    assert parse_as(
        "<foo>x</foo>",
        FragmentContext("annotation-xml", namespace=namespaces["mathml"]),
    ) == "\n".join([
        "|<DOCUMENT_FRAGMENT>",
        "|  <math foo>",
        '|    "x"',
    ])


def test_ancestor_chain_sets_insertion_mode():
    # An option inside a select uses the "in select" insertion mode.
    parser = HTMLParser()
    parser.parseFragment(
        "<div>x</div><option>a</option>",
        FragmentContext("option", ancestors=["select"]))
    assert [error[1] for error in parser.errors] == [
        "unexpected-start-tag-in-select",
        "unexpected-end-tag-in-select",
    ]

    tree = parse_as(
        "<div>x</div><option>a</option>",
        FragmentContext("option", ancestors=["select"]))
    assert tree == "\n".join([
        "|<DOCUMENT_FRAGMENT>",
        '|  "x"',
        "|  <html option>",
        '|    "a"',
    ])


def test_form_pointer_from_form_context():
    parser = HTMLParser()
    tree = parser.parseFragment(
        "<form id='inner'><input></form>", FragmentContext("form"))
    # A nested form start tag is ignored because the context element already
    # occupies the form element pointer.
    assert [error[1] for error in parser.errors] == ["unexpected-start-tag"]
    assert parser.tree.testSerializer(tree) == "\n".join([
        "|<DOCUMENT_FRAGMENT>",
        "|  <html input>",
    ])


def test_string_container_remains_compatible():
    for name in ("div", "table", "textarea", "script", "plaintext",
                 "select", "title", "noscript"):
        fragment = "<b>&amp;</b><i></i>"
        from_string = parse_as(fragment, name)
        from_context = parse_as(fragment, FragmentContext(name))
        assert from_string == from_context, name


def test_context_is_not_mutated_by_parsing():
    attributes = {"id": "cell"}
    context = FragmentContext(
        "td",
        ancestors=[ElementContext("table"), "tbody"],
        attributes=attributes)
    snapshot = (context.name, context.namespace, dict(context.attributes),
                tuple((node.name, node.namespace, dict(node.attributes))
                      for node in context.ancestors))
    parseFragment("<b>bold</b><p>para</p>", context)
    assert (context.name, context.namespace, dict(context.attributes),
            tuple((node.name, node.namespace, dict(node.attributes))
                  for node in context.ancestors)) == snapshot

    # The caller's own mapping can be modified afterwards without affecting
    # the context.
    attributes["id"] = "changed"
    assert context.attributes == {"id": "cell"}


def test_void_namespace_mode_keeps_foreign_namespaces():
    parser = HTMLParser(namespaceHTMLElements=False)
    tree = parser.parseFragment(
        "<path></path><b>x</b>",
        FragmentContext("svg", namespace=namespaces["svg"]))
    assert parser.tree.testSerializer(tree) == "\n".join([
        "|<DOCUMENT_FRAGMENT>",
        "|  <b>",
        '|    "x"',
        "|  <svg path>",
    ])


def test_invalid_contexts_raise_specific_errors():
    with pytest.raises(FragmentContextError) as error:
        FragmentContext("")
    assert "non-empty string" in str(error.value)

    with pytest.raises(FragmentContextError) as error:
        FragmentContext("div", namespace="http://example.com/ns")
    assert "unknown namespace" in str(error.value)


def test_namespace_aliases_resolve_to_uris():
    svg = FragmentContext("svg", namespace="svg")
    assert svg.namespace == namespaces["svg"]
    math = FragmentContext("math", namespace="mathml")
    assert math.namespace == namespaces["mathml"]
    html = FragmentContext("div", namespace="html")
    assert html.namespace == namespaces["html"]
    # The alias and the full URI describe the same context.
    assert parse_as("<circle />", svg) == parse_as(
        "<circle />", FragmentContext("svg", namespace=namespaces["svg"]))

    with pytest.raises(FragmentContextError) as error:
        FragmentContext("div", attributes={"id": 1})
    assert "must be a string" in str(error.value)


def test_contradictory_contexts_raise_specific_errors():
    # HTML content cannot be placed inside foreign content unless the parent
    # is a MathML text integration point or an HTML integration point.
    with pytest.raises(FragmentContextError) as error:
        FragmentContext(
            "div", ancestors=[ElementContext("svg", namespaces["svg"])])
    assert "HTML integration point" in str(error.value)

    with pytest.raises(FragmentContextError) as error:
        FragmentContext(
            "span", ancestors=[ElementContext("math", namespaces["mathml"])])
    assert "HTML integration point" in str(error.value)

    # SVG needs an SVG parent or an HTML integration point.
    with pytest.raises(FragmentContextError) as error:
        FragmentContext(
            "svg", namespace=namespaces["svg"],
            ancestors=[ElementContext("math", namespaces["mathml"])])
    assert "HTML integration point" in str(error.value)

    # Valid cases must not raise.
    FragmentContext(
        "div", ancestors=[ElementContext("foreignObject", namespaces["svg"])])
    FragmentContext(
        "b", ancestors=[ElementContext("mi", namespaces["mathml"])])
    FragmentContext(
        "div",
        ancestors=[ElementContext(
            "annotation-xml", namespaces["mathml"],
            {"encoding": "TEXT/HTML"})])


def test_invalid_container_arguments_raise():
    with pytest.raises(FragmentContextError):
        parseFragment("<b></b>", container=42)
    with pytest.raises(FragmentContextError):
        parseFragment("<b></b>", container={"name": "div"})


def test_parse_fragment_accepts_none_container():
    # None keeps the historical default of "div".
    assert parse_as("<b>x</b>", None) == parse_as("<b>x</b>", "div")


def test_context_works_with_dom_treebuilder():
    parser = HTMLParser(tree="dom")
    tree = parser.parseFragment(
        "<path></path><b>&amp;</b>",
        FragmentContext("svg", namespace=namespaces["svg"]))
    assert parser.tree.testSerializer(tree) == "\n".join([
        "#document-fragment",
        "|  <html b>",
        '|    "&"',
        "|  <svg path>",
    ])
