# Hermes

[Hermes Agent](https://hermes-agent.nousresearch.com/)（Nous Research）支持将
OpenViking 用作记忆提供方。根据 Hermes 版本，该提供方从插件目录安装，或由
Hermes 内置。

## 安装提供方

使用目录插件的 Hermes 版本需要先运行：

```bash
hermes plugins install openviking
```

如果当前 Hermes 版本仍内置 OpenViking，请跳过此命令；内置副本存在时会优先加载。
当 Hermes 更新移除内置副本时，已配置 `memory.provider: openviking` 的 profile
会自动尝试安装目录插件。如果迁移安装失败，请在同一 Hermes profile 中运行上述命令。

## 隔离 Python 环境

Hermes 通过 HTTP 连接 OpenViking，因此无需把 OpenViking 安装到 Hermes 的
Python 环境中。请在独立的虚拟环境或容器中运行 OpenViking 服务。不要在
已有 Hermes 的环境中使用 `--force-reinstall` 安装或升级 OpenViking：Hermes
版本可能会固定与 OpenViking 已支持、已修复安全问题的版本不同的依赖。如果确实要将
两个应用放在同一环境中，请在同一次依赖求解中安装它们，并在启动任一服务前运行
`python -m pip check`。

## 配置

```bash
hermes memory setup openviking
```

- 云：保持 **OpenViking Service (VolcEngine Cloud)**，粘贴 API Key
- 自托管：填 URL（默认 `http://127.0.0.1:1933`）和 API Key；本地免鉴权可留空
- 向导若发现已有 `ovcli.conf`，直接复用即可

## 验证

```bash
hermes memory status
```

`available` 表示 provider 已配置，不会检查服务端连通性，也不代表记忆已完成抽取。

## 参见

- [集成能力参考](./16-capability-reference.md)
- [Hermes — OpenViking memory provider 文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking) — 完整配置指南
- [部署指南](../guides/03-deployment.md) — 搭建 OpenViking 服务
- [鉴权](../guides/04-authentication.md) — 远程访问的 API Key 设置
