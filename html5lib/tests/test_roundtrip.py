from __future__ import absolute_import, division, unicode_literals

"""Round-trip checks for the HTML serializer and tree walkers.

HTML text can only express a subset of tree shapes.  These tests pin down
which shapes survive ``parse -> walk -> serialize -> parse`` unchanged, and
which ones are reported through ``HTMLSerializer.errors`` instead of being
silently rewritten.

Logical trees are compared through tree-walker tokens, i.e. by namespace URI
and local name.  Nothing here depends on the temporary prefixes that an
ElementTree implementation may invent when dumping XML.
"""

from xml.dom import minidom

import pytest

from .support import treeTypes

import html5lib
from html5lib.constants import namespaces
from html5lib.html5parser import HTMLParser
from html5lib.serializer import HTMLSerializer, SerializeError
from html5lib.treewalkers import getTreeWalker

SVG = namespaces["svg"]
MATHML = namespaces["mathml"]
XLINK = namespaces["xlink"]


# (label, HTML source[, expected serializer errors]).  Every entry is
# expected to round-trip losslessly; entries with a third element trigger
# the serializer's conservative diagnostics without losing information.
ROUNDTRIP_CASES = [
    # foreignObject: SVG integration point back to HTML
    ("foreignObject-html",
     '<svg><foreignObject><p class=x>hi <b>there</b></p></foreignObject></svg>'),
    ("foreignObject-nested-svg",
     '<svg><foreignObject><svg><circle r=1></circle></svg>'
     '</foreignObject></svg>'),
    # MathML integration points
    ("mathml-mi-html-child",
     '<math><mi><b>x</b></mi><mtext>t</mtext></math>'),
    ("mathml-annotation-xml-html",
     '<math><annotation-xml encoding="text/html"><p>x</p>'
     '</annotation-xml></math>'),
    ("mathml-annotation-xml-svg",
     '<math><annotation-xml encoding="image/svg+xml"><svg><rect w=1>'
     '</rect></svg></annotation-xml></math>'),
    ("mathml-html-svg-nesting",
     '<math><annotation-xml encoding="text/html"><svg><foreignObject>'
     '<div>d</div></foreignObject></svg></annotation-xml></math>'),
    # namespaced attributes that HTML has a spelling for
    ("xlink-attributes",
     '<svg><a xlink:href="#x" xlink:type="simple" xlink:title="t">y</a></svg>'),
    ("xml-attributes",
     '<svg xml:lang="en"><text xml:space="preserve">t</text></svg>'),
    ("xmlns-attributes",
     '<svg xmlns:xlink="http://www.w3.org/1999/xlink">'
     '<a xlink:href="#x">y</a></svg>'),
    # same local names in HTML and foreign content
    ("same-name-a",
     '<svg><a><text>x</text></a></svg><a href="#h">html-a</a>'),
    ("same-name-script",
     '<head><script>var a=1</script></head>'
     '<body><svg><script>var b=2</script></svg></body>'),
    ("same-name-title",
     '<head><title>doc</title></head>'
     '<body><svg><title>svg title</title></svg></body>'),
    ("same-name-style",
     '<head><style>p{}</style></head>'
     '<body><svg><style>circle{}</style></svg></body>'),
    # case folding rules for foreign content
    ("svg-camelcase-tags",
     '<svg><linearGradient gradientUnits="u"><stop offset=0>'
     '</stop></linearGradient></svg>'),
    ("svg-camelcase-attrs",
     '<svg><pattern patternTransform="t" xlink:href="#p"></pattern></svg>'),
    ("mathml-definitionurl",
     '<math><mi definitionURL="u">x</mi></math>'),
    # comments, character references, raw text
    ("comments", '<div><!--a--><p><!-- b --></p><!--c--></div>'),
    ("charrefs", '<p>&amp;&lt;&gt;&quot;&#65;&#x1F600;&copy;</p>'),
    ("script-raw-text", '<script>if(a<b&&c>d){x="</";}</script>',
     ["Unexpected </ in CDATA"]),
    ("style-raw-text", '<style>p::before{content:"</"}</style>',
     ["Unexpected </ in CDATA"]),
    ("textarea-rcdata", '<textarea>&lt;x&gt;</textarea>'),
    ("title-rcdata", '<head><title>a &lt; b</title></head>'),
]


