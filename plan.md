# Velvet Radio — 자동화 파이프라인 아키텍처 설계서 (Plan.md)

> 작성일: 2026-04-04  
> 버전: 1.0  
> 상태: 구현 대기  
> 목표: "Velvet Radio" 이지리스닝 팝 플레이리스트 채널의 음악 생성 → 영상 제작 → YouTube 업로드 전 과정 자동화

---

## 1. 시스템 개요

### 1.1 아키텍처 한 줄 요약

```
n8n Cron → Claude API (컨셉+가사) → Suno API (음악) → FFmpeg (영상) → YouTube API (업로드)
```

### 1.2 설계 원칙

1. **모듈형 구조**: 각 Phase를 독립 모듈로 구현하여 개별 테스트·교체 가능
2. **실패 격리**: 한 모듈의 실패가 전체 파이프라인을 멈추지 않도록 큐 기반 처리
3. **설정 분리**: 채널 DNA, API 키, 경로 등 모든 설정은 `config/` 디렉토리에 분리
4. **점진적 구현**: Phase 1~2를 먼저 완성한 후 Phase 3~5를 순차 추가

---

## 2. 디렉토리 구조

```
velvet-radio/
├── config/
│   ├── channel_dna.json          # 채널 정체성 설정
│   ├── suno_prompts.json         # Suno 스타일 프롬프트 템플릿
│   ├── vocal_personas.json       # 보컬 페르소나 라이브러리
│   └── .env                      # API 키 및 환경변수
│
├── src/
│   ├── phase1_concept/
│   │   ├── theme_generator.py    # 플레이리스트 테마 생성
│   │   ├── lyrics_generator.py   # 곡별 가사 생성
│   │   ├── style_assembler.py    # Suno 스타일 프롬프트 조립
│   │   └── prompts/
│   │       ├── system_theme.txt  # 테마 생성 시스템 프롬프트
│   │       └── system_lyrics.txt # 가사 생성 시스템 프롬프트
│   │
│   ├── phase2_music/
│   │   ├── suno_client.py        # Suno API 클라이언트
│   │   ├── music_generator.py    # 일괄 음악 생성 오케스트레이터
│   │   ├── quality_filter.py     # 품질 필터링 (오디오 분석)
│   │   └── storage_manager.py    # Google Drive 업로드
│   │
│   ├── phase3_video/
│   │   ├── image_generator.py    # 썸네일/배경 이미지 생성
│   │   ├── spectrum_renderer.py  # FFmpeg 오디오 스펙트럼 렌더링
│   │   ├── subtitle_generator.py # 가사 SRT 생성
│   │   ├── video_compositor.py   # 이미지+스펙트럼+자막 합성
│   │   └── playlist_concat.py    # 복수 곡 1시간+ 영상 합본
│   │
│   ├── phase4_publish/
│   │   ├── seo_generator.py      # 다국어 SEO 메타데이터 생성
│   │   ├── youtube_uploader.py   # YouTube Data API v3 업로드
│   │   └── scheduler.py          # 업로드 예약 관리
│   │
│   ├── phase5_distribute/
│   │   ├── distrokid_prep.py     # DistroKid 업로드 패키지 준비
│   │   └── revenue_tracker.py    # 수익 추적 대시보드 데이터
│   │
│   └── common/
│       ├── claude_client.py      # Claude API 래퍼
│       ├── config_loader.py      # 설정 로더
│       ├── logger.py             # 구조화된 로깅
│       ├── notifier.py           # Telegram/Discord 알림
│       └── models.py             # 공통 데이터 모델 (Pydantic)
│
├── n8n/
│   └── workflows/
│       ├── W1_theme_lyrics.json  # Phase 1 워크플로
│       ├── W2_music_gen.json     # Phase 2 워크플로
│       ├── W3_video_prod.json    # Phase 3 워크플로
│       ├── W4_publish.json       # Phase 4 워크플로
│       └── W5_distribute.json    # Phase 5 워크플로
│
├── data/
│   ├── playlists/                # 생성된 플레이리스트 JSON
│   ├── lyrics/                   # 생성된 가사 파일
│   ├── audio/                    # 다운로드된 음원
│   ├── images/                   # 생성된 이미지
│   ├── videos/                   # 렌더링된 영상
│   └── exports/                  # DistroKid 업로드용 패키지
│
├── tests/
│   ├── test_theme_generator.py
│   ├── test_lyrics_generator.py
│   ├── test_suno_client.py
│   ├── test_spectrum_renderer.py
│   └── test_youtube_uploader.py
│
├── requirements.txt
├── docker-compose.yml            # n8n + Python worker 컨테이너
├── Makefile                      # 주요 명령어 단축
└── README.md
```

