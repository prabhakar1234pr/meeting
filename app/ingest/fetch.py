"""Fetch raw text for a Knowledge Store source (github repo, docs, website)."""
import os
import re

import httpx

TEXT_EXT = {
    ".md", ".mdx", ".txt", ".rst", ".py", ".js", ".ts", ".tsx", ".jsx", ".go",
    ".java", ".rb", ".rs", ".c", ".h", ".cpp", ".cs", ".php", ".json", ".yaml",
    ".yml", ".toml", ".ini", ".sh", ".sql", ".html", ".css",
}
MAX_FILES = 40
MAX_FILE_BYTES = 120_000
_UA = {"User-Agent": "ai-teammate/1.0"}


def _gh_headers() -> dict:
    h = {"Accept": "application/vnd.github+json", **_UA}
    tok = os.getenv("GITHUB_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def parse_repo(uri: str) -> tuple[str, str]:
    uri = uri.strip().rstrip("/")
    m = re.search(r"github\.com[/:]([^/]+)/([^/]+?)(?:\.git)?$", uri)
    if m:
        return m.group(1), m.group(2)
    parts = uri.split("/")
    if len(parts) == 2:
        return parts[0], parts[1]
    raise ValueError(f"cannot parse a github owner/repo from {uri!r}")


def fetch_github(uri: str) -> str:
    owner, repo = parse_repo(uri)
    with httpx.Client(timeout=30, headers=_gh_headers(), follow_redirects=True) as c:
        meta = c.get(f"https://api.github.com/repos/{owner}/{repo}")
        meta.raise_for_status()
        branch = meta.json().get("default_branch", "main")

        tree = c.get(
            f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
        )
        tree.raise_for_status()

        picked: list[str] = []
        for b in tree.json().get("tree", []):
            if b.get("type") != "blob":
                continue
            path = b.get("path", "")
            if os.path.splitext(path)[1].lower() in TEXT_EXT and b.get("size", 0) <= MAX_FILE_BYTES:
                picked.append(path)
            if len(picked) >= MAX_FILES:
                break

        texts = []
        for path in picked:
            raw = c.get(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}")
            if raw.status_code == 200:
                texts.append(f"# FILE: {path}\n\n{raw.text}")
    return "\n\n".join(texts)


def fetch_file(path: str) -> str:
    """Extract text from an uploaded file (PDF / DOCX / Markdown / plain text)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    if ext == ".docx":
        import docx  # python-docx
        document = docx.Document(path)
        return "\n".join(p.text for p in document.paragraphs)
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def fetch_source(type: str, uri: str) -> str:
    if type == "github":
        return fetch_github(uri)
    if type == "file":
        return fetch_file(uri)
    raise ValueError(f"unsupported source type: {type!r}")
