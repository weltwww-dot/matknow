# 桌面完成后的扣子接入

当前提供经过本地测试的 API 适配层，尚未部署公网服务，也未创建或发布扣子智能体。API 与桌面复用 `ResearchAgent`，使用方向选定的厂商、URL 和模型，支持当前方向的资料和实验工具；没有模型凭据时返回检索预览。

## 本地验证

先在桌面版导入资料。然后在项目目录启动同一知识库的服务：

```powershell
$env:MATKNOW_API_TOKEN = "替换为你自己生成的长随机令牌"
.\.venv\Scripts\python.exe -m matknow.api
```

v0.10.6 起，源码开发版默认使用项目根目录的 `app-data/`，EXE 使用自身目录的 `app-data/`。从源码运行 API 访问安装版资料时，应传入 `--data-dir <EXE所在目录>/app-data`。桌面显式使用自定义数据目录时同样需要保持一致。默认仅监听 `127.0.0.1:8765`，不会自动暴露到公网。

API 默认固定绑定“材料制备”Profile（`00000000-0000-0000-0000-000000000001`），不会跟随桌面切换。启动其他方向时使用 `--profile <UUID>`；UUID 可以在该数据根目录的 `profiles.json` 中找到。无效 ID 拒绝启动。API 仅读取已有方向设置，不修改桌面的当前选择。

Windows 上默认读取同一数据根目录下、对应接口的 DPAPI 加密 Key 文件。部署服务也可以通过 `MATKNOW_MODEL_API_KEY` 环境变量提供当前模型 Key（优先于本地加密文件）；旧 `DEEPSEEK_API_KEY` 仅用于 DeepSeek 官方接口，不转发给其他厂商。Key 不放在请求体中。服务访问令牌 `MATKNOW_API_TOKEN` 与模型 Key 各自独立。更换 Key 或方向模型设置后重启 API，以载入新的配置；已经运行的 API 使用启动时绑定的方向和模型。

接口：`GET /health` 返回运行模式；`POST /v1/ask` 必须携带 `Authorization: Bearer <令牌>`。请求体为：

```json
{"question":"这份资料中的干燥条件是什么？","document_ids":null}
```

`document_ids` 为数据库生成的资料 UUID，不是收集清单中的 P001 等人工编号。省略或 `null` 检索全库；`[]` 表示不检索任何资料。指定 ID 时只检索对应资料。

响应含 `answer`、`status`、`sources` 和可空的 `agent_run`。后者记录简短计划、实际工具摘要和完成状态，不含隐藏推理或凭据。每个来源包括文件名、页码／段落定位、原文与 `citation_number`（固定正文编号）。不同方向的资料 ID 不能越过绑定数据库查询。`document_ids` 限制资料检索，实验工具只访问当前绑定方向。`GET /health` 返回绑定的 `profile_id` 和运行模式：`model_qa` 表示已配置模型，`retrieval_preview` 表示本地检索；健康信息不验证实时模型连通性。

状态为 `generated` 时是通过引用校验的模型回答；`retrieval_preview` 为未配置模型的检索预览；`insufficient_evidence` 为未找到匹配片段或模型明确指出证据不足；`model_error` 为调用或输出校验失败，保留真实命中片段。扣子须保留这些差别，不能把错误预览改写成确定答案。

导出接口描述：

```powershell
.\.venv\Scripts\python.exe -m matknow.api --export-openapi docs/coze-openapi.json
```

## 后续接入步骤

1. 使用自己的 Key 验证真实模型生成，并完成知识库评测，确认接口输出稳定。
2. 将 API 核心部署到具有可访问域名的服务上，配置 HTTPS 和鉴权；部署前选择允许上传的方向，停写后迁移完整数据根目录中的注册表及选定数据库，确保对应 UUID 保持一致。Windows 凭据不随资料迁移，服务端单独提供模型 Key。服务独立运行，不依赖个人电脑开机。
3. 在扣子的实际账号入口创建相应插件／工具，配置问答接口、请求体和服务访问令牌。OpenAPI 文件是接口描述，不能保证各版本平台都能未经调整直接导入。
4. 编写智能体提示词：材料制备问题调用工具；答案保留来源；资料不足时追问或说明；不同文献条件分别呈现；工具报错不能改写成知识结论。
5. 使用真实评测题在扣子调试后，再按比赛要求发布并验证体验入口。

扣子官方文档：[基于 API 创建插件](https://docs.coze.cn/guides_services)说明可以集成自有 API，插件 URL 要求域名格式，并支持服务令牌／API key 鉴权。[常见问题](https://docs.coze.cn/guides_FAQ)说明新账号不开放旧版低代码入口，接入时应以账号实际可用的新入口为准。上述信息于 2026-10-13 核对。
