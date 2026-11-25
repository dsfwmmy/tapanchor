# TapAnchor · 点锚

> 手机上的 YAML 自动化，点不准就自己重试。

**核心承诺：动作发出去了不算成功。界面没变就是失败，失败就换下一种打法，全打完还不行才报错——但报错时一定带着现场。**

---

## 为什么需要它

所有手机自动化框架都栽在同一件事上：

| 框架 | 现象 | 出处 |
|---|---|---|
| Maestro | `tapOn` 机械返回成功，底部 tab 没跳 | issue #2448 |
| AutoJs6 | `click` 不生效，只能靠 `sleep` 硬等 | issue #224 |
| AutoJs6 | 澎湃OS2 上无障碍点击完全失效 | issue #396 |
| 社区实测 | MIUI 上 `text().click()` 跑 50 次只成功 13 次 | — |

原因不复杂：**它们只保证「点击事件发出去了」，不保证「界面真的变了」**。

更麻烦的是，自绘控件（Compose / Flutter / WebView / Canvas）已经成为主流，无障碍树的失效率被结构性地放大——这不是边缘 case，是常态。

TapAnchor 只做一件事：**每个动作都有验收条件，验收不过就换打法。**

---

## 它是什么，不是什么

**是**：一个 Python + adb 的执行内核。声明式 YAML 描述流程，运行时做状态校验与降级重试。

**不是**：
- 不是 Android App（v0.3 才打包，当前必须连 adb）
- 不是 AI Agent（不做自然语言规划，那层有阿里 Mobile-Agent、droidrun 在做）
- 不是云真机平台（不做并发与设备编排）

---

## 与其他工具的位置关系

| | 手机端运行 | 声明式 | 动作后校验 | 失败降级 | 失败留证 |
|---|---|---|---|---|---|
| TapAnchor | ❌ 需 adb | ✅ YAML | ✅ | ✅ 多级 | ✅ 截图+树 |
| AutoJs6 / AutoX | ✅ | ❌ 命令式 JS | ❌ | ❌ | ❌ |
| Maestro | ❌ 需 adb | ✅ | 部分（settle） | ❌ | 部分 |
| midscene | ❌ Node | ✅ | 视觉模型 | ❌ | ❌ |
| Appium | ❌ server | ❌ | 手写等待 | ❌ | ❌ |

---

## 安装

```bash
pip install pyyaml
python -m tapanchor doctor        # 查 adb、设备、树规模
python -m tapanchor dump          # 打印清洗后的界面树
python -m tapanchor run flows/demo.yaml
```

除 `pyyaml` 外无第三方依赖。

---

## YAML 怎么写

```yaml
name: 打开设置再返回
steps:
  - name: 回桌面
    key: 3                    # KEYCODE_HOME
    verify: {changed: true}

  - name: 打开系统设置
    app: com.android.settings
    verify: {pkg: com.android.settings}

  - name: 点「关于手机」
    tap: "关于手机"
    verify: {text_exists: "版本"}     # 验收条件：不过就换打法
    fallback: [primary, longpress, xy] # 降级链，可省略
    xy: [540, 1800]                    # xy 兜底坐标，可省略

  - name: 断言
    assert: {any_text: ["版本", "Version"]}
```

### 动作

| 键 | 含义 |
|---|---|
| `tap: "文字"` | 按文字找目标并点击（自动回溯到可点击祖先） |
| `text: "内容"` | 输入文本（中文需 ADBKeyboard） |
| `swipe: [x1,y1,x2,y2]` | 滑动 |
| `xy: [x,y]` | 直接点坐标 |
| `key: 3` | 按键（3=HOME，4=BACK） |
| `app: 包名` | 启动应用 |
| `wait: 2` | 等待秒数 |
| `assert: {...}` | 纯断言，不动作 |

### 验收条件（`verify`）

不写 `verify` 时，**默认验收是「界面指纹必须变化」**——这是最保守、也最贴合真实痛点的默认值。

| 断言 | 说明 |
|---|---|
| `changed: true` | 界面与动作前不同 |
| `changed: false` | 界面与动作前相同 |
| `text_exists: "x"` | 文本出现（可传列表） |
| `text_gone: "x"` | 文本消失 |
| `any_text: ["a","b"]` | 至少出现一个 |
| `pkg: "com.xxx"` | 当前界面属于该包 |
| `count: {text: "x", min: 1, max: 3}` | 命中数量在区间内 |

多条断言写在一起 = 全部满足才通过。

### 降级链（`fallback`）

默认顺序：精确文本 → 模糊文本 → 描述 → resource-id → 长按 → 坐标兜底。

每打一种就重新抓树做一次验收。全失败则：
1. 自动截图
2. 自动保存当时的无障碍树 XML
3. 抛错并打印每一步用了哪种打法、为什么失败

产物落在 `runs/<时间戳>/`。

---

## 内部结构

```
tapanchor/
  adb.py      # 只负责发命令、拿界面
  tree.py     # 脏树清洗：非法 bounds、同框父子合并、空容器、稳定标识
  verify.py   # 状态断言
  runner.py   # 动作 + 验收 + 降级链（核心）
  cli.py      # doctor / dump / run
tests/
  test_core.py  # 11 个离线测试，不需要真机
```

`tree.py` 的清洗规则值得单独说，因为它是稳定查找的前提：

1. 丢掉 bounds 非法或面积为 0 的节点
2. 合并 bounds 完全相同、且自己没有文字的父子（把那层「壳」拆掉）
3. 丢掉既无文字又无子节点的容器
4. 给每个节点算稳定标识：文本 → 描述 → resource-id 末段 → 类名

---

## 测试

```bash
python -m unittest tests.test_core -v
```

11 个测试全部离线运行，覆盖：脏树清洗、自绘控件回溯、状态断言、**以及最关键的——「第一下点了没生效、第二下才生效」时，框架能不能自动换打法并最终通过**。

---

## 路线图

- **v0.1**（当前）：YAML 执行 + 状态校验 + 降级链 + 失败留证。离线测试通过。
- **v0.2**：半自动选择器自愈——失败时导出现场快照，调一次模型给 3 个候选选择器，人工点选后写回。**刻意不做全自动**，那会退化成玄学。
- **v0.3**：打包成 Android App，内置无障碍服务，脱离 adb 直接在手机上跑。这才是与 Maestro/midscene 拉开身位的地方。
- **v0.4**：把 flow 暴露成 MCP/HTTP 接口，让 Claude / 本地 Agent 调用确定性动作——**做「点得准的手」，不做「大脑」**。

---

## 已知限制

- 中文输入依赖 ADBKeyboard 或剪贴板通道，`input text` 只支持 ASCII
- 需要 adb 与 USB/无线调试
- 每个动作后要重新 dump 树，慢设备上单步可能 1–2 秒
- 不做并发、不做多设备编排

---

## License

MIT
