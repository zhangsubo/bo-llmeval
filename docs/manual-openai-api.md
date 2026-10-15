# OpenAI 兼容接口独立测试脚本

在 `bo-llmeval` 项目根目录运行 `python3 scripts/test-openai-api.py`，选择常用配置或临时输入 base URL、key，
再选择接口类型并输入 model ID 和提示词。Python 3.11+，无需安装依赖，临时输入 key 时隐藏回显。
菜单：1 为 `chat/completions`，2 为 `responses`，3 为 `video`（回车默认视频），4 为 `seedance`（火山方舟原生）。
选择 video 后会先显示分辨率菜单：1 为 720p（回车默认）、2 为 1080p、3 为 4K；
也可直接输入档位名称。选择以 `resolution` 字段发送，不同时发送 `size`。
`--resolution` 或 `--size` 已指定时跳过菜单，`--task-id` 查询和文本接口也不显示此菜单。
video 新建测试还会显示参考素材菜单：1 为不使用（回车默认），2 为视频，3 为图片，4 为音频。
可多选，用逗号分隔，例如 `3,4` 表示图片和音频；随后分别输入素材地址，再输入 model ID 和提示词。
三种素材均支持 HTTP(S) URL 或 `asset://asset-<素材ID>`，请按素材实际类型选择。
HTTP(S) 地址需能被上游访问；asset 地址引用上游已有素材，不是本地文件路径。
输入无效选项或 URL 会重新询问，尚未发送生成请求。
已指定任一 `--reference-video`、`--reference-image`、`--reference-audio` 参数，或 `--no-reference` 时跳过素材菜单。
兼容旧参数 `--no-reference-video`：跳过菜单且不使用参考视频，仍可显式传入图片或音频。
文本接口和 `--task-id` 查询也不会询问参考素材。
本工具从 peopleopen 迁入，用于单次手工诊断；测试纳入本项目 `tests/`。
它不写入平台任务/连接/数据库，不取代 EvalScope 的正式评测流程。
默认读取脚本同目录的 `openai-api-config`，即 `scripts/openai-api-config`。
路径相对于脚本位置解析，不受运行时工作目录影响；`--config` 显式指定的文件优先，不合并多个文件。
字段使用 `NAME编号`、`BASE_URL编号`、`API_KEY编号`，四种接口共用配置结构；不同服务需各自配置正确的地址和 key。

也可直接指定接口类型，跳过接口菜单：

```bash
python3 scripts/test-openai-api.py --api-type chat/completions
python3 scripts/test-openai-api.py --api-type responses
python3 scripts/test-openai-api.py --api-type video
python3 scripts/test-openai-api.py --api-type responses --no-stream
```

| 类型 | 请求路径 | 请求正文 | 结果 |
| --- | --- | --- | --- |
| chat/completions | POST /v1/chat/completions | JSON：model、messages、stream=true、stream_options.include_usage=true | SSE 增量、文本、usage、TTFT 和耗时 |
| responses | POST /v1/responses | JSON：model、input、stream=true | SSE 增量、文本、usage、TTFT 和耗时 |
| video | POST /v1/videos | JSON：model、prompt/content、seconds/duration、size/resolution、ratio、generate_audio、watermark | 任务轮询、完成耗时、下载地址、Token 用量、自动下载视频 |
| seedance | POST/GET/DELETE /api/v3/contents/generations/tasks[/id] | 方舟原生 JSON：model、content、duration、resolution 等 | 创建、查询、列表、取消/删除；轮询、用量、视频下载 |

## 火山方舟 Seedance 原生接口

选择菜单 `4` 后可选择创建、查询、列表或取消/删除。直接指定 `--api-type seedance` 时，
默认创建；带 `--task-id` 则默认查询，也可用 `--action` 显式选择。
原有 `video` 类型继续使用中转平台协议，单独使用 `--task-id` 仍选择 `video`；方舟任务必须显式选择 `seedance`。

使用原有配置文件的独立一组配置，填写方舟 API key，以及公开 Base URL
`https://ark.cn-beijing.volces.com/api/v3`。也接受该官方域名根地址和完整的
`/api/v3/contents/generations/tasks` 地址；自定义网关地址需保留正确的版本前缀。
配置格式仍是 `NAME编号`、`BASE_URL编号`、`API_KEY编号`；环境变量仍使用
`OPENAI_BASE_URL`、`OPENAI_API_KEY`、`OPENAI_MODEL_ID`，不自动读取 `ARK_API_KEY`。
模型 ID / 推理接入点 ID 由用户输入，不预设某个 Seedance 版本。

