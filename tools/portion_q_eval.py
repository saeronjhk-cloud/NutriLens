"""양(포션) 순서 평가 — AI Hub 양추정 Validation 의 Q 단계(Q1<…<Q5)를 «순서 정답»으로 쓴다. (2026-10-11)
왜: 사진마다 실측 g 정답이 없다(IP/integration/calorie_serving_diagnosis_v1.md D1·D5).
    같은 음식이면 Q 가 클수록 양이 많다 → 엔진의 최종 g 이 그 순서를 지키는가, 양 보정 규칙이 순서를 뭉개는가.
설계: GPT 는 한 번만 부른다. 사진마다 (GPT 원래 g, CORE 1인분, shape, 출처)를 기록하면
      양 보정은 결정론이라 규칙 끔/현행/후보를 오프라인(비용 0)으로 다시 계산할 수 있다.
사용:
  python tools\\portion_q_eval.py extract      # H: 외장하드에서 추출 (비용 0)
  python tools\\portion_q_eval.py run [--dry-run]   # GPT 실행 (~$0.005/장), 중간 저장·이어하기
결과: .tmp/portion_q_results.json  (채점은 Claude 가 오프라인으로)
"""
import io, json, os, re, sys, time, zipfile, copy
from pathlib import Path
from collections import defaultdict, Counter

T = Path(__file__).resolve().parent
PROJECT = T.parent
ROOT = PROJECT.parent.parent
sys.path.insert(0, str(T))
VAL_ZIP = Path(r"H:\NutriLens Traning Data\음식 이미지 및 영양정보 텍스트\Validation\[원천]양추정_VAL.zip")
OUT = ROOT / "Images" / "aihub_portion_q"
RES = PROJECT / ".tmp" / "portion_q_results.json"
IMG = ('.jpg', '.jpeg', '.png')
PER_Q = 4          # (음식, Q) 당 사진 수
MAX_FOODS = 15     # volume/drink 음식 최대 수
# 측정 전 고정(2026-10-11 20:4x): 반찬 7 + 밥·면·국 8. 1차 실행은 가나다순 15종(탕·찌개 위주)이라 반찬이 빠졌고 GPT 호출 0건으로 무효.
PICK = ['시금치나물', '어묵볶음', '오징어볶음', '순대볶음', '갈치조림', '꽁치조림', '코다리조림',
        '김치볶음밥', '카레라이스', '자장면', '순두부찌개', '갈비탕', '떡만둣국', '호박죽', '콩국수']
# ★ 확인셋 q2 (2026-10-12 고정, 결과 보기 전) — 1차 15종과 겹치지 않는 «아직 GPT 에 안 보낸» volume 음식 20종.
#   목적: C3c(연속형) 를 1차셋(이미 본 셋)에서 골랐으므로 새 셋에서 재확인. 판정 기준 = IP/integration/calorie_serving_diagnosis_v1.md §6
PICK_Q2 = ['감자탕', '곱창전골', '꽃게탕', '닭볶음탕', '닭죽', '류산슬덮밥', '매운탕', '부대찌개', '삼계탕', '삼선짬뽕',
           '새우볶음밥', '소고기무국', '순대국', '시래기된장국', '알탕', '잡탕밥', '전주비빔밥', '제육덮밥', '쫄면', '회덮밥']
if any(a == '--set=q2' for a in sys.argv):
    PICK = PICK_Q2
    RES = PROJECT / ".tmp" / "portion_q_results_q2.json"
# 검산 = «기록 kcal 을 만든 엔진 규칙»: 1차셋은 10-11 CUR(덮어쓰기) 엔진, q2 는 10-12 운영(C3b+포장 기준) 엔진으로 실행됨
CHECK_VARIANT = 'C3bP' if PICK is PICK_Q2 else 'CUR'
QRE = re.compile(r'^[Qq](\d)$')


def fixname(n):
    try:
        return n.encode('cp437').decode('cp949')
    except Exception:
        return n


def parse(n):
    parts = n.replace('\\', '/').split('/')
    for i, p in enumerate(parts):
        m = QRE.match(p)
        if m and i >= 1:
            return parts[i - 1], 'Q' + m.group(1)
    m = re.search(r'_([Qq]\d)_', parts[-1])          # 파일명에만 Q 가 있는 경우
    if m and len(parts) >= 2:
        return parts[-2], m.group(1).upper()
    return None, None


def is_target(food, fa):
    if fa._detect_shape(food) == 'piece':
        return False
    k, d = fa._search_gold(food)
    return bool(d) and d.get('source') == 'core'


