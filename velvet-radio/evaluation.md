# Velvet Radio — Evaluation.md
## 코드 검증 · 최적화 · 테스트 · 배포 피드백 프레임워크

> 버전: 1.0 | 작성일: 2026-04-06
> 주기: **Research → Plan → Evaluation → SEO → Research** 사이클에서 3번째 단계
> 목적: 각 Sprint 완료 시 코드 품질, 성능, 테스트 커버리지, 배포 안정성을 체계적으로 평가

---

## 1. Evaluation 사이클 개요

```
[Research] ──→ [Plan] ──→ [Evaluation] ──→ [SEO] ──→ [Research]
                              │
                    ┌─────────┴─────────┐
                    │                   │
               코드 검증            성능 측정
               테스트 실행          배포 검증
               오류 수정            피드백 반영
```

### 1.1 Evaluation 트리거 조건

| 트리거 | 설명 |
|--------|------|
| Sprint 완료 시 | 각 Sprint 종료 후 자동 실행 |
| PR 머지 전 | main 브랜치 병합 전 필수 통과 |
| 플레이리스트 생성 실패 시 | 파이프라인 에러 발생 후 즉시 실행 |
| 주 1회 정기 | 매주 월요일 8AM KST 자동 실행 |

---

## 2. 코드 검증 (Code Validation)

### 2.1 정적 분석 체크리스트

```bash
# 전체 린팅 + 타입 체크 실행
make lint

# 상세 명령어
ruff check src/ tests/ dashboard/ --output-format=full
mypy src/ --ignore-missing-imports --strict
```

| 검사 항목 | 도구 | 기준 | 현재 상태 |
|-----------|------|------|-----------|
| 문법 오류 | ruff | 0 오류 | ⬜ 미확인 |
| 타입 안전성 | mypy | 0 에러 | ⬜ 미확인 |
| 미사용 변수 | ruff F841 | 0개 | ⬜ 미확인 |
| 임포트 정리 | ruff I | 통과 | ⬜ 미확인 |
| 보안 취약점 | bandit | 높음 0개 | ⬜ 미확인 |

### 2.2 코드 품질 기준 (필수 통과)

```
✅ 함수 복잡도: max Cyclomatic Complexity 10 이하
✅ 파일당 라인 수: 500줄 이하
✅ 함수당 라인 수: 80줄 이하
✅ 모든 공개 함수에 docstring 존재
✅ .env 파일 코드에 하드코딩 금지
✅ API 키 로그 출력 금지
```

### 2.3 보안 검사

```bash
# 환경변수 하드코딩 검출
grep -rn "sk-ant-" src/ && echo "🚨 API 키 하드코딩 발견!" || echo "✅ 통과"
grep -rn "Bearer " src/ --include="*.py" | grep -v "config\|env\|test"

# bandit 보안 스캔
pip install bandit
bandit -r src/ -ll
```

**금지 패턴:**
- API 키를 코드에 직접 기입
- `eval()` 또는 `exec()` 사용
- `shell=True` subprocess (FFmpeg 명령어 제외)
- SQL 쿼리 문자열 포매팅

---

## 3. 테스트 체계 (Test Strategy)

### 3.1 테스트 레이어

```
Layer 4: E2E Tests           - 전체 파이프라인 1회 실행 검증
           ↑
Layer 3: Integration Tests   - Phase 간 연동 테스트
           ↑
Layer 2: Component Tests     - 각 모듈 단위 테스트
           ↑
Layer 1: Unit Tests          - 함수/메서드 단위 테스트
```

### 3.2 테스트 실행 명령어

```bash
# 전체 테스트 (느림)
make test

# 빠른 테스트 (E2E 제외)
make test-fast

# 커버리지 포함
make test-cov

# 특정 파일만
pytest tests/test_style_assembler.py -v

# 마커별 실행
pytest -m "not slow and not e2e" -v
```

### 3.3 커버리지 목표

| 모듈 | 목표 커버리지 | 우선순위 |
|------|-------------|---------|
| `src/common/models.py` | ≥ 95% | P0 |
| `src/phase1_concept/style_assembler.py` | ≥ 90% | P0 |
| `src/phase1_concept/lyrics_generator.py` | ≥ 85% | P0 |
| `src/phase2_music/quality_filter.py` | ≥ 90% | P0 |
| `src/phase3_video/subtitle_generator.py` | ≥ 85% | P1 |
| `src/phase4_publish/seo_generator.py` | ≥ 80% | P1 |
| `src/phase4_publish/approval_manager.py` | ≥ 85% | P0 |
| `dashboard/app.py` | ≥ 70% | P2 |
| **전체 평균** | **≥ 85%** | - |

### 3.4 품질 게이트 (자동 차단 조건)

아래 조건 중 하나라도 실패 시 배포 차단:

```
❌ 테스트 실패 1건 이상
❌ 커버리지 85% 미만
❌ 린팅 오류 1건 이상
❌ mypy 에러 1건 이상 (critical 모듈)
❌ 보안 High severity 1건 이상
```

### 3.5 현재 테스트 파일 목록

| 파일 | 테스트 대상 | 케이스 수 |
|------|------------|----------|
| `tests/test_models.py` | Pydantic 데이터 모델 | 15+ |
| `tests/test_style_assembler.py` | 스타일 프롬프트 조립 | 12+ |
| `tests/test_lyrics_generator.py` | 가사 검증/수정 | 5+ |
| `tests/test_quality_filter.py` | 오디오 품질 채점 | 7+ |
| `tests/test_subtitle_generator.py` | SRT 자막 생성 | 6+ |

---

## 4. 성능 최적화 (Performance Optimization)

### 4.1 Phase별 성능 목표

| Phase | 작업 | 목표 시간 | 현재 측정값 |
|-------|------|----------|------------|
| Phase 1 | 테마 + 20곡 가사 생성 | < 5분 | ⬜ 미측정 |
| Phase 2 | 40개 변형 병렬 생성 | < 60분 | ⬜ 미측정 |
| Phase 2 | 품질 선별 (20곡) | < 3분 | ⬜ 미측정 |
| Phase 3 | 이미지 생성 | < 5분 | ⬜ 미측정 |
| Phase 3 | 20개 트랙 영상 렌더링 | < 20분 | ⬜ 미측정 |
| Phase 3 | 최종 합본 | < 10분 | ⬜ 미측정 |
| Phase 4 | SEO 메타데이터 | < 1분 | ⬜ 미측정 |
| Phase 4 | YouTube 업로드 (1GB+) | < 15분 | ⬜ 미측정 |
| **전체** | **E2E 파이프라인** | **< 2.5시간** | ⬜ 미측정 |

### 4.2 성능 측정 방법

```python
# 각 Phase에 타이머 삽입 (scheduler.py에 이미 구현)
from datetime import datetime

t0 = datetime.utcnow()
# ... Phase 실행 ...
duration = (datetime.utcnow() - t0).total_seconds()
logger.info("Phase 완료", duration_seconds=duration)
```

```bash
# 로그에서 성능 데이터 추출
python -c "
import json
from pathlib import Path

for line in Path('logs/velvet_radio.jsonl').read_text().splitlines():
    data = json.loads(line)
    if 'duration_seconds' in data:
        print(f\"{data.get('phase','?')}: {data['duration_seconds']:.1f}s\")
"
```

### 4.3 최적화 우선순위

**[P0] 즉시 적용 최적화:**
- Suno 배치 동시 처리 수: `MAX_CONCURRENT_SUNO_JOBS=3` → 서버 상황에 따라 조정
- Claude API 캐싱: 동일 트랙 유형에 대한 프롬프트 캐시 활용
- FFmpeg preset: `medium` → `fast` (화질 미미한 차이, 속도 2배 향상)

**[P1] Sprint 2~3 적용:**
- Phase 1 가사 생성 병렬화: asyncio 활용 (현재 순차 처리)
- 이미지 생성과 음원 생성 동시 실행 (Phase 2~3 오버랩)
- 렌더링 완료된 트랙부터 QA 시작

**[P2] 안정화 후 적용:**
- librosa 분석 결과 캐싱 (동일 파일 재분석 방지)
- n8n 워크플로 분기: 빠른 경로 / 안전 경로 선택

---

## 5. 오류 분류 및 대응 (Error Classification)

### 5.1 오류 심각도 분류

| 심각도 | 정의 | 대응 | 예시 |
|--------|------|------|------|
| **Critical** | 파이프라인 전체 중단 | 즉시 Telegram 알림 + 수동 개입 | YouTube API 인증 만료 |
| **High** | 1개 Phase 실패 | 재시도 3회 후 해당 Phase 건너뜀 | Suno API 타임아웃 |
| **Medium** | 일부 트랙 실패 | 나머지 트랙으로 계속 진행 | 단일 트랙 생성 실패 |
| **Low** | 데이터 품질 이슈 | 로깅 후 계속 | LUFS 목표 범위 초과 |
| **Warning** | 성능 저하 | 로깅만 | 렌더링 목표 시간 초과 |

### 5.2 공통 오류 패턴 및 해결

| 오류 | 원인 | 해결 |
|------|------|------|
| `SunoAPIError: job_id 없음` | API 응답 구조 변경 | `suno_client.py` 파싱 로직 업데이트 |
| `Claude API RateLimitError` | 동시 호출 초과 | `tenacity` 재시도 대기 시간 증가 |
| `FFmpeg 실패: codec not found` | FFmpeg 설치 누락 | `apt install ffmpeg` |
| `YouTube 401 Unauthorized` | Refresh Token 만료 | OAuth 재인증 (`make yt-auth`) |
| `ValidationError: 트랙 수 부족` | Phase 1 응답 파싱 오류 | Claude 프롬프트 강화 |
| `Quality score < 0.3 전트랙` | Suno API 품질 저하 | 스타일 프롬프트 수정 + 재생성 |

