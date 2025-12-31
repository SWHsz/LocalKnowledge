#!/usr/bin/env python3
"""
Zotero 元数据提取器

从 zotero.sqlite 数据库中提取完整的文献元数据：
- 标题、作者、年份
- DOI、期刊名、卷期页码
- 摘要、标签
- 笔记内容
"""

import sqlite3
import json
import re
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional
from datetime import datetime

import yaml
from rich.console import Console
from rich.table import Table

console = Console()


@dataclass
class PaperMetadata:
    """论文完整元数据"""
    item_key: str                          # Zotero key (8字符)
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: str = ""
    date: str = ""
    
    # 期刊信息
    journal: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    
    # 标识符
    doi: str = ""
    url: str = ""
    isbn: str = ""
    issn: str = ""
    
    # 内容
    abstract: str = ""
    tags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    
    # 类型
    item_type: str = ""  # journalArticle, conferencePaper, book, etc.
    
    # 附件
    attachments: list[str] = field(default_factory=list)  # PDF 文件名列表


class ZoteroDatabase:
    """Zotero 数据库读取器"""
    
    # 字段名映射
    FIELD_MAP = {
        'title': 'title',
        'date': 'date',
        'DOI': 'doi',
        'url': 'url',
        'abstractNote': 'abstract',
        'publicationTitle': 'journal',
        'journalAbbreviation': 'journal_abbrev',
        'volume': 'volume',
        'issue': 'issue',
        'pages': 'pages',
        'ISBN': 'isbn',
        'ISSN': 'issn',
        'publisher': 'publisher',
        'place': 'place',
        'language': 'language',
        'rights': 'rights',
    }
    
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"Zotero 数据库不存在: {db_path}")
        
        self.temp_db = None
        self.conn = None
        self._connect()
    
    def _connect(self):
        """连接数据库，如果被锁定则复制一份"""
        import shutil
        import tempfile
        
        # 先尝试只读模式直接打开
        try:
            self.conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro&nolock=1", 
                uri=True,
                timeout=5
            )
            self.conn.row_factory = sqlite3.Row
            # 测试是否可读
            self.conn.execute("SELECT 1 FROM items LIMIT 1")
            return
        except sqlite3.OperationalError:
            pass
        
        # 如果失败，复制数据库到临时文件
        console.print("[dim]Zotero 运行中，复制数据库...[/dim]")
        
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.sqlite')
        self.temp_db.close()
        
        shutil.copy2(self.db_path, self.temp_db.name)
        
        self.conn = sqlite3.connect(self.temp_db.name)
        self.conn.row_factory = sqlite3.Row
    
    def close(self):
        if self.conn:
            self.conn.close()
        if self.temp_db:
            try:
                Path(self.temp_db.name).unlink()
            except:
                pass
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.close()
    
    def get_all_items(self) -> dict[str, PaperMetadata]:
        """获取所有文献条目的元数据"""
        cursor = self.conn.cursor()
        
        # 获取所有非附件、非笔记的条目
        cursor.execute("""
            SELECT i.itemID, i.key, it.typeName
            FROM items i
            JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
            WHERE it.typeName NOT IN ('attachment', 'note', 'annotation')
            AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
        """)
        
        items = {}
        for row in cursor.fetchall():
            item_id = row['itemID']
            item_key = row['key']
            item_type = row['typeName']
            
            metadata = PaperMetadata(item_key=item_key, item_type=item_type)
            
            # 获取字段值
            self._load_fields(cursor, item_id, metadata)
            
            # 获取作者
            self._load_creators(cursor, item_id, metadata)
            
            # 获取标签
            self._load_tags(cursor, item_id, metadata)
            
            # 获取笔记
            self._load_notes(cursor, item_id, metadata)
            
            # 获取附件
            self._load_attachments(cursor, item_id, metadata)
            
            # 解析年份
            if metadata.date:
                year_match = re.search(r'(\d{4})', metadata.date)
                if year_match:
                    metadata.year = year_match.group(1)
            
            items[item_key] = metadata
        
        return items
    
    def _load_fields(self, cursor, item_id: int, metadata: PaperMetadata):
        """加载条目字段"""
        cursor.execute("""
            SELECT f.fieldName, iv.value
            FROM itemData id
            JOIN itemDataValues iv ON id.valueID = iv.valueID
            JOIN fields f ON id.fieldID = f.fieldID
            WHERE id.itemID = ?
        """, (item_id,))
        
        for row in cursor.fetchall():
            field_name = row['fieldName']
            value = row['value']
            
            if field_name == 'title':
                metadata.title = value
            elif field_name == 'date':
                metadata.date = value
            elif field_name == 'DOI':
                metadata.doi = value
            elif field_name == 'url':
                metadata.url = value
            elif field_name == 'abstractNote':
                metadata.abstract = value
            elif field_name == 'publicationTitle':
                metadata.journal = value
            elif field_name == 'volume':
                metadata.volume = value
            elif field_name == 'issue':
                metadata.issue = value
            elif field_name == 'pages':
                metadata.pages = value
            elif field_name == 'ISBN':
                metadata.isbn = value
            elif field_name == 'ISSN':
                metadata.issn = value
    
    def _load_creators(self, cursor, item_id: int, metadata: PaperMetadata):
        """加载作者信息"""
        cursor.execute("""
            SELECT c.firstName, c.lastName, ct.creatorType
            FROM itemCreators ic
            JOIN creators c ON ic.creatorID = c.creatorID
            JOIN creatorTypes ct ON ic.creatorTypeID = ct.creatorTypeID
            WHERE ic.itemID = ?
            ORDER BY ic.orderIndex
        """, (item_id,))
        
        for row in cursor.fetchall():
            first = row['firstName'] or ""
            last = row['lastName'] or ""
            
            if first and last:
                name = f"{first} {last}"
            else:
                name = last or first
            
            if name:
                metadata.authors.append(name)
    
    def _load_tags(self, cursor, item_id: int, metadata: PaperMetadata):
        """加载标签"""
        cursor.execute("""
            SELECT t.name
            FROM itemTags it
            JOIN tags t ON it.tagID = t.tagID
            WHERE it.itemID = ?
        """, (item_id,))
        
        for row in cursor.fetchall():
            metadata.tags.append(row['name'])
    
    def _load_notes(self, cursor, item_id: int, metadata: PaperMetadata):
        """加载笔记"""
        cursor.execute("""
            SELECT in2.note
            FROM itemNotes in2
            JOIN items i ON in2.itemID = i.itemID
            WHERE in2.parentItemID = ?
            AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
        """, (item_id,))
        
        for row in cursor.fetchall():
            note = row['note']
            if note:
                # 移除 HTML 标签
                clean_note = re.sub(r'<[^>]+>', '', note)
                clean_note = clean_note.strip()
                if clean_note:
                    metadata.notes.append(clean_note)
    
    def _load_attachments(self, cursor, item_id: int, metadata: PaperMetadata):
        """加载附件信息"""
        cursor.execute("""
            SELECT ia.path, i.key
            FROM itemAttachments ia
            JOIN items i ON ia.itemID = i.itemID
            WHERE ia.parentItemID = ?
            AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
        """, (item_id,))
        
        for row in cursor.fetchall():
            path = row['path']
            key = row['key']
            if path:
                # path 格式: "storage:filename.pdf"
                if path.startswith('storage:'):
                    filename = path[8:]
                    metadata.attachments.append(f"{key}/{filename}")
    
    def get_item_by_attachment_key(self, attachment_key: str) -> Optional[PaperMetadata]:
        """通过附件 key 获取父条目元数据"""
        cursor = self.conn.cursor()
        
        # 查找附件的父条目
        cursor.execute("""
            SELECT ia.parentItemID
            FROM itemAttachments ia
            JOIN items i ON ia.itemID = i.itemID
            WHERE i.key = ?
        """, (attachment_key,))
        
        row = cursor.fetchone()
        if not row or not row['parentItemID']:
            return None
        
        parent_id = row['parentItemID']
        
        # 获取父条目的 key
        cursor.execute("SELECT key FROM items WHERE itemID = ?", (parent_id,))
        row = cursor.fetchone()
        if not row:
            return None
        
        parent_key = row['key']
        
        # 获取完整元数据
        all_items = self.get_all_items()
        return all_items.get(parent_key)


