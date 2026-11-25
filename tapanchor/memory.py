"""TapAnchor · 记忆库

每次点击都在丢掉一次学习机会。这个模块把丢掉的东西存下来：

1. 页面身份 —— 这屏是「设置首页」还是「微信聊天窗」
2. 动作效果 —— 在这一页点这个动作，通常会发生什么
3. 样本统计 —— 这个判断被验证过几次、错了几次

设计立场和自愈一致：**不用模型，可解释，置信度不够就退回原路径。**
记忆只用来「提前知道该验什么」，不允许覆盖显式写下的 verify，也不允许在样本不足时自作主张。
"""
import json
import os
import time

DB_DEFAULT = "memory.json"

MIN_SUPPORT = 3        # 至少成功这么多次才启用这条记忆
MIN_CONFIDENCE = 0.8   # 成功率门槛
MATCH_THRESHOLD = 0.75 # 页面相似度门槛
IDEMPOTENT_RATIO = 0.7 # 「多数时候没变化」的比例，且失败多于成功，才认定是幂等动作


def _zone(v, size, buckets=8):
    """把坐标分桶，抵消分辨率与小幅位移。"""
    if size <= 0:
        return 0
    return min(buckets - 1, int(v * buckets / size))


def page_signature(tree):
    """页面结构签名：只看骨架（类名 / 是否可点击 / 在屏内哪个区），不看文字。

    文字会变（文案更新、数据刷新），骨架不会。所以认得出来。
    """
    nodes = [n for n in tree.nodes() if n.bounds]
    if not nodes:
        return frozenset()
    w = max(n.bounds[2] for n in nodes)
    h = max(n.bounds[3] for n in nodes)
    sig = set()
    for n in nodes:
        cls = n.cls.rsplit(".", 1)[-1]
        x1, y1, _, _ = n.bounds
        sig.add((cls, bool(n.clickable), _zone(x1, w), _zone(y1, h)))
    return frozenset(sig)


def jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def action_key(step):
    """动作的规范形式，做记忆的键。"""
    for k in ("tap", "app", "key", "swipe", "text", "xy"):
        if k in step:
            v = step[k]
            if k == "swipe":
                return "swipe"
            if k == "xy":
                return "xy"
            return "%s:%s" % (k, v)
    return "other"