---

## 3. 데이터 모델 (Pydantic)

### 3.1 핵심 모델

```python
from pydantic import BaseModel
from typing import Optional
from enum import Enum

class VocalPersona(str, Enum):
    VR_F1 = "warm female, breathy, intimate, subtle vibrato"
    VR_F2 = "bright female, airy, youthful, light falsetto"
    VR_M1 = "smooth male, warm baritone, relaxed delivery"

class Mood(str, Enum):
    COZY = "cozy"
    NOSTALGIC = "nostalgic"
    DREAMY = "dreamy"
    GOLDEN_HOUR = "golden-hour"
    RAINY_DAY = "rainy-day"
    SUNDAY_MORNING = "sunday-morning"
    LATE_NIGHT = "late-night"
    BREEZY = "breezy"

class Track(BaseModel):
    order: int
    title: str
    mood: Mood
    sub_genre: str
    bpm: int                          # 75~105 범위
    key: str                          # 예: "G major"
    vocal: VocalPersona
    hook_priority: bool = False       # 초반 3곡 여부
    concept_note: Optional[str] = None

class Lyrics(BaseModel):
    track_order: int
    content: str                      # Suno 메타태그 포함 가사
    language: str = "en"
    char_count: int                   # 3,000자 이내 검증용

class StylePrompt(BaseModel):
    track_order: int
    prompt: str                       # 1,000자 이내
    negative: str                     # 네거티브 프롬프트

class SunoPayload(BaseModel):
    lyrics: str
    style_prompt: str
    model: str = "v5"
    instrumental: bool = False

class Playlist(BaseModel):
    id: str                           # YYYYMMDD_theme_slug 형식
    theme: str
    concept: str
    tracks: list[Track]
    created_at: str

class VideoAsset(BaseModel):
    playlist_id: str
    thumbnail_path: str
    background_path: str
    spectrum_video_path: str
    subtitle_path: str
    final_video_path: str
    duration_seconds: int

class YouTubeUpload(BaseModel):
    playlist_id: str
    video_path: str
    title: str
    description: str
    tags: list[str]
    thumbnail_path: str
    scheduled_at: Optional[str] = None
    privacy: str = "private"          # 예약 업로드 시 private → public
```

---

## 4. Phase별 상세 설계

### 4.1 Phase 1 — 컨셉 기획 + 작사

#### 모듈: `theme_generator.py`

```
입력: channel_dna.json
처리: Claude API 호출 → 테마 + 20곡 트랙리스트 JSON 생성
출력: data/playlists/{playlist_id}.json
```

**Claude API 호출 사양:**
- 모델: `claude-sonnet-4-20250514`
- max_tokens: 4,000
- system prompt: `prompts/system_theme.txt` + `channel_dna.json` 주입
- temperature: 0.8 (창의적 변주 허용)

**핵심 로직:**
1. `channel_dna.json`에서 서브장르 팔레트, 무드 매트릭스 로드
2. 이전 플레이리스트 테마 히스토리 조회 → 중복 방지
3. Claude API 호출 → JSON 응답 파싱 → `Playlist` 모델로 유효성 검증
4. 초반 3곡에 `hook_priority: true` 자동 배정 확인
5. `data/playlists/` 에 저장

#### 모듈: `lyrics_generator.py`

```
입력: Playlist.tracks (20곡 Track 목록)
처리: 곡별 Claude API 호출 → 메타태그 포함 가사 생성
출력: data/lyrics/{playlist_id}/track_{order}.txt
```

**Claude API 호출 사양:**
- 모델: `claude-sonnet-4-20250514`
- max_tokens: 1,500/곡
- system prompt: `prompts/system_lyrics.txt`
- user message: Track JSON + 가사 규칙

**가사 규칙 (시스템 프롬프트에 포함):**
1. Suno 메타태그(`[Verse]`, `[Chorus]`, `[Bridge]`, `[Outro]`) 필수
2. 보컬 퍼포먼스 태그(`[Soft]`, `[Breathy]`, `[Whispered]`) 적극 활용
3. 각 Verse 4줄 이내
4. `[Instrumental]` 또는 `[Interlude]` 1개 이상 포함
5. 총 3,000자 이내