def find_val_zip():
    if VAL_ZIP.exists():
        return VAL_ZIP
    print(f"지정 경로에 없음: {VAL_ZIP}\n외장하드에서 양추정 zip 을 찾습니다...")
    base = Path("H:/")
    hits = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith(('$', 'System Volume'))]
        for fn in files:
            if fn.lower().endswith('.zip') and '양추정' in fn:
                hits.append(Path(root) / fn)
    for h in hits:
        print("  발견:", h, f"{h.stat().st_size/1e9:.1f}GB")
    val = [h for h in hits if 'VAL' in h.name.upper() and '원천' in h.name]
    if not val:
        val = [h for h in hits if 'VAL' in h.name.upper()]
    return val[0] if val else None


def extract():
    import food_analyzer as fa
    zp = find_val_zip()
    if not zp:
        print("[STOP] 양추정 VAL zip 을 외장하드에서 찾지 못했습니다."); return 1
    print(f"사용: {zp}")
    z = zipfile.ZipFile(zp)
    infos = z.infolist()
    names = [(i, i.filename if i.flag_bits & 0x800 else fixname(i.filename)) for i in infos]
    imgs = [(i, n) for i, n in names if n.lower().endswith(IMG)]
    inner = [n for i, n in names if n.lower().endswith('.zip')]
    print(f"엔트리 {len(infos):,} · 이미지 {len(imgs):,} · 안쪽 zip {len(inner)}")
    for i, n in names[:8]:
        print("  예:", n)
    by = defaultdict(lambda: defaultdict(list))
    nested = {}                                  # (음식, Q) → 안쪽 zip 이름  (구조: image/<음식>/<Q>/<Q>.zip)
    if not imgs and inner:
        for n in inner:
            f, q = parse(n)
            if f:
                nested[(f, q)] = n
                by[f][q] = None                  # 장수는 열어 봐야 안다
        print(f"중첩 구조 — 안쪽 zip {len(nested)}개 (음식×Q 하나씩, 열 때 메모리로 읽음)")
    for i, n in imgs:
        f, q = parse(n)
        if f:
            by[f][q].append((n, i))
    print(f"\n음식 {len(by)}종 (Q 단계별 장수, shape, 대상 여부)")
    cand = []
    for f in sorted(by):
        qs = {q: (len(v) if v is not None else '?') for q, v in sorted(by[f].items())}
        tgt = is_target(f, fa)
        print(f"  {f:<14} {qs}  shape={fa._detect_shape(f)}  {'★대상' if tgt else ''}")
        if tgt and sum(1 for c in qs.values() if c == '?' or c >= PER_Q) >= 3:
            cand.append(f)
    pick = [f for f in PICK if f in cand] or cand[:MAX_FOODS]
    miss = [f for f in PICK if f not in cand]
    if miss:
        print(f"  ⚠ 고정 목록 중 대상 아님/없음: {miss}")
    print(f"\n선정 {len(pick)}종 (volume/drink · CORE 매칭 · Q 3단계 이상): {pick}")
    if not pick:
        print("[STOP] 양 보정 규칙이 걸리는 음식이 없습니다. 창 내용을 Claude 에게 보내 주십시오."); return 1
    n_out = 0
    for f in pick:
        for q, lst in sorted(by[f].items()):
            if lst is None:                      # 중첩: 안쪽 zip 을 메모리로 열어 앞에서 PER_Q 장
                try:
                    iz = zipfile.ZipFile(io.BytesIO(z.read(nested[(f, q)])))
                except Exception as e:
                    print(f"  ⚠ {f} {q} 안쪽 zip 열기 실패: {e}"); continue
                members = sorted((ii for ii in iz.infolist() if ii.filename.lower().endswith(IMG)), key=lambda ii: ii.filename)
                src = [(ii.filename.replace('\\', '/').split('/')[-1], lambda ii=ii, iz=iz: iz.read(ii)) for ii in members[:PER_Q]]
                print(f"  {f} {q}: 안쪽 {len(members)}장 중 {len(src)}장")
            else:
                src = [(n.replace('\\', '/').split('/')[-1], lambda info=info: z.read(info)) for n, info in sorted(lst, key=lambda t: t[0])[:PER_Q]]
            for base, read in src:
                dst = OUT / f / q / fixname(base)
                dst.parent.mkdir(parents=True, exist_ok=True)
                if not dst.exists():
                    dst.write_bytes(read())
                n_out += 1
    print(f"\n추출 {n_out}장 → {OUT}")
    return 0


