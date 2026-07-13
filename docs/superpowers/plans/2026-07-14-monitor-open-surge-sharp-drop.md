# 盘中盯盘：开盘冲高回落 + 急跌幅度 检测器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `src/monitor.py` 的盘中盯盘规则引擎新增两个检测器——`open_surge_revert`（开盘冲高回落，标记高点价位 + 下杀起点）与 `sharp_drop`（近 N 笔价格急跌幅度），补齐用户回放中漏掉的"开盘最高点"与"立马下杀风险"两类信号。

**Architecture:** 两个检测器作为**新规则类型**接入既有规则引擎，与现有 5 条默认规则同构：由 `_inject_default_rules` 默认注入、在 `evaluate_quote` 派发、自带状态 + 15 分钟冷却 + 回放/实时双通道发出。`open_surge_revert` 维护日内 `open_price/open_high/open_surge_fired/open_day` 状态（开盘窗口内更新高点，冲高+回落一次性触发）；`sharp_drop` 维护 `price_history` 近期价格序列（固定 `deque(maxlen=60)`，按 `bars` 回看跌幅）。两者均受总闸门 `AGENT_EVENT_MONITOR_DEFAULT_RULES_ENABLED` + 各自独立 enable 控制，config 可调阈值。

**Tech Stack:** Python 3（PEP8 + 类型注解），`unittest`（项目用 `.venv/Scripts/python.exe tests/test_monitor.py -v` 跑，无 pytest）。纯后端，前端零改动。

**设计依据：** `docs/superpowers/specs/2026-07-14-monitor-open-surge-sharp-drop-design.md`（已评审通过并提交 `b8ff90e7`）。

---

## 文件结构（改动清单）

| 文件 | 责任 | 改动 |
|---|---|---|
| `src/config.py` | 配置 dataclass + env 加载 | 新增 7 个 `agent_event_monitor_*` 字段 + env 读取 |
| `src/monitor.py` | 盘中规则引擎 | `__init__` 新增 5 状态字段；8 个阈值 helper + `_in_open_window`；`evaluate_quote` 顶部状态捕获 + 2 个派发分支；`_inject_default_rules` 注入 2 条；两处回放清理块加新状态 |
| `.env.example` | 配置文档 | 7 个注释变量 + 调参建议 |
| `tests/test_monitor.py` | 单测 | 更新 `_make_default_rules_config`（7 字段）+ 2 import；新增 12 用例 |

---

## 关键实现约束（实现者必读）

1. **MagicMock 配置陷阱（务必处理）：** `tests/test_monitor.py` 的 `_make_default_rules_config` 用 `MagicMock()` 构建 config。`getattr(MagicMock实例, "任意名", 默认)` 会**返回一个新 Mock（不返回默认值）**，而 `float(Mock)` / `int(Mock)` **静默返回 `0.0` / `0`（不抛异常）**。若不显式设值，`_open_surge_pct()` 会返回 `0.0` → 冲高判定 `open_high >= open_price*1.0` 恒真 → 破坏"无冲高不触发"等负面测试。**故 Task 1 必须更新 `_make_default_rules_config` 显式设 7 字段为真实值。** int 类 helper 另有 `if v >= 1 else 默认` 兜底（Mock→0 会回落默认），float 类 helper 无此兜底，依赖测试 helper 设值。

2. **状态捕获位置（务必在 `if last_quote:` 之前）：** `evaluate_quote` 第 301 行 `last_quote = self.last_quotes.get(code)`，第 302 行 `if last_quote:` 守卫了整个派发逻辑。**首笔行情无 `last_quote` → 派发被跳过**，但 `open_surge_revert` 必须在首笔种子 `open_price`。故状态捕获（price_history 追加 + open_* 维护）须插在第 299 行（`current_time_val` 赋值）与第 301 行（`last_quote` 取值）之间，**在守卫之前**。

3. **`sharp_drop` 用 `price_history[-bars]` 而非 `change_pct`：** `change_pct` 基于 `pre_close`，盘中仍涨时的下杀会被"全日仍涨"掩盖。`price_history` 用固定 `deque(maxlen=60)`（不随 config `bars` 变，避免用户调小 `bars` 后窗口过窄丢历史），派发时按 `hist[-bars]` 回看 `bars` 笔前的价。

4. **既有测试不回归（已核对）：** 现有 `green_streak_*` 测试最多发 3 次 `evaluate_quote`（首笔经 `monitor.last_quotes[code]=self._q(...)` 直接设，不进 `price_history`）→ `price_history` ≤3 元素 < 默认 `bars=5` → `sharp_drop` 不触发；且这些测试 `replay_time` 为 `10:00+`（开盘窗口 09:30–09:45 之外）→ `open_surge_revert` 不触发。`test_default_anomaly_rules_injected` 用 `assertIn`（存在性），新增 2 类型不破坏。故既有用例零回归。

5. **时间基准一致性：** 回放 `replay_time` 经 `datetime.datetime.strptime(...).timestamp()`（本地时区），实时为 `time.time()`；`_in_open_window` 用 `datetime.datetime.fromtimestamp(ts)`（本地时区）→ `dt.replace(hour=9,minute=30)`。两者本地时区一致，A 股 09:30 判定正确。

6. **不自动提交以外的 git 约束：** 每个任务末尾按 TDD 流程提交（本 superpowers 工作流已由用户批准，含 per-task commit）。提交信息用 conventional commits（`feat:`/`test:`/`chore:`），**不加 Co-Authored-By trailer**（全局已禁用 attribution）。

---

## Task 1: Config 字段 + env 加载 + .env.example + 测试 helper 更新

**Files:**
- Modify: `src/config.py`（`Config` dataclass 字段 ~873 行后；`_load_from_env` env 读取 ~1837 行后）
- Modify: `.env.example`（monitor 段，`AGENT_EVENT_MONITOR_REVERSAL_BARS` 行后）
- Modify: `tests/test_monitor.py:6-9`（imports）+ `tests/test_monitor.py:153-162`（`_make_default_rules_config`）
- Test: `tests/test_monitor.py`（新增 `TestMonitorDetectorConfig` 类）

- [ ] **Step 1: 写失败测试 — config 解析 7 字段（env 覆盖 + 默认）**

在 `tests/test_monitor.py` 顶部 imports 区，把第 6-9 行：
```python
import sys
import time
import unittest
from unittest.mock import MagicMock, patch
```
改为：
```python
import datetime
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch
```

