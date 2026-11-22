# KnowAll Studio 桌面客户端

Windows、macOS、Linux 客户端内置同一版本的前端，通过本机临时网关连接云服务。用户无需安装 Python、数据库或 Node.js。首次打开填写云服务根地址，再使用账号与密码登录。HTTPS 是默认要求，仅 `localhost`、`127.0.0.1`、`[::1]` 允许 HTTP 开发服务。

顶部「服务 → 更换云服务」会清除当前客户端认证、缓存与离线队列，并重新打开连接页面。正常重启保留已配置服务的端口与登录状态；原端口被占用时使用新端口并重新登录。云端不可用时显示连接失败，保留地址以便重试。学习资料由云端存储，客户端不内置独立账号库。

## 构建

使用 Node.js 24，在 `frontend` 和 `electron` 分别运行 `npm ci`。运行 `npm test`，然后按构建机器选择 `npm run build:win`、`npm run build:mac` 或 `npm run build:linux`。脚本重新构建前端、由现有品牌 SVG 生成图标，再输出至 `electron/release/`。Windows 生成 NSIS 安装包，macOS 生成 Intel/Apple Silicon DMG，Linux 生成 x64 AppImage 与 deb。

`.github/workflows/desktop-release.yml` 提供三端原生 runner 的手动构建，不自动发布。无发行证书时产物未签名；macOS 公证与 Windows 发行签名需要另行配置证书。跨系统构建与安装体验必须分别验证，不可用 Windows 的成功推定其他系统已通过。

`npm run smoke:packaged` 在 Windows 运行实际 `win-unpacked/KnowAll Studio.exe`，验证首启、资源加载、代理、切换服务。使用临时服务响应与临时用户目录，不是云端账号/数据库集成验收。

`npm run smoke:cloud` 进一步使用实际云账号网关与两个真实个人学习进程验收打包客户端。运行前需开发机器的 Python 后端依赖、可导入的 KAF 包、frontend 的 Playwright 依赖及已构建 EXE；可通过 `KNOWALL_TEST_PYTHON` 指定 Python 路径，通过 `KNOWALL_DESKTOP_EXECUTABLE` 指定解包后的应用程序路径。这些依赖仅供开发验收，普通客户端用户无需安装 Python。

该验收创建临时账号和存储，经真实接口上传 Markdown，使用固定内容生成测试学习包（不调用外部 AI），操作匹配题部分得分、来源定位、错题复习、卡片评分和 JSON 下载。第二个独立客户端校验相同账号的数据与旧题库进度；回收个人进程后再次校验持久化，并验证其他账号不能访问资料与学习包。结束时关闭进程、清理临时目录。截图及包含打包文件校验值的结果在 `release/cloud-integration/`，只有完整流程及清理成功才写入通过结果。

无需启动桌面时，在 backend 运行 `python scripts/cloud_integration_fixture.py --check` 可以检查真实 HTTP、资料上传、学习评分/导出、空间隔离和进程恢复。主 CI 已加入该检查；本机运行不代表远程 CI 或 Docker/TLS 已验证。

## 运行设置

- `KNOWALL_SERVER_URL`：可覆盖首次连接服务地址，仍执行 HTTPS 与健康检查。
- `KNOWALL_DESKTOP_DATA_DIR`：可选的绝对用户数据目录，支持隔离测试和独立客户端配置；默认采用 Electron 的系统用户数据目录。
- `npm start`：本地运行云客户端；需要先构建前端。
- `npm run dev`：仅供开发者，保留旧 Python/Vite 启动方式，需要完整后端环境。正式安装包始终使用云模式。

打包白名单仅包含 Electron 主壳、连接设置页、图标及前端构建；不打包 backend、`.env`、数据库、API 密钥、用户数据。升级安装保留客户端用户数据。
