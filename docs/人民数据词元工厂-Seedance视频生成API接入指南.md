# 人民数据词元工厂 · Seedance 视频生成 API 接入指南

更新日期：2026-10-13

适用对象：通过 HTTP API 调用视频生成服务的开发者。

## 1. 接入准备

按以下流程完成接入准备：

1. **获取账号和密码**：由平台提供登录账号及密码。
2. **登录平台**：访问 [人民数据词元工厂](https://token.peopleopen.cn)，使用平台提供的账号和密码登录。
3. **创建 API Key**：登录后自行开通并创建 API Key，保存好生成的密钥，用于后续 API 调用。
4. **确认可用能力**：确认账号已开通的模型、分辨率权限及可用额度；如有疑问，联系平台支持。

账号和密码用于登录网站；调用视频 API 时，使用自行创建的 API Key 进行 Bearer 鉴权。

| 项目 | 说明 |
| --- | --- |
| 服务地址 | `https://token.peopleopen.cn`；如开通通知提供了专用地址，以通知为准 |
| 示例模型 | `doubao-seedance-2.0`；实际使用平台为账号开通的模型名称 |
| 鉴权 | `Authorization: Bearer <API_KEY>` |
| 创建请求格式 | `Content-Type: application/json` |
| 处理方式 | 异步：创建任务 → 查询状态 → 下载视频 |

**创建请求返回 HTTP 200，只表示请求已得到成功响应，不代表视频已经生成完成。** 必须保存返回的 `id`，并查询至 `status` 为 `completed` 后下载。

API Key 应由服务端或密钥管理服务保管，不放在网页前端、公开仓库或日志中。

## 2. 接口一览

| 操作 | 方法 | 路径 |
| --- | --- | --- |
| 创建视频任务 | POST | `/v1/videos` |
| 查询任务状态 | GET | `/v1/videos/{id}` |
| 查询任务详情、下载地址与 Token 用量 | GET | `/v1/video/generations/{id}` |
| 通过文件流下载视频（备选） | GET | `/v1/videos/{id}/content` |

以上平台接口均需携带 API Key。`{id}` 使用创建响应返回的完整值，不自行构造或替换成其他系统的任务编号。查询和下载时，应使用创建任务时的接入地址及有权访问该任务的账号密钥。

**推荐先获取下载地址，再通过该地址下载或分享视频**：使用 `/v1/video/generations/{id}`，读取 `data.result_url`，见第 3.4 节；同一响应中的 Token 用量读取 `data.data.usage`，见第 3.5 节。需要通过平台鉴权接口直接获取文件时，可使用第 3.6 节的文件流下载方式。任务详情接口与 `/v1/videos/{id}` 的响应结构不同，不能共用同一套字段路径。

## 3. 快速开始

以下命令在 Bash 中执行，需要安装 `curl` 和 `jq`。示例在当前目录保存响应和视频，请使用独立的工作目录。

### 3.1 设置接入信息

先按第 1 节登录平台并创建 API Key，再设置以下接入信息。服务地址填写域名根地址，不在末尾重复添加 `/v1`。

```bash
export PEOPLEOPEN_BASE_URL="https://token.peopleopen.cn"
read -r -s -p "请输入 API Key：" PEOPLEOPEN_API_KEY
printf '\n'
export PEOPLEOPEN_API_KEY
```

已有密钥管理或环境变量注入机制时，可直接使用该机制，无需交互输入。

### 3.2 创建视频任务

下面示例请求生成一段 5 秒、720p、带音频、不带水印的视频。请将 `prompt` 改为实际需求。

```bash
if ! curl --fail-with-body -sS \
  --connect-timeout 10 --max-time 120 \
  "${PEOPLEOPEN_BASE_URL%/}/v1/videos" \
  -H "Authorization: Bearer ${PEOPLEOPEN_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "doubao-seedance-2.0",
    "prompt": "一只小奶牛在草原上散步，镜头平稳，画面自然",
    "seconds": 5,
    "resolution": "720p",
    "ratio": "16:9",
    "generate_audio": true,
    "watermark": false
  }' \
  -o create-response.json; then
  cat create-response.json
  echo "创建请求失败或结果不确定；请先核实，不要自动重复提交。" >&2
  exit 1
fi

if ! VIDEO_ID=$(jq -er '.id | strings | select(length > 0)' create-response.json); then
  echo "未取得有效任务 ID，请保留响应并联系平台支持。" >&2
  exit 1
fi

VIDEO_ID_PATH=$(printf '%s' "$VIDEO_ID" | jq -sRr @uri)
printf '任务 ID：%s\n' "$VIDEO_ID"
```

创建响应示意如下。ID、时间和初始状态以实际响应为准：

```json
{
  "id": "task_example",
  "object": "video",
  "model": "doubao-seedance-2.0",
  "status": "queued",
  "progress": 0,
  "created_at": 1791792139
}
```

**画面比例说明：当前接入验证中出现过请求 `16:9`、实际返回竖屏 `9:16` 的情况，尚未确认原因。对横竖屏有严格要求的业务，请先进行小样验证并确认。** 分辨率与画面比例是两个不同的参数。

### 3.3 查询任务状态

沿用上一步获得的 `VIDEO_ID_PATH`：

```bash
if ! curl --fail-with-body -sS \
  --connect-timeout 10 --max-time 60 \
  "${PEOPLEOPEN_BASE_URL%/}/v1/videos/${VIDEO_ID_PATH}" \
  -H "Authorization: Bearer ${PEOPLEOPEN_API_KEY}" \
  -o status-response.json; then
  cat status-response.json
  echo "本次查询失败；保留原任务 ID，稍后查询，不要重新创建任务。" >&2
  exit 1
fi

jq '{id, status, progress, error}' status-response.json
```

如果在另一个终端继续查询，先重新设置接入信息，再恢复任务 ID：

```bash
VIDEO_ID="替换为已保存的完整任务ID"
VIDEO_ID_PATH=$(printf '%s' "$VIDEO_ID" | jq -sRr @uri)
```

| `status` | 含义 | 建议处理 |
| --- | --- | --- |
| `queued` | 已提交或排队中 | 等待后继续查询 |
| `in_progress` | 生成中 | 等待后继续查询 |
| `completed` | 生成完成 | 下载视频 |
| `failed` | 任务失败 | 读取 `error.message`，停止轮询 |
| `unknown` | 当前无法识别任务状态 | 保存响应并联系平台支持 |

完成响应示例：

```json
{
  "id": "task_example",
  "object": "video",
  "model": "doubao-seedance-2.0",
  "status": "completed",
  "progress": 100,
  "created_at": 1791792139,
  "completed_at": 1791792221
}
```

建议从每 5 秒查询一次开始，等待较久时适当拉长间隔，并设置业务等待上限，例如 30 分钟。该上限是客户端策略，不是服务完成时间承诺。视频内容时长不等于生成耗时。

HTTP 429 表示请求频率受限，应延长查询间隔。客户端超时或停止轮询，不代表远端任务已经取消。

### 3.4 获取可分享的下载地址（推荐）

**优先推荐此方式**：一次查询即可取得下载地址和 Token 用量，便于下载、分享或交给其他系统处理。沿用第 3.2 节保存的任务 ID 和第 3.1 节设置的接入信息：

```text
GET /v1/video/generations/{id}
Authorization: Bearer <API_KEY>
```

```bash
if ! curl --fail-with-body -sS \
  --connect-timeout 10 --max-time 60 \
  "${PEOPLEOPEN_BASE_URL%/}/v1/video/generations/${VIDEO_ID_PATH}" \
  -H "Authorization: Bearer ${PEOPLEOPEN_API_KEY}" \
  -o detail-response.json; then
  cat detail-response.json
  echo "任务详情查询失败；请核对接入信息并保留原任务 ID。" >&2
  exit 1
fi

if ! VIDEO_URL=$(jq -er '
  select(.code == "success" and .data.status == "SUCCESS")
  | .data.result_url | strings | select(length > 0)
' detail-response.json); then
  jq '{code, message, status: .data.status, fail_reason: .data.fail_reason}' detail-response.json
  echo "尚未取得成功任务的下载地址，请检查状态或错误信息。" >&2
  exit 1
fi

printf '%s\n' "$VIDEO_URL"
```

成功响应示例（仅列相关字段；任务 ID 和下载地址为占位值）：

```json
{
  "code": "success",
  "message": "",
  "data": {
    "task_id": "task_example",
    "status": "SUCCESS",
    "fail_reason": "",
    "result_url": "https://example.com/video.mp4?signature=example",
    "data": {
      "model": "doubao-seedance-2.0",
      "status": "completed",
      "usage": {
        "completion_tokens": 50638,
        "total_tokens": 50638
      }
    }
  }
}
```

- 此接口先检查顶层 `code == "success"`，再检查任务状态 `data.status == "SUCCESS"`。这与 `/v1/videos/{id}` 的顶层 `status == "completed"` 不同。
- `data.result_url` 是下载地址，可能含临时签名参数。复制时保留整个 URL 及查询参数，不截断、不自行拼接。JSON 中的 `\u0026` 经 JSON 解析后为 `&`；上面的 `jq -r` 会输出可直接使用的地址。
- 实测返回的签名 URL 可用于直接下载，无需附带平台 API Key；不要向下载站点转发平台密钥。
- 签名链接有有效期。本次返回了 `X-Tos-Date` 和 `X-Tos-Expires=86400`，表示从签名时间起 24 小时，不能保证其他响应也采用同样期限。过期后可重新查询；若返回的链接仍已过期，联系平台支持，或使用第 3.6 节的下载接口。
- 分享下载地址时无需分享 API Key；含签名的链接本身应只交给需要访问视频的接收方。

### 3.5 获取消耗的 Token 数

**2026-10-13 对已完成的 `doubao-seedance-2.0` 任务实测，Token 用量由 `GET /v1/video/generations/{id}` 返回，字段位于 `data.data.usage`（两层 `data`）。** 同一任务的 `GET /v1/videos/{id}` 仅返回状态等信息，未返回 `usage`。`/v1/videos/{id}/content` 用于下载文件，不用于读取 Token 用量。

沿用第 3.4 节保存的响应，无需再次发送请求：

```bash
jq '.data.data.usage // null' detail-response.json

# 仅提取总 Token 数；未返回该字段时输出 null
jq '.data.data.usage.total_tokens // null' detail-response.json
```

| 字段路径 | 含义 | 本次实测值 |
| --- | --- | --- |
| `data.data.usage.completion_tokens` | 服务端上报的生成 Token 数 | `50638` |
| `data.data.usage.total_tokens` | 服务端上报的总 Token 数 | `50638` |
| `data.quota` | 平台额度字段，不是 Token 数 | 不用于统计 Token |

本次任务上报总用量为 **50,638 Token**。`total_tokens` 已是总数，不要再与 `completion_tokens` 相加。响应未返回 `prompt_tokens`，不能据此认定输入 Token 为零。

以上字段和值反映本次接入验证，不代表所有模型、渠道或任务阶段均会返回相同字段。应在任务成功后读取用量；若 `usage` 或某项计数缺失，记录为“未上报”，不要补零。视频 Token 按服务端上报口径记录，不按提示词字数推算；实际费用以平台账单和适用计价规则为准，不能直接将 `quota` 当作 Token 数或金额。

### 3.6 通过文件流下载视频（备选）

需要通过平台鉴权接口直接获取文件时，可使用本节方式。沿用第 3.3 节的 `status-response.json`，确认 `status` 为 `completed` 后下载：

```bash
if ! jq -e '.status == "completed"' status-response.json >/dev/null; then
  echo "任务尚未确认完成，请继续查询或检查错误。" >&2
  exit 1
fi

curl --fail -sS -L \
  --connect-timeout 10 --max-time 300 \
  "${PEOPLEOPEN_BASE_URL%/}/v1/videos/${VIDEO_ID_PATH}/content" \
  -H "Authorization: Bearer ${PEOPLEOPEN_API_KEY}" \
  -o video.mp4.part \
  && mv video.mp4.part video.mp4
```

下载成功后，当前目录得到 `video.mp4`。下载失败时保留的 `.part` 文件可能不完整，不应作为完整视频使用。

- 下载接口可以直接返回 `Content-Type: video/mp4` 的二进制流，**不要求查询响应包含独立下载 URL**。
- `-L` 用于处理下载重定向。不要添加 `--location-trusted`，避免向其他域名转发鉴权信息。
- 已取得任务 ID 时，后续只需查询和下载，不必重新创建视频。
- 实测文件包含 H.264 或 HEVC/H.265 视频编码，以及 AAC 音轨。不同模型或分辨率的输出编码可能不同；播放异常时请检查播放器是否支持对应编码。

如果安装了 FFmpeg，可检查实际文件参数：

```bash
ffprobe -v error \
  -show_entries format=duration,size:stream=codec_type,codec_name,width,height \
  -of json video.mp4
```

## 4. 创建请求参数

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `model` | string | 必填，使用账号已开通的模型名称；本文示例为 `doubao-seedance-2.0` |
| `prompt` | string | 纯文本提示词；简单场景推荐使用 |
| `content` | array | 文本及参考素材数组；与 `prompt` 选择一种写法，至少提供非空文本或参考素材 |
| `seconds` | number / string | 视频时长；建议显式填写，本文示例为 `5` |
| `duration` | number | 时长的另一种写法；与 `seconds` 选择一种，不与 `frames` 同时使用 |
| `resolution` | string | 分辨率档位，例如 `720p`、`1080p`；与 `size` 选择一种 |
| `size` | string | 像素尺寸写法，例如 `1280x720`；用于选择分辨率档位，不保证最终文件恰好采用该宽高 |
| `ratio` | string | 请求的画面比例，例如 `16:9`；当前存在实际输出比例不一致的已知情况，见第 3.2 节 |
| `generate_audio` | boolean | 是否请求音频；传 `true` 或 `false`，不要传字符串 `"true"` / `"false"` |
| `watermark` | boolean | 是否请求添加水印；传 JSON 布尔值 |

### 分辨率选择

| 档位 | 接入说明 |
| --- | --- |
| `720p` | 已完成生成和下载验证，可作为首次接入的验证档位 |
| `1080p` | 已完成生成和下载验证；使用前确认账号和模型已开通对应能力 |
| `4k` | 本轮未完成验证，不作为已确认支持的能力；需要时先向平台确认 |

上述验证范围针对本次使用的 `doubao-seedance-2.5` 接入环境，不能直接视为示例模型 `doubao-seedance-2.0` 的能力承诺，也不代表所有账号或服务地址均具备相同能力。时长、分辨率、参考素材限制以实际开通能力和接口返回为准。

仅当平台确认所用模型和账号支持 1080p 后，才将请求中的 `"resolution": "720p"` 改为 `"resolution": "1080p"`，无需同时传 `size`。最终宽高、横竖屏及编码应以下载文件为准。

## 5. 使用参考图片或视频

需要参考素材时，将纯文本 `prompt` 改为 `content` 数组。以下为请求结构示例，素材地址必须替换为服务端能够访问的真实 HTTP(S) URL：

```json
{
  "model": "doubao-seedance-2.0",
  "content": [
    {
      "type": "text",
      "text": "结合参考图片中的主体和参考视频的动作生成短视频"
    },
    {
      "type": "image_url",
      "image_url": {
        "url": "https://example.com/reference-image.jpg"
      },
      "role": "reference_image"
    },
    {
      "type": "video_url",
      "video_url": {
        "url": "https://example.com/reference-video.mp4"
      },
      "role": "reference_video"
    }
  ],
  "duration": 5,
  "resolution": "720p",
  "ratio": "16:9",
  "generate_audio": true,
  "watermark": false
}
```

请求仍发送到 `POST /v1/videos`，使用 `application/json`。不通过 multipart 直接上传本地素材文件。

`role` 与 `type` 位于同一层级。请检查素材 URL 是否可访问、是否过期，以及是否符合已开通模型的素材要求。本轮实测覆盖纯文本生成；参考素材示例未做线上生成验证。

## 6. 常见错误与处理

先检查 HTTP 状态码，再读取响应正文。错误可能位于 `error.code` / `error.message`，也可能位于顶层 `code` / `message`。

| 现象或错误码 | 调用方处理建议 |
| --- | --- |
| HTTP 401 / `Invalid token` | 检查 API Key、Bearer 格式和接入地址是否匹配；仍失败时联系平台确认令牌状态 |
| HTTP 403 | 联系平台确认账号的模型访问权限 |
| `model_not_found` | 核对模型名称和接入地址；名称无误时联系平台确认模型是否已开通、当前是否可用 |
| `model_price_error` | 当前模型的计费配置不可用，请联系平台处理；重复提交相同请求不会解决此问题 |
| `plugin_request_invalid` | 请求参数不满足当前服务要求，按错误信息调整时长、分辨率或其他字段；不要只看 HTTP 状态码 |
| HTTP 429 | 降低调用频率；已有任务优先保留 ID，延长间隔后继续查询 |
| HTTP 5xx / `convert_to_openai_video_failed` | 保存响应并联系平台排查；查询出错不等同于任务生成失败，不要因此重新创建任务 |
| 任务状态 `failed` | 查看 `error.message`，例如素材处理失败；修正原因后再决定是否重新提交 |
| `task_not_exist` / `Task not found` | 核对任务 ID、接入地址及当前密钥所属账号；查询不到不代表应重新创建任务 |
| 任务已完成，但状态接口没有下载 URL | 优先调用 `/v1/video/generations/{id}` 读取 `data.result_url`，见第 3.4 节；也可通过第 3.6 节的 `/v1/videos/{id}/content` 获取文件流 |
| 状态接口没有 Token 用量 | 查询 `/v1/video/generations/{id}` 的 `data.data.usage`；仍缺失时记录“未上报”，不要将 `quota` 当作 Token 数 |
| 下载中断或文件无法播放 | 检查 HTTP 状态、文件是否完整、播放器编码支持；可重试下载，勿重新创建任务 |

创建请求如果因网络超时或断开而没有拿到响应，服务端可能已经接受任务。**不要自动重复 POST**：先检查已有响应或调用记录，必要时联系平台确认，避免创建多条可能计费的任务。

## 7. 联系平台支持时提供的信息

请提供：

- 请求时间及所用时区。
- 接口方法、路径、模型名称。
- 不含密钥和敏感素材信息的请求参数。
- HTTP 状态码、错误码和错误消息。
- 响应中的 request ID 或已取得的任务 ID。
- 如涉及输出问题，提供实际分辨率、横竖屏、编码及必要的文件信息。

不要提供登录密码、API Key、完整 Authorization 请求头，或不必要的带签名素材链接。
