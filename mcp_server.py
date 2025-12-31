#!/usr/bin/env python3
"""
Zotero MCP Server

让 Claude Desktop 能够直接搜索和查询你的 Zotero 文献库。

安装:
    pip install mcp pydantic httpx

配置 Claude Desktop (claude_desktop_config.json):
    {
      "mcpServers": {
        "zotero": {
          "command": "python",
          "args": ["C:/Users/你的用户名/LocalKnowledge/mcp_server.py"]
        }
      }
    }
"""

import json
import re
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict

from llama_index.core import (
    StorageContext,
    Settings,
    load_index_from_storage,
)
from llama_index.core.retrievers import VectorIndexRetriever
from llama_index.llms.ollama import Ollama
from llama_index.embeddings.ollama import OllamaEmbedding
import yaml

# ============================================================
# 配置加载
# ============================================================

def load_config(config_path: str = None) -> dict:
    """加载配置文件"""
    if config_path is None:
        # 尝试多个位置
        candidates = [
            Path(__file__).parent / "config.yaml",
            Path.home() / "LocalKnowledge" / "config.yaml",
            Path("config.yaml"),
        ]
        for p in candidates:
            if p.exists():
                config_path = str(p)
                break
    
    if config_path is None:
        raise FileNotFoundError("找不到 config.yaml 配置文件")
    
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_llama_index(config: dict):
    """配置 LlamaIndex"""
    ollama_config = config["ollama"]
    
    Settings.llm = Ollama(
        model=ollama_config["llm_model"],
        base_url=ollama_config["base_url"],
        request_timeout=120.0,
    )
    
    Settings.embed_model = OllamaEmbedding(
        model_name=ollama_config["embed_model"],
        base_url=ollama_config["base_url"],
    )


# ============================================================
# 数据类
# ============================================================

@dataclass
class Citation:
    """引用信息"""
    title: str
    authors: str
    year: str
    page: int
    score: float
    snippet: str


# ============================================================
# MCP Server 初始化
# ============================================================

mcp = FastMCP("zotero_mcp")

# 全局变量存储索引
_index = None
_config = None


def get_index():
    """懒加载索引"""
    global _index, _config
    
    if _index is None:
        _config = load_config()
        setup_llama_index(_config)
        
        db_path = Path(_config["paths"]["vector_db"])
        if not db_path.exists():
            raise FileNotFoundError(
                f"向量数据库不存在: {db_path}\n"
                "请先运行 python indexer.py 构建索引"
            )
        
        storage_context = StorageContext.from_defaults(persist_dir=str(db_path))
        _index = load_index_from_storage(storage_context)
    
    return _index, _config


# ============================================================
# 输入模型
# ============================================================

class SearchInput(BaseModel):
    """文献搜索输入"""
    model_config = ConfigDict(str_strip_whitespace=True)
    
    query: str = Field(
        ..., 
        description="搜索查询，可以是关键词、问题或主题描述。例如：'knowledge distillation methods' 或 'transformer attention mechanism'",
        min_length=1,
        max_length=500
    )
    top_k: int = Field(
        default=5,
        description="返回的最相关文献数量",
        ge=1,
        le=20
    )


class GetPaperInput(BaseModel):
    """获取特定论文信息"""
    model_config = ConfigDict(str_strip_whitespace=True)
    
    title_keyword: str = Field(
        ...,
        description="论文标题中的关键词，用于查找特定论文",
        min_length=1
    )


class ListPapersInput(BaseModel):
    """列出论文"""
    model_config = ConfigDict(str_strip_whitespace=True)
    
    year: Optional[str] = Field(
        default=None,
        description="筛选特定年份的论文，例如 '2024'"
    )
    limit: int = Field(
        default=20,
        description="返回的最大数量",
        ge=1,
        le=100
    )


# ============================================================
# MCP Tools
# ============================================================

@mcp.tool(
    name="zotero_search",
    annotations={
        "title": "Search Zotero Literature",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def zotero_search(params: SearchInput) -> str:
    """搜索 Zotero 文献库中的相关内容。
    
    使用语义搜索在用户的学术文献库中查找相关段落。
    返回匹配的文献信息和相关文本片段，可用于回答学术问题或撰写文献综述。
    
    Args:
        params: 包含搜索查询和返回数量的参数
        
    Returns:
        JSON 格式的搜索结果，包含标题、作者、年份、页码和相关文本
    """
    try:
        index, config = get_index()
        
        retriever = VectorIndexRetriever(
            index=index,
            similarity_top_k=params.top_k,
        )
        
        nodes = retriever.retrieve(params.query)
        
        if not nodes:
            return json.dumps({
                "status": "no_results",
                "message": "未找到相关文献",
                "query": params.query
            }, ensure_ascii=False, indent=2)
        
        results = []
        for node in nodes:
            meta = node.node.metadata
            results.append({
                "title": meta.get("title", "Unknown"),
                "authors": meta.get("authors", "Unknown"),
                "year": meta.get("year", ""),
                "page": meta.get("page", 0),
                "relevance_score": round(node.score, 3) if node.score else 0,
                "snippet": node.node.text[:500] + "..." if len(node.node.text) > 500 else node.node.text
            })
        
        return json.dumps({
            "status": "success",
            "query": params.query,
            "total_results": len(results),
            "results": results
        }, ensure_ascii=False, indent=2)
        
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": str(e)
        }, ensure_ascii=False, indent=2)