def find_zotero_database() -> Optional[Path]:
    """自动查找 Zotero 数据库位置"""
    import platform
    
    system = platform.system()
    
    candidates = []
    
    if system == "Windows":
        # Windows 默认位置
        user_home = Path.home()
        candidates = [
            user_home / "Zotero" / "zotero.sqlite",
            user_home / "Documents" / "Zotero" / "zotero.sqlite",
        ]
    elif system == "Darwin":  # macOS
        user_home = Path.home()
        candidates = [
            user_home / "Zotero" / "zotero.sqlite",
        ]
    else:  # Linux
        user_home = Path.home()
        candidates = [
            user_home / "Zotero" / "zotero.sqlite",
            user_home / ".zotero" / "zotero" / "zotero.sqlite",
        ]
        
        # 查找 profile 目录
        zotero_dir = user_home / ".zotero" / "zotero"
        if zotero_dir.exists():
            for profile_dir in zotero_dir.iterdir():
                if profile_dir.is_dir() and profile_dir.name.endswith('.default'):
                    candidates.append(profile_dir / "zotero" / "zotero.sqlite")
    
    for path in candidates:
        if path.exists():
            return path
    
    return None


def load_config(config_path: str = "config.yaml") -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def extract_and_cache_metadata(config: dict, force: bool = False) -> dict[str, PaperMetadata]:
    """提取元数据并缓存"""
    cache_dir = Path(config["paths"]["cache_dir"])
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "zotero_metadata.json"
    
    # 如果缓存存在且不强制刷新
    if not force and cache_file.exists():
        console.print(f"[dim]从缓存加载元数据: {cache_file}[/dim]")
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {k: PaperMetadata(**v) for k, v in data.items()}
    
    # 查找数据库
    zotero_config = config.get("zotero", {})
    
    if "database" in zotero_config:
        db_path = Path(zotero_config["database"]).expanduser()
    else:
        data_dir = Path(zotero_config.get("data_dir", "~/Zotero")).expanduser()
        db_path = data_dir / "zotero.sqlite"
    
    if not db_path.exists():
        # 尝试自动查找
        found = find_zotero_database()
        if found:
            db_path = found
            console.print(f"[green]自动找到数据库: {db_path}[/green]")
        else:
            console.print("[red]找不到 Zotero 数据库[/red]")
            console.print("请在 config.yaml 中设置正确的路径：")
            console.print("  zotero:")
            console.print("    database: C:/Users/你的用户名/Zotero/zotero.sqlite")
            return {}
    
    console.print(f"[bold blue]读取 Zotero 数据库: {db_path}[/bold blue]")
    
    with ZoteroDatabase(db_path) as db:
        items = db.get_all_items()
    
    console.print(f"[green]提取了 {len(items)} 条文献元数据[/green]")
    
    # 缓存到文件
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump({k: asdict(v) for k, v in items.items()}, f, ensure_ascii=False, indent=2)
    
    console.print(f"[dim]已缓存到: {cache_file}[/dim]")
    
    return items


