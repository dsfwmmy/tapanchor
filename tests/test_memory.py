"""TapAnchor · 记忆库测试

核心要验证的是「越用越准」这件事真的成立：
  第一次跑：认不出页面，靠默认规则执行
  跑够样本后：认得出页面，并且知道「这一步通常导致什么」
  显式写下的 verify 永远优先于记忆
"""
import json
import os
import tempfile
import unittest

from tapanchor import memory as mem
from tapanchor.runner import Runner
from tapanchor.tree import Tree
from tests.test_core import PAGE_HOME, PAGE_LOGIN, FakeAdb


class TestSignature(unittest.TestCase):
    def test_same_page_same_signature(self):
        a = mem.page_signature(Tree.parse(PAGE_LOGIN))
        b = mem.page_signature(Tree.parse(PAGE_LOGIN))
        self.assertEqual(mem.jaccard(a, b), 1.0)

    def test_different_pages_differ(self):
        a = mem.page_signature(Tree.parse(PAGE_LOGIN))
        b = mem.page_signature(Tree.parse(PAGE_HOME))
        self.assertLess(mem.jaccard(a, b), 1.0)

    def test_signature_ignores_text(self):
        """页面骨架相同但文字变了，仍应认成同一页。"""
        page_a = PAGE_HOME
        page_b = PAGE_HOME.replace("首页", "主页").replace("我的", "个人")
        sa = mem.page_signature(Tree.parse(page_a))
        sb = mem.page_signature(Tree.parse(page_b))
        self.assertEqual(mem.jaccard(sa, sb), 1.0, "文字变化不应影响页面身份")


class TestMemoryDb(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "memory.json")
        from tapanchor.tree import Tree
        self.sig = mem.page_signature(Tree.parse(PAGE_LOGIN))

    def test_identify_and_register(self):
        m = mem.Memory(self.path)
        pid, sim = m.register_page(self.sig)
        self.assertIsNotNone(pid)
        pid2, sim2 = m.identify(self.sig)
        self.assertEqual(pid, pid2, "同一页面应认成同一个 ID")

    def test_expected_needs_support(self):
        m = mem.Memory(self.path)
        pid, _ = m.register_page(self.sig)
        m.record(pid, "tap:登录", True, expect={"text_exists": "首页"})
        self.assertIsNone(m.expected(pid, "tap:登录"), "样本不足时不应给判断")
        m.record(pid, "tap:登录", True, expect={"text_exists": "首页"})
        m.record(pid, "tap:登录", True, expect={"text_exists": "首页"})
        got = m.expected(pid, "tap:登录")
        self.assertIsNotNone(got, "样本够了就应该给出判断")
        self.assertEqual(got["expect"]["text_exists"], "首页")
        self.assertEqual(got["support"], 3)

    def test_low_confidence_is_suppressed(self):
        m = mem.Memory(self.path)
        pid, _ = m.register_page(self.sig)
        for _ in range(2):
            m.record(pid, "tap:x", True, expect={"text_exists": "A"})
        for _ in range(3):
            m.record(pid, "tap:x", False)
        self.assertIsNone(m.expected(pid, "tap:x"), "成功率太低就不该再用记忆")

    def test_persist_roundtrip(self):
        m = mem.Memory(self.path)
        pid, _ = m.register_page(self.sig, hint="登录页")
        m.save()
        m2 = mem.Memory(self.path)
        self.assertIn(pid, m2.data["pages"])
        self.assertEqual(m2.data["pages"][pid]["hint"], "登录页")


class TestRunnerLearns(unittest.TestCase):
    """跑三次同样的 flow，第三次应该已经成为「认得出 + 有判断」的状态。"""

    def _run(self, memory_path):
        fake = FakeAdb([PAGE_LOGIN, PAGE_HOME], advance_after=1)
        r = Runner(fake, settle=0, outdir=tempfile.mkdtemp(), verbose=False,
                   flow_name="learn.yaml", memory_path=memory_path,
                   selectors_path=os.path.join(tempfile.mkdtemp(), "s.json"))
        res = r.run([{"name": "点登录", "tap": "登录", "verify": {"text_exists": "首页"}}])
        return res, r

    def test_learns_after_repeats(self):
        mpath = os.path.join(tempfile.mkdtemp(), "memory.json")
        for _ in range(3):
            res, r = self._run(mpath)
            self.assertTrue(res["ok"])

        m = mem.Memory(mpath)
        st = m.stats()
        self.assertGreaterEqual(st["pages"], 1, "应该记住了页面")
        self.assertGreaterEqual(st["usable_memories"], 1, "样本够了应该有可用记忆")

        # 第四次：记忆应该参与进来（日志里出现「记忆中这个动作通常导致」）
        res, r = self._run(mpath)
        self.assertTrue(res["ok"])
        joined = "\n".join(r.log_lines)
        self.assertIn("记忆中这个动作通常导致", joined,
                      "样本足够后，runner 应该用上记忆里的期望")

    def test_no_memory_flag(self):
        mpath = os.path.join(tempfile.mkdtemp(), "memory.json")
        for _ in range(3):
            self._run(mpath)
        # 关掉记忆：不应该读也不应该写
        fake = FakeAdb([PAGE_LOGIN, PAGE_HOME], advance_after=1)
        r = Runner(fake, settle=0, outdir=tempfile.mkdtemp(), verbose=False,
                   flow_name="learn.yaml", memory_path=mpath, use_memory=False)
        r.run([{"name": "点登录", "tap": "登录", "verify": {"text_exists": "首页"}}])
        self.assertNotIn("记忆中这个动作通常导致", "\n".join(r.log_lines))


