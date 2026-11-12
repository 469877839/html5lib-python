"""Optional, ordered observation of the tree mutations html5lib performs.

The token stream alone cannot be used to maintain a live tree: when html5lib
corrects malformed input (foster parenting, active formatting element
reconstruction, the adoption agency algorithm) it moves nodes that were
already inserted, and it may discard a partially built tree when an encoding
change forces a reparse.  A downstream consumer that needs to mirror the tree
as it is being built can instead register a :class:`TreeMutationObserver`.

Observer events
===============

:class:`~html5lib.treebuilders.base.TreeBuilder` accepts an optional
``observer`` argument.  When given, every actual structural change is
reported, in order, as a :class:`MutationEvent`.  Each event carries:

``sequence``
    Monotonically increasing operation counter, starting at 0 for the first
    mutation of a parse.
``generation``
    Counter that starts at 0 and is bumped every time the parser resets
    (including the reset forced by an encoding change).  Events from different
    generations must not be merged into one tree.
``type``
    One of the ``MUTATION_*`` constants below.
``node_id``
    The stable id of the node the event is about.  Ids are assigned when a
    node is first seen by the recorder; they are unique within a generation
    and are never reused for another node.
``parent_id`` / ``before_id``
    For insertion-like events: the container and the sibling the node is
    inserted before (``None`` means "append at the end").  For
    :data:`MUTATION_TEXT`, ``before_id`` is the child whose tail receives the
    text, or ``None`` when the container's own text receives it.
``kind`` / ``name`` / ``namespace`` / ``attributes`` / ``text`` / ``tail``
    Event type specific payload.

Event types:

* :data:`MUTATION_CREATE` -- a node was created.  ``kind`` is ``"element"``,
  ``"comment"``, ``"doctype"``, ``"document"`` or ``"fragment"``.  Clones
  made by active formatting element reconstruction and by the adoption
  agency are reported as ``CREATE`` events too (``clone_of`` points at the
  original node id); they are *new* logical nodes, not moves.
* :data:`MUTATION_INSERT` -- a node was inserted into ``parent_id`` directly
  before ``before_id`` (``None`` = append).
* :data:`MUTATION_MOVE` -- a node that already had a parent was detached and
  inserted into ``parent_id`` before ``before_id``.  ``old_parent_id`` and
  ``old_before_id`` describe where it was removed from.
* :data:`MUTATION_TEXT` -- ``text`` was appended to ``node_id``.  When
  ``before_id`` is ``None`` the data was merged into the node's own leading
  text; otherwise it was merged into the tail of the child ``before_id``.
  Adjacent text is therefore always merged, never reported as a new node.
* :data:`MUTATION_ATTRIBUTE` -- an attribute was added to ``node_id`` after
  the node entered the tree.  ``name`` is either a string or a
  ``(prefix, localName, namespace)`` tuple and ``value`` is the new value.
* :data:`MUTATION_REMOVE` -- ``node_id`` was detached from ``parent_id``.
* :data:`MUTATION_RESET` -- a parse boundary.  Every node observed before
  this event belongs to an abandoned/previous tree and must be discarded.
  ``parent_id`` is ``None``; ``reason`` is a short string.

Nothing is recorded when no observer is supplied: the recorder is not
created and the wrappers are not installed.

Replaying events
================

:class:`ReplayTree` applies an event log to an in-memory logical tree.  It is
deliberately small and is meant as a reference for receivers, not as a
replacement for a real DOM.  Feed it the events of a single generation and
compare its :func:`replay_tokens` output with the tokens produced by
``html5lib.treewalkers.getTreeWalker(...)`` for the final tree.

Not-yet-stable nodes
====================

The log is a *history of changes*, not a promise that the current state is
final.  Until the parser has consumed EOF:

* elements still in ``openElements`` may receive more children or text;
* ``<html>``, ``<head>`` and ``<body>`` may have attributes added later;
* active formatting elements may be cloned and reconstructed;
* table descendants may be foster parented elsewhere, and the adoption
  agency algorithm may detach and re-insert any element below a formatting
  element;
* ``ReparseException`` may throw the whole generation away.

A receiver can only treat a node as stable once it has been removed from the
open element stack (and no later ``MOVE``/``INSERT`` refers to it), or once
the parse has finished without a trailing ``RESET`` event.
"""

