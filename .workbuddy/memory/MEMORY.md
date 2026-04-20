# Market Monitor 项目长期记忆

## 项目架构
- 网站: market-monitor.uk (Cloudflare Access 保护)
- 服务器: 腾讯云 43.134.6.175:2222
- 技术栈: FastAPI + uvicorn, Docker, nginx, Cloudflare CDN
- 部署: GitHub push → webhook → auto-update.sh → docker build + restart
- 服务器项目路径: /root/.openclaw/workspace_coder/a_share_market_monitor/
- 数据源: 东方财富(主) + 新浪(备)

## CF Access 凭证
- CF-Access-Client-Id: 54d674ccaedaa55147c01ca6a8e1953a.access
- CF-Access-Client-Secret: eccc024810c1f8604e6d1080e7f557a6bc78f6936ceeedbe481cb4e3787f7dce

## 本地网络环境
- 本地DNS解析 market-monitor.uk → 198.18.0.44 (VPN/代理拦截)
- curl 访问网站超时，但 Python requests 正常
- SSH 连接服务器不可用（无SSH密钥配置）

## 关键代码约定
- **不能修改频控参数**: API_BASE_INTERVAL, API_MAX_INTERVAL, SECONDARY_API_INTERVAL 等
- 溢价数据使用 _premium_cache 字典缓存，持久化到 premium_cache.json
- 智能合并逻辑: refresh_spot 中新旧数据合并，新数据为0/null时保留旧数据有效值

## 踩坑记录
- 东方财富列表API f183(净值)/f184(溢价率) 之前请求了但未解析 (2026-04-19修复)
- 非交易时间 f184 返回0，不应当作有效溢价；需通过 f43/f183 手动计算
- f43 在东方财富单条接口中是放大1000倍的价格(如4739→4.739)
- refresh_spot 旧逻辑直接覆盖数据，导致非交易时间获取的空数据覆盖了有效数据
- **关键bug**: 东方财富单条股票API (`push2.eastmoney.com/api/qt/stock/get`) 缺少 `fltt=2` 参数时，f43/f183 返回放大1000倍的整数；代码只对f43做了/1000处理，遗漏了f183(nav) (2026-04-19修复)
- 自动部署(auto-update.sh)在OpenClaw平台环境下可能无法正常重启Docker容器，需要用户手动介入
- **非交易时间刷新bug**: `_should_refresh_spot` 用 `now.minute % 30 == 0` 判断是否刷新，但APScheduler的interval模式无法保证命中0/30分，导致数据可能长时间不刷新 (2026-04-19修复为基于last_updated时间差判断)
- **重大发现(2026-04-20)**: 东方财富单条股票API对ETF的f183/f184**始终返回0**，即使fltt=2也无效！该接口仅对股票有效，对ETF无效。之前的fltt=2修复方案对ETF溢价率问题毫无作用
- **fundgz接口(2026-04-20)**: fundgz.1234567.com.cn在Docker容器内无法访问（DNS解析失败），不能作为净值数据源
- **IOPV方案(2026-04-20)**: 东方财富trends2接口(`push2.eastmoney.com/api/qt/stock/trends2/get`)的分时数据最后一个字段是IOPV(ETF参考净值)，这是目前发现的唯一可靠ETF净值获取方案

## 部署注意
- webhook-server.py 运行在宿主机 8082 端口
- 应用容器映射到宿主机 8081 端口
- nginx 将 /webhook 代理到 8082，其他代理到 8081
- auto-update.sh 可能与 OpenClaw 平台的 Docker 管理机制不兼容，部署可能需要手动操作
- **确认**: auto-update.sh 在OpenClaw平台下完全无法工作（7+次webhook触发均未成功重建容器），需要找到替代部署方案
- 宿主机webhook-server.py的REPO_PATH已正确配置为 /root/.openclaw/workspace_coder/a_share_market_monitor/（从webhook返回的日志路径确认）
- **webhook正确用法**: 必须添加 `X-GitHub-Event: push` header，否则返回"Ignored event"
- **2026-04-20 10:11**: 服务器曾成功部署cb5faa5代码（原因不明，可能是用户手动操作或OpenClaw平台自动重启），此后5个commit均未部署
