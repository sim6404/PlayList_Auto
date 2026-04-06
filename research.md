# Velvet Radio — 기술 리서치 요약

> 작성일: 2026-04-04  
> 목적: AI 기반 이지리스닝 팝 플레이리스트 YouTube 채널 "Velvet Radio"의 자동화 파이프라인 구축을 위한 기술 리서치 정리

---

## 1. AI 음악 생성 — Suno AI

### 1.1 현재 상태 (2026년 4월 기준)

- **최신 모델**: Suno V5 (2025년 9월 23일 출시), V5.5 (2026년 3월 26일 출시)
- **V5 핵심 개선사항**:
  - 44.1kHz 스테레오 출력, 이전 버전의 배경 노이즈(haze) 거의 제거
  - 보컬 합성이 거의 사람과 구분 불가 수준으로 향상
  - 최대 8분 길이 곡 생성 가능 (Intelligent Composition Architecture)
  - 스튜디오 모드: 스템 분리, 섹션별 편집, 네거티브 프롬프트 지원
  - ELO 벤치마크 점수 1,293 (V4.5의 1,208 대비 상승)
- **V5.5 추가 기능**: 보이스 클론, 커스텀 모델 학습, My Taste 자동 학습 (프롬프트 시스템은 V5와 동일)

### 1.2 API 접근 방법

Suno는 2026년 4월 기준 **공식 API를 제공하지 않는다**. 다음 대안이 존재한다:

| 방법 | 비용 | 안정성 | 리스크 |
|------|------|--------|--------|
| **서드파티 API (APIPASS, Apiframe 등)** | $39~/월 | 높음 | 낮음 (계정 관리 대행) |
| **오픈소스 suno-api (gcui-art/suno-api)** | 무료 + 2Captcha 비용 | 중간 | CAPTCHA 빈도 변동 |
| **비공식 REST wrapper (SunoAPI.org 등)** | 크레딧 기반 | 높음 | 서비스 중단 가능성 |

- **오픈소스 suno-api 특징**: OpenAI 호환 `/v1/chat/completions` 형식, Docker/Vercel 배포 지원, 2Captcha 기반 자동 CAPTCHA 해결, LGPL-3.0 라이선스
- **서드파티 API 가격**: Suno Premier 구독 기준 곡당 약 $0.03~0.04, 벌크 할인 시 더 저렴

### 1.3 프롬프트 엔지니어링

#### 스타일 프롬프트 (최대 1,000자, V4.5+)
- **Top-Loaded Palette 공식**: `[Mood] + [Energy] + [2 Instruments] + [Vocal Identity]`
- 가장 중요한 태그를 앞에 배치 (제한 초과 시 경고 없이 잘림)
- V5는 감정적 디스크립터(raw, yearning, expansive 등)에 더 잘 반응
- 프로덕션 태그 효과적: `radio-ready mix`, `wide stereo field`, `analog warmth`
- 네거티브 프롬프트는 스타일 프롬프트 **끝**에 배치

#### 가사 프롬프트 (최대 3,000자)
- 메타태그: `[Verse]`, `[Chorus]`, `[Bridge]`, `[Outro]`, `[Instrumental]`, `[Interlude]`
- 보컬 퍼포먼스 태그: `[Whispered]`, `[Spoken Word]`, `[Belted]`, `[Soft]`, `[Breathy]`
- 인트로 악기 지정 가능: `[Intro: Acoustic guitar]`
- V5는 4줄 이내의 Verse에서 최적 성능
- 섹션 태그는 각자 별도 줄에 배치, 오타에 민감

### 1.4 저작권 및 상업적 이용

- **유료 플랜 필수**: Suno Pro($10/월) 또는 Premier($30/월) 구독 시 상업적 이용 및 저작권 귀속 가능
- **미국 저작권청 입장 (2026년)**: 순수 AI 생성물은 저작권 불가, 그러나 가사를 직접 작성하고 오디오를 상당 수준 수정(스템 활용)하면 인간 창작 요소에 대해 저작권 주장 가능
- **핵심 원칙**: AI를 도구로 사용하되, 인간의 창작적 개입(가사 작성, 프롬프트 설계, 후처리)을 명확히 할 것

---

## 2. 이미지 생성 — 썸네일 및 배경 아트

### 2.1 Midjourney

