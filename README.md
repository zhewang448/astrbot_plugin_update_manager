# ⬆️ AstrBot 插件更新管理器 astrbot_plugin_update_manager(v2.2.2)

### 这是一个用于 AstrBot 的插件，它提供了一键更新所有已安装插件的功能，并且支持定时检查。

你是否为 AstrBot 插件`逐个更新`感到烦恼？是否希望 `定时自动检查更新`并`自动更新`？甚至希望更新完能`自动重启`让更改生效？

那么，这个插件就是为你准备的！

## 访问统计

## <a href="https://count.getloli.com/"><img src="https://count.getloli.com/get/@:astrbot_plugin_update_manager?theme=rule34"></a>

## ✨ 主要功能

- **一键更新所有插件**：通过简单的命令即可检查并更新所有需要更新的 AstrBot 插件。
- **定时自动检查与更新**：支持配置定时任务，自动检查插件更新并在后台执行更新操作。
- **自动重启 AstrBot**：支持在插件更新成功后自动重启 AstrBot 核心，使更新立即生效（需开启配置）。
- **手动重启指令**：提供便捷的指令用于手动重启 AstrBot。
- **代理支持**：支持配置 GitHub 代理地址，以便在网络受限环境下进行插件更新。
- **更新结果摘要**：更新完成后，会提供详细的成功或失败信息，方便用户了解更新状态。
- **日志记录**：详细记录更新过程中的信息、警告和错误，便于问题排查。
- **管理员权限控制**：敏感操作仅限管理员使用。

## ⬆️ 重要更新

### v2.2.2

- **新增重启功能**：
  - 新增配置项 `restart_mode`。开启后，当插件更新成功时会自动重启 AstrBot。
  - 新增指令 `/重启astrbot`，支持手动重启核心。
  - **特别说明**：本插件的重启功能核心逻辑（DashboardClient 及相关实现）移植自 **[astrbot_plugin_restart](https://github.com/Zhalslar/astrbot_plugin_restart)** 项目，感谢 [Zhalslar](https://github.com/Zhalslar) 大佬开源贡献。

### v2.2.0

- **增添了`admin_sid_list`配置项**：可以配置管理员的 SID 列表，在更新之后自动汇报给管理员。

### v2.1.0

- **增添了`black_plugin_list`配置项**：可以配置黑名单插件。
- **增添了`white_plugin_list`配置项**：可以配置白名单插件。
- **增添了`test_mode`配置项**：可以查看调试信息。
- **尽全力匹配了部分不规范命名的插件 😭**。
- **跳过了对官方内置插件的更新检查**。

### v2.0.0

**此版本是一个重大更新，重构了部分代码，以提升稳定性和性能。**

- **不再依赖 `metadata.json` 文件**：改为使用与 astrbot 前端同样的版本获取及比较机制。
- **用户返回优化**：手动更新时返回的消息`更加具体`。
- **性能优化**：提高了更新检查的速度。

## 📦 安装

将本插件放置于 AstrBot 的 `data/plugins` 目录下即可。

## ⚙️ 配置项

您可以在 AstrBot 的配置文件中为本插件配置以下项：

- `interval_hours` (float): 插件定时检查更新的间隔时间（单位：小时，默认为 `24` 小时）。设置为 `0` 则禁用定时更新功能。
- `admin_sid_list` (list)：可以配置管理员的 SID 列表，在更新之后自动汇报给管理员。
- `github_proxy` (str): 用于插件更新的 github 加速地址。例如: `"https://gh-proxy.com"`。
- `black_plugin_list` (list): 黑名单插件列表，不会更新这些插件。
- `white_plugin_list` (list): 白名单插件列表，只更新这些插件（不填则不启用）。
- `restart_mode` (bool):是否启用重启功能（默认为 `False`）。开启后，支持通过指令重启，且在检测到插件更新成功后会自动尝试重启 AstrBot。
- `test_mode` (bool): 是否开启调试模式。

## 🚀 指令说明

以下是本插件支持的指令：

| 指令           | 别名                               | 功能说明                                | 权限   |
| :------------- | :--------------------------------- | :-------------------------------------- | :----- |
| `更新所有插件` | `updateallplugins`, `更新全部插件` | 检查并更新所有需要更新的 AstrBot 插件。 | 管理员 |
| `重启astrbot`  | 无                                 | 调用面板接口立即重启 AstrBot 核心。     | 管理员 |

## 💡 使用示例

1.  **手动触发更新：**
    作为管理员，在聊天中发送：
    `更新所有插件`，
    机器人将会返回更新结果摘要。如果开启了 `restart_mode` 且有插件更新成功，它将自动重启。

2.  **配置定时更新与自动重启：**
    在 AstrBot 的配置文件中，设置 `interval_hours` 为 `24`，并设置 `restart_mode` 为 `true`。这样机器人每天会自动检查更新，并在更新完成后自动重启。

## ⚠️ 注意事项

- **重启功能依赖**：重启功能是通过调用 AstrBot 本地管理面板接口实现的，请确保 AstrBot 的管理面板配置正确（默认情况下无需额外配置）。
- ~~目前由于 AstrBot 内部函数对插件状态标记的处理存在问题，`可能导致部分插件不能正常更新。`（已提 issue）~~

## 🤝 致谢

- 本插件的“重启 AstrBot”功能逻辑参考并移植自 [Zhalslar](https://github.com/Zhalslar) 大佬的 [astrbot_plugin_restart](https://github.com/Zhalslar/astrbot_plugin_restart) 插件。非常感谢该项目的作者提供了优秀的实现思路。

## 🐞 已知问题和 ToDo List

- 暂无，欢迎提交 issue 和 参与 discussion。
