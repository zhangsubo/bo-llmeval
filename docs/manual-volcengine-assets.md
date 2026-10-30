# 火山引擎素材库测试工具

`scripts/test-volcengine-assets.py` 使用 Python 3.11+ 标准库，独立于现有视频测试脚本，无需安装 SDK。本工具用于私域虚拟人像及真人人像素材库的认证、素材组和素材管理；支持 CLI 与 Web 工作台，共用请求、签名和脱敏逻辑。

## Web 工作台

运行 `./start.sh` 后，从左侧 **素材库** 分组进入 **虚拟人像（AIGC）** 或 **真人人像** 页面：

1. Endpoint 默认 `https://token.peopleopen.cn/`，可自定义修改；填写AK/SK（或兼容网关的 Bearer）、Region 和 ProjectName。可先在「配置管理 → 素材库连接配置组」新增多个具名配置，分别保存根地址和 AK/SK，在两个人像页面通过配置组选择器切换。根地址写入现有配置文件，AK/SK 存于 1Password，页面只显示已保存状态；临时填写模式的凭证仍仅用于当前页面请求。
2. 选择素材/素材组的创建、查询、列表、更新或删除操作，填写对应字段。列表查询按当前页面固定传入 Filter.GroupType：虚拟页面为 AIGC，真人页面为 LivenessFace；高级 JSON 不会覆盖页面类型。虚拟页面提供创建素材组，真人页面提供 H5 认证与认证结果查询，两者操作入口独立。高级参数支持附加 JSON 筛选与分页；删除远端资源会显示确认框。
3. 真人人像使用右侧流程入口：生成 H5 链接 → 本人完成认证 → 查询认证素材组。认证链接只在本次响应与页面内存中可用；BytedToken 自动填入查询表单，查询成功的 GroupId 自动填入上传表单。
4. 上传素材时可开启“等待素材入库完成”，查询结果为 Active 后复制 `asset://…` 用于视频生成。最长等待可调整至 600 秒。执行期间请保持页面，离开页面不会撤销已发送的请求。
5. 点击“查看历史报告”进入结果中心，或使用“素材库 · volcengine-assets”分类筛选。CLI 和 Web 仍使用同一个 `scripts/test-results/volcengine-assets/` 目录，报告中认证链接、凭证均脱敏。

Web API 为 `POST /api/assets/run`。可传 `use_saved_credentials=true` 使用配置管理中保存的 AK/SK，后端读取 1Password并固定使用已保存根地址；不可用该凭证临时覆盖为其他地址。默认仍接受临时凭证。请求字段为 endpoint、auth、access_key/secret_key/security_token 或 api_key、action、region、payload、wait、timeout、max_wait、poll_interval；真人认证查询通过独立 byted_token 字段传入会话凭证。删除需 confirm_delete=true。接口校验失败返回 HTTP 400，供应商执行结果通过 report.success/report.error 表达，并附带 category、folder 供历史查询。认证会话创建成功时，额外返回 validation_session，仅本次响应可见且禁止缓存。

左侧导航支持折叠和窄屏自动收起，按“测试与诊断 / 素材库 / 系统设置”分组。切换虚拟与真人页面时会重置表单、结果和认证会话，避免数据混用。

## Endpoint 与鉴权

Web 和 CLI 默认 Endpoint 为 `https://token.peopleopen.cn/`。CLI 可通过 `--endpoint` 或环境变量 `VOLC_ASSET_ENDPOINT` 覆盖，优先级为命令行 > 环境变量 > 默认地址。不会自动追加 `/api/v3`、`/v1`、`/business-api`。含路径、查询串、fragment 或账号密码的 Endpoint 会被拒绝，避免请求发送到错误接口。

例如 Endpoint 为 `https://example.com` 时，请求如下：

```text
POST https://example.com/?Action=ListAssetGroups&Version=2024-01-01
```

官方根地址为 `https://ark.cn-beijing.volcengineapi.com`。官方素材库要求 AK/SK，不能用推理 API Key 替代。设置以下环境变量，或在交互终端中隐藏输入凭证：

| 环境变量 | 用途 |
| --- | --- |
| `VOLC_ASSET_ENDPOINT` | 可自定义的根地址 |
| `VOLC_ACCESS_KEY` | AK |
| `VOLC_SECRET_KEY` | SK |
| `VOLC_SECURITY_TOKEN` | 可选 STS token |
| `VOLC_REGION` | 签名 region，默认 `cn-beijing` |
| `VOLC_BYTED_TOKEN` | 真人认证会话凭证，仅 GetVisualValidateResult 使用，也可隐藏输入 |
| `VOLC_ASSET_API_KEY` | 仅 `--auth bearer` 的兼容网关凭证 |

