"""Explicit description of the context an HTML fragment is parsed in.

When setting the ``innerHTML`` property of an element, the fragment parsing
algorithm needs more information than a single tag name: the element's
namespace, some of its attributes (for integration points) and the elements
that form its ancestor chain (for things like the initial insertion mode and
the form element pointer).  :class:`FragmentContext` carries that information
without having to build and parse a fake complete document.
"""
from __future__ import absolute_import, division, unicode_literals

from six import text_type

from .constants import (
    namespaces,
    asciiUpper2Lower,
    cdataElements,
    rcdataElements,
    htmlIntegrationPointElements,
    mathmlTextIntegrationPointElements,
)


class FragmentContextError(ValueError):
    """Raised when a :class:`FragmentContext` is invalid or self-contradictory."""


_namespaceAliases = {
    "html": namespaces["html"],
    "svg": namespaces["svg"],
    "mathml": namespaces["mathml"],
}


def _known_namespace(namespace):
    return namespace in (namespaces["html"],
                         namespaces["svg"],
                         namespaces["mathml"])


class ElementContext(object):
    """Description of an element in a fragment context.

    :arg name: local name of the element (stored lower case for HTML
        elements; SVG and MathML names are case-sensitive and kept as given)

    :arg namespace: namespace of the element, either the full namespace
        URI or one of the ``"html"``, ``"svg"`` and ``"mathml"`` aliases.
        Defaults to the HTML namespace.

    :arg attributes: mapping of attribute name to attribute value.  Plain
        names are stored as strings, namespaced attributes as
        ``(prefix, local name, namespace URI)`` tuples.
    """

    __slots__ = ("name", "namespace", "attributes")

    def __init__(self, name, namespace=None, attributes=None):
        if not isinstance(name, text_type) or not name:
            raise FragmentContextError(
                "element name must be a non-empty string, got %r" % (name,))

        if namespace is None:
            namespace = namespaces["html"]
        elif not isinstance(namespace, text_type):
            raise FragmentContextError(
                "namespace for %r must be a string, got %r" % (name, namespace))
        else:
            namespace = _namespaceAliases.get(namespace, namespace)

        if not _known_namespace(namespace):
            raise FragmentContextError(
                "unknown namespace %r for element %r; expected 'html', "
                "'svg', 'mathml' or one of their namespace URIs"
                % (namespace, name))

        if namespace == namespaces["html"]:
            name = name.translate(asciiUpper2Lower)

        if attributes is None:
            normalized = {}
        else:
            if not hasattr(attributes, "items"):
                raise FragmentContextError(
                    "attributes of %r must be a mapping of attribute name "
                    "to value" % (name,))
            normalized = {}
            for attr_name, attr_value in attributes.items():
                if isinstance(attr_name, tuple):
                    if (len(attr_name) != 3 or
                            not all(isinstance(part, text_type) for part in attr_name) or
                            not attr_name[1]):
                        raise FragmentContextError(
                            "namespaced attribute on %r must be a "
                            "(prefix, local name, namespace URI) tuple of "
                            "strings, got %r" % (name, attr_name))
                    key = attr_name
                elif isinstance(attr_name, text_type) and attr_name:
                    key = attr_name
                else:
                    raise FragmentContextError(
                        "attribute name on %r must be a non-empty string or "
                        "a (prefix, local name, namespace URI) tuple, got %r"
                        % (name, attr_name))
                if not isinstance(attr_value, text_type):
                    raise FragmentContextError(
                        "value of attribute %r on %r must be a string, got %r"
                        % (attr_name, name, attr_value))
                normalized[key] = attr_value

        self.name = name
        self.namespace = namespace
        # Defensive copy: callers must be able to keep mutating (or reuse)
        # their own mapping without affecting the context.
        self.attributes = normalized

    @property
    def nameTuple(self):
        return self.namespace, self.name

    def isHTMLIntegrationPoint(self):
        if (self.name == "annotation-xml" and
                self.namespace == namespaces["mathml"]):
            encoding = self.attributes.get("encoding")
            if encoding is not None:
                encoding = encoding.translate(asciiUpper2Lower)
            return encoding in ("text/html", "application/xhtml+xml")
        return self.nameTuple in htmlIntegrationPointElements

    def isMathMLTextIntegrationPoint(self):
        return self.nameTuple in mathmlTextIntegrationPointElements

    def __repr__(self):
        return "ElementContext(%r, %r, %r)" % (self.name,
                                               self.namespace,
                                               self.attributes)


def _as_element_context(element, position):
    if isinstance(element, ElementContext):
        return element
    if isinstance(element, text_type):
        return ElementContext(element)
    raise FragmentContextError(
        "%s must be an ElementContext or an element name string, got %r"
        % (position, element))


