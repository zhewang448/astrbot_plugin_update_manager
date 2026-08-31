# 更新日志

## v2.7.0

- 新增 `检查astrbot更新`（`checkastrbotupdates`）和 `更新astrbot`（`updateastrbot`）管理员指令。
- 框架更新复用本地 Dashboard 的原生更新服务，涵盖 AstrBot 核心、WebUI 和依赖更新；只有任务成功后才触发重启并向原会话回告。
- `更新所有插件` 仍只处理插件更新，避免将框架更新混入既有自动更新流程。
- 修复 `更新astrbot` 在当前已是最新版本时仍启动更新任务的问题；现在先检查更新状态。
- 命令主名称中的 `astrbot` 统一为小写，并增加常用小写英文别名及原大小写写法的兼容别名。
- 新增 `astrbot_update_enabled` 总开关和 `astrbot_auto_update` 定时更新开关；定时框架更新复用既有调度设置。
- AstrBot 框架更新成功后向 `admin_sid_list` 发送对应发布版本的更新日志。

## v2.6.1

- 修复手动执行 `重启astrbot` 后，AstrBot 重启完成不会通知原会话的问题。

## v2.6.0

- 新增管理员指令 `安装插件 <链接>`，复用 AstrBot 原生插件安装接口完成下载、校验、依赖处理和加载。
- 新增管理员指令 `清除插件数据 <插件名> --confirm`，只清理 AstrBot 管理的非配置持久化数据并重载目标插件。
- `重新安装插件` 支持仅提供紧贴命令的仓库链接，从远端 `metadata.name` 自动定位本地插件。
- 修复自定义更新源读取 `metadata.yaml` 时仍访问 `raw.githubusercontent.com`，导致已配置 `github_token` 仍可能受 raw 下载 429 限流影响；现在改用带认证的 GitHub Contents API。
- 修复 `重新安装插件` 将 GitHub ZIP 直链交给 AstrBot Core 时绕过 `github_proxy`，导致下载重定向到 `codeload.github.com` 后可能触发 429 限流。

本文件记录 AstrBot 插件更新管理器的重要版本变化。

## v2.5.0

### 新增

- 新增 `send_changelog_to_admin` 配置项：更新成功后自动读取各插件本地 `CHANGELOG.md`，截取旧版本到新版本区间的内容，以合并转发消息发送给管理员；不支持转发的平台自动降级为长文本。
- 新增 `检查插件更新` 指令（别名 `checkpluginupdates`）：只检查可用更新，不执行更新操作。
- 新增 `重新安装插件` 指令（别名 `reinstallplugin`）：强制重新下载安装指定插件，不进行版本比较，适用于插件文件损坏等场景；支持 GitHub 仓库地址自动转换为下载链接（含分支/tag），未指定分支时自动查询默认分支；支持 `--no-proxy` 参数禁用代理加速。

### 改进

- `github_proxy` 代理地址现在同时作用于自定义源的 `raw.githubusercontent.com` 文件请求、`.zip` 下载地址，以及插件市场的 GitHub 备用地址；`api.github.com` 请求不经代理（避免 Token 经第三方中转）。
- 自定义更新源改为并发检查（最多 5 个同时进行），多个绑定时速度大幅提升。
- HTTP 缓存改为有界 LRU，避免长期运行中 commit SHA URL 无限累积。
- GitHub API 限流（`403` / `X-RateLimit-Remaining: 0`）时提示剩余配额、恢复时间和配置 Token 的建议，不再显示无指向的原始报错。
- 黑白名单匹配改为大小写不敏感，避免手动修改配置时大小写不一致导致规则静默失效。
- 调试数据写盘改为后台线程，不阻塞事件循环。

### 修复

- 修复 `_http_cache` 使用 `dict` 直接赋值绕过 `BoundedCache.set` 的问题。

## v2.4.2

- 新增 `github_token` 配置项，GitHub API 请求支持 Bearer Token，缓解匿名 API 限流。

## v2.4.1

### 新增与改进

- 新增原生配置表单 `custom_plugin_sources`，可搜索选择已安装插件并绑定标准 GitHub 仓库。
- 通过远端 `metadata.yaml` 或 `metadata.yml` 比较版本，支持自动识别默认分支或指定分支。
- 检查时解析并锁定 commit SHA，更新时下载同一提交的压缩包。
- 自定义绑定优先于插件市场；配置或请求失败时不会回退到同名市场条目。
- 自定义源与插件市场的部分失败互不阻断，并在检查摘要中分别说明。
- 校验远端插件名及 metadata 仓库地址，避免绑定到错误插件。

## v2.4.0

### 修复

- 修复新版 AstrBot 中 Dashboard 密码存储及登录验证方式变化导致的重启失效。
- 重启请求改用 AstrBot 本地 Dashboard JWT，不受密码哈希或 TOTP 登录影响。
- 修复市场访问失败时误报"没有更新"的问题。
- 修复重复错误日志与结果摘要问题。
- 防止手动检查和定时检查同时执行。

### 新增与改进

- 适配新版插件市场的"作者/插件名"数据格式，并跳过市场元数据项。
- 市场匹配改为依次使用仓库地址、作者与插件名、唯一名称判断。
- 同名候选不唯一时标记为歧义并跳过，不再任意选择候选。
- 仓库地址匹配支持大小写、`.git`、末尾斜杠、GitHub 分支地址及常见代理 URL 的归一化。
- 支持市场 `download_url`；旧版 AstrBot 不支持该参数时自动回退到仓库更新。
- 黑白名单改为已安装插件的可搜索多选框，并保留当前未加载的历史选项。
- 新增固定间隔和指定星期、时间两种定时方式，配置页按所选方式动态显示相关配置。
- 日历调度支持多个每日执行时间、启动后检查、错过任务处理和重复任务保护。

## v2.2.2

- 新增 `restart_mode` 配置项，可在插件更新成功后自动重启 AstrBot。
- 新增 `重启astrbot` 指令，支持管理员手动重启 AstrBot 核心。
- 重启功能参考并移植自 [Zhalslar](https://github.com/Zhalslar) 的 [`astrbot_plugin_restart`](https://github.com/Zhalslar/astrbot_plugin_restart) 项目。

## v2.2.0

- 新增 `admin_sid_list` 配置项，可在更新完成后向管理员会话发送结果。

## v2.1.0

- 新增插件黑名单、白名单和测试模式配置。
- 改进对部分不规范插件名称的匹配。
- 跳过 AstrBot 官方内置插件的更新检查。

## v2.0.0

- 重构更新流程，提升稳定性和性能。
- 不再依赖 `metadata.json`，改用与 AstrBot 前端一致的版本获取和比较机制。
- 改进手动更新结果，使成功和失败信息更加清晰。
- 优化更新检查性能。
