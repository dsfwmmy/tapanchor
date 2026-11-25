# Your tap succeeded. Nothing happened.

*A note on the failure mode that Android UI automation keeps sweeping under the rug.*

---

The first time I hit this, I was automating a quiz app.

The script was supposed to tap option A on a multiple-choice question. The node existed, the text was there, the bounds were sane, `click` returned success. The log was clean.

Then it tapped "Next".

When I submitted, the app told me there were unanswered questions. Twenty questions. Zero answers selected.

The script hadn't crashed. **It thought it had succeeded.** The tile was custom-drawn, the tap got swallowed by the parent scroll container, and `performAction` still returned `true` — because the event was delivered, just never consumed. The script ran an entire exam on screen state that didn't exist.

There's a milder version of the same thing. You write a script: open settings, tap "Display & brightness", assert you're on the second page.

```
tapOn: "Display & brightness"   ✓
assertVisible: "Brightness"     ✓
```

And nothing happened. The tap landed on nothing. Or on a non-clickable container. Or the page was mid-transition and the tap got swallowed. The script walked three more steps on a screen that never existed, then crashed somewhere unrelated with an error that pointed at everything except the real cause.

The thing about this failure mode is that **it doesn't raise**.

---

## Why it happens

### `ACTION_CLICK` means "request delivered", not "clicked"

Android's accessibility API exposes `ACTION_CLICK`. Its semantics are: *I am requesting that this node be clicked.* Not: *this node was clicked.*

The request gets dispatched to the target View. Then any of these can happen:

- The View is off-screen, or covered, or inside a scroll container
- The View isn't actually clickable — it just happens to hold text
- The click listener returns early because of app-specific logic
- A parent container intercepts the touch

The system has no mechanism to tell you "the request was rejected." `performAction` returning `true` only means **the request was delivered**.

That's by design. It's not a bug. But every framework above it treats delivery as success.

### Custom-drawn UI has no semantic nodes

Compose, Flutter, WebView, Canvas — what they expose to the accessibility tree is often a single shell node. Under Compose, `className("android.widget.Button")` matches nothing, because there is no Button class.

AutoJs6 issue #269 is a textbook case: `bounds` returned `[952,1036-1024,1108]` — malformed, negative area, unclickable forever. Vendor ROMs make this worse.

Maestro issue #2293 is the same family: element IDs inside a WebView aren't recognized.

### The world doesn't change on the same frame

Tap → network request → load → render → animation ends. On real devices that's 300ms to 2 seconds.

Dump the tree immediately after a tap and you get the old screen. If the framework considers "tap dispatched" to be "tap complete," the next step operates on a screen that's already stale.

---

## What existing tools do about it

**Imperative frameworks (AutoJs6, AutoX.js)** hand the problem back to you. You write `sleep(1000)`. You decide whether a transition happened. In well-written scripts, the wait-and-retry logic ends up longer than the business logic.

**Declarative frameworks (Maestro)** have `waitForAnimationToEnd`, and settle/sharded-retry landed recently. But settle means "the UI stopped moving" — not "the thing I wanted appeared." A screen can be perfectly stable in the wrong place.

**Vision-model approaches** can find the button. But they re-query every time, cost tokens per action, and are non-deterministic — two runs can produce different coordinates. They solve "can I find it," not "did it work."

**Appium** is the most engineered of the bunch, but waiting is still explicit. It gives you a toolbox, not a judgment.

All four share one assumption: **the framework guarantees the action was dispatched; the user is responsible for whether it took effect.**

---

## A different stance: make "did the world change" a precondition

If `click` can only ever mean "request delivered," the only way out is to **verify after every action**.

The idea isn't new — Airtest does screenshot diffing, Playwright has auto-waiting. But in native Android automation it isn't the default behavior anywhere.

### Screen fingerprint

Dump the tree before the action, compute a fingerprint:

```python
signature = frozenset((node.ident(), node.bounds) for node in tree.nodes() if node.label)
```

Dump again after. Different → the world changed. Identical → nothing happened.

It's crude. That's the point: it captures the exact signal you need — *did anything move at all*.

### Tighter assertions when crude isn't enough

