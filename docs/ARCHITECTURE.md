# 架构与模块职责

项目按入口、业务编排、平台协议、纯解析和基础设施划分。模块以一项可说明的职责为单位；不按行数机械拆分，也不为每个函数建立文件。

## 依赖方向

```text
main.py                         组装应用、注册服务生命周期
  ├─ app/api.py                 REST 路由、参数及入口校验
  ├─ app/auth.py                管理员身份、会话与访问控制
  └─ app/ui/routes.py           NiceGUI 页面路由
       └─ app/ui/dashboard.py   页面状态、业务视图组合、导航与清理

入口 → service / im_service / auto_sign → providers → HTTP / WebSocket
界面 / providers → activity / homework / im → 纯解析与数据归一化
service / providers / 媒体服务 → store / crypto / media
```

界面与 API 不直接构造平台 HTTP 请求。平台协议和解析模块不依赖界面或应用路由。`ProviderError` 是共享的平台错误类型，定义在 `providers/base.py`；纯解析模块可以使用它，但不创建客户端或访问数据库。

`auto_sign.py` 接收 `im_service` 分发的学习通实时课程签到消息，负责随机延迟、重读设置、账号锁、
持久化去重及自动提交记录；历史缓存与预取入口不调度自动签到。`sign_code.py` 只负责日期模板校验与替换。
自动签到任务独立于浏览器页面，由 `Service` 管理生命周期；手动签到经 `Service.submit_sign` 与自动任务协调，防止重复提交。

## 浏览器界面：`app/ui/`

| 模块 | 职责 |
| --- | --- |
| `__init__.py` | 兼容原 `app.ui` 的导入入口；导入时注册页面路由 |
| `routes.py` | 首页、作业列表、作答记录与作答路由，校验页面上下文 |
| `dashboard.py` | 组合业务视图；初始化当前页面状态，管理导航、账号切换和任务清理 |
| `components.py` / `constants.py` | 通用输入、按钮、富文本、异常提示与展示标签 |
| `accounts.py` | 账号管理、登录与登录验证 |
| `courses.py` | 课程列表、筛选、课程详情与活动分发 |
| `settings.py` | 管理员表单、课程设置与手动批量操作界面 |
| `homework.py` | 作业列表、记录、分页和手动作答控件 |
| `sign.py` / `capture.py` | 签到表单和滑块验证；摄像头与二维码识别 |
| `activities.py` | 题目、评分、讨论和选人活动 |
| `classroom.py` / `presentation.py` | 雨课堂事件、时间线与答题；课件加载、翻页和下载 |
| `im_conversations.py` | 会话连接、联系人、会话加载、草稿与手动发送 |
| `im_events.py` | 事件轮询、历史分页、控制事件与本地消息状态更新 |
| `im_rendering.py` | 会话列表、头像、消息及回执展示 |
| `history.py` | 本地操作记录展示 |

业务视图是由 `Dashboard` 组合的能力类（mixin），不单独实例化，不导入 `Dashboard`，也不互相继承。它们共享同一客户端的页面状态，通过 `self` 调用导航、服务访问和其他视图能力。`Dashboard` 负责状态与资源生命周期，能力类不增加自己的构造函数，避免初始化顺序和任务清理分散。公共控件不反向依赖业务视图。

这种组合保留了原有回调和客户端状态语义。拆成多个文件并不代表视图状态已完全解耦：作业、课堂和消息视图仍通过 Dashboard 协作。新增可复用逻辑应优先抽成参数明确的函数或服务；只有与当前页面控件、状态紧密相关的逻辑才放进视图能力类。

## 学习通协议：`app/providers/chaoxing/`

| 模块 | 职责 |
| --- | --- |
| `client.py` | 组合 `Chaoxing` 客户端，初始化设备和 HTTP 请求头 |
| `auth.py` | 密码、短信、二维码登录与账号资料 |
| `messaging.py` | IM 凭据及用户、群聊资料接口 |
| `courses.py` | 课程与活动发现 |
| `homework.py` | 作业读取、账号绑定的表单、附件与手动提交 |
| `sign.py` | 签到详情、位置报文、人脸校验与签到提交 |
| `activities.py` | 答题、投票、选人、评分与讨论协议 |
| `captcha.py` / `files.py` | 滑块验证；云盘上传与文件去重 |
| `common.py` | 平台地址、IM 请求头及 HTML / 内嵌 JSON 提取 |

`Chaoxing` 最终继承公共 `Provider`，所有能力共用同一账号、Cookie、HTTP 客户端与 Store。能力模块只处理自己的协议，不创建独立会话，不导入 `Service` 或界面。新增方法须避免与其他能力或基类重名；需要复用的协议由当前客户端方法调用，纯函数放在公共或领域模块。

## 作业解析：`app/homework/`

| 模块 | 职责 |
| --- | --- |
| `constants.py` | 官方域名和允许的页面路径 |
| `pages.py` | URL 与分页边界校验、页面权限识别、列表和作答记录解析 |
| `forms.py` | 签名表单解析、题目完整性与分页合并 |
| `controls.py` | 连线 / 排序控件映射和已有答案读取 |
| `attachments.py` | 附件 HTML 编码、平台已有媒体提取 |
| `submission.py` | 答案校验及提交地址、字段编码 |

该包只处理输入输出，不执行页面脚本或网络请求。依赖从表单到控件、从控件和提交编码到附件，不反向引用包的聚合入口。`__init__.py` 显式导出原来的函数与常量，其他调用方仍可使用 `from app.homework import parse_form, submission`。

## 扩展和验证

- 优先扩展对应业务模块；新业务增加独立模块，不继续堆进组合入口或公共工具文件。
- `service.py` 保持账号、课程缓存、设置与批量编排职责；消息连接与媒体缓存由现有独立服务管理。
- 新的平台请求放进对应 provider；需要多入口复用的业务规则放进服务或领域函数。
- 调整文件位置时检查基于 `__file__` 的静态资源路径，并保持页面路由和公开导入入口兼容。
- 重构验证使用静态检查、公开入口导入检查、模拟协议和临时数据库；页面验证覆盖登录、导航和异步回调。真实账号操作遵守 [开发约束](DEVELOPMENT.md)。