ROUNDTRIP_CASES = [case if len(case) == 3 else (case + ([],))
                   for case in ROUNDTRIP_CASES]


def available_trees():
    return [(name, cls) for name, cls in sorted(treeTypes.items())
            if cls is not None]


def parse_tokens(treeCls, source):
    parser = HTMLParser(tree=treeCls["builder"])
    document = parser.parse(source)
    document = treeCls.get("adapter", lambda x: x)(document)
    return normalize_text(list(treeCls["walker"](document)))


def normalize_text(tokens):
    """Coalesce adjacent text tokens.

    Some tree implementations keep adjacent text nodes separate (DOM) while
    others merge them (ElementTree); both describe the same logical text.
    """
    normalized = []
    for token in tokens:
        if (token["type"] in ("Characters", "SpaceCharacters") and
                normalized and normalized[-1]["type"] == "Text"):
            normalized[-1]["data"] += token["data"]
        elif token["type"] in ("Characters", "SpaceCharacters"):
            normalized.append({"type": "Text", "data": token["data"]})
        else:
            normalized.append(token)
    return normalized


def serialize_source(treeCls, source, **options):
    parser = HTMLParser(tree=treeCls["builder"])
    document = parser.parse(source)
    document = treeCls.get("adapter", lambda x: x)(document)
    walker = treeCls["walker"](document)
    serializer = HTMLSerializer(**options)
    return serializer.render(walker), serializer.errors


@pytest.mark.parametrize("treeName,treeCls", available_trees(),
                         ids=[name for name, _ in available_trees()])
@pytest.mark.parametrize("label,source,expected_errors", ROUNDTRIP_CASES,
                         ids=[label for label, *_ in ROUNDTRIP_CASES])
def test_matrix_roundtrips_losslessly(treeName, treeCls, label, source,
                                      expected_errors):
    """parse -> serialize -> parse must preserve the logical tree."""
    out, errors = serialize_source(treeCls, source, omit_optional_tags=False)
    assert errors == expected_errors

    logical_before = parse_tokens(treeCls, source)
    logical_after = parse_tokens(treeCls, out)
    assert logical_before == logical_after, out

    # Serialization itself must be stable: serializing the re-parsed output
    # gives exactly the same HTML text and the same diagnostics.
    out_again, errors_again = serialize_source(treeCls, out,
                                              omit_optional_tags=False)
    assert errors_again == expected_errors
    assert out_again == out


@pytest.mark.parametrize("label,source,expected_errors", ROUNDTRIP_CASES,
                         ids=[label for label, *_ in ROUNDTRIP_CASES])
def test_cross_tree_agreement(label, source, expected_errors):
    """All walkers must describe the same logical tree, including namespace
    URIs; this catches prefix-derived or namespace-dropping walkers."""
    tokens = {}
    for treeName, treeCls in available_trees():
        out, errors = serialize_source(treeCls, source,
                                       omit_optional_tags=False)
        assert errors == expected_errors
        tokens[treeName] = parse_tokens(treeCls, out)

    values = list(tokens.values())
    for other in values[1:]:
        assert values[0] == other


XLINK_CASE = '<svg><a xlink:href="#x" xlink:type="simple" xlink:title="t">y</a></svg>'


def test_foreign_attributes_use_qualified_names():
    """xlink/xml/xmlns attributes must be written with their HTML names."""
    out, errors = serialize_source(available_trees()[0][1], XLINK_CASE,
                                   omit_optional_tags=False,
                                   quote_attr_values="always")
    assert errors == []
    assert 'xlink:href="#x"' in out
    assert 'xlink:type="simple"' in out
    assert 'xlink:title="t"' in out


