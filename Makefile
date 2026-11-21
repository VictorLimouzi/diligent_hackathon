# Casebook: common commands. DATA is the folder with the JSON Lines files.
DATA ?= data/screening/dev
PY   ?= .venv/bin/python

.PHONY: decisions eval test ml second-opinion web serve all

decisions:        ## the scored output (standard library only)
	system/run $(DATA) output/dev.jsonl
	python3 run.py $(DATA) -o out/decisions.jsonl --workbench out/casebook.html

eval:             ## score against the practice labels
	python3 eval.py $(DATA)

test:             ## engine, server, model and agent tests
	python3 -m unittest discover -s tests
	CASEBOOK_DATA=$(DATA) $(PY) -m pytest -q server/tests ml/tests agents/tests
	python3 -m unittest discover -s evals

ml:               ## train the advisory models, write out/ml/
	$(PY) -m ml.train $(DATA) --out out/ml

second-opinion:   ## LLM second opinion on every hit, write out/llm/ (needs ANTHROPIC_API_KEY for live output)
	$(PY) -m agents.second_opinion $(DATA) --out out/llm/second_opinion.json

web:              ## build the frontend into web/dist
	cd web && npm install && npm run build

serve:            ## the app at http://localhost:8000
	CASEBOOK_DATA=$(DATA) $(PY) -m uvicorn server.app:app --port 8000

all: decisions ml web serve
