# WebUI 盘中盯盘 运行时启停（开始/停止按钮 + API）

## 背景
盘中盯盘 (`RealtimeMonitor`) 目前只能通过 `.env` 的 `AGENT_EVENT_MONITOR_ENABLED=true` 在 serve/schedule 启动时自动拉起后台线程，改开关需重启服务。目标：在 WebUI 增加「开始/停止盯盘」按钮，运行时即可启停，无需重启。

## 现状（已调研）
- `RealtimeMonitor.start()` (src/monitor.py:740) 循环 `run_check_cycle()` + `time.sleep(interval)`；已有 `stop()` (src/monitor.py:754) 仅置 `_running=False`，但 `time.sleep` 阻塞 → stop 最多延迟一个 interval（默认 300s）才生效。
- `start_monitor_thread(config)` (src/monitor.py:758) 创建守护线程后 **fire-and-forget**：无全局句柄，外部无法 stop / 查状态。
- 自动启动点：main.py:1378（serve）、main.py:1540（schedule），都调 `start_monitor_thread(config)`。
- 模板：`/api/v1/system/scheduler/status` + `/system/scheduler/run-now`（system_config.py）已通过 API 控制 in-process 后台服务，照此模式做。
- 前端：`apps/dsa-web/src/api/`（每域一个 client，如 systemConfig.ts），`AlertsPage.tsx`(14K) / `SettingsPage.tsx`(72K) 均存在。

## 实施步骤

### 后端
1. **src/monitor.py** — 全局注册表 + 响应式停止
   - `__init__`：加 `self._stop_event = threading.Event()`
   - `start()`：`time.sleep(interval)` → `self._stop_event.wait(interval)`，循环退出补 log
   - `stop()`：`_running=False` + `_stop_event.set()` → 立即唤醒
   - 模块级注册表（带 `threading.Lock`）：`_monitor_state = {"monitor": None, "thread": None}`
   - 改造 `start_monitor_thread(config)`：先 `stop_monitor_thread()` 停旧的 → 新建 → 注册 → 返回 thread
   - 新增 `stop_monitor_thread()`：调 `monitor.stop()`、清理注册表（不 join 或短超时 join，避免请求卡住）
   - 新增 `get_monitor_status() -> dict`：`running` / `thread_alive` / `stock_list` / `interval_seconds` / `rules_count`
   - main.py 自动启动点 **无需改**（走同一个 start_monitor_thread，自动注册）

2. **api/v1/endpoints/monitor.py**（新文件，仿 system_config 的 scheduler 端点）
   - `router = APIRouter()`
   - `GET /status` → `get_monitor_status()`
   - `POST /start` → 从 `get_config()` 拿最新配置 → `start_monitor_thread(config)` → 返回 status（始终从最新 config 重建，使 Settings 改的 interval/rules 即时生效）
   - `POST /stop` → `stop_monitor_thread()` → 返回 status
   - 鉴权：沿用全局 auth middleware（同 `/system/scheduler/*`，不额外声明 Security）

3. **api/v1/router.py** — 注册：`import monitor` + `router.include_router(monitor.router, prefix="/monitor", tags=["Monitor"])`

### 前端
4. **apps/dsa-web/src/api/monitor.ts**（新）— `monitorApi.status()/start()/stop()`
5. **页面盯盘控制卡**（位置见下方开放问题）— 状态灯 + 开始/停止按钮 + 间隔/股票池/规则数显示；start/stop 后刷新 status

### 测试与验证
6. **tests/test_monitor.py** — 加：注册表启停（start→status.running=True→stop→False）、stop 立即唤醒（Event）、status 字段
7. 运行 `.venv/Scripts/python.exe tests/test_monitor.py -v`
8. 手动：serve 模式下 `curl /api/v1/monitor/status|start|stop`；WebUI 点按钮观察状态切换

## 开放问题
- **按钮放哪页？** AlertsPage（盯盘/告警操作中心，推荐）还是 SettingsPage（与 AGENT_EVENT_MONITOR_ENABLED 开关同页）？

