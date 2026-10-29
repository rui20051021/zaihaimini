# 迷宫多人疏散实验

Python 3.9 或更新版本。主机和手机通过浏览器使用同一套真实 3D 建筑场景；二维网格只作为历史兼容代码，不参与新 v2 场景运行。

本项目可按“一场次 = 一个实验试次（trial）”的方式运行，自动收集逐帧轨迹与
场次汇总数据，并支持参与者编号、知情同意、实验条件/分组标注。研究使用前仍须完成
仿真模型验证、伦理审查和数据治理，详见文末限制说明。

## 启动

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

主机使用终端打印的 `127.0.0.1` 地址。手机与电脑连接同一个 Wi-Fi，扫描主机二维码。
Three.js 0.160.0 已保存在 `static/vendor/`，使用时无需连接外网；许可见 `THREE-LICENSE.txt`。

## 真人实验模式

默认启动为真人实验模式（`MAZE_DEFAULT_NPCS=0`）：系统不会自动生成 NPC。主试打开主机页面后，让参与者扫码或访问手机端，完成参与者编号和知情同意；人数出现在主机后点击“开始演示”或“开始正式实验”。如需 NPC 对照演示，可启动时设置 `MAZE_DEFAULT_NPCS=20`，或通过受保护的 v2 控制接口调整 NPC 数量。

## 真实 3D 场景与校准

新实验使用 `schema: 2` 场景清单。当前内置的 Uncle Mugen 学校模型位于
`static/scenes/uncle_mugens_school_building_display.glb`，配套标注在
`static/scenes/scene-v2.json`。首版标注是**临时比例**和演示用的楼层/楼梯草案，
正式实验前必须在校准器中用已知长度确认比例，并把场景状态保存为 `verified`。

- 主机 `/`：默认进入真实 3D 总览，显示所有 NPC、真人、楼层、楼梯、火源和分层烟气。
- 手机 `/play`：第一人称真实 3D 视角，方向键/键盘/拖动视角控制；到达楼梯后连续上下楼。
- 校准器 `/calibrate?scene=school`：上传单文件 `.glb`，测量比例，创建楼层、可走区域、障碍孔洞、楼梯、出口和起始区，保存并激活。
- 运行时不显示二维网格；服务端只把导航三角面作为隐藏计算单元，建筑 GLB 负责视觉显示。
- `GET /api/scenes` 列出场景；`/api/scenes/upload`、`calibration` 和 `activate` 用于场景管理。
- `GET /api/v2/state`、`/ws/host-v2`、`/ws/player-v2` 使用 `protocol: 2`，角色带连续 `x/y/z`、楼层、速度、烟气、血量和楼梯连接器状态。

烟气是按导航区域/三角面和楼梯传播的分层近似模型，支持风向；它不是 CFD，也不模拟
CO/FED 或真实消防规范。历史 `evac_latest.json` 和旧 CSV 不修改，新场次写入
`evac_latest_v2.json` 与 `evac_latest_tracks_v2.csv`。学校模型来源、CC BY 署名和许可见
`static/scenes/CREDITS.txt`。

### 运行探针与主机保护

部署到反向代理或局域网服务器时，可以使用：

- `GET /healthz`：进程存活探针。
- `GET /readyz`：地图、仿真和数据卷均可写入时才返回就绪；响应中的 `checks` 会分别报告 `maze`、`simulation`、`scene_3d`、`data_dir`；3D 场景文件缺失或数据目录只读时返回 HTTP 503。
- `GET /api/version`：查看当前应用版本。

主机控制台的“结束并归档本场”会关闭当前 trial 并保存汇总和轨迹，不会立即删除内存状态；之后再次开始会进入新 trial。它适合在实验规定的停止时刻结束场次，避免用“重置人群”代替结束操作。

桌面开发默认会自动打开主机页面；服务器或容器环境设置 `MAZE_OPEN_BROWSER=0` 可关闭这一行为。

如果主机页面可能暴露到不可信网络，启动时设置管理令牌：

```sh
MAZE_ADMIN_TOKEN='change-this-long-random-token' python main.py
```

打开 `http://主机地址:端口/`，在主机大屏验证框中输入令牌。令牌通过同源 POST 请求交换为 12 小时的 HttpOnly、SameSite 会话 Cookie，不写入 URL、浏览器存储或 WebSocket 地址；重启服务会注销现有管理会话，主机大屏也提供显式退出。登录失败会限流；主机控制 WebSocket、实验控制、参与者数据和导出接口都会要求有效会话，并校验同源来源；WebSocket 每 30 秒复核会话有效期；手机玩家接口不需要管理令牌。正式部署仍应使用 HTTPS/TLS、反向代理限流，并避免把令牌放在截图或聊天记录中。