- 2026년 4월 기준 **공식 API 없음** (웹 인터페이스 + Discord 전용)
- V8 Alpha 출시 (2026년 3월 17일): 2K 해상도 네이티브 렌더링, 개선된 텍스트 렌더링, 4~5배 빠른 속도
- V7이 현재 기본 모델

#### 비공식 API 옵션

| 서비스 | 가격 | 특징 |
|--------|------|------|
| **Apiframe** | $39~/월 (900크레딧) | 관리형, 계정 밴 리스크 없음, Zapier/Make 연동 |
| **ImagineAPI** | $30~/월 (무제한) | REST API, CSV 일괄 프롬프트, Zapier 연동 |
| **PiAPI** | $0.01~/태스크 | 종량제, Suno/Dream Machine API도 제공 |

### 2.2 대안: Flux (공식 API 보유)

- Midjourney 수준 품질의 공식 API 제공 서비스
- 계정 밴 리스크 없음, REST API 표준 제공
- **추천**: 자동화 안정성이 중요하므로 Flux 또는 Ideogram을 1순위로 검토

---

## 3. 영상 제작 — FFmpeg 기반 자동화

### 3.1 CapCut API 상태

- 2026년 3월 기준 CapCut은 **자동화용 공개 API를 제공하지 않음**
- Open Platform은 에디터 내부 플러그인 전용 (서버사이드 영상 생성 불가)
- 오픈소스 CapCutAPI (sun-guannan/CapCutAPI) 존재: Python 기반, HTTP API + MCP 프로토콜 지원, 그러나 CapCut 소프트웨어 설치 필요

### 3.2 FFmpeg 기반 파이프라인 (추천 경로)

**오디오 스펙트럼 시각화**:
```bash
ffmpeg -i song.mp3 -filter_complex \
  "[0:a]showwaves=s=1920x1080:mode=cline:rate=30,format=yuv420p[v]" \
  -map "[v]" -map 0:a output.mp4
```

**추가 FFmpeg 활용**:
- `showfreqs`: 주파수 스펙트럼 시각화
- `avectorscope`: 벡터스코프 시각화
- 배경 이미지 오버레이: `-filter_complex "[1:v]overlay"`
- 자막 합성: `-vf subtitles=lyrics.srt`
- 복수 곡 합본: `-f concat -i filelist.txt`

### 3.3 AI Slop 방지 필수 요소

YouTube에서 AI 양산형 스팸으로 간주되지 않으려면 정지 이미지만으로는 불충분. 다음 중 1개 이상 반드시 포함:
- 음악 반응형 오디오 스펙트럼(파형)
- 가사 자막 (타임스탬프 동기화)
- 가벼운 애니메이션 효과 (줌 인/아웃, 패럴랙스)

---

## 4. YouTube 업로드 자동화

### 4.1 YouTube Data API v3

- 무료 (일일 쿼터 10,000 유닛)
- 영상 업로드 (resumable upload): 1,600 유닛/회
- 플레이리스트 생성: 50 유닛/회
- 플레이리스트에 영상 추가: 50 유닛/회

### 4.2 n8n 워크플로 자동화

- n8n에 YouTube 노드 내장: 업로드, 플레이리스트 관리, 메타데이터 설정
- Google Drive 연동: 파일 드롭 → 자동 감지 → 메타데이터 생성 → 예약 업로드
- 검증된 n8n 워크플로 템플릿 다수 존재:
  - "Automated music video creation & YouTube publishing" (Google Drive 트리거)
  - "Create complete 20-song YouTube playlists with Suno API, Claude & Telegram bot" (11개 워크플로 체인)
  - "Automated YouTube video scheduling & AI metadata generation"

### 4.3 SEO 메타데이터

- Claude API로 다국어(영·일·포르투갈어) 제목/설명/태그 자동 생성
- 글로벌 시장 타겟팅으로 해외 RPM 극대화
- 해시태그, 챕터 마커(타임스탬프) 자동 삽입

---

## 5. 음원 유통 — DistroKid

### 5.1 AI 음악 정책

- **DistroKid은 AI 생성 음악을 허용** (조건부):
  - AI 사용 명시 필수
  - 100% 권리 소유 확인
  - 타인의 목소리/아이덴티티 모방 금지
  - 알고리즘 게이밍 목적의 대량 스팸 금지
  - 스트리밍 서비스 콘텐츠 가이드라인 준수

