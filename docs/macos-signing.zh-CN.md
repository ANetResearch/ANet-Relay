# macOS 签名与公证

ANet Relay 以 Developer ID 方式在 GitHub Release 上分发（不上架 Mac App Store），不需要描述文件，也不需要在开发者后台注册 Bundle ID。仓库配置了下列 Secret 后，`v*` 标签触发的发布流水线会对 macOS 包做 Developer ID 签名（Hardened Runtime）、公证并装订票据，用户下载后可直接打开。未配置时自动退回 ad-hoc 签名，首次打开需要移除隔离属性。

## 1. 准备 Developer ID Application 证书

以下三步须在**同一台 Mac** 上完成：私钥在生成 CSR 时写入这台 Mac 的钥匙串。

**生成 CSR**

1. 打开"钥匙串访问"，菜单 钥匙串访问 → 证书助理 → **从证书颁发机构请求证书…**
2. 用户电子邮件地址填 Apple ID 邮箱；常用名称自定（如 `ANet Relay Developer ID`）；CA 电子邮件地址留空；选择 **存储到磁盘**。
3. 保存得到 `CertificateSigningRequest.certSigningRequest`。

**创建证书**

1. 打开 [developer.apple.com/account](https://developer.apple.com/account) → Certificates, IDs & Profiles → Certificates → **+**。
2. Software 下选择 **Developer ID Application** → Continue。该项不可选时，说明当前账号不是团队的 Account Holder；只有 Account Holder 能创建 Developer ID 证书。
3. Profile Type 选择 **G2 Sub-CA (Xcode 11.4.1 or later)**，上传 CSR → Continue → **Download**，得到 `developerID_application.cer`。

**导出 .p12**

1. 双击 `.cer` 安装到"登录"钥匙串。
2. 钥匙串访问 → 登录 → **我的证书**，找到 `Developer ID Application: 名称 (TEAMID)`，展开确认下面带有私钥。
3. 右键证书 → 导出 → 格式 `.p12`，设置导出密码。
4. 在终端执行 `security find-identity -v -p codesigning`，应能看到这张证书。

也可以在 Xcode → Settings → Accounts → Manage Certificates → **+** → Developer ID Application 直接创建，再按上面第 2–3 步导出。

## 2. 准备公证用的 App Store Connect API Key

App Store Connect → 用户和访问 → 集成 → App Store Connect API → 团队密钥 → 新建，角色选 Developer。记下 **Key ID** 与 **Issuer ID**，下载 `AuthKey_<KeyID>.p8`（只能下载一次）。

## 3. 配置仓库 Secret

GitHub 仓库 → Settings → Secrets and variables → Actions → New repository secret：

| Secret | 内容 |
|---|---|
| `CSC_LINK` | `.p12` 的 base64：`base64 -i DeveloperID.p12 \| pbcopy` 后粘贴 |
| `CSC_KEY_PASSWORD` | `.p12` 的导出密码 |
| `APPLE_API_KEY_P8` | `.p8` 文件的完整文本（含 BEGIN / END 行） |
| `APPLE_API_KEY_ID` | Key ID |
| `APPLE_API_ISSUER` | Issuer ID |

`.p12` 与 `.p8` 请自行离线备份；不要提交到仓库，也不要通过聊天或邮件传递。

## 4. 验证

在 Actions → Release → **Run workflow**（分支 main）手动运行一次：只构建、不发布，产物在该次运行的 Artifacts 中。macOS 任务日志里应依次出现 `signed … with the Developer ID (Hardened Runtime)` 和 `notarized and stapled`。下载产物后可在 Mac 上核对：

```sh
codesign --verify --deep --strict --verbose=2 "/Applications/ANet Relay.app"
spctl --assess --type execute --verbose=2 "/Applications/ANet Relay.app"   # 应显示 source=Notarized Developer ID
xcrun stapler validate "/Applications/ANet Relay.app"
```

## 实现说明

- 签名由 `scripts/after-pack.cjs` 完成（`electron-builder.yml` 中 `identity` 保持 `null`）：先逐个签名 PyInstaller 后端的 Mach-O 文件，再用 `@electron/osx-sign` 签名 Electron 框架、辅助进程与主程序，全部启用 Hardened Runtime 并使用 `brand/entitlements.mac.plist`（JIT、未签名可执行内存、关闭库校验、摄像头）。
- 公证使用 `xcrun notarytool submit --wait`，通过后 `xcrun stapler staple` 并以 `spctl` 复核；随后才生成 zip 与 dmg。
- 本地 Mac 也可以签名：设置 `MAC_SIGN_IDENTITY`（证书名称或 SHA-1）以及可选的 `APPLE_API_KEY_PATH`、`APPLE_API_KEY_ID`、`APPLE_API_ISSUER` 后运行 `npm run dist:mac`。
