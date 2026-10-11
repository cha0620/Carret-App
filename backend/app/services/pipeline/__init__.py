"""변환 파이프라인 (LangGraph) — 사진 한 장을 대표사진으로.

    load → analyze → plan ─┬─ 생성:     [read_text] → generate → validate_result → score_similarity → verify ─┐
                           └─ 배경 교체: composite (원본 물건 픽셀 + 새 배경)                                   ┴→ save_inspect → finalize

생성과 배경 교체는 판매자가 고르는 별개 기능이다 (user_mode, 10-11). 생성은 검사에서 떨어져도 1회 재생성 뒤
마지막 결과를 탈락 기록과 함께 내보내고, 배경 교체는 [배경만 바꾸기]를 골랐을 때만 한다. 생성 전에 배경 교체로 보내던
판단(잘림 · 겹침 · 여러 개 · 사용감 · 문서 · 내부 · 글자)은 지웠다 — 먼저 생성해 보고 얼마나 깨지는지 본다 (10-11).

파일 지도 (읽는 순서):
    state.py      State · 재시도 · 백그라운드 실행
    analysis.py   원본 분석(analyze) · 글자 읽기 · 게시글 구성품 캐시
    selection.py  팔 물건 고르기 · 세트 구성품 세기
    routing.py    갈림길 — 생성 / 배경 교체 / 글자 읽기
    generation.py 정답 구도 · 프롬프트 조립 · generate
    checks.py     생성 뒤 검사 — 사진 성립 · 마크 보존 · 개수 · 재생성
    composite.py  배경 교체 · 원본 그대로
    results.py    inspect · 성적표(eval · dev) · 누끼 비교 · DB 기록
    graph.py      그래프 조립 · run_transform

여기서는 아래 모듈의 이름을 전부 다시 내보낸다 — `from app.services import pipeline; pipeline.run_transform(...)` 그대로.
`pipeline.X = ...`(테스트의 monkeypatch 포함)은 X 를 가진 하위 모듈에도 같이 들어간다 — 한 파일이던 때와 같게.
"""
import sys
import time  # noqa: F401 — 예전 한 파일의 이름들 (pipeline.time · pipeline.guards 를 쓰는 곳이 있다)
import types

from app.core.config import settings  # noqa: F401
from app.services import style_refs  # noqa: F401
from app.services.ai import compositor, detector, embedder, judge  # noqa: F401
from app.services.persistence import storage, store  # noqa: F401
from app.services.quality import guards  # noqa: F401
from app.services.pipeline import (analysis, checks, composite, generation, graph, results, routing, selection,
                                   state)

_MODULES = (state, analysis, selection, routing, generation, checks, composite, results, graph)
for _m in _MODULES:
    globals().update({k: v for k, v in vars(_m).items() if not k.startswith("__")})
del _m


class _Pipeline(types.ModuleType):
    """이름을 바꾸면 그 이름을 쓰는 하위 모듈에도 — 함수는 자기 모듈의 이름을 보므로 (10-11 파일 나누기)."""

    def __setattr__(self, name, value):
        owners = [m for m in _MODULES if name in vars(m)]
        if not owners and not name.startswith("__"):
            # 어느 하위 모듈도 안 쓰는 이름 — 바꿔치기가 실제 코드에 안 들어간다 (조용한 실패 방지, 10-11 리뷰)
            raise AttributeError(f"pipeline.{name}: 하위 모듈 어디에도 없는 이름")
        super().__setattr__(name, value)
        for m in owners:
            setattr(m, name, value)

    def __delattr__(self, name):
        super().__delattr__(name)
        for m in _MODULES:
            if name in vars(m):
                delattr(m, name)


sys.modules[__name__].__class__ = _Pipeline
