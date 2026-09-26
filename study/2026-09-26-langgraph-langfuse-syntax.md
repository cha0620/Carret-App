# 2026-09-26 — LangGraph / Langfuse 문법 정리 (이 코드베이스에서 쓰는 것만)

대상 파일: `backend/app/services/pipeline.py`, `backend/app/core/tracing.py`,
`backend/app/core/prompt_registry.py`, `backend/app/services/ai/*.py`

---

# Part 1. LangGraph

## 1-1. import
```python
from langgraph.graph import END, START, StateGraph
```
| 이름 | 뜻 |
|---|---|
| `StateGraph` | 그래프 설계도 (노드/엣지를 붙이는 빌더) |
| `START` | 가상 시작 노드 — 첫 노드를 가리킬 때 |
| `END` | 가상 종료 노드 — 여기 도달하면 `invoke` 가 반환 |

## 1-2. State 정의 — `TypedDict`
```python
class State(TypedDict, total=False):
    file_id: str
    anchors: list
    gen_attempts: int
    ...
```
- 그래프 전체가 들고 다니는 공유 dict 의 **스키마**.
- `total=False` → 모든 키가 선택. 그래서 노드에서 `s.get("키", 기본값)` 으로 읽는다.
- 리듀서(`Annotated[list, add]` 등)를 안 썼으므로 **키마다 "덮어쓰기"** 가 기본 동작.

## 1-3. 노드 — `(State) -> dict`
```python
def detect(s: State) -> dict:
    ...
    return {"anchors": anchors}        # 바뀐 키만 반환
```
- 반환한 dict 가 State 에 **merge(덮어쓰기)** 된다.
- ⚠️ 반환 안 한 키는 **이전 값이 그대로 남는다** → 루프 재진입 시 오래된 값을
  명시적으로 초기화해야 함 (`mark_gate_retry` 가 `photo_check: None`,
  `guard_seed: None` 을 돌려주는 이유).
- 노드 안에서 예외를 던지면 그래프 전체가 중단되고 `invoke` 밖으로 예외가 나간다
  (`load` 의 `FileNotFoundError` → route 에서 404).

## 1-4. 그래프 조립
```python
g = StateGraph(State)
g.add_node("detect", detect)                 # 이름, 함수
g.add_edge(START, "load")                    # 무조건 이동
g.add_edge("load", "classify")
g.add_edge("finalize", END)
```

## 1-5. 조건부 엣지 — `add_conditional_edges`
```python
g.add_conditional_edges(
    "verify",                     # 출발 노드
    _route_after_verify,          # 라우터 함수: (State) -> str
    {"done": "save_inspect",      # 라벨 → 도착 노드 매핑
     "regen": "mark_gate_retry",
     "composite": "composite"},
)
```
- 라우터는 **State 를 읽기만** 하고 라벨 문자열만 반환 (수정 금지).
- 상태를 바꿔야 하면 → 별도 노드로 뺀다 (`mark_gate_retry` 가 그 예).
- 도착 노드를 이전 노드로 지정하면 **루프**가 된다:
  - `validate_result --retry--> generate`
  - `mark_gate_retry --> generate`
  - `composite --ok--> score_similarity`

## 1-6. 컴파일 & 실행
```python
GRAPH = g.compile()                           # 모듈 로드 시 1회 (재사용)
out = GRAPH.invoke(
    {"file_id": file_id, "preset_key": preset_key},   # 초기 State
    {"recursion_limit": RECURSION_LIMIT},             # config
)
# out = 최종 State (dict)
```
- `recursion_limit`: 노드 실행 1번 = 1 step. 넘으면 `GraphRecursionError`.
  기본 25 → 이 코드는 루프가 많아 80. **안전망일 뿐, 실제 종료는 State 의
  카운터/플래그가 책임** (`guard_seed`, `gen_attempts`, `gate_retried`, `mode`).

## 1-7. 그래프 없이 노드 직접 호출 (`run_transform_with_result`)
```python
s = {...}
s.update(load(s))
s.update(classify_node(s))
...
```
- 노드가 순수한 `(dict) -> dict` 라서 그래프 없이도 **수동으로 merge** 가능.
- 테스트 랩(dev)에서 "이미 있는 결과 이미지"로 뒷단만 돌릴 때 쓴다.

## 1-8. 루프 종료 장치 요약
| 루프 | 경로 | 종료 조건 (State) |
|---|---|---|
| 가드 재시도 | validate_result → generate | `guard_seed` 가 None 이 아니면 재시도 끝 |
| 구도 재생성 | validate_result → generate | `gen_attempts >= max_generate_attempts` |
| 게이트 재생성 | verify → mark_gate_retry → generate | `gate_retried == True` |
| composite 복귀 | composite → score_similarity → verify | `mode != "generate"` 면 즉시 done |

---

# Part 2. Langfuse

원칙: **키 없으면 완전 noop, 있는데 실패해도 로컬 fallback 으로 계속 동작.**
Langfuse SDK 를 직접 쓰는 곳은 `tracing.py`, `prompt_registry.py` 두 곳뿐이고,
나머지 코드는 이 래퍼만 쓴다.

