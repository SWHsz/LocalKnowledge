#!/usr/bin/env python3
"""
Gradio Web 界面 - 适配 Gradio 6.0
"""

import gradio as gr
from pathlib import Path

import yaml
from query import ZoteroRAG, RAGResponse
from indexer import get_index_stats, index_papers

# 全局变量
rag_engine = None
config = None


def load_config(config_path: str = "config.yaml") -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def init_rag():
    global rag_engine, config
    
    if config is None:
        config = load_config()
    
    try:
        rag_engine = ZoteroRAG(config)
        return "✅ RAG 引擎加载成功"
    except Exception as e:
        return f"❌ 加载失败: {e}"


def format_response(response: RAGResponse) -> str:
    answer = response.answer
    
    if response.citations:
        refs = ["\n\n---\n\n### 📚 References\n"]
        for i, cite in enumerate(response.citations, 1):
            year_str = f" ({cite.year})" if cite.year else ""
            refs.append(
                f"**[{i}]** {cite.title}\n"
                f"- Authors: {cite.authors}{year_str}\n"
                f"- Page: {cite.page} | Relevance: {cite.score:.0%}\n"
            )
            snippet = cite.text_snippet[:200].replace("\n", " ")
            if len(cite.text_snippet) > 200:
                snippet += "..."
            refs.append(f"> {snippet}\n\n")
        
        return answer + "\n".join(refs)
    
    return answer


def query_rag(question: str, history: list):
    global rag_engine
    
    if history is None:
        history = []
    
    if rag_engine is None:
        return history + [
            {"role": "user", "content": question},
            {"role": "assistant", "content": "❌ 请先点击 'Load Engine' 按钮加载 RAG 引擎"}
        ]
    
    if not question.strip():
        return history
    
    try:
        response = rag_engine.query(question)
        answer = format_response(response)
        return history + [
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer}
        ]
    except Exception as e:
        return history + [
            {"role": "user", "content": question},
            {"role": "assistant", "content": f"❌ 查询失败: {e}"}
        ]


def search_documents(keywords: str) -> str:
    global rag_engine
    
    if rag_engine is None:
        return "❌ 请先加载 RAG 引擎"
    
    if not keywords.strip():
        return "请输入关键词"
    
    try:
        citations = rag_engine.retrieve_only(keywords, top_k=10)
        
        if not citations:
            return "未找到相关文档"
        
        results = ["### 🔎 Search Results\n"]
        for i, cite in enumerate(citations, 1):
            year_str = f" ({cite.year})" if cite.year else ""
            results.append(
                f"**{i}. {cite.title}**\n"
                f"- {cite.authors}{year_str}\n"
                f"- Page: {cite.page} | Score: {cite.score:.0%}\n"
            )
        
        return "\n".join(results)
        
    except Exception as e:
        return f"❌ 检索失败: {e}"


def reindex_documents(force: bool = False) -> str:
    global config, rag_engine
    
    if config is None:
        config = load_config()
    
    try:
        count = index_papers(config, force=force)
        rag_engine = ZoteroRAG(config)
        return f"✅ 索引完成，处理了 {count} 篇文献"
    except Exception as e:
        return f"❌ 索引失败: {e}"


def get_stats() -> str:
    global config
    
    if config is None:
        config = load_config()
    
    try:
        stats = get_index_stats(config)
        
        year_stats = ""
        if stats.get('by_year'):
            years = sorted(stats['by_year'].items(), reverse=True)[:5]
            year_stats = "\n".join(f"  - {y}: {c} 篇" for y, c in years)
        
        return (
            f"### 📊 Knowledge Base Stats\n\n"
            f"- **Papers:** {stats['total_papers']}\n"
            f"- **Pages:** {stats['total_pages']}\n"
            f"- **Last indexed:** {stats['last_indexed'] or 'Never'}\n\n"
            f"**By Year:**\n{year_stats}"
        )
    except Exception as e:
        return f"❌ {e}"


def clear_chat():
    return []


# 创建界面
with gr.Blocks(title="Zotero RAG") as demo:
    
    gr.Markdown("# 📚 Zotero Literature RAG\n基于 Zotero 本地文献库的 RAG 问答系统")
    
    with gr.Tab("💬 Ask"):
        with gr.Row():
            with gr.Column(scale=3):
                chatbot = gr.Chatbot(
                    label="Conversation",
                    height=400,
                )
                
                with gr.Row():
                    question_input = gr.Textbox(
                        label="Your Question",
                        placeholder="Ask anything about your papers...",
                        lines=2,
                        scale=4,
                    )
                    submit_btn = gr.Button("Ask", variant="primary", scale=1)
                
                with gr.Row():
                    clear_btn = gr.Button("Clear Chat")
                
                gr.Examples(
                    examples=[
                        "What are the main findings in these papers?",
                        "Summarize the methodology used",
                        "What are the limitations mentioned?",
                        "Compare the different approaches",
                    ],
                    inputs=question_input,
                )
            
            with gr.Column(scale=1):
                load_btn = gr.Button("🔄 Load Engine", variant="secondary")
                status_text = gr.Textbox(label="Status", interactive=False)
                
                stats_md = gr.Markdown()
                refresh_stats_btn = gr.Button("📊 Refresh Stats")
        
        # 事件绑定
        submit_btn.click(
            query_rag,
            inputs=[question_input, chatbot],
            outputs=[chatbot],
        ).then(
            lambda: "",
            outputs=[question_input],
        )
        
        question_input.submit(
            query_rag,
            inputs=[question_input, chatbot],
            outputs=[chatbot],
        ).then(
            lambda: "",
            outputs=[question_input],
        )
        
        clear_btn.click(clear_chat, outputs=[chatbot])
        load_btn.click(init_rag, outputs=status_text)
        refresh_stats_btn.click(get_stats, outputs=stats_md)
    
    with gr.Tab("🔍 Search"):
        gr.Markdown("### Quick Document Search\nFind relevant documents without generating an answer")
        
        search_input = gr.Textbox(
            label="Keywords",
            placeholder="Enter keywords to search...",
        )
        search_btn = gr.Button("Search", variant="primary")
        search_results = gr.Markdown()
        
        search_btn.click(
            search_documents,
            inputs=search_input,
            outputs=search_results,
        )
    
    with gr.Tab("⚙️ Index"):
        gr.Markdown(
            "### Index Management\n\n"
            "索引器会自动扫描 Zotero storage 目录，解析 PDF 并构建向量索引。"
        )
        
        force_reindex = gr.Checkbox(label="Force full reindex (重建全部索引)")
        reindex_btn = gr.Button("🔄 Reindex", variant="secondary")
        reindex_status = gr.Textbox(label="Status", interactive=False)
        
        reindex_btn.click(
            reindex_documents,
            inputs=force_reindex,
            outputs=reindex_status,
        )


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", "-p", type=int, default=7860)
    parser.add_argument("--share", "-s", action="store_true")
    
    args = parser.parse_args()
    
    config = load_config()
    
    demo.launch(
        server_port=args.port,
        share=args.share,
    )