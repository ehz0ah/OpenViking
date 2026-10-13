# langchain-openviking

`langchain-openviking` 是 OpenViking 官方维护的 LangChain 和 LangGraph
集成包。框架适配逻辑不再依赖 OpenViking 服务端实现，远程访问统一通过轻量的
`openviking-sdk` 完成。

> **OpenViking Server 要求**：文档中的示例使用 `viking://~` Home 别名（例如 `viking://~/memories`），Server 会将其展开为当前调用方自己的用户空间，因此需要一个支持 `viking://~` 的 Server。不带 uid 的 `viking://user/memories` 旧写法会被新版 Server 拒绝；要访问其他用户请显式传入 `viking://user/<uid>/...`。

## 安装

LangChain Retriever、Tools、Message History 和 Context Wrapper：

```bash
pip install langchain-openviking
```

LangGraph Store 和 Middleware：

```bash
pip install "langchain-openviking[langgraph]"
```

## 快速开始

```python
from langchain_openviking import OpenVikingRetriever
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(
    url="http://127.0.0.1:1933",
    api_key="your-user-api-key",
)
client.initialize()
retriever = OpenVikingRetriever(
    client=client,
    target_uri="viking://~/memories",
)

try:
    documents = retriever.invoke("需要记住哪些部署偏好？")
finally:
    client.close()
```

外部传入的 client 仍由调用方管理。通过 `url=` 创建的 client 由适配器管理。

## 导入迁移

服务端包不再提供 `openviking.integrations.langchain`。请安装独立包，并将导入改为：

```python
from langchain_openviking import OpenVikingRetriever
```

参见[示例](https://github.com/volcengine/OpenViking/tree/main/integrations/frameworks/langchain/examples)和[框架适配指南](https://github.com/volcengine/OpenViking/blob/main/integrations/frameworks/README.md)。

## 验证

在仓库根目录运行：

```bash
pip install -e 'integrations/frameworks/langchain[langgraph,test,dev]'
python -m pytest -q --noconftest -o addopts='' \
  integrations/frameworks/langchain/tests tests/unit/test_langchain_package_boundary.py
```

适配器使用 AGPL-3.0；可运行[示例](https://github.com/volcengine/OpenViking/blob/main/integrations/frameworks/langchain/examples/LICENSE)保留 Apache-2.0 许可。