def pick_food(foods, folder):
    for f in foods:
        nm = (f.get('name_ko') or '').replace(' ', '')
        if nm and (nm in folder or folder in nm):
            return f
    return foods[0] if foods else None


def run(dry=False):
    import accuracy_test as at
    at.load_env()
    import food_analyzer as fa
    imgs = sorted(p for p in OUT.rglob('*') if p.suffix.lower() in IMG and p.parent.parent.name in PICK)
    print(f"사진 {len(imgs)}장 · 음식 {len({p.parent.parent.name for p in imgs})}종 · 예상 비용 ~${len(imgs)*0.005:.2f}")
    if dry or not imgs:
        return 0 if imgs else 1
    db_path = PROJECT / '.tmp' / 'db_head.sqlite'
    db = fa.load_food_db(str(db_path) if db_path.exists() else None)
    key = os.environ.get('OPENAI_API_KEY')
    done = json.loads(RES.read_text(encoding='utf-8')) if RES.exists() else {}
    for k, p in enumerate(imgs, 1):
        rel = str(p.relative_to(OUT)).replace('\\', '/')
        if rel in done and 'error' not in done[rel]:
            continue                              # 에러난 것은 다시 시도
        folder, q = p.parent.parent.name, p.parent.name
        t0 = time.time()
        rec = {'food': folder, 'q': q}
        try:
            a = fa.analyze_food_image(str(p), api_key=key, allow_raw=True, preprocess='production')   # production = 운영과 같은 768px 최소화
            if 'error' in a:
                rec['error'] = str(a['error'])[:200]
            else:
                f = pick_food(a.get('foods', []), folder)
                rec['ai_name'] = f and f.get('name_ko')
                rec['gpt_g'] = f and f.get('estimated_serving_g')
                b = fa.match_with_db(copy.deepcopy(a), db)
                g = pick_food(b.get('foods', []), folder)
                rec['shape'] = g and g.get('shape')
                rec['source'] = g and g.get('source')
                rec['kcal'] = g and g.get('calories_kcal')
                gk, gd = fa._search_gold(rec['ai_name'] or folder)
                if gd:
                    rec['gold_key'], rec['gold_serving'], rec['gold_cal'], rec['gold_src'] = gk, gd.get('serving'), gd.get('cal'), gd.get('source')
        except Exception as e:
            rec['error'] = f"{type(e).__name__}: {e}"[:200]
        done[rel] = rec
        RES.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f"  [{k}/{len(imgs)}] {folder} {q} → {rec.get('ai_name')} GPT {rec.get('gpt_g')}g · {rec.get('kcal')}kcal ({time.time()-t0:.1f}s){' ERR' if 'error' in rec else ''}")
    print(f"\n완료 → {RES}")
    return 0


# ── 채점 (오프라인, 비용 0) — 기준은 IP/integration/calorie_serving_diagnosis_v1.md §4 (측정 전 고정) ──
def rule(gpt, std, variant, key=None):
    if not gpt or not std:
        return gpt
    if variant == 'C3bP':                            # 운영(10-12~) = C3b + 포장 기준 12종 1회분 고정
        from food_analyzer import PACKAGE_BASIS_KEYS
        if key in PACKAGE_BASIS_KEYS:
            return std
        variant = 'C3b'
    if variant == 'C3c':                             # 연속형: 범위 밖이면 «범위 끝» 쪽으로 30% (1.5배 경계에서 값이 뒤집히지 않음)
        r = gpt / std
        if r > 1.5:
            return round(gpt * 0.7 + std * 1.5 * 0.3)
        if r < 0.7:
            return round(gpt * 0.7 + std * 0.7 * 0.3)
        return gpt
    r = gpt / std
    if variant == 'OFF':
        return gpt
    hi, lo, bhi, blo = 2.5, 0.4, 1.5, 0.7
    if variant == 'C3a':
        hi, lo = 4.0, 0.25
    if variant == 'C1' and r < 1:
        return gpt                                   # 늘리는 보정 없음
    if variant != 'C3b' and (r > hi or r < lo):
        return std
    if r > bhi or r < blo or (variant == 'C3b' and (r > hi or r < lo)):
        return round(gpt * 0.7 + std * 0.3)
    return gpt


def concordance(rows):
    num = den = 0.0
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            (qa, ga), (qb, gb) = rows[i], rows[j]
            if qa == qb or ga is None or gb is None:
                continue
            den += 1
            if ga == gb:
                num += 0.5
            elif (ga < gb) == (qa < qb):
                num += 1
    return num / den if den else None


