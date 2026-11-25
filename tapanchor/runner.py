"""TapAnchor · 执行器
一条 flow 就是一组 step。每个 step 有「动作 + 验收条件 + 降级链」。

核心规矩：
  动作发出去了不算成功，界面没变就是失败，失败就换下一种打法，全打完还不行才报错并留证。
"""
import json
import os
import time

from . import heal, memory as mem_mod
from .adb import AdbError
from .tree import Tree
from .verify import evaluate

SETTLE = 0.9


class StepFailed(RuntimeError):
    pass


def _pick_default_verify(action_name):
    """没写 verify 时的默认验收：界面必须发生变化。"""
    if action_name in ("wait", "assert", "sleep"):
        return {}
    return {"changed": True}


class Runner:
    def __init__(self, adb, settle=SETTLE, outdir="runs", verbose=True,
                 flow_name=None, selectors_path=heal.DB_DEFAULT,
                 memory_path=mem_mod.DB_DEFAULT, use_memory=True):
        self.adb = adb
        self.settle = settle
        self.outdir = outdir
        self.verbose = verbose
        self.run_dir = None
        self.log_lines = []
        self.flow_name = flow_name
        self.selectors_path = selectors_path
        self.db = heal.load_db(selectors_path)
        self.memory = mem_mod.Memory(memory_path, enabled=use_memory)

    # ---------- 基础设施 ----------

    def log(self, msg):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
        self.log_lines.append(line)
        if self.verbose:
            print(line, flush=True)

    def snapshot(self):
        return Tree.parse(self.adb.dump_xml())

    def ensure_run_dir(self):
        if self.run_dir:
            return self.run_dir
        stamp = time.strftime("%Y%m%d-%H%M%S")
        d = os.path.join(self.outdir, stamp)
        os.makedirs(d, exist_ok=True)
        self.run_dir = d
        return d

    def save_evidence(self, tag):
        d = self.ensure_run_dir()
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in tag)[:40]
        png = os.path.join(d, safe + ".png")
        try:
            self.adb.screencap(png)
        except Exception as e:
            self.log("截图失败: %s" % e)
            png = None
        try:
            xml = os.path.join(d, safe + ".xml")
            with open(xml, "w", encoding="utf-8") as f:
                f.write(self.adb.dump_xml())
        except Exception:
            xml = None
        return png, xml

    # ---------- 动作 ----------

    def _candidates(self, tree, want):
        """按可靠性从高到低给出点按目标。"""
        out = []
        for n in tree.find(text=want):
            a = n.clickable_ancestor()
            if a and a.center:
                out.append(("text-exact", a))
        for n in tree.find(text=want, contains=True):
            a = n.clickable_ancestor()
            if a and a.center:
                out.append(("text-contains", a))
        for n in tree.find(desc=want):
            a = n.clickable_ancestor()
            if a and a.center:
                out.append(("desc", a))
        for n in tree.find(rid=want):
            a = n.clickable_ancestor()
            if a and a.center:
                out.append(("rid", a))
        seen, uniq = set(), []
        for tag, node in out:
            k = (node.ident(), node.bounds)
            if k in seen:
                continue
            seen.add(k)
            uniq.append((tag, node))
        return uniq

    def _do_tap(self, strategy, step, tree):
        """执行一种打法，返回是否把动作发出去了。"""
        if strategy == "learned":
            sel = step.get("_learned")
            if not sel or sel.get("kind") not in ("text", "desc", "rid", "cls"):
                return False
            nodes = tree.find(**{sel["kind"]: sel["value"]})
            if not nodes:
                return False
            tgt = nodes[0].clickable_ancestor() or nodes[0]
            if not tgt.center:
                return False
            x, y = tgt.center
            self.log("  学到的定位 %s=%r @(%d,%d)" % (sel["kind"], sel["value"], x, y))
            self.adb.tap(x, y)
            time.sleep(self.settle)
            return True

        if strategy == "xy":
            xy = step.get("xy") or []
            if len(xy) != 2:
                return False
            self.adb.tap(xy[0], xy[1])
            return True

        want = step.get("tap") or step.get("text_target")
        cands = self._candidates(tree, want)

        if strategy == "longpress":
            if not cands:
                return False
            x, y = cands[0][1].center
            self.adb.longpress(x, y)
            return True

        if not cands:
            return False

        pool = cands[:1] if strategy == "primary" else cands
        for tag, node in pool:
            x, y = node.center
            self.log("  尝试 %s → %s %r @(%d,%d)" % (strategy, tag, node.label, x, y))
            self.adb.tap(x, y)
            time.sleep(self.settle)
            return True
        return False

    # ---------- 单步 ----------

    def run_step(self, step, index):
        name = None
        for k in ("app", "tap", "text", "swipe", "xy", "key", "wait", "assert", "sleep"):
            if k in step:
                name = k
                break
        if name is None:
            raise StepFailed("第 %d 步没有可识别的动作: %r" % (index, step))

        label = step.get("name") or str(step.get("tap") or step.get("assert") or name)
        step_key_id = heal.step_key(index, label)
        self.log("step %d · %s · %s" % (index, name, label))

        before_tree = self.snapshot()
        before_sig = before_tree.signature()

        # 认页面：这是哪一页？认出来，才能谈「这一页上的动作通常会发生什么」
        sig_now = mem_mod.page_signature(before_tree)
        page_id, page_sim = self.memory.register_page(sig_now, hint=label)
        if page_id:
            self.log("  页面 %s（相似度 %.2f）" % (page_id, page_sim))

        if name in ("wait", "sleep"):
            time.sleep(float(step.get(name, 1)))
            after = self.snapshot()
            ok, detail = evaluate(after, step.get("verify"), before_sig)
            self.log("  %s%s" % ("OK " if ok else "FAIL ", detail))
            return ok

        if name == "assert":
            ok, detail = evaluate(before_tree, step.get("assert"), before_sig)
            self.log("  %s%s" % ("OK " if ok else "FAIL ", detail))
            return ok

        targets = step.get("fallback") or (["primary", "longpress", "xy"]
                                           if "xy" in step else ["primary", "longpress"])

        # 之前有人点选过「这一步该用哪个选择器」，就把它排在最前面
        learned = heal.get_learned(self.db, self.flow_name, step_key_id)
        if learned:
            step["_learned"] = learned
            targets = ["learned"] + [t for t in targets if t != "learned"]
            self.log("  库里有学到的定位：%s=%r" % (learned["kind"], learned["value"]))

        # 查记忆：在这一页做这个动作，历史上通常导致什么
        akey = mem_mod.action_key(step)
        mem = self.memory.expected(page_id, akey)
        if mem:
            self.log("  记忆中这个动作通常导致：%s（置信 %.2f · 样本 %d）"
                     % (json.dumps(mem["expect"], ensure_ascii=False),
                        mem["confidence"], mem["support"]))

        last_detail = ""
        last_changed = None
        for strategy in targets:
            try:
                if name == "tap":
                    sent = self._do_tap(strategy, step, before_tree)
                elif name == "text":
                    self.adb.text(step["text"])
                    time.sleep(self.settle)
                    sent = True
                elif name == "swipe":
                    p = step["swipe"]
                    self.adb.swipe(*p)
                    time.sleep(self.settle)
                    sent = True
                elif name == "xy":
                    self.adb.tap(*step["xy"])
                    time.sleep(self.settle)
                    sent = True
                elif name == "app":
                    pkg = step["app"]
                    try:
                        self.adb.run("shell", "monkey", "-p", pkg, "-c",
                                     "android.intent.category.LAUNCHER", "1")
                    except AdbError:
                        comp = step.get("component") or pkg
                        self.adb.run("shell", "am", "start", "-n", comp)
                    time.sleep(self.settle * 2)
                    sent = True
                elif name == "key":
                    self.adb.keyevent(step["key"])
                    time.sleep(self.settle)
                    sent = True
                else:
                    sent = False
            except Exception as e:
                self.log("  打法 %s 报错: %s" % (strategy, e))
                sent = False

            if not sent:
                self.log("  打法 %s 找不到目标" % strategy)
                continue

            after = self.snapshot()
            changed_now = after.signature() != before_sig
            last_changed = changed_now
            spec = (step.get("verify")
                    or (mem["expect"] if mem else None)
                    or _pick_default_verify(name))
            ok, detail = evaluate(after, spec, before_sig)
            last_detail = detail
            if ok:
                self.memory.record(page_id, akey, True, expect=spec, changed=changed_now)
                self.log("  OK  (%s) %s" % (strategy, detail))
                return True
            self.log("  打法 %s 动作已发出%s" % (strategy, "但界面没变" if not changed_now else "且界面变了，但验收不通过"))

        # 这一页上这个动作从来没成功过，降一次置信度（同时记下界面到底变没变）
        self.memory.record(page_id, akey, False, changed=last_changed)

        # 全部打法都没让界面变化：先算「现场里最像的 3 个目标」，再留证
        if name == "tap":
            want = step.get("tap") or step.get("text_target")
            cands = heal.candidates(before_tree, want)
            if cands:
                hp, payload = heal.write_heal_file(
                    self.ensure_run_dir(), self.flow_name, step_key_id, want, cands)
                self.log("  现场候选 %d 个 → %s" % (len(cands), hp))
                for c in payload["candidates"]:
                    self.log("    [%d] %s=%r 相似度 %.2f bounds=%s"
                             % (c["pick"], c["selector"]["kind"],
                                c["selector"]["value"], c["score"], c["bounds"]))

        png, xml = self.save_evidence("fail-step%d-%s" % (index, label))
        self.log("  FAIL 全部打法用尽 · 证据: %s" % (png or xml))
        raise StepFailed("第 %d 步「%s」全部打法失败 · 校验: %s" % (index, label, last_detail))

    # ---------- flow ----------

    def run(self, steps, keep_going=False, flow_name=None):
        if flow_name:
            self.flow_name = flow_name
        failed = []
        for i, step in enumerate(steps, 1):
            try:
                if not self.run_step(step, i):
                    failed.append((i, step, "verify 未通过"))
                    if not keep_going:
                        break
            except StepFailed as e:
                failed.append((i, step, str(e)))
                if not keep_going:
                    break
        total = len(steps)
        okn = total - len(failed)
        saved = self.memory.save()
        if saved:
            st = self.memory.stats()
            self.log("记忆已更新：%s（页面 %d · 可用记忆 %d 条）"
                     % (saved, st["pages"], st["usable_memories"]))
        self.log("—— 结束：%d/%d 步通过 ——" % (okn, total))
        return {"ok": not failed, "passed": okn, "total": total,
                "failed": failed, "run_dir": self.run_dir,
                "log": self.log_lines}
