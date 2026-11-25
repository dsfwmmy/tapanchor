"""TapAnchor —— 手机上的 YAML 自动化，点不准就自己重试。

设计只有一条主线：
    动作发出去了不算成功；界面没变就是失败；失败就换打法；全失败才报错并留证。
"""
__version__ = "0.1.0"

from .adb import Adb, AdbError
from .tree import Tree, Node
from .runner import Runner, StepFailed
from .verify import evaluate

__all__ = ["Adb", "AdbError", "Tree", "Node", "Runner", "StepFailed",
           "evaluate", "__version__"]
