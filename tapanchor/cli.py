"""TapAnchor · 命令行入口

  python -m tapanchor doctor              检查 adb 与设备
  python -m tapanchor dump [--json]       打印清洗后的界面树
  python -m tapanchor run flows/demo.yaml 执行一条 flow
"""
import argparse
import json
import os
import sys

from .adb import Adb, AdbError
from .runner import Runner
from .tree import Tree


def _load_yaml(path):
    try:
        import yaml
    except ImportError:
        sys.exit("缺少 pyyaml：pip install pyyaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def cmd_doctor(args):
    adb = Adb(serial=args.serial)
    print("adb:", adb.adb)
    try:
        print("version:", adb.version().splitlines()[0])
    except Exception as e:
        print("version 取不到:", e)
    devs = adb.devices()
    print("devices:", devs or "无")
    if not devs:
        return 1
    pkg, act = adb.current_focus()
    print("focus:", pkg, act)
    t = Tree.parse(adb.dump_xml())
    print("tree: 原始 %d 节点 → 清洗后 %d 节点" % (t.raw_count, len(t.nodes())))
    return 0


def cmd_dump(args):
    adb = Adb(serial=args.serial)
    t = Tree.parse(adb.dump_xml())
    if args.json:
        out = [{"cls": n.cls, "text": n.text, "desc": n.desc, "rid": n.rid,
                "bounds": n.bounds, "clickable": n.clickable, "depth": n.depth}
               for n in t.nodes()]
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print("原始 %d → 清洗后 %d" % (t.raw_count, len(t.nodes())))
        print(t.describe())
    return 0


def cmd_run(args):
    data = _load_yaml(args.flow)
    steps = data.get("steps", data if isinstance(data, list) else [])
    if not steps:
        sys.exit("flow 里没有 steps")
    adb = Adb(serial=args.serial)
    r = Runner(adb, settle=args.settle, outdir=args.outdir)
    result = r.run(steps, keep_going=args.keep_going)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in result.items() if k != "log"},
                      f, ensure_ascii=False, indent=1)
    return 0 if result["ok"] else 2


def main(argv=None):
    p = argparse.ArgumentParser(prog="tapanchor", description="手机上的 YAML 自动化，点不准就自己重试")
    p.add_argument("--serial", default=os.environ.get("TA_SERIAL"))
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor")
    d.set_defaults(func=cmd_doctor)

    du = sub.add_parser("dump")
    du.add_argument("--json", action="store_true")
    du.set_defaults(func=cmd_dump)

    ru = sub.add_parser("run")
    ru.add_argument("flow")
    ru.add_argument("--settle", type=float, default=0.9)
    ru.add_argument("--outdir", default="runs")
    ru.add_argument("--keep-going", action="store_true")
    ru.add_argument("--json-out", default=None)
    ru.set_defaults(func=cmd_run)

    a = p.parse_args(argv)
    try:
        return a.func(a)
    except AdbError as e:
        print("ADB 错误:", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
