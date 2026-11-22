#!/usr/bin/env python3
"""P0.6a RESTORE SHADOW (22.11.2026). Только чтение, поведение прогона не меняет.

Сравнивает платные файлы рабочей копии (публичный checkout) с приватным
secrett-archive-data, склонированным в PRIV_DIR, и проверяет свежесть
приватного манифеста docs/_private_write.json.
Состав платных путей берётся из scripts/private_write.sh (DIRS + FILES) -
один источник с записью в приватный.
Ничего не пишет в репозиторий: результат - строка [RESTORE-SHADOW] {json}
в журнале и таблица в step summary. Всегда выходит с кодом 0.

Поля: same / diff / missing_in_private / extra_in_private по файлам,
dir_hash - хэш содержимого папки (по нему между прогонами видно, какие
папки обновлялись за окно наблюдения), fresh - манифест записан последним
опубликованным прогоном конвейера.
"""
import hashlib, json, os, re, subprocess, sys, time
from pathlib import Path

t0 = time.time()
PRIV = Path(sys.argv[1])
ROOT = Path('.')
src = Path('scripts/private_write.sh').read_text(encoding='utf-8')
DIRS = re.search(r'^DIRS="([^"]+)"', src, re.M).group(1).split()
FILES = re.search(r'^FILES="([^"]+)"', src, re.M).group(1).split()

def blob(p):
    d = p.read_bytes()
    return hashlib.sha1(b'blob %d\0' % len(d) + d).hexdigest()

def listing(base, rel):
    p = base / rel
    if p.is_file():
        return {rel: blob(p)}
    if not p.is_dir():
        return {}
    out = {}
    for f in p.rglob('*'):
        if f.is_file():
            out[f.relative_to(base).as_posix()] = blob(f)
    return out

res = {'dirs': {}, 'totals': {'files': 0, 'same': 0, 'diff': 0, 'missing_in_private': 0, 'extra_in_private': 0}}
problems = []
for rel in DIRS + FILES:
    pub = listing(ROOT, rel); prv = listing(PRIV, rel)
    same = sum(1 for k, v in pub.items() if prv.get(k) == v)
    diff = [k for k, v in pub.items() if k in prv and prv[k] != v]
    miss = [k for k in pub if k not in prv]
    extra = [k for k in prv if k not in pub]
    h = hashlib.sha1(''.join(f'{k}:{pub[k]}\n' for k in sorted(pub)).encode()).hexdigest()[:12]
    res['dirs'][rel] = {'files': len(pub), 'same': same, 'diff': len(diff), 'missing': len(miss), 'extra': len(extra), 'dir_hash': h}
    for k, n in (('files', len(pub)), ('same', same), ('diff', len(diff)), ('missing_in_private', len(miss)), ('extra_in_private', len(extra))):
        res['totals'][k] += n
    if diff or miss or extra:
        problems.append({'path': rel, 'diff': diff[:3], 'missing': miss[:3], 'extra': extra[:3]})
res['problems'] = problems[:20]

# Свежесть: манифест приватного против последнего опубликованного коммита конвейера
man = {}
try:
    man = json.loads((PRIV / 'docs/_private_write.json').read_text(encoding='utf-8'))
except Exception as e:
    res['manifest_error'] = str(e)[:80]
last_pub = None
try:
    repo = os.environ.get('GITHUB_REPOSITORY', 'luvenmedicalmsk-byte/secrett-archive')
    out = subprocess.run(['gh', 'api', f'repos/{repo}/commits?per_page=40', '-q',
                          '.[] | select(.commit.message | startswith("Карта сигналов")) | .sha'],
                         capture_output=True, text=True, timeout=30).stdout.split()
    last_pub = out[0] if out else None
except Exception as e:
    res['last_pub_error'] = str(e)[:80]
res['manifest_public_sha'] = man.get('public_sha')
res['manifest_written_at'] = man.get('written_at')
res['last_pipeline_publish_sha'] = last_pub
res['head_sha'] = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
res['fresh'] = bool(last_pub) and man.get('public_sha') == last_pub
t = res['totals']
res['pass'] = res['fresh'] and t['diff'] == 0 and t['missing_in_private'] == 0
res['seconds'] = round(time.time() - t0, 1)

print('[RESTORE-SHADOW] ' + json.dumps(res, ensure_ascii=False, separators=(',', ':')))
summ = os.environ.get('GITHUB_STEP_SUMMARY')
if summ:
    with open(summ, 'a', encoding='utf-8') as f:
        f.write('### P0.6a restore shadow: ' + ('PASS' if res['pass'] else 'FAIL') + '\n\n')
        f.write(f"files {t['files']} · same {t['same']} · diff {t['diff']} · missing {t['missing_in_private']} · extra {t['extra_in_private']} · "
                f"fresh {res['fresh']} (manifest {str(res['manifest_public_sha'])[:7]} / last publish {str(last_pub)[:7]})\n\n")
        for p in problems[:10]:
            f.write(f"- {p['path']}: diff {p['diff']} missing {p['missing']} extra {p['extra']}\n")
sys.exit(0)
