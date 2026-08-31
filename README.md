# AstrBot 插件更新管理器 v2.7.0

用于批量检查并更新已安装的 AstrBot 插件与 AstrBot 框架，支持手动更新、灵活定时检查、管理员通知和更新后自动重启。

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
- **只检查不更新**：`检查插件更新` 指令仅列出可用更新，不执行更新操作。
- **框架更新**：可检查并更新 AstrBot 核心、WebUI 与依赖，成功后自动重启并通知管理员更新日志。
- **定时框架更新**：可复用既有定时方式自动更新 AstrBot 框架。
- **两种定时方式**：支持固定间隔，以及指定星期和每日时间两种调度方式。
- **黑白名单管理**：配置页可从已安装插件中搜索并多选，控制需要更新或跳过的插件。
- **可靠的市场匹配**：优先按照仓库地址匹配，再使用作者与名称、唯一名称进行兜底判断。
- **歧义保护**：存在多个同名候选时不会任意选择，避免更新到错误插件。
- **更新结果通知**：可向指定管理员会话发送检查和更新摘要。
- **更新日志通知**：更新成功后自动读取各插件本地 CHANGELOG，以合并转发消息发送给管理员。
- **更新后自动重启**：支持在更新成功后通过 AstrBot 本地 Dashboard 接口重启核心。
- **GitHub 代理支持**：`github_proxy` 加速地址作用于自定义源的 zip 下载地址。
- **自定义 GitHub 更新源**：可为未上架插件市场的本地插件绑定仓库，通过远端 `metadata.yaml` 检查版本。

## 安装

将插件放入 AstrBot 的 `data/plugins` 目录，或从 AstrBot 插件市场安装。

依赖 APScheduler；AstrBot 安装插件时会根据 `requirements.txt` 自动处理。

## 指令

| 指令 | 别名 | 功能 | 权限 |
| --- | --- | --- | --- |
| `更新所有插件` | `updateallplugins`、`updateplugins`、`更新全部插件` | 检查并更新所有符合条件的插件 | 管理员 |
| `检查插件更新` | `checkpluginupdates`、`checkplugins` | 只检查可用更新，不执行更新 | 管理员 |
| `检查astrbot更新` | `checkastrbotupdates`、`checkastrbot`、`检查AstrBot更新` | 检查 AstrBot 框架是否有可用更新 | 管理员 |
| `更新astrbot` | `updateastrbot`、`astrbotupdate`、`更新AstrBot` | 更新 AstrBot 核心、WebUI 和依赖，成功后重启 | 管理员 |
| `安装插件 <链接>` | `installplugin`、`plugininstall` | 调用 AstrBot 原生接口安装并加载插件 | 管理员 |
| `清除插件数据 <插件名> --confirm` | `clearplugindata`、`clearplugin` | 清除插件持久化文件和 KV 数据并重载插件，不删除用户配置 | 管理员 |
| `重新安装插件 <插件名> [地址] [--no-proxy]` | `reinstallplugin`、`reinstall` | 覆盖式重新下载安装指定插件，不进行版本比较 | 管理员 |
| `重新安装插件<仓库链接> [--no-proxy]` | `reinstallplugin`、`reinstall` | 从仓库 metadata.name 定位插件并覆盖重装 | 管理员 |
| `重启astrbot` | `restartastrbot`、`astrbotrestart` | 调用 Dashboard 接口重启 AstrBot | 管理员 |

### 更新 AstrBot 框架

```
检查astrbot更新
更新astrbot
```

框架更新复用本机 AstrBot Dashboard 的原生更新服务：先下载并校验 WebUI 与核心包，再更新 `requirements.txt` 依赖。插件持续查询该服务的进度；任务成功后才发起重启，并在框架再次启动后向原会话发送完成回告。更新成功时，会将对应 AstrBot 发布版本的更新日志发送到 `admin_sid_list`。

`更新astrbot` 会先检查可用更新；当前版本已是最新时直接返回结果，不会启动更新任务。`更新所有插件` 保持只更新插件，不会隐式更新 AstrBot 框架。关闭 `astrbot_update_enabled` 后，框架检查、手动更新与定时框架更新都会停止，但 `重启astrbot` 仍可使用。框架更新不支持的启动模式或 Desktop 托管后端会直接返回 AstrBot 原生的状态说明。

### 安装插件

```
安装插件 https://github.com/owner/repo
```

