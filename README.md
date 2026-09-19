# PPanel 单容器部署

本仓库提供一个包含 PPanel 后端、用户前端和管理前端的单容器镜像：

```
ghcr.io/yywo/ppanel:latest
```

容器内部由 Nginx 对外监听 8080，用户端和管理端分别是：

```
http://127.0.0.1:8080/
http://127.0.0.1:8080/admin/
```

Nginx 将 /v1、/v2 以及订阅地址转发到同一容器内的后端
127.0.0.1:8081。当前 GHCR 工作流发布 linux/amd64 镜像。

## 一、准备宿主机配置

后端启动前必须准备好已经初始化的 ppanel.yaml，并放在宿主机配置目录中。按照原项目的约定，配置目录映射到容器的 /app/etc。

宝塔/现有服务器推荐使用：

```
/www/wwwroot/ppanel/config
```

确认配置文件存在：

```bash
mkdir -p /www/wwwroot/ppanel/config
test -f /www/wwwroot/ppanel/config/ppanel.yaml
```

如果已有原 PPanel 配置，应把原配置目录中的文件复制到该目录，不要覆盖已有数据库密码、JWT 密钥等配置：

```bash
cp -a /path/to/existing/ppanel-config/. /www/wwwroot/ppanel/config/
```

仓库内的 [ppanel.yaml.example](ppanel.yaml.example) 是完整配置模板。只有在配置目录还没有 `ppanel.yaml` 时才复制它，复制后必须修改数据库连接、密码和 `JwtAuth.AccessSecret`：

```bash
cp -n ppanel.yaml.example /www/wwwroot/ppanel/config/ppanel.yaml
vi /www/wwwroot/ppanel/config/ppanel.yaml
```

示例文件中的凭据仅用于示例，不能直接用于生产环境。

配置至少需要包含已配置的数据库地址和 JwtAuth.AccessSecret。当前镜像不负责首次安装页面，也不支持启动时才创建数据库配置。

可以继续使用原项目的配置字段，例如：

```yaml
Host: 0.0.0.0
Port: 8080
JwtAuth:
  AccessSecret: replace-with-a-random-secret
  AccessExpire: 604800
MySQL:
  Addr: 127.0.0.1:3306
  Username: ppanel
  Password: change-me
  Dbname: ppanel
Redis:
  Host: 127.0.0.1:6379
```

上面只是字段示例，请优先保留原项目生成的完整配置。数据库和 Redis 如果运行在其他容器中，地址应使用 Docker 网络中的服务名，而不是 127.0.0.1。

## 二、配置文件映射说明

本仓库的 compose.yaml 默认使用下面的映射：

```yaml
volumes:
  - type: bind
    source: ${PPANEL_CONFIG_DIR:-/www/wwwroot/ppanel/config}
    target: /app/etc
    read_only: true
    bind:
      create_host_path: false
```

含义是：

- 宿主机 /www/wwwroot/ppanel/config 映射到容器 /app/etc；
- 只读挂载，容器不会直接改写宿主机配置；
- 配置目录不存在时 Compose 直接报错，不会悄悄创建空目录；
- 容器启动时把配置复制到 /run/ppanel/etc，仅修改副本的内部监听地址为 127.0.0.1:8081。

与原网关部署不同，新镜像已经把前端和后端二进制打包进去，因此不要再使用旧的这些映射：

```
/www/wwwroot/ppanel/web:/app/static
./ppanel-server:/app/modules/ppanel-server
```

它们会覆盖镜像内容或与当前 Nginx 路由不一致。前端位于镜像内的 /app/web/user 和 /app/web/admin，后端位于镜像内的 /app/modules/ppanel-server。

## 三、使用 Docker Compose 部署

服务器需要安装 Docker Engine 和 Docker Compose v2。进入 Compose 项目目录：

```bash
cd /www/server/panel/data/compose/ppanel
```

如果该目录还没有本仓库代码，首次可以执行：

```bash
git clone https://github.com/yywo/ppanel.git /www/server/panel/data/compose/ppanel
cd /www/server/panel/data/compose/ppanel
```

创建 .env，明确指定本仓库镜像和配置目录：

