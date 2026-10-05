test:
	pytest backend/test -m "not eval and not e2e"

e2e:
	pytest backend/test -m e2e

eval:
	cd backend && python eval/run.py --analyze-only

docs:
	python3 scripts/md_serve.py --port $${MD_PORT:-8090}
