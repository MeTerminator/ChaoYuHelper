# 开发与维护

## 项目结构

```text
main.py                    课程助手路由、启动参数与服务入口
app/
  api.py                   Bearer Token 认证的 REST API
  auth.py                  管理员初始化、登录、凭据修改及 HTTP / WebSocket 访问控制
  ui.py                    课程助手浏览器界面
  service.py               账号会话、课程缓存、设置与批量操作
  store.py                 SQLite + Fernet 加密存储
  activity.py              活动解析、参考答案、选人去重与课堂计时
  homework.py              官方作业列表、作答记录、签名表单与提交报文解析
  crypto.py                协议加密、签名和图片 CRC
  media.py                 PPT 图片格式识别
  providers/               学习通、雨课堂 HTTP / WebSocket 协议
  static/                  样式、环信 SDK 源码与打包产物
scripts/read_only_check.py 凭据从标准输入进入的临时会话只读检查
tests/                    协议测试、隔离的浏览器测试服务与用户流程测试
docs/                     使用、开发、API 与历史验证报告
data/                     本地账号数据、密钥和 Token（不纳入版本控制）
.nicegui/                 浏览器偏好等状态（不纳入版本控制）
```

项目已从旧子目录迁移至根目录。所有启动命令在根目录执行，源码通过文件位置解析静态资源和默认数据目录。

## 安装与测试

```bash
uv sync --locked --extra dev
uv run --locked --extra dev playwright install chromium
uv run --locked --extra dev ruff check app main.py scripts tests
uv run --locked --extra dev pytest -q
```

测试用模拟 HTTP 响应和隔离的浏览器服务，不使用真实凭据、不执行真实平台提交。浏览器测试的数据和 界面状态均进入临时目录；默认截图也在测试临时目录。需要保留截图时：

```bash
COURSE_HELPER_TEST_ARTIFACTS="$PWD/artifacts/screenshots" uv run --locked --extra dev pytest tests/test_ui_story.py -q
```

`artifacts/` 是可再生成目录，已排除版本控制。测试后可以删除 `.pytest_cache/`、`.ruff_cache/`、`__pycache__/`。`.venv/` 为当前根目录重新创建的运行环境；需要重建时删除后执行 `uv sync --locked`。不要移动或复制旧虚拟环境代替重建。

## 环信资源构建

运行时使用已提交的 `app/static/im.js`，不需要 Node。修改 `app/static/im-source.js` 后再执行：

```bash
npm ci
npm run build:im
```

保留 `package.json`、`package-lock.json` 和构建产物中的许可说明；`node_modules/` 可清理。锁定的第三方版本和许可说明见根目录文档。

## 真实账号联调

应用以正常模式运行，界面中手动点击会执行对应的平台操作。开发回归使用模拟 HTTP、SDK 和临时数据库；个人账号联调仍应按测试范围选择读取接口。

`scripts/read_only_check.py` 是独立的开发诊断脚本，自带读取白名单，不影响应用运行。它从标准输入接收一行 JSON 凭据，使用临时加密会话并输出脱敏计数；不会调用签到、入课堂、答题、上传或回复接口。凭据不得写入源码、命令行参数、共享日志或报告。

## 故障排查

| 情况 | 检查方式 |
| --- | --- |
| 服务未启动 | 执行 `uv run --locked python main.py --no-open` 查看错误；用 `curl http://127.0.0.1:8080/health` 检查健康状态 |
| 端口被占用 | 用 `--port 8088` 等其他端口启动，并打开对应地址 |
| 迁移后提示找不到 Python 或脚本 | 在根目录执行 `uv sync --locked`；不要继续使用旧子目录的解释器路径 |
| 课程空白或账号消失 | 核对平台、雨课堂服务器和账号，手动刷新课程；平台接口可能要求重新登录 |
| 新设备验证 / 验证码错误 | 在界面中由用户完成验证，或选择对应的短信 / 二维码登录；不要连续重试或绕过验证 |
| `UNAUTHENTICATED` | 核查服务器、会话是否过期，以及保存的 Web / App 登录方式是否与 `x-client` 匹配 |
| 解密失败 | 确认数据库与 `.key` 来自同一次备份；不要替换或重新生成现有密钥 |

## 后续扩展

将新增业务放在 `app/service.py` 或独立服务模块，协议请求集中在 `app/providers/`。界面和 REST API 共用服务层。后续定时任务、AI 和无人值守流程尚未实现，增加这些能力时须单独设计启用条件、账号隔离和操作记录。

## 作业适配约束

列表读取先访问官方课程入口，提取 `workEnc` / `enc` / `t`，按官方 `course-stu.js` 的字段请求作业列表。已提交记录直接请求 `view`，不能为了查看记录先请求 `task`，后者可能跳到作答页并启动计时。

`homework_open` 必须由用户点击“开始作答”触发。提交仅使用当前账号刚加载的表单签名和隐藏字段，不接受界面自造的签名。题目数量和类型不完整时禁止提交；成功后移除内存表单，防止再次点击同一份提交。草稿使用与账号、作业 ID 关联的加密记录，不会自动保存到平台。

作业列表、详情结构来自官方真实只读页面；作答表单和提交协议参考现有表单约定及[公开实现源码](https://greasyfork.org/fr-CA/scripts/583231/code)，采用独立实现。手动作答仅用模拟 HTML / HTTP 验证，没有打开用户的未交作业。已补充签名分页合并、图片 / 文件附件、排序 / 连线内部值映射和多编辑器字段的模拟验证；不要把模拟覆盖描述为真实作业提交成功。排序与连线字段另参考[题目控件解析源码](https://github.com/iPycc/Fk-Chaoxing-Extension/blob/main/modules/question/parse/dropdown.js)，采用独立 Python 实现。

IM 适配使用 `easemob-websdk@4.24.0` 源码中的 `connection.osType` 字段，在建立连接前设为原应用的 Linux 设备类型 `2`。SDK 默认类型 `16` 在原项目已记录可能被平台拒绝。测试替身验证生命周期和账号隔离，未建立真实 IM 会话。


### 作业分页与附件

上游分页只跟随页面实际提供的官方链接，不拼造签名或提交地址。合并后校验题目 ID 和总数；本地界面将作业列表按每页 10 项、题目和记录按每页 5 项显示，翻页不销毁答案控件。排序按内部选项值序列提交，连线保留左侧内部编号，不能把界面显示的字母直接当成内部 ID。

附件调用既有云盘上传流程，单文件上限 50 MB，图片需要通过格式校验。上传记录以账号、作业和题目关联并加密保存；客户端只能选择该记录中的 Object ID。提交时编码图片或平台附件 HTML，不执行官方页面脚本。草稿的 `_attachments` 记录选中的附件，`_keep_existing` 记录是否保留平台原有的图片或附件；默认保留，防止只改文字时意外删掉已有内容。

HTTP 请求与重定向均校验目标是否为支持的平台地址。
