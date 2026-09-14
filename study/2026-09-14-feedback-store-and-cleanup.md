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
- [ ] `backend/data/carret.db`의 더미 데이터 정리(원할 때 수동 삭제)