from __future__ import absolute_import, division, unicode_literals

from collections import OrderedDict

MUTATION_CREATE = "create"
MUTATION_INSERT = "insert"
MUTATION_MOVE = "move"
MUTATION_TEXT = "text"
MUTATION_ATTRIBUTE = "attribute"
MUTATION_REMOVE = "remove"
MUTATION_RESET = "reset"


class MutationEvent(object):
    """A single observed tree mutation.

    Only the fields relevant to ``type`` are populated; everything else is
    ``None``.  ``attrs``/``attributes`` mappings are copied, so an event is
    stable even if the parser keeps mutating the node afterwards.
    """

    __slots__ = ("sequence", "generation", "type", "node_id", "parent_id",
                 "before_id", "old_parent_id", "old_before_id", "kind", "name",
                 "namespace", "attributes", "text", "tail", "value", "reason",
                 "clone_of", "moved_from_id")

    def __init__(self, sequence, generation, type, node_id=None,
                 parent_id=None, before_id=None, old_parent_id=None,
                 old_before_id=None, kind=None, name=None, namespace=None,
                 attributes=None, text=None, tail=None, value=None,
                 reason=None, clone_of=None, moved_from_id=None):
        self.sequence = sequence
        self.generation = generation
        self.type = type
        self.node_id = node_id
        self.parent_id = parent_id
        self.before_id = before_id
        self.old_parent_id = old_parent_id
        self.old_before_id = old_before_id
        self.kind = kind
        self.name = name
        self.namespace = namespace
        self.attributes = attributes
        self.text = text
        self.tail = tail
        self.value = value
        self.reason = reason
        self.clone_of = clone_of
        # For MUTATION_TEXT emitted by reparentChildren: the id of the
        # container whose leading text was moved (it is cleared there).
        self.moved_from_id = moved_from_id

    def __repr__(self):
        fields = []
        for slot in ("sequence", "generation", "type", "node_id", "parent_id",
                     "before_id", "old_parent_id", "old_before_id", "kind",
                     "name", "namespace", "attributes", "text", "tail",
                     "value", "reason", "clone_of", "moved_from_id"):
            value = getattr(self, slot)
            if value is not None:
                fields.append("%s=%r" % (slot, value))
        return "MutationEvent(%s)" % ", ".join(fields)


class TreeMutationObserver(object):
    """Observer protocol.

    Subclasses (or plain duck-typed objects) receive every event through
    :meth:`on_event`.  ``on_event`` must not mutate the tree; raising from it
    aborts the parse.
    """

    def on_event(self, event):
        raise NotImplementedError

    def on_reset(self, event):
        """Called for :data:`MUTATION_RESET` as well as ``on_event``.

        The default does nothing; override it to drop any half-built state.
        """


class EventRecorder(TreeMutationObserver):
    """A minimal observer that keeps the events it is given.

    ``events`` only contains mutations of the *current* generation; earlier
    generations are dropped when :attr:`reparse_boundaries` receives a reset.
    Nothing is retained when this observer is not attached to a builder.
    """

    def __init__(self):
        self.events = []
        self.events_by_generation = OrderedDict()
        self.reparse_boundaries = []
        self._generation = 0

    def on_event(self, event):
        self.events_by_generation.setdefault(event.generation,
                                              []).append(event)

    def on_reset(self, event):
        self._generation = event.generation
        self.reparse_boundaries.append(event)
        # ``on_event`` has already appended the RESET event to this
        # generation's list; keep it so a replay can see the boundary even if
        # all generations are fed in order.
        self.events = self.events_by_generation.setdefault(event.generation,
                                                           [])

    def events_for(self, generation):
        return list(self.events_by_generation.get(generation, ()))

    @property
    def generation(self):
        return self._generation