```yaml
verify:
  text_exists: "Brightness"
  pkg: com.android.settings
  count: {text: "Toggle", min: 3}
```

When you omit `verify` entirely, the default is **"the screen must change."** That's the most conservative default, and it's the one that matches the actual failure mode.

### A fallback chain

When verification fails, don't raise. Change your approach:

```
exact text → fuzzy text → content-desc → resource-id → long-press → coordinate
```

Each new strategy re-dumps the tree and re-verifies.

On the MIUI devices where the first tap doesn't land, this chain does what a human does: tap again, or press and hold.

### Evidence on total failure

When every strategy is exhausted, don't just throw. Keep:

- A screenshot of what was actually on screen
- The accessibility tree XML at that moment
- Which strategy was tried, in what order, and where it broke

This is the least glamorous feature and the most valuable in practice. When your scheduled job dies at 3am, you don't want `step 7 failed`. You want the screenshot.

---

## Three things I got wrong first

### You have to clean the tree

`uiautomator dump` XML is written for machines, not for querying. Out of the box you'll hit:

- Malformed `bounds` (`[0,0][0,0]`, bad formats, negative area)
- Parent and child with identical bounds — text on the inner node, clickable on the outer
- Hundreds of empty containers with neither text nor children

So there's a cleaning pass: drop invalid bounds, merge same-bounds parent/child pairs where the parent has no text, drop empty containers, then assign each node a stable identity (text → content-desc → resource-id tail → class).

Node count routinely drops from several hundred to a few dozen. Lookups get steadier.

### Over adb, there is no "semantic click"

This one trips people up. Over adb you can only `input tap x y` — you're poking a coordinate. "Clicking an element" means: find the node, compute the bounds center, poke that coordinate.

Real semantic clicking needs `performAction` through a resident accessibility service — i.e. an app.

That constrains the design: your fallback chain can't switch *event types*. It can only switch *how you find the target* and *which gesture you use*.

### Every dump is slow

A `uiautomator dump` on a real device costs 1–3 seconds. With the action and the settle, 2–8 seconds per step is normal.

Which means: **this approach is for flows with few steps that must be reliable**, not for high-frequency interaction. If you need thousands of taps, look elsewhere.

---

## Selector self-healing, deliberately semi-automatic

Apps change copy and your script breaks. That's the most common way automation dies.

The obvious move is to let a model rewrite the selector. I tried that and narrowed it to semi-automatic, for one reason: **trust**.

One wrong auto-fix — "Cancel" silently becoming "Confirm" — and the user's trust in the tool is gone permanently. That costs more than the labor it saved.

So instead:

1. On failure, score the live tree with edit distance + clickability + size, surface the 3 most likely targets
2. A human picks one
3. It's written to a selector store; that step uses it first next time

On a real device I intentionally mistyped "Display & brightness" as "Display and brightnes". First run failed, surfaced `text='Display & brightness' similarity 0.88`. After picking it, the next run hit first try.

The scoring **doesn't call a model** — offline, zero cost, and every result can explain why it ranked where it did. That matters more than being clever.

---

## What this is not

- **Not a natural-language planner.** Alibaba's Mobile-Agent and droidrun do that well. This is the hand, not the brain.
- **Not concurrent / multi-device orchestration.** That needs racks and ops.
- **Not for high-frequency interaction.** 2–8 seconds per step.
- **Not for non-ASCII input out of the box.** `input text` is ASCII-only; Chinese needs ADBKeyboard or a clipboard channel.

---

## Code

MIT, all of it here: **https://github.com/dsfwmmy/tapanchor**

Current state is v0.3: YAML execution, post-action verification, fallback chain, failure evidence, semi-automatic selector healing, and the ability to run on the phone itself (phone adb-connects to itself, no PC needed). 18 offline tests, none of which need a device.

```bash
# from your machine
adb connect <phone-ip>:<port>
python -m tapanchor --serial <phone-ip>:<port> run flows/demo.yaml

# or on the phone itself
./run_local.sh run flows/demo.yaml
```

---

## The last line

The hard part of automation isn't making the program move. It's making the program know whether it moved *correctly*.

Most frameworks leave that to the user. This one treats it as a precondition of every action — and I think that's where it belongs.