class FragmentContext(object):
    """Explicit description of the context of a fragment parse.

    This describes the element whose ``innerHTML`` is being parsed, together
    with the elements that form its ancestor chain.  It replaces the older
    string ``container`` argument with a value that carries namespace and
    attribute information.

    :arg name: local name of the context element (case-insensitive for HTML)

    :arg namespace: namespace of the context element, either the full
        namespace URI or one of the ``"html"``, ``"svg"`` and ``"mathml"``
        aliases; defaults to the HTML namespace

    :arg attributes: mapping of attribute name to attribute value

    :arg ancestors: iterable of ancestor elements, ordered from the
        document root towards the context element's parent.  Each entry is an
        :class:`ElementContext` or an element name string.

    A number of derived pieces of parser state are determined from this
    description:

    * the **form element pointer** is set to the context element when it is a
      ``form`` element in the HTML namespace;
    * the **scripting flag** still comes from the ``scripting`` argument, but
      it is what makes a ``noscript`` context tokenize as raw text;
    * **integration points** (HTML integration points such as SVG
      ``foreignObject`` and MathML ``annotation-xml``, and MathML text
      integration points) are detected from the namespace/name/attributes of
      the context and its ancestors;
    * the **initial tokenizer state** is RCDATA for ``title``/``textarea``,
      raw text for the raw text elements (and ``noscript`` when scripting is
      enabled), plain text for ``plaintext`` and the data state otherwise;
    * the **initial insertion mode** is determined by walking the ancestor
      chain, matching the fragment parsing algorithm.
    """

    __slots__ = ("element", "ancestors")

    def __init__(self, name, namespace=None, attributes=None, ancestors=None):
        self.element = ElementContext(name, namespace=namespace,
                                      attributes=attributes)
        if ancestors is None:
            chain = []
        else:
            try:
                entries = list(ancestors)
            except TypeError:
                raise FragmentContextError(
                    "ancestors must be an iterable of ElementContext "
                    "instances or element name strings, got %r"
                    % (ancestors,))
            chain = [_as_element_context(entry, "ancestor #%d" % index)
                     for index, entry in enumerate(entries)]

        self._validate_chain(chain, self.element)
        self.ancestors = tuple(chain)

    @staticmethod
    def _validate_chain(chain, context):
        elements = chain + [context]
        for index, element in enumerate(elements):
            if element.namespace != namespaces["html"]:
                continue
            if index == 0:
                continue
            parent = elements[index - 1]
            if parent.namespace == namespaces["html"]:
                continue
            if parent.isHTMLIntegrationPoint():
                continue
            if (parent.namespace == namespaces["mathml"] and
                    parent.isMathMLTextIntegrationPoint()):
                continue
            raise FragmentContextError(
                "HTML element %r cannot be a child of %s %r; the parent "
                "would have to be an HTML integration point or a MathML "
                "text integration point" % (element.name,
                                            _namespace_name(parent.namespace),
                                            parent.name))

        # SVG only nests inside SVG or an HTML/MathML integration point.
        for index, element in enumerate(elements):
            if element.namespace != namespaces["svg"] or index == 0:
                continue
            parent = elements[index - 1]
            if parent.namespace == namespaces["svg"]:
                continue
            if parent.namespace == namespaces["html"]:
                continue
            if parent.isHTMLIntegrationPoint():
                continue
            raise FragmentContextError(
                "SVG element %r cannot be a child of %s %r without an HTML "
                "integration point" % (element.name,
                                       _namespace_name(parent.namespace),
                                       parent.name))

    @property
    def chain(self):
        """All elements from the outermost ancestor to the context element."""
        return self.ancestors + (self.element,)

    @property
    def name(self):
        return self.element.name

    @property
    def namespace(self):
        return self.element.namespace

    @property
    def attributes(self):
        return self.element.attributes

    def isHTMLIntegrationPoint(self):
        return self.element.isHTMLIntegrationPoint()

    def isMathMLTextIntegrationPoint(self):
        return self.element.isMathMLTextIntegrationPoint()

    def initialTokenizerState(self, scripting=False):
        """Name of the tokenizer state the fragment parse must start in."""
        name = self.element.name
        if self.element.namespace != namespaces["html"]:
            return "data"
        if name in cdataElements:
            return "rcdata"
        if name in rcdataElements:
            if name == "noscript" and not scripting:
                return "data"
            return "rawtext"
        if name == "plaintext":
            return "plaintext"
        return "data"

    def isFormContext(self):
        return self.element.nameTuple == (namespaces["html"], "form")

    def __repr__(self):
        return "FragmentContext(%r, %r, %r, ancestors=%r)" % (
            self.element.name, self.element.namespace,
            self.element.attributes, self.ancestors)


def _namespace_name(namespace):
    for key, value in namespaces.items():
        if value == namespace and key in ("html", "svg", "mathml"):
            return key
    return namespace
