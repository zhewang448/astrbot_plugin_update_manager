# AstrBot 插件更新管理器 v2.4.0

用于批量检查并更新已安装的 AstrBot 插件，支持手动更新、灵活定时检查、管理员通知和更新后自动重启。

## 访问统计

<div align="center">
  <a href="https://count.getloli.com/">
    <img src="https://count.getloli.com/get/@:astrbot_plugin_update_manager?theme=rule34" alt="访问统计">
  </a>
</div>

## 插件介绍

你是否为 AstrBot 插件需要逐个更新而烦恼，或希望机器人能够定时检查、自动更新，并在更新完成后自动重启使新版本立即生效？

本插件用于集中管理已安装插件的更新流程。它会读取本地插件信息并与 AstrBot 插件市场进行匹配，在确认来源唯一、版本有效后执行更新；遇到同名插件、来源不明确或市场访问失败时会安全跳过并给出说明。

## 主要功能

- **批量检查和更新**：通过管理员命令检查并更新符合条件的已安装插件。
- **两种定时方式**：支持固定间隔，以及指定星期和每日时间两种调度方式。
- **黑白名单管理**：配置页可从已安装插件中搜索并多选，控制需要更新或跳过的插件。
- **可靠的市场匹配**：优先按照仓库地址匹配，再使用作者与名称、唯一名称进行兜底判断。
- **歧义保护**：存在多个同名候选时不会任意选择，避免更新到错误插件。
- **更新结果通知**：可向指定管理员会话发送检查和更新摘要。
- **更新后自动重启**：支持在更新成功后通过 AstrBot 本地 Dashboard 接口重启核心。
- **GitHub 代理支持**：可配置 GitHub 加速地址，并兼容市场提供的安装包下载地址。
- **自定义 GitHub 更新源**：可为未上架插件市场的本地插件绑定仓库，通过远端 `metadata.yaml` 检查版本。

## 安装

将插件放入 AstrBot 的 `data/plugins` 目录，或从 AstrBot 插件市场安装。

依赖 APScheduler；AstrBot 安装插件时会根据 `requirements.txt` 自动处理。

## 定时方式

### 方式 1：固定间隔

保持旧版行为。AstrBot 启动后每隔 `interval_hours` 小时检查一次：

- 默认值为 24。
- 支持浮点数。
- 设置为 0 时关闭定时检查。
- 老配置没有 `schedule_mode` 时默认使用此方式。

### 方式 2：指定星期和时间

适合希望在固定时刻执行的场景：

- `check_weekdays`：选择星期一至星期日，可多选。
- `check_times`：填写一个或多个 24 小时制时间，例如 `04:00`、`16:30`。
- `check_on_startup`：是否在 AstrBot 启动后额外检查一次。

无效时间会被记录为警告并忽略；重复时间会自动去重。日历模式不会补跑 AstrBot 启动前已经错过的任务。

## 配置说明

| 配置项 | 说明 |
| --- | --- |
| `schedule_mode` | `interval` 为固定间隔，`calendar` 为指定星期和时间 |
| `interval_hours` | 方式 1 的检查间隔，单位为小时 |
| `check_weekdays` | 方式 2 的每周执行日期 |
| `check_times` | 方式 2 的每日执行时间列表，格式为 `HH:MM` |
| `check_on_startup` | 方式 2 下启动后是否立即检查一次 |
| `admin_sid_list` | 定时检查结束后接收结果的管理员会话 SID |
| `github_proxy` | GitHub 加速地址，不填则不使用 |
| `github_token` | 可选 GitHub API Token，用于提高 GitHub API 限额 |
| `custom_plugin_sources` | 为已安装插件绑定 GitHub 仓库，可搜索选择本地插件 |
| `white_plugin_list` | 非空时只检查所选插件，支持搜索和多选 |
| `black_plugin_list` | 跳过所选插件，支持搜索和多选 |
| `restart_mode` | 有插件更新成功后是否自动重启 AstrBot |
| `test_mode` | 在插件目录生成 `test.md` 调试数据 |

黑名单优先于白名单。测试分支或不希望自动更新的插件，应主动加入黑名单。