该命令直接调用 AstrBot 的原生插件管理器，由 AstrBot 负责仓库解析、下载、元数据校验、依赖安装和加载。链接格式与当前 AstrBot 版本支持的插件仓库格式一致。

AstrBot 的插件持久化数据不属于用户配置：插件可使用 `PluginKVStoreMixin` 的 `get_kv_data`、`put_kv_data`、`delete_kv_data`，文件数据位于 `data/plugin_data/<插件目录>`。原生 `uninstall_plugin(plugin_name, delete_config=False, delete_data=True)` 在卸载插件时可清理其文件数据和 KV 数据，同时保留插件配置文件；目前没有“保留插件、仅清理其他插件数据”的独立公开接口。本插件安装命令不会清理任何已有数据。

### 清除插件数据

该命令必须显式带上 `--confirm` 才会执行。首次发送不带确认参数的命令只会显示警告，不会修改任何文件：

```
清除插件数据 astrbot_plugin_demo
清除插件数据 astrbot_plugin_demo --confirm
```

确认后复用 AstrBot 的内部清理流程，只传入 `delete_config=False` 和 `delete_data=True`，然后仅重载目标插件。AstrBot 的配置文件不会删除，但插件数据目录和 KV 可能含有用户录入内容，清除后不可恢复；框架未管理的其他路径不会处理。命令拒绝清理本插件、AstrBot 保留插件和不安全目录名；若清理目录仍存在或重载失败，会明确报告，不会宣称操作成功。

### 重新安装插件

用于插件文件损坏、手动改动后需要还原，或需要临时切换到指定版本、指定分支的场景。该指令不比较版本号，直接覆盖安装。

**基本用法**（自动查找）：

```
重新安装插件 astrbot_plugin_demo
```

不带地址时，按「自定义源绑定 → 插件市场」的顺序自动查找下载地址。

**指定 GitHub 仓库或分支**：

```
重新安装插件 astrbot_plugin_demo https://github.com/owner/repo
重新安装插件 astrbot_plugin_demo https://github.com/owner/repo/tree/dev
重新安装插件 astrbot_plugin_demo github.com/owner/repo/tree/test
```

**仅提供仓库链接**：

```
重新安装插件https://github.com/zhewang448/astrbot_plugin_update_manager/tree/test
```

该形式会先调用 AstrBot 原生仓库检查接口，读取远端 `metadata.yaml` 或 `metadata.yml` 中的 `name`，精确定位同名的已加载插件后重装；不会根据仓库名猜测插件名。

- 只给仓库地址时，自动查询并使用默认分支（通常是 `main`）
- 给 `/tree/分支名` 时，使用指定分支
- 插件会自动转换为 `.zip` 归档下载地址

**指定直接下载地址**：

```
重新安装插件 astrbot_plugin_demo https://github.com/owner/repo/archive/v1.0.0.zip
```

**禁用代理加速**：

```
重新安装插件 astrbot_plugin_demo https://github.com/owner/repo --no-proxy
```

默认会使用配置的 `github_proxy` 加速，加 `--no-proxy` 可禁用（适合加速服务不稳定或访问内网地址时）。

**说明**：

- 支持 GitHub 仓库地址自动转换，不限于 `.zip` 结尾的直接下载地址
- 未指定分支时自动使用仓库的默认分支（通过 GitHub API 查询）
- 旧版 AstrBot 不支持按固定地址安装时，该指令会明确提示

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

开启 `astrbot_auto_update` 后，框架更新会复用上述定时方式执行。发现 AstrBot 新版本时会自动更新、发送更新日志到管理员 SID 列表并重启；默认关闭。

## 配置说明

| 配置项 | 说明 |
| --- | --- |
| `schedule_mode` | `interval` 为固定间隔，`calendar` 为指定星期和时间 |
| `interval_hours` | 方式 1 的检查间隔，单位为小时 |
| `check_weekdays` | 方式 2 的每周执行日期 |
| `check_times` | 方式 2 的每日执行时间列表，格式为 `HH:MM` |
| `check_on_startup` | 方式 2 下启动后是否立即检查一次 |
| `admin_sid_list` | 定时检查结束后接收结果的管理员会话 SID |
| `github_proxy` | GitHub 加速地址，作用于自定义源 zip 下载，也会传给 AstrBot 框架更新服务；不填则不使用 |
| `github_token` | 可选 GitHub API Token，用于自定义源的仓库、提交和 metadata 查询，并提高 GitHub API 限额 |
| `custom_plugin_sources` | 为已安装插件绑定 GitHub 仓库，可搜索选择本地插件 |
| `white_plugin_list` | 非空时只检查所选插件，支持搜索和多选 |
| `black_plugin_list` | 跳过所选插件，支持搜索和多选 |
| `restart_mode` | 有插件更新成功后是否自动重启 AstrBot |
| `astrbot_update_enabled` | AstrBot 框架更新总开关，默认开启；不影响 `重启astrbot` |
| `astrbot_auto_update` | 是否按既有定时方式自动更新 AstrBot，默认关闭 |
| `send_changelog_to_admin` | 更新成功后读取各插件本地 CHANGELOG，以合并转发发送给管理员 |
| `test_mode` | 在插件目录生成 `test.md` 调试数据 |