class MutationRecorder(object):
    """Assigns stable ids and emits events for a single TreeBuilder.

    The recorder is created by the treebuilder only when an observer is
    supplied.  It holds a mapping from node objects to ids for as long as the
    generation lives; it does not keep a copy of the tree.
    """

    def __init__(self, observer):
        self.observer = observer
        self.generation = 0
        self.sequence = 0
        self._ids = {}
        self._nodes = {}
        self._created = set()
        # Optional callable installed by the treebuilder to wrap the mutation
        # methods of each node as it is created.
        self._node_wrapper = None
        # When True, wrapped node methods do not emit events; used by the
        # treebuilder helpers that emit events themselves before performing
        # the actual mutation.
        self._suspend = False

    def reset(self, reason=None):
        self.generation += 1
        self.sequence = 0
        self._ids = {}
        self._nodes = {}
        self._created = set()
        event = MutationEvent(self.sequence, self.generation,
                              MUTATION_RESET, reason=reason)
        self.sequence += 1
        self._emit(event, reset=True)

    def node_id(self, node):
        node_id = self._ids.get(id(node))
        if node_id is None:
            node_id = len(self._ids)
            self._ids[id(node)] = node_id
            self._nodes[node_id] = node
        return node_id

    def node(self, node_id):
        return self._nodes.get(node_id)

    def kind_of(self, node):
        explicit = getattr(node, "_mutation_kind", None)
        if explicit is not None:
            return explicit
        name = getattr(node, "name", None)
        if name == "DOCUMENT_ROOT":
            return "document"
        if name == "DOCUMENT_FRAGMENT":
            return "fragment"
        if name == "<!DOCTYPE>" or getattr(node, "publicId", None) is not None \
                or getattr(node, "systemId", None) is not None:
            return "doctype"
        if getattr(node, "data", None) is not None and name is None:
            return "comment"
        return "element"

    def create(self, node, kind=None, clone_of=None, name=None,
               text=None, attributes=None):
        node_id = self.node_id(node)
        if node_id in self._created:
            return node_id
        self._created.add(node_id)
        if self._node_wrapper is not None:
            self._node_wrapper(node)
        if kind is None:
            kind = self.kind_of(node)
        if name is None:
            name = getattr(node, "name", None)
        namespace = getattr(node, "namespace", None)
        if attributes is None:
            attributes = OrderedDict()
            if kind == "element":
                attributes = OrderedDict(_iter_attributes(node))
        else:
            attributes = OrderedDict(attributes)
        if text is None:
            if kind == "comment":
                text = getattr(node, "data", None) or \
                    getattr(node, "value", None)
            elif kind == "doctype":
                # The etree wrapper exposes the doctype name as element text.
                element = getattr(node, "_element", None)
                if element is not None and \
                        getattr(element, "text", None) is not None:
                    text = element.text
                    if name is None or name == "<!DOCTYPE>":
                        name = element.text
            if kind == "doctype":
                public_id = getattr(node, "publicId", None)
                system_id = getattr(node, "systemId", None)
                if public_id:
                    attributes["publicId"] = public_id
                if system_id:
                    attributes["systemId"] = system_id
        event = MutationEvent(self.sequence, self.generation,
                              MUTATION_CREATE, node_id=node_id, kind=kind,
                              name=name, namespace=namespace,
                              attributes=attributes, text=text,
                              clone_of=clone_of)
        self.sequence += 1
        self._emit(event)
        return node_id

    def insert(self, node, parent, before=None, move=False,
               old_parent=None, old_before=None):
        node_id = self.create(node)
        parent_id = self.create(parent)
        before_id = self.create(before) if before is not None else None
        if move:
            event = MutationEvent(self.sequence, self.generation,
                                  MUTATION_MOVE, node_id=node_id,
                                  parent_id=parent_id, before_id=before_id,
                                  old_parent_id=(self.create(old_parent)
                                                 if old_parent is not None
                                                 else None),
                                  old_before_id=(self.create(old_before)
                                                 if old_before is not None
                                                 else None))
        else:
            event = MutationEvent(self.sequence, self.generation,
                                  MUTATION_INSERT, node_id=node_id,
                                  parent_id=parent_id, before_id=before_id)
        self.sequence += 1
        self._emit(event)
        return node_id

    def text(self, node, data, before=None, moved_from=None):
        node_id = self.create(node)
        before_id = self.create(before) if before is not None else None
        moved_from_id = self.node_id(moved_from) \
            if moved_from is not None else None
        event = MutationEvent(self.sequence, self.generation,
                              MUTATION_TEXT, node_id=node_id,
                              parent_id=node_id, before_id=before_id,
                              text=data,
                              tail=before_id is not None,
                              moved_from_id=moved_from_id)
        self.sequence += 1
        self._emit(event)

    def attribute(self, node, name, value):
        node_id = self.create(node)
        event = MutationEvent(self.sequence, self.generation,
                              MUTATION_ATTRIBUTE, node_id=node_id,
                              name=name, value=value)
        self.sequence += 1
        self._emit(event)

    def remove(self, node, parent):
        node_id = self.create(node)
        parent_id = self.create(parent)
        event = MutationEvent(self.sequence, self.generation,
                              MUTATION_REMOVE, node_id=node_id,
                              parent_id=parent_id)
        self.sequence += 1
        self._emit(event)

    def _emit(self, event, reset=False):
        self.observer.on_event(event)
        if reset:
            on_reset = getattr(self.observer, "on_reset", None)
            if on_reset is not None:
                on_reset(event)


