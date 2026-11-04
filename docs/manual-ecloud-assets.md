# 移动云素材库 Web 测试

在「视频模型测试」下选择「【移动云】虚拟人像（aigc）」或「【移动云】真人人像」。移动云使用独立 REST 路径和 OpenAPI V2.0 签名，不能直接使用原火山页面调用。

## 连接

默认根地址为 `https://ecloud.10086.cn/`，请求自动追加 `/api/openapi-maas/exp/aicc/v2` 和操作路径。支持临时填写 AK/SK，或在「配置管理 → 素材库链接配置」保存具名连接后选择使用。保存凭证继续使用现有 1Password 流程；配置文件仅保存引用。移动云页面默认使用临时连接，避免自动选中已有火山连接；请主动选择对应移动云配置组。

签名 Timestamp 使用北京时间（UTC+8）并保留字面量 `Z`，这是移动云网关的实际约定，与标准 ISO UTC 语义不同；实现显式固定时区，不依赖操作系统时区。报告 started_at 仍使用标准 UTC。

移动云仅支持 AK/SK 签名，无需 Region、ProjectName，不支持火山 STS 或 Bearer。凭证、认证链接、BytedToken 不进入历史报告；认证会话只在当前页面内存保留，切换页面后清除。

## 虚拟人像

1. 创建素材组，填写名称（不超过 64 字符）及可选描述（不超过 300 字符）。
2. 使用返回的 GroupId 上传素材，填写名称、公网 HTTP(S) URL 和 Image/Video/Audio 类型。
3. 等待入库，或按素材 ID 继续查询状态。只有 ACTIVE 状态才返回可用的 `asset://` 引用。

支持素材和素材组的列表、详情、更新、删除。删除需明确确认；素材名称更新要求素材已为 ACTIVE，由供应商校验。文件须符合移动云当前大小、时长和格式限制，本页面不支持直接上传本地文件或 Base64。

列表固定当前页面的 groupType；高级 JSON 使用移动云原生字段，例如 `{"pageNo":1,"pageSize":10,"groupIds":["group-example"]}`。素材列表还支持 statuses 数组（PROCESSING、ACTIVE、FAILED）与 assetName，素材组列表支持 groupName。

## 真人人像

- 默认回调：生成 H5 认证链接时不填 callbackUrl，本人用手机扫描页面显示的二维码（或打开 H5 链接）完成活体认证后，查询认证结果取得 GroupId。
- 自定义回调：填写不含 query/fragment 的回调地址；收到 `resultCode=10000` 的成功回调后，手动执行「确认认证结果并落库」，再用 GroupId 上传同人素材。工作台不提供公网回调接收服务。
- 有效期以本次接口返回的 expiresIn 秒数为准，不能假定为 30 分钟；确认落库也须在有效期内完成。凭证过期后重新生成会话。

## 实现与验收边界

`POST /api/assets/run` 增加可选 `provider: "ecloud"`，省略时仍为原火山适配。移动云 payload 使用文档字段，详情/更新/删除统一通过本地字段 `id` 填充远端路径；BytedToken 使用顶层专用字段 `byted_token`。13 个操作由 `scripts/ecloud_assets.py` 实现，复用配置解析、脱敏、报告及 Web UI。该模块不是独立 CLI 入口。

结果保存至 `scripts/test-results/ecloud-assets/`，在结果解读中可按移动云素材库筛选。报告保留实际 HTTP 方法、路径及脱敏响应，不保存带认证签名的请求 URL。

本地测试覆盖 REST 合约、签名、错误处理、轮询与脱敏。真实移动云账号权限、真人手机认证和素材跨视频渠道引用仍需在对应账号联调，不代表已通过真实云端验收。

## 官方依据

核对日期：2026-11-04。

- [素材库接口目录](https://ecloud.10086.cn/op-help-center/doc/outline/109811)
- [整体接入说明](https://ecloud.10086.cn/op-help-center/doc/article/100301)
- [签名规范](https://ecloud.10086.cn/op-help-center/doc/article/24283)
- [创建 H5 认证会话](https://ecloud.10086.cn/op-help-center/doc/article/100375)
- [确认真人认证结果](https://ecloud.10086.cn/op-help-center/doc/article/102001)