如果应用位于反向代理之后，代理必须把外部的 `Host`、`Origin` 和协议正确转发；只有在代理完全由你控制、且会覆盖而不是透传客户端值时，才设置：

```sh
MAZE_TRUST_PROXY=1 MAZE_ADMIN_TOKEN='change-this-long-random-token' python main.py
```

启用后程序才会使用代理提供的 `X-Forwarded-Proto` 和 `X-Forwarded-Host` 做同源校验及安全 Cookie 判断。不要在没有可信代理时开启，否则客户端可以伪造这些请求头。

### 跨网络分享地址

默认二维码使用局域网地址。若通过 Cloudflare Tunnel、反向代理或其他公网入口分享，可以固定端口并设置公共地址：

```sh
MAZE_PORT=8000 MAZE_PUBLIC_URL=https://example.com python main.py
```

`MAZE_PUBLIC_URL` 可以填写站点根地址，程序会自动补上 `/play`；也可以直接填写完整的玩家地址。
macOS 优先使用 en0/en1 的地址。如果电脑有多个网卡，可手动指定手机可达的地址：

```sh
MAZE_LAN_IP=192.168.1.20 python main.py
```

运行中的 Python 终端按 Ctrl+C 停止，当前实验汇总保存到 `data/evac_latest.json`，轨迹保存到 `data/evac_latest_tracks.csv`。
每场自然结束、重置人群、重建地图、加载场景或手动归档时，都会另存带时间戳的独立汇总 JSON 和轨迹 CSV，
文件名形如 `evac_<时间戳>_t<场次号>_<运行号>.json`，同一秒内多次落盘也不会互相覆盖。
归档写盘失败时，系统会保留内存中的当前场次并拒绝重置/换图/载入场景，管理 API 返回 HTTP 507；
自然结束时会报告自动归档失败，之后的重置操作会再次尝试归档。正常关闭时也会尝试生成独立场次归档。

仓库提供只读数据校验器 `scripts/verify_data.py`，会检查汇总 JSON、轨迹 CSV 的基本结构、配对关系和行列一致性；它兼容旧版数据，默认把缺少新字段或配套轨迹的文件报告为警告，备份前可用 `--strict` 将这些问题升级为错误。`scripts/backup_data.py` 会在源目录之外创建带 `BACKUP-MANIFEST.json` 和 SHA-256 清单的 `.tar.gz` 备份，创建时会再次验证归档内容，采用临时文件加原子替换，默认拒绝覆盖已有备份；`scripts/verify_backup.py` 可在复制到另一台机器后独立验证归档。

服务会在 `data/audit.jsonl` 追加精简的操作审计记录（时间、请求 ID、路由、状态码和来源地址），用于排查误操作和复现实验流程；不会记录令牌、请求体或参与者编号。该文件属于敏感运行日志，应和实验数据一起保护并纳入备份/保留策略。可通过 `MAZE_AUDIT_LOG` 指定其他路径。

场景导入接口限制 JSON 请求体为 1 MB，实验元数据接口限制为 16 KB；主机页面也会在上传前拒绝超过 1 MB 的场景文件。正式部署仍应在反向代理层配置更严格的请求体、超时和速率限制。

## 参与者与知情同意

- 默认要求真实参与者填写参与者编号并勾选知情同意才能加入（`MAZE_REQUIRE_CONSENT=1`）。
  手机端会弹出同意层：填写编号、勾选同意后才允许连接；拒绝同意会被服务端以 4005 关闭，
  缺少编号以 4004 关闭。编号与同意状态会写入轨迹与汇总，匿名场景可自行填写内部编号。
- 手机端提供“退出实验”入口：确认后立即停止输入并断开连接，后续重新加入需再次阅读并确认同意。
  退出不会自动删除已采集或已归档数据；数据撤回按本研究伦理审批与数据管理流程联系主试处理。
- 同一个参与者编号在同一场次内只对应一个身份：换个页面/设备用同一编号重连会复用原身份与既有数据，
  旧连接会被关闭，避免重复计数。
- 纯演示或无需身份校验时可用 `MAZE_REQUIRE_CONSENT=0` 启动，恢复“扫码即可加入”。
- 未采集同意时字段为 `null`（未知），与“明确拒绝”是两种不同状态，分析时需区分。

```sh
MAZE_REQUIRE_CONSENT=0 python main.py   # 演示模式：不做身份/同意校验
```

