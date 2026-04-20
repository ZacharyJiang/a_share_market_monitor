# Market Monitor 24h Watchdog - 执行记录

## 2026-04-20 08:05 执行（第7次）

### 检查结果
- 网站前端正常可访问 (HTTP 200)
- ETF总数1889只 ✅（从1886增加，说明服务器在10:11成功部署过一次cb5faa5代码）
- 价格(currentPrice)92%有效 ✅
- 指数数据正常（上证4078、深证14967、沪深3004753）✅
- **溢价率(premium)100%为null** ❌
- 净值(nav)100%缺失 ❌
- 数据更新时间正常（持续更新中，不再停滞）✅
- rate_limiter: state=closed, interval=2.5, failure_streak=0 ✅

### 根因分析（重大发现）
1. **之前7次监控的溢价率修复方案是错误的**！
   - 东方财富 `push2.eastmoney.com/api/qt/stock/get` 单条API对ETF的f183(净值)和f184(溢价率)**始终返回0**，即使添加fltt=2参数也无效
   - 该接口仅对股票有效，对ETF无效。之前4个commit(7856640~cb5faa5)的fltt=2修复对ETF毫无作用
2. **列表API的f183/f184对ETF也不可靠**
   - 列表API(fs=b:MK0021)的f183返回负数（不是净值），f184值也异常
3. **fundgz.1234567.com.cn接口在Docker容器内无法访问**（DNS解析/网络限制）
4. **东方财富trends2接口可返回IOPV**（分时数据最后一个字段），但尚未确认在Docker容器内是否可用

### 代码修复（5个commit已推送）
- 00387dc: 重写 `_fetch_premium_batch_sync` 使用fundgz接口获取净值
- 1554b01: fundgz改用HTTPS+HTTP回退+诊断日志
- 9bf7571: 添加fundgz接口诊断到diag端点
- 3e9755d: 优先使用东方财富trends2接口获取IOPV，fundgz作为备用
- db938b1: 添加trends2接口分步诊断到diag端点

### 部署状态
- **5个commit全部未部署到服务器！**
- auto-update.sh累计7次以上失败（webhook触发成功，但docker build/stop/run在OpenClaw平台下无法执行）
- 服务器仍在运行cb5faa5版本的代码（约10:11时成功部署过一次）
- 通过diag端点确认：无fundgz_test/trends2_test/premium_cache_size字段

### 结论
- **必须用户手动部署！** SSH到服务器执行 `docker restart a-share-etf-monitor`，或通过OpenClaw平台重启容器
- 自动部署方案在OpenClaw平台下确认完全不可行（7+次失败）
- 一旦手动部署，新代码将使用trends2接口获取IOPV，修复溢价率100%缺失问题
- 如果trends2在容器内也不可用，需要进一步寻找替代方案

## 2026-04-20 03:14 执行

### 检查结果
- 网站前端正常可访问 (HTTP 200)
- ETF总数1886只 ✅
- 价格(currentPrice)94.2%有效 ✅
- 指数数据正常（上证4051、深证14885、沪深3004728）✅
- sparkline 1784/1886有效 ✅
- **溢价率(premium)100%为null** ❌
- 净值(nav)100%缺失 ❌
- 数据更新时间停留在 00:48:18（停滞超26小时）❌
- 费率(fee)73%有效 ⚠️
- 规模(scale)53.4%有效 ⚠️

### 根因
与之前5次执行完全相同——服务器未部署最新代码。
本地代码已有4个修复commit（7856640~cb5faa5），但服务器容器仍在运行旧代码：
1. 溢价率计算bug（fltt=2参数缺失导致nav返回放大值）
2. 非交易时间数据刷新停滞bug（旧代码minute%30太脆弱）

### 部署尝试
- 首次正确使用X-GitHub-Event: push header触发webhook成功（返回"Update triggered"）
- 之前5次使用的curl/webhook请求缺少X-GitHub-Event header，导致返回"Ignored event"
- 本次webhook虽成功触发，但等待2分钟后数据无变化，auto-update.sh仍然无法重建容器
- 累计6次监控均发现相同问题

### 结论
- **必须用户手动SSH到服务器部署**，或通过OpenClaw平台界面重启容器
- 自动部署方案在OpenClaw平台下确认不可行（6次失败）
- 新发现：之前5次webhook未触发的原因是缺少X-GitHub-Event header，本次修复了header问题但auto-update.sh本身仍无法重建容器

## 2026-04-19 22:27 执行

