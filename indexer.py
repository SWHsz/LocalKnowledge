#!/usr/bin/env python3
"""
Zotero 本地文献索引器

直接读取 Zotero 本地 storage 目录：
storage/
├── 3GPQD2K2/
│   └── Lee 等 - 2025 - Dropout Connects Transformers...pdf
└── ...
"""

import os
import re
import json
import hashlib
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Optional

import yaml
import fitz  # PyMuPDF
import lancedb
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from llama_index.core import (
    Document,
    VectorStoreIndex,
    StorageContext,
    Settings,
)
from llama_index.core.node_parser import SentenceSplitter
from llama_index.llms.ollama import Ollama
from llama_index.embeddings.ollama import OllamaEmbedding

console = Console()


@dataclass
class PaperInfo:
    """从文件名解析的论文信息"""
    authors: str
    year: str
    title: str
    zotero_key: str
    file_path: Path


def load_config(config_path: str = "config.yaml") -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_zotero_storage_path(config: dict) -> Path:
    zotero_config = config.get("zotero", {})
    if "storage_dir" in zotero_config:
        return Path(zotero_config["storage_dir"]).expanduser()
    data_dir = Path(zotero_config.get("data_dir", "~/Zotero")).expanduser()
    return data_dir / "storage"


def parse_filename(filename: str, zotero_key: str, file_path: Path) -> PaperInfo:
    """解析 Zotero 文件名: 作者 - 年份 - 标题.pdf"""
    name = filename.rsplit('.', 1)[0] if filename.lower().endswith('.pdf') else filename
    
    pattern = r'^(.+?)\s*[-–—]\s*(\d{4})\s*[-–—]\s*(.+)$'
    match = re.match(pattern, name)
    
    if match:
        authors = match.group(1).strip()
        year = match.group(2)
        title = match.group(3).strip()
    else:
        authors = "Unknown"
        year = ""
        title = name
    
    return PaperInfo(
        authors=authors, year=year, title=title,
        zotero_key=zotero_key, file_path=file_path,
    )


def find_all_pdfs(storage_path: Path) -> list[PaperInfo]:
    """扫描 Zotero storage 目录"""
    papers = []
    
    if not storage_path.exists():
        console.print(f"[red]Storage 目录不存在: {storage_path}[/red]")
        return papers
    
    for item_dir in storage_path.iterdir():
        if not item_dir.is_dir():
            continue
        
        zotero_key = item_dir.name
        if not re.match(r'^[A-Z0-9]{8}$', zotero_key):
            continue
        
        for pdf_file in item_dir.glob("*.pdf"):
            paper = parse_filename(pdf_file.name, zotero_key, pdf_file)
            papers.append(paper)
    
    return papers


def extract_pdf_by_pages(paper: PaperInfo) -> list[tuple[str, dict]]:
    """按页提取 PDF"""
    doc = fitz.open(paper.file_path)
    
    pages = []
    for page_num, page in enumerate(doc):
        text = page.get_text()
        if text.strip():
            meta = {
                "title": paper.title,
                "authors": paper.authors,
                "year": paper.year,
                "zotero_key": paper.zotero_key,
                "source": paper.file_path.name,
                "file_path": str(paper.file_path),
                "page": page_num + 1,
                "total_pages": len(doc),
            }
            pages.append((text, meta))
    
    doc.close()
    return pages


def get_file_hash(filepath: Path) -> str:
    hash_md5 = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def load_index_state(cache_dir: Path) -> dict:
    state_file = cache_dir / "index_state.json"
    if state_file.exists():
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"indexed_files": {}}


def save_index_state(cache_dir: Path, state: dict):
    state_file = cache_dir / "index_state.json"
    state["last_indexed"] = datetime.now().isoformat()
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def setup_llama_index(config: dict):
    """配置 LlamaIndex"""
    ollama_config = config["ollama"]
    rag_config = config["rag"]
    
    Settings.llm = Ollama(
        model=ollama_config["llm_model"],
        base_url=ollama_config["base_url"],
        request_timeout=120.0,
    )
    
    Settings.embed_model = OllamaEmbedding(
        model_name=ollama_config["embed_model"],
        base_url=ollama_config["base_url"],
    )
    
    Settings.node_parser = SentenceSplitter(
        chunk_size=rag_config["chunk_size"],
        chunk_overlap=rag_config["chunk_overlap"],
    )


