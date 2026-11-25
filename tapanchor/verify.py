"""TapAnchor · 状态断言
存在的意义只有一个：区分「点击事件发出去了」和「界面真的变了」。

adapter 出来的一堆自动化框架，全部只保证前者。
本模块提供后者，作为每个动作的验收条件。
"""


def _texts(tree):
    return {n.label for n in tree.nodes() if n.label}


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def evaluate(tree, spec, before=None):
    """返回 (ok, detail)。spec 内多条断言需全部满足。"""
    if not spec:
        return True, "no-assert"

    texts = _texts(tree)
    checks = []

    for want in _as_list(spec.get("text_exists")):
        checks.append((want in texts, "text_exists %r" % want))

    for want in _as_list(spec.get("text_gone")):
        checks.append((want not in texts, "text_gone %r" % want))

    for want in _as_list(spec.get("any_text")):
        checks.append((want in texts, "any_text %r" % want))

    if "pkg" in spec:
        pkg = spec["pkg"]
        hit = any(n.pkg == pkg for n in tree.nodes())
        checks.append((hit, "pkg %r" % pkg))

    if "changed" in spec and before is not None:
        diff = tree.signature() != before
        checks.append((diff == bool(spec["changed"]), "changed=%s" % spec["changed"]))

    cnt = spec.get("count")
    if isinstance(cnt, dict):
        sel = {k: v for k, v in cnt.items() if k in ("text", "desc", "rid", "cls")}
        hits = tree.find(contains=cnt.get("contains", False), **sel)
        lo = cnt.get("min", 0)
        hi = cnt.get("max", 10 ** 6)
        checks.append((lo <= len(hits) <= hi,
                       "count(%s)=%d in [%d,%d]" % (sel, len(hits), lo, hi)))

    ok = all(c[0] for c in checks)
    return ok, "; ".join(c[1] for c in checks)
