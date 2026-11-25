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


def cmd_memory(args):
    from . import memory as mmod
    mem = mmod.Memory(args.memory)
    print(mem.describe())
    return 0


def cmd_run(args):
    data = _load_yaml(args.flow)
    steps = data.get("steps", data if isinstance(data, list) else [])
    if not steps:
        sys.exit("flow 里没有 steps")
    adb = Adb(serial=args.serial)
    flow_name = os.path.basename(args.flow)
    r = Runner(adb, settle=args.settle, outdir=args.outdir,
               flow_name=flow_name, selectors_path=args.selectors,
               memory_path=args.memory, use_memory=not args.no_memory)
    result = r.run(steps, keep_going=args.keep_going)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in result.items() if k != "log"},
                      f, ensure_ascii=False, indent=1)
    return 0 if result["ok"] else 2


def cmd_heal(args):
    from . import heal as hmod
    target = args.run_dir
    if target == "last":
        runs = sorted(d for d in os.listdir(args.outdir)
                      if os.path.isdir(os.path.join(args.outdir, d)))
        if not runs:
            sys.exit("runs 目录里没有记录")
        target = os.path.join(args.outdir, runs[-1])
    hp = os.path.join(target, "heal.json")
    if not os.path.exists(hp):
        sys.exit("没找到 %s —— 只有点按类步骤失败时才会生成候选" % hp)
    with open(hp, "r", encoding="utf-8") as f:
        payload = json.load(f)

    print(hmod.render_candidates(payload))
    if args.pick is None:
        return 0

    cands = payload.get("candidates", [])
    if not (1 <= args.pick <= len(cands)):
        sys.exit("--pick 只能是 1..%d" % len(cands))
    chosen = cands[args.pick - 1]
    db = hmod.load_db(args.selectors)
    hmod.set_learned(db, payload["flow"], payload["step"], chosen["selector"])
    hmod.save_db(db, args.selectors)
    print("已写回 %s：%s → %s=%r"
          % (args.selectors, payload["step"],
             chosen["selector"]["kind"], chosen["selector"]["value"]))
    print("下次跑 %s 时，这一步会先用学到的定位。" % payload["flow"])
    return 0


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
    ru.add_argument("--selectors", default="selectors.json")
    ru.add_argument("--memory", default="memory.json")
    ru.add_argument("--no-memory", action="store_true", help="只用当期识别，不读也不写记忆")
    ru.add_argument("--keep-going", action="store_true")
    ru.add_argument("--json-out", default=None)
    ru.set_defaults(func=cmd_run)

    me = sub.add_parser("memory", help="看积累下来的页面与动作记忆")
    me.add_argument("--memory", default="memory.json")
    me.set_defaults(func=cmd_memory)

    he = sub.add_parser("heal", help="看失败现场的候选目标，并把选中的写回选择器库")
    he.add_argument("run_dir", help="runs/<时间戳> 或 last")
    he.add_argument("--pick", type=int, default=None, help="选第几个候选写回")
    he.add_argument("--outdir", default="runs")
    he.add_argument("--selectors", default="selectors.json")
    he.set_defaults(func=cmd_heal)

    a = p.parse_args(argv)
    try:
        return a.func(a)
    except AdbError as e:
        print("ADB 错误:", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
