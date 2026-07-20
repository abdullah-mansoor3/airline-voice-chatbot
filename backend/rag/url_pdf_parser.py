import os
import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md
import pymupdf4llm
from urllib.parse import urlparse

async def parse_url_to_markdown(url: str) -> str:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        
    content_type = response.headers.get("Content-Type", "")
    if "application/pdf" in content_type.lower() or url.lower().endswith(".pdf"):
        # Save to temp file
        temp_path = f"/tmp/rag_ingest_{os.getpid()}.pdf"
        with open(temp_path, "wb") as f:
            f.write(response.content)
        try:
            markdown = pymupdf4llm.to_markdown(temp_path)
            return markdown
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
                
    # Otherwise assume HTML
    soup = BeautifulSoup(response.text, "html.parser")
    # Clean up scripts and styles
    for element in soup(["script", "style", "nav", "footer", "header"]):
        element.decompose()
    
    markdown = md(str(soup), heading_style="ATX")
    return markdown

def parse_pdf_bytes_to_markdown(pdf_bytes: bytes) -> str:
    temp_path = f"/tmp/rag_ingest_{os.getpid()}.pdf"
    with open(temp_path, "wb") as f:
        f.write(pdf_bytes)
    try:
        markdown = pymupdf4llm.to_markdown(temp_path)
        return markdown
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