**후처리:**
- 문자 수 검증 (3,000자 초과 시 Bridge 축소)
- 메타태그 존재 여부 정규식 검증
- `Lyrics` 모델로 변환 후 저장

#### 모듈: `style_assembler.py`

```
입력: Track + channel_dna.json
처리: 규칙 기반 스타일 프롬프트 조립 (API 호출 불필요)
출력: StylePrompt 객체
```

**조립 공식:**
```python
def assemble_style_prompt(track: Track, dna: dict) -> StylePrompt:
    parts = []
    
    # 1. Mood + Energy (최우선)
    parts.append(f"{track.mood.value}, gentle energy")
    
    # 2. Genre
    parts.append(f"{track.sub_genre}")
    
    # 3. Instruments (서브장르별 매핑 테이블 활용)
    instruments = INSTRUMENT_MAP[track.sub_genre]
    parts.append(", ".join(instruments[:3]))
    
    # 4. Vocal Identity
    parts.append(track.vocal.value)
    
    # 5. Production specs
    parts.append(f"{track.bpm} BPM, {track.key}")
    parts.extend(dna["production_tags"])
    
    prompt = ", ".join(parts)
    negative = ", ".join(dna["negative_tags"])
    
    # 1,000자 제한 검증
    if len(prompt) > 950:
        prompt = prompt[:950]
    
    return StylePrompt(
        track_order=track.order,
        prompt=prompt,
        negative=negative
    )
```

**서브장르별 악기 매핑 테이블:**
```python
INSTRUMENT_MAP = {
    "acoustic bossa nova pop":  ["nylon guitar", "soft percussion", "light piano"],
    "soft jazz pop":            ["jazz piano", "upright bass", "brushed drums"],
    "lo-fi dream pop":         ["lo-fi keys", "ambient pads", "tape-saturated drums"],
    "warm retro city pop":     ["electric piano", "funk guitar", "warm synths"],
    "mellow folk pop":         ["acoustic guitar", "gentle strings", "soft harmonica"],
}
```

---

### 4.2 Phase 2 — 음악 생성

#### 모듈: `suno_client.py`

```
입력: SunoPayload (lyrics + style_prompt)
처리: Suno 서드파티 API 호출 → 음원 다운로드
출력: data/audio/{playlist_id}/track_{order}_{variant}.mp3
```

**API 클라이언트 설계:**
```python
class SunoClient:
    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url     # 서드파티 API 엔드포인트
        self.session = httpx.AsyncClient(timeout=300)
    
    async def generate(self, payload: SunoPayload) -> dict:
        """곡 생성 요청 → job_id 반환"""
        
    async def poll_status(self, job_id: str) -> dict:
        """생성 상태 폴링 (exponential backoff)"""
        
    async def download(self, audio_url: str, path: str) -> str:
        """완성된 음원 다운로드"""
        
    async def generate_batch(self, payloads: list[SunoPayload], 
                              variants: int = 2) -> list[dict]:
        """20곡 × 2변형 = 40곡 일괄 생성 (동시 처리 수 제한)"""
```

**에러 핸들링:**
- API Rate Limit: exponential backoff (1s → 2s → 4s → 8s, 최대 5회)
- 생성 실패: 최대 3회 재시도 후 스킵, 알림 발송
- 타임아웃: 5분 제한, 초과 시 재요청

#### 모듈: `quality_filter.py`

```
입력: 40개 음원 파일 (20곡 × 2변형)
처리: 오디오 분석 → 품질 점수 산출 → 곡당 상위 1개 선택
출력: 선별된 20개 음원 + quality_report.json
```

**품질 평가 기준:**
```python
class QualityMetrics:
    duration_ok: bool          # 2분~4분 범위 내
    silence_ratio: float       # 무음 비율 5% 이하
    clipping_detected: bool    # 클리핑 없음
    loudness_lufs: float       # -14 ~ -10 LUFS 범위
    spectral_bandwidth: float  # 주파수 대역폭 (너무 좁으면 저품질)
```

**라이브러리:** `librosa` (오디오 분석), `pyloudnorm` (LUFS 측정)

---

### 4.3 Phase 3 — 영상 제작

#### 모듈: `spectrum_renderer.py`

