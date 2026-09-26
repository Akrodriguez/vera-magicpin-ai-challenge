# VERA — Deterministic Merchant AI Assistant

A deterministic, context-grounded message engine for the magicpin VERA AI Challenge.

## Endpoints

- `GET /v1/healthz`
- `GET /v1/metadata`
- `POST /v1/context`
- `POST /v1/tick`
- `POST /v1/reply`

## Local run

```bash
python -m pip install -r requirements.txt
uvicorn bot:app --host 0.0.0.0 --port 8080
```

## Tests

```bash
python -m pytest -q
```

Generate the official deterministic dataset when needed:

```bash
python dataset/generate_dataset.py --seed-dir dataset --out dataset/expanded
```

Run the supplied judge simulator in offline mode:

```bash
BOT_URL=http://127.0.0.1:8080 LLM_PROVIDER=mock TEST_SCENARIO=all python judge_simulator.py
```

For the full local simulator:

```bash
BOT_URL=http://127.0.0.1:8080 LLM_PROVIDER=mock TEST_SCENARIO=full_evaluation python judge_simulator.py
```

## Design

The bot stores pushed context by `(scope, context_id)`, accepts idempotent replays of the same version, atomically replaces older context with higher versions, and uses the current context for deterministic composition.

Messages are composed from supplied category, merchant, trigger, and optional customer context. The composer avoids relying on an external API or LLM at runtime, which keeps latency and behavior deterministic.

Conversation replies use a small state/intent detector for auto-replies, opt-outs, commitments, slot selections, and out-of-scope curveballs.

## Deployment

The included Dockerfile is suitable for a container host that provides a public HTTPS URL. Set `PORT` if the platform supplies its own port. Keep the process live after submission because the judge may inject fresh context after submission.
