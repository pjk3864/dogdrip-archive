"""Archive Dogdrip popular posts as standalone GitHub Pages files."""

import base64
import hashlib
import html
import json
import mimetypes
import os
import re
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait


DOGDRIP_URL = "https://www.dogdrip.net/?mid=dogdrip&sort_index=popular"
DOGDRIP_FALLBACK_URL = "https://www.dogdrip.net/dogdrip?sort_index=popular"
DOGDRIP_ORIGIN = "https://www.dogdrip.net"
GITHUB_USERNAME = "pjk3864"
REPO_NAME = "dogdrip-archive"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
MAX_ASSET_BYTES = 15 * 1024 * 1024
HISTORICAL_ARCHIVE_TARGET = 400
POSTS_PER_LIST_PAGE = 20
PAGER_WINDOW_SIZE = 10
MAX_POPULAR_PAGES = 9
COMMENT_ARCHIVE_VERSION = 2
POPULAR_PAGE_DELAY_SECONDS = 2
MANUAL_URLS_FILE = "manual_urls.txt"
DOGDRIP_DOCUMENT_DELAY_SECONDS = 1.5
DOGDRIP_RETRY_DELAY_SECONDS = 60
_last_dogdrip_document_request = 0.0
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
DARK_POST_STYLE = '''<style id="archive-dark-theme">
body{background:#111317!important;color:#e9edf2!important}main{background:#202329!important}.back,.source,.article a{color:#4ba3ff!important}h1{color:#f6f8fa!important}.meta{color:#9aa3ae!important;border-color:#363b43!important}.archived-comments{border-color:#363b43!important}.comment{border-color:#30353c!important}.comment-meta{color:#9aa3ae!important}.comment-author{color:#e9edf2!important}
</style>'''
READ_LIST_SCRIPT = '''<script id="archive-read-list">
(() => {
  const key = "dogdrip-archive-read-posts";
  try {
    const read = new Set(JSON.parse(localStorage.getItem(key) || "[]"));
    document.querySelectorAll(".post-row[data-post-id]").forEach((row) => {
      if (read.has(row.dataset.postId)) row.classList.add("is-read");
      row.addEventListener("click", () => {
        read.add(row.dataset.postId);
        localStorage.setItem(key, JSON.stringify([...read]));
      });
    });
  } catch (_) {}
})();
</script>'''
READ_POST_SCRIPT = '''<script id="archive-read-post">
(() => {
  const key = "dogdrip-archive-read-posts";
  try {
    const read = new Set(JSON.parse(localStorage.getItem(key) || "[]"));
    read.add(document.body.dataset.postId);
    localStorage.setItem(key, JSON.stringify([...read]));
  } catch (_) {}
})();
</script>'''


def _class_contains(expected):
    return lambda classes: classes and expected in classes


def canonical_post_url(url):
    """Remove list-page query parameters that can trigger an access block."""
    match = re.search(r"/dogdrip/(\d+)", url)
    if not match:
        return url
    return f"{DOGDRIP_ORIGIN}/dogdrip/{match.group(1)}"


def _github_headers():
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN 환경 변수를 설정하세요.")
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _github_url(path):
    return f"https://api.github.com/repos/{GITHUB_USERNAME}/{REPO_NAME}/contents/{path}"


def github_get_file(path, decode=True):
    """Return (content bytes or None, SHA or None) for a repository file."""
    response = requests.get(_github_url(path), headers=_github_headers(), timeout=30)
    if response.status_code == 404:
        return None, None
    response.raise_for_status()
    payload = response.json()
    content = base64.b64decode(payload["content"]) if decode else None
    return content, payload["sha"]