```
입력: 음원 MP3 + 배경 이미지
처리: FFmpeg로 오디오 스펙트럼 + 배경 합성
출력: 곡별 영상 MP4
```

**FFmpeg 명령어 템플릿:**
```python
FFMPEG_SPECTRUM_CMD = """
ffmpeg -y \
  -loop 1 -i {background_image} \
  -i {audio_file} \
  -filter_complex "
    [0:v]scale=1920:1080,setsar=1[bg];
    [1:a]showwaves=s=1920:200:mode=cline:rate=30:
      colors=white@0.6:scale=sqrt,format=rgba[wave];
    [bg][wave]overlay=0:H-220:shortest=1[v]
  " \
  -map "[v]" -map 1:a \
  -c:v libx264 -preset medium -crf 20 \
  -c:a aac -b:a 192k \
  -shortest \
  {output_path}
"""
```

#### 모듈: `playlist_concat.py`

```
입력: 20개 곡별 영상 MP4
처리: FFmpeg concat으로 1시간+ 영상 생성
출력: 최종 플레이리스트 영상
```

**합본 로직:**
```python
async def concat_playlist(video_paths: list[str], output: str):
    # 1. filelist.txt 생성
    filelist = "\n".join(f"file '{p}'" for p in video_paths)
    
    # 2. FFmpeg concat 실행
    cmd = f"ffmpeg -y -f concat -safe 0 -i filelist.txt "
    cmd += f"-c copy {output}"
    
    # 3. 챕터 메타데이터 생성 (곡 제목 + 시작 시간)
    chapters = generate_chapters(video_paths)
    
    # 4. 챕터 삽입
    embed_chapters(output, chapters)
```

---

### 4.4 Phase 4 — SEO + 업로드

#### 모듈: `seo_generator.py`

```
입력: Playlist 메타데이터
처리: Claude API → 다국어 제목/설명/태그 생성
출력: YouTubeUpload 객체
```

**SEO 생성 규칙:**
- 제목: 60자 이내, 핵심 키워드 앞배치 (예: "Easy Listening Pop Playlist | Sunset Drive Vibes")
- 설명: 첫 150자에 핵심 키워드, 곡 목록(챕터 타임스탬프), 채널 소개, 해시태그
- 태그: 20~30개, 롱테일 키워드 포함
- 다국어: 영어(기본), 일본어, 포르투갈어 제목·설명 추가

#### 모듈: `youtube_uploader.py`

```
입력: YouTubeUpload 객체 + 영상 파일
처리: YouTube Data API v3 resumable upload
출력: YouTube video_id
```

**YouTube API 사양:**
```python
class YouTubeUploader:
    SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
    API_VERSION = "v3"
    
    async def upload_video(self, upload: YouTubeUpload) -> str:
        """Resumable upload → video_id 반환"""
        
    async def create_playlist(self, title: str, description: str) -> str:
        """플레이리스트 생성 → playlist_id 반환"""
        
    async def add_to_playlist(self, playlist_id: str, video_id: str):
        """영상을 플레이리스트에 추가"""
        
    async def set_thumbnail(self, video_id: str, image_path: str):
        """커스텀 썸네일 설정"""
```

---

## 5. n8n 워크플로 설계

### 5.1 마스터 워크플로 (W0_master)

```
[Cron Trigger] ─→ [W1 호출] ─→ [W2 호출] ─→ [W3 호출] ─→ [W4 호출]
 매주 화/금            │            │            │            │
 09:00 KST          성공/실패    성공/실패    성공/실패    성공/실패
                      ↓            ↓            ↓            ↓
                 [Telegram 알림: 각 단계별 결과 리포트]
```

### 5.2 각 워크플로 상세

**W1: 테마 + 가사 (Phase 1)**
```
Trigger: W0에서 호출 또는 수동 실행
  ↓
HTTP Request: Claude API → 테마 + 20곡 트랙리스트
  ↓
Loop (20회):
  ├── HTTP Request: Claude API → 곡별 가사
  ├── Function: 스타일 프롬프트 조립
  └── Function: SunoPayload JSON 생성
  ↓
Google Drive: 결과물 JSON 일괄 저장
  ↓
Webhook: W2 트리거
```

