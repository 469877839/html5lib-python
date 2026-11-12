from __future__ import absolute_import, division, unicode_literals
from six import text_type

from ..constants import scopingElements, tableInsertModeElements, namespaces
from .. import mutation

# The scope markers are inserted when entering object elements,
# marquees, table cells, and table captions, and are used to prevent formatting
# from "leaking" into tables, object elements, and marquees.
Marker = None

listElementsMap = {
    None: (frozenset(scopingElements), False),
    "button": (frozenset(scopingElements | {(namespaces["html"], "button")}), False),
    "list": (frozenset(scopingElements | {(namespaces["html"], "ol"),
                                          (namespaces["html"], "ul")}), False),
    "table": (frozenset([(namespaces["html"], "html"),
                         (namespaces["html"], "table")]), False),
    "select": (frozenset([(namespaces["html"], "optgroup"),
                          (namespaces["html"], "option")]), True)
}


def _leadingText(node):
    """Return the leading text of a node in a backend-independent way.

    etree-style backends store it as ``_element.text``; DOM-style backends
    keep it as a leading text child node.
    """
    element = getattr(node, "_element", None)
    if element is not None:
        return getattr(element, "text", None)
    dom_element = getattr(node, "element", None)
    if dom_element is not None:
        # DOM backends keep every text insert as a separate text node, so the
        # leading text may span several consecutive nodes.
        parts = []
        for child in dom_element.childNodes:
            if child.nodeType != child.TEXT_NODE:
                break
            parts.append(child.nodeValue or "")
        return "".join(parts) or None
    return None


class Node(object):
    """Represents an item in the tree"""
    def __init__(self, name):
        """Creates a Node

        :arg name: The tag name associated with the node

        """
        # The tag name associated with the node
        self.name = name
        # The parent of the current node (or None for the document node)
        self.parent = None
        # The value of the current node (applies to text nodes and comments)
        self.value = None
        # A dict holding name -> value pairs for attributes of the node
        self.attributes = {}
        # A list of child nodes of the current node. This must include all
        # elements but not necessarily other node types.
        self.childNodes = []
        # A list of miscellaneous flags that can be set on the node.
        self._flags = []

    def __str__(self):
        attributesStr = " ".join(["%s=\"%s\"" % (name, value)
                                  for name, value in
                                  self.attributes.items()])
        if attributesStr:
            return "<%s %s>" % (self.name, attributesStr)
        else:
            return "<%s>" % (self.name)

    def __repr__(self):
        return "<%s>" % (self.name)

    def appendChild(self, node):
        """Insert node as a child of the current node

        :arg node: the node to insert

        """
        raise NotImplementedError

    def insertText(self, data, insertBefore=None):
        """Insert data as text in the current node, positioned before the
        start of node insertBefore or to the end of the node's text.

        :arg data: the data to insert

        :arg insertBefore: True if you want to insert the text before the node
            and False if you want to insert it after the node

        """
        raise NotImplementedError

    def insertBefore(self, node, refNode):
        """Insert node as a child of the current node, before refNode in the
        list of child nodes. Raises ValueError if refNode is not a child of
        the current node

        :arg node: the node to insert

        :arg refNode: the child node to insert the node before

        """
        raise NotImplementedError

    def removeChild(self, node):
        """Remove node from the children of the current node

        :arg node: the child node to remove

        """
        raise NotImplementedError

    def reparentChildren(self, newParent):
        """Move all the children of the current node to newParent.
        This is needed so that trees that don't store text as nodes move the
        text in the correct way

        :arg newParent: the node to move all this node's children to

        """
        # XXX - should this method be made more general?
        for child in self.childNodes:
            newParent.appendChild(child)
        self.childNodes = []

    def cloneNode(self):
        """Return a shallow copy of the current node i.e. a node with the same
        name and attributes but with no parent or child nodes
        """
        raise NotImplementedError

    def hasContent(self):
        """Return true if the node has children or text, false otherwise
        """
        raise NotImplementedError


class ActiveFormattingElements(list):
    def append(self, node):
        """Append node to the end of the list."""
        equalCount = 0
        if node != Marker:
            for element in self[::-1]:
                if element == Marker:
                    break
                if self.nodesEqual(element, node):
                    equalCount += 1
                if equalCount == 3:
                    self.remove(element)
                    break
        list.append(self, node)

    def nodesEqual(self, node1, node2):
        if not node1.nameTuple == node2.nameTuple:
            return False

        if not node1.attributes == node2.attributes:
            return False

        return True