def _normalize_attr_key(key):
    """Normalize any backend/token attribute key to ``(namespace, name)``."""
    if isinstance(key, tuple):
        if len(key) == 3:
            # Token data uses ``(prefix, localName, namespace)``.
            return (key[2], key[1])
        return key
    if key.startswith("{"):
        namespace, name = key[1:].split("}", 1)
        return (namespace, name)
    return (None, key)


def _iter_attributes(node):
    attributes = getattr(node, "attributes", None)
    if attributes is None:
        return ()
    try:
        items = attributes.items()
    except AttributeError:
        return ()
    # DOM backends expose attributes through a NamedNodeMap with qualified
    # names; resolve namespaces from the attribute nodes when possible.
    dom_element = getattr(node, "element", None)
    for key, value in items:
        if dom_element is not None and isinstance(key, str) and \
                dom_element.hasAttribute(key):
            attr = dom_element.getAttributeNode(key)
            if attr.namespaceURI:
                yield ((attr.namespaceURI, attr.localName), value)
                continue
        yield (_normalize_attr_key(key), value)


class ReplayNode(object):
    """Logical tree node reconstructed from mutation events.

    Mirrors the ElementTree text model: a leading ``text`` plus an ordered
    list of children, each child having a ``tail``.  This is enough to
    represent any tree a built-in builder produces, including the DOM
    backends.
    """

    __slots__ = ("node_id", "kind", "name", "namespace", "attributes",
                 "text", "tail", "children", "parent")

    def __init__(self, node_id, kind, name=None, namespace=None,
                 attributes=None, text=None):
        self.node_id = node_id
        self.kind = kind
        self.name = name
        self.namespace = namespace
        self.attributes = attributes if attributes is not None else OrderedDict()
        self.text = text
        # Tail data belongs to the node itself, mirroring ElementTree: it
        # follows the node when the node is moved by foster parenting or the
        # adoption agency algorithm.
        self.tail = None
        self.children = []
        self.parent = None

    def append(self, child):
        child.parent = self
        self.children.append(child)

    def insert_before(self, child, before):
        child.parent = self
        index = self.children.index(before)
        self.children.insert(index, child)

    def remove(self, child):
        self.children.remove(child)
        child.parent = None

    def add_text(self, data, before_id, nodes):
        if before_id is None:
            self.text = (self.text or "") + data
        else:
            child = nodes[before_id]
            child.tail = (child.tail or "") + data