## 自定义更新源

在配置页的 `custom_plugin_sources` 中新增条目：

1. 搜索并选择一个已安装插件。
2. 填写标准 GitHub 仓库地址，例如 `https://github.com/owner/repo`。
3. 分支可留空，插件会自动读取仓库默认分支；也可指定固定分支。

插件会读取远端 `metadata.yaml` 或 `metadata.yml` 的 `name`、`version`，与本地插件版本比较。发现新版本时会锁定检查时的 commit 下载，避免检查后仓库变化造成版本不一致。

同一插件只能绑定一个自定义源。绑定后自定义源优先于插件市场；配置无效或请求失败时只跳过该插件，不会回退到同名市场条目，也不会阻断其他插件检查。远端插件名必须与本地插件名一致；metadata 中填写了 `repo` 时，也必须与绑定仓库一致。

## 市场匹配规则

1. 本地插件 `repo` 与市场仓库地址唯一匹配。
2. 本地插件 `author + name` 与市场条目唯一匹配。
3. 兼容 `astrbot_plugin_` 前缀及连字符、下划线差异，进行唯一名称兜底。
4. 出现多个同名候选时标记为歧义并跳过，不会任意选取第一个。

仓库地址匹配会处理大小写、`.git`、末尾斜杠、GitHub `tree/分支` 地址及常见 GitHub 代理 URL。

## 指令

| 指令 | 别名 | 功能 | 权限 |
| --- | --- | --- | --- |
| `更新所有插件` | `updateallplugins`、`更新全部插件` | 检查并更新所有符合条件的插件 | 管理员 |
| `重启astrbot` | 无 | 调用 Dashboard 接口重启 AstrBot | 管理员 |

## 更新日志

### v2.4.1

- 新增自定义 GitHub 插件更新源，可在配置页搜索并绑定已安装插件。
- 读取远端 `metadata.yaml` 或 `metadata.yml` 比较版本，并锁定检查时的 commit 下载。
- 支持自动识别默认分支或手动指定分支。
- 自定义源优先于插件市场；单个来源失败不会阻断其他插件检查。

### v2.4.0

- 修复新版 AstrBot 中 Dashboard 密码存储及登录验证方式变化导致的重启失效。
- 重启请求改用 AstrBot 本地 Dashboard JWT，不受密码哈希或 TOTP 登录影响。
- 适配新版插件市场的“作者/插件名”数据格式，并跳过市场元数据项。
- 市场匹配按“仓库地址 -> 作者与插件名 -> 唯一名称”依次判断。
- 同名候选不唯一时不再猜测，明确提示歧义并安全跳过。
- 市场请求失败会单独报错，不再误报“没有更新”。
- 支持市场 `download_url`；旧版 AstrBot 不支持该参数时自动回退到仓库更新。
- 黑白名单改为已安装插件的可搜索多选框，并保留当前未加载的历史选项。
- 新增两种定时方式，配置页会根据所选方式显示对应设置。
- 防止手动检查和定时检查同时执行，修复重复错误日志与结果摘要问题。

历史版本记录请查看 [CHANGELOG.md](CHANGELOG.md)。

## 注意事项

- 自动更新会修改插件文件。重要插件建议先备份。
- 市场或自定义源提供固定安装包地址时，新版 AstrBot 会优先下载该安装包，此时 `github_proxy` 不参与该安装包下载。
- GitHub API 出现 `403 rate limit exceeded` 时，在配置页填写 `github_token` 后重载插件；Token 仅用于 GitHub API 请求，不会写入日志。
- 插件市场不可访问时会明确提示；已成功检查到的自定义源更新仍可继续执行。
- 插件版本无法比较、市场不存在或匹配有歧义时，会跳过该插件并在结果中说明。
- 重启功能通过 AstrBot 本地 Dashboard 接口完成，不需要安装其他重启插件。

## 致谢

重启功能参考并移植自 [Zhalslar](https://github.com/Zhalslar) 的
[`astrbot_plugin_restart`](https://github.com/Zhalslar/astrbot_plugin_restart) 项目。感谢原作者的开源贡献。
