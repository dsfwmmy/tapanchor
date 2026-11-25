"""TapAnchor · 离线测试
不依赖任何真机：用构造的 uiautomator XML 验证
  1. 脏树清洗（非法 bounds / 同 bounds 父子 / 空容器）
  2. 语义查找与可点击祖先回溯（自绘控件场景）
  3. 状态断言（changed / text_exists / count）
  4. 核心：动作发出但界面没变 → 自动降级换打法 → 界面变了才算过

跑法：python -m unittest tests.test_core -v
"""
import os
import tempfile
import unittest

from tapanchor.runner import Runner, StepFailed
from tapanchor.tree import Tree
from tapanchor.verify import evaluate

PAGE_LOGIN = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]" package="com.demo">
    <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]" package="com.demo">
      <node class="android.widget.TextView" text="登录" bounds="[400,1200][680,1260]" clickable="true" package="com.demo"/>
      <node class="android.view.View" text="自绘切换" bounds="[100,800][300,900]" clickable="false" package="com.demo"/>
    </node>
    <node class="android.widget.FrameLayout" bounds="[0,0][0,0]" package="com.demo"/>
    <node class="android.widget.TextView" text="" bounds="[0,2000][1080,2100]" package="com.demo"/>
  </node>
</hierarchy>"""

PAGE_HOME = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]" package="com.demo">
    <node class="android.widget.TextView" text="首页" bounds="[0,200][400,280]" package="com.demo"/>
    <node class="android.widget.TextView" text="我的" bounds="[680,200][1080,280]" package="com.demo"/>
  </node>
</hierarchy>"""


class FakeAdb:
    """按下之后页面会换的假设备。

    advance_after=1 正常：第一次动作就生效
    advance_after=2 有病：第一下点了没反应，第二下才生效（就是真实世界里最常见的坑）
    """

    def __init__(self, pages, advance_after=1):
        self.pages = pages
        self.idx = 0
        self.taps = []
        self.advance_after = advance_after

    def dump_xml(self):
        return self.pages[min(self.idx, len(self.pages) - 1)]

    def advance(self):
        if self.idx < len(self.pages) - 1:
            self.idx += 1

    def _record(self, kind, x, y):
        self.taps.append((kind, int(x), int(y)))
        if len(self.taps) >= self.advance_after:
            self.advance()

    def tap(self, x, y):
        self._record("tap", x, y)

    def longpress(self, x, y, ms=800):
        self._record("longpress", x, y)

    def screencap(self, path):
        with open(path, "wb") as f:
            f.write(b"\x89PNG")
        return path


class TestTree(unittest.TestCase):
    def setUp(self):
        self.t = Tree.parse(PAGE_LOGIN)

    def test_dirty_bounds_dropped(self):
        for n in self.t.nodes():
            self.assertIsNotNone(n.bounds, "非法 bounds 的节点应被丢弃")

    def test_same_bounds_merged(self):
        hits = self.t.find(text="登录")
        self.assertEqual(len(hits), 1)
        # 合并后不应再有「同框且无文字」的中间层卡在中间
        parents = [n for n in self.t.nodes() if n.bounds == (0, 0, 1080, 2400) and not n.label]
        self.assertLessEqual(len(parents), 1)

    def test_find_exact_and_contains(self):
        self.assertEqual(len(self.t.find(text="登录")), 1)
        self.assertEqual(len(self.t.find(text="登", contains=True)), 1)
        self.assertEqual(len(self.t.find(text="不存在")), 0)

    def test_clickable_ancestor_for_selfdrawn(self):
        hits = self.t.find(text="自绘切换")
        self.assertEqual(len(hits), 1)
        # 自绘控件本身 clickable=false，但同框祖先可点
        anc = hits[0].clickable_ancestor()
        self.assertIsNotNone(anc)
        self.assertIsNotNone(anc.center)


class TestVerify(unittest.TestCase):
    def test_text_exists(self):
        t = Tree.parse(PAGE_HOME)
        ok, _ = evaluate(t, {"text_exists": "首页"})
        self.assertTrue(ok)
        ok, _ = evaluate(t, {"text_exists": "登录"})
        self.assertFalse(ok)

    def test_changed(self):
        a = Tree.parse(PAGE_LOGIN).signature()
        b = Tree.parse(PAGE_HOME).signature()
        ok, _ = evaluate(Tree.parse(PAGE_HOME), {"changed": True}, a)
        self.assertTrue(ok)
        ok, _ = evaluate(Tree.parse(PAGE_HOME), {"changed": False}, b)
        self.assertTrue(ok)

    def test_count(self):
        t = Tree.parse(PAGE_HOME)
        ok, _ = evaluate(t, {"count": {"text": "首页", "min": 1, "max": 2}})
        self.assertTrue(ok)
        ok, _ = evaluate(t, {"count": {"text": "首页", "min": 2}})
        self.assertFalse(ok)


class TestRunnerFallback(unittest.TestCase):
    """这是整个项目的核心断言：
    第一次点击发出去了但界面没变 → 必须换打法 → 界面变了才算过。"""

    def test_retry_until_world_changes(self):
        # 第一下点了没反应，第二下才生效 —— 正是「点了没生效」的真实形态
        fake = FakeAdb([PAGE_LOGIN, PAGE_HOME], advance_after=2)
        r = Runner(fake, settle=0, outdir=tempfile.mkdtemp(), verbose=False)
        steps = [{"tap": "登录", "verify": {"text_exists": "首页"}}]
        res = r.run(steps)
        self.assertTrue(res["ok"], "降级重试后应通过")
        self.assertGreaterEqual(len(fake.taps), 2, "应当尝试了不止一种打法")
        self.assertEqual(fake.taps[0][0], "tap")
        self.assertEqual(fake.taps[1][0], "longpress", "第一下没生效就应换成按住")

    def test_all_strategies_fail_reports_and_keeps_evidence(self):
        # 界面从头到尾不变：所有打法都白打，必须判失败并留下证据目录
        fake = FakeAdb([PAGE_LOGIN, PAGE_LOGIN], advance_after=99)
        outdir = tempfile.mkdtemp()
        r = Runner(fake, settle=0, outdir=outdir, verbose=False)
        steps = [{"tap": "登录", "verify": {"text_exists": "首页"}}]
        res = r.run(steps)
        self.assertFalse(res["ok"])
        self.assertEqual(len(res["failed"]), 1)
        self.assertIsNotNone(res["run_dir"], "失败必须留证")
        self.assertTrue(os.path.isdir(res["run_dir"]))
        files = os.listdir(res["run_dir"])
        self.assertTrue(any(f.endswith(".png") for f in files), "应留下失败截图")

    def test_step_raises_when_exhausted(self):
        fake = FakeAdb([PAGE_LOGIN, PAGE_LOGIN], advance_after=99)
        r = Runner(fake, settle=0, outdir=tempfile.mkdtemp(), verbose=False)
        with self.assertRaises(StepFailed):
            r.run_step({"tap": "登录", "verify": {"text_exists": "首页"}}, 1)

    def test_default_verify_is_changed(self):
        # 只写 tap 不写 verify 时，默认验收是「界面必须变化」
        fake = FakeAdb([PAGE_LOGIN, PAGE_HOME], advance_after=1)
        r = Runner(fake, settle=0, outdir=tempfile.mkdtemp(), verbose=False)
        res = r.run([{"tap": "登录"}])
        self.assertTrue(res["ok"])
        self.assertEqual(len(fake.taps), 1, "一次就生效时不允许多点")


if __name__ == "__main__":
    unittest.main(verbosity=2)
