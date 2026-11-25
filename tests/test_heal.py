"""TapAnchor · 选择器自愈测试

重点验证闭环：
  文案对不上 → 跑失败 → 现场算出候选 → 人点选写回 → 下次一步到位
"""
import json
import os
import tempfile
import unittest

from tapanchor import heal
from tapanchor.runner import Runner
from tapanchor.tree import Tree
from tests.test_core import PAGE_HOME, PAGE_LOGIN, FakeAdb

FLOW = "selfheal_demo.yaml"


class TestCandidates(unittest.TestCase):
    def test_finds_typo_target(self):
        t = Tree.parse(PAGE_LOGIN)
        cands = heal.candidates(t, "登入")  # 实际按钮叫「登录」
        self.assertTrue(cands, "文案差一个字也应该能找到候选")
        self.assertEqual(cands[0][1]["kind"], "text")
        self.assertEqual(cands[0][1]["value"], "登录")

    def test_no_candidate_when_nothing_close(self):
        t = Tree.parse(PAGE_HOME)
        cands = heal.candidates(t, "zzzz不存在zzzz", threshold=0.6)
        self.assertEqual(cands, [])

    def test_prefers_clickable_and_sized(self):
        t = Tree.parse(PAGE_LOGIN)
        cands = heal.candidates(t, "登录")
        self.assertEqual(cands[0][1]["value"], "登录")


class TestSelectorDb(unittest.TestCase):
    def test_roundtrip(self):
        p = os.path.join(tempfile.mkdtemp(), "selectors.json")
        db = heal.load_db(p)
        heal.set_learned(db, FLOW, "1:点登录", {"kind": "text", "value": "登录"})
        heal.save_db(db, p)

        db2 = heal.load_db(p)
        got = heal.get_learned(db2, FLOW, "1:点登录")
        self.assertIsNotNone(got)
        self.assertEqual(got["value"], "登录")
        self.assertIn("learned_at", got)

    def test_missing_db_is_empty(self):
        self.assertEqual(heal.load_db("/nonexistent/x.json"), {})


class TestSelfHealLoop(unittest.TestCase):
    def _flow(self):
        # 故意写错一个字：真实世界里最常见的情形（App 改文案 / 抄错）
        return [{"name": "点登录", "tap": "登入", "verify": {"text_exists": "首页"}}]

    def test_full_loop(self):
        work = tempfile.mkdtemp()
        selectors = os.path.join(work, "selectors.json")

        # 第一轮：找不到目标 → 失败 → 现场候选
        fake1 = FakeAdb([PAGE_LOGIN, PAGE_HOME], advance_after=1)
        r1 = Runner(fake1, settle=0, outdir=os.path.join(work, "runs"),
                    verbose=False, flow_name=FLOW, selectors_path=selectors)
        res1 = r1.run(self._flow())
        self.assertFalse(res1["ok"], "第一轮应失败")

        heal_path = os.path.join(res1["run_dir"], "heal.json")
        self.assertTrue(os.path.exists(heal_path), "失败必须产出候选文件")
        with open(heal_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        self.assertEqual(payload["flow"], FLOW)
        top = payload["candidates"][0]
        self.assertEqual(top["selector"]["value"], "登录")

        # 人点选第 1 个
        db = heal.load_db(selectors)
        heal.set_learned(db, payload["flow"], payload["step"], top["selector"])
        heal.save_db(db, selectors)

        # 第二轮：学到的定位排最前，一次点中即通过
        fake2 = FakeAdb([PAGE_LOGIN, PAGE_HOME], advance_after=1)
        r2 = Runner(fake2, settle=0, outdir=os.path.join(work, "runs2"),
                    verbose=False, flow_name=FLOW, selectors_path=selectors)
        res2 = r2.run(self._flow())
        self.assertTrue(res2["ok"], "学到定位后应当一次通过")
        self.assertEqual(len(fake2.taps), 1, "不应该再走其他打法")


if __name__ == "__main__":
    unittest.main(verbosity=2)