def score():
    if not RES.exists():
        print(f"[STOP] 결과 파일 없음: {RES} — 먼저 run 을 실행하십시오."); return 1
    d = {k: v for k, v in json.loads(RES.read_text(encoding='utf-8')).items() if v.get('food') in PICK}
    ok = [r for r in d.values() if 'error' not in r and r.get('gpt_g') and r.get('gold_serving')
          and r.get('shape') in ('volume', 'drink') and r.get('gold_src') == 'core']
    print(f"기록 {len(d)} · 채점 대상 {len(ok)} (에러 {sum('error' in r for r in d.values())})")
    bad = 0
    for r in ok:                                     # 검산: 기록 kcal 로 역산한 g = CUR 재계산 g
        if r.get('kcal') and r.get('gold_cal'):
            g_rec = r['kcal'] / r['gold_cal'] * 100
            if abs(g_rec - rule(r['gpt_g'], r['gold_serving'], CHECK_VARIANT, r.get('gold_key'))) > 2:
                bad += 1
    print(f"검산 불일치 {bad}건 (0 이어야 유효 · 기준 {CHECK_VARIANT})")
    V = ('OFF', 'CUR', 'C1', 'C3a', 'C3b', 'C3bP', 'C3c')
    by = defaultdict(list)
    for r in ok:
        by[r['food']].append(r)
    tab = {}
    for f, rs in sorted(by.items()):
        tab[f] = {v: concordance([(int(r['q'][1:]), rule(r['gpt_g'], r['gold_serving'], v, r.get('gold_key'))) for r in rs]) for v in V}
        qm = defaultdict(list)
        for r in rs:
            qm[r['q']].append(r['gpt_g'])
        print(f"  {f:<12} n={len(rs):<3} 1인분 {rs[0]['gold_serving']}g · GPT 평균 " +
              ' '.join(f"{q}:{sum(v)/len(v):.0f}" for q, v in sorted(qm.items())) + ' | ' +
              ' '.join(f"{v} {tab[f][v]*100:.0f}" if tab[f][v] is not None else f"{v} -" for v in V))
    mean = {v: (lambda xs: sum(xs) / len(xs) if xs else None)([t[v] for t in tab.values() if t[v] is not None]) for v in V}
    print('\n평균 순서 일치율: ' + ' · '.join(f"{v} {mean[v]*100:.1f}%" for v in V if mean[v] is not None))
    for v in ('C1', 'C3a', 'C3b', 'C3bP', 'C3c'):
        if mean[v] is None or mean['CUR'] is None:
            continue
        worst = min((t[v] - t['CUR']) for t in tab.values() if t[v] is not None and t['CUR'] is not None)
        ok_ = (mean[v] - mean['CUR']) >= 0.05 and worst >= -0.10
        print(f"  {v}: CUR 대비 {100*(mean[v]-mean['CUR']):+.1f}%p · 최악 음식 {100*worst:+.1f}%p → {'채택 가능' if ok_ else '불채택'}")
    print(f"  (참고) OFF: CUR 대비 {100*(mean['OFF']-mean['CUR']):+.1f}%p — 규칙이 순서를 얼마나 뭉개는지")
    out = PROJECT / '.tmp' / ('portion_q_score_q2.json' if PICK is PICK_Q2 else 'portion_q_score.json')
    out.write_text(json.dumps(dict(n=len(ok), mismatch=bad, mean=mean, by_food=tab), ensure_ascii=False, indent=1), encoding='utf-8')
    return 0


class _Tee:
    def __init__(self, *s): self.s = s
    def write(self, x):
        for t in self.s:
            try: t.write(x); t.flush()
            except Exception: pass
    def flush(self):
        for t in self.s:
            try: t.flush()
            except Exception: pass


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    _log = PROJECT / '.tmp' / f'portion_q_log_{cmd or "help"}{"_q2" if PICK is PICK_Q2 else ""}.txt'
    _log.parent.mkdir(parents=True, exist_ok=True)
    _lf = open(_log, 'a', encoding='utf-8')
    _lf.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(sys.argv[1:])} =====\n")
    sys.stdout = _Tee(sys.__stdout__, _lf)
    sys.stderr = _Tee(sys.__stderr__, _lf)
    if cmd == 'extract':
        sys.exit(extract())
    if cmd == 'score':
        sys.exit(score())
    if cmd == 'run':
        sys.exit(run('--dry-run' in sys.argv))
    print(__doc__); sys.exit(2)
