"""앵커 평가 CLI — evaluator 를 불러서 찍기만."""
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services import evaluator

target = sys.argv[1] if len(sys.argv) > 1 else None

if target:
    print(json.dumps(evaluator.eval_pair(target),
                     ensure_ascii=False, indent=2))
else:
    for r in evaluator.eval_all():
        print(f"{r['name']}: recall={r['recall']} pre={r['precision']} "
              f"missed={[m['what'] for m in r['missed']]} "
              f"new={[n['what'] for n in r['new']]}")