黑名单优先于白名单。测试分支或不希望自动更新的插件，应主动加入黑名单。

## 自定义更新源

在配置页的 `custom_plugin_sources` 中新增条目：

1. 搜索并选择一个已安装插件。
2. 填写标准 GitHub 仓库地址，例如 `https://github.com/owner/repo`。
3. 分支可留空，插件会自动读取仓库默认分支；也可指定固定分支。

### GitHub API 说明

自定义更新源检查会通过 GitHub API 查询仓库默认分支、commit 和远端 metadata。未填写 `github_token` 时使用匿名请求，频繁检查或多人共享同一出口 IP 时可能遇到 API 限流。

如遇到 GitHub API 限流，可按以下步骤获取 Token：

1. 登录 GitHub，进入头像菜单中的 **Settings → Developer settings → Personal access tokens → Fine-grained tokens**。
2. 点击 **Generate new token**，为 Token 设置合适的有效期，并只授予目标仓库所需的只读权限；不需要授予写入或管理仓库权限。
3. 创建后立即复制 Token，在插件配置的 `github_token` 中填写，然后重载插件。

Token 仅用于 GitHub API 请求认证，不会显示在运行日志中。请勿将 Token 提交到公开仓库、发到聊天消息或截图中；如发生泄露，应立即在 GitHub 中撤销并重新生成。

插件会读取远端 `metadata.yaml` 或 `metadata.yml` 的 `name`、`version`，与本地插件版本比较。发现新版本时会锁定检查时的 commit 下载，避免检查后仓库变化造成版本不一致。

同一插件只能绑定一个自定义源。绑定后自定义源优先于插件市场；配置无效或请求失败时只跳过该插件，不会回退到同名市场条目，也不会阻断其他插件检查。远端插件名必须与本地插件名一致；metadata 中填写了 `repo` 时，也必须与绑定仓库一致。

## 市场匹配规则

1. 本地插件 `repo` 与市场仓库地址唯一匹配。
2. 本地插件 `author + name` 与市场条目唯一匹配。
3. 兼容 `astrbot_plugin_` 前缀及连字符、下划线差异，进行唯一名称兜底。
4. 出现多个同名候选时标记为歧义并跳过，不会任意选取第一个。

仓库地址匹配会处理大小写、`.git`、末尾斜杠、GitHub `tree/分支` 地址及常见 GitHub 代理 URL。

## 更新日志

版本变更记录请查看 [CHANGELOG.md](CHANGELOG.md)。

## 注意事项

- 自动更新会修改插件文件。重要插件建议先备份。
- 市场或自定义源提供固定安装包地址时，新版 AstrBot 会优先下载该安装包，此时 `github_proxy` 不参与该安装包下载。
- GitHub API 出现 `403 rate limit exceeded` 时，在配置页填写 `github_token` 后重载插件；Token 仅用于 GitHub API 请求，不会写入日志。
- 插件市场不可访问时会明确提示；已成功检查到的自定义源更新仍可继续执行。
- 插件版本无法比较、市场不存在或匹配有歧义时，会跳过该插件并在结果中说明。
- 重启功能通过 AstrBot 本地 Dashboard 接口完成，不需要安装其他重启插件。
- 框架更新由 AstrBot 原生更新器执行；`更新astrbot` 会等待任务成功后再重启，等待超时则保留任务运行状态，不会强制重启。
- AstrBot 框架更新成功后会发送对应发布版本的更新日志到 `admin_sid_list`；发布服务暂时不可用时不影响更新和重启。

## 致谢

重启功能参考并移植自 [Zhalslar](https://github.com/Zhalslar) 的
[`astrbot_plugin_restart`](https://github.com/Zhalslar/astrbot_plugin_restart) 项目。感谢原作者的开源贡献。
