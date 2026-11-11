from __future__ import absolute_import, division, unicode_literals
from six import text_type

from ..constants import scopingElements, tableInsertModeElements, namespaces

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


class WorkCounter(object):
    """Counters for the linear scans the tree construction algorithm does.

    Every counter records work done on an actual data structure (the stack
    of open elements or the list of active formatting elements), never wall
    clock time. The counters are only updated when a WorkCounter instance is
    attached to a tree builder; otherwise parsing runs without any extra
    bookkeeping.
    """

    __slots__ = ("scope_probes", "stack_contains", "stack_index",
                 "stack_remove", "afe_probes", "afe_index", "afe_contains",
                 "afe_remove", "foster_probes", "agency_probes",
                 "stack_snapshot")

    def __init__(self):
        self.reset()

    def reset(self):
        # Element comparisons performed while walking the stack of open
        # elements backwards to determine scope.
        self.scope_probes = 0
        # Element comparisons performed by membership tests
        # (``element in openElements``) and by index lookups / removals on
        # the stack of open elements.
        self.stack_contains = 0
        self.stack_index = 0
        self.stack_remove = 0
        # Element comparisons performed while walking the list of active
        # formatting elements, plus its membership, index and removal work.
        self.afe_probes = 0
        self.afe_index = 0
        self.afe_contains = 0
        self.afe_remove = 0
        # Element comparisons performed while searching the stack for the
        # most recently opened table (foster parent positioning).
        self.foster_probes = 0
        # Element comparisons performed by the adoption agency algorithm
        # while walking the stack of open elements.
        self.agency_probes = 0
        # Elements included in a one-shot identity snapshot of the stack of
        # open elements, used to answer several membership questions without
        # rescanning the stack once per active formatting element.
        self.stack_snapshot = 0

    def as_dict(self):
        return dict((name, getattr(self, name)) for name in self.__slots__)

    @property
    def total(self):
        return sum(getattr(self, name) for name in self.__slots__)


