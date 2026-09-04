r"""
colab_v5_1280_snippet.py — food30 v5 학습 (해상도 1280)   세션52 · 2026-09-03
=============================================================================
Colab 셀에 **통째로 붙여넣는** 원본이다. import 해서 쓰는 모듈이 아니다.
표준 체크포인트 하네스(`colab_ckpt_snippet.py`)를 그대로 물려받고 해상도만 바꿨다.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 이 학습이 «무엇을 묻는 실험»인지 먼저 읽을 것
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
IP/178 §17 이 v5 재학습을 사실상 기각했다. 근거는 세 겹이었다:
   데이터 양은 문제가 아니다        30클래스 불균형 1.06배 (거의 완벽)
   재학습 상한이 낮다              오답 337건 중 269건(80%)이 정답을 top4 에도 안 올린다
   대칭 혼동이 69%                 「흡수」가 아니라 「구별 불가」다

⛔ 그래서 **「같은 데이터·같은 설정으로 다시 돌리기」는 하지 않는다.** 같은 결과가 나온다.
   배제되지 «않은» 것은 하나뿐이다 — **더 나은 학습 레시피**. 그중 가장 그럴듯한 가설이
   해상도다: 국물 색·건더기 질감이 640px 에서 뭉개졌을 수 있다.

⇒ 이 스크립트는 그 «하나의 가설»만 검증한다. 다른 하이퍼파라미터는 v4 와 동일하게 둔다.

⚠ 완전한 통제가 아니다 — 정직하게 인용할 것
   1280 은 640 대비 픽셀이 4배라 batch 를 24 → 6 으로 낮춰야 한다(OOM). batch 는
   BN 통계와 유효 학습률에 영향을 준다. 즉 «해상도만» 바뀐 게 아니다.
   그래서 결과 해석이 비대칭이다:
     v5 가 지면  → 해상도 가설은 거의 죽는다 (해상도 이득이 batch 손실을 못 넘음)
     v5 가 이기면 → 원인 귀속 불가. 640×batch6 통제군을 한 번 더 돌려야 한다
   ⇒ **이기면 그때 통제군을 돌려라.** 지면 거기서 끝내라.

★ 먼저 값싼 프로브를 돌릴 것 — 아래 ⛔ 절을 읽으면 «먼저»가 아니라 «필수»임을 알게 된다.
   backends/NutriLens/tools/food30_resolution_probe.py   (run-resolution-probe.bat)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 ⛔⛔ 지금은 이 셀을 돌릴 수 없다 — 세션52 실측
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 **학습 이미지가 전부 max_side 640 으로 잘려 있다.**
   .tmp/food30/images/train · images/val · food30_021/images/train
   에서 무작위 180장을 열어 «전부 최대변 정확히 640» 이었다.
   food30_021/build_report_021.json 에 `"max_side": 640` 이 박혀 있다.

 ⇒ 640 이미지를 imgsz=1280 으로 학습하면 **업스케일일 뿐 새 정보가 없다.**
   시간과 GPU 만 4배 쓰고 가설은 검증되지 않는다. 「돌려는 봤다」가 가장 나쁜 결과다.

 ⇒ 이 실험을 하려면 먼저 **AI Hub 원본에서 다시 추출**해야 한다:
     추출기: training/extract_021_spanned.py · extract_021_stream.py (max_side 인자 있음)
     원본:   D:\서박사의 영양공식\AI Hub 탕류\  ·  download.tar (라벨)
     비용 추정: 640 에서 47,832장이 ≈3.4GB(food30_021.zip 578MB × 비례).
               1280 이면 픽셀 4배 → **≈12GB**. Drive 업로드·해제 시간이 학습만큼 든다.

 ★ 그러므로 먼저 값싼 관문을 통과할 것:
     run-resolution-probe.bat  →  tools/food30_resolution_probe.py   ($0)
   v4 를 그대로 두고 «추론» 해상도만 640↔1280 으로 바꿔 holdout 1,800장을 다시 본다.
   holdout 은 원본 해상도 그대로다(최대변 4032·3024…) — 여기서만 물을 수 있다.
   혼동이 안 움직이면 12GB 재빌드를 할 이유가 없다.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 사전 준비 (프로브를 통과한 «뒤에» 할 것)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 0) ⛔ 데이터 재추출 (위 참조). 안 하면 이 스크립트는 의미가 없다.

 1) ★★★ **`.tmp\food30\` 만 압축하면 안 된다 — 5개 클래스가 빠진다.**
      세션52 실측: `.tmp/food30/labels/val` 4,090개 전수 + train 무작위 3,000개에서
      **추어탕·해물탕·닭개장·육개장·뼈해장국(cid 25~29)이 0건**이다.
      그 7,978장(train 7,183 / val 795)은 여기에 따로 있다:
        backends\NutriLens\training\datasets\food30_021\   (같은 30클래스 공간, 순서 동일)
      ⇒ v4 학습 정본 = `.tmp\food30\` **＋** `food30_021\`  = 30클래스 47,832장
      ⚠ IP/178 §17-12 는 「v4 학습 정본 = .tmp/food30/」이라고만 적었다. **불완전하다.**
        그대로 압축해 올리면 5클래스를 모르는 모델이 나오고, 하필 그 안에
        닭개장·육개장(우리가 풀려는 쌍)이 들어 있다.
      명령 예:
        powershell Compress-Archive -Path "D:\서박사의 영양공식\.tmp\food30\*" `
                   -DestinationPath "D:\food30_dataset.zip"
        powershell Compress-Archive -Path "D:\서박사의 영양공식\backends\NutriLens\training\datasets\food30_021\images","...\labels" `
                   -DestinationPath "D:\food30_dataset.zip" -Update
        (또는 두 폴더를 한 곳에 합친 뒤 한 번에 압축. images/labels 하위구조가 같아야 한다)

 2) 업로드: Drive  MyDrive/NutriLens_Train/food_cls/food30_dataset.zip
      ⛔ `.tmp/` 는 gitignore 이고 CLAUDE.md 가 「언제든 버려도 됨」이라 적어 둔 곳이다.
        학습 데이터가 거기 있다. 이번 업로드를 **백업으로도** 삼을 것.

 3) Colab 런타임 → GPU (A100 권장. T4 면 batch 를 4 로 더 낮출 것)
 4) 아래 셀 붙여넣고 실행. 끊기면 **같은 셀을 다시 실행**하면 이어서 간다.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# ── Colab 첫 셀: 마운트 + 설치 ────────────────────────────────────────────
# from google.colab import drive; drive.mount('/content/drive')
# !pip -q install ultralytics

import zipfile, glob, shutil, yaml, time, os
from pathlib import Path
from ultralytics import YOLO

# ── 바꾸는 곳 ────────────────────────────────────────────────────────────
WORK        = Path('/content/food30')
D           = Path('/content/drive/MyDrive/NutriLens_Train/food_cls')
RUN         = 'food30_v5_1280'
EPOCHS      = 30
IMGSZ       = 1280          # ★ 이 실험의 «유일한 의도된 변수»
BATCH       = 6             # ⚠ 1280 은 640 대비 픽셀 4배. OOM 이면 4 → 2 로 낮춘다
DATASET_ZIP = 'food30_dataset.zip'
NEG_ZIP     = None          # v4 는 밥류 negative 를 썼다. v5 도 쓰려면 파일명을 넣을 것
FINAL_NAME  = 'food30_detection_v5_1280.pt'
# ─────────────────────────────────────────────────────────────────────────

CKPT = D / 'ckpt' / RUN                # ★ Drive. 여기 있는 것만 살아남는다
CKPT.mkdir(parents=True, exist_ok=True)

# 데이터 풀기 — 이미 풀려 있으면 건너뛴다(재시작 비용 최소화)
if not (WORK / 'images/val').exists():
    _t = time.time()
    if WORK.exists(): shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    with zipfile.ZipFile(D / DATASET_ZIP) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        for k, i in enumerate(infos, 1):
            tgt = WORK / i.filename.replace('\\', '/')   # Windows 백슬래시 경로 정규화
            tgt.parent.mkdir(parents=True, exist_ok=True)
            with z.open(i) as s, open(tgt, 'wb') as f:
                shutil.copyfileobj(s, f, 1 << 20)
            if k % 10000 == 0:
                print(f'  풀기 {k:,}/{len(infos):,} · {(time.time()-_t)/60:.1f}분', flush=True)
    dy = yaml.safe_load(open(WORK / 'data.yaml')); dy['path'] = str(WORK)
    yaml.safe_dump(dy, open(WORK / 'data.yaml', 'w'), allow_unicode=True)
    if NEG_ZIP:
        with zipfile.ZipFile(D / NEG_ZIP) as z:
            z.extractall(WORK / 'images/train')

_dy = yaml.safe_load(open(WORK / 'data.yaml'))
print('클래스', _dy['nc'],
      '| train', len(glob.glob(str(WORK / 'images/train/*'))),
      '| val', len(glob.glob(str(WORK / 'images/val/*'))))
assert _dy['nc'] == 30, f"클래스 수가 30 이 아니다: {_dy['nc']} — 데이터셋을 잘못 올렸다"
# ★ 클래스 «순서»가 v4 와 다르면 기존 평가·게이트가 통째로 무의미해진다.
assert _dy['names'][0] == '쌀밥' and _dy['names'][29] == '뼈해장국', \
    '클래스 순서가 v4 와 다르다. food_analyzer.FOOD30_CLASS_NAMES 와 대조할 것'

# ★★★ 세션52 — 「30클래스라고 적혀 있다」와 「30클래스가 들어 있다」는 다른 사건이다.
#   .tmp/food30 만 올리면 data.yaml 은 nc=30 이지만 실제 라벨에는 25종뿐이다
#   (추어탕·해물탕·닭개장·육개장·뼈해장국 = cid 25~29 가 0건).
#   그대로 학습하면 5클래스를 «침묵»하는 모델이 나오고, 학습 로그는 아무 말도 안 한다.
import collections as _c
_seen = _c.Counter()
for _p in glob.glob(str(WORK / 'labels/train/*.txt'))[:6000]:
    with open(_p, encoding='utf-8') as _f:
        for _l in _f:
            if _l.strip():
                _seen[int(_l.split()[0])] += 1
_empty = [_dy['names'][i] for i in range(30) if _seen.get(i, 0) == 0]
assert not _empty, (
    f'⛔ 라벨이 «0건»인 클래스가 있다: {_empty}\n'
    '   food30_021 을 함께 올리지 않았다. 위 「사전 준비 1)」 을 다시 읽을 것.')
print('클래스 30종 전부 라벨 확인 ✅  (표본 6,000장)')

# 해상도 확인 — 640 짜리를 1280 으로 돌리는 헛수고를 여기서 막는다.
from PIL import Image as _Im
_sizes = _c.Counter()
for _p in glob.glob(str(WORK / 'images/train/*'))[:200]:
    try:
        with _Im.open(_p) as _im: _sizes[max(_im.size)] += 1
    except Exception: pass
print('학습 이미지 최대변 분포:', _sizes.most_common(5))
assert max(_sizes) > 640, (
    f'⛔ 학습 이미지가 최대변 {max(_sizes)} 이다. 1280 학습은 업스케일일 뿐이다.\n'
    '   AI Hub 원본에서 다시 추출해야 한다. 위 「지금은 이 셀을 돌릴 수 없다」 참조.')


def _push(trainer, why):
    """매 epoch Drive 로 원자적 복사. `/content` 는 런타임이 끊기면 통째로 사라진다."""
    n = 0
    for name in ('last.pt', 'best.pt'):
        src = Path(trainer.save_dir) / 'weights' / name
        if not src.exists():
            continue
        try:
            tmp = CKPT / (name + '.tmp')
            shutil.copy(src, tmp)
            os.replace(tmp, CKPT / name)       # 복사 중 끊겨도 기존 체크포인트 보존
        except Exception:
            shutil.copy(src, CKPT / name)      # 폴백 (Drive FUSE 가 replace 를 거부할 때)
        n += 1
    for name in ('results.csv', 'args.yaml'):
        src = Path(trainer.save_dir) / name
        if src.exists():
            try: shutil.copy(src, CKPT / name)
            except Exception: pass
    ep = getattr(trainer, 'epoch', -1) + 1
    (CKPT / 'STATUS.txt').write_text(
        f'{time.strftime("%Y-%m-%d %H:%M:%S")} | epoch {ep}/{EPOCHS} | imgsz {IMGSZ} '
        f'| batch {BATCH} | {why} | 파일 {n}\n', encoding='utf-8')
    return n


_t0 = time.time()

def _on_epoch(tr):
    e = tr.epoch + 1
    el = time.time() - _t0
    ok = _push(tr, 'epoch')
    print(f'  ep {e:2d}/{tr.epochs}  경과 {el/60:5.1f}분  남은 {el/e*(tr.epochs-e)/60:5.1f}분  '
          f'mAP50 {(tr.metrics or {}).get("metrics/mAP50(B)",0):.3f}  Drive저장 {ok}/2', flush=True)


# Drive 에 last.pt 가 있으면 그걸 시작 가중치로 — 재실행이 곧 이어하기.
_resume = CKPT / 'last.pt'
model = YOLO(str(_resume)) if _resume.exists() else YOLO('yolov8s.pt')
print('시작 가중치:', _resume if _resume.exists() else 'yolov8s.pt (처음부터)')
# ⚠ v4 가중치(food30_detection_v4.pt)에서 이어받지 «않는다». 그러면 「해상도 효과」가
#   「추가 학습 효과」와 섞여 실험이 무의미해진다. 같은 출발선(yolov8s)에서 다시 배운다.
model.add_callback('on_fit_epoch_end', _on_epoch)
model.add_callback('on_train_end', lambda tr: _push(tr, 'train_end'))

try:
    # ★ imgsz·batch 를 뺀 나머지는 v4 와 «글자까지» 같다. 그래야 비교가 성립한다.
    #   (colab_ckpt_snippet.py 의 train 호출과 대조할 것)
    r = model.train(data=str(WORK / 'data.yaml'), epochs=EPOCHS, imgsz=IMGSZ, batch=BATCH,
                    device=0, workers=2, patience=10, fliplr=0.5, hsv_v=0.4, mosaic=1.0,
                    verbose=False, plots=False, project=str(WORK / 'runs'), name=RUN,
                    exist_ok=True)
    print('완료:', r.save_dir, f'| 총 {(time.time()-_t0)/60:.1f}분')
finally:
    b = CKPT / 'best.pt'
    if b.exists():
        shutil.copy(str(b), str(D / FINAL_NAME))
        print('★ Drive 최종본:', D / FINAL_NAME)
    print('★ 체크포인트 폴더:', CKPT, '| STATUS.txt 로 진행상황 확인')

# ═════════════════════════════════════════════════════════════════════════
# 학습 «후» — 이 순서로 판정할 것. 순서를 바꾸지 마라.
# ═════════════════════════════════════════════════════════════════════════
#
# 1) 가중치를 내려받아 backends/NutriLens/models/food30_detection_v5_1280.pt 로 둔다.
#
# 2) ⛔ 먼저 게이트91 부터 돌린다. 정확도가 올랐어도 게이트를 깨면 못 쓴다.
#       python tools/food30_sweep.py        (τ=0.70, 거짓 교체 예산 2건)
#       v4 실측: 91장 중 발화 3장 · 거짓 교체 2건(둘 다 닭볶음탕發, 가드가 차단)
#       ⚠ v5 의 «오탐 출처 클래스»가 닭볶음탕이 아닐 수 있다.
#         그러면 _F30_FP_PRONE_CLASSES 를 다시 정해야 한다. 가드는 v4 실측의 산물이다.
#
# 3) 대칭 혼동이 실제로 줄었는지 — 이 실험의 «본 질문»이다.
#       python tools/food30_diagnose_attractor.py     (1,998장, 엔진 단독)
#       python tools/food30_merge_group_sim.py "D:\서박사의 영양공식"
#       v4 기준선: 오답 337건 · 대칭 234(69%) · 흡수 103(31%)
#                  정답이 top4 밖 269건(80%)  ← ★ 이 숫자가 줄어야 재학습이 이긴 것이다
#       ⚠ mAP50 이 올랐다고 이기는 게 아니다. mAP 는 전체 평균이고, 우리 문제는
#         «특정 쌍»이다. 반드시 위 두 진단으로 볼 것.
#
# 4) 그 다음에야 유료 실행. 순서를 지키면 돈을 안 버린다.
#       run-aihub300-production.bat   (~$1.50)
#       v4 기준선(세션51): EXACT 164/300 · 순수 GPT 92 · 엔진 순기여 +72(+24.0%p)
#       ⚠ EXACT 만 보지 말 것. 「엔진 순기여」가 진짜 지표다 — tools/food30_counterfactual.py
#
# 5) v5 가 이겼다면 → 640×batch6 통제군을 한 번 더 돌려 원인을 귀속시킨다(위 ⚠ 참조).
#    v5 가 졌다면   → 해상도 가설은 여기서 닫는다. IP/178 §17-8 의 권고로 돌아간다:
#                     재학습 말고 «엔진을 더 쓰는 쪽»(정책·임계값·흡수 103건).