class ReplayTree(TreeMutationObserver):
    """Reference receiver: rebuilds the logical tree from the event stream.

    Use one instance per parse.  A :data:`MUTATION_RESET` event discards
    every node observed so far, so encoding-triggered reparses never get
    merged into the previous tree.  The tree is exposed through ``root`` once
    the first real node has been inserted.
    """

    def __init__(self):
        self.root = None
        self._nodes = {}
        self._generation = None

    def on_event(self, event):
        if event.type == MUTATION_RESET:
            self.on_reset(event)
        elif self._generation is None:
            # Adopt the generation of the first event seen.
            self._generation = event.generation
            self._dispatch(event)
        elif event.generation != self._generation:
            # Events from an abandoned generation (e.g. before an encoding
            # reparse) must not be merged into the current tree.
            return
        else:
            self._dispatch(event)

    def _dispatch(self, event):
        if event.type == MUTATION_CREATE:
            self._handle_create(event)
        elif event.type == MUTATION_INSERT:
            self._handle_insert(event)
        elif event.type == MUTATION_MOVE:
            self._handle_move(event)
        elif event.type == MUTATION_TEXT:
            self._handle_text(event)
        elif event.type == MUTATION_ATTRIBUTE:
            name = _normalize_attr_key(event.name)
            self._nodes[event.node_id].attributes[name] = event.value
        elif event.type == MUTATION_REMOVE:
            self._handle_remove(event)

    def on_reset(self, event):
        self._generation = event.generation
        self.root = None
        self._nodes = {}

    def _handle_create(self, event):
        attributes = OrderedDict(event.attributes) \
            if event.attributes is not None else OrderedDict()
        node = ReplayNode(event.node_id, event.kind, name=event.name,
                          namespace=event.namespace,
                          attributes=attributes, text=event.text)
        self._nodes[event.node_id] = node
        if event.kind == "fragment":
            # getFragment() moves the parsed children into a fresh fragment
            # root, which replaces the parse-time document root.
            self.root = node
        elif event.kind == "document" and self.root is None:
            self.root = node

    def _handle_insert(self, event):
        node = self._nodes[event.node_id]
        parent = self._nodes[event.parent_id]
        if node.parent is not None and node.parent is not parent:
            node.parent.remove(node)
        before = self._nodes.get(event.before_id)
        if before is None:
            parent.append(node)
        else:
            parent.insert_before(node, before)
        if self.root is None:
            self.root = parent if parent.kind in ("document", "fragment") \
                else node

    def _handle_move(self, event):
        node = self._nodes[event.node_id]
        old_parent = self._nodes.get(event.old_parent_id)
        if old_parent is None and node.parent is not None:
            old_parent = node.parent
        if old_parent is not None and node in old_parent.children:
            old_parent.remove(node)
        parent = self._nodes[event.parent_id]
        before = self._nodes.get(event.before_id)
        if before is None:
            parent.append(node)
        else:
            parent.insert_before(node, before)

    def _handle_text(self, event):
        if event.moved_from_id is not None:
            # The leading text was moved away from another container during a
            # reparent; clear it there so it is not duplicated.
            source = self._nodes.get(event.moved_from_id)
            if source is not None:
                source.text = None
        container = self._nodes[event.node_id]
        container.add_text(event.text, event.before_id, self._nodes)

    def _handle_remove(self, event):
        node = self._nodes[event.node_id]
        parent = self._nodes.get(event.parent_id)
        if parent is None and node.parent is not None:
            parent = node.parent
        if parent is not None and node in parent.children:
            parent.remove(node)

    def walk_tokens(self):
        """Yield walker-compatible tokens for the reconstructed tree."""
        if self.root is None:
            return
        for token in replay_tokens(self.root):
            yield token


def replay_tokens(root):
    """Serialize a :class:`ReplayNode` subtree into walker tokens."""
    if root.kind in ("document", "fragment"):
        if root.text:
            for token in _text_tokens(root.text):
                yield token
        for child in root.children:
            for token in replay_tokens(child):
                yield token
        return

    if root.kind == "doctype":
        yield {"type": "Doctype", "name": root.name or None,
               "publicId": root.attributes.get("publicId"),
               "systemId": root.attributes.get("systemId")}
        if root.tail:
            for token in _text_tokens(root.tail):
                yield token
        return

    if root.kind == "comment":
        yield {"type": "Comment", "data": root.text}
        if root.tail:
            for token in _text_tokens(root.tail):
                yield token
        return

    from .constants import voidElements, namespaces
    is_void = (not root.namespace or
               root.namespace == namespaces["html"]) and \
        root.name in voidElements
    if is_void:
        yield {"type": "EmptyTag", "name": root.name,
               "namespace": root.namespace,
               "data": OrderedDict(root.attributes)}
    else:
        yield {"type": "StartTag", "name": root.name,
               "namespace": root.namespace,
               "data": OrderedDict(root.attributes)}
        if root.text:
            for token in _text_tokens(root.text):
                yield token
        for child in root.children:
            for token in replay_tokens(child):
                yield token
        yield {"type": "EndTag", "name": root.name,
               "namespace": root.namespace}
    if root.tail:
        for token in _text_tokens(root.tail):
            yield token


def _text_tokens(data):
    from .treewalkers.base import TreeWalker
    walker = TreeWalker([])
    for token in walker.text(data):
        yield token