### 检查结果
- 网站前端正常可访问 (HTTP 200)
- ETF总数1886只 ✅
- 价格(currentPrice)94.2%有效 ✅
- 指数数据正常（上证4051、深证14885、沪深3004728）✅
- **溢价率(premium)100%为null** ❌
- 净值(nav)100%缺失 ❌
- K线字段不存在（前端用sparkline替代，正常）
- 费率(fee)73%有效 ⚠️
- 规模(scale)53.4%有效 ⚠️
- 数据更新时间停留在 00:48:18（停滞超22小时）❌
- rate_limiter: state=closed, interval=3.26, failure_streak=0

### 根因
与之前3次执行完全相同——服务器未部署最新代码。
本地代码已修复（commit 7856640~cb5faa5），但服务器容器仍在运行旧代码：
1. 溢价率计算bug（fltt=2参数缺失导致nav返回放大值）
2. 非交易时间数据刷新停滞bug（旧代码minute%30太脆弱）

### 部署尝试
- 触发webhook成功（返回"Update triggered"）
- 等待2分钟后数据无变化，auto-update.sh仍然无法重建容器
- 累计5次监控均发现相同问题

### 结论
- **必须用户手动SSH到服务器部署**，或通过OpenClaw平台界面重启容器
- 自动部署方案已确认在OpenClaw平台下完全不可行

## 2026-04-19 18:19 执行

### 检查结果
- 网站前端正常可访问 (HTTP 200)
- ETF总数1886只 ✅
- **严重异常**: 所有1886只ETF的溢价率(premium)100%为null ❌
- 费率缺失509/1886 (27%) ⚠️
- 价格缺失109/1886 (5.8%) ⚠️
- K线缺失102/1886 (5.4%) ⚠️
- 数据更新时间停留在 00:48:18（停滞超17小时）❌
- 容器内rate_limiter状态正常(state=closed, failure_streak=0)

### 根因
与之前两次相同——服务器未部署最新代码（commit 7856640, 1461e0f, cb5faa5），旧代码存在：
1. 溢价率计算bug（fltt=2参数缺失导致nav返回放大值）
2. 非交易时间数据刷新停滞bug（minute%30判断太脆弱）

### 部署尝试
- 触发webhook成功（返回"Update triggered"，日志路径确认REPO_PATH正确）
- 等待3分钟后数据无变化，Docker重建仍然失败
- auto-update.sh的docker build/stop/run命令在OpenClaw平台环境下无法正常执行

### 结论
- **必须用户手动SSH到服务器部署**，或通过OpenClaw平台界面重启容器
- 累计4次监控（01:59, 14:57, 及本次）均发现相同问题，根因一致：服务器未部署新代码

## 2026-04-19 14:57 执行

### 检查结果
- 网站前端/API均可正常访问
- ETF总数1886只，价格/K线/费率基本正常
- **严重异常**: 所有1886只ETF的溢价率(premium)100%为null
- 数据更新时间停留在 00:48:18（超14小时未刷新）

### 根因
1. **溢价率bug**: 之前2次修复(commit 7856640, 1461e0f)已推送GitHub但服务器未部署新代码
2. **刷新停滞bug**: `_should_refresh_spot` 使用 `now.minute % 30 == 0` 判断非交易时间是否刷新，该条件太脆弱，APScheduler可能永远无法命中

### 修复
- `_should_refresh_spot`: 改为基于 `last_updated` 时间差判断（commit cb5faa5）
- 累计3个commit未部署: 7856640, 1461e0f, cb5faa5

### 部署状态
- 代码已推送到 GitHub
- 自动部署再次失败（auto-update.sh 与 OpenClaw 平台不兼容）
- **需要用户手动SSH到服务器部署，或通过 OpenClaw 平台重启容器**

## 2026-04-19 01:59 执行

### 检查结果
- 网站前端正常可访问
- ETF总数1886只，价格/K线/费率基本正常
- **严重异常**: 所有1886只ETF的溢价率(premium)和净值(nav)100%为null

### 根因
东方财富单条股票API缺少 `fltt=2` 参数，导致 f183(nav) 返回放大1000倍的值，代码未做/1000处理，溢价率计算结果被 abs(premium)<30 过滤。

### 修复
- `_fetch_premium_batch_sync`: 添加 fltt=2，f183 安全处理
- `_fetch_premium_from_eastmoney`: 同上
- `check_and_fill_missing_data`: 价格补全添加 fltt=2
- Commit: 1461e0f

### 部署状态
- 代码已推送到 GitHub
- 自动部署未成功（auto-update.sh 与 OpenClaw 平台可能不兼容）
- **需要用户手动部署**
