# 安装与签名发布

## 安装

从 [BoardBench 最新发布页](https://github.com/skk0/boardbench-local/releases/latest) 下载对应系统的 ZIP 和同页的 `SHA256SUMS.txt`。除非发布说明另有注明，Mac 应用未经 Apple 公证，Windows EXE 未签名。校验值用于核对下载内容，发布者身份由代码签名验证。

| 系统 | 在下载目录校验 ZIP | 首次启动 |
|---|---|---|
| Mac（Apple Silicon） | 终端运行 `shasum -a 256 BoardBench-macOS-arm64.zip` | 解压，将 BoardBench.app 拖入“应用程序”，双击打开。若仅提示开发者未经验证，确认下载来源可信且摘要一致后，在“系统设置 → 隐私与安全性”找到 BoardBench，点“仍要打开”并确认。 |
| Windows（x64） | PowerShell 运行 `Get-FileHash .\BoardBench-Windows-x64.zip -Algorithm SHA256` | 解压并双击 BoardBench.exe。若仅提示 SmartScreen 无法识别该应用，确认来源和摘要后，可在系统提供该选项时点“更多信息 → 仍要运行”。窗口提示缺少 WebView2 时，安装微软的 Evergreen Runtime。 |

将命令输出与清单中**相同文件名**一行的 64 位摘要逐位对照。摘要不同请重新下载。遇到恶意软件检测、文件损坏或公司设备策略拦截时，请停止启动并联系管理员。首次看到本地板子测试台后，即可插入板子的 USB 数据线并选择串口。[Apple 首次打开说明](https://support.apple.com/zh-cn/102445) · [Microsoft SmartScreen 说明](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation) · [WebView2 下载](https://developer.microsoft.com/microsoft-edge/webview2/)

## 维护者发布已签名版本

在各平台从同一份待发布提交构建。示例使用 `v0.5.1`，实际发布时换成新版本号。各发布机安装 Python 3.12 后，先运行 `python -m pip install -r requirements.txt pyinstaller`。签名使用发布者经过验证的个人或组织身份，该名称可能对下载者公开。证书私钥、硬件令牌 PIN 和公证凭据仅在发布机上使用。

### Mac

1. 加入 Apple Developer Program，由具备权限的帐号申请 **Developer ID Application** 证书并安装到钥匙串。执行 `security find-identity -v -p codesigning`，确认能看到含姓名或组织名及 Team ID 的完整身份。[Apple Developer ID](https://developer.apple.com/developer-id/)
2. 安装包含 `notarytool` 和 `stapler` 的 Xcode 工具。运行 `xcrun notarytool store-credentials BoardBench-notary`，按提示输入 Apple 账户、团队 ID 和 App 专用密码。也可按 Apple 文档使用 App Store Connect API 密钥建立同名钥匙串配置。[Apple 公证工作流](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow)
3. 在仓库根目录执行以下命令。打包器用指定身份签署内嵌二进制及 App；脚本等待 Apple 接受提交，装订并验证票据，通过 Gatekeeper 和桌面启动测试后重新压缩。

```sh
export BOARDBENCH_MAC_IDENTITY='Developer ID Application: 你的发布者名称 (团队ID)'
export BOARDBENCH_NOTARY_PROFILE='BoardBench-notary'
python3 scripts/signed_release.py v0.5.1
```

### Windows

1. 取得受 Windows 信任的代码签名证书（如公共 CA 的 OV 代码签名证书），按颁发方说明安装硬件令牌或密钥提供程序，使当前用户的“个人”证书存储区能访问证书及对应私钥。安装 Windows SDK，在能运行 `signtool.exe` 的开发者 PowerShell 中操作。[微软代码签名选项](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options)
2. 执行 `Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Select-Object Subject,Thumbprint,HasPrivateKey`，选用发布者的证书。填入证书指纹和 CA 提供的 RFC 3161 时间戳地址，再运行脚本。SHA-1 指纹用于选择证书，文件签名和时间戳均使用 SHA-256。[Microsoft SignTool](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool)

```powershell
$env:BOARDBENCH_WIN_CERT_SHA1 = '换成证书的40位指纹'
$env:BOARDBENCH_TIMESTAMP_URL = '换成证书机构的RFC3161时间戳地址'
python scripts/signed_release.py v0.5.1
```

3. 脚本签署最终 EXE，验证 Authenticode 信任链和时间戳，并实际启动桌面程序。签名后用户可以在 EXE 的“属性 → 数字签名”中查看发布者。新发布者或新文件仍可能触发 SmartScreen 信誉提示。[微软信誉机制](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)

### 发布到 GitHub

在 `dist/signed/<版本>/<平台>/` 中取得验证通过的 ZIP 和对应的 `.sha256`。在 GitHub Releases 创建与构建提交对应的新标签和草稿，上传两个平台的 ZIP，将两个校验文件的内容合并为 `SHA256SUMS.txt`。如附带固件，同样将该固件包的 SHA-256 加入清单。核对草稿附件后再发布，并在说明中注明实际签名的发布者和 Mac 公证结果。下载者可据此核对身份。普通 CI 生成的测试包与正式签名产物存放在不同目录。

## 在软件内更新

从 v0.13.0 开始，桌面版启动时通过 HTTPS 查询本仓库的最新稳定发行版，发现新版后自动下载并校验。稍后再打开软件也可复用已校验的安装包。点窗口右上角「软件更新」可以重新检查、阅读更新内容、下载适用于当前系统的包，完成后点「立即重启更新」。有测试、刷写或回声服务运行时请先结束任务。应用须放在当前用户可写的位置，Mac 可以放在用户的 Applications 目录。旧版用户首次安装带更新入口的版本后即可使用。

桌面版按 GitHub Release 记录的文件名、大小和 SHA-256 校验 ZIP。安装助手在应用退出后替换程序，复用原有数据目录和端口，新窗口载入后再清理备份。安装日志位于数据目录的 `update-install.log`。如果安装期间断电或新版超时，安装位置旁的 `.BoardBench-update-*` 目录可能保留旧程序供恢复。源码和浏览器模式继续通过原有方式更新。

发布桌面新版时同步修改 `boardbench/version.py`，使用与版本相符的稳定标签，上传对应架构 ZIP 后再公开该 Release。Mac 使用带顶层 BoardBench.app 的 ditto ZIP，Windows ZIP 根目录放 BoardBench.exe。