def build_attachment_mapping(items: dict[str, PaperMetadata]) -> dict[str, PaperMetadata]:
    """构建附件 key 到元数据的映射"""
    mapping = {}
    
    for item_key, metadata in items.items():
        for attachment in metadata.attachments:
            # attachment 格式: "XXXXXXXX/filename.pdf"
            att_key = attachment.split('/')[0]
            mapping[att_key] = metadata
    
    return mapping


def print_metadata_stats(items: dict[str, PaperMetadata]):
    """打印元数据统计"""
    
    # 统计
    has_abstract = sum(1 for m in items.values() if m.abstract)
    has_doi = sum(1 for m in items.values() if m.doi)
    has_journal = sum(1 for m in items.values() if m.journal)
    has_tags = sum(1 for m in items.values() if m.tags)
    has_notes = sum(1 for m in items.values() if m.notes)
    
    total = len(items)
    
    console.print("\n[bold]元数据完整度统计:[/bold]")
    
    table = Table(show_header=True)
    table.add_column("字段")
    table.add_column("数量", justify="right")
    table.add_column("比例", justify="right")
    
    table.add_row("摘要 (Abstract)", str(has_abstract), f"{has_abstract/total*100:.1f}%")
    table.add_row("DOI", str(has_doi), f"{has_doi/total*100:.1f}%")
    table.add_row("期刊名", str(has_journal), f"{has_journal/total*100:.1f}%")
    table.add_row("标签", str(has_tags), f"{has_tags/total*100:.1f}%")
    table.add_row("笔记", str(has_notes), f"{has_notes/total*100:.1f}%")
    
    console.print(table)
    
    # 按类型统计
    types = {}
    for m in items.values():
        t = m.item_type or "unknown"
        types[t] = types.get(t, 0) + 1
    
    console.print("\n[bold]文献类型统计:[/bold]")
    for t, count in sorted(types.items(), key=lambda x: -x[1]):
        console.print(f"  {t}: {count}")


def show_sample_items(items: dict[str, PaperMetadata], n: int = 3):
    """显示示例条目"""
    console.print(f"\n[bold]示例条目 (前 {n} 条):[/bold]\n")
    
    for i, (key, meta) in enumerate(list(items.items())[:n]):
        console.print(f"[cyan]── {key} ──[/cyan]")
        console.print(f"  标题: {meta.title[:60]}{'...' if len(meta.title) > 60 else ''}")
        console.print(f"  作者: {', '.join(meta.authors[:3])}{'...' if len(meta.authors) > 3 else ''}")
        console.print(f"  年份: {meta.year}")
        if meta.journal:
            console.print(f"  期刊: {meta.journal}")
        if meta.doi:
            console.print(f"  DOI: {meta.doi}")
        if meta.abstract:
            console.print(f"  摘要: {meta.abstract[:100]}...")
        if meta.tags:
            console.print(f"  标签: {', '.join(meta.tags)}")
        if meta.notes:
            console.print(f"  笔记: {len(meta.notes)} 条")
        console.print()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="提取 Zotero 元数据")
    parser.add_argument("--config", "-c", default="config.yaml", help="配置文件路径")
    parser.add_argument("--force", "-f", action="store_true", help="强制刷新缓存")
    parser.add_argument("--sample", "-s", type=int, default=3, help="显示示例数量")
    parser.add_argument("--db", type=str, help="直接指定数据库路径")
    
    args = parser.parse_args()
    
    config = load_config(args.config)
    
    if args.db:
        config.setdefault("zotero", {})["database"] = args.db
    
    items = extract_and_cache_metadata(config, force=args.force)
    
    if items:
        print_metadata_stats(items)
        show_sample_items(items, args.sample)