@mcp.tool(
    name="zotero_list_papers",
    annotations={
        "title": "List Papers in Library",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def zotero_list_papers(params: ListPapersInput) -> str:
    """列出 Zotero 文献库中的论文。
    
    获取文献库中所有论文的概览，可按年份筛选。
    
    Args:
        params: 包含筛选条件的参数
        
    Returns:
        JSON 格式的论文列表
    """
    try:
        config = load_config()
        cache_dir = Path(config["paths"]["cache_dir"])
        state_file = cache_dir / "index_state.json"
        
        if not state_file.exists():
            return json.dumps({
                "status": "error",
                "message": "索引状态文件不存在，请先运行 indexer.py"
            }, ensure_ascii=False, indent=2)
        
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
        
        papers = []
        for key, info in state.get("indexed_files", {}).items():
            # 年份筛选
            if params.year and info.get("year") != params.year:
                continue
            
            papers.append({
                "title": info.get("title", "Unknown"),
                "authors": info.get("authors", "Unknown"),
                "year": info.get("year", ""),
                "pages": info.get("pages", 0),
                "zotero_key": key
            })
        
        # 按年份排序
        papers.sort(key=lambda x: (x.get("year", "0000"), x.get("title", "")), reverse=True)
        
        # 限制数量
        papers = papers[:params.limit]
        
        # 统计
        years = {}
        for p in state.get("indexed_files", {}).values():
            y = p.get("year", "Unknown")
            years[y] = years.get(y, 0) + 1
        
        return json.dumps({
            "status": "success",
            "total_in_library": len(state.get("indexed_files", {})),
            "returned": len(papers),
            "year_filter": params.year,
            "papers_by_year": dict(sorted(years.items(), reverse=True)),
            "papers": papers
        }, ensure_ascii=False, indent=2)
        
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": str(e)
        }, ensure_ascii=False, indent=2)


@mcp.tool(
    name="zotero_get_paper_content",
    annotations={
        "title": "Get Paper Content",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def zotero_get_paper_content(params: GetPaperInput) -> str:
    """获取特定论文的详细内容。
    
    通过标题关键词搜索并返回论文的完整内容片段。
    
    Args:
        params: 包含标题关键词的参数
        
    Returns:
        JSON 格式的论文内容
    """
    try:
        index, config = get_index()
        
        # 使用标题作为查询
        retriever = VectorIndexRetriever(
            index=index,
            similarity_top_k=10,
        )
        
        nodes = retriever.retrieve(params.title_keyword)
        
        # 按论文分组
        papers = {}
        for node in nodes:
            meta = node.node.metadata
            title = meta.get("title", "Unknown")
            
            # 检查标题是否匹配关键词
            if params.title_keyword.lower() not in title.lower():
                continue
            
            if title not in papers:
                papers[title] = {
                    "title": title,
                    "authors": meta.get("authors", "Unknown"),
                    "year": meta.get("year", ""),
                    "total_pages": meta.get("total_pages", 0),
                    "excerpts": []
                }
            
            papers[title]["excerpts"].append({
                "page": meta.get("page", 0),
                "content": node.node.text
            })
        
        if not papers:
            return json.dumps({
                "status": "not_found",
                "message": f"未找到标题包含 '{params.title_keyword}' 的论文"
            }, ensure_ascii=False, indent=2)
        
        return json.dumps({
            "status": "success",
            "papers": list(papers.values())
        }, ensure_ascii=False, indent=2)
        
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": str(e)
        }, ensure_ascii=False, indent=2)


@mcp.tool(
    name="zotero_stats",
    annotations={
        "title": "Library Statistics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def zotero_stats() -> str:
    """获取 Zotero 文献库的统计信息。
    
    返回文献库的基本统计，包括论文数量、页数、年份分布等。
    
    Returns:
        JSON 格式的统计信息
    """
    try:
        config = load_config()
        cache_dir = Path(config["paths"]["cache_dir"])
        state_file = cache_dir / "index_state.json"
        
        if not state_file.exists():
            return json.dumps({
                "status": "error",
                "message": "索引未建立"
            }, ensure_ascii=False, indent=2)
        
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
        
        files = state.get("indexed_files", {})
        total_pages = sum(f.get("pages", 0) for f in files.values())
        
        years = {}
        for f in files.values():
            y = f.get("year", "Unknown")
            years[y] = years.get(y, 0) + 1
        
        return json.dumps({
            "status": "success",
            "total_papers": len(files),
            "total_pages": total_pages,
            "last_indexed": state.get("last_indexed"),
            "papers_by_year": dict(sorted(years.items(), reverse=True))
        }, ensure_ascii=False, indent=2)
        
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": str(e)
        }, ensure_ascii=False, indent=2)


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    mcp.run()