## 实验元数据与分组

主机“实验信息”面板可填写实验编号、实验条件、分组、主试与备注，并“保存实验信息”“立即归档本场”。
这些字段会写入每场汇总的 `experiment` 块，用于跨场次分组统计。对应接口：

- `GET  /api/experiment`：查看当前实验元数据。
- `POST /api/experiment`：设置元数据（`experiment_id`、`session_id`、`condition`、`group`、`notes`、`operator`，值须为字符串/数字）。
- `POST /api/experiment/archive`：立即把当前场次落盘为独立文件，不影响正在运行的仿真，
  可在一场进行中定期存档以免数据丢失。
- `POST /api/evac/spawn_zone?x0=&y0=&x1=&y1=`：设置人群起始区（格坐标）；不带参数表示恢复全图随机。

## 实验控制

- 主机默认显示真实 3D 建筑、所有 NPC/真人、楼层和楼梯。可切换楼层聚焦、建筑透明度、自动旋转、跟随真人、隐藏屋顶（需要模型节点包含 roof/ceiling 元数据）和场景校准。
- 当前学校场景是 `provisional`，主机按钮只允许“开始演示”；在 `/calibrate` 完成真实长度校准并保存为 `verified` 后，才允许“开始正式实验”。
- NPC 数量支持 0–300，真人不占 NPC 名额。NPC 使用 A* 导航区域图，遇到火源/高烟气会重新规划；楼梯移动连续插值高度，不瞬移楼层。
- 真人输入由服务端验证，只能在楼层可走区和楼梯连接器中移动。手机视角跟随服务端 `x/y/z`，通过楼梯时相机高度连续变化。
- 火源可绑定到楼层区域；烟气在导航区域之间扩散，并通过楼梯跨楼层传播。风场只影响这套分层近似，模型不宣称 CFD、CO/FED 或真实消防规范精度。
- 控制接口位于 `/api/v2/control/{action}`，包括 `start`、`pause`、`reset`、`npcs`、`fire`、`panic`、`speed`、`wind`、`experiment` 和 `archive`。
- 旧二维控制接口和页面保留为历史兼容/空场景回归模式，但不会参与已激活 v2 场景的新实验。

## 数据字典

新 3D 场次使用独立的 `schema: 2` 文件，不覆盖历史二维数据：

- `main/data/evac_latest_v2.json`：场次汇总，包含 `scene_id`、`scale_status`、实验元数据、楼层数、逃出/倒下统计。
- `main/data/evac_latest_tracks_v2.csv`：逐帧记录 `t_sim_s`、`t_real_s`、`x_m/y_m/z_m`、`floor_id`、`nav_region`、速度、烟气浓度、烟气剂量、血量、压力、密度、状态、逃生和倒下标记。
- WebSocket `protocol: 2` 的角色状态与 v2 CSV 使用同一坐标约定：X/Z 为水平面，Y 为高度，单位为米。
- `provisional` 只允许演示；正式实验应在开始前确认 `verified`，并保留模型 SHA-256、来源和 CC BY 署名。

历史 `evac_latest.json`、历史 CSV 及旧场次归档保持原格式，分析脚本应按文件名/schema 分流读取；新 v2 逻辑不会回写或重算旧数据。

## 开发检查

仓库根目录提供常用命令：

```sh
make compile
make js-check
make test
# 安装 Chromium 并运行真实浏览器的完整流程测试；覆盖手机、3D 校准/楼梯和主机第一视角：
#   - 短横屏（568x320 / 844x390）知情同意与退出/重新加入流程
#   - 同一参与者在第二个标签页重复加入时合并身份（4001），不产生多余样本
#   - 场次结束后新参与者加入被拒绝（4006），不污染已归档样本
#   - 主机端破坏性操作（结束归档/重置人群/重置并灭火/应用设置并重开）必须二次确认；
#     取消与 Esc 不发请求，确认后才真正执行
#   - 主机端 3D 总览：引擎就绪后可切入三维，迷宫与全部角色都在同一屏内，
#     墙体透明/实心、自动旋转、跟随真人、重置视角均可交互，退回二维不黑屏
make e2e
# 先启动服务后，可检查关键 HTTP 接口与本地 3D 资源
make smoke BASE_URL=http://127.0.0.1:8000
# 只读校验已有实验文件，不修改数据；旧版文件会给出 WARN
make verify-data DATA_DIR=main/data
# 备份/交付前可严格要求每个汇总都有轨迹 CSV
make verify-data DATA_DIR=main/data VERIFY_DATA_ARGS=--strict
# 创建带 SHA-256 清单的原子备份，不修改源数据
make backup-data DATA_DIR=main/data BACKUP_OUTPUT=backups/maze-data.tar.gz
# 备份前严格校验；已有同名备份默认不会被覆盖
make backup-data DATA_DIR=main/data BACKUP_OUTPUT=backups/maze-data.tar.gz BACKUP_ARGS="--strict"
# 独立验证归档中的清单和 SHA-256
make verify-backup BACKUP_INPUT=backups/maze-data.tar.gz
```

