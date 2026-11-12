# LangGraph Support Evaluation

This evaluation compares a question-only, tool-free model baseline with ToyAgent on 20 historical LangGraph support cases. Both arms use GPT-6.1 Terra with medium reasoning. A separate model call scores each pair against references collected from public issue discussions. Read the [full report](blind-terra-20261111-report.md) for results and limitations.

- `public/cases.json`: the questions presented to both systems.
- `private/reference.json`: references withheld from the tested systems and supplied to the grader. They are published here so readers can inspect the scoring criteria.
- `blind-terra-20261111.jsonl`: answers, status, timing, token usage, and tool names/statuses. Fetched page contents and tool arguments were removed from the public copy; not all evidence judgments can be independently checked from these records.
- `blind-terra-20261111-judgments.jsonl`: model judgments, reasons, and grading usage.
- `blind_compare.py`, `blind_judge_stream.py`, `live_judge.py`, and `retrieval.py`: the comparison and scoring implementation.

The judge saw the original run's recorded tool traces, not the sanitized public copy. A new run accesses current public information and may therefore produce different results. Scoring is model-assisted, uses the same model family, and relies on historical references; it is not a substitute for independent human evaluation. These 20 selected cases are not a general benchmark of all agent tasks.

## Run the comparison

After setting up the project's Python dependencies and configuring `OPENAI_API_KEY` in your environment, run:

```bash
uv run python benchmarks/langgraph-support/blind_compare.py --execute --parallel 2 --output benchmarks/langgraph-support/my-results.jsonl
```

Optional `DEEPSEEK_API_KEY`, `SERPER_API_KEY`, and `SERPAPI_API_KEY` environment variables enable the corresponding search fallbacks. This performs paid model and potentially search calls. Keep keys out of the command line and result files.

The scorer expects `blind-terra-20261111.jsonl` as input and appends to `blind-terra-20261111-judgments.jsonl` in this directory. Run it in a separate copy with the result and judgment filenames adjusted or moved aside to avoid mixing runs:

```bash
uv run python benchmarks/langgraph-support/blind_judge_stream.py
```

The scorer reads the reference answers; the baseline and ToyAgent do not. Verify that all 40 arm results and all 20 judgments are present before computing a summary.
