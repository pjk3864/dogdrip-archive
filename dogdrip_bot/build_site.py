"""Build a small Pages site; serve preserved media from immutable GitHub URLs."""
import argparse
import ast
import datetime
import html
import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote


ASSET_ATTRIBUTE = re.compile(
    r'(?P<prefix>\b(?:src|href|poster|data-src)\s*=\s*)(?P<quote>[\"\'])'
    r'(?P<url>(?:\.\./|\./|/)?assets/[^\"\'\s<>]+)(?P=quote)',
    re.IGNORECASE,
)


def build(source, output):
    commit = subprocess.check_output(
        ['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True
    ).strip()
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Expected a complete Git commit SHA')
    raw_base = f'https://raw.githubusercontent.com/pjk3864/dogdrip-archive/{commit}/'
    assets = set(subprocess.check_output(
        ['git', '-C', str(source), 'ls-tree', '-r', '--name-only', 'HEAD', 'assets'],
        text=True,
    ).splitlines())
    entries = json.loads((source / 'archive.json').read_text(encoding='utf-8'))
    entries.sort(key=lambda entry: int(entry['id']), reverse=True)

    def media_url(url):
        if url.startswith('../'):
            url = url[3:]
        elif url.startswith('./'):
            url = url[2:]
        elif url.startswith('/'):
            url = url[1:]
        path = url.split('?', 1)[0].split('#', 1)[0]
        if path not in assets:
            raise ValueError(f'Archived media is missing from this commit: {path}')
        return raw_base + quote(url, safe='/?:=&%#')

    # Reuse the bot's UI without importing or executing its collection code.
    module = ast.parse((source / 'dogdrip_bot/bot.py').read_text(encoding='utf-8'))
    names = {'POSTS_PER_LIST_PAGE', 'PAGER_WINDOW_SIZE', 'READ_LIST_SCRIPT', 'SEARCH_LIST_SCRIPT'}
    nodes = [node for node in module.body if (
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id in names for target in node.targets)
    ) or (
        isinstance(node, ast.FunctionDef)
        and node.name in {'list_page_path', 'generate_index_html'}
    )]
    context = {'html': html, 'datetime': datetime.datetime}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'bot-ui', 'exec'), context)
    context['SEARCH_LIST_SCRIPT'] = context['SEARCH_LIST_SCRIPT'].replace(
        'fetch("archive.json",', f'fetch("archive.json?v={commit}",'
    )
    collected = max((entry.get('archived_at', '') for entry in entries), default='')
    if collected:
        collected = datetime.datetime.fromisoformat(collected).strftime('%Y-%m-%d %H:%M')
    if output.exists():
        shutil.rmtree(output)
    (output / 'posts').mkdir(parents=True)
    references = 0
    for entry in entries:
        if not re.fullmatch(r'\d+', str(entry['id'])):
            raise ValueError('Invalid post ID')
        path = f"posts/{entry['id']}.html"
        page = (source / path).read_text(encoding='utf-8')

        def replace_asset(match):
            return match['prefix'] + match['quote'] + media_url(match['url']) + match['quote']

        page, count = ASSET_ATTRIBUTE.subn(replace_asset, page)
        references += count
        (output / path).write_text(page, encoding='utf-8')
        thumbnail = entry.get('thumbnail', '')
        if thumbnail.startswith(('assets/', '../assets/', './assets/', '/assets/')):
            entry['thumbnail'] = media_url(thumbnail)
    page_count = max(1, (len(entries) + context['POSTS_PER_LIST_PAGE'] - 1) // context['POSTS_PER_LIST_PAGE'])
    for number in range(1, page_count + 1):
        page = context['generate_index_html'](entries, number)
        if collected:
            page = re.sub(r'마지막 수집 \d{4}-\d{2}-\d{2} \d{2}:\d{2}', '마지막 수집 ' + collected, page)
        (output / context['list_page_path'](number)).write_text(page, encoding='utf-8')
    (output / 'archive.json').write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / '.nojekyll').touch()
    (output / 'deployment.json').write_text(json.dumps({
        'commit': commit, 'post_count': len(entries), 'collected_at': collected,
        'media_source': raw_base, 'media_references': references,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    size = sum(path.stat().st_size for path in output.rglob('*') if path.is_file())
    if size >= 900_000_000:
        raise ValueError(f'Site is too large for Pages: {size} bytes')
    print(f'Built {len(entries)} posts, {page_count} list pages, {references} media references, {size:,} bytes')
    return size


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--output', type=Path, default=Path('_site'))
    args = parser.parse_args()
    build(args.source.resolve(), args.output.resolve())