签名服务固定 `ark`，版本固定 `2024-01-01`。如果兼容网关支持 Bearer，可以显式加 `--auth bearer`；这不是官方素材库支持的鉴权方式。凭证不接受命令行参数、不保存至配置；HTTP 重定向不会跟随。

## 操作示例

以下假定已设置 Endpoint 和凭证环境变量。无参数默认查询素材组列表。

```bash
# 查询素材组
python3 scripts/test-volcengine-assets.py

# 查询真人人像素材组/素材
python3 scripts/test-volcengine-assets.py --action ListAssetGroups --group-type LivenessFace
python3 scripts/test-volcengine-assets.py --action ListAssets --group-type LivenessFace

# 创建虚拟人像素材组（GroupType 默认为 AIGC）
python3 scripts/test-volcengine-assets.py --action CreateAssetGroup \
  --name '测试素材组' --description '虚拟人像测试'

# 使用上一步返回的素材组 Id，从公共 URL 导入素材并等待入库
python3 scripts/test-volcengine-assets.py --action CreateAsset \
  --group-id group-example --asset-type Image \
  --url https://example.com/portrait.png --name '测试形象' --wait

# 后续查询；不加 --wait 则只查询一次
python3 scripts/test-volcengine-assets.py --action GetAsset --id asset-example --wait

# 查询素材组详情及素材列表
python3 scripts/test-volcengine-assets.py --action GetAssetGroup --id group-example
python3 scripts/test-volcengine-assets.py --action ListAssets

# 更新名称
python3 scripts/test-volcengine-assets.py --action UpdateAsset --id asset-example --name '新名称'
python3 scripts/test-volcengine-assets.py --action UpdateAssetGroup --id group-example --name '新组名'

# 删除指定资源：显式调用才执行，不自动清理创建的资源
python3 scripts/test-volcengine-assets.py --action DeleteAsset --id asset-example
python3 scripts/test-volcengine-assets.py --action DeleteAssetGroup --id group-example
```

素材类型支持 `Image`、`Video`、`Audio`，以公共 HTTP(S) URL 导入，不上传本地文件或 Base64。`--project-name` 默认为 `default`，素材组、素材和后续视频推理所用项目需一致。

ListAssetGroups 与 ListAssets 均自动补齐缺省的 `Filter.GroupType=AIGC`；CLI 可用 `--group-type LivenessFace` 查询真人素材。JSON 中已有的 GroupType 会保留，显式 `--group-type` 优先；Web 则以当前页面的固定类型为准。其他 Filter 字段保留，非对象 Filter 或非法 GroupType 会在发送前拒绝。

高级筛选、分页等官方字段可写入 JSON 对象，通过 `--body-file /path/to/body.json` 传入；本工具不改写其中的字段结构。显式命令行字段覆盖 JSON 中的同名字段；未指定 `--project-name` 时保留 JSON 中的 ProjectName。请勿将凭证放入请求 JSON 文件。

## 真人人像素材资产

真人素材组由本人完成认证后获得，不能通过 `CreateAssetGroup` 创建（该操作仅用于 AIGC）。Endpoint 和 AK/SK 配置沿用上文，认证接口同样只请求根路径。

```bash
# 1. 获取 H5 认证链接；CallbackURL 为你可访问的认证结束跳转页面
python3 scripts/test-volcengine-assets.py --action CreateVisualValidateSession \
  --callback-url https://example.com/callback --project-name default

# 2. 本人打开返回的 H5Link，完成授权及活体认证。
# 回调地址中的 resultCode=10000 表示认证通过，再查询 GroupId：
python3 scripts/test-volcengine-assets.py --action GetVisualValidateResult \
  --project-name default

# 3. 使用查询返回的 GroupId 上传该真人素材，等待处理完成
python3 scripts/test-volcengine-assets.py --action CreateAsset \
  --group-id group-example --asset-type Image \
  --url https://example.com/portrait.png --project-name default --wait
```

第 2 步通过 `VOLC_BYTED_TOKEN` 环境变量或终端隐藏输入提供第 1 步返回的 `BytedToken`，不通过命令行或 JSON 文件传入。其有效期为 30 分钟，H5 链接使用后失效。认证失败需重新生成会话。脚本不代替本人完成活体认证，不托管回调页面，也不将回调参数当作服务端认证结果；实际 `GroupId` 由 `GetVisualValidateResult` 返回。未返回有效 GroupId、凭证过期或服务端业务错误均退出失败，不自动重试。

认证链接和 BytedToken 仅在交互终端中显示一次；非交互运行默认脱敏，确需查看可显式添加 `--show-validation-session`。该选项的终端输出包含临时认证凭证，请勿重定向到文件或收集到日志。无论是否显示，`result.json` / `summary.txt` 均脱敏，不持久保存认证链接和 BytedToken。

认证得到的素材组可复用现有 GetAssetGroup、ListAssetGroups、UpdateAssetGroup、DeleteAssetGroup 以及全部素材管理操作。同一真人后续素材上传到同一 GroupId，无需重复认证。上传素材会与认证采集的人像进行一致性比对，只有状态为 Active 才可用于视频生成。认证、素材上传和视频生成需使用一致的项目。

官方参考：[真人人像素材资产指南](https://docs.volcengine.com/docs/ark/guide-preview?lang=zh)、[创建 H5 认证会话](https://docs.volcengine.com/docs/ark/h5-create-visual-validate-session-api?lang=zh)。

## 结果与错误

- 每次请求记录 HTTP 状态、耗时、请求体、响应体。结果写到 `scripts/test-results/volcengine-assets/<时间-随机串>/result.json` 和 `summary.txt`；可用 `--output-dir` 更换父目录。
- 终端与结果文件均脱敏当前凭证及素材下载 URL 中的常见临时签名参数，不保存 Authorization 请求头。
- `--wait` 仅用于 CreateAsset/GetAsset：`Processing` 继续轮询，`Active` 成功，`Failed` 失败。成功入库后输出 `asset_uri`，可传给现有视频脚本的 `--reference-image` / `--reference-video` / `--reference-audio`。
- HTTP 非 2xx、非 JSON、业务 Error（包括 HTTP 200 中的 ResponseMetadata.Error）、素材失败与轮询超时均以退出码 1 结束；成功为 0。
- 默认单次 HTTP 超时 `--timeout 60` 秒，轮询间隔 `--poll-interval 3` 秒，首个请求结束后最多等待 `--max-wait 300` 秒。创建成功但不加 `--wait`，只表示请求成功，不代表素材已可用于推理。
- 不自动重试创建、更新、删除操作。超时或中断后请求可能已经生效，应先查询状态再决定是否重试。

本地自动化验证：

```bash
./.venv/bin/python -m pytest -q tests/test_volcengine_assets.py
```

参考：[私域虚拟人像素材资产库使用指南](https://docs.volcengine.com/docs/ark/private-virtual-avatar-library-guide-preview?lang=zh)、[CreateAsset API](https://docs.volcengine.com/docs/ark/create-asset-api?lang=zh)、[GetAsset API](https://docs.volcengine.com/docs/ark/get-asset-api?lang=zh)、[官方 Python SDK 签名实现](https://github.com/volcengine/volc-sdk-python/blob/master/volcengine/auth/SignerV4.py)。

### 素材库配置保存与清除

在配置管理指定 1Password 账号及保险库，然后添加素材库配置组。新增组的根地址默认填入 `https://token.peopleopen.cn/`，可手动修改。AK/SK 同时留空保留原引用，更新时同时填写两项。标记清除或删除组并保存后解除引用；保险库历史条目保留供恢复。保存其他配置不会删除素材库组。

本地只保存 `ASSET_ID1`、`ASSET_NAME1`、`ASSET_ENDPOINT1`、`ASSET_CREDENTIAL_STORE1` 等编号字段；凭证存储字段为 `op://` 引用，指向包含 AK/SK 的隐藏字段。稳定 ID 保证改名、重排时凭证仍正确对应。旧单组显示为“素材库配置 1”；保存时先将旧钥匙串凭证写入 1Password 并核验，配置替换成功后再清理旧钥匙串项。清理失败会返回提示。

切换配置组会清空认证会话、素材 ID 与结果。未配齐凭证的组不可用于请求；仍支持临时填写。保存失败保留原配置，已创建的新条目会尝试归档。旧字段 `ASSET_ENDPOINT`、`ASSET_CREDENTIAL_STORE` 保留为首组兼容别名。

模型测试 CLI 和 AI 解读 CLI 自动解析配置内的 1Password 引用。独立素材库 CLI 仍使用环境变量或隐藏输入，可通过 `op run` 注入凭证。详见 [1Password 密钥存储](../README.md#1password-密钥存储)。
