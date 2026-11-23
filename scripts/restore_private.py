#!/usr/bin/env python3
"""P0.6b RESTORE FROM PRIVATE (23.11.2026, решение Мии). Режим fail-closed.

Источник истины - приватный secrett-archive-data, склонированный в PRIV_DIR.
До парсеров и сборки платные пути (DIRS + FILES из scripts/private_write.sh -
один источник с записью в приватный) копируются из приватного в рабочую копию:
rsync -a БЕЗ --delete. Приватное ничем из публичного не перезаписывается:
направление копирования только private -> рабочая копия.

Публикация запрещена (код выхода не 0, шаг workflow падает, следующие шаги
с публикацией не выполняются), если:
  - приватный манифест несвежий (fresh=false);
  - путь из списка отсутствует в приватном;
  - копирование завершилось ошибкой;
  - после копирования хотя бы один файл рабочей копии не совпадает с приватным по sha.
Автоматического отката на публичные данные нет.

Лог: одна строка [RESTORE] {json}: status, fresh, files, copied, verified,
mismatch, extra_in_public (файлы, которые есть только в публичном - не удаляются,
только считаются), error, seconds. Таблица - в step summary.
"""
import hashlib, json, os, re, subprocess, sys, time
from pathlib import Path

t0 = time.time()
PRIV = Path(sys.argv[1])
ROOT = Path('.')
res = {'status': 'error', 'fresh': None, 'files': 0, 'copied': 0, 'verified': 0, 'mismatch': 0,
       'extra_in_public': 0, 'error': None}


def done(code):
    res['seconds'] = round(time.time() - t0, 1)
    print('[RESTORE] ' + json.dumps(res, ensure_ascii=False, separators=(',', ':')))
    summ = os.environ.get('GITHUB_STEP_SUMMARY')
    if summ:
        with open(summ, 'a', encoding='utf-8') as f:
            f.write('### P0.6b restore from private: ' + res['status'].upper() + '\n\n')
            f.write(f"fresh {res['fresh']} · files {res['files']} · verified {res['verified']} · mismatch {res['mismatch']} · "
                    f"extra_in_public {res['extra_in_public']} · error {res['error']}\n")
    if code == 0 and os.environ.get('GITHUB_ENV'):
        with open(os.environ['GITHUB_ENV'], 'a') as f:
            f.write('PRIVATE_RESTORE=ok\n')
    sys.exit(code)


def blob(p):
    d = p.read_bytes()
    return hashlib.sha1(b'blob %d\0' % len(d) + d).hexdigest()


def listing(base, rel):
    p = base / rel
    if p.is_file():
        return {rel: blob(p)}
    if not p.is_dir():
        return None
    return {f.relative_to(base).as_posix(): blob(f) for f in p.rglob('*') if f.is_file()}


try:
    src = Path('scripts/private_write.sh').read_text(encoding='utf-8')
    DIRS = re.search(r'^DIRS="([^"]+)"', src, re.M).group(1).split()
    FILES = re.search(r'^FILES="([^"]+)"', src, re.M).group(1).split()
except Exception as e:
    res['error'] = 'paths: ' + str(e)[:80]; done(3)

# 1. Свежесть: манифест приватного записан последним опубликованным прогоном конвейера
man = {}
try:
    man = json.loads((PRIV / 'docs/_private_write.json').read_text(encoding='utf-8'))
except Exception as e:
    res['error'] = 'manifest: ' + str(e)[:80]; res['fresh'] = False; done(2)
last_pub = None
try:
    repo = os.environ.get('GITHUB_REPOSITORY', 'luvenmedicalmsk-byte/secrett-archive')
    out = subprocess.run(['gh', 'api', f'repos/{repo}/commits?per_page=40', '-q',
                          '.[] | select(.commit.message | startswith("Карта сигналов")) | .sha'],
                         capture_output=True, text=True, timeout=30).stdout.split()
    last_pub = out[0] if out else None
except Exception as e:
    res['error'] = 'last_publish: ' + str(e)[:80]
res['manifest_public_sha'] = man.get('public_sha')
res['manifest_written_at'] = man.get('written_at')
res['last_pipeline_publish_sha'] = last_pub
res['fresh'] = bool(last_pub) and man.get('public_sha') == last_pub
if os.environ.get('RESTORE_TEST_SKIP_FRESH') == '1':   # только для локальной проверки, в workflow не задаётся
    res['fresh'] = True
if not res['fresh']:
    res['status'] = 'stale'; res['error'] = res['error'] or 'private manifest is not fresh'; done(2)

# 2. Все пути должны быть в приватном
priv_lists = {}
for rel in DIRS + FILES:
    lst = listing(PRIV, rel)
    if lst is None:
        res['error'] = 'missing in private: ' + rel; done(3)
    priv_lists[rel] = lst
    res['files'] += len(lst)

# 3. Копирование private -> рабочая копия, без --delete
for rel in DIRS + FILES:
    s, d = PRIV / rel, ROOT / rel
    if s.is_dir():
        d.mkdir(parents=True, exist_ok=True)
        cmd = ['rsync', '-a', '--checksum', str(s) + '/', str(d) + '/']
    else:
        d.parent.mkdir(parents=True, exist_ok=True)
        cmd = ['rsync', '-a', '--checksum', str(s), str(d)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except Exception as e:
        res['error'] = f'rsync {rel}: ' + str(e)[:100]; done(4)
    if r.returncode != 0:
        res['error'] = f'rsync {rel}: ' + (r.stderr or '')[-120:]; done(4)
    res['copied'] += len(priv_lists[rel])

# 4. Проверка результата: каждый приватный файл в рабочей копии с тем же sha
mism = []
for rel in DIRS + FILES:
    work = listing(ROOT, rel) or {}
    for k, v in priv_lists[rel].items():
        if work.get(k) == v:
            res['verified'] += 1
        else:
            mism.append(k)
    res['extra_in_public'] += sum(1 for k in work if k not in priv_lists[rel])
res['mismatch'] = len(mism)
if mism:
    res['error'] = 'mismatch after copy: ' + ', '.join(mism[:3]); done(5)

res['status'] = 'ok'
done(0)