```bash
# 创建并轮询，成功后自动下载；Model ID 按提示输入或通过 OPENAI_MODEL_ID 提供
python3 scripts/test-openai-api.py --api-type seedance --prompt '小猫在花园里散步' --resolution 720p --no-reference
# 只创建并保存任务 ID，不等待完成
python3 scripts/test-openai-api.py --api-type seedance --prompt '小猫在花园里散步' --resolution 720p --no-reference --no-wait
# 查询已有任务并等待完成；不需要 model、提示词或素材输入
python3 scripts/test-openai-api.py --api-type seedance --task-id 'cgt-你的任务ID'
# 单次查询；尚未完成时立即退出，已成功时仍下载视频
python3 scripts/test-openai-api.py --api-type seedance --task-id 'cgt-你的任务ID' --no-wait
# 查询一页任务，可按状态、模型/推理接入点和多个任务 ID 筛选
python3 scripts/test-openai-api.py --api-type seedance --action list --page-num 1 --page-size 10 --filter-status succeeded
python3 scripts/test-openai-api.py --api-type seedance --action list --filter-model 'ep-你的接入点ID' --filter-task-id 'cgt-任务一' --filter-task-id 'cgt-任务二'
# 明确发起取消/删除，立即发送 DELETE；执行前确认任务 ID
python3 scripts/test-openai-api.py --api-type seedance --action delete --task-id 'cgt-你的任务ID'
# 首尾帧输入；URL 可替换为可用的 asset://asset-... 素材标识
python3 scripts/test-openai-api.py --api-type seedance --first-frame 'https://example.com/first.jpg' --last-frame 'https://example.com/last.jpg'
```