在 `tests/test_monitor.py` **文件末尾**追加新测试类：
```python
class TestMonitorDetectorConfig(unittest.TestCase):
    """开盘冲高回落 + 急跌幅度 检测器的 config 字段解析。"""

    def test_config_loads_detector_fields_from_env(self):
        """config 应解析 7 个新盘中检测器字段（含 env 覆盖）。"""
        from src.config import Config
        env = {
            "AGENT_EVENT_MONITOR_OPEN_SURGE_REVERT_ENABLED": "false",
            "AGENT_EVENT_MONITOR_OPEN_SURGE_WINDOW_MINUTES": "20",
            "AGENT_EVENT_MONITOR_OPEN_SURGE_PCT": "2.5",
            "AGENT_EVENT_MONITOR_OPEN_REVERT_PCT": "0.8",
            "AGENT_EVENT_MONITOR_SHARP_DROP_ENABLED": "false",
            "AGENT_EVENT_MONITOR_SHARP_DROP_BARS": "8",
            "AGENT_EVENT_MONITOR_SHARP_DROP_PCT": "2.0",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = Config._load_from_env()
        self.assertFalse(cfg.agent_event_monitor_open_surge_revert_enabled)
        self.assertEqual(cfg.agent_event_monitor_open_surge_window_minutes, 20)
        self.assertAlmostEqual(cfg.agent_event_monitor_open_surge_pct, 2.5)
        self.assertAlmostEqual(cfg.agent_event_monitor_open_revert_pct, 0.8)
        self.assertFalse(cfg.agent_event_monitor_sharp_drop_enabled)
        self.assertEqual(cfg.agent_event_monitor_sharp_drop_bars, 8)
        self.assertAlmostEqual(cfg.agent_event_monitor_sharp_drop_pct, 2.0)

    def test_config_detector_fields_defaults(self):
        """无 env 时 7 字段取默认值（save/restore，避免污染本进程 env）。"""
        from src.config import Config
        env_keys = [
            "AGENT_EVENT_MONITOR_OPEN_SURGE_REVERT_ENABLED",
            "AGENT_EVENT_MONITOR_OPEN_SURGE_WINDOW_MINUTES",
            "AGENT_EVENT_MONITOR_OPEN_SURGE_PCT",
            "AGENT_EVENT_MONITOR_OPEN_REVERT_PCT",
            "AGENT_EVENT_MONITOR_SHARP_DROP_ENABLED",
            "AGENT_EVENT_MONITOR_SHARP_DROP_BARS",
            "AGENT_EVENT_MONITOR_SHARP_DROP_PCT",
        ]
        saved = {k: os.environ.pop(k, None) for k in env_keys}
        try:
            cfg = Config._load_from_env()
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
        self.assertTrue(cfg.agent_event_monitor_open_surge_revert_enabled)
        self.assertEqual(cfg.agent_event_monitor_open_surge_window_minutes, 15)
        self.assertAlmostEqual(cfg.agent_event_monitor_open_surge_pct, 1.5)
        self.assertAlmostEqual(cfg.agent_event_monitor_open_revert_pct, 0.5)
        self.assertTrue(cfg.agent_event_monitor_sharp_drop_enabled)
        self.assertEqual(cfg.agent_event_monitor_sharp_drop_bars, 5)
        self.assertAlmostEqual(cfg.agent_event_monitor_sharp_drop_pct, 1.5)


if __name__ == "__main__":
    unittest.main()
```

> 注：若 `tests/test_monitor.py` 末尾已有 `if __name__ == "__main__":` 块，则把新类 `TestMonitorDetectorConfig` 插在该块**之前**，不要重复添加 `if __name__` 块。先 grep 确认。

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /f/codeRepo/ai/agent/daily_stock_analysis && .venv/Scripts/python.exe tests/test_monitor.py -v 2>&1 | tail -30`
Expected: 2 个新测试 FAIL（`AttributeError: 'Config' object has no attribute 'agent_event_monitor_open_surge_revert_enabled'` 或类似）。

- [ ] **Step 3: 更新 `_make_default_rules_config`（避免 MagicMock float→0.0 陷阱）**

把 `tests/test_monitor.py:153-162`：
```python
    def _make_default_rules_config(self, stock_list=None, rules_json="", defaults_enabled=True, green_streak_mode="both"):
        """构建一个用于默认规则测试的 Mock Config。"""
        config = MagicMock()
        config.stock_list = stock_list if stock_list is not None else ["000725"]
        config.agent_event_alert_rules_json = rules_json
        config.agent_event_monitor_default_rules_enabled = defaults_enabled
        config.agent_event_monitor_green_streak_mode = green_streak_mode
        config.agent_event_monitor_reversal_bars = 3
        config.agent_mode = False
        return config
```
改为：
```python
    def _make_default_rules_config(self, stock_list=None, rules_json="", defaults_enabled=True, green_streak_mode="both"):
        """构建一个用于默认规则测试的 Mock Config。"""
        config = MagicMock()
        config.stock_list = stock_list if stock_list is not None else ["000725"]
        config.agent_event_alert_rules_json = rules_json
        config.agent_event_monitor_default_rules_enabled = defaults_enabled
        config.agent_event_monitor_green_streak_mode = green_streak_mode
        config.agent_event_monitor_reversal_bars = 3
        # 开盘冲高回落 + 急跌幅度 检测器配置（显式设值，避免 MagicMock float()/int() 静默返回 0.0/0）
        config.agent_event_monitor_open_surge_revert_enabled = True
        config.agent_event_monitor_open_surge_window_minutes = 15
        config.agent_event_monitor_open_surge_pct = 1.5
        config.agent_event_monitor_open_revert_pct = 0.5
        config.agent_event_monitor_sharp_drop_enabled = True
        config.agent_event_monitor_sharp_drop_bars = 5
        config.agent_event_monitor_sharp_drop_pct = 1.5
        config.agent_mode = False
        return config
```

- [ ] **Step 4: 在 `src/config.py` 的 `Config` dataclass 加 7 字段**

在 `src/config.py` 找到 `agent_event_monitor_reversal_bars: int = 3`（约 873 行，紧随 `agent_event_monitor_green_streak_mode` 之后）。在该行**之后**追加：
```python
        # 开盘冲高回落检测器（受 default_rules_enabled 总闸门 + 独立开关控制）
        agent_event_monitor_open_surge_revert_enabled: bool = True
        agent_event_monitor_open_surge_window_minutes: int = 15
        agent_event_monitor_open_surge_pct: float = 1.5
        agent_event_monitor_open_revert_pct: float = 0.5
        # 急跌幅度检测器
        agent_event_monitor_sharp_drop_enabled: bool = True
        agent_event_monitor_sharp_drop_bars: int = 5
        agent_event_monitor_sharp_drop_pct: float = 1.5
