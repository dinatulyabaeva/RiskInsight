.PHONY: run test seed smoke
run:
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
test:
	python -m pytest -q
seed:
	python -m scripts.seed
smoke:
	python -m scripts.smoke
