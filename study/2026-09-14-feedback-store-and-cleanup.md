# 2026-09-14 — 피드백 저장 기능 + 파일/경로 정리

## 1. 오늘 구현한 것: 피드백 저장/조회 기능

### 백엔드
- `backend/app/schemas/feedback.py`
  - `FeedbackRequest` (`file_id`는 업로드 규칙과 동일하게 hex32 패턴 검증, `rating`은 1~5 범위 검증)
  - `FeedbackResponse`
- `backend/app/api/routes/feedback.py`
  - `POST /api/feedback` → `store.save_feedback()` 호출 (upsert)
  - `GET /api/feedback/{file_id}/{preset_key}` → 없으면 404
- `backend/app/services/store.py` (기존)
  - SQLite `feedbacks` 테이블, `UNIQUE(file_id, preset_key)` + `ON CONFLICT DO UPDATE`로 중복 시 갱신
  - `db.get_conn()` 재사용
- `backend/main.py`
  - `feedback.router` 등록
  - **`db.init_db()` 를 앱 시작 시 호출하도록 추가** — 원래 어디서도 호출되지 않아서 `results`/`feedbacks` 테이블이 실제로 생성된 적이 없었음 (숨은 버그)

### 프론트엔드
- `index.html`: 결과 카드 아래 별점(1~5) + 코멘트 textarea + 전송 버튼 추가
- `render.js`: `setStars` / `resetFeedbackBox` / `renderFeedback` / `hideFeedbackBox` — 화면 렌더링만 담당 (서버 모름 원칙 유지)
- `api.js`: `submitFeedback()`, `fetchFeedback()` 추가
- `main.js`: 변환 성공 시 피드백 박스를 보여주고, 같은 `file_id`+`preset`으로 이미 남긴 피드백이 있으면 자동으로 채워줌(prefill)

### 검증
- `FastAPI TestClient`로 생성 → 200, 같은 키로 재전송 → upsert로 갱신, 존재하지 않는 키 조회 → 404, `rating=9` → 422 확인.

---

## 2. 파일 정리 기준

### 발견한 문제: `storage.py`에 구현이 두 번 겹쳐 있었음
S3 마이그레이션 작업 중 옛 로컬 전용 구현 위에 새 `LocalBackend`/`S3Backend` 구현을 이어붙인 흔적이 남아 있었음 (파일 중간에 모듈 docstring이 또 나옴, `BASE`가 두 번 정의됨). 그 결과:

- `BASE`가 `settings.storage_dir`(`./storage`)가 아니라 `Path(__file__).../data`로 **재정의**되어 있었음.
- 그런데 `images.py`의 파일 업로드는 `settings.storage_dir`에 **직접** 쓰고, `pipeline.py`/`dev.py`는 여전히 `storage.BASE`, `storage.original_of()`, `storage.result_url()`을 쓰고 있어서 — 업로드 경로로 무엇을 쓰느냐(파일 vs URL)에 따라 원본을 찾는 디렉터리가 달라지는 상태였음.
- 새 `save()`가 `img_util.normalize()` 호출을 빠뜨려서, 저장되는 이미지가 리사이즈/포맷 정규화 없이 그대로 저장되고 있었음(조용한 리그레션).

### 적용한 기준
1. **경로는 단일 진실원(Single Source of Truth) 하나만 둔다.** 로컬 백엔드는 `BASE = Path(settings.storage_dir)` 하나만 기준으로 삼고, `original_of()` / `result_url()` / dev 도구 / 파이프라인이 전부 같은 값을 보게 함.
2. **정규화 같은 공통 로직은 백엔드별로 중복시키지 않고 한 곳(모듈 레벨 `save()`)에만 둔다.** `LocalBackend`/`S3Backend`는 순수 저장/조회만 담당.
3. **비밀값은 항상 빈 문자열 기본값 + `.env`로만 채운다.** `config.py`에 하드코딩돼 있던 Langfuse 키 기본값을 `""`로 되돌림 (다른 시크릿 필드들과 동일한 규칙).
4. **외부 입력(.env)에 대한 스키마는 알 수 없는 값에 관대해야 한다.** `Settings`에 `extra="ignore"` 추가 — `.env`에 모델이 모르는 키(예: `AWS_ACCESS_KEY_ID`)가 있어도 앱이 부팅 자체를 못 하는 문제를 막음. (이 버그로 인해 앱이 아예 켜지지 않는 상태였음 — 피드백 기능 검증 중 발견)

### 손대지 않고 남겨둔 것 (범위 밖 / 별도 결정 필요)
- **`.env`가 `STORAGE_BACKEND=s3`로 실제 S3 버킷을 가리키고 있음.** 그런데 IAM 사용자(`carret-app`)에 `s3:PutObject` 권한이 없어서 실제 업로드가 막혀 있음 (`test_storage.py::test_save_caps_big_image`가 이걸로 실패 — 코드 문제 아님, AWS 콘솔에서 권한 추가 필요).
- **S3 백엔드와 `dev.py`/`pipeline.py`의 파일 직접 접근(`storage.BASE`) 간 통합은 미완성.** `original_of()`/`result_url()`/dev 라우트는 로컬 파일시스템 전제이므로, S3 모드에서는 이 경로들이 실제 S3 객체를 보지 못함. 로컬 개발은 정상 동작하지만, S3 배포 시 dev 도구·verify 단계는 별도 작업 필요.
- **`test/conftest.py`의 `isolated_storage` fixture**는 `storage.BASE`/`settings.storage_dir`만 패치하고 `storage_backend`는 그대로 둠 — 로컬 백엔드를 가정하고 짜인 테스트라, `.env`가 s3로 설정된 상태에서는 그 가정이 깨짐.
- `backend/.env`의 실 AWS 자격증명은 이번 세션 중 터미널에 한 번 평문 노출됨(사용자 확인 결과 로테이션 불필요로 판단, 별도 조치 없음).
- `backend/data/carret.db`에 이번 피드백 기능 검증용 더미 row가 남아있음 — 삭제는 파괴적 작업이라 자동 실행하지 않음.

## 3. 남은 TODO
- [ ] IAM 정책에 `s3:PutObject`/`GetObject`/`HeadObject` 권한 추가
- [ ] S3 백엔드일 때 dev 도구/verify 단계도 동작하도록 `storage` API 확장 (또는 dev 도구는 local 전용으로 명시)
- [ ] `test/conftest.py`가 `storage_backend`도 함께 `"local"`로 패치하도록 보강
- [x] `backend/data/carret.db`의 더미 데이터 정리 — 세션 중 수동 삭제함

---

## 4. 말풍선 위치 버그 수정 + tester/reviewer 서브에이전트 + PR 머지

### 말풍선(결함 오버레이) 위치가 안 맞던 버그
- **원인**: 결과 이미지 박스(`#result-canvas`)는 `object-fit: contain`으로 그려지는데, 이미지 비율이 박스(500×400)와 다르면 위아래(또는 좌우)에 레터박스 여백이 생김. 그런데 말풍선은 이 여백을 무시하고 박스 전체 기준 %로 그려지고 있어서 정사각형이 아닌 이미지마다 어긋났음.
- 게다가 `renderBubbles()`가 새 결과 이미지가 실제로 로드되기 **전에** 호출되고 있어서(직전 이미지의 크기로 계산), 타이밍도 꼬여 있었음.
- **적용한 기준**: 오버레이 좌표 계산은 항상 컨테이너가 아니라 "실제로 렌더링된 이미지 박스"를 기준으로 해야 한다 (`imageBoxInCanvas()` 추가 — `object-fit:contain`과 동일한 레터박스 계산을 JS로 재현). 그리고 크기 의존적인 렌더링은 반드시 해당 리소스의 `load` 이벤트 이후로 미룬다.
- 곁다리로 정리한 것: `initZoom()`이 정의만 되고 실제로 호출된 적이 없었음 + `render.js` 끝에 아직 정의도 안 된 함수(`initZoom`)를 참조하는 죽은 코드가 매 로드마다 콘솔 에러를 던지고 있었음 → 둘 다 정리하고 줌이 실제로 동작하도록 연결.
- `api.js`(통신 계층)가 DOM을 직접 건드리던 부분(메타칩/말풍선/게이트 렌더링)을 제거 — "통신 계층은 화면을 모른다"는 원래 설계 원칙을 다시 지키게 함.

### tester / reviewer 서브에이전트
- 사용자가 `.claude/agents/tester.md`(pytest 작성 전담), `.claude/agents/reviewer.md`(읽기 전용 diff 리뷰)를 만들어달라고 요청 + "기능 추가할 때마다 고려해줘"라고 지시 → 프로젝트 워크플로 메모리에 기록함.
- **주의할 점**: `.claude/agents/*.md`는 세션 시작 시점에 로드되는 목록이라, **같은 세션 안에서 방금 만든 에이전트는 바로 호출이 안 됨** (`Agent type 'tester' not found`). 이번엔 `general-purpose`에 같은 지시문을 넘겨서 대신 실행함. 다음 세션부터는 `tester`/`reviewer` 이름으로 정상 호출됨.
- 실행 결과: tester가 피드백/storage/config에 대해 pytest 24개 신규 작성 → 45 passed (기존 known-fail 1개 제외). 버그는 못 찾았지만, `.env`의 `LANGFUSE_SECRET_KEY`/`LANGFUSE_PUBLIC_KEY` 값이 접두어(`pk-lf`/`sk-lf`) 기준으로 서로 바뀐 것 같다고 보고함(로컬 `.env`라 직접 수정은 안 함).
- reviewer가 지적한 것 중 즉시 반영한 것: `GET /api/feedback/{file_id}/{preset_key}`에 `file_id` hex32 검증이 빠져있던 것(POST와 비대칭) → `Path(pattern=...)`로 고침. `main.js`에 결과 이미지 로드 실패(`onerror`) 처리가 없던 것 → 추가. 나머지(S3/파이프라인 미통합, `config.py`의 `extra="ignore"` 트레이드오프)는 이미 알고 있는 범위 밖 이슈라 유지.

### CI에서 발견한 진짜 버그: `boto3` 미설치
- `storage.py`가 이제 `import boto3`를 무조건 실행하는데, `.github/workflows/ci.yml`의 pip install 목록엔 `boto3`가 없었음 (requirements.txt엔 있지만 CI는 requirements.txt를 쓰지 않고 자체 목록을 하드코딩함 — 둘이 따로 놀고 있었음).
- **검증 방법**: 로컬에 CI와 똑같은 install 목록으로 깨끗한 venv를 만들고 `.env`를 잠깐 치워서(진짜 CI엔 `.env`가 없음 — gitignore돼서 체크아웃 안 됨) 재현 → `ModuleNotFoundError: No module named 'boto3'`로 실제 재현 확인 후 수정, 다시 재현해서 초록 확인.
- **교훈**: CI의 의존성 목록은 `requirements.txt`와 별개로 관리되는 "제2의 소스"라서 수동으로 동기화해줘야 함. 실제로 CI 환경을 흉내내서 검증하지 않으면 이런 드리프트는 로컬에서 절대 안 보임(로컬엔 이미 boto3가 깔려 있었으므로).
- 이 과정에서 알아낸 보너스: 로컬 `.env`가 `STORAGE_BACKEND=s3`라서 `test_save_caps_big_image`가 항상 AWS `AccessDenied`로 실패했던 건데, **진짜 GitHub Actions CI에는 `.env`가 없어서 기본값(`local`)으로 동작** → 이 테스트는 CI에서는 원래 통과함. 로컬 전용 문제였음.

### PR #11 → main 머지 + 이슈 정리
- 5개 커밋으로 나눠(feedback API/storage/config, 테스트, 프론트 버블+피드백 UI, CI boto3, 에이전트+study) → PR #11 오픈 → `free unit tests` 통과 확인 → merge.
- GitHub 이슈 검색 결과 **#10 "feat: frontend image zoom with bubble overlay"**가 이번에 고친 것과 정확히 일치(캔버스 동시 확대 + 버블 좌표 정규화) → PR 본문에 `Closes #10` 넣어서 머지 시 자동 종료. #9(detector 프롬프트), #2(eval recall/precision)는 무관해서 안 건드림.
- **알게 된 별개 이슈**: main에 push될 때 도는 `paid eval (main only)` 잡이 이번 push 포함 최근 여러 push에서 계속 실패 중 — 리포지토리에 `VLM_KEY`/`FAL_KEY` GitHub Secrets가 설정 안 되어 있어서(`ValueError: No API key was provided`). 이번 변경과 무관한 사전 존재 문제, 리포 Settings에서 시크릿을 넣어야 해결됨.

### Dockerfile / .dockerignore
- 이번 세션 시작 전부터 이미 untracked 상태로 있던 별개 작업물이라, PR에 포함하지 않고 그대로 손대지 않음.

## 5. README 갱신
- 위 4번까지의 내용(피드백 루프, 말풍선/줌 수정, 로컬·S3 스토리지, CI, 서브에이전트+study 워크플로)을 `Readme.md`에 반영: How It Works에 피드백 단계 추가, Key Features/Design Decisions/Failures & Lessons/Roadmap 갱신.
- 문서 전용 변경이라 별도 PR 없이 `main`에 직접 커밋/푸시함.
