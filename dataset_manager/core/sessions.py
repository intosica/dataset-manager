"""Saved scraping sessions: a tree of folders and sessions, persisted as sessions.json (no GUI code here).

A session is a named bag of Collect-page parameters. Exactly one session is *active*; edits on the Collect page
are saved into it, picking another session loads that one.
"""
from __future__ import annotations

import copy
import json
import os
import uuid
from dataclasses import dataclass, field

from . import paths

FILE = "sessions.json"


@dataclass(eq=False)       # identity comparison: `node in children` must not deep-compare trees
class Node:
    kind: str                                   # "session" | "folder"
    name: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    params: dict = field(default_factory=dict)  # sessions only
    auto_name: bool = False                     # session name follows the query until the user renames it
    expanded: bool = True                       # folders only
    children: list = field(default_factory=list)

    def to_json(self) -> dict:
        d = {"id": self.id, "type": self.kind, "name": self.name}
        if self.kind == "folder":
            d.update(expanded=self.expanded, children=[c.to_json() for c in self.children])
        else:
            d.update(params=self.params, auto_name=self.auto_name)
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Node":
        n = cls(d.get("type", "session"), str(d.get("name", "Untitled")), str(d.get("id") or uuid.uuid4().hex[:12]))
        if n.kind == "folder":
            n.expanded = bool(d.get("expanded", True))
            n.children = [cls.from_json(c) for c in d.get("children", [])]
        else:
            n.params = d.get("params") or {}
            n.auto_name = bool(d.get("auto_name", False))
        return n


class SessionStore:
    def __init__(self, path: str | None = None):
        self.path = path or str(paths.config_dir() / FILE)
        self.roots: list[Node] = []
        self.active_id = ""
        self.load()

    # ---- lookup ----
    def walk(self, nodes=None):
        for n in (self.roots if nodes is None else nodes):
            yield n
            yield from self.walk(n.children)

    def find(self, nid: str) -> Node | None:
        return next((n for n in self.walk() if n.id == nid), None)

    def siblings(self, node: Node) -> list:
        """The list that holds `node`."""
        for n in self.walk():
            if node in n.children:
                return n.children
        return self.roots

    def parent(self, node: Node) -> Node | None:
        return next((n for n in self.walk() if node in n.children), None)

    @property
    def active(self) -> Node | None:
        n = self.find(self.active_id)
        return n if n is not None and n.kind == "session" else None

    # ---- edit ----
    def add_session(self, name: str = "New session", params: dict | None = None, near: Node | None = None,
                    auto_name: bool = False) -> Node:
        node = Node("session", name, params=copy.deepcopy(params or {}), auto_name=auto_name)
        self._insert(node, near)
        self.active_id = node.id
        return node

    def add_folder(self, name: str = "New folder", near: Node | None = None) -> Node:
        node = Node("folder", name)
        self._insert(node, near)
        return node

    def _insert(self, node: Node, near: Node | None) -> None:
        if near is None:
            self.roots.append(node)
        elif near.kind == "folder":
            near.children.append(node)
            near.expanded = True
        else:
            lst = self.siblings(near)
            lst.insert(lst.index(near) + 1, node)

    def duplicate(self, node: Node) -> Node:
        if node.kind == "folder":
            raise ValueError("folders cannot be duplicated")
        return self.add_session(node.name + " (copy)", node.params, near=node)

    def delete(self, node: Node) -> None:
        """Deletes the node and, for folders, everything inside. The active session moves to a neighbour."""
        ids = {n.id for n in self.walk([node])}
        self.siblings(node).remove(node)
        if self.active_id in ids:
            self.active_id = next((n.id for n in self.walk() if n.kind == "session"), "")

    def move(self, node: Node, target: Node | None, into: bool) -> bool:
        """Move `node` into folder `target` (into=True), or just before `target` (into=False); target None = end of root."""
        if target is not None and (target is node or target.id in {n.id for n in self.walk([node])}):
            return False                        # cannot drop onto itself or into its own subtree
        if into and (target is None or target.kind != "folder"):
            return False
        self.siblings(node).remove(node)
        if target is None:
            self.roots.append(node)
        elif into:
            target.children.append(node)
            target.expanded = True
        else:
            lst = self.siblings(target)
            lst.insert(lst.index(target), node)
        return True

    def sort(self) -> None:
        """Folders first, then sessions, A–Z at every level."""
        def key(n: Node):
            return (n.kind != "folder", n.name.casefold())
        def go(lst):
            lst.sort(key=key)
            for n in lst:
                go(n.children)
        go(self.roots)

    def update_active(self, params: dict) -> None:
        n = self.active
        if n is None:
            return
        n.params = copy.deepcopy(params)
        if n.auto_name:
            n.name = (params.get("query") or "").strip()[:60] or "New session"

    # ---- persistence ----
    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.roots = [Node.from_json(d) for d in data.get("sessions", [])]
            self.active_id = data.get("active", "")
        except (OSError, ValueError, AttributeError):
            self.roots, self.active_id = [], ""
        if self.active is None:
            self.active_id = next((n.id for n in self.walk() if n.kind == "session"), "")

    def save(self) -> None:
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 2, "active": self.active_id, "sessions": [n.to_json() for n in self.roots]},
                          f, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            pass


if __name__ == "__main__":
    import tempfile
    p = os.path.join(tempfile.mkdtemp(), "s.json")
    st = SessionStore(p)
    a = st.add_session("b", {"query": "x"}, auto_name=True)
    f = st.add_folder("F")
    c = st.add_session("a", {"query": "y"}, near=f)         # inside folder
    assert st.parent(c) is f and st.active is c
    st.update_active({"query": "new"})
    assert c.name == "a"                                    # renamed sessions keep their name
    a.auto_name = True
    st.active_id = a.id
    st.update_active({"query": "renamed by query"})
    assert a.name == "renamed by query"
    assert not st.move(f, c, into=True)                     # folder into its own child: refused
    assert st.move(a, f, into=True) and st.parent(a) is f
    st.sort()
    assert [n.name for n in f.children] == ["a", "renamed by query"]
    st.save()
    st2 = SessionStore(p)
    assert st2.active_id == a.id and st2.find(c.id).params == {"query": "new"}
    st2.delete(st2.find(f.id))
    assert st2.roots == [] and st2.active_id == ""
    print("ok")