## 2-1. 클라이언트 — `Langfuse(...)` (`tracing.py:get_langfuse`)
```python
from langfuse import Langfuse
_client = Langfuse(public_key=..., secret_key=..., host=...)
```
- 싱글톤 + double-checked locking (스레드풀 동시 첫 호출 대비).
- 키 없으면 `_disabled = True` → 이후 항상 `None` 반환.

## 2-2. 트레이스 구간 — `observe()` (우리 래퍼)
```python
with observe("transform", as_type="span", input={...}, metadata={...}) as obs:
    ...
    if obs is not None:
        obs.update(output={...})
```
내부 SDK 호출:
```python
with lf.start_as_current_observation(name=..., as_type=..., **kw) as obs:
    yield obs
```
- `start_as_current_observation`: 구간을 열고 **현재 컨텍스트로 push** →
  이 안에서 연 `observe` 는 자동으로 자식이 된다 (트리 구조).
- 예외가 나면 SDK 가 ERROR 로 표시하고 다시 던진다.
- 키 없으면 `obs = None` → 그래서 항상 `if obs is not None` 체크.

### `as_type` 종류 (이 코드에서 쓰는 것)
| as_type | 용도 | 쓰는 곳 |
|---|---|---|
| `"span"` | 일반 구간 (묶음) | `pipeline.run_transform` (`transform`, `transform_dev`) |
| `"generation"` | LLM/생성 모델 호출 — model, 토큰, 비용 집계 | `detector._call`, `detector.match_anchors`, `judge`, `auto_feedback`, `generator` |
| `"embedding"` | 임베딩 계산 | `embedder` (`dino_similarity`) |

### generation 패턴 (`detector._call`)
```python
with observe(name, as_type="generation", model=settings.VLM_MODEL,
             input=prompt) as obs:
    resp = client.models.generate_content(...)
    data = json.loads(resp.text)
    if obs is not None:
        obs.update(output=data, usage_details=_usage(resp))
```
- `model=` → Langfuse 가 모델 단가로 비용 계산.
- `usage_details=` → 토큰 수. `gemini_usage(resp)` 가 Gemini 의
  `usage_metadata` 를 `{"input", "output", "thoughts", "total"}` 로 변환.
  thinking 토큰은 출력 단가로 청구되므로 `output` 에 합산.

## 2-3. 점수 — `score()` (우리 래퍼)
```python
score("visual_similarity", sim, data_type="NUMERIC")
```
내부: `lf.score_current_trace(name=..., value=..., **kw)`
- **현재 활성 트레이스**(= 바깥 `observe("transform")`)에 점수를 붙인다.
  → 반드시 `observe` 컨텍스트 안에서 호출돼야 의미가 있음.
- 쓰는 곳: `score_similarity` (DINOv2), `run_judge` (루브릭 축별), `guards` (가드 값).

## 2-4. 전송 — `flush()`
```python
try:
    with observe(...):
        ...
finally:
    flush()        # 내부: lf.flush()
```
- SDK 는 배치로 백그라운드 전송 → 요청 끝나면 즉시 밀어내기.
- `finally` 에 둬서 **실패한 요청의 트레이스도 유실 없이** 보낸다.

## 2-5. 프롬프트 관리 — `get_prompt_text()` (`prompt_registry.py`)
```python
text = get_prompt_text("judge_system", fallback=TEMPLATE, rubric=rubric_text())
```
내부 SDK 호출:
```python
prompt = lf.get_prompt(name, label="production", fallback=fallback,
                       max_retries=0, fetch_timeout_seconds=3)
return prompt.compile(**variables)
```
| 인자 | 뜻 |
|---|---|
| `label="production"` | 콘솔에서 production 라벨 붙은 버전만 가져옴 |
| `fallback=` | 조회 실패 시 SDK 가 이 문자열을 프롬프트로 감싸서 반환 |
| `max_retries=0`, `fetch_timeout_seconds=3` | Langfuse 막혀도 요청이 오래 멈추지 않게 |
| `.compile(**vars)` | `{{var}}` 자리에 값 채우기 |

- 변수 문법은 **이중 중괄호 `{{name}}`** — 프롬프트 안 JSON 예시(`{"k": 1}`)와 충돌 방지.
- 키 없음 → `_compile_locally` 가 `"{{k}}"` 문자열 치환만 (`str.format` 안 씀).

---

# Part 3. 둘이 만나는 지점 (`run_transform`)
```python
try:
    with observe("transform", as_type="span", input=..., metadata=...) as obs:  # Langfuse 루트 span
        out = GRAPH.invoke(initial_state, {"recursion_limit": 80})              # LangGraph 실행
        #   └ 노드들 안의 observe(...generation...) 들이 전부 자식으로 붙음
        #   └ score(...) 들이 이 트레이스에 붙음
        result = {...}
        if obs is not None:
            obs.update(output={...})
finally:
    flush()
```
Langfuse 화면에서 보이는 트리 (예):
```
transform (span)
├─ classify (generation)
├─ item_text (generation)
├─ detect (generation)
├─ generate_image (generation)
├─ check_photo (generation)
├─ dino_similarity (embedding)
├─ verify (generation)
├─ judge (generation)
└─ scores: visual_similarity, fidelity, ...
```
※ 이 코드는 LangGraph 의 LangChain 콜백 연동(`CallbackHandler`)을 **쓰지 않는다** —
노드 단위 span 은 자동으로 안 생기고, 우리가 `observe` 를 건 호출만 보인다.