```
（保持与周围字段相同的缩进——`Config` 是 `@dataclass`，字段在 class body 内，通常 8 空格缩进。先 Read 该区域确认缩进。）

- [ ] **Step 5: 在 `src/config.py` 的 `_load_from_env` 加 7 个 env 读取**

在 `src/config.py` 找到 `agent_event_monitor_reversal_bars` 的 env 读取块（约 1837 行，形如 `parse_env_int(os.getenv('AGENT_EVENT_MONITOR_REVERSAL_BARS'), 3, field_name=..., minimum=1)`）。在该块**之后**（同一 `Config(...)` 构造调用内，逗号分隔）追加：
```python
        agent_event_monitor_open_surge_revert_enabled=parse_env_bool(
            os.getenv('AGENT_EVENT_MONITOR_OPEN_SURGE_REVERT_ENABLED'), True),
        agent_event_monitor_open_surge_window_minutes=parse_env_int(
            os.getenv('AGENT_EVENT_MONITOR_OPEN_SURGE_WINDOW_MINUTES'), 15,
            field_name='AGENT_EVENT_MONITOR_OPEN_SURGE_WINDOW_MINUTES', minimum=1),
        agent_event_monitor_open_surge_pct=parse_env_float(
            os.getenv('AGENT_EVENT_MONITOR_OPEN_SURGE_PCT'), 1.5,
            field_name='AGENT_EVENT_MONITOR_OPEN_SURGE_PCT', minimum=0.0),
        agent_event_monitor_open_revert_pct=parse_env_float(
            os.getenv('AGENT_EVENT_MONITOR_OPEN_REVERT_PCT'), 0.5,
            field_name='AGENT_EVENT_MONITOR_OPEN_REVERT_PCT', minimum=0.0),
        agent_event_monitor_sharp_drop_enabled=parse_env_bool(
            os.getenv('AGENT_EVENT_MONITOR_SHARP_DROP_ENABLED'), True),
        agent_event_monitor_sharp_drop_bars=parse_env_int(
            os.getenv('AGENT_EVENT_MONITOR_SHARP_DROP_BARS'), 5,
            field_name='AGENT_EVENT_MONITOR_SHARP_DROP_BARS', minimum=1),
        agent_event_monitor_sharp_drop_pct=parse_env_float(
            os.getenv('AGENT_EVENT_MONITOR_SHARP_DROP_PCT'), 1.5,
            field_name='AGENT_EVENT_MONITOR_SHARP_DROP_PCT', minimum=0.0),
