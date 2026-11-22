# KnowAll 云服务部署包

此包提供集中账号服务、每个账号独立学习数据库/文件/向量目录和 Caddy HTTPS 入口。Windows、macOS、Linux 客户端填写同一个 HTTPS 服务地址，登录相同账号可访问同一学习空间。本次没有实际部署服务器或申请证书。

首版采用 SQLite 与单实例网关，仅允许一个网关进程管理持久卷，不能通过增加副本直接实现分布式扩容。个人学习进程按需启动，默认最多 8 个；满载返回可重试的服务繁忙提示。模型/解析依赖较大，需要按实际并发准备内存与磁盘。跨个人空间分享、协作和管理能力仍需单独的产品设计；现有个人空间中的协作功能不会自动获得另一账号的私人资料。

## 准备与启动

需要一台可运行 Docker Compose 的 Linux 服务器、自己控制的域名，以及把域名指向该服务器的 DNS。开放 80/443 端口。以下命令在仓库根目录执行；云镜像只复制明确列出的源码，开发 `.env`、密钥和已有数据均不进入构建上下文。

```sh
cp KnowAll_Studio/docker/cloud/.env.example KnowAll_Studio/docker/cloud/.env
# 编辑 .env 的 KNOWALL_DOMAIN 和 ACME_EMAIL。
# 可选：生成自己的 JWT_SECRET 并写入 .env，不能复用开发密钥。
python -c 'import secrets; print(secrets.token_urlsafe(64))'
cd KnowAll_Studio/frontend
npm ci
npm run build
cd ../..
docker compose --env-file KnowAll_Studio/docker/cloud/.env -f KnowAll_Studio/docker/cloud/compose.yaml config
docker compose --env-file KnowAll_Studio/docker/cloud/.env -f KnowAll_Studio/docker/cloud/compose.yaml up -d --build
docker compose --env-file KnowAll_Studio/docker/cloud/.env -f KnowAll_Studio/docker/cloud/compose.yaml logs --tail=100
```

`JWT_SECRET` 留空时，入口生成随机密钥并保存到持久卷。之后启动继续使用同一密钥。中央数据库是 `/srv/knowall/accounts.sqlite`；账号学习空间位于 `/srv/knowall/workspaces/<账号 ID 的 SHA-256>/`。后端与个人学习进程只在容器网络/loopback 上使用 HTTP，公网 TLS 由 Caddy 提供。网关端口不发布到宿主机。访问 `https://自己的域名/health` 检查运行状态，再在客户端配置此 HTTPS 地址，完成注册、登录、上传和退出验收。

网关信任此 Compose 私网中 Caddy 重写的转发 IP，用于区分登录限流的客户端地址。保持 8000 端口不对外发布，并且不要把网关加入不受信任容器的共享网络。网关与个人进程关闭访问日志，避免把 WebSocket 查询参数中的登录令牌写入日志。

登录注册使用中央数据库，资料、学习记录和用户设置的 AI Key 使用个人学习空间。云端本地自动登录永久关闭。开发配置不会被读取，个人进程也不会继承网关的供应商密钥。AI 功能需要用户在设置中添加自己的 Key。当前邮箱验证/找回接口没有邮件发送服务，第三方 OAuth/SMS 登录未配置，界面仅提供账密注册登录；正式开放这些功能前必须补齐发送和回调服务。

首次启动建立新数据库并登记迁移版本；已有带版本数据库执行迁移。发现无版本旧库时拒绝启动，避免自动清空用户资料。迁移错误需要先备份并人工排查。

## 备份、恢复与更新

持久卷包含账号库、全部个人资料和加密/JWT 密钥，必须一起备份。为 SQLite 保持一致快照，先停止网关及个人进程，再通过同一个 Compose 服务导出持久卷；命令输出文件在仓库根目录：

```sh
docker compose --env-file KnowAll_Studio/docker/cloud/.env -f KnowAll_Studio/docker/cloud/compose.yaml stop
docker compose --env-file KnowAll_Studio/docker/cloud/.env -f KnowAll_Studio/docker/cloud/compose.yaml run --rm --no-deps -T gateway tar -C /srv/knowall -czf - . > knowall-backup.tar.gz
docker compose --env-file KnowAll_Studio/docker/cloud/.env -f KnowAll_Studio/docker/cloud/compose.yaml up -d
```

将备份和 `.env` 保存到有权限保护的离线位置。上述二进制重定向命令应在 Linux shell 执行。恢复应停机到空卷，通过 `docker compose run --rm --no-deps -T gateway tar -C /srv/knowall -xzf - < knowall-backup.tar.gz` 还原，再启动；若旧卷已有数据，先保留旧卷并创建新的恢复卷，避免混入不同版本资料。更新前备份，重新构建前端和镜像，然后 `up -d --build`。勿运行 `down -v`，它会删除持久卷。

## 验证范围

源码单元与 ASGI 测试使用临时 SQLite 和受控响应，覆盖账号校验、空间路由、流式上传、重定向保护及入口持久配置。部署 ZIP 还包含 `backend/scripts/cloud_integration_fixture.py`：在已安装后端依赖和 KAF 包的开发环境，于 backend 运行 `python scripts/cloud_integration_fixture.py --check`，即可启动临时真实云网关与学习进程，验证注册、Markdown 上传、部分评分、卡片复习、导出、跨账号拒绝及进程恢复。此脚本自动创建和清理测试账号/数据，仅内容生成使用固定样例，不访问现有资料或调用付费模型。

桌面源码目录的 `npm run smoke:cloud` 另操作实际打包 EXE，并验证独立客户端同步、旧题库进度与文件下载。开发验收依赖不影响普通用户的客户端安装。

这些临时 loopback 检查不代替已部署服务器的验收。镜像构建、容器启动、真实域名 TLS、备份恢复和三端远程联调需在具备 Docker/服务器环境时执行，未执行前不能视作上线验收通过。