def github_put_file(path, content, message, sha=None):
    """Create or update one repository file through GitHub's Contents API."""
    # GitHub requires the current file SHA when replacing an existing file.
    # Look it up here so every caller safely handles both new and old files.
    if sha is None:
        _, sha = github_get_file(path, decode=False)
    payload = {
        "message": message,
        "content": base64.b64encode(content).decode("ascii"),
        "branch": "main",
    }
    if sha:
        payload["sha"] = sha
    response = requests.put(_github_url(path), headers=_github_headers(), json=payload, timeout=60)
    if not response.ok:
        print(f"GitHub 저장 오류 ({response.status_code}) - {path}")
        print(response.text)
    response.raise_for_status()


def _open_list_browser():
    """Open a normal Chrome window for list pages that block plain HTTP clients."""
    options = Options()
    options.add_argument("--window-size=1280,900")
    if os.environ.get("CI"):
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
    try:
        return webdriver.Chrome(options=options)
    except Exception as error:
        raise RuntimeError(
            "Chrome 목록 수집을 시작하지 못했습니다. "
            "먼저 `py -3.11 -m pip install -r requirements.txt`를 실행하세요."
        ) from error


def _get_popular_page_html(browser, page_number):
    """Load one popular-posts page through the same Chrome engine as a user."""
    browser.get(f"{DOGDRIP_FALLBACK_URL}&page={page_number}")
    try:
        WebDriverWait(browser, 20).until(
            lambda current: current.find_elements(
                "css selector", "a.ed.title-link[data-document-srl]"
            )
        )
    except Exception as error:
        raise RuntimeError(f"개드립 인기글 {page_number}페이지를 열지 못했습니다.") from error
    return browser.page_source


def get_manual_posts():
    """Load article URLs copied by the user from pages that block automation."""
    if not os.path.exists(MANUAL_URLS_FILE):
        return []

    with open(MANUAL_URLS_FILE, "r", encoding="utf-8") as handle:
        urls = [line.strip() for line in handle if line.strip()]

    posts = []
    seen_ids = set()
    for source_url in urls:
        match = re.search(r"/dogdrip/(\d+)", source_url)
        if not match or match.group(1) in seen_ids:
            continue
        document_id = match.group(1)
        source_url = canonical_post_url(source_url)
        # Do not request every URL merely to read a title. The real archive request
        # below reads each post once, at a safe pace, and replaces this temporary title.
        posts.append(
            {
                "id": document_id,
                "title": f"개드립 글 {document_id}",
                "source_url": source_url,
                "thumbnail_url": "",
                "votes": "0",
                "comments": "0",
                "published": "",
            }
        )
        seen_ids.add(document_id)
    return posts


def get_popular_posts(limit=None, after_id=None):
    """Collect popular posts, scanning until a page has nothing newer than after_id."""
    posts = []
    seen_ids = set()
    browser = _open_list_browser()
    try:
        page_number = 1
        while page_number <= MAX_POPULAR_PAGES:
            if limit is not None and len(posts) >= limit:
                break
            if page_number > 1:
                time.sleep(POPULAR_PAGE_DELAY_SECONDS)
            soup = BeautifulSoup(_get_popular_page_html(browser, page_number), "html.parser")
            page_posts = 0
            page_has_new_post = False
            for anchor in soup.select("a.ed.title-link[data-document-srl]"):
                document_id = anchor.get("data-document-srl")
                title = anchor.get_text(strip=True)
                link = anchor.get("href")
                row = anchor.find_parent("li", class_=_class_contains("webzine"))
                if not document_id or document_id in seen_ids or not title or not link or row is None:
                    continue

                if after_id is not None and int(document_id) > after_id:
                    page_has_new_post = True

                comments = anchor.find_next_sibling("span")
                thumbnail = row.select_one("img.ed.webzine-thumbnail[src]")
                vote_nodes = row.select(".list-meta span.ed.text-xxsmall.text-primary")
                votes = next(
                    (node.get_text(strip=True) for node in reversed(vote_nodes) if node.get_text(strip=True)),
                    "0",
                )
                time_node = row.select_one(".list-meta span.ed.text-muted")
                posts.append(
                    {
                        "id": document_id,
                        "title": title,
                        "source_url": urljoin(DOGDRIP_ORIGIN, link),
                        "thumbnail_url": urljoin(DOGDRIP_ORIGIN, thumbnail["src"]) if thumbnail else "",
                        "votes": votes,
                        "comments": comments.get_text(strip=True) if comments else "0",
                        "published": time_node.get_text(" ", strip=True) if time_node else "",
                    }
                )
                seen_ids.add(document_id)
                page_posts += 1
                if limit is not None and len(posts) >= limit:
                    break
            if page_posts == 0 or (after_id is not None and not page_has_new_post):
                break
            page_number += 1
    finally:
        browser.quit()
    return posts