### 5.2 주의사항

- DistroKid은 유통만 담당, **최종 권한은 DSP(Spotify, Apple Music 등)에 있음**
- DSP들의 자체 AI 탐지 시스템이 트랙을 제거할 수 있음
- 플래깅 리스크를 높이는 패턴:
  - 반복적인 AI 사운드 구조
  - 고빈도 릴리즈 패턴 (자동화처럼 보이는 계정)
  - 불충분한 메타데이터
  - 최소한의 후처리 흔적

### 5.3 유통 구조

- **가격**: Musician $24.99/년 (무제한 업로드)
- **유통 플랫폼**: Spotify, Apple Music, YouTube Music, TikTok, Amazon, Pandora, Deezer, TIDAL, 멜론 등 150+
- **지급**: 주 2회, 수익 100% 아티스트 귀속
- **자동화**: 공식 API 없음, 비공식 Go 래퍼(distrogo) 존재하나 읽기 전용, 업로드는 수동 필요

### 5.4 이중 수익 전략

| 수익원 | 경로 | 특성 |
|--------|------|------|
| YouTube AdSense | 1시간+ 플레이리스트 → 높은 시청 지속 시간 → 고RPM | 즉시 수익 |
| 스트리밍 로열티 | DistroKid → Spotify/Apple Music/멜론 등 | 3개월 후 지급, 연금형 |
| YouTube Content ID | DistroKid 경유 등록 시 타인 사용에 대한 수익 수집 | 패시브 수익 |

---

## 6. 오케스트레이션 — n8n

### 6.1 n8n 개요

- 오픈소스 워크플로 자동화 플랫폼
- Self-hosted 무료 (VPS $5~10/월) 또는 Cloud ($20~/월)
- 400+ 내장 통합, HTTP Request 노드로 모든 API 연동 가능
- AI Agent 노드를 통한 LLM 직접 통합

### 6.2 YouTube + Music 관련 검증된 워크플로

1. **Google Drive 트리거 → 자동 메타데이터 → YouTube 업로드**: MP3 드롭 시 장르 감지, 커버 아트 매핑, 설명/태그 생성, 예약 업로드까지 자동
2. **Telegram Bot → 20곡 플레이리스트 완전 자동화**: Claude API + Suno API + Google Drive 폴더 관리
3. **Suno + GPT-4 + Runway + Creatomate → YouTube**: 11개 워크플로 체인, 컨셉→곡→아트→영상→발행 전 파이프라인

### 6.3 기술 요구사항

- Node.js 18+ (n8n 실행 환경)
- YouTube OAuth 2.0 인증 설정
- Google Drive API 연동
- 외부 API 키: Claude API, Suno 서드파티 API, 이미지 생성 API

---

## 7. 비용 분석 (월간 예상)

| 항목 | 월 비용 | 비고 |
|------|---------|------|
| Suno AI Premier | $30 | 10,000 크레딧/월 (약 250곡) |
| Claude API (Sonnet) | $5~10 | 가사/메타데이터 생성 |
| 이미지 생성 (Flux/Apiframe) | $20~40 | 썸네일 + 배경 |
| VPS (n8n + FFmpeg) | $10~20 | 4GB RAM 이상 권장 |
| DistroKid | ~$2 | $24.99/년 |
| YouTube Data API | 무료 | 일일 쿼터 내 |
| **월 합계** | **$67~102** | |

주 2회 업로드 기준, 월 8~10개 플레이리스트 제작 가능.

---

## 8. 리스크 및 주의사항

| 리스크 | 심각도 | 대응 |
|--------|--------|------|
| Suno 비공식 API 중단 | 높음 | 2개 이상 API 소스 확보, 공식 API 출시 모니터링 |
| YouTube AI Slop 판정 | 높음 | 스펙트럼+자막+애니메이션 필수, 정지 이미지 단독 사용 금지 |
| DistroKid/DSP 트랙 제거 | 중간 | 후처리 흔적 남기기, 릴리즈 빈도 조절 (주 1~2곡) |
| 저작권 분쟁 | 중간 | 유료 플랜 사용, 가사 직접 작성, 아티스트 모방 금지 |
| n8n 워크플로 실패 | 낮음 | 에러 핸들링, 재시도 로직, Telegram 알림 |
