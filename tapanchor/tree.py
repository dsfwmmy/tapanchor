"""TapAnchor · 无障碍树
把 uiautomator 吐出来的脏 XML 洗成可稳定查找的语义树。

清洗做四件事：
1. 丢掉 bounds 非法 / 面积为 0 的节点
2. 合并 bounds 完全相同的父子（保留有文字的那个）
3. 丢掉既无文字又无子节点的容器
4. 给每个节点算一个「稳定标识」，用于状态对比与路径回溯
"""
import re
import xml.etree.ElementTree as ET

BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


def parse_bounds(s):
    m = BOUNDS_RE.match(s or "")
    if not m:
        return None
    x1, y1, x2, y2 = (int(v) for v in m.groups())
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)


class Node:
    __slots__ = ("cls", "text", "desc", "rid", "pkg", "bounds",
                 "clickable", "enabled", "selected", "checked",
                 "index", "parent", "children", "depth")

    def __init__(self):
        self.cls = ""
        self.text = ""
        self.desc = ""
        self.rid = ""
        self.pkg = ""
        self.bounds = None
        self.clickable = False
        self.enabled = True
        self.selected = False
        self.checked = False
        self.index = 0
        self.parent = None
        self.children = []
        self.depth = 0

    @property
    def center(self):
        if not self.bounds:
            return None
        x1, y1, x2, y2 = self.bounds
        return ((x1 + x2) // 2, (y1 + y2) // 2)

    @property
    def area(self):
        if not self.bounds:
            return 0
        x1, y1, x2, y2 = self.bounds
        return (x2 - x1) * (y2 - y1)

    @property
    def label(self):
        return self.text or self.desc

    def ident(self):
        """稳定标识：优先 text，其次 desc，其次 resource-id 的末段。"""
        if self.text:
            return "T:" + self.text
        if self.desc:
            return "D:" + self.desc
        if self.rid:
            return "R:" + self.rid.rsplit("/", 1)[-1]
        return "C:" + self.cls.rsplit(".", 1)[-1]

    def clickable_ancestor(self):
        n = self
        while n is not None:
            if n.clickable and n.bounds:
                return n
            n = n.parent
        return self if self.bounds else None

    def __repr__(self):
        return "<Node %s %r %s>" % (self.cls.rsplit(".", 1)[-1], self.label, self.bounds)


class Tree:
    def __init__(self, root):
        self.root = root
        self.raw_count = 0

    @classmethod
    def parse(cls, xml_text):
        root_el = ET.fromstring(xml_text)
        raw = []

        def walk(el, depth, parent):
            n = Node()
            a = el.attrib
            n.cls = a.get("class", "")
            n.text = (a.get("text") or "").strip()
            n.desc = (a.get("content-desc") or "").strip()
            n.rid = a.get("resource-id", "")
            n.pkg = a.get("package", "")
            n.bounds = parse_bounds(a.get("bounds", ""))
            n.clickable = a.get("clickable") == "true"
            n.enabled = a.get("enabled", "true") == "true"
            n.selected = a.get("selected") == "true"
            n.checked = a.get("checked") == "true"
            n.index = int(a.get("index", "0") or 0)
            n.depth = depth
            n.parent = parent
            raw.append(n)
            for child_el in el:
                kid = walk(child_el, depth + 1, n)
                n.children.append(kid)
            return n

        root = walk(root_el, 0, None)
        t = cls(root)
        t.raw_count = len(raw)
        t._clean()
        return t

    def _clean(self):
        # 1 + 3：非法 bounds 与空容器
        def prune(n):
            n.children = [c for c in n.children if prune(c)]
            if n.bounds is None:
                return False
            if not n.label and not n.children and not n.clickable:
                return False
            return True

        self.root = self._drop_root(self.root, prune)
        # 2：合并同 bounds 父子
        self._merge_same_bounds(self.root)

    def _drop_root(self, root, prune):
        kids = [c for c in root.children if prune(c)]
        root.children = kids
        return root

    def _merge_same_bounds(self, n):
        kept = []
        for c in n.children:
            self._merge_same_bounds(c)
            if c.bounds and n.bounds and c.bounds == n.bounds and not c.label:
                # 子节点与父同框且自己没文字：把它的子节点提上来
                for gc in c.children:
                    gc.parent = n
                kept.extend(c.children)
            else:
                kept.append(c)
        n.children = kept

    def nodes(self):
        # 不包含 <hierarchy> 容器本身：它只是根壳，没有 bounds 也不该参与查找
        out = []
        stack = list(self.root.children)
        while stack:
            n = stack.pop()
            out.append(n)
            stack.extend(n.children)
        return out

    def find(self, text=None, desc=None, rid=None, cls=None, contains=False, limit=None):
        hits = []
        for n in self.nodes():
            if text is not None:
                if contains:
                    if text not in n.text:
                        continue
                elif n.text != text:
                    continue
            if desc is not None:
                if contains:
                    if desc not in n.desc:
                        continue
                elif n.desc != desc:
                    continue
            if rid is not None and rid not in n.rid:
                continue
            if cls is not None and cls not in n.cls:
                continue
            hits.append(n)
            if limit and len(hits) >= limit:
                break
        return hits

    def signature(self):
        """界面指纹：用于判断「动作之后世界变了没有」。"""
        items = []
        for n in self.nodes():
            if n.label and n.bounds:
                items.append((n.ident(), n.bounds))
        return frozenset(items)

    def describe(self):
        lines = []
        for n in self.nodes():
            if n.depth > 8:
                continue
            pad = "  " * n.depth
            flag = "*" if n.clickable else " "
            lines.append("%s%s %s %r %s" % (pad, flag, n.cls.rsplit(".", 1)[-1], n.label, n.bounds))
        return "\n".join(lines)