def _make_links_absolute(element):
    """Keep copied media and links usable from a GitHub Pages page."""
    for child in element.select("img[src], a[href], video[src], source[src]"):
        attribute = "href" if child.name == "a" else "src"
        value = child.get(attribute)
        if value:
            child[attribute] = urljoin(DOGDRIP_ORIGIN, value)
        if child.name == "img":
            child["loading"] = "lazy"


def _get_dogdrip_document(link):
    """Request one article at a measured pace to avoid stressing the source site."""
    global _last_dogdrip_document_request
    for attempt in range(3):
        wait_seconds = DOGDRIP_DOCUMENT_DELAY_SECONDS - (
            time.monotonic() - _last_dogdrip_document_request
        )
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        response = requests.get(canonical_post_url(link), headers=REQUEST_HEADERS, timeout=30)
        _last_dogdrip_document_request = time.monotonic()
        if response.status_code != 429:
            response.raise_for_status()
            return response
        print(f"글 요청이 잠시 제한되었습니다. {DOGDRIP_RETRY_DELAY_SECONDS}초 뒤 재시도합니다...")
        time.sleep(DOGDRIP_RETRY_DELAY_SECONDS)
    response.raise_for_status()


def get_post_snapshot(link):
    """Fetch one article, its title, body, and every visible comment in one request."""
    response = _get_dogdrip_document(link)
    soup = BeautifulSoup(response.content, "html.parser")
    title_node = soup.select_one('meta[property="og:title"]')
    title = title_node.get("content", "").strip() if title_node else ""
    title = re.sub(r"\s*[-|]\s*DogDrip.*$", "", title, flags=re.IGNORECASE)
    content = soup.select_one("div.rhymix_content.xe_content[class^='document_']")
    if content is None:
        content_html = "<p>본문을 불러오지 못했습니다.</p>"
    else:
        _make_links_absolute(content)
        content_html = str(content)

    comments = []
    for item in soup.select("#commentbox .comment-item"):
        body = item.select_one(".rhymix_content.xe_content")
        author = item.select_one(".comment-bar h6")
        published = item.select_one(".comment-bar .text-muted")
        if body is None:
            continue
        comments.append(
            {
                "author": author.get_text(" ", strip=True) if author else "익명",
                "published": published.get_text(" ", strip=True) if published else "",
                "body": body.get_text("\n", strip=True),
            }
        )
    return {"title": title, "content": content_html, "comments": comments}


def get_post_details(link):
    """Fetch an article body plus every comment visible on its source page."""
    snapshot = get_post_snapshot(link)
    return snapshot["content"], snapshot["comments"]


def get_post_content(link):
    """Compatibility helper for callers that only need the article body."""
    content, _ = get_post_details(link)
    return content


def _extension_for(url, content_type):
    path_extension = os.path.splitext(urlparse(url).path)[1].lower()
    if path_extension and len(path_extension) <= 8:
        return path_extension
    mime = (content_type or "").split(";", 1)[0].strip()
    return mimetypes.guess_extension(mime) or ".bin"