class TestTransition(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "memory.json")
        self.m = mem.Memory(self.path)

    def test_record_and_predict(self):
        for _ in range(3):
            self.m.record_transition("P1", "tap:登录", "P2")
        got = self.m.predict_next("P1", "tap:登录")
        self.assertIsNotNone(got)
        self.assertEqual(got["to"], "P2")
        self.assertEqual(got["support"], 3)

    def test_no_prediction_without_support(self):
        self.m.record_transition("P1", "tap:x", "P2")
        self.assertIsNone(self.m.predict_next("P1", "tap:x"), "样本不足不该预测")

    def test_ambiguous_targets_suppressed(self):
        for _ in range(3):
            self.m.record_transition("P1", "tap:x", "P2")
        for _ in range(3):
            self.m.record_transition("P1", "tap:x", "P3")
        self.assertIsNone(self.m.predict_next("P1", "tap:x"), "去向五五开时不该硬猜")


class TestPredictionSavesDump(unittest.TestCase):
    """预判命中时应该少抓一次树——这是速度收益的来源。"""

    def test_dump_count_drops(self):
        mpath = os.path.join(tempfile.mkdtemp(), "memory.json")
        base = None
        for _ in range(3):
            fake = FakeAdb([PAGE_LOGIN, PAGE_HOME], advance_after=1)
            r = Runner(fake, settle=0, outdir=tempfile.mkdtemp(), verbose=False,
                       memory_path=mpath, selectors_path=os.path.join(tempfile.mkdtemp(), "s.json"))
            r.dumps = 0
            r.run_step({"tap": "登录", "verify": {"text_exists": "首页"}}, 1)
            r.memory.save()
            base = r.dumps
        self.assertEqual(base, 2, "写了 verify 时必须抓两次树（动作前后各一次）")

        # 第四步不写 verify，预判应当接管
        fake = FakeAdb([PAGE_LOGIN, PAGE_HOME], advance_after=1)
        r = Runner(fake, settle=0, outdir=tempfile.mkdtemp(), verbose=False,
                   memory_path=mpath, selectors_path=os.path.join(tempfile.mkdtemp(), "s.json"))
        r.dumps = 0
        ok = r.run_step({"tap": "登录"}, 1)
        self.assertTrue(ok)
        self.assertEqual(r.dumps, 1, "预判命中时应只抓动作前那一次树")
        self.assertIsNotNone(r._pending, "验证应被推到下一步")

    def test_prediction_failure_is_caught(self):
        mpath = os.path.join(tempfile.mkdtemp(), "memory.json")
        for _ in range(3):
            fake = FakeAdb([PAGE_LOGIN, PAGE_HOME], advance_after=1)
            r = Runner(fake, settle=0, outdir=tempfile.mkdtemp(), verbose=False,
                       memory_path=mpath, selectors_path=os.path.join(tempfile.mkdtemp(), "s.json"))
            r.run_step({"tap": "登录", "verify": {"text_exists": "首页"}}, 1)
            r.memory.save()

        # 这次点了没生效，停在原页：下一步开头必须发现「预测失败」
        fake = FakeAdb([PAGE_LOGIN, PAGE_LOGIN], advance_after=99)
        r = Runner(fake, settle=0, outdir=tempfile.mkdtemp(), verbose=False,
                   memory_path=mpath, selectors_path=os.path.join(tempfile.mkdtemp(), "s.json"))
        r.run_step({"tap": "登录"}, 1)
        try:
            # 这一步本身也会因为界面没变而失败，但它开头就会先检查上一步的预测
            r.run_step({"key": 4}, 2)
        except Exception:
            pass
        joined = "\n".join(r.log_lines)
        self.assertIn("预测失败", joined, "动作没生效时，下一步开头必须报出来")


if __name__ == "__main__":
    unittest.main(verbosity=2)