class Memory:
    def __init__(self, path=DB_DEFAULT, enabled=True):
        self.path = path
        self.enabled = enabled
        self.data = self._load() if enabled else {"pages": {}, "actions": {}}
        self.dirty = False

    # ---------- 存取 ----------

    def _load(self):
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                d.setdefault("pages", {})
                d.setdefault("actions", {})
                return d
            except Exception:
                pass
        return {"pages": {}, "actions": {}}

    def save(self):
        if not self.enabled or not self.dirty:
            return None
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        self.dirty = False
        return self.path

    # ---------- 页面身份 ----------

    def identify(self, sig):
        """返回 (page_id, similarity)。认不出返回 (None, 最高相似度)。"""
        if not self.enabled or not sig:
            return None, 0.0
        best, best_sim = None, 0.0
        for pid, rec in self.data["pages"].items():
            stored = frozenset(tuple(x) for x in rec.get("sig", []))
            s = jaccard(sig, stored)
            if s > best_sim:
                best, best_sim = pid, s
        if best_sim >= MATCH_THRESHOLD:
            return best, round(best_sim, 3)
        return None, round(best_sim, 3)

    def register_page(self, sig, hint=""):
        if not self.enabled or not sig:
            return None, 0.0
        pid, sim = self.identify(sig)
        now = time.strftime("%Y-%m-%d %H:%M")
        if pid:
            rec = self.data["pages"][pid]
            rec["seen"] = rec.get("seen", 1) + 1
            rec["last"] = now
            if hint and not rec.get("hint"):
                rec["hint"] = hint
            self.dirty = True
            return pid, sim
        pid = "P%d" % (len(self.data["pages"]) + 1)
        self.data["pages"][pid] = {
            "sig": [list(x) for x in sig],
            "hint": hint,
            "seen": 1,
            "first": now,
            "last": now,
        }
        self.dirty = True
        return pid, 1.0

    # ---------- 动作效果 ----------

    def expected(self, pid, key):
        """查历史：在这一页做这个动作，历史上通常导致什么。

        两种情况都会给出判断：
          1. 多数时候界面变了 → 用学到的那条验收条件
          2. 多数时候界面根本没变 → 说明这是个幂等动作，验收条件应是「允许不变」

        样本不足或成功率不够 → 返回 None，绝不自作主张。
        """
        if not self.enabled or not pid:
            return None
        rec = self.data["actions"].get(pid, {}).get(key)
        if not rec:
            return None

        # 先看「变没变」的分布：这是幂等动作的识别依据。
        # 条件要同时满足三件事，否则宁可不用记忆：
        #   1. 样本够
        #   2. 没变化占多数（>= 0.7）
        #   3. 失败次数不少于成功次数 —— 说明它不是「偶尔失败」，而是稳定地不变化
        ct = rec.get("changed_true", 0)
        cf = rec.get("changed_false", 0)
        seen = ct + cf
        ok_now, bad_now = rec.get("ok", 0), rec.get("bad", 0)
        if (seen >= MIN_SUPPORT and cf > ct and bad_now >= ok_now
                and cf / seen >= IDEMPOTENT_RATIO):
            return {"confidence": round(cf / seen, 3), "support": seen,
                    "expect": {"changed": False}, "bad": bad_now,
                    "reason": "历史上一半以上没变化，按幂等动作处理"}

        ok, bad = rec.get("ok", 0), rec.get("bad", 0)
        total = ok + bad
        if total < MIN_SUPPORT:
            return None
        conf = ok / total
        if conf < MIN_CONFIDENCE or not rec.get("expect"):
            return None
        return {"confidence": round(conf, 3), "support": total,
                "expect": rec["expect"], "bad": bad, "reason": "历史成功率"}

    def record(self, pid, key, ok, expect=None, changed=None):
        """每次执行完都记一笔。

        除了成功/失败，还要记「界面到底变了没有」——因为「没变」有两种含义：
        一种是失败，一种是这个动作本来就该幂等。只有统计能分开它们。
        """
        if not self.enabled or not pid:
            return
        node = self.data["actions"].setdefault(pid, {}).setdefault(
            key, {"ok": 0, "bad": 0, "expect": None})
        if changed is not None:
            field = "changed_true" if changed else "changed_false"
            node[field] = node.get(field, 0) + 1
        if ok:
            node["ok"] += 1
            if expect:
                node["expect"] = expect
        else:
            node["bad"] += 1
        self.dirty = True

    # ---------- 统计 ----------

    def stats(self):
        pages = self.data["pages"]
        actions = self.data["actions"]
        pair_count = sum(len(v) for v in actions.values())
        learned = 0
        for pid, kv in actions.items():
            for key, rec in kv.items():
                total = rec.get("ok", 0) + rec.get("bad", 0)
                if total >= MIN_SUPPORT and rec.get("ok", 0) / max(total, 1) >= MIN_CONFIDENCE:
                    learned += 1
        return {
            "pages": len(pages),
            "page_actions": pair_count,
            "usable_memories": learned,
        }

    def describe(self):
        lines = []
        st = self.stats()
        lines.append("页面 %d 个 · 动作组合 %d 个 · 已可用的记忆 %d 条"
                     % (st["pages"], st["page_actions"], st["usable_memories"]))
        if not self.data["pages"]:
            lines.append("（还没积累，多跑几次 flow 就有了）")
            return "\n".join(lines)
        for pid, rec in sorted(self.data["pages"].items()):
            hint = rec.get("hint") or ""
            lines.append("  %s 见过 %d 次%s" % (pid, rec.get("seen", 0),
                                              ("  · " + hint) if hint else ""))
            for key, a in sorted(self.data["actions"].get(pid, {}).items()):
                ok, bad = a.get("ok", 0), a.get("bad", 0)
                total = ok + bad
                conf = (ok / total) if total else 0
                mark = "✓" if (total >= MIN_SUPPORT and conf >= MIN_CONFIDENCE) else " "
                exp = a.get("expect")
                lines.append("      %s %-24s 成功 %d / 失败 %d  置信 %.2f%s"
                             % (mark, key, ok, bad, conf,
                                ("  期望 " + json.dumps(exp, ensure_ascii=False)) if exp else ""))
        return "\n".join(lines)
