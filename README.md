# Codex 额度仪表盘 v1.0.0

Windows 桌面悬浮仪表盘，显示 Codex 的 5 小时额度与周额度。每 30 秒通过本机 Codex CLI 刷新一次。仪表盘随 Codex 桌面应用显示，点击顶部 GitHub 头像可打开项目作者主页。

## 运行环境

- Windows 10/11
- Python 3.12 与 Pillow 12.3.0
- 已登录的 Codex 桌面应用，以及可用的 Codex CLI

## 启动

1. 安装依赖：`python -m pip install -r requirements.txt`
2. 保持 `v1.0.0_Codex额度仪表盘.pyw` 和 `v1.0.0_GitHub头像.png` 在同一目录。
3. 双击 `.pyw` 文件启动。Codex 桌面应用运行时，仪表盘会显示。

点击右上角的缩小按钮可将仪表盘收进任务栏。点击任务栏图标，或再次双击 `.pyw` 文件，可恢复窗口。

## 开机启动

在当前目录打开 PowerShell，执行以下命令，将仪表盘快捷方式放入 Windows 启动文件夹。`pythonw.exe` 需要在 PATH 中；也可以将第一行换成其绝对路径。

```powershell
$pythonw = (Get-Command pythonw.exe).Source
$script = (Resolve-Path .\v1.0.0_Codex额度仪表盘.pyw).Path
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'v1.0.0_Codex额度仪表盘.lnk'
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"' + $script + '"'
$shortcut.WorkingDirectory = Split-Path $script
$shortcut.Save()
```

## 数据来源

程序使用本机 `codex app-server` 的 `account/rateLimits/read` 接口读取当前账号的额度。界面显示剩余百分比和重置时间。

## 许可

MIT License。头像素材来自项目作者的 GitHub 个人头像。
