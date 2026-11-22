# KnowAll Studio（智识工坊）

农客松参赛公开版，收录智识工坊 Web、后端、桌面与移动端源码。运行时可导入自己的课程资料，示例使用可公开的课程练习。

[两分钟操作演示](https://knowall-agri-studio.vercel.app/demo.mp4) · [完整操作录屏](https://knowall-agri-studio.vercel.app/full.mp4)

## 环境与启动

在 Windows 10/11 上完整解压，保留目录结构。建议使用 Python 3.11、Node.js 22 LTS 和 npm。首次安装需要联网下载前后端依赖，并预留数 GB 磁盘空间。请使用空闲端口 5273 和 8001。

在项目根目录打开 PowerShell。网页复用移动端的学习进度模块，先安装移动端依赖以解析 Expo 的 TypeScript 配置，再启动后端与网页。

```powershell
npm ci --prefix ./KnowAll_Mobile
python -m venv .venv
$env:Path = "$(Resolve-Path .venv/Scripts);$env:Path"
.\KnowAll_Studio\start.bat
```

启动器安装后端 requirements.txt、本地 kaf_format 和前端 npm 依赖。浏览器入口为 http://localhost:5273 。后端健康检查为 http://127.0.0.1:8001/health 。此参赛副本默认在后端 data 目录创建本机 SQLite 数据库。在本机运行期间保持两个服务窗口开启。

## 复现视频中的课程

首次启动后，在解压根目录另开一个 PowerShell 窗口，执行下列命令，再刷新浏览器。

```powershell
.\.venv\Scripts\python.exe .\demo\seed.py
```

打开“我的资料”，选择“分子生物学 · 遗传信息的传递”。阅读“转录与翻译”，写下一条笔记，再做章节练习、查看解析。进入闪卡复习，翻面后按掌握程度评分。最后搜索“转录”，打开匹配的章节核对答案。示例载入四份教学资料，其中分子生物学资料含四章、四题与四张卡，均为预先编写的教学示例。以上流程可使用本地数据。需要实时 AI 生成时，在应用设置中连接自己的模型服务。

## 源码范围

KnowAll_Studio 包含 React/Vite 前端、FastAPI 后端和 Electron 桌面外壳。KnowAll_Mobile 为 Expo 移动端。kaf_format 是本地知识包组件。前后端依赖清单、锁文件、随附的第三方许可与源码见各组件目录。提交副本采用本地数据库默认配置，演示资料由脚本生成。
## 公开版材料

本版本提供完整产品源码和可公开的示例题。内置课程与专题练习选用精简的示例数据，自己的课程可通过资料导入功能添加。第三方组件沿用各自随附的许可证和声明。