class ActiveFormattingElements(list):
    def __init__(self, work=None):
        super(ActiveFormattingElements, self).__init__()
        self._work = work

    def append(self, node):
        """Append node to the end of the list."""
        equalCount = 0
        if node != Marker:
            for element in self[::-1]:
                if self._work is not None:
                    self._work.afe_probes += 1
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

    def appendChecked(self, node):
        """Append a node whose Noah's Ark clause check has already run.

        Callers that already scanned the list for equal elements (e.g. the
        tree construction algorithm before inserting a formatting element)
        can use this to avoid scanning the list a second time.
        """
        list.append(self, node)


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

    def __init__(self, namespaceHTMLElements):
        """Create a TreeBuilder

        :arg namespaceHTMLElements: whether or not to namespace HTML elements

        """
        if namespaceHTMLElements:
            self.defaultNamespace = "http://www.w3.org/1999/xhtml"
        else:
            self.defaultNamespace = None
        # Attach a WorkCounter instance here to collect linear-scan counters;
        # None keeps the default zero-overhead behaviour.
        self.work = None
        self.reset()

    def reset(self):
        self.openElements = []
        self.activeFormattingElements = ActiveFormattingElements(self.work)

        # XXX - rename these to headElement, formElement
        self.headPointer = None
        self.formPointer = None

        self.insertFromTable = False

        self.document = self.documentClass()

    def elementInScope(self, target, variant=None):

        # If we pass a node in we match that. if we pass a string
        # match any node with that name
        exactNode = hasattr(target, "nameTuple")
        if not exactNode:
            if isinstance(target, text_type):
                target = (namespaces["html"], target)
            assert isinstance(target, tuple)

        listElements, invert = listElementsMap[variant]

        work = self.work
        for node in reversed(self.openElements):
            if work is not None:
                work.scope_probes += 1
            if exactNode and node == target:
                return True
            elif not exactNode and node.nameTuple == target:
                return True
            elif (invert ^ (node.nameTuple in listElements)):
                return False

        assert False  # We should never reach this point

    def stackContains(self, node):
        """Membership test for the stack of open elements.

        The search runs from the top of the stack downwards: active
        formatting elements being looked up are always currently open, so
        they are typically close to the top. The result is identical to a
        forward membership test.
        """
        work = self.work
        stack = self.openElements
        if work is None:
            for item in reversed(stack):
                if item is node:
                    return True
            return False
        for item in reversed(stack):
            work.stack_contains += 1
            if item is node:
                return True
        return False

    def stackIndex(self, node):
        """Index lookup for a node known to be on the stack of open elements."""
        index = self.stackFindIndex(node)
        if index == -1:
            raise ValueError("node is not in the stack of open elements")
        return index

    def stackFindIndex(self, node):
        """Index of a node on the stack of open elements, or -1 if absent."""
        work = self.work
        stack = self.openElements
        index = len(stack) - 1
        if work is None:
            while index >= 0:
                if stack[index] is node:
                    return index
                index -= 1
            return -1
        while index >= 0:
            work.stack_index += 1
            if stack[index] is node:
                return index
            index -= 1
        return -1

    def stackRemove(self, node):
        """Identity-based removal from the stack of open elements."""
        work = self.work
        stack = self.openElements
        index = len(stack) - 1
        while index >= 0:
            if work is not None:
                work.stack_remove += 1
            if stack[index] is node:
                del stack[index]
                return
            index -= 1
        raise ValueError("node is not in the stack of open elements")

    def activeFormattingContains(self, node):
        """Membership test for the list of active formatting elements."""
        work = self.work
        for item in reversed(self.activeFormattingElements):
            if work is not None:
                work.afe_contains += 1
            if item is node:
                return True
        return False

    def activeFormattingIndex(self, node):
        """Index lookup on the list of active formatting elements."""
        work = self.work
        elements = self.activeFormattingElements
        index = len(elements) - 1
        if work is None:
            while index >= 0:
                if elements[index] is node:
                    return index
                index -= 1
        else:
            while index >= 0:
                work.afe_index += 1
                if elements[index] is node:
                    return index
                index -= 1
        raise ValueError("node is not in the list of active formatting elements")

    def activeFormattingRemove(self, node):
        """Identity-based removal from the list of active formatting elements."""
        work = self.work
        elements = self.activeFormattingElements
        index = len(elements) - 1
        while index >= 0:
            if work is not None:
                work.afe_remove += 1
            if elements[index] is node:
                del elements[index]
                return
            index -= 1
        raise ValueError("node is not in the list of active formatting elements")

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
        if entry == Marker or self.stackContains(entry):
            return

        # The stack of open elements is not mutated while walking back
        # through the list of active formatting elements, so when more
        # than one entry may have to be checked, one identity snapshot can
        # answer all the membership questions instead of rescanning the
        # stack once per entry.
        work = self.work
        if len(self.activeFormattingElements) > 1:
            if work is not None:
                work.stack_snapshot += len(self.openElements)
            openElementsSnapshot = set(self.openElements)
            entryOnStack = openElementsSnapshot.__contains__
        else:
            entryOnStack = self.stackContains

        # Step 6
        while entry != Marker and not entryOnStack(entry):
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
            clone = entry.cloneNode()  # Mainly to get a new copy of the attributes

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
            if self.work is not None:
                self.work.afe_probes += 1
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
        self.document.appendChild(element)

    def insertDoctype(self, token):
        name = token["name"]
        publicId = token["publicId"]
        systemId = token["systemId"]

        doctype = self.doctypeClass(name, publicId, systemId)
        self.document.appendChild(doctype)

    def insertComment(self, token, parent=None):
        if parent is None:
            parent = self.openElements[-1]
        parent.appendChild(self.commentClass(token["data"]))

    def createElement(self, token):
        """Create an element but don't insert it anywhere"""
        name = token["name"]
        namespace = token.get("namespace", self.defaultNamespace)
        element = self.elementClass(name, namespace)
        element.attributes = token["data"]
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
        self.openElements[-1].appendChild(element)
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
            if insertBefore is None:
                parent.appendChild(element)
            else:
                parent.insertBefore(element, insertBefore)
            self.openElements.append(element)
        return element

    def insertText(self, data, parent=None):
        """Insert text data."""
        if parent is None:
            parent = self.openElements[-1]

        if (not self.insertFromTable or (self.insertFromTable and
                                         self.openElements[-1].name
                                         not in tableInsertModeElements)):
            parent.insertText(data)
        else:
            # We should be in the InTable mode. This means we want to do
            # special magic element rearranging
            parent, insertBefore = self.getTableMisnestedNodePosition()
            parent.insertText(data, insertBefore)

    def getTableMisnestedNodePosition(self):
        """Get the foster parent element, and sibling to insert before
        (or None) when inserting a misnested table node"""
        # The foster parent element is the one which comes before the most
        # recently opened table element
        # XXX - this is really inelegant
        lastTable = None
        fosterParent = None
        insertBefore = None
        work = self.work
        for elm in self.openElements[::-1]:
            if work is not None:
                work.foster_probes += 1
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
                    self.stackIndex(lastTable) - 1]
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
        self.openElements[0].reparentChildren(fragment)
        return fragment

    def testSerializer(self, node):
        """Serialize the subtree of node in the format required by unit tests

        :arg node: the node from which to start serializing

        """
        raise NotImplementedError
