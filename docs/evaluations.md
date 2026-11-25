# Evaluations

## Datasets

A dataset is a named list of items. Creating a dataset with an existing name adds a new **immutable
version**; runs always point at the exact version they used. Limit: 2000 items per version
(`COMPANION_MAX_DATASET_ITEMS`).

Item fields (anything else is rejected):

| Field | Type | Used by |
|---|---|---|
| `id` | string | Optional; defaults to `item-1`, `item-2`, ... Must be unique. |
| `input` | string, or list of `{role: user\|assistant, content}` | The prompt or task |
| `expected` | any | `exact_match`, `contains`, `json_fields`, `numeric_close`, judges |
| `context` | string | Source text for the `hallucination` judge rubric |
| `expected_tools` | list of strings | `tool_selection` |
| `expected_trajectory` | list of strings | `trajectory_match` |
| `relevant_ids` | list of strings | `recall_at_k`, `mrr` |
| `pattern` | string, at most 200 characters | `regex` |
| `metadata` | object | Free-form; passed to agents |

## Targets

An evaluation sends each item to a **target**:

- `session`: Claude in the Code tab writes the outputs. The run waits on a session task until Claude
  submits them. No key needed.
- `<provider>` + `model`: the gateway calls a configured provider (`anthropic`, `openai`, `gemini`,
  `openrouter`, `ollama`, `custom`). It retries rate limits and server errors, and records latency,
  tokens and cost.
- `agent` (via the [agent runner](agent-runner.md)): your own agent process.

## Scorers

| Scorer | Passes when | Params |
|---|---|---|
| `exact_match` | Normalized output (case, whitespace) equals `expected`, or one of a list | – |
| `contains` | Output contains `expected` (or `params.values`); score is the fraction found when `mode: all` | `values`, `mode` |
| `regex` | Output matches `params.pattern` or `item.pattern` | `pattern` |
| `json_valid` | Output parses as JSON (code fences allowed) | – |
| `json_fields` | All key/values in `expected` (an object) appear in the JSON output; score is the fraction | – |
| `numeric_close` | First number in the output is within `tolerance` of `expected` | `tolerance` |
| `tool_selection` | F1 of tools called vs `expected_tools` is 1 | – |
| `tool_success` | Every tool call has `ok: true`; score is the fraction | – |
| `trajectory_match` | Tool-name sequence equals `expected_trajectory`; score is LCS / longer length | – |
| `recall_at_k` | All `relevant_ids` appear in the top `k` retrieved ids | `k` (default 5) |
| `mrr` | Score is the reciprocal rank of the first relevant id | – |

For model targets, trajectory and retrieval scorers read JSON from the output (`{"tool_calls": [...]}`,
`{"retrieved_ids": [...]}`). For agent runs they read the trajectory the agent reported. A scorer
never crashes a run: a bad item scores 0 with a reason.

## Judges

Add `judge: {provider, model, rubric, pass_threshold}`:

- `rubric` is `correctness`, `hallucination` (claims must be supported by `context`), `helpfulness`,
  or your own text.
- A provider judge is asked for `{"score": 0..1, "reason": "..."}`. Its calls are tracked like any
  other.
- `provider: session` makes Claude the judge: the run opens a `judge` session task after the outputs
  are in.

The judge score is stored as the `judge` scorer and counts toward `success_rate`.

## Session tasks

When a run targets or is judged by `session`, the gateway opens session tasks of up to 20 items each.

| Kind | Submit with |
|---|---|
| `generate` | `{"outputs": [{"item_id", "output"}]}` with every item of the task |
| `judge` | `{"judgments": [{"item_id", "score": 0..1, "reason"}]}` |

Submissions are validated (all items, valid ids, scores in range). A task can be submitted once.

## Reports and comparisons

- `retrieve_evaluation_report`: summary (per-scorer mean and pass rate, latency p50/p95, tokens, cost,
  errors, agent metrics) plus up to 200 items; `failures_only` filters to failing items.
- `compare_model_results`: baseline vs candidates, per scorer: means, delta, wins/losses/ties, newly
  failing items, and a `regression` flag when the mean drops more than the threshold.
- `export_report`: HTML, CSV or JSON files (see [agent-runner.md](agent-runner.md#5-compare-analyze-report)).

## Experiments

`create_experiment` (name, description, tags) groups runs: pass `experiment` when starting a run.
`search_experiments` finds them by name, description or tag, and returns each with its runs and
summaries.

## Costs and budgets

Paid provider calls are blocked if `COMPANION_ALLOW_PAID_CALLS=false`. Within one evaluation, provider
generation stops once known spend reaches `COMPANION_MAX_RUN_COST_USD` (default $1). The remaining
items are marked `skipped` and score 0. Costs use the price table in `providers.py` (Anthropic list
prices) plus `COMPANION_PRICING_FILE`. Models without a price show cost as unknown, never guessed.