def archive_asset(url, post_id):
    """Copy one media asset to the repository and return its repository path.

    Oversized or failed assets remain remote so a single media file cannot break the run.
    """
    if not url or url.startswith("data:"):
        return ""

    try:
        response = requests.get(url, headers=REQUEST_HEADERS, stream=True, timeout=45)
        response.raise_for_status()
        declared_size = int(response.headers.get("content-length", "0"))
        if declared_size > MAX_ASSET_BYTES:
            print(f"큰 파일은 원본으로 유지: {url}")
            return ""

        chunks = []
        size = 0
        for chunk in response.iter_content(chunk_size=128 * 1024):
            if not chunk:
                continue
            size += len(chunk)
            if size > MAX_ASSET_BYTES:
                print(f"큰 파일은 원본으로 유지: {url}")
                return ""
            chunks.append(chunk)
        data = b"".join(chunks)
    except requests.RequestException as error:
        print(f"미디어 보관 실패: {error}")
        return ""

    digest = hashlib.sha256(data).hexdigest()[:16]
    extension = _extension_for(url, response.headers.get("content-type"))
    path = f"assets/{post_id}/{digest}{extension}"
    _, sha = github_get_file(path, decode=False)
    if sha is None:
        github_put_file(path, data, f"Archive asset for {post_id}")
    return path


def localize_article_content(content_html, post_id):
    """Replace article image and video URLs with archived local asset paths."""
    soup = BeautifulSoup(content_html, "html.parser")
    for element in soup.select("img[src], video[src], source[src]"):
        source_url = element.get("src")
        asset_path = archive_asset(source_url, post_id)
        if asset_path:
            element["src"] = f"../{asset_path}"
    return str(soup)


def list_page_path(page_number):
    return "index.html" if page_number == 1 else f"page-{page_number}.html"