class TreeBuilder(object):
    """Base treebuilder implementation

    * documentClass - the class to use for the bottommost node of a document
    * elementClass - the class to use for HTML Elements
    * commentClass - the class to use for comments
    * doctypeClass - the class to use for doctypes

    """
    # pylint:disable=not-callable

    # Document class
    documentClass = None

    # The class to use for creating a node
    elementClass = None

    # The class to use for creating comments
    commentClass = None

    # The class to use for creating doctypes
    doctypeClass = None

    # Fragment class
    fragmentClass = None

    def __init__(self, namespaceHTMLElements, mutationObserver=None):
        """Create a TreeBuilder

        :arg namespaceHTMLElements: whether or not to namespace HTML elements

        :arg mutationObserver: an optional object implementing the
            :class:`html5lib.mutation.TreeMutationObserver` protocol.  When
            given, every structural change is reported to it; when omitted
            no event log is kept and the mutation code paths are not
            installed.

        """
        if namespaceHTMLElements:
            self.defaultNamespace = "http://www.w3.org/1999/xhtml"
        else:
            self.defaultNamespace = None
        self.mutationObserver = mutationObserver
        self.recorder = mutation.MutationRecorder(mutationObserver) \
            if mutationObserver is not None else None
        self._wrapMutationMethods()
        self.reset()

    def reset(self, reason="parser-reset"):
        self.openElements = []
        self.activeFormattingElements = ActiveFormattingElements()

        # XXX - rename these to headElement, formElement
        self.headPointer = None
        self.formPointer = None

        self.insertFromTable = False

        self.document = self.documentClass()
        if self.recorder is not None:
            self.recorder.reset(reason=reason)
            self.document._mutation_kind = "document"
            self.recorder.create(self.document)

    def elementInScope(self, target, variant=None):

        # If we pass a node in we match that. if we pass a string
        # match any node with that name
        exactNode = hasattr(target, "nameTuple")
        if not exactNode:
            if isinstance(target, text_type):
                target = (namespaces["html"], target)
            assert isinstance(target, tuple)

        listElements, invert = listElementsMap[variant]

        for node in reversed(self.openElements):
            if exactNode and node == target:
                return True
            elif not exactNode and node.nameTuple == target:
                return True
            elif (invert ^ (node.nameTuple in listElements)):
                return False

        assert False  # We should never reach this point

    def reconstructActiveFormattingElements(self):
        # Within this algorithm the order of steps described in the
        # specification is not quite the same as the order of steps in the
        # code. It should still do the same though.

        # Step 1: stop the algorithm when there's nothing to do.
        if not self.activeFormattingElements:
            return

        # Step 2 and step 3: we start with the last element. So i is -1.
        i = len(self.activeFormattingElements) - 1
        entry = self.activeFormattingElements[i]
        if entry == Marker or entry in self.openElements:
            return

        # Step 6
        while entry != Marker and entry not in self.openElements:
            if i == 0:
                # This will be reset to 0 below
                i = -1
                break
            i -= 1
            # Step 5: let entry be one earlier in the list.
            entry = self.activeFormattingElements[i]

        while True:
            # Step 7
            i += 1

            # Step 8
            entry = self.activeFormattingElements[i]
            clone = self._observeClone(entry)  # New copy of the attributes

            # Step 9
            element = self.insertElement({"type": "StartTag",
                                          "name": clone.name,
                                          "namespace": clone.namespace,
                                          "data": clone.attributes})

            # Step 10
            self.activeFormattingElements[i] = element

            # Step 11
            if element == self.activeFormattingElements[-1]:
                break

    def clearActiveFormattingElements(self):
        entry = self.activeFormattingElements.pop()
        while self.activeFormattingElements and entry != Marker:
            entry = self.activeFormattingElements.pop()

    def elementInActiveFormattingElements(self, name):
        """Check if an element exists between the end of the active
        formatting elements and the last marker. If it does, return it, else
        return false"""

        for item in self.activeFormattingElements[::-1]:
            # Check for Marker first because if it's a Marker it doesn't have a
            # name attribute.
            if item == Marker:
                break
            elif item.name == name:
                return item
        return False

    def insertRoot(self, token):
        element = self.createElement(token)
        self.openElements.append(element)
        self._observeInsert(self.document, element)

    def insertDoctype(self, token):
        name = token["name"]
        publicId = token["publicId"]
        systemId = token["systemId"]

        doctype = self.doctypeClass(name, publicId, systemId)
        if self.recorder is not None:
            doctype._mutation_kind = "doctype"
            attributes = {}
            if publicId:
                attributes["publicId"] = publicId
            if systemId:
                attributes["systemId"] = systemId
            self.recorder.create(doctype, kind="doctype", name=name,
                                 attributes=attributes)
        self._observeInsert(self.document, doctype)

    def insertComment(self, token, parent=None):
        if parent is None:
            parent = self.openElements[-1]
        comment = self.commentClass(token["data"])
        if self.recorder is not None:
            comment._mutation_kind = "comment"
            self.recorder.create(comment, kind="comment", text=token["data"])
        self._observeInsert(parent, comment)

    def createElement(self, token):
        """Create an element but don't insert it anywhere"""
        name = token["name"]
        namespace = token.get("namespace", self.defaultNamespace)
        element = self.elementClass(name, namespace)
        element.attributes = token["data"]
        element._mutation_kind = "element"
        if self.recorder is not None:
            self.recorder.create(element)
        return element

    def _getInsertFromTable(self):
        return self._insertFromTable

    def _setInsertFromTable(self, value):
        """Switch the function used to insert an element from the
        normal one to the misnested table one and back again"""
        self._insertFromTable = value
        if value:
            self.insertElement = self.insertElementTable
        else:
            self.insertElement = self.insertElementNormal

    insertFromTable = property(_getInsertFromTable, _setInsertFromTable)

    def insertElementNormal(self, token):
        name = token["name"]
        assert isinstance(name, text_type), "Element %s not unicode" % name
        namespace = token.get("namespace", self.defaultNamespace)
        element = self.elementClass(name, namespace)
        element.attributes = token["data"]
        element._mutation_kind = "element"
        if self.recorder is not None:
            self.recorder.create(element)
        self._observeInsert(self.openElements[-1], element)
        self.openElements.append(element)
        return element

    def insertElementTable(self, token):
        """Create an element and insert it into the tree"""
        element = self.createElement(token)
        if self.openElements[-1].name not in tableInsertModeElements:
            return self.insertElementNormal(token)
        else:
            # We should be in the InTable mode. This means we want to do
            # special magic element rearranging
            parent, insertBefore = self.getTableMisnestedNodePosition()
            self._observeInsert(parent, element, before=insertBefore)
            self.openElements.append(element)
        return element

    def insertText(self, data, parent=None):
        """Insert text data."""
        if parent is None:
            parent = self.openElements[-1]

        if (not self.insertFromTable or (self.insertFromTable and
                                         self.openElements[-1].name
                                         not in tableInsertModeElements)):
            # When the container already has children, etree-style backends
            # merge the data into the last child's tail rather than the
            # container's own text, so compute the same anchor for the event.
            anchor = parent.childNodes[-1] if parent.childNodes else None
            if self.recorder is not None:
                self.recorder.text(parent, data, before=anchor)
                self.recorder._suspend = True
                try:
                    parent.insertText(data)
                finally:
                    self.recorder._suspend = False
            else:
                parent.insertText(data)
        else:
            # We should be in the InTable mode. This means we want to do
            # special magic element rearranging
            parent, insertBefore = self.getTableMisnestedNodePosition()
            self._observeText(parent, data, insertBefore=insertBefore)

    def getTableMisnestedNodePosition(self):
        """Get the foster parent element, and sibling to insert before
        (or None) when inserting a misnested table node"""
        # The foster parent element is the one which comes before the most
        # recently opened table element
        # XXX - this is really inelegant
        lastTable = None
        fosterParent = None
        insertBefore = None
        for elm in self.openElements[::-1]:
            if elm.name == "table":
                lastTable = elm
                break
        if lastTable:
            # XXX - we should really check that this parent is actually a
            # node here
            if lastTable.parent:
                fosterParent = lastTable.parent
                insertBefore = lastTable
            else:
                fosterParent = self.openElements[
                    self.openElements.index(lastTable) - 1]
        else:
            fosterParent = self.openElements[0]
        return fosterParent, insertBefore

    def generateImpliedEndTags(self, exclude=None):
        name = self.openElements[-1].name
        # XXX td, th and tr are not actually needed
        if (name in frozenset(("dd", "dt", "li", "option", "optgroup", "p", "rp", "rt")) and
                name != exclude):
            self.openElements.pop()
            # XXX This is not entirely what the specification says. We should
            # investigate it more closely.
            self.generateImpliedEndTags(exclude)

    def getDocument(self):
        """Return the final tree"""
        return self.document

    def getFragment(self):
        """Return the final fragment"""
        # assert self.innerHTML
        fragment = self.fragmentClass()
        fragment._mutation_kind = "fragment"
        if self.recorder is not None:
            self.recorder.create(fragment)
        # reparentChildren is wrapped when observing, so every child moved
        # into the fragment root is reported as an insert into it.
        self.openElements[0].reparentChildren(fragment)
        return fragment

    # -- Mutation observation -------------------------------------------------
    #
    # Every structural change goes through one of the helpers below.  When no
    # observer was supplied the ``recorder`` attribute is None and the
    # wrappers installed by ``_wrapMutationMethods`` forward straight to the
    # original node methods, so an unobserved parse does not keep an event
    # log or even allocate event objects.

    def _wrapMutationMethods(self):
        if self.recorder is None:
            return
        recorder = self.recorder

        def wrapNode(node):
            if getattr(node, "_mutation_wrapped", False):
                return
            # Methods are installed per node instance, so a different
            # TreeBuilder (observed or not) never sees these wrappers.
            try:
                node._mutation_wrapped = True
            except (AttributeError, TypeError):
                return
            for methodName in ("appendChild", "insertBefore", "removeChild",
                               "insertText", "reparentChildren"):
                original = getattr(type(node), methodName, None)
                if original is None:
                    continue

                def makeWrapper(node=node, original=original,
                                methodName=methodName):
                    # Installed as an instance attribute, so this is not a
                    # bound method: ``node`` comes from the closure.
                    def wrapper(*args, **kwargs):
                        if recorder._suspend:
                            return original(node, *args, **kwargs)
                        if methodName == "appendChild":
                            child = args[0]
                            if getattr(child, "parent", None) is not None:
                                siblings = child.parent.childNodes
                                index = siblings.index(child) \
                                    if child in siblings else None
                                old_before = siblings[index + 1] \
                                    if index is not None and \
                                    index + 1 < len(siblings) else None
                                recorder.insert(child, node, move=True,
                                                old_parent=child.parent,
                                                old_before=old_before)
                            else:
                                recorder.insert(child, node)
                        elif methodName == "insertBefore":
                            child = args[0]
                            if getattr(child, "parent", None) is not None:
                                siblings = child.parent.childNodes
                                index = siblings.index(child) \
                                    if child in siblings else None
                                old_before = siblings[index + 1] \
                                    if index is not None and \
                                    index + 1 < len(siblings) else None
                                recorder.insert(child, node, before=args[1],
                                                move=True,
                                                old_parent=child.parent,
                                                old_before=old_before)
                            else:
                                recorder.insert(child, node, before=args[1])
                        elif methodName == "removeChild":
                            recorder.remove(args[0], node)
                        elif methodName == "insertText":
                            insertBefore = args[1] if len(args) > 1 else None
                            # Anchor the text to the child whose tail it is
                            # merged into (None = the container's own text).
                            if insertBefore is None:
                                anchor = (node.childNodes[-1]
                                          if node.childNodes else None)
                            else:
                                index = node.childNodes.index(insertBefore)
                                anchor = (node.childNodes[index - 1]
                                          if index > 0 else None)
                            recorder.text(node, args[0], before=anchor)
                        elif methodName == "reparentChildren":
                            new_parent = args[0]
                            # Only the leading text has to be reported as a
                            # move: tails travel with the child elements that
                            # are moved, and the original performs that move.
                            leading_text = _leadingText(node)
                            if leading_text:
                                anchor = (new_parent.childNodes[-1]
                                          if new_parent.childNodes else None)
                                recorder.text(new_parent, leading_text,
                                              before=anchor, moved_from=node)
                            for child in list(node.childNodes):
                                recorder.insert(child, new_parent)
                            # The original reparents the children itself; make
                            # sure the nested node mutations stay silent.
                            recorder._suspend = True
                            try:
                                return original(node, *args, **kwargs)
                            finally:
                                recorder._suspend = False
                        return original(node, *args, **kwargs)

                    wrapper.__name__ = methodName
                    return wrapper

                try:
                    setattr(node, methodName, makeWrapper())
                except (AttributeError, TypeError):
                    # Backends that do not allow per-instance attributes must
                    # go through the TreeBuilder helpers instead.
                    pass

        recorder._node_wrapper = wrapNode

    def _observeInsert(self, parent, node, before=None):
        """Emit an insert (or move) event and perform the real insertion."""
        if self.recorder is not None and getattr(node, "parent", None) is not None:
            return self._observeMove(parent, node, before=before)
        if self.recorder is not None:
            self.recorder.insert(node, parent, before=before)
            self.recorder._suspend = True
            try:
                if before is None:
                    parent.appendChild(node)
                else:
                    parent.insertBefore(node, before)
            finally:
                self.recorder._suspend = False
        elif before is None:
            parent.appendChild(node)
        else:
            parent.insertBefore(node, before)

    def _observeMove(self, parent, node, before=None, old_parent=None,
                     old_before=None):
        """Report and perform a reparent operation as a single move event."""
        if old_parent is None:
            old_parent = getattr(node, "parent", None)
        if old_parent is None:
            return self._observeInsert(parent, node, before=before)
        siblings = getattr(old_parent, "childNodes", [])
        if old_parent is parent:
            if before is None:
                if siblings and siblings[-1] is node:
                    # Already in place; the remove/append would be a no-op.
                    return
            elif node in siblings and before in siblings and \
                    siblings.index(node) == siblings.index(before) - 1:
                # Already directly before the reference sibling.
                return
        if self.recorder is not None:
            if old_before is None:
                siblings = getattr(old_parent, "childNodes", [])
                index = siblings.index(node) if node in siblings else None
                if index is not None and index + 1 < len(siblings):
                    old_before = siblings[index + 1]
            self.recorder.insert(node, parent, before=before, move=True,
                                 old_parent=old_parent,
                                 old_before=old_before)
            self.recorder._suspend = True
            try:
                if node in getattr(old_parent, "childNodes", []):
                    old_parent.removeChild(node)
                if before is None:
                    parent.appendChild(node)
                else:
                    parent.insertBefore(node, before)
            finally:
                self.recorder._suspend = False
        else:
            if node in getattr(old_parent, "childNodes", []):
                old_parent.removeChild(node)
            if before is None:
                parent.appendChild(node)
            else:
                parent.insertBefore(node, before)

    def _observeRemove(self, parent, node):
        if self.recorder is not None:
            self.recorder.remove(node, parent)
            self.recorder._suspend = True
            try:
                parent.removeChild(node)
            finally:
                self.recorder._suspend = False
        else:
            parent.removeChild(node)

    def _observeText(self, parent, data, insertBefore=None):
        """Insert text into ``parent`` before ``insertBefore``.

        The event anchor is the child whose tail receives the data; when the
        text lands before the first child (or the parent has no children) it
        becomes the parent's own leading text.
        """
        anchor = None
        if insertBefore is not None:
            siblings = parent.childNodes
            index = siblings.index(insertBefore)
            if index > 0:
                anchor = siblings[index - 1]
        elif parent.childNodes:
            anchor = parent.childNodes[-1]
        if self.recorder is not None:
            self.recorder.text(parent, data, before=anchor)
            self.recorder._suspend = True
            try:
                parent.insertText(data, insertBefore)
            finally:
                self.recorder._suspend = False
        else:
            parent.insertText(data, insertBefore)

    def _observeClone(self, node):
        """Clone ``node`` and report the clone as a new logical node."""
        clone = node.cloneNode()
        clone._mutation_kind = "element"
        if self.recorder is not None:
            original_id = self.recorder.node_id(node)
            self.recorder.create(clone, clone_of=original_id)
        return clone

    def testSerializer(self, node):
        """Serialize the subtree of node in the format required by unit tests

        :arg node: the node from which to start serializing

        """
        raise NotImplementedError
