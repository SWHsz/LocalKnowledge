# Zotero RAG - 本地文献知识库

直接读取 Zotero 本地 storage 目录，构建带引用的 RAG 问答系统。

## 特点

- 📚 直接读取 Zotero 本地存储，无需同步
- 🏷️ 自动从文件名解析作者、年份、标题
- 📄 按页分块，精确到页码引用
- 💬 带引用来源的 RAG 问答
- 🖥️ 命令行 + Web 界面

## 快速开始

### 1. 安装 Ollama 和模型

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:14b-instruct-q4_K_M
ollama pull nomic-embed-text
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置 Zotero 路径

编辑 `config.yaml`：

```yaml
zotero:
  # 指向你的 Zotero 数据目录
  data_dir: "~/Zotero"
  # 或直接指定 storage 目录
  # storage_dir: "~/Zotero/storage"
```

Zotero 目录结构：
```
~/Zotero/
├── storage/
│   ├── 3GPQD2K2/
│   │   └── Lee 等 - 2025 - Dropout Connects Transformers...pdf
│   ├── ABCD1234/
│   │   └── Smith et al. - 2024 - Some Paper Title.pdf
│   └── ...
└── zotero.sqlite
```

### 4. 构建索引

```bash
# 索引所有文献
python indexer.py

# 查看统计
python indexer.py --stats

# 列出已索引文献
python indexer.py --list
```

### 5. 开始问答

```bash
# 命令行交互
python query.py

# 单次查询
python query.py -q "What are the main contributions of these papers?"

# Web 界面
python web_ui.py
# 访问 http://localhost:7860
```

## 使用示例

```
Question: What methods are used for knowledge distillation?

Answer: Based on the literature, several approaches are used...

References:
1. Dropout Connects Transformers and CNNs Transfer General Knowledge for Knowledge Distillation
   Lee 等 (2025) — Page 3 (relevance: 0.92)
   > The proposed method leverages dropout...

2. Another Related Paper
   Smith et al. (2024) — Page 7 (relevance: 0.85)
   > Knowledge distillation techniques...
```

## 配置说明

```yaml
# config.yaml

zotero:
  data_dir: "~/Zotero"      # Zotero 数据目录

paths:
  vector_db: "./data/chroma_db"  # 向量数据库
  cache_dir: "./data/cache"       # 缓存

ollama:
  base_url: "http://localhost:11434"
  llm_model: "qwen2.5:14b-instruct-q4_K_M"
  embed_model: "nomic-embed-text"

rag:
  chunk_size: 1024      # 分块大小
  chunk_overlap: 200    # 重叠
  top_k: 5              # 检索数量
  similarity_threshold: 0.7
```

## 硬件建议

| 配置 | 推荐模型 |
|------|---------|
| 16GB 显存 | qwen2.5:14b-instruct-q4_K_M |
| 8GB 显存 | qwen2.5:7b-instruct |
| 纯 CPU | qwen2.5:3b |

## 文件结构

```
zotero-rag/
├── config.yaml      # 配置
├── indexer.py       # 索引器
├── query.py         # RAG 查询
├── web_ui.py        # Web 界面
├── requirements.txt
└── data/
    ├── chroma_db/   # 向量数据库
    └── cache/       # 索引状态缓存
```

## FAQ

**Q: 索引需要多久？**

A: 100 篇论文大约 10-20 分钟，取决于 PDF 大小和 embedding 模型速度。

**Q: 如何更新索引？**

A: 直接运行 `python indexer.py`，会自动检测新增/修改的文件。

**Q: 显存不够怎么办？**

A: 换用更小的模型，修改 `config.yaml` 中的 `llm_model`。