## Review（完成后填）
- **后端**：`src/monitor.py` 加 `_stop_event` + 模块注册表(`_monitor_state`/Lock) + `stop_monitor_thread()`/`get_monitor_status()`；`start()` 改 `Event.wait`、`stop()` 置 event；`start_monitor_thread` 先停旧再注册（main.py 自动启动点无需改，自动入注册表）。新端点 `api/v1/endpoints/monitor.py`(status/start/stop) 注册到 `/api/v1/monitor`。
- **前端**：`api/monitor.ts` + `components/monitor/MonitorControlCard.tsx`(状态灯/启停按钮/间隔·股票池·规则数，10s 自刷新)，挂到 `AlertsPage.tsx` 顶部。命名导出，遵循现有组件约定。
- **测试**：`tests/test_monitor.py` 新增 2 个用例（注册表启停 + Event 即时唤醒；空闲 stop 幂等）。` Ran 17 tests ... OK`（含原 15 + 新 2）。
- **类型**：`tsc --noEmit -p tsconfig.app.json` → exit=0，0 errors。
- **冒烟**：`get_monitor_status()` 返回 sane dict；端点 routes `/status /start /stop`；`api.v1.router` 导入无循环依赖。
- **遗留/未做**：未做浏览器端 e2e；未在真实 serve+盘中环境实跑（需用户在交易时段验证 green_streak 实时触发）。改动未提交（待用户确认是否 commit，会先开分支）。

---

# 反转预警降噪（红绿转换过于冗余）

## 背景
用户反馈 `[回放预警触发] 09:35 价格方向反转 绿转红（上笔绿 → 本笔红）` 等反转预警太吵——`price_reversal`/`flow_reversal` 0 冷却、任意一次方向翻转即报。经 AskUserQuestion 确认（语义先行，遵 memory lesson），用户选 **「保留但降噪」**：保留两个反转规则，但加 15 分钟冷却 + 前序需连续 N 根同向才算「有效反转」（过滤单笔抖动）。

## 实施（已完成）
- **src/monitor.py**
  - 常量 `DEFAULT_REVERSAL_BARS = 3`；新增 `_reversal_bars()` 读 `config.agent_event_monitor_reversal_bars`（最低 1）。
  - 新增状态 `price_dir_run` / `flow_sign_run`（截至上一笔的「连续同向」计数），在 `evaluate_quote` 末尾随方向/符号更新一起维护；两个回放清空块(E/G)同步 `.pop`。
  - 规则 E(price_reversal)/F(flow_reversal)：`cur != prev` 且 `run >= N` 才触发；文案改为「前 N 笔红 → 本笔绿」（含连击数，更可读）。
  - `_cooldown_seconds()` 统一返回 900s（反转类不再 0 冷却）。
- **src/config.py**：字段 `agent_event_monitor_reversal_bars: int = 3` + env `AGENT_EVENT_MONITOR_REVERSAL_BARS`（`parse_env_int` minimum=1）。
- **.env.example**：新增 `AGENT_EVENT_MONITOR_REVERSAL_BARS=3` 说明（含调参建议 2↔4~5）；更新默认规则描述。
- **tests/test_monitor.py**：`_make_default_rules_config` 加 `agent_event_monitor_reversal_bars=3`；`test_price_reversal_fires`/`test_flow_reversal_fires` 改为先造 3 根同向再翻转；新增 `test_reversal_filtered_when_streak_too_short`（2 根<3 不触发）、`test_reversal_rules_have_cooldown`（反转 900s 冷却）。

## Review
- **测试**：`.venv/Scripts/python.exe tests/test_monitor.py -v` → `Ran 19 tests ... OK`（17→19，+2 新增；原 15+注册表2 全绿无回归）。
- **设计取舍**：N 用单一全局 env（而非每条规则 threshold）——「降噪」是全局语义；显式规则如需不同灵敏度可后续扩展。`r_type` 参数保留在 `_cooldown_seconds` 以便未来按类型差异化冷却。
- **遗留/未做**：改动尚未提交（待用户确认）。真实盘中验证需用户在交易时段用 `--monitor` 或回放复跑确认噪声下降幅度；如仍吵可把 `AGENT_EVENT_MONITOR_REVERSAL_BARS` 调到 4~5。