```dotenv
PPANEL_IMAGE=ghcr.io/yywo/ppanel:latest
PPANEL_CONFIG_DIR=/www/wwwroot/ppanel/config
```

如果 GHCR 包是私有的，先登录 GHCR。需要使用具有 read:packages 权限的 GitHub Token：

```bash
docker login ghcr.io
```

启动前先检查 Compose 展开的映射：

```bash
docker compose config
```

拉取并启动：

```bash
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail=100 ppanel
```

当前 Compose 设置为：

```
容器名：ppanel-service
宿主机监听：127.0.0.1:8080
容器监听：8080
重启策略：always
```

因此服务只对本机开放。宝塔或其他外部 Nginx 需要反向代理到 127.0.0.1:8080，用户端路径为 /，管理端路径为 /admin/。

## 四、检查运行状态

```bash
docker compose ps
docker inspect --format='{{json .State.Health}}' ppanel-service
nc -z 127.0.0.1 8080
curl -I http://127.0.0.1:8080/
curl -I http://127.0.0.1:8080/admin/
```

查看实际挂载的宿主机配置：

```bash
docker exec ppanel-service cat /app/etc/ppanel.yaml
```

查看启动时生成的后端有效副本：

```bash
docker exec ppanel-service cat /run/ppanel/etc/ppanel.yaml
```

日志和退出原因：

```bash
docker compose logs -f ppanel
docker inspect ppanel-service --format='exit={{.State.ExitCode}} error={{.State.Error}}'
```

## 五、升级和回滚

升级 latest：

```bash
docker compose pull
docker compose up -d --force-recreate
docker compose ps
```

配置目录不会被镜像更新删除或覆盖。升级前建议备份：

```bash
tar czf /root/ppanel-config-$(date +%Y%m%d-%H%M%S).tgz \
  -C /www/wwwroot/ppanel config
```

每次发布还会生成 sha-xxxxxxx 镜像标签。出现问题时，可以临时在 .env 中指定旧的 SHA 标签回滚：

```dotenv
PPANEL_IMAGE=ghcr.io/yywo/ppanel:sha-xxxxxxx
PPANEL_CONFIG_DIR=/www/wwwroot/ppanel/config
```

然后执行：

```bash
docker compose pull
docker compose up -d --force-recreate
```

## 六、自动更新和 GHCR 发布

[.github/workflows/publish-ghcr.yml](.github/workflows/publish-ghcr.yml) 有四种触发方式：

- 推送到 main：使用仓库当前锁定版本构建并发布；
- 推送 v* 标签：使用当前锁定版本发布版本标签；
- 每天北京时间 02:17：查询后端和前端最新稳定 Release，下载并校验后构建；
- Actions 页面点击 Run workflow：默认也会查询最新稳定版本，可取消 refresh_upstream 后只重建当前锁定版本。

每日任务的顺序是：

```
查询最新稳定后端/前端 Release
        ↓
校验官方 SHA-256，安全解包
        ↓
构建后端和两个前端
        ↓
运行 Linux 容器集成测试
        ↓
构建并推送 ghcr.io/yywo/ppanel:latest
        ↓
镜像推送成功后提交 upstream.lock.json
```

如果上游下载、前端兼容性补丁、Docker 构建或容器集成测试失败，工作流会失败，不会提交新的锁文件，也不会更新 latest。自动提交使用 GitHub Actions 自己的 Token，并带有 [skip ci]，不会形成循环构建。

服务器只需要拉取镜像，不需要在服务器上安装 Bun、Node、Python 或编译后端：

```bash
docker compose pull
docker compose up -d --force-recreate
```

GitHub Actions 不会连接服务器，也不会修改 /www/wwwroot/ppanel/config 或重启生产容器。

## 七、本地构建和测试

刷新并锁定指定版本：

```bash
python3 scripts/update.py --resolve \
  --backend v1.20.3 \
  --frontend v1.21.0 \
  --arch amd64
```

有 Docker 的 Linux 机器上构建：

```bash
python3 scripts/update.py --build
python3 scripts/test_container.py \
  ppanel-local:v1.20.3-v1.21.0-amd64 --browser
```

没有 Docker 时，仍可运行前端构建、Nginx 路由和 Python 单元测试。生产环境不要直接使用未经容器集成测试的镜像。
