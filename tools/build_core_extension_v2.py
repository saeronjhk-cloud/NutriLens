# -*- coding: utf-8 -*-
"""
core_extension_v2 빌드 (보완판 — Gemini·ChatGPT 자문 반영).
- 식약처 통합 식품영양성분DB(2021) DB군='음식'·상용제품='품목대표' 중 CORE 신규만.
- 영양값 per-1회제공량 → per-100g (÷serving×100) 변환.
- 동일명 다중행: per-100g '중앙값'(평균 아님), serving 중앙값.  [자문: median 권장]
- 결측 미량영양소는 0으로 안 채움(생략).  [자문: 최고의 선택]
- src 태그(generic_korean_representative) + 국물/뼈 note 부착.
"""
import json, re, sys, statistics as st
from pathlib import Path

BASE = Path("/sessions/affectionate-vibrant-albattani/mnt/NutriLens")
RAW  = BASE/".tmp/h_nutrition/eumsik_raw.json"
OUT  = BASE/"core_extension_v2.json"

def num(x):
    if x is None: return None
    s=str(x).strip()
    if s in ('','-','N/A','nan'): return None
    try: return float(s)
    except: return None
def norm(s): return re.sub(r'\s+','',str(s)).strip()

NUT=['kcal','prot','fat','carbs','sugar','fiber','sodium','calcium','phos','pot','mag','iron','zinc','chol']
CK={'kcal':'cal','prot':'prot','fat':'fat','carbs':'carbs','sugar':'sugar','fiber':'fiber','sodium':'sodium',
    'calcium':'calcium','phos':'phos','pot':'pot','mag':'mag','iron':'iron','zinc':'zinc','chol':'chol'}

LIQUID = ('국','탕','찌개','전골','죽','수프','스프')
BONE_KW = ('갈비','뼈','곰탕','감자탕','꼬리','족발','사골','갈치','고등어','조기','도미','병어','임연수','장어',
           '삼치','꽁치','우럭','광어','생선','이면수','홍어','가자미','명태','동태','대구')

rows=json.load(open(RAW))
rep=[r for r in rows if r.get('sangyong')=='품목대표']

groups={}
for r in rep:
    nm=str(r['name']).strip(); sv=num(r['serving'])
    if not sv or sv<=0 or sv<30 or sv>1500: continue   # 비정상 serving 변환 금지(자문)
    per100={k:(num(r.get(k))/sv*100.0) for k in NUT if num(r.get(k)) is not None}
    if 'kcal' not in per100: continue
    g=groups.setdefault(norm(nm), {'name':nm,'cat':r.get('cat'),'servings':[],'vals':{k:[] for k in NUT}})
    g['servings'].append(sv)
    for k,v in per100.items(): g['vals'][k].append(v)

def agg(g):
    out={'serving': round(st.median(g['servings']))}
    for k in NUT:
        vs=g['vals'][k]
        out[CK[k]] = round(st.median(vs),2) if vs else None   # median (자문 반영)
    out['category']='korean'
    out['src']='generic_korean_representative'        # 출처 태그 (자문)
    nm=g['name']; cat=str(g['cat'] or '')
    notes=[]
    if any(w in cat for w in LIQUID) or any(w in nm for w in ('국','탕','찌개','전골')):
        notes.append('국물요리(희석/건더기 비율로 분량 편차)')
    if any(w in nm for w in BONE_KW):
        notes.append('뼈/껍질 포함 가능(분량 보정 주의)')
    if notes: out['note']=' / '.join(notes)
    return out

agg_foods={g['name']: agg(g) for g in groups.values()}

sys.path.insert(0, str(BASE/"tools"))
import food_analyzer as fa
core=set(norm(k) for k in fa.CORE_FOODS.keys())
# food_analyzer가 이미 기존 core_extension_v2를 로드했으므로, 그 항목은 비교에서 제외(자기자신 매칭 방지)
if OUT.exists():
    _old=json.load(open(OUT,encoding="utf-8")).get("new_additions",{})
    core -= set(norm(n) for n in _old)
new={nm:v for nm,v in agg_foods.items() if norm(nm) not in core}
clean={nm:v for nm,v in new.items() if v.get('cal') and 4<=v['cal']<=700}

result={"_meta":{
    "source":"식약처 통합 식품영양성분DB (2021) — DB군 '음식'·상용제품 '품목대표'",
    "merge_date":"2026-06-23","basis":"per-100g (원천 per-1회제공량 ÷serving×100)",
    "aggregation":"동일명 다중행 median (자문 반영). 결측 미량영양소 생략.",
    "advisory":"Gemini+ChatGPT 자문 반영: median, src 태그, 국물/뼈 note, serving 30~1500g 외 변환 제외.",
    "count":len(clean)},
  "new_additions":clean}
json.dump(result, open(OUT,"w"), ensure_ascii=False, indent=1)
print(f"신규 {len(clean)}종 저장 (이전 440 → 중앙값/serving필터 후)")

# ── 뼈 포함 음식 감사 (Gemini 1순위) ──
bone=[(nm,v['cal'],v['serving']) for nm,v in clean.items() if v.get('note') and '뼈' in v['note']]
bone.sort(key=lambda x:x[1])
print(f"\n[뼈/껍질 포함 의심 {len(bone)}종] per-100g 낮은 순 (낮으면 뼈 무게 희석 의심):")
for nm,c,s in bone[:14]: print(f"  {nm:12} {c:6.0f}/100g  serving {s}g  1인분 {round(c*s/100)}kcal")

# ── Top-20 한식 상식 검증 (regression-lite) ──
print("\n[신규 대표메뉴 상식 검증]")
for nm in ['채소볶음밥','북엇국','갈치구이','안심스테이크','떡만두국','조개된장찌개','소보로빵','쇠고기전골',
           '냉이된장국','돼지머리고기','버섯구이','새우구이','치즈케이크','오징어순대','시루떡']:
    if nm in clean:
        v=clean[nm]; print(f"  {nm:10} {v['cal']:5.0f}/100g  {v['serving']:4}g  1인분 {round(v['cal']*v['serving']/100):4}kcal  na/100g={v.get('sodium')}")
