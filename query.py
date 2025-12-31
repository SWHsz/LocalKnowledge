#!/usr/bin/env python3
"""
RAG 查询引擎 - 支持带引用来源的问答
"""

from pathlib import Path
from dataclasses import dataclass

import yaml
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from llama_index.core import (
    VectorStoreIndex,
    StorageContext,
    Settings,
    load_index_from_storage,
    get_response_synthesizer,
)
from llama_index.core.retrievers import VectorIndexRetriever
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.postprocessor import SimilarityPostprocessor
from llama_index.core.response_synthesizers import ResponseMode
from llama_index.llms.ollama import Ollama
from llama_index.embeddings.ollama import OllamaEmbedding

console = Console()


@dataclass
class Citation:
    """引用信息"""
    source: str
    page: int
    title: str
    authors: str
    year: str
    text_snippet: str
    score: float


@dataclass
class RAGResponse:
    """RAG 响应"""
    answer: str
    citations: list[Citation]
    
    def format_markdown(self) -> str:
        lines = [self.answer, "", "---", "", "**References:**", ""]
        
        for i, cite in enumerate(self.citations, 1):
            year_str = f" ({cite.year})" if cite.year else ""
            lines.append(
                f"{i}. **{cite.title}**\n"
                f"   {cite.authors}{year_str} — Page {cite.page} (relevance: {cite.score:.2f})"
            )
            snippet = cite.text_snippet[:200] + "..." if len(cite.text_snippet) > 200 else cite.text_snippet
            lines.append(f"   > {snippet}")
            lines.append("")
        
        return "\n".join(lines)


def load_config(config_path: str = "config.yaml") -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_llama_index(config: dict):
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


class ZoteroRAG:
    """Zotero 文献 RAG 引擎"""
    
    def __init__(self, config: dict):
        self.config = config
        self.rag_config = config["rag"]
        
        setup_llama_index(config)
        self.index = self._load_index()
        self.query_engine = self._create_query_engine()
    
    def _load_index(self):
        db_path = Path(self.config["paths"]["vector_db"])
        
        if not db_path.exists():
            raise FileNotFoundError(
                f"向量数据库不存在: {db_path}\n"
                "请先运行 python indexer.py 构建索引"
            )
        
        storage_context = StorageContext.from_defaults(persist_dir=str(db_path))
        return load_index_from_storage(storage_context)
    
    def _create_query_engine(self):
        retriever = VectorIndexRetriever(
            index=self.index,
            similarity_top_k=self.rag_config["top_k"],
        )
        
        postprocessor = SimilarityPostprocessor(
            similarity_cutoff=self.rag_config["similarity_threshold"]
        )
        
        response_synthesizer = get_response_synthesizer(
            response_mode=ResponseMode.COMPACT,
        )
        
        return RetrieverQueryEngine(
            retriever=retriever,
            node_postprocessors=[postprocessor],
            response_synthesizer=response_synthesizer,
        )
    
    def query(self, question: str) -> RAGResponse:
        response = self.query_engine.query(question)
        
        citations = []
        for node in response.source_nodes:
            meta = node.node.metadata
            citations.append(Citation(
                source=meta.get("source", "Unknown"),
                page=meta.get("page", 0),
                title=meta.get("title", "Unknown"),
                authors=meta.get("authors", "Unknown"),
                year=meta.get("year", ""),
                text_snippet=node.node.text,
                score=node.score if node.score else 0.0,
            ))
        
        return RAGResponse(answer=str(response), citations=citations)
    
    def retrieve_only(self, question: str, top_k: int = 5) -> list[Citation]:
        retriever = VectorIndexRetriever(
            index=self.index,
            similarity_top_k=top_k,
        )
        
        nodes = retriever.retrieve(question)
        
        citations = []
        for node in nodes:
            meta = node.node.metadata
            citations.append(Citation(
                source=meta.get("source", "Unknown"),
                page=meta.get("page", 0),
                title=meta.get("title", "Unknown"),
                authors=meta.get("authors", "Unknown"),
                year=meta.get("year", ""),
                text_snippet=node.node.text,
                score=node.score if node.score else 0.0,
            ))
        
        return citations


def print_response(response: RAGResponse):
    console.print(Panel(
        Markdown(response.answer),
        title="[bold green]Answer[/bold green]",
        border_style="green",
    ))
    
    if response.citations:
        table = Table(title="References", show_header=True)
        table.add_column("#", style="dim", width=3)
        table.add_column("Title", style="cyan", max_width=40)
        table.add_column("Authors")
        table.add_column("Year")
        table.add_column("Page", justify="right")
        table.add_column("Score", justify="right")
        
        for i, cite in enumerate(response.citations, 1):
            table.add_row(
                str(i),
                cite.title[:40] + "..." if len(cite.title) > 40 else cite.title,
                cite.authors[:20] + "..." if len(cite.authors) > 20 else cite.authors,
                cite.year or "-",
                str(cite.page),
                f"{cite.score:.2f}",
            )
        
        console.print(table)


def interactive_mode(rag: ZoteroRAG):
    console.print(Panel(
        "[bold]Zotero RAG 交互模式[/bold]\n\n"
        "输入问题进行查询，输入 'quit' 或 'exit' 退出\n"
        "输入 'find: <关键词>' 仅检索不生成回答",
        border_style="blue",
    ))
    
    while True:
        try:
            question = console.input("\n[bold cyan]Question:[/bold cyan] ").strip()
            
            if not question:
                continue
            
            if question.lower() in ("quit", "exit", "q"):
                console.print("[yellow]再见！[/yellow]")
                break
            
            if question.lower().startswith("find:"):
                keywords = question[5:].strip()
                console.print(f"[dim]正在检索: {keywords}[/dim]")
                citations = rag.retrieve_only(keywords)
                
                if citations:
                    table = Table(title="Related Documents")
                    table.add_column("Title")
                    table.add_column("Authors")
                    table.add_column("Page")
                    table.add_column("Score")
                    
                    for cite in citations:
                        table.add_row(
                            cite.title[:50],
                            cite.authors[:20],
                            str(cite.page),
                            f"{cite.score:.2f}"
                        )
                    
                    console.print(table)
                else:
                    console.print("[yellow]未找到相关文档[/yellow]")
                continue
            
            console.print("[dim]正在思考...[/dim]")
            response = rag.query(question)
            print_response(response)
            
        except KeyboardInterrupt:
            console.print("\n[yellow]已中断[/yellow]")
            break
        except Exception as e:
            console.print(f"[red]错误: {e}[/red]")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="RAG 查询引擎")
    parser.add_argument("--config", "-c", default="config.yaml", help="配置文件路径")
    parser.add_argument("--query", "-q", type=str, help="直接查询问题")
    parser.add_argument("--find", "-f", type=str, help="仅检索相关文档")
    
    args = parser.parse_args()
    config = load_config(args.config)
    
    console.print("[dim]正在加载 RAG 引擎...[/dim]")
    rag = ZoteroRAG(config)
    console.print("[green]RAG 引擎加载完成[/green]")
    
    if args.find:
        citations = rag.retrieve_only(args.find)
        for cite in citations:
            console.print(f"• {cite.title} (p.{cite.page}) - {cite.score:.2f}")
    elif args.query:
        response = rag.query(args.query)
        print_response(response)
    else:
        interactive_mode(rag)