### 5.3 오류 로그 분석 쿼리

```bash
# 에러 레벨 로그 필터링
python -c "
import json
from pathlib import Path

for line in Path('logs/velvet_radio.jsonl').read_text().splitlines():
    try:
        d = json.loads(line)
        if d.get('level') in ('error', 'critical'):
            print(f\"[{d.get('timestamp','')}] {d.get('event','')} - {d.get('error','')}\")
    except:
        pass
"

# Phase별 실패 통계
python -c "
import json
from collections import Counter
from pathlib import Path

phases = Counter()
for line in Path('logs/velvet_radio.jsonl').read_text().splitlines():
    try:
        d = json.loads(line)
        if d.get('level') == 'error' and d.get('phase'):
            phases[d['phase']] += 1
    except:
        pass
print(dict(phases))
"
```

---

## 6. 배포 검증 (Deployment Validation)

### 6.1 배포 전 체크리스트

```
□ 1. 환경변수 확인
     python -c "from src.common.config_loader import config; print('OK')"

□ 2. FFmpeg 설치 확인
     ffmpeg -version

□ 3. API 연결 테스트
     python scripts/test_connections.py

□ 4. YouTube OAuth 유효성 확인
     python -c "from src.phase4_publish.youtube_uploader import YouTubeUploader; YouTubeUploader()"

□ 5. 디렉토리 쓰기 권한 확인
     python -c "from pathlib import Path; Path('data/test.txt').write_text('ok'); print('OK')"

□ 6. Telegram 알림 테스트
     python -c "from src.common.notifier import get_notifier; get_notifier()._send('✅ 배포 테스트')"

□ 7. 단위 테스트 전체 통과
     make test-fast

□ 8. Docker 컨테이너 헬스체크
     docker-compose up -d && sleep 30 && docker-compose ps
```

### 6.2 스모크 테스트 (배포 후 즉시)

```bash
# Phase 1만 실행하여 기본 동작 확인
make phase1

# 예상 출력:
# ✅ 플레이리스트 생성 완료: 20260406_golden-hour-café
# ✅ 가사 생성 완료: 20/20곡
# Telegram: "📝 [Velvet Radio] PHASE1 완료"
```

### 6.3 롤백 절차

```bash
# 빠른 롤백 (이전 Docker 이미지)
docker-compose down
git checkout HEAD~1
docker-compose build && docker-compose up -d

# 데이터 보존하면서 코드만 롤백
git stash
git checkout <previous-tag>
# 단, data/ 디렉토리는 유지
```

---

## 7. Evaluation 결과 기록 템플릿

Sprint 완료 후 아래 표를 채워 GitHub PR 또는 Notion에 기록:

```markdown
## Evaluation Report — Sprint [N] — [날짜]

### 테스트 결과
- 전체 테스트: ✅/❌ ([통과]/[전체])
- 커버리지: [XX]% (목표: 85%)
- 린팅: ✅/❌

### 성능 측정
| Phase | 소요 시간 | 목표 | 결과 |
|-------|----------|------|------|
| Phase 1 | Xs | 5분 | ✅/❌ |
| Phase 2 | Xs | 60분 | ✅/❌ |
...

### 발견된 버그 및 수정
1. [버그 설명] → [수정 내용]

### 다음 Sprint에 반영할 피드백
- [ ] 항목 1
- [ ] 항목 2

### SEO 단계로 전달할 인사이트
- [YouTube 알고리즘 관련 발견사항]
```

---

## 8. 지속적 개선 루프 (Research → Plan → Evaluation → SEO → Research)

```
Week 1:  Research (트렌드/API 변경 파악)
          ↓
Week 2:  Plan (아키텍처 업데이트)
          ↓
Week 3:  Evaluation (구현 + 테스트 + 성능 측정)
          ↓
Week 4:  SEO (메타데이터 전략 업데이트 + 조회수 분석)
          ↓
Week 5:  Research (결과 기반 다음 사이클 시작)
```

### 8.1 월간 KPI 추적

| KPI | 목표 | 측정 주기 |
|-----|------|---------|
| 파이프라인 성공률 | ≥ 90% | 매 실행 |
| 선별 곡 수 평균 | ≥ 18/20 | 매 플레이리스트 |
| E2E 완료 시간 | < 2.5시간 | 매 실행 |
| 테스트 커버리지 | ≥ 85% | 매 Sprint |
| 버그 재발율 | 0% | 매 Sprint |
| YouTube 업로드 성공률 | 100% | 매 플레이리스트 |
