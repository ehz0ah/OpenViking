# Hermes

[Hermes Agent](https://hermes-agent.nousresearch.com/)（Nous Research）支持将
OpenViking 用作记忆提供方。根据 Hermes 版本，该提供方从插件目录安装，或由
Hermes 内置。

## 安装提供方

使用目录插件的 Hermes 版本需要先运行：

```bash
hermes plugins install openviking --enable
```

如果当前 Hermes 版本仍内置 OpenViking，请跳过此命令；内置副本存在时会优先加载。
当 Hermes 更新移除内置副本时，已配置 `memory.provider: openviking` 的 profile
会自动尝试安装目录插件。如果迁移安装失败，请在同一 Hermes profile 中运行上述命令。

## 配置并开始对话

```bash
hermes memory setup openviking
hermes
```

先选择 **Personal Agent** 或 **Shared Agent**，再选择连接方式：

- **Quick Local**：复用 Hermes 已配置且受支持的 LLM。向导安装独立的 OpenViking
  服务和本地 embedding 模型，检查 LLM 后启动服务。记忆抽取仍可使用远程 LLM。
- **OpenViking Service**：输入服务 API Key。
- **Custom**：输入服务 URL 和凭据；向导若发现已有 `ovcli.conf` profile，可直接复用。

Quick Local 需要外部插件实际生效，服务在 Hermes 退出后仍会运行。
其他自托管服务应使用独立 Python 环境或容器；Hermes 通过 HTTP 连接。

对话捕获后，OpenViking 提交并抽取记忆，供后续召回。配置不会改变已有服务端数据。

## 更新

通过插件目录安装的用户可运行：

```bash
hermes plugins update openviking
```

更新后重启 Hermes 或 gateway。连接设置和服务端数据会保留。

如需更新 Quick Local 服务，再次运行 `hermes memory setup openviking` 并选择
Quick Local。向导选择最新兼容的 OpenViking 0.4 版本；正常聊天复用已安装的服务。

## 验证

```bash
hermes memory status
```

`available` 表示 provider 已配置，不会检查服务端连通性，也不代表记忆已完成抽取。

## 参见

- [集成能力参考](./16-capability-reference.md)
- [OpenViking 插件页面](https://hermes-agent.nousresearch.com/docs/plugins/openviking) — 完整配置指南
- [部署指南](../guides/03-deployment.md) — 搭建 OpenViking 服务
- [鉴权](../guides/04-authentication.md) — 远程访问的 API Key 设置