创建使用 `content` 数组；`--seconds` 和 `--duration` 都转成整数 `duration`，默认 5 秒。
本脚本目前只接受正整数秒；具体时长和分辨率是否支持由所选模型校验。
用 `--resolution` 指定分辨率，方舟模式拒绝 `--size`。默认比例 16:9、请求音频、关闭水印，
可用既有布尔参数调整。`--reference-image/video/audio` 沿用原有 HTTP(S)/asset 素材输入及角色；
`--first-frame`/`--last-frame` 使用对应的图片角色，尾帧必须同时提供首帧，且不能与参考素材混用。
新建仍需提示词；脚本不自动降档、不重新创建、不重试请求。
协议字段依据[官方创建文档](https://docs.volcengine.com/docs/ark/create-video-generation-task-api?lang=zh)。

查询使用响应顶层 `status`、`usage` 和 `content.video_url`。创建仅返回 `id` 也会继续查询；
`queued`/`running` 继续轮询，失败/取消/过期停止并保留响应。
下载链接请求不携带 API key；成功却缺少链接时标记 `completed_download_failed`，不会探测兼容接口的 `/videos/{id}/content`。
参见[官方查询文档](https://docs.volcengine.com/docs/ark/get-video-generation-task-api?lang=zh)。

列表仅查询指定页，显示总数及任务 ID、状态、模型，完整响应保存到报告，不批量下载。
分页和筛选参数仅用于 `--action list`，多个任务 ID 按重复的 `filter.task_ids` 参数发送。
参见[官方列表文档](https://docs.volcengine.com/docs/ark/list-video-generation-tasks-api?lang=zh)。

`delete` 共用官方取消/删除接口：排队任务取消，已成功/失败/过期任务删除记录，运行中任务不支持取消。
成功响应通常为 HTTP 200 `{}`，脚本也兼容成功的空响应；HTTP/业务错误保存后退出，不自动重试。
参见[官方取消/删除文档](https://docs.volcengine.com/docs/ark/cancel-or-delete-video-generation-tasks-api)。

方舟结果默认保存到 `scripts/test-results/seedance/`。退出码 0 表示本次操作成功：
`submitted` 仅代表创建已受理；单次查询的 `queued`/`running` 不代表视频完成。
退出码 1 表示失败/超时，2 表示生成成功但下载失败，130 表示中断。
本功能通过本地模拟 HTTP 测试，尚未执行方舟线上付费生成验收。

## 原有文本与兼容视频行为

文本和视频新建测试时均需输入提示词，不使用默认内容；输入不能为空。
也可通过 `--prompt "你的提示词"` 直接指定，跳过提示词输入。
文本接口默认流式，`--stream` 可显式开启，`--no-stream` 切换非流式。video 不支持 `--stream`。
Chat Completions 等待 `[DONE]` 且已有 finish_reason；Responses 等待
`response.completed` 或 `response.incomplete`，最终响应中的 usage 原样保存；上游未返回 usage 时不估算。
自动处理 UTF-8 分片、CR/LF/CRLF、多行 data、心跳及角色事件。
控制台实时提示首个文本片段的延时，正文完成后统一脱敏输出，避免分片直接回显 key。
服务端忽略 stream 参数而返回普通 JSON 时会保存响应并报错，不伪装为流式成功。
HTTP 200 中的 error、无效响应结构、responses 未完成状态不会被标记为成功。
服务端标明文本截断时保留已有文本，标记 incomplete；断流缺失结束标记、流内错误或超时则标记失败/超时并保存部分输出。
文本每次仅提交一个请求，不进行轮询或视频下载探测，不自动重试。

首次配置：在项目根目录运行
`cp -n scripts/openai-api-config.example scripts/openai-api-config`，自行编辑 `scripts/openai-api-config`。
每组填写 `NAME1`、`BASE_URL1`、`API_KEY1`，增加配置时将编号改为 2、3……。
仅同时填写 BASE_URL 和 API_KEY 的组可选；菜单只显示名称、地址和“key 已配置”。
输入 `0` 可临时自定义整组地址和 key；脚本不会回写配置，测试报告不保存 key。
配置文件已被 Git 忽略；模板只含空值，不要把真实 key 填入 `.example` 模板。
可用 `--config /绝对路径/配置.env` 指定其他文件；显式指定不存在的文件会报错。
文件支持空行、注释、单行单/双引号、`export` 前缀；不执行 shell、不展开变量或转义序列。

也支持 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL_ID` 环境变量。
环境变量的地址和 key 必须成对：存在文件配置时作为额外菜单项；没有文件配置时直接使用，
配合 `--api-type` 可无交互调用。选择自定义不会沿用文件或环境变量中的 key。

视频协议依据《人民数据词元工厂-Seedance视频生成接入指南》（2026-09-29）：
使用 JSON 向 `/v1/videos` 创建任务，保存响应中的平台 `id`，默认通过 `/v1/video/generations/{id}` 轮询，
优先获取下载地址和 Token 用量，先写入测试报告，再自动下载视频到本次结果目录的 `video.mp4`。
不自行拼接上游任务 ID，创建 HTTP 200 不代表生成完成；即使创建响应已完成，默认流程仍查询详情以取得地址和用量。
任务失败会保存并展示 `error.message` 或 `fail_reason`；`unknown` 保留响应并停止，需平台排查。
HTTP 429 保存错误并停止，可增大 `--poll-interval` 后使用已有 ID 再查；创建请求不自动重试。

默认 `--video-query-api generations` 读取 `{"code":"success","data":{...}}` 中的
`data.status`、`data.result_url`、`data.fail_reason` 和 `data.data.usage`（两层 `data`）；
查询已有任务时，也从 `data.data.model` 获取模型名称。
业务错误、无效 data 或任务 ID 不一致均保存响应并报错。不会自动切换查询接口。
如服务仅支持状态查询接口，可显式添加 `--video-query-api videos`，改用 `/v1/videos/{id}`。
此时保存该接口实际返回的 `usage`（若有），未上报则显示“未上报”。
base URL 可以是域名根地址、
带 `/v1` 的地址或上述三种接口的完整地址；自动移除接口后缀后拼接当前选择的路径。
自定义路径前缀需包含 API 版本路径。
视频提示词由用户输入。分辨率通过菜单选择，默认 720p；其余默认 5 秒、16:9、请求音频、关闭水印。
菜单提供 720p、1080p、4K 作为请求档位，不代表当前模型全部支持；接口拒绝时保存原始错误，不自动降档或重建任务。
这些是脚本默认值，不代表模型支持范围；时长、素材限制由上游校验。
每 5 秒轮询，最多等待 30 分钟，可通过 `--poll-interval`、`--max-wait` 调整。
`--seconds` 与 `--duration` 二选一；`--size` 与 `--resolution` 二选一。
`size` 用于选择分辨率档位，不保证最终像素宽高；需要固定画面比例时用 `--ratio`。
`--generate-audio` / `--no-generate-audio`、`--watermark` / `--no-watermark` 发送真正的 JSON 布尔值。
可用 `--content` 改为 content 文本数组；使用 `--reference-image URL`、`--reference-video URL` 或 `--reference-audio URL`
会自动构造 content 数组，各参数可重复指定，也可组合使用。

| 素材 | type 及地址字段 | role |
| --- | --- | --- |
| 视频 | `video_url` / `video_url.url` | `reference_video` |
| 图片 | `image_url` / `image_url.url` | `reference_image` |
| 音频 | `audio_url` / `audio_url.url` | `reference_audio` |

三种素材均可使用上游能访问的 HTTP(S) URL 或 `asset://asset-<素材ID>`。
asset 地址原样写入对应字段；脚本不下载该素材，也不上传本地文件，不根据素材 ID 推断类型。
素材 ID 是否存在及当前账号是否有权使用，由上游校验。asset 地址不包含账号密码、路径、查询参数或 fragment。
`--reference-audio` 是输入参考音频，`--generate-audio` 则控制输出视频是否生成音频。
根据 [Seedance 官方说明](https://docs.volcengine.com/docs/ark/create-video-generation-task-api?lang=zh)，Seedance 2.0 的参考音频必须至少搭配一张参考图片或一段参考视频。
脚本不对其他模型统一施加此限制，实际支持范围以平台开通能力为准。
交互选择与命令行参数使用同一请求格式，例如图片和音频一起发送：

```json
{
  "content": [
    {"type": "text", "text": "你输入的提示词"},
    {"type": "image_url", "image_url": {"url": "asset://asset-image-example"}, "role": "reference_image"},
    {"type": "audio_url", "audio_url": {"url": "asset://asset-audio-example"}, "role": "reference_audio"}
  ]
}
```

上面仅展示 content 字段，实际请求还包含 model、时长、分辨率等参数。

```bash
python3 scripts/test-openai-api.py --api-type video --seconds 5 --size 1280x720 --ratio 16:9
python3 scripts/test-openai-api.py --api-type video --resolution 1080p
python3 scripts/test-openai-api.py --api-type video --content --duration 5 --resolution 720p --no-generate-audio
# 将素材地址替换为实际可访问的 URL；提示词仍由你输入
python3 scripts/test-openai-api.py --api-type video --reference-image 'https://example.com/reference-image.jpg'
python3 scripts/test-openai-api.py --api-type video --reference-video 'https://example.com/reference.mp4'
# 使用上游已有的视频素材；替换为账号可用的素材 ID
python3 scripts/test-openai-api.py --api-type video --reference-video 'asset://asset-example'
# 同时使用图片和音频素材；替换为账号可用的素材 ID
python3 scripts/test-openai-api.py --api-type video --reference-image 'asset://asset-image-example' --reference-audio 'asset://asset-audio-example'
# 跳过两个视频配置菜单；model ID 和提示词按原方式提供
python3 scripts/test-openai-api.py --api-type video --resolution 720p --no-reference
```
`--request-timeout` 是网络操作超时，慢速响应可能令实际总等待略超出上限。

已有视频任务可直接继续查询，选择创建时使用的 base URL/key，无需输入 Model ID 或提示词：

```bash
python3 scripts/test-openai-api.py --task-id '你的任务ID'
# 仅在服务需要时，手动改用 videos 状态查询接口
python3 scripts/test-openai-api.py --task-id '你的任务ID' --video-query-api videos
```

`--task-id` 自动选择 video，仅发送 GET 查询，首次立即查询，未完成时按轮询间隔继续；
完成后先记录返回的下载地址和 Token 用量，再从 `result_url` 下载视频；没有该链接时使用 `/videos/{id}/content`，不发送创建请求。查询错误也保存结果和原任务 ID，
不会自动重新创建任务。不能与文本接口类型或 `--stream` 同用。
查询结果另存到新的时间戳目录，`query_only=true`，不覆盖原报告。
`query_completion_ms` 仅表示本次查询至首次观察到完成的耗时，不是生成总耗时；
查询报告不填写 `create_ms` 或 `completion_ms`。

每次结果独立保存至脚本同目录的 `test-results/<接口目录>/<UTC时间戳>-<随机后缀>/`，
即本项目的 `scripts/test-results/<接口目录>/<UTC时间戳>-<随机后缀>/`；
接口目录分别为 `chat-completions`、`responses`、`openai-video`，也可用 `--output` 自定义：

- `summary.txt`：接口类型、各次请求耗时、文本及用量；视频额外记录完成耗时、下载地址、总/生成 Token 数、下载状态和本地文件路径。
- `result.json`：请求参数、响应正文/SSE 事件和时间戳、HTTP 状态、部分响应头、计时及错误详情。
- `video.mp4`：视频生成成功后自动下载的完整文件；文本测试不生成此文件。

`headers_ms` 是收到响应头的延时；`first_body_byte_ms` 是收到首个响应体字节的延时；
`duration_ms` 是本次请求观测耗时（包含读取正文；视频下载包含完整文件传输）。
`ttft_ms` 从发起 HTTP 请求到首个非空文本或拒绝文本 delta 的时间，排除角色、心跳和推理事件；
无文本 delta 时为 null。它是客户端观察到的首片段延时，不是服务端精确 token 边界。
`text_delta_times_ms` 保存每个非空文本增量事件的到达时间；事件可能包含多个 token，不据此计算 TPOT。
`requests[].events` 保留事件名、到达时间和 JSON 数据，异常事件也留存；完整响应及跨 delta 的 key 均脱敏。
文本的 `request_ms` 是本次完整请求耗时。视频的 `completion_ms` 从创建请求前计时至首次观察到完成，包含轮询间隔误差。
流式结果约每秒写一次检查点，最终/异常退出再次写入；文件写入会带来少量本地观测开销。
SSE 总响应上限 2 MiB，单行上限 256 KiB，超过上限会报错并保存已接收内容。
失败、超时、Ctrl+C 均保存已有数据；本地中断不取消远端任务，不自动重试创建请求。

报告提取响应中的所有 HTTP(S) URL 并保留字段路径，需区分视频链接和封面链接。
视频报告的 `result_url` 保存下载地址，`usage` 保存服务端上报的用量对象，
`usage_source` 记录原响应字段路径（例如 `$.data.data.usage`），`usage_status` 为 `reported` 或 `not_reported`。
未返回有效用量对象时 `usage` / `usage_source` 为 `null`，摘要显示“未上报”；只返回部分计数时不补齐其余计数。
`usage.total_tokens` 和 `usage.completion_tokens` 不相加；轮询用量也不累加，不从 `quota` 推算 Token 或费用。
下载前已将这些信息保存到 `result.json` 和 `summary.txt`，下载失败或中断仍可查看。
已有 `result_url` 时直接下载，不携带 API key；否则使用 Bearer 鉴权请求 `/videos/{id}/content`。
下载最多跟随 5 次 HTTP(S) 重定向，保存 `Location`，重定向后的请求不携带 API key，也不允许 HTTPS 降级。
若接口直接返回视频流而没有 URL，会明确记录“无返回链接”，单列按协议拼出的
下载入口（需要 Bearer 鉴权），不把它当作接口返回链接。
视频以流式方式写入临时文件，校验 HTTP 200、视频类型、非空、MP4 文件头及 Content-Length（若存在），
下载完成后才保存为 `video.mp4`；失败或中断会清理临时文件。
`download` 字段记录下载状态、字节数、耗时、成功文件路径或错误；下载耗时不计入 `completion_ms` / `query_completion_ms`。
`--download-timeout` 默认 300 秒，与生成等待 `--max-wait` 独立；网络操作仍受 `--request-timeout` 限制，实际超时可能略有偏差。
下载失败会保留任务成功状态和 ID，报告状态为 `completed_download_failed`；可用 `--task-id` 再次查询和下载，无需重新生成。
下载中断的报告状态为 `completed_download_interrupted`。每次运行另建结果目录，不覆盖之前的视频。
签名下载 URL 原样保留，可能过期；结果目录已被 Git 忽略。

退出码：0 完成；1 失败或超时；2 文本输出不完整，或视频完成但下载失败；130 用户中断。

项目验证：`./.venv/bin/python -m pytest tests/test_manual_openai*.py -q`。
无第三方依赖的独立验证：`python3 -B -m unittest discover -s tests -p 'test_manual_openai*.py' -v`。
自动化测试使用本地模拟接口；generations 查询曾实测成功。新版 JSON 创建未执行线上生成，不代表线上模型验收。
