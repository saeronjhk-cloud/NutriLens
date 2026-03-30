#!/usr/bin/env python3
"""
엑셀 DB → SQLite 변환
서버 시작 속도를 대폭 개선하고 메모리 사용량을 줄입니다.
"""
import json
import sys
import sqlite3
from pathlib import Path

try:
    import openpyxl
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "openpyxl", "-q"])
    import openpyxl

PROJECT_DIR = Path(__file__).parent.parent
DB_XLSX = PROJECT_DIR / "NutriLens_음식DB.xlsx"
DB_SQLITE = PROJECT_DIR / "nutrilens_db.sqlite"

print(f"엑셀 DB 로딩: {DB_XLSX.name}")
wb = openpyxl.load_workbook(DB_XLSX, read_only=True, data_only=True)
ws = wb["음식DB_전체"]

headers = [str(cell.value or "").strip() for cell in next(ws.iter_rows(min_row=1, max_row=1))]
print(f"컬럼: {len(headers)}개")

# SQLite DB 생성
try:
    if DB_SQLITE.exists():
        DB_SQLITE.unlink()
except Exception:
    pass  # 삭제 실패해도 덮어쓰기 가능

conn = sqlite3.connect(str(DB_SQLITE))
cur = conn.cursor()

cur.execute("""
    CREATE TABLE foods (
        food_id TEXT,
        name_ko TEXT NOT NULL,
        category TEXT DEFAULT '',
        subcategory TEXT DEFAULT '',
        serving_size_g REAL DEFAULT 100,
        calories_kcal REAL DEFAULT 0,
        protein_g REAL DEFAULT 0,
        carbs_g REAL DEFAULT 0,
        fat_g REAL DEFAULT 0,
        fiber_g REAL DEFAULT 0,
        sodium_mg REAL DEFAULT 0,
        sugar_g REAL DEFAULT 0
    )
""")

# 컬럼 인덱스 매핑
col_map = {}
for i, h in enumerate(headers):
    col_map[h] = i

FIELDS = ["food_id", "name_ko", "category", "subcategory", "serving_size_g",
           "calories_kcal", "protein_g", "carbs_g", "fat_g", "fiber_g", "sodium_mg", "sugar_g"]

count = 0
batch = []
for row in ws.iter_rows(min_row=2, values_only=True):
    name_idx = col_map.get("name_ko", 1)
    if not row[name_idx]:
        continue

    values = []
    for field in FIELDS:
        idx = col_map.get(field)
        if idx is not None and idx < len(row):
            val = row[idx]
            if val is None:
                val = "" if field in ("food_id", "name_ko", "category", "subcategory") else 0
            values.append(val)
        else:
            values.append("" if field in ("food_id", "name_ko", "category", "subcategory") else 0)

    batch.append(tuple(values))
    count += 1

    if len(batch) >= 10000:
        cur.executemany(f"INSERT INTO foods ({','.join(FIELDS)}) VALUES ({','.join('?' * len(FIELDS))})", batch)
        conn.commit()
        batch = []
        print(f"  {count:,}종 처리...")

if batch:
    cur.executemany(f"INSERT INTO foods ({','.join(FIELDS)}) VALUES ({','.join('?' * len(FIELDS))})", batch)
    conn.commit()

# 인덱스 생성 (검색 속도 향상)
print("인덱스 생성 중...")
cur.execute("CREATE INDEX idx_name_ko ON foods(name_ko)")
cur.execute("CREATE INDEX idx_category ON foods(category)")
conn.close()
wb.close()

size_mb = DB_SQLITE.stat().st_size / 1024 / 1024
print(f"\n완료! {count:,}종 → {DB_SQLITE.name} ({size_mb:.1f}MB)")
