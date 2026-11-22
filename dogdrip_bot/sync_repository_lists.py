"""Copy generated list pages back to the repository in a stable form."""
from pathlib import Path
import re
import subprocess
import sys


def sync(site, destination, commit):
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Invalid source commit')
    pages = sorted((p for p in site.iterdir() if re.fullmatch(r'page-[2-9][0-9]*\.html|page-1[0-9]+\.html', p.name)),
                   key=lambda p: int(p.stem.split('-')[1]))
    numbers = [int(p.stem.split('-')[1]) for p in pages]
    if numbers != list(range(2, len(numbers) + 2)):
        raise ValueError('Generated list pages are incomplete')
    sources = [site / 'index.html', *pages]
    if not sources[0].is_file():
        raise ValueError('Generated index is missing')
    for source in sources:
        content = source.read_text(encoding='utf-8')
        content = content.replace('archive.json?v=' + commit, 'archive.json')
        (destination / source.name).write_text(content, encoding='utf-8')
    return [p.name for p in sources]


if __name__ == '__main__':
    root = Path.cwd()
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    print('Updated', len(sync(Path(sys.argv[1]), root, commit)), 'repository list pages')
