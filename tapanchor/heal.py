"""TapAnchor · 选择器自愈（半自动）

设计立场：**不做全自动自愈。**
自动改选择器一旦改错，用户对工具的信任会一次性崩掉，而修复成本高于收益。
这里只做三件事：
  1. 失败时从现场快照里算出「最可能的 3 个目标」
  2. 人点一下选哪个（或命令行 --pick N）
  3. 选中结果存进库，下次同一步优先用它

候选打分不依赖任何模型：文本相似度 + 结构可达性 + 可点击性。
离线、零成本、结果可解释——这三点比「聪明」重要。
"""
import difflib
import json
import os
import time

DB_DEFAULT = "selectors.json"


# ---------- 库 ----------

def load_db(path=DB_DEFAULT):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_db(db, path=DB_DEFAULT):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=1)
    return path


def step_key(index, label):
    return "%d:%s" % (index, (label or "")[:40])


def get_learned(db, flow, key):
    if not flow:
        return None
    return db.get(flow, {}).get(key)


def set_learned(db, flow, key, selector):
    if not flow:
        raise ValueError("没有 flow 名，无法写进选择器库")
    db.setdefault(flow, {})[key] = dict(selector, learned_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    return db


# ---------- 候选生成 ----------

def selector_of(node):
    """把一个节点压成可复用的选择器。优先选最稳的定位方式。"""
    if node.text:
        return {"kind": "text", "value": node.text}
    if node.desc:
        return {"kind": "desc", "value": node.desc}
    if node.rid:
        return {"kind": "rid", "value": node.rid}
    return {"kind": "cls", "value": node.cls}


def _similarity(want, s):
    if not s:
        return 0.0
    r = difflib.SequenceMatcher(None, want, s).ratio()
    if want and want in s:
        r = max(r, 0.85)
    elif want and s in want:
        r = max(r, 0.78)
    return r


def score_node(node, want):
    """综合分数：文本相似度为主，可点击性与有实际尺寸为加分项。"""
    best = 0.0
    for s in (node.text, node.desc, node.rid.rsplit("/", 1)[-1]):
        best = max(best, _similarity(want, s))
    if best <= 0:
        return 0.0
    if node.clickable or node.clickable_ancestor() is not node:
        best += 0.05
    if node.area > 400:
        best += 0.03
    return min(best, 1.0)


def candidates(tree, want, limit=3, threshold=0.45):
    """返回 [(score, selector, node)]，按分数降序。"""
    if not want:
        return []
    scored = []
    seen = set()
    for n in tree.nodes():
        s = score_node(n, want)
        if s < threshold:
            continue
        sel = selector_of(n)
        sig = (sel["kind"], sel["value"])
        if sig in seen:
            continue
        seen.add(sig)
        scored.append((round(s, 3), sel, n))
    scored.sort(key=lambda x: -x[0])
    return scored[:limit]


# ---------- 现场文件 ----------

def write_heal_file(run_dir, flow, key, want, cands, snapshot_name=None):
    payload = {
        "flow": flow,
        "step": key,
        "wanted": want,
        "snapshot": snapshot_name,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "candidates": [
            {"pick": i + 1, "score": s, "selector": sel,
             "label": (n.label or n.cls.rsplit(".", 1)[-1]),
             "bounds": list(n.bounds) if n.bounds else None,
             "clickable": n.clickable}
            for i, (s, sel, n) in enumerate(cands)
        ],
    }
    path = os.path.join(run_dir, "heal.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    return path, payload


def render_candidates(payload):
    lines = []
    lines.append("第 %s 步没点中，下面是现场里最像的 %d 个目标："
                 % (payload.get("step"), len(payload.get("candidates", []))))
    for c in payload.get("candidates", []):
        sel = c["selector"]
        lines.append("  [%d] %-5s %-6s %r   相似度 %.2f  bounds=%s%s"
                     % (c["pick"], sel["kind"], "", sel["value"], c["score"],
                        c["bounds"], "  可点" if c["clickable"] else ""))
    lines.append("选一个写回：python -m tapanchor heal <run_dir> --pick N")
    return "\n".join(lines)