def generate_index_html(entries, page_number=1):
    """Generate one 20-item archive list page with simple page navigation."""
    total_pages = max(1, (len(entries) + POSTS_PER_LIST_PAGE - 1) // POSTS_PER_LIST_PAGE)
    start = (page_number - 1) * POSTS_PER_LIST_PAGE
    page_entries = entries[start : start + POSTS_PER_LIST_PAGE]
    rows = []
    for entry in page_entries:
        rows.append(
            f'''<a class="post-row" data-post-id="{html.escape(entry["id"], quote=True)}" href="posts/{entry["id"]}.html">
  <span class="row-title">{html.escape(entry["title"])}</span>
</a>'''
        )

    pager_group_start = ((page_number - 1) // PAGER_WINDOW_SIZE) * PAGER_WINDOW_SIZE + 1
    pager_group_end = min(pager_group_start + PAGER_WINDOW_SIZE - 1, total_pages)
    pager_parts = []
    if pager_group_start > 1:
        pager_parts.append(
            f'''<a class="page-link page-shift" href="{list_page_path(pager_group_start - 1)}" aria-label="이전 페이지 묶음">이전</a>'''
        )
    pager_parts.extend(
        f'''<a class="page-link{' current' if number == page_number else ''}" href="{list_page_path(number)}"{' aria-current="page"' if number == page_number else ''}>{number}</a>'''
        for number in range(pager_group_start, pager_group_end + 1)
    )
    if pager_group_end < total_pages:
        pager_parts.append(
            f'''<a class="page-link page-shift" href="{list_page_path(pager_group_end + 1)}" aria-label="다음 페이지 묶음">다음</a>'''
        )
    pager = "".join(pager_parts)
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f'''<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/css/all.min.css" referrerpolicy="no-referrer">
  <title>개드립 인기글 아카이브</title>
  <style>
    :root {{ --blue:#4ba3ff; --ink:#e9edf2; --muted:#9aa3ae; --line:#363b43; --panel:#202329; --panel-deep:#17191d; }}
    * {{ box-sizing:border-box; }} body {{ margin:0; background:#111317; color:var(--ink); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans KR",sans-serif; }}
    .site {{ max-width:960px; min-height:100vh; margin:auto; background:var(--panel); }}
    header {{ display:flex; align-items:center; gap:12px; height:54px; padding:0 20px; background:var(--panel-deep); border-bottom:1px solid var(--line); }}
    h1 {{ margin:0; font-size:17px; }} .updated {{ margin:0; padding:13px 20px; color:var(--muted); border-bottom:1px solid var(--line); font-size:12px; }}
    .post-row {{ display:block; min-height:62px; padding:19px 20px; color:inherit; text-decoration:none; border-bottom:1px solid var(--line); transition:background .15s; }}
    .post-row:hover, .post-row:focus-visible {{ background:#292e36; outline:none; }} .post-row.is-read .row-title {{ color:#777f8a; }} .row-title {{ display:block; overflow:hidden; font-size:18px; line-height:1.35; text-overflow:ellipsis; white-space:nowrap; }}
    .pager {{ display:flex; flex-wrap:wrap; justify-content:center; gap:6px; padding:24px 16px; }} .page-link {{ min-width:34px; padding:8px 10px; border:1px solid var(--line); border-radius:5px; color:var(--ink); text-align:center; text-decoration:none; font-size:14px; }} .page-link.page-shift {{ min-width:52px; }} .page-link:hover,.page-link:focus-visible {{ border-color:var(--blue); color:var(--blue); outline:none; }} .page-link.current {{ background:var(--blue); border-color:var(--blue); color:#fff; pointer-events:none; }}
    @media(max-width:640px) {{ header {{ height:50px; padding:0 14px; }} .updated {{ padding:11px 14px; }} .post-row {{ min-height:55px; padding:16px 14px; }} .row-title {{ font-size:16px; }} .pager {{ gap:5px; padding:20px 10px; }} .page-link {{ min-width:32px; padding:7px 8px; }} }}
  </style>
</head>
<body><main class="site"><header><i class="fa-solid fa-list" aria-hidden="true"></i><h1>개드립 인기글 아카이브</h1></header><p class="updated">보관된 글 {len(entries)}개 · {page_number}/{total_pages} 페이지 · 마지막 수집 {updated_at}</p>{''.join(rows)}<nav class="pager" aria-label="목록 페이지">{pager}</nav></main>{READ_LIST_SCRIPT}</body>
</html>'''


def build_list_pages(entries):
    """Publish the root list and follow-up pages, 20 archived posts per page."""
    total_pages = max(1, (len(entries) + POSTS_PER_LIST_PAGE - 1) // POSTS_PER_LIST_PAGE)
    for page_number in range(1, total_pages + 1):
        path = list_page_path(page_number)
        _, sha = github_get_file(path, decode=False)
        github_put_file(
            path,
            generate_index_html(entries, page_number).encode("utf-8"),
            f"Build archive list page {page_number}/{total_pages}",
            sha,
        )


def generate_post_html(post):
    """Generate one self-contained archived article page."""
    comments_html = generate_comments_html(post.get("archived_comments", []))
    return f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/css/all.min.css" referrerpolicy="no-referrer"><title>{html.escape(post["title"])} · 개드립 아카이브</title>
<style>body{{margin:0;background:#111317;color:#e9edf2;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans KR",sans-serif}}main{{max-width:960px;min-height:100vh;margin:auto;padding:28px 24px 48px;background:#202329}}.back,.source{{color:#4ba3ff;text-decoration:none;font-size:14px}}h1{{margin:20px 0 0;color:#f6f8fa;font-size:25px;line-height:1.42}}.meta{{margin:10px 0 20px;color:#9aa3ae;border-bottom:1px solid #363b43;padding-bottom:18px;font-size:13px}}.article{{font-size:16px;line-height:1.72;overflow-wrap:anywhere}}.article img,.article video{{display:block;max-width:100%;height:auto;margin:10px auto}}.article a{{color:#4ba3ff}}.archived-comments{{margin-top:42px;border-top:1px solid #363b43;padding-top:22px}}.archived-comments h2{{margin:0 0 14px;font-size:18px}}.comment{{padding:14px 0;border-top:1px solid #30353c}}.comment:first-of-type{{border-top:0}}.comment-meta{{margin:0 0 7px;color:#9aa3ae;font-size:13px}}.comment-author{{color:#e9edf2;font-weight:600}}.comment-body{{margin:0;white-space:pre-wrap;font-size:15px;line-height:1.6;overflow-wrap:anywhere}}@media(max-width:640px){{main{{padding:22px 16px 40px}}h1{{font-size:22px}}}}</style></head>
<body data-post-id="{html.escape(post["id"], quote=True)}"><main><a class="back" href="../index.html"><i class="fa-solid fa-arrow-left" aria-hidden="true"></i> 목록으로</a><h1>{html.escape(post["title"])}</h1><p class="meta"><i class="fa-regular fa-thumbs-up" aria-hidden="true"></i> {html.escape(post["votes"])} · <i class="fa-regular fa-comment" aria-hidden="true"></i> {html.escape(post["comments"])} · {html.escape(post["published"])} · <a class="source" href="{html.escape(post["source_url"], quote=True)}" target="_blank" rel="noopener noreferrer">원문</a></p><article class="article">{post["content"]}</article>{comments_html}</main>{READ_POST_SCRIPT}</body></html>'''


def generate_comments_html(comments):
    """Render copied text comments without copying Dogdrip page controls or scripts."""
    if comments:
        rows = "".join(
            f'''<div class="comment"><p class="comment-meta"><span class="comment-author">{html.escape(comment["author"])}</span>{(" · " + html.escape(comment["published"])) if comment["published"] else ""}</p><p class="comment-body">{html.escape(comment["body"])}</p></div>'''
            for comment in comments
        )
        description = f"수집 당시 댓글 {len(comments)}개"
    else:
        rows = '<p class="comment-body">수집할 댓글이 없습니다.</p>'
        description = "수집 당시 댓글 없음"
    return f'''<!-- archived-comments:start --><section class="archived-comments"><h2><i class="fa-regular fa-comments" aria-hidden="true"></i> 댓글</h2><p class="comment-meta">{description}</p>{rows}</section><!-- archived-comments:end -->'''


def add_comments_to_archived_page(page_html, comments):
    """Replace (or add) the comment section in an already archived post page."""
    section = generate_comments_html(comments)
    pattern = r"<!-- archived-comments:start -->.*?<!-- archived-comments:end -->"
    if re.search(pattern, page_html, flags=re.DOTALL):
        return re.sub(pattern, section, page_html, flags=re.DOTALL)
    return page_html.replace("</main>", f"{section}</main>", 1)


def apply_dark_theme_to_existing_posts(entries):
    """Upgrade old article files with the dark theme and local read tracking."""
    updated = 0
    for entry in entries:
        path = f"posts/{entry['id']}.html"
        page, page_sha = github_get_file(path)
        if page is None:
            continue
        page_html = page.decode("utf-8")
        needs_dark_theme = 'id="archive-dark-theme"' not in page_html
        needs_read_tracking = 'id="archive-read-post"' not in page_html
        if not needs_dark_theme and not needs_read_tracking:
            continue
        updated_page = page_html
        if needs_dark_theme:
            updated_page = updated_page.replace("</head>", f"{DARK_POST_STYLE}</head>", 1)
        if needs_read_tracking:
            post_id = html.escape(entry["id"], quote=True)
            updated_page = updated_page.replace(
                "<body>", f'<body data-post-id="{post_id}">', 1
            )
            updated_page = updated_page.replace("</body>", f"{READ_POST_SCRIPT}</body>", 1)
        github_put_file(
            path,
            updated_page.encode("utf-8"),
            f"Upgrade archive page {entry['id']}",
            page_sha,
        )
        updated += 1
    return updated


def load_archive():
    content, sha = github_get_file("archive.json")
    if content is None:
        return [], None
    return json.loads(content.decode("utf-8")), sha


def refresh_comments_for_existing_posts(entries):
    """Upgrade legacy 10-comment pages to archive all comments once."""
    updated = 0
    for entry in entries:
        if entry.get("comment_archive_version") == COMMENT_ARCHIVE_VERSION:
            continue
        print(f"전체 댓글 보관 중: {entry['title'][:30]}...")
        try:
            _, comments = get_post_details(entry["source_url"])
            page, page_sha = github_get_file(f"posts/{entry['id']}.html")
            if page is None:
                print("기존 글 파일을 찾지 못했습니다.")
                continue
            updated_page = add_comments_to_archived_page(page.decode("utf-8"), comments)
            github_put_file(
                f"posts/{entry['id']}.html",
                updated_page.encode("utf-8"),
                f"Archive comments for {entry['id']}",
                page_sha,
            )
            # The post page owns the comment text. Keep the manifest compact.
            entry.pop("archived_comments", None)
            entry["archived_comment_count"] = len(comments)
            entry["comment_archive_version"] = COMMENT_ARCHIVE_VERSION
            updated += 1
        except requests.RequestException as error:
            print(f"댓글 보관 실패: {error}")
    return updated


def archive_posts():
    """Preserve every popular post created after the newest archived post."""
    entries, archive_sha = load_archive()
    known_ids = {entry["id"] for entry in entries}
    new_entries = []

    manual_posts = get_manual_posts()
    if entries:
        newest_archived_id = max(int(entry["id"]) for entry in entries)
        print(f"마지막 보관 글 {newest_archived_id} 이후의 새 글을 확인합니다.")
        candidates = [
            post
            for post in get_popular_posts(after_id=newest_archived_id)
            if int(post["id"]) > newest_archived_id and post["id"] not in known_ids
        ]
        print(f"새로 생성된 글 {len(candidates)}개를 확인했습니다.")
    elif manual_posts:
        print(f"수동 목록에서 {len(manual_posts)}개 글을 확인했습니다.")
        candidates = manual_posts
    else:
        # Empty repository bootstrap only. Subsequent runs never backfill old posts.
        candidates = get_popular_posts(limit=HISTORICAL_ARCHIVE_TARGET)

    for post in candidates:
        if post["id"] in known_ids:
            continue
        print(f"새 글 보관 중: {post['title'][:30]}...")
        try:
            snapshot = get_post_snapshot(post["source_url"])
            post["title"] = snapshot["title"] or post["title"]
            post["archived_comments"] = snapshot["comments"]
            post["comments"] = str(len(post["archived_comments"]))
            post["content"] = localize_article_content(snapshot["content"], post["id"])
            thumbnail_path = archive_asset(post["thumbnail_url"], post["id"])
            post["thumbnail"] = thumbnail_path or post["thumbnail_url"]
            github_put_file(
                f"posts/{post['id']}.html",
                generate_post_html(post).encode("utf-8"),
                f"Archive post {post['id']}",
            )
        except requests.RequestException as error:
            print(f"글 보관 실패: {error}")
            continue

        new_entries.append(
            {
                key: post[key]
                for key in (
                    "id",
                    "title",
                    "source_url",
                    "thumbnail",
                    "votes",
                    "comments",
                    "published",
                )
            }
            | {
                "archived_at": datetime.now().isoformat(timespec="seconds"),
                "archived_comment_count": len(post["archived_comments"]),
                "comment_archive_version": COMMENT_ARCHIVE_VERSION,
            }
        )

    entries = new_entries + entries
    backfilled = refresh_comments_for_existing_posts(entries)
    themed = apply_dark_theme_to_existing_posts(entries)
    github_put_file(
        "archive.json",
        json.dumps(entries, ensure_ascii=False, indent=2).encode("utf-8"),
        f"Update archive index ({len(new_entries)} new posts)",
        archive_sha,
    )
    build_list_pages(entries)
    print(
        f"아카이브 완료: 새 글 {len(new_entries)}개, "
        f"댓글 추가 {backfilled}개, 기존 글 화면 업데이트 {themed}개, 총 {len(entries)}개"
    )


if __name__ == "__main__":
    archive_posts()
