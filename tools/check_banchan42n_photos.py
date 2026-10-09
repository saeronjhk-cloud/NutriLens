"""banchan42n(새 판정셋) 사진 점검 — 규칙 75: «서로 다른 사진인가»부터.
- 종마다 목록(banchan_photos_v1_420.json) 밖 사진이 6장 이상인가
- 새 사진 md5 가 서로 다르고, 기존 420장 md5 와도 겹치지 않는가
실패하면 exit 1 (bat 이 멈춘다).
"""
import hashlib, json, sys
from pathlib import Path
T = Path(__file__).parent
ROOT = T.parent.parent.parent
base = ROOT / 'Images' / 'aihub_banchan'
mani = json.loads((T / 'banchan_photos_v1_420.json').read_text(encoding='utf-8'))['classes']
EX = ('.jpg', '.jpeg', '.png')
md5 = lambda p: hashlib.md5(p.read_bytes()).hexdigest()
old = set(); new = {}; short = []
for cls, names in mani.items():
    d = base / cls
    for n in names:
        old.add(md5(d / n))
    fs = sorted(f for f in d.iterdir() if f.suffix.lower() in EX and f.name not in set(names))[:6]
    if len(fs) < 6: short.append(f'{cls} {len(fs)}/6')
    for f in fs: new[f] = md5(f)
vals = list(new.values())
dup_new = len(vals) - len(set(vals)); dup_old = sum(1 for v in set(vals) if v in old)
print(f'새 사진 {len(vals)}장 · 고유 {len(set(vals))} · 기존 420장과 겹침 {dup_old} · 6장 미달 {len(short)}종 {short[:5]}')
ok = len(vals) == 252 and dup_new == 0 and dup_old == 0 and not short
print('OK' if ok else '[STOP] 새 판정셋이 깨끗하지 않습니다 — 창 내용을 Claude 에게 보내 주십시오.')
sys.exit(0 if ok else 1)