**W2: 음악 생성 (Phase 2)**
```
Trigger: W1 Webhook
  ↓
Google Drive: SunoPayload JSON 로드
  ↓
Loop (20회, 동시 처리 3):
  ├── HTTP Request: Suno API → 곡 생성 (2변형)
  ├── Wait: 폴링 (30초 간격, 최대 10분)
  └── HTTP Request: 음원 다운로드
  ↓
Execute Command: Python quality_filter.py → 상위 20곡 선택
  ↓
Google Drive: 선별 음원 업로드
  ↓
Webhook: W3 트리거
```

**W3: 영상 제작 (Phase 3)**
```
Trigger: W2 Webhook
  ↓
HTTP Request: 이미지 생성 API → 썸네일 + 배경
  ↓
Execute Command: Python subtitle_generator.py → SRT 생성
  ↓
Loop (20회):
  └── Execute Command: FFmpeg → 곡별 스펙트럼 영상
  ↓
Execute Command: FFmpeg → 합본 + 챕터 삽입
  ↓
Webhook: W4 트리거
```

**W4: SEO + 업로드 (Phase 4)**
```
Trigger: W3 Webhook
  ↓
HTTP Request: Claude API → 다국어 SEO 메타데이터
  ↓
YouTube Node: 영상 업로드 (예약 발행)
  ↓
YouTube Node: 플레이리스트 생성/추가
  ↓
YouTube Node: 썸네일 설정
  ↓
Telegram: 완료 알림 + 영상 URL
```

---

## 6. 환경 설정

### 6.1 .env 파일

```env
# Claude API
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL=claude-sonnet-4-20250514

# Suno API (서드파티)
SUNO_API_KEY=...
SUNO_API_BASE_URL=https://api.sunoapi.org/v1

# Image Generation
IMAGE_API_KEY=...
IMAGE_API_URL=https://api.apiframe.ai/v1

# YouTube
YOUTUBE_CLIENT_ID=...
YOUTUBE_CLIENT_SECRET=...
YOUTUBE_REFRESH_TOKEN=...

# Google Drive
GDRIVE_FOLDER_ID=...

# Notifications
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# Paths
DATA_DIR=./data
CONFIG_DIR=./config
```

### 6.2 docker-compose.yml

```yaml
version: "3.8"
services:
  n8n:
    image: n8nio/n8n:latest
    ports:
      - "5678:5678"
    volumes:
      - n8n_data:/home/node/.n8n
      - ./data:/data
    environment:
      - N8N_BASIC_AUTH_ACTIVE=true
      - N8N_BASIC_AUTH_USER=admin
      - N8N_BASIC_AUTH_PASSWORD=${N8N_PASSWORD}
    restart: unless-stopped

  worker:
    build: .
    volumes:
      - ./src:/app/src
      - ./config:/app/config
      - ./data:/app/data
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - SUNO_API_KEY=${SUNO_API_KEY}
    depends_on:
      - n8n
    restart: unless-stopped

volumes:
  n8n_data:
```

### 6.3 requirements.txt

```
httpx>=0.27
pydantic>=2.0
anthropic>=0.40
librosa>=0.10
pyloudnorm>=0.1
google-api-python-client>=2.0
google-auth-oauthlib>=1.0
python-dotenv>=1.0
tenacity>=8.0
structlog>=24.0
```

---

## 7. 구현 로드맵

### 7.1 Sprint 1 — 핵심 인프라 + Phase 1 (1주)

| 태스크 | 우선순위 | 예상 시간 |
|--------|----------|-----------|
| 프로젝트 구조 세팅 + config 파일 작성 | P0 | 2h |
| `common/` 모듈 구현 (claude_client, config_loader, models, logger) | P0 | 4h |
| `channel_dna.json` 작성 (Velvet Radio 설정) | P0 | 1h |
| `theme_generator.py` 구현 + 테스트 | P0 | 3h |
| `lyrics_generator.py` 구현 + 테스트 | P0 | 3h |
| `style_assembler.py` 구현 + 테스트 | P0 | 2h |
| Phase 1 E2E 테스트: 플레이리스트 1회분 생성 검증 | P0 | 2h |

### 7.2 Sprint 2 — Phase 2 음악 생성 (1주)

| 태스크 | 우선순위 | 예상 시간 |
|--------|----------|-----------|
| `suno_client.py` 구현 (API 연동 + 폴링 + 다운로드) | P0 | 6h |
| `quality_filter.py` 구현 (오디오 분석 + 선별) | P1 | 4h |
| `storage_manager.py` 구현 (Google Drive 업로드) | P1 | 3h |
| `music_generator.py` 오케스트레이터 구현 | P0 | 3h |
| Phase 1→2 연동 테스트 | P0 | 2h |