def test_foreign_attribute_tokens_are_namespace_uris():
    """Attribute keys are (namespace URI, local name) -- never prefixes."""
    tokens = parse_tokens(treeTypes["ElementTree"], XLINK_CASE)
    a = [t for t in tokens if t["type"] == "StartTag" and t["name"] == "a"][0]
    assert list(a["data"].items()) == [
        ((XLINK, "href"), "#x"),
        ((XLINK, "type"), "simple"),
        ((XLINK, "title"), "t"),
    ]


def _token_walker(tokens):
    class TokenWalker(html5lib.treewalkers.base.TreeWalker):
        def __iter__(self):
            for token in tokens:
                yield token
    return TokenWalker(None)


def _render_tokens(tokens, **options):
    serializer = HTMLSerializer(**options)
    return serializer.render(_token_walker(tokens)), serializer.errors


def test_unmappable_attribute_namespace_is_diagnosed_not_rewritten():
    out, errors = _render_tokens([
        {"type": "StartTag", "namespace": SVG, "name": "g",
         "data": {("http://example.com/ns", "custom"): "v"}},
        {"type": "EndTag", "namespace": SVG, "name": "g"},
    ])
    assert errors == ["Cannot serialize attribute custom in namespace "
                      "http://example.com/ns to HTML"]
    # Best effort output, but the loss was reported.
    assert out == "<g custom=v></g>"


def test_unmappable_element_namespace_is_diagnosed():
    out, errors = _render_tokens([
        {"type": "StartTag", "namespace": "http://example.com/ns",
         "name": "weird", "data": {}},
        {"type": "EndTag", "namespace": "http://example.com/ns",
         "name": "weird"},
    ])
    assert errors == ["Cannot serialize element weird in namespace "
                      "http://example.com/ns to HTML"]


def test_diagnostics_raise_in_strict_mode():
    serializer = HTMLSerializer()
    serializer.strict = True
    with pytest.raises(SerializeError):
        serializer.render(_token_walker([
            {"type": "StartTag", "namespace": SVG, "name": "g",
             "data": {("http://example.com/ns", "custom"): "v"}},
            {"type": "EndTag", "namespace": SVG, "name": "g"},
        ]))


def test_comment_with_double_hyphen_is_diagnosed():
    out, errors = _render_tokens([
        {"type": "Comment", "data": "a -- b"},
    ])
    assert errors == ["Comment contains --"]


def test_prefixed_dom_element_uses_local_name():
    """A DOM element created with a prefix must not leak it into the tag."""
    doc = minidom.getDOMImplementation().createDocument(None, None, None)
    rect = doc.createElementNS(SVG, "svg:rect")
    rect.setAttributeNS(XLINK, "xlink:href", "#g")
    doc.appendChild(rect)

    tokens = list(getTreeWalker("dom")(doc))
    start = [t for t in tokens if t["type"] == "StartTag"][0]
    assert start["name"] == "rect"
    assert start["namespace"] == SVG
    assert list(start["data"].items()) == [((XLINK, "href"), "#g")]

    out = HTMLSerializer().render(getTreeWalker("dom")(doc))
    assert out == "<rect xlink:href=#g></rect>"


def test_namespaced_elements_reparse_into_same_namespace():
    """Spot-check the namespaces the matrix relies on, without prefixes."""
    for source, namespace, name in [
        ('<svg><rect></rect></svg>', SVG, "rect"),
        ('<math><mi>x</mi></math>', MATHML, "mi"),
        ('<div></div>', namespaces["html"], "div"),
    ]:
        tokens = parse_tokens(treeTypes["ElementTree"], source)
        matches = [t for t in tokens
                   if t["type"] == "StartTag" and t["name"] == name]
        assert matches and matches[0]["namespace"] == namespace
