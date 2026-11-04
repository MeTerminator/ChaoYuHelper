# REST API

服务启动后查看 [Swagger UI](http://127.0.0.1:8080/docs) 或 `/openapi.json`。界面通过 HTTP / WebSocket 通信使用服务层；REST API 是另外提供的服务端扩展入口，并未覆盖界面的每项交互。

## 认证

`GET /health` 无需认证。`/api/*` 请求需要：

```text
Authorization: Bearer <data/api-token 中的值>
```

Token 文件在首次启动时生成，权限为 `0600`。不要将 Token 放入 URL、源码或共享日志。API 返回的账号资料不含 Cookie、IM 专用凭据和设备签名参数；姓名、电话等账号资料仍需按私有信息处理。

以下根目录 Python 示例只读取账号列表和缓存课程，不打印认证信息：

```python
from pathlib import Path
import httpx

token = Path('data/api-token').read_text().strip()
with httpx.Client(
    base_url='http://127.0.0.1:8080',
    headers={'Authorization': f'Bearer {token}'},
) as client:
    accounts = client.get('/api/accounts').raise_for_status().json()
    if accounts:
        account_id = accounts[0]['id']
        response = client.get(f'/api/accounts/{account_id}/courses')
        response.raise_for_status()
        print('缓存课程数量：', len(response.json()))
```

## 读取与本地设置

| 方法 | 路径 | 参数与返回 |
| --- | --- | --- |
| GET | `/health` | 返回 `status`、应用标识 |
| GET | `/api/accounts` | 返回已保存账号的公开资料字段 |
| GET | `/api/accounts/{account_id}/courses` | 默认 `refresh=false` 读取本地缓存；`refresh=true` 请求平台并更新缓存 |
| POST | `/api/activities` | 学习通活动列表；请求 `account_id` 和课程信息 |
| POST | `/api/homework` | 当前学习通账号的作业列表；参数同课程请求 |
| POST | `/api/homework/review` | 已提交作业详情；课程请求另加 `work` 对象（列表返回值） |
| POST | `/api/settings/read` | 读取本地课程设置；请求 `account_id` 和课程信息 |
| PUT | `/api/settings` | 保存本地课程设置；另含 `settings` 对象，不会提交签到 |

读取活动 / 设置时，请求结构如下，ID 使用读取接口实际返回的值：

```json
{
  "account_id": "<账号 id>",
  "course": {
    "courseId": "<课程 id>",
    "classId": "<班级 id>",
    "cpi": "<学习通 cpi，活动读取需要>"
  }
}
```

本地 `settings` 可包含：

```json
{
  "location": {
    "classroom": "教室（可选）",
    "address": "地址",
    "latitude": 0,
    "longitude": 0
  },
  "imageObjectIds": []
}
```

雨课堂活动依赖进入课堂后取得的 WebSocket / PPT 数据；没有提供统一的 `/api/activities` 返回格式。

## 手动操作接口

`POST /api/actions/{action}` 会对指定账号执行业务操作。真实账号只读测试不调用这些接口。

`POST /api/homework/open` 接受课程请求另加 `work` 对象，显式进入作业并加载当前账号的签名表单，可能启动平台计时，不是只读接口。

`POST /api/homework/attachments` 使用 `multipart/form-data`，字段为 `account_id`、`work_id`、`question_id` 和 `file`。必须先加载该账号的作业表单，且目标是可上传附件的主观题。单文件上限 50 MB，返回 `object_id`、`name` 和 `image`。它是上传操作接口。

作业 `answers` 的普通键为题目 ID，选择题值为选项值，多选、填空、排序、连线和多编辑器题使用数组，文字题使用字符串。可额外包含 `_attachments: {"题目ID": ["已上传的object_id"]}`，以及 `_keep_existing: {"题目ID": false}`；未指定时保留平台已有图片和附件。签名、隐藏表单字段和附件元数据由后端校验，不接受客户端自造的表单签名。

通用请求字段：

| 字段 | 含义 |
| --- | --- |
| `account_id` | 来源账号；目标账号必须与其属于同一平台、同一服务器 |
| `accounts` | 至少一个操作账号 id |
| `course` | 课程上下文；学习通操作需要 `courseId`、`classId`，雨课堂需要 `lessonId` |
| `active` | 活动上下文；学习通签到、评分需要 `id`，题目提交还需要 `type` |
| `values` | 下表所列的操作参数 |

| action | 平台 | values |
| --- | --- | --- |
| `sign` | 学习通 | `kind`：`0` 普通、`2` 二维码、`3` 手势、`4` 位置、`5` 签到码；其他参数对应 `Chaoxing.sign`，如 `code`、`enc`、`object_id`、`address`、经纬度与验证码参数 |
| `questions` | 学习通 | `questions` 题目数组、`answers` 按题目 ID 索引的答案对象；活动 `type` 为 `14` 问卷、`42` 练习或 `43` 投票 |
| `evaluate` | 学习通 | `score`、可选 `content`、`scores` 分项分数数组 |
| `reply` | 学习通 | `topic_uuid`、`content`、可选 `anonymous` |
| `homework` | 学习通 | `work_id`、`answers`；先调用 `/api/homework/open`，目标账号必须为当前账号 |
| `rain-checkin` | 雨课堂 | 使用 `course.lessonId` 入课堂，可能产生签到效果 |
| `rain-answer` | 雨课堂 | `problem_id`、`problem_type`，以及 `options`、`content`、`image_urls`、`retry`、`time` 等；需要时先取得对应课堂授权 |

批量操作返回逐账号结果数组：

```json
[
  {"account": "账号显示名称", "ok": true, "result": {"result": 1}},
  {"account": "其他账号", "ok": false, "error": "平台返回的错误"}
]
```

HTTP 200 只表示批量请求已处理，**每个账号是否成功必须检查 `ok`**。不同平台的 `result` 结构不同。

## 错误处理

- 未认证 / 无效 Token：HTTP 401 或 403。
- 缺少必要字段、混用平台 / 服务器、类型不符：HTTP 422。
- 不存在的操作：HTTP 404。
- 直接读取时的平台错误、账号不存在等：HTTP 400，返回 `detail`。
- 批量操作中的平台错误：通常仍返回 HTTP 200，在对应账号结果中标记 `ok: false`。