### 7.3 Sprint 3 — Phase 3 영상 제작 (1주)

| 태스크 | 우선순위 | 예상 시간 |
|--------|----------|-----------|
| `image_generator.py` 구현 | P0 | 3h |
| `spectrum_renderer.py` 구현 (FFmpeg 래퍼) | P0 | 4h |
| `subtitle_generator.py` 구현 (SRT 생성) | P1 | 2h |
| `video_compositor.py` 구현 (합성) | P0 | 4h |
| `playlist_concat.py` 구현 (합본 + 챕터) | P0 | 3h |
| Phase 1→2→3 연동 테스트 | P0 | 2h |

### 7.4 Sprint 4 — Phase 4~5 + n8n (1주)

| 태스크 | 우선순위 | 예상 시간 |
|--------|----------|-----------|
| `seo_generator.py` 구현 | P0 | 2h |
| `youtube_uploader.py` 구현 (OAuth + Upload) | P0 | 6h |
| n8n 워크플로 W0~W4 구축 | P0 | 6h |
| docker-compose 환경 구성 | P1 | 2h |
| `distrokid_prep.py` 구현 (패키지 준비) | P2 | 2h |
| 전체 파이프라인 E2E 테스트 | P0 | 4h |
| Telegram 알림 연동 | P1 | 1h |

### 7.5 총 예상 기간

- **MVP 완성**: 4주 (Sprint 1~4)
- **첫 자동 플레이리스트 업로드**: Sprint 4 종료 시
- **안정화 + 최적화**: MVP 이후 2주

---

## 8. 테스트 전략

### 8.1 단위 테스트

```python
# test_style_assembler.py
def test_prompt_length_under_1000():
    track = Track(order=1, title="Test", mood=Mood.DREAMY, ...)
    result = assemble_style_prompt(track, load_dna())
    assert len(result.prompt) <= 1000

def test_negative_prompt_appended():
    result = assemble_style_prompt(track, dna)
    assert "no EDM drops" in result.negative

# test_lyrics_generator.py
def test_metatags_present():
    lyrics = generate_lyrics(track)
    assert "[Verse" in lyrics.content
    assert "[Chorus]" in lyrics.content

def test_lyrics_under_3000_chars():
    lyrics = generate_lyrics(track)
    assert lyrics.char_count <= 3000
```

### 8.2 통합 테스트

- Phase 1 E2E: `channel_dna.json` → 20곡 가사+스타일 프롬프트 생성 검증
- Phase 1→2: 생성된 프롬프트로 Suno API 실제 호출 → 음원 다운로드 검증
- Phase 3: 샘플 음원 + 이미지 → FFmpeg 영상 렌더링 검증
- Phase 4: YouTube API 테스트 업로드 (private 모드)

### 8.3 품질 게이트

각 Phase 완료 시 다음 조건 충족 필수:
- Phase 1: 20곡 전부 가사 존재, 메타태그 정규식 통과, 스타일 프롬프트 1,000자 이내
- Phase 2: 최소 15곡 이상 음원 생성 성공, 각 곡 2분~4분, LUFS -14~-10 범위
- Phase 3: 최종 영상 55분~75분, 해상도 1920×1080, 챕터 타임스탬프 존재
- Phase 4: YouTube 업로드 성공, 썸네일 설정 완료

---

## 9. 모니터링 및 알림

### 9.1 Telegram 알림 구조

```
✅ [Velvet Radio] 플레이리스트 생성 완료
━━━━━━━━━━━━━━━━━
📎 테마: Golden Hour Drive
🎵 곡 수: 20곡 (선별: 18곡)
⏱️ 총 길이: 1h 12m
📊 Phase 1: 2m 30s | Phase 2: 45m | Phase 3: 15m | Phase 4: 3m
🔗 https://youtube.com/watch?v=xxxxx
━━━━━━━━━━━━━━━━━
다음 예약: 금요일 09:00 KST
```

### 9.2 에러 알림

```
🚨 [Velvet Radio] Phase 2 에러
━━━━━━━━━━━━━━━━━
Track 7 "Moonlit Café" 생성 실패
원인: Suno API timeout (3회 재시도 후 스킵)
조치: 나머지 19곡으로 진행
━━━━━━━━━━━━━━━━━
```