def index_papers(config: dict, force: bool = False) -> int:
    """索引 Zotero 文献"""
    cache_dir = Path(config["paths"]["cache_dir"])
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    db_path = Path(config["paths"]["vector_db"])
    db_path.mkdir(parents=True, exist_ok=True)
    
    storage_path = get_zotero_storage_path(config)
    console.print(f"[bold blue]扫描 Zotero storage: {storage_path}[/bold blue]")
    
    papers = find_all_pdfs(storage_path)
    
    if not papers:
        console.print("[yellow]未找到 PDF 文件[/yellow]")
        return 0
    
    console.print(f"[green]找到 {len(papers)} 篇文献[/green]")
    
    # 配置 LlamaIndex
    setup_llama_index(config)
    
    # 加载索引状态
    state = load_index_state(cache_dir) if not force else {"indexed_files": {}}
    
    # 准备文档
    all_documents = []
    indexed_count = 0
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        task = progress.add_task("索引文献...", total=len(papers))
        
        for paper in papers:
            file_hash = get_file_hash(paper.file_path)
            
            if not force and paper.zotero_key in state["indexed_files"]:
                if state["indexed_files"][paper.zotero_key].get("hash") == file_hash:
                    progress.advance(task)
                    continue
            
            progress.update(task, description=f"{paper.title[:40]}...")
            
            try:
                pages = extract_pdf_by_pages(paper)
                
                for text, meta in pages:
                    doc = Document(
                        text=text,
                        metadata=meta,
                        excluded_llm_metadata_keys=["file_path"],
                        excluded_embed_metadata_keys=["file_path"],
                    )
                    all_documents.append(doc)
                
                state["indexed_files"][paper.zotero_key] = {
                    "hash": file_hash,
                    "indexed_at": datetime.now().isoformat(),
                    "title": paper.title,
                    "authors": paper.authors,
                    "year": paper.year,
                    "pages": len(pages),
                }
                indexed_count += 1
                
                console.print(f"  [green]✓[/green] {paper.authors} ({paper.year}) - {paper.title[:50]}")
                
            except Exception as e:
                console.print(f"  [red]✗[/red] {paper.file_path.name}: {e}")
            
            progress.advance(task)
    
    # 构建索引（使用简单的本地存储）
    if all_documents:
        console.print(f"\n[bold blue]构建向量索引 ({len(all_documents)} 个文档块)...[/bold blue]")
        
        index = VectorStoreIndex.from_documents(
            all_documents,
            show_progress=True,
        )
        
        # 持久化到本地
        index.storage_context.persist(persist_dir=str(db_path))
        
        console.print("[bold green]索引构建完成！[/bold green]")
    
    save_index_state(cache_dir, state)
    console.print(f"\n[bold green]完成！索引了 {indexed_count} 篇文献[/bold green]")
    
    return indexed_count


def get_index_stats(config: dict) -> dict:
    cache_dir = Path(config["paths"]["cache_dir"])
    state = load_index_state(cache_dir)
    
    files = state.get("indexed_files", {})
    total_pages = sum(f.get("pages", 0) for f in files.values())
    
    years = {}
    for f in files.values():
        y = f.get("year", "Unknown")
        years[y] = years.get(y, 0) + 1
    
    return {
        "total_papers": len(files),
        "total_pages": total_pages,
        "by_year": years,
        "last_indexed": state.get("last_indexed"),
    }


def list_indexed_papers(config: dict):
    cache_dir = Path(config["paths"]["cache_dir"])
    state = load_index_state(cache_dir)
    files = state.get("indexed_files", {})
    
    sorted_papers = sorted(
        files.items(),
        key=lambda x: (x[1].get("year", "0000"), x[1].get("title", "")),
        reverse=True
    )
    
    console.print(f"\n[bold]已索引 {len(files)} 篇文献:[/bold]\n")
    
    current_year = None
    for key, info in sorted_papers:
        year = info.get("year", "Unknown")
        if year != current_year:
            current_year = year
            console.print(f"\n[bold cyan]── {year} ──[/bold cyan]")
        
        authors = info.get("authors", "Unknown")
        title = info.get("title", "Unknown")
        console.print(f"  • {authors} - {title[:60]}{'...' if len(title) > 60 else ''}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="索引 Zotero 本地文献")
    parser.add_argument("--config", "-c", default="config.yaml", help="配置文件路径")
    parser.add_argument("--force", "-f", action="store_true", help="强制重新索引")
    parser.add_argument("--stats", "-s", action="store_true", help="显示统计")
    parser.add_argument("--list", "-l", action="store_true", help="列出已索引文献")
    
    args = parser.parse_args()
    config = load_config(args.config)
    
    if args.stats:
        stats = get_index_stats(config)
        console.print("[bold]索引统计:[/bold]")
        console.print(f"  文献数: {stats['total_papers']}")
        console.print(f"  总页数: {stats['total_pages']}")
        console.print(f"  最后索引: {stats['last_indexed']}")
        if stats['by_year']:
            console.print("\n  按年份:")
            for year, count in sorted(stats['by_year'].items(), reverse=True):
                console.print(f"    {year}: {count} 篇")
    elif args.list:
        list_indexed_papers(config)
    else:
        index_papers(config, force=args.force)