```
> 若 `parse_env_float` 不支持 `minimum` 参数（grep `def parse_env_float` 确认签名），则对 3 个 float 字段去掉 `minimum=0.0`。`parse_env_bool(value, default)` 与 `parse_env_int(value, default, *, field_name, minimum=None)` 签名已核对存在。

- [ ] **Step 6: 在 `.env.example` 加 7 个注释变量**

在 `.env.example` 找到 `AGENT_EVENT_MONITOR_REVERSAL_BARS` 行（monitor 段）。在其**之后**追加：
```
# 开盘冲高回落检测器（开盘 N 分钟内冲高创局部高点后回落 → 一次性提示高点价位 + 下杀起点）
AGENT_EVENT_MONITOR_OPEN_SURGE_REVERT_ENABLED=true
# 开盘冲高窗口长度（分钟，自 09:30 起）；窗口内才追踪高点
AGENT_EVENT_MONITOR_OPEN_SURGE_WINDOW_MINUTES=15
# 冲高判定：相对开盘价涨幅 %（开盘波动大可调大，如 2.0）
AGENT_EVENT_MONITOR_OPEN_SURGE_PCT=1.5
# 回落判定：自开盘高点跌幅 %（想更早捕下杀可调小，如 0.3）
AGENT_EVENT_MONITOR_OPEN_REVERT_PCT=0.5
# 急跌幅度检测器（近 N 笔价格跌幅超阈 → 急跌预警）
AGENT_EVENT_MONITOR_SHARP_DROP_ENABLED=true
# 急跌回看笔数（想更早捕下杀可调小，如 3；更稳可调大，如 8）
AGENT_EVENT_MONITOR_SHARP_DROP_BARS=5
# 急跌判定：窗口跌幅 %（想更早捕下杀可调小，如 1.0）
AGENT_EVENT_MONITOR_SHARP_DROP_PCT=1.5
```

- [ ] **Step 7: 运行测试验证通过**

Run: `cd /f/codeRepo/ai/agent/daily_stock_analysis && .venv/Scripts/python.exe tests/test_monitor.py -v 2>&1 | tail -40`
Expected: 2 个 config 测试 PASS；既有测试全部仍 PASS（不回归）。

- [ ] **Step 8: 提交**

```bash
cd /f/codeRepo/ai/agent/daily_stock_analysis
git add src/config.py .env.example tests/test_monitor.py
git commit -m "feat(monitor): add config fields for open-surge-revert & sharp-drop detectors"
```

---

## Task 2: Monitor 状态字段 + 阈值 helper + 状态捕获

**Files:**
- Modify: `src/monitor.py:154-172`（`__init__` 状态声明）
- Modify: `src/monitor.py:255-261`（`_reversal_bars` 后加 8 helper）
- Modify: `src/monitor.py:299-301`（`evaluate_quote` 顶部状态捕获）
- Test: `tests/test_monitor.py`（`TestMonitor` 类内新增 3 用例）

- [ ] **Step 1: 写失败测试 — helper 读 config + 窗口边界 + 首笔种子**

在 `tests/test_monitor.py` 的 `TestMonitor` 类内（`_q` 方法之后，`test_default_anomaly_rules_injected` 之前或任意既有测试之间）追加：
```python
    def test_open_surge_and_sharp_drop_helpers_read_config(self):
        """7 个阈值 helper 应从 config 读出真实值（非 MagicMock 的 0.0/0）。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        self.assertTrue(monitor._open_surge_revert_enabled())
        self.assertEqual(monitor._open_surge_window_minutes(), 15)
        self.assertAlmostEqual(monitor._open_surge_pct(), 1.5)
        self.assertAlmostEqual(monitor._open_revert_pct(), 0.5)
        self.assertTrue(monitor._sharp_drop_enabled())
        self.assertEqual(monitor._sharp_drop_bars(), 5)
        self.assertAlmostEqual(monitor._sharp_drop_pct(), 1.5)

    def test_in_open_window_boundary(self):
        """开盘窗口 [09:30, 09:45) 左闭右开；09:45 与 10:00 不在窗口内。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        self.assertTrue(monitor._in_open_window(datetime.datetime(2026, 7, 13, 9, 30).timestamp()))
        self.assertTrue(monitor._in_open_window(datetime.datetime(2026, 7, 13, 9, 44, 59).timestamp()))
        self.assertFalse(monitor._in_open_window(datetime.datetime(2026, 7, 13, 9, 45).timestamp()))
        self.assertFalse(monitor._in_open_window(datetime.datetime(2026, 7, 13, 10, 0).timestamp()))

    def test_price_history_and_open_state_seeded_on_first_bar(self):
        """首笔行情（无 last_quote）应种子 open_price/open_high/open_day 并追加 price_history。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        with patch("src.monitor.NotificationService"):
            monitor.evaluate_quote(self._q(price=10.0, volume=100000), is_replay=True, replay_time="2026-07-13 09:30")
        self.assertAlmostEqual(monitor.open_price["000725"], 10.0)
        self.assertAlmostEqual(monitor.open_high["000725"], 10.0)
        self.assertEqual(monitor.open_day["000725"], "2026-07-13")
        self.assertFalse(monitor.open_surge_fired["000725"])
        self.assertEqual(len(monitor.price_history["000725"]), 1)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /f/codeRepo/ai/agent/daily_stock_analysis && .venv/Scripts/python.exe tests/test_monitor.py -v 2>&1 | tail -30`
Expected: 3 个新测试 FAIL（`AttributeError: 'RealtimeMonitor' object has no attribute '_open_surge_revert_enabled'` / `open_price` 等）。

- [ ] **Step 3: 在 `__init__` 加 5 个状态字段**

在 `src/monitor.py` 找到第 168-169 行：
```python
        self.price_dir_run: Dict[str, int] = {}
        self.flow_sign_run: Dict[str, int] = {}
```
在这两行**之后**（第 169 行后，第 170 空行前）追加：
```python
        # 急跌幅度检测：近期价格序列（固定上限窗口，按 bars 回看，含首笔）
        self.price_history = collections.defaultdict(lambda: collections.deque(maxlen=60))
        # 开盘冲高回落：日内开盘价 / 开盘窗口运行最高价 / 当日已触发标志 / 状态所属日期
        self.open_price: Dict[str, float] = {}
        self.open_high: Dict[str, float] = {}
        self.open_surge_fired: Dict[str, bool] = {}
        self.open_day: Dict[str, str] = {}
```
（`collections` 与 `Dict` 已在本文件 import——`__init__` 第 157 行已用 `collections.defaultdict`，第 155 行已用 `Dict[str, ...]`。无需新增 import。）

- [ ] **Step 4: 在 `_reversal_bars` 后加 8 个 helper**

在 `src/monitor.py` 找到 `_reversal_bars` 方法（第 255-261 行）：
```python
    def _reversal_bars(self) -> int:
        """红绿反转降噪所需的前序"连续同向"笔数（过滤单笔抖动）。读 config，最低 1。"""
        try:
            val = int(getattr(self.config, "agent_event_monitor_reversal_bars", DEFAULT_REVERSAL_BARS) or DEFAULT_REVERSAL_BARS)
        except (TypeError, ValueError):
            val = DEFAULT_REVERSAL_BARS
        return val if val >= 1 else DEFAULT_REVERSAL_BARS
```
在该方法**之后**（第 261 行后，`def run_check_cycle` 第 263 行前）追加：
```python
    def _open_surge_revert_enabled(self) -> bool:
        """是否注入开盘冲高回落规则（受总闸门 + 独立开关双重控制）。"""
        return bool(getattr(self.config, "agent_event_monitor_open_surge_revert_enabled", True))

    def _open_surge_window_minutes(self) -> int:
        """开盘冲高窗口长度（分钟，自 09:30 起），最低 1。"""
        try:
            val = int(getattr(self.config, "agent_event_monitor_open_surge_window_minutes", 15) or 15)
        except (TypeError, ValueError):
            val = 15
        return val if val >= 1 else 15

    def _open_surge_pct(self) -> float:
        """冲高判定：相对开盘价涨幅 %。"""
        try:
            return float(getattr(self.config, "agent_event_monitor_open_surge_pct", 1.5) or 1.5)
        except (TypeError, ValueError):
            return 1.5

    def _open_revert_pct(self) -> float:
        """回落判定：自开盘高点跌幅 %。"""
        try:
            return float(getattr(self.config, "agent_event_monitor_open_revert_pct", 0.5) or 0.5)
        except (TypeError, ValueError):
            return 0.5

    def _sharp_drop_enabled(self) -> bool:
        """是否注入急跌幅度规则（受总闸门 + 独立开关双重控制）。"""
        return bool(getattr(self.config, "agent_event_monitor_sharp_drop_enabled", True))

    def _sharp_drop_bars(self) -> int:
        """急跌回看笔数，最低 1。"""
        try:
            val = int(getattr(self.config, "agent_event_monitor_sharp_drop_bars", 5) or 5)
        except (TypeError, ValueError):
            val = 5
        return val if val >= 1 else 5

    def _sharp_drop_pct(self) -> float:
        """急跌判定：窗口跌幅 %。"""
        try:
            return float(getattr(self.config, "agent_event_monitor_sharp_drop_pct", 1.5) or 1.5)
        except (TypeError, ValueError):
            return 1.5

    def _in_open_window(self, ts: float) -> bool:
        """当前时间戳是否落在开盘冲高窗口 [09:30, 09:30+window) 内（A 股，本地时区）。"""
        try:
            dt = datetime.datetime.fromtimestamp(ts)
        except Exception:
            return False
        start = dt.replace(hour=9, minute=30, second=0, microsecond=0)
        end = start + datetime.timedelta(minutes=self._open_surge_window_minutes())
        return start <= dt < end
```
（`datetime` 已在本文件 import——第 294 行已用 `datetime.datetime.strptime`。`datetime.timedelta` 同模块可用。）

- [ ] **Step 5: 在 `evaluate_quote` 顶部加状态捕获（`if last_quote:` 之前）**

在 `src/monitor.py` 找到第 298-301 行：
```python
        else:
            current_time_val = time.time()

        last_quote = self.last_quotes.get(code)
```
在 `current_time_val = time.time()`（第 299 行）与 `last_quote = self.last_quotes.get(code)`（第 301 行）之间插入：
```python

        # 开盘冲高回落 / 急跌幅度 状态捕获（须在 last_quote 判定之前，确保首笔种子 open_price）
        if self._sharp_drop_enabled() and quote.price is not None:
            self.price_history[code].append(quote.price)
        if self._open_surge_revert_enabled() and quote.price is not None:
            try:
                _now_dt = datetime.datetime.fromtimestamp(current_time_val)
            except Exception:
                _now_dt = None
            if _now_dt is not None:
                _today = _now_dt.strftime("%Y-%m-%d")
                if self.open_day.get(code) != _today:
                    self.open_price[code] = quote.price
                    self.open_high[code] = quote.price
                    self.open_surge_fired[code] = False
                    self.open_day[code] = _today
                if self._in_open_window(current_time_val):
                    if quote.price > self.open_high.get(code, quote.price):
                        self.open_high[code] = quote.price
```

- [ ] **Step 6: 运行测试验证通过**

Run: `cd /f/codeRepo/ai/agent/daily_stock_analysis && .venv/Scripts/python.exe tests/test_monitor.py -v 2>&1 | tail -40`
Expected: 3 个新测试 PASS；既有测试全部 PASS（不回归）。

- [ ] **Step 7: 提交**

```bash
cd /f/codeRepo/ai/agent/daily_stock_analysis
git add src/monitor.py tests/test_monitor.py
git commit -m "feat(monitor): add state, helpers & top-of-quote capture for surge-revert/sharp-drop"
```

---

## Task 3: `open_surge_revert` 规则注入 + 派发 + 测试

**Files:**
- Modify: `src/monitor.py:225`（`_inject_default_rules` 循环末尾追加注入）
- Modify: `src/monitor.py:465-467`（`evaluate_quote` 派发链加分支）
- Test: `tests/test_monitor.py`（`TestMonitor` 类内新增 6 用例）

- [ ] **Step 1: 写失败测试 — 6 个 open_surge_revert 用例**

在 `tests/test_monitor.py` 的 `TestMonitor` 类内追加：
```python
    def test_open_surge_revert_fires_once(self):
        """开盘冲高(+2%≥1.5%)后回落(自高点跌0.98%≥0.5%)→触发一次，标记高点价 10.20。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        with patch("src.monitor.NotificationService"):
            monitor.evaluate_quote(self._q(price=10.00, volume=100000), is_replay=True, replay_time="2026-07-13 09:30")
            monitor.evaluate_quote(self._q(price=10.20, volume=120000), is_replay=True, replay_time="2026-07-13 09:35")
            monitor.evaluate_quote(self._q(price=10.10, volume=130000), is_replay=True, replay_time="2026-07-13 09:40")
        hits = [t for t in monitor._replay_triggers if "开盘冲高" in t]
        self.assertEqual(len(hits), 1, f"expected one open-surge-revert, got {monitor._replay_triggers}")
        self.assertIn("10.20", hits[0])
        self.assertTrue(monitor.open_surge_fired["000725"])

    def test_open_surge_revert_no_spike_no_fire(self):
        """开盘即跌(无冲高)→不触发(交给 sharp_drop)。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        with patch("src.monitor.NotificationService"):
            monitor.evaluate_quote(self._q(price=10.00, volume=100000), is_replay=True, replay_time="2026-07-13 09:30")
            monitor.evaluate_quote(self._q(price=9.95, volume=110000), is_replay=True, replay_time="2026-07-13 09:35")
            monitor.evaluate_quote(self._q(price=9.90, volume=120000), is_replay=True, replay_time="2026-07-13 09:40")
        self.assertFalse(any("开盘冲高" in t for t in monitor._replay_triggers), monitor._replay_triggers)

    def test_open_surge_revert_no_revert_no_fire(self):
        """冲高后继续上行(无回落)→不触发。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        with patch("src.monitor.NotificationService"):
            monitor.evaluate_quote(self._q(price=10.00, volume=100000), is_replay=True, replay_time="2026-07-13 09:30")
            monitor.evaluate_quote(self._q(price=10.20, volume=120000), is_replay=True, replay_time="2026-07-13 09:35")
            monitor.evaluate_quote(self._q(price=10.30, volume=130000), is_replay=True, replay_time="2026-07-13 09:40")
        self.assertFalse(any("开盘冲高" in t for t in monitor._replay_triggers), monitor._replay_triggers)

    def test_open_surge_revert_one_shot(self):
        """冲高回落触发后，再冲高回落→仅触发 1 次(fired 标志)。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        with patch("src.monitor.NotificationService"):
            monitor.evaluate_quote(self._q(price=10.00, volume=100000), is_replay=True, replay_time="2026-07-13 09:30")
            monitor.evaluate_quote(self._q(price=10.20, volume=120000), is_replay=True, replay_time="2026-07-13 09:35")
            monitor.evaluate_quote(self._q(price=10.10, volume=130000), is_replay=True, replay_time="2026-07-13 09:40")
            monitor.evaluate_quote(self._q(price=10.25, volume=140000), is_replay=True, replay_time="2026-07-13 09:42")
            monitor.evaluate_quote(self._q(price=10.10, volume=150000), is_replay=True, replay_time="2026-07-13 09:44")
        self.assertEqual(len([t for t in monitor._replay_triggers if "开盘冲高" in t]), 1)

    def test_open_surge_revert_resets_next_day(self):
        """跨日 open_day 变更→fired 复位，可再次触发。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        with patch("src.monitor.NotificationService"):
            monitor.evaluate_quote(self._q(price=10.00, volume=100000), is_replay=True, replay_time="2026-07-13 09:30")
            monitor.evaluate_quote(self._q(price=10.20, volume=120000), is_replay=True, replay_time="2026-07-13 09:35")
            monitor.evaluate_quote(self._q(price=10.10, volume=130000), is_replay=True, replay_time="2026-07-13 09:40")
            monitor.evaluate_quote(self._q(price=10.00, volume=100000), is_replay=True, replay_time="2026-07-14 09:30")
            monitor.evaluate_quote(self._q(price=10.20, volume=120000), is_replay=True, replay_time="2026-07-14 09:35")
            monitor.evaluate_quote(self._q(price=10.10, volume=130000), is_replay=True, replay_time="2026-07-14 09:40")
        self.assertEqual(len([t for t in monitor._replay_triggers if "开盘冲高" in t]), 2)

    def test_open_surge_revert_out_of_window_no_high_update(self):
        """10:00(窗口外)不更新 open_high，open_high 停在首笔 10.00，无 spike 不触发。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        with patch("src.monitor.NotificationService"):
            monitor.evaluate_quote(self._q(price=10.00, volume=100000), is_replay=True, replay_time="2026-07-13 10:00")
            monitor.evaluate_quote(self._q(price=10.20, volume=120000), is_replay=True, replay_time="2026-07-13 10:05")
            monitor.evaluate_quote(self._q(price=10.10, volume=130000), is_replay=True, replay_time="2026-07-13 10:10")
        self.assertFalse(any("开盘冲高" in t for t in monitor._replay_triggers), monitor._replay_triggers)
        self.assertAlmostEqual(monitor.open_high["000725"], 10.00)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /f/codeRepo/ai/agent/daily_stock_analysis && .venv/Scripts/python.exe tests/test_monitor.py -v 2>&1 | tail -30`
Expected: `test_open_surge_revert_fires_once` / `resets_next_day` FAIL（无规则注入 → 派发分支不存在 → 不触发）；其余负面用例可能 PASS（本就不触发）。`out_of_window_no_high_update` 可能 FAIL（`open_high` 逻辑尚未生效——但 Task 2 已加状态捕获，应 PASS；若 Task 2 已合入则此处仅 fires/resets 失败）。

- [ ] **Step 3: 在 `_inject_default_rules` 注入 `open_surge_revert` 规则**

在 `src/monitor.py` 找到 `_inject_default_rules` 的循环体末尾（第 218-225 行的 `green_streak_sell` 追加块）：
```python
            enriched.append({
                "stock_code": code,
                "type": "green_streak_sell",
                "threshold": DEFAULT_GREEN_STREAK_BARS,
                "mode": self._green_streak_mode(),
                "volume_ratio": DEFAULT_GREEN_STREAK_VOLUME_RATIO,
                "_default": True,
            })
        return enriched
```
在 `})`（第 225 行）与 `return enriched`（第 226 行）之间，**仍在 `for code in self.stock_list:` 循环内**（12 空格缩进），插入：
```python
            if self._open_surge_revert_enabled():
                enriched.append({
                    "stock_code": code,
                    "type": "open_surge_revert",
                    "surge_pct": self._open_surge_pct(),
                    "revert_pct": self._open_revert_pct(),
                    "window": self._open_surge_window_minutes(),
                    "_default": True,
                })
```

- [ ] **Step 4: 在 `evaluate_quote` 派发链加 `open_surge_revert` 分支**

在 `src/monitor.py` 找到 `green_streak_sell` 分支末尾（第 460-466 行）：
```python
                    if _hit and _volume_ok:
                        triggered = True
                        rule_desc = (
                            f"{_detail} 且放量(区间成交 {delta_v / 100:.0f} 手，为近均值的 "
                            f"{delta_v / _avg_v:.1f} 倍) → ⚠️ 注意卖出止盈/止损"
                        )

                # 3. 触发预警逻辑
```
在 `green_streak_sell` 块的 `)`（第 465 行）与 `# 3. 触发预警逻辑`（第 467 行）之间，**仍在 `for rule in self.rules:` 循环内**（16 空格缩进，与 `elif r_type == "green_streak_sell":` 同级），插入：
```python
                # H. 开盘冲高回落：开盘窗口内冲高创局部高点后回落 → 一次性提示高点价位 + 下杀起点
                elif r_type == "open_surge_revert":
                    _raw_sp = rule.get("surge_pct")
                    _surge_pct = float(_raw_sp) if _raw_sp is not None else self._open_surge_pct()
                    _raw_rp = rule.get("revert_pct")
                    _revert_pct = float(_raw_rp) if _raw_rp is not None else self._open_revert_pct()
                    _o_high = self.open_high.get(code)
                    _o_price = self.open_price.get(code)
                    if (_o_high is not None and _o_price is not None and _o_price > 0
                            and not self.open_surge_fired.get(code, False) and quote.price is not None):
                        _spike = _o_high >= _o_price * (1.0 + _surge_pct / 100.0)
                        _revert = _o_high > 0 and (_o_high - quote.price) / _o_high * 100.0 >= _revert_pct
                        if _spike and _revert:
                            self.open_surge_fired[code] = True
                            triggered = True
                            rule_desc = (
                                f"开盘冲高至 {_o_high:.2f} 元后回落"
                                f"（跌幅 {(_o_high - quote.price) / _o_high * 100.0:.2f}%），⚠️ 注意下杀风险"
                            )
```

- [ ] **Step 5: 运行测试验证通过**

Run: `cd /f/codeRepo/ai/agent/daily_stock_analysis && .venv/Scripts/python.exe tests/test_monitor.py -v 2>&1 | tail -40`
Expected: 6 个 open_surge 测试 PASS；既有测试全部 PASS。

- [ ] **Step 6: 提交**

```bash
cd /f/codeRepo/ai/agent/daily_stock_analysis
git add src/monitor.py tests/test_monitor.py
git commit -m "feat(monitor): add open-surge-revert detector (intraday high + drop-start alert)"
```

---

## Task 4: `sharp_drop` 规则注入 + 派发 + 测试

**Files:**
- Modify: `src/monitor.py:225`（`_inject_default_rules` 循环末尾，紧随 Task 3 的 open_surge 注入后追加）
- Modify: `src/monitor.py:465-467`（`evaluate_quote` 派发链，紧随 Task 3 的 open_surge 分支后加分支）
- Test: `tests/test_monitor.py`（`TestMonitor` 类内新增 4 用例）

- [ ] **Step 1: 写失败测试 — 4 个 sharp_drop 用例**

在 `tests/test_monitor.py` 的 `TestMonitor` 类内追加：
```python
    def test_sharp_drop_fires(self):
        """5 笔内自首笔跌 2%(≥1.5%)→触发急跌，rule_desc 含 '近 5 笔'。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        with patch("src.monitor.NotificationService"):
            for i, p in enumerate([10.00, 9.90, 9.85, 9.80, 9.80]):
                monitor.evaluate_quote(self._q(price=p, volume=100000), is_replay=True, replay_time=f"2026-07-13 10:0{i}")
        hits = [t for t in monitor._replay_triggers if "急跌" in t]
        self.assertEqual(len(hits), 1, f"expected sharp-drop trigger, got {monitor._replay_triggers}")
        self.assertIn("近 5 笔", hits[0])

    def test_sharp_drop_subthreshold_no_fire(self):
        """5 笔累计跌 1%(<1.5%)→不触发。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        with patch("src.monitor.NotificationService"):
            for i, p in enumerate([10.00, 9.98, 9.97, 9.96, 9.90]):
                monitor.evaluate_quote(self._q(price=p, volume=100000), is_replay=True, replay_time=f"2026-07-13 10:0{i}")
        self.assertFalse(any("急跌" in t for t in monitor._replay_triggers), monitor._replay_triggers)

    def test_sharp_drop_insufficient_bars_no_fire(self):
        """仅 3 笔(<5)→不触发。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        with patch("src.monitor.NotificationService"):
            monitor.evaluate_quote(self._q(price=10.00, volume=100000), is_replay=True, replay_time="2026-07-13 10:00")
            monitor.evaluate_quote(self._q(price=9.80, volume=110000), is_replay=True, replay_time="2026-07-13 10:01")
            monitor.evaluate_quote(self._q(price=9.60, volume=120000), is_replay=True, replay_time="2026-07-13 10:02")
        self.assertFalse(any("急跌" in t for t in monitor._replay_triggers), monitor._replay_triggers)

    def test_sharp_drop_cooldown(self):
        """触发后 15 分钟内同码再达阈值→不重复触发(冷却)。"""
        monitor = RealtimeMonitor(self._make_default_rules_config())
        monitor._replay_triggers = []
        with patch("src.monitor.NotificationService"):
            for i, p in enumerate([10.00, 9.90, 9.85, 9.80, 9.80]):
                monitor.evaluate_quote(self._q(price=p, volume=100000), is_replay=True, replay_time=f"2026-07-13 10:0{i}")
            monitor.evaluate_quote(self._q(price=9.60, volume=100000), is_replay=True, replay_time="2026-07-13 10:05")
        hits = [t for t in monitor._replay_triggers if "急跌" in t]
        self.assertEqual(len(hits), 1, f"cooldown should block 2nd fire, got {monitor._replay_triggers}")
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /f/codeRepo/ai/agent/daily_stock_analysis && .venv/Scripts/python.exe tests/test_monitor.py -v 2>&1 | tail -30`
Expected: `test_sharp_drop_fires` / `test_sharp_drop_cooldown` FAIL（无 sharp_drop 规则注入 → 不触发）；`subthreshold` / `insufficient_bars` PASS（本就不触发）。

- [ ] **Step 3: 在 `_inject_default_rules` 注入 `sharp_drop` 规则**

在 `src/monitor.py` 找到 Task 3 刚插入的 `open_surge_revert` 注入块（`_inject_default_rules` 循环内）：
```python
            if self._open_surge_revert_enabled():
                enriched.append({
                    "stock_code": code,
                    "type": "open_surge_revert",
                    "surge_pct": self._open_surge_pct(),
                    "revert_pct": self._open_revert_pct(),
                    "window": self._open_surge_window_minutes(),
                    "_default": True,
                })
```
在该块**之后**（仍在 `for code in self.stock_list:` 循环内，`return enriched` 之前）追加：
```python
            if self._sharp_drop_enabled():
                enriched.append({
                    "stock_code": code,
                    "type": "sharp_drop",
                    "bars": self._sharp_drop_bars(),
                    "drop_pct": self._sharp_drop_pct(),
                    "_default": True,
                })
```

- [ ] **Step 4: 在 `evaluate_quote` 派发链加 `sharp_drop` 分支**

在 `src/monitor.py` 找到 Task 3 刚插入的 `open_surge_revert` 分支末尾（`rule_desc = (... ⚠️ 注意下杀风险")` 块之后）。在该 `elif r_type == "open_surge_revert":` 块**之后**、`# 3. 触发预警逻辑`（第 467 行）**之前**，追加：
```python
                # I. 急跌幅度：近 N 笔价格跌幅超阈（用近期价格窗口，非 change_pct，避免盘中仍涨时漏报）
                elif r_type == "sharp_drop":
                    _raw_b = rule.get("bars")
                    _bars = int(_raw_b) if _raw_b is not None else self._sharp_drop_bars()
                    _raw_dp = rule.get("drop_pct")
                    _drop_pct = float(_raw_dp) if _raw_dp is not None else self._sharp_drop_pct()
                    _hist = self.price_history[code]
                    if _bars >= 1 and len(_hist) >= _bars and quote.price is not None:
                        _p0 = _hist[-_bars]
                        if _p0 > 0:
                            _drop_observed = (_p0 - quote.price) / _p0 * 100.0
                            if _drop_observed >= _drop_pct:
                                triggered = True
                                rule_desc = (
                                    f"近 {_bars} 笔跌幅 {_drop_observed:.2f}%"
                                    f"（{_p0:.2f} → {quote.price:.2f}），⚠️ 急跌"
                                )
```

- [ ] **Step 5: 运行测试验证通过**

Run: `cd /f/codeRepo/ai/agent/daily_stock_analysis && .venv/Scripts/python.exe tests/test_monitor.py -v 2>&1 | tail -40`
Expected: 4 个 sharp_drop 测试 PASS；既有测试全部 PASS。

- [ ] **Step 6: 提交**

```bash
cd /f/codeRepo/ai/agent/daily_stock_analysis
git add src/monitor.py tests/test_monitor.py
git commit -m "feat(monitor): add sharp-drop detector (N-bar price drop alert)"
```

---

## Task 5: 回放清理块更新 + 回放集成测试

**Files:**
- Modify: `src/monitor.py:652-661`（pre-replay 清理块 E）
- Modify: `src/monitor.py:688-697`（post-replay 清理块 G）
- Test: `tests/test_monitor.py`（`TestMonitor` 类内新增 1 集成用例）

- [ ] **Step 1: 写失败测试 — run_replay_simulation 触发两检测器 + 事后清理状态**

在 `tests/test_monitor.py` 的 `TestMonitor` 类内追加：
```python
    def test_run_replay_emits_both_detectors_and_clears_state(self):
        """run_replay_simulation 应在分钟序列中触发 open_surge_revert + sharp_drop，且事后清掉 price_history。"""
        monitor = RealtimeMonitor(self._make_default_rules_config(stock_list=["000725"]))
        monitor._replay_triggers = []

        # 静态行情（run_replay 用其 name/pre_close/circ_mv/source 作为每笔基础信息）
        static_quote = UnifiedRealtimeQuote(
            code="000725", name="京东方A", source=RealtimeSource.TENCENT,
            price=10.0, volume=0, main_net_inflow=0.0, large_net_inflow=0.0,
            pre_close=10.0, circ_mv=10000000000.0,
        )

        # 分时趋势字符串: parts[0]=time, parts[2]=price, parts[5]=vol(手)，至少 8 字段
        # 含冲高回落(09:30 10.00 → 09:35 10.20 → 09:40 10.10) + 急跌(10:00-10:04 共5笔跌至 9.80)
        trends = [
            "2026-07-13 09:30,10.0,10.00,0,0,100,0,0",
            "2026-07-13 09:35,10.1,10.20,0,0,120,0,0",
            "2026-07-13 09:40,10.1,10.10,0,0,130,0,0",
            "2026-07-13 10:00,10.0,10.00,0,0,100,0,0",
            "2026-07-13 10:01,9.95,9.90,0,0,110,0,0",
            "2026-07-13 10:02,9.9,9.85,0,0,120,0,0",
            "2026-07-13 10:03,9.85,9.80,0,0,130,0,0",
            "2026-07-13 10:04,9.85,9.80,0,0,140,0,0",
        ]

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"data": {"trends": trends}}

        with patch.object(monitor.fetcher_mgr, "get_realtime_quote", return_value=static_quote), \
             patch("src.monitor.fetch_fflow_klines", return_value=[["2026-07-13 09:30", "0.0", "0", "0", "0.0", "0.0"]]), \
             patch("src.monitor.requests.get", return_value=fake_resp):
            monitor.run_replay_simulation()

        triggers = monitor._replay_triggers
        self.assertTrue(any("开盘冲高" in t for t in triggers), f"missing open-surge-revert, got {triggers}")
        self.assertTrue(any("急跌" in t for t in triggers), f"missing sharp-drop, got {triggers}")
        # 事后清理（G 块）应清掉 price_history，防止污染实时内存
        self.assertEqual(len(monitor.price_history["000725"]), 0)
```

> **Mock 要点（实现者核对）：**
> - `fetch_fflow_klines` 在 `src/monitor.py` 顶部以 `from data_provider.eastmoney_flow import fetch_fflow_klines` 导入（第 562 行裸调用），故 `patch("src.monitor.fetch_fflow_klines", ...)` 生效。若导入方式不同，调整 patch 目标。
> - `requests` 在 `src/monitor.py` 已 import（第 589 行 `requests.get`），`patch("src.monitor.requests.get", ...)` 生效。
> - `fetch_fflow_klines` 返回非空（否则第 577 行 `continue` 跳过该股），故至少返回 1 条 kline；趋势时间无匹配 flow 时 `flow_map.get(time_str, 默认0.0)`，不影响价格类检测器。
> - 趋势字符串必须 ≥8 字段（第 612 行 `if len(parts) < 8: continue`），时间 `HH:MM` 须在 09:30–11:30 / 13:00–15:00（第 619 行过滤）。

- [ ] **Step 2: 运行测试验证失败**

Run: `cd /f/codeRepo/ai/agent/daily_stock_analysis && .venv/Scripts/python.exe tests/test_monitor.py -v 2>&1 | tail -20`
Expected: `test_run_replay_emits_both_detectors_and_clears_state` FAIL —— 前两个断言（两检测器触发）应 PASS（Task 3/4 已实现派发），但 `assertEqual(len(monitor.price_history["000725"]), 0)` FAIL：因 post-replay 清理块 G（第 688-697 行）尚未清 `price_history`，回放后残留 8 笔。Step 3/4 补清理行后转 PASS。

- [ ] **Step 3: 在 pre-replay 清理块 E 加新状态清理**

在 `src/monitor.py` 找到 pre-replay 清理块（第 652-661 行）：
```python
            # E. 初始化该股票的回放队列环境，清除缓存
            self.volume_history[code].clear()
            self.flow_history[code].clear()
            self.large_flow_history[code].clear()
            self.last_quotes.pop(code, None)
            self.prev_price_direction.pop(code, None)
            self.prev_large_sign.pop(code, None)
            self.consecutive_price_green.pop(code, None)
            self.consecutive_flow_green.pop(code, None)
            self.price_dir_run.pop(code, None)
            self.flow_sign_run.pop(code, None)
```
在 `self.flow_sign_run.pop(code, None)`（第 661 行）**之后**追加：
```python
            self.price_history[code].clear()
            self.open_price.pop(code, None)
            self.open_high.pop(code, None)
            self.open_surge_fired.pop(code, None)
            self.open_day.pop(code, None)
```

- [ ] **Step 4: 在 post-replay 清理块 G 加新状态清理**

在 `src/monitor.py` 找到 post-replay 清理块（第 688-697 行）：
```python
            # G. 清理该股票的历史缓存，防止污染实时内存
            self.volume_history[code].clear()
            self.flow_history[code].clear()
            self.large_flow_history[code].clear()
            self.last_quotes.pop(code, None)
            self.prev_price_direction.pop(code, None)
            self.prev_large_sign.pop(code, None)
            self.consecutive_price_green.pop(code, None)
            self.consecutive_flow_green.pop(code, None)
            self.price_dir_run.pop(code, None)
            self.flow_sign_run.pop(code, None)
```
在 `self.flow_sign_run.pop(code, None)`（第 697 行）**之后**追加：
```python
            self.price_history[code].clear()
            self.open_price.pop(code, None)
            self.open_high.pop(code, None)
            self.open_surge_fired.pop(code, None)
            self.open_day.pop(code, None)
```

- [ ] **Step 5: 运行测试验证通过**

Run: `cd /f/codeRepo/ai/agent/daily_stock_analysis && .venv/Scripts/python.exe tests/test_monitor.py -v 2>&1 | tail -40`
Expected: 全部测试 PASS（含 Task 1-4 新增 + 既有 + Task 5 新增）。

- [ ] **Step 6: 提交**

```bash
cd /f/codeRepo/ai/agent/daily_stock_analysis
git add src/monitor.py tests/test_monitor.py
git commit -m "feat(monitor): clear surge-revert/sharp-drop state in replay cleanup blocks"
```

---

## Task 6: 全量回归验证

**Files:** 无改动（仅验证）

- [ ] **Step 1: 全量跑 monitor 测试**

Run: `cd /f/codeRepo/ai/agent/daily_stock_analysis && .venv/Scripts/python.exe tests/test_monitor.py -v 2>&1 | tail -60`
Expected: 全部 PASS。统计新增用例数：2（config）+ 3（helper/state）+ 6（open_surge）+ 4（sharp_drop）+ 1（replay clear）= **16 个新用例**，既有用例零回归。

- [ ] **Step 2: config 导入冒烟（确认无语法/导入错误）**

Run: `cd /f/codeRepo/ai/agent/daily_stock_analysis && .venv/Scripts/python.exe -c "from src.config import get_config; from src.monitor import RealtimeMonitor; print('imports OK')"`
Expected: 输出 `imports OK`（无 ImportError / SyntaxError）。

- [ ] **Step 3: 检查 diff 完整性**

Run: `cd /f/codeRepo/ai/agent/daily_stock_analysis && git diff main --stat`
Expected: 仅 4 文件改动——`src/config.py`、`src/monitor.py`、`.env.example`、`tests/test_monitor.py`。无意外文件。

- [ ] **Step 4: （可选）回放实跑验证**

若在交易时段且需端到端确认，可设 `AGENT_EVENT_MONITOR_REPLAY_DEBUG=true` 并触发 `run_replay_simulation`，观察日志中冲高回落 + 急跌的 `[回放预警触发]` 行。此步需东财联网，非必做——单测已覆盖核心逻辑。

- [ ] **Step 5: 标记完成**

全部测试通过、diff 干净 → 实现完成。交由 `superpowers:finishing-a-development-branch` 处理分支收尾（merge / PR / keep）。

---

## 非目标（YAGNI，不在本计划内）

- 不改前端 `AlertType` 枚举（那是另一套 portfolio 告警系统，与本盘中盯盘无关）。
- 不加非 A 股开盘时间适配（`is_within_trading_hours` 已假定 A 股 09:30）。
- 不为现有 5 条规则补个别 enable 开关（仅新检测器带独立 enable）。
- 不加多 host / 节流（东财资金流加固已在 `fe1d200d` 入 main，与本批无关）。
- 不加 `sharp_drop` 的"加速跌"（每笔跌幅率）变体——先用 N 笔累计跌幅，YAGNI。
