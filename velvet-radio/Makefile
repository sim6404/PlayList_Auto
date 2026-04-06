# ============================================================
#  Velvet Radio — Makefile
#  주요 명령어 단축키
# ============================================================

.PHONY: setup run test lint fmt clean docker-up docker-down pipeline dashboard

# 환경 설정
setup:
	@echo "🔧 환경 설정 중..."
	pip install -r requirements.txt
	cp config/.env.example config/.env 2>/dev/null || true
	@echo "✅ config/.env 파일을 편집하여 API 키를 입력하세요"

# 파이프라인 실행 (전체 자동화)
run:
	@echo "🚀 Velvet Radio 파이프라인 시작..."
	python -c "import asyncio; from src.phase4_publish.scheduler import run_full_pipeline; asyncio.run(run_full_pipeline())"

# 단계별 실행
phase1:
	@echo "📝 Phase 1: 테마 + 가사 생성"
	python -c "from src.phase1_concept.theme_generator import generate_playlist; from src.phase1_concept.lyrics_generator import generate_all_lyrics; p=generate_playlist(); generate_all_lyrics(p)"

phase2:
	@echo "🎵 Phase 2: 음악 생성 (playlist_id 필요)"
	@read -p "Playlist ID: " id; python -c "import asyncio; from src.phase1_concept.theme_generator import load_playlist; from src.phase2_music.music_generator import run_music_generation; asyncio.run(run_music_generation(load_playlist('$$id')))"

# 대시보드 실행
dashboard:
	@echo "🖥️ 승인 대시보드 시작 (http://localhost:8080)"
	python -m dashboard.app

# Docker
docker-up:
	@echo "🐳 Docker 서비스 시작..."
	docker-compose up -d
	@echo "✅ n8n: http://localhost:5678"
	@echo "✅ 대시보드: http://localhost:8080"

docker-down:
	docker-compose down

docker-logs:
	docker-compose logs -f

docker-build:
	docker-compose build --no-cache

# 테스트
test:
	@echo "🧪 테스트 실행..."
	python -m pytest tests/ -v --tb=short

test-fast:
	python -m pytest tests/ -v -x -m "not slow"

test-cov:
	python -m pytest tests/ --cov=src --cov-report=html --cov-report=term-missing

# 코드 품질
lint:
	ruff check src/ tests/ dashboard/
	mypy src/ --ignore-missing-imports

fmt:
	ruff format src/ tests/ dashboard/
	ruff check --fix src/ tests/ dashboard/

# 정리
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov

clean-data:
	@echo "⚠️  생성된 데이터 삭제 (audio, video, images)"
	rm -rf data/audio/* data/videos/* data/images/*

# n8n 워크플로 가져오기
n8n-import:
	@echo "📥 n8n 워크플로 가져오기..."
	@for f in n8n/workflows/*.json; do \
	  curl -s -X POST http://localhost:5678/rest/workflows \
	    -H "Content-Type: application/json" \
	    -u admin:$$N8N_PASSWORD \
	    --data-binary "@$$f"; \
	  echo "imported: $$f"; \
	done

# YouTube OAuth 설정
yt-auth:
	@echo "🔐 YouTube OAuth 설정"
	python -c "from src.phase4_publish.youtube_uploader import YouTubeUploader; YouTubeUploader()"

# 상태 확인
status:
	@echo "📊 Velvet Radio 상태"
	@echo "대기 중인 승인:"
	@ls data/pending_approval/*.json 2>/dev/null | wc -l || echo 0
	@echo "완료된 플레이리스트:"
	@ls data/approved/*.json 2>/dev/null | wc -l || echo 0
	@echo "생성된 음원:"
	@find data/audio -name "*.mp3" 2>/dev/null | wc -l || echo 0