GitHub Actions 会在 push 和 pull request 时运行 Python 3.9/3.11 回归测试、Python 编译检查、浏览器 JavaScript 语法检查，并在 Linux 上安装 Chromium 运行上面的手机流程端到端测试（browser-e2e job）。

## Docker 部署

仓库提供 `Dockerfile` 和 `docker-compose.yml`。Compose 会把实验数据挂载到 `./main/data/`，把本地学校模型目录只读挂载到容器；模型目录必须挂载且包含与场景清单匹配的 GLB；缺少模型时 3D 页面会明确报错，不会静默回退到二维网格。

```sh
cp .env.example .env
# 至少修改 MAZE_ADMIN_TOKEN；正式环境还应设置 MAZE_PUBLIC_URL
docker compose up --build
```

默认地址是 `http://127.0.0.1:8000/`。Docker 部署时可通过 `MAZE_SCENE_DIR=/app/scene-assets` 指定模型目录；模型文件不应提交到普通 Git 仓库，建议使用挂载卷、对象存储或 Git LFS。

## 验证

```sh
python -m unittest discover -s tests -v
```

测试使用临时数据目录，覆盖计时冻结、重复开始、重置、CSV 逐行状态与公式转义、非法消息、
慢连接隔离、NPC 避火、死因记录、种子复现、场景加载、实验元数据、真人真实时间轴与参与者同意校验。

默认启动适合可信局域网演示；如果要跨网络访问，请设置 `MAZE_ADMIN_TOKEN`，并在 HTTPS 反向代理后运行。
项目仍不是经过伦理审查的正式人体实验系统：正式使用前还需要伦理审查、正式知情同意文本、数据脱敏/加密、备份策略和真实建筑/火灾模型标定。


### 主机「重新开始一场」

在默认 3D 主机页的「场次控制」区点击「重新开始一场」，确认后由
`POST /api/v2/control/restart` 一次完成重置和开始，不需要先点重置再点开始。

- 保留当前场景、NPC 人数、倍速、恐慌系数、风场和在线真人身份；手机无需刷新或重新加入。
- 人物重新回到合法起点，健康/速度/输入/压力/计时归零或恢复初始值；清除火源和烟雾，门恢复场景初始状态。
- 当前实时轨迹和回放会清空；**需要保留本场结果时，请先导出 v2 数据**。此操作本身不修改已保存的 v2 文件及历史二维文件。
- 真人模式须至少有一部手机在线（包含本场已逃出/倒下但仍在线的参与者）；离线参与者不会被重新加入。
- 临时尺度必须显式 `demo:true`；正式实验仍要求场景尺度已确认。接口继续受管理员鉴权保护。
- 确认弹窗支持取消、Esc 退出及重复点击保护；接口失败时保留错误提示，不先清空当前场次。


### 连续移动修复与导航复核

手机仅用服务端已确认位置的短缓冲插值，不外推位置，不在客户端修改采集坐标。
楼层移动采用连续圆形扫掠碰撞，圆半径 0.15 m；速度上限 1.25 m/s，
速度响应时间常数 0.14 s。WebSocket 输入超过 0.5 s 未刷新会停止；后台/失焦也立即发送零输入。
这些控制参数与旧模型不同，新导出的汇总以 `movement_model: swept-disc-1` 标记，CSV 列不变。

`window.play3d.debug()` 只读显示模型加载次数、实际渲染 FPS、drawCalls、triangle 数、输入同步位置与相机位置，方便诊断。
`npm run test:e2e:movement` 使用独立临时场次，覆盖慢模型单次加载、手动开门、双向碰撞、松手/失焦停止、重新开始和导出。

学校场景的模型/导航审计见项目根目录 `docs/school-navigation-audit.md`。
当前 `navigation_review.status = needs-review`，页面明确提示仅演示，正式启动会被服务端拒绝。
**移动算法修复不等于旧的房间、门、楼梯与出口标注已校准**；需要按模型重新标注并做真实长度测量，不能只把比例状态改成 verified。
