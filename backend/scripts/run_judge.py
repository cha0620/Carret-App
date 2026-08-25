# backend/run_judge.py
import sys 
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services import judge, storage
from app.prompts.rubric import AXES

for orig in sorted((storage.BASE / "original").glob("*.*")):
    fid = orig.stem
    for res in sorted((storage.BASE / "result").glob(f"{fid}_*.*")):
        s = judge.judge(orig.read_bytes(), res.read_bytes())   # ← await 삭제
        print(f"{fid[:8]} | " +
              " ".join(f"{ax}={s[ax]}" for ax in AXES) +
              f" | {s.get('analysis', '')[:80]}")