# LangGraph Support Evaluation: Direct Model vs. ToyAgent

The two systems answered the same 20 historical questions. Both used GPT-6.1 Terra with medium reasoning. The baseline received only the question and could not search or use other tools. ToyAgent used its normal routing, search, and verification tools with temporary memory and an isolated workspace. Neither system received the scoring references.

A separate call to the same model graded the answers against references compiled from historical issue discussions. These model judgments are not human ground truth. References may be outdated, the selected cases are not representative of every task, and concurrent execution affects latency measurements.

## Results

| Rating | Direct model | ToyAgent |
|---|---:|---:|
| Correct | 4/20 | 18/20 |
| Partially correct | 16/20 | 2/20 |
| Incorrect | 0/20 | 0/20 |
| Abstained | 0/20 | 0/20 |
| Execution failed | 0/20 | 0/20 |

**Direct model:** median latency 15.3 seconds; 19,251 recorded tokens; 0 search tool calls.

**ToyAgent:** median latency 112.9 seconds; 1,908,994 recorded tokens; 78 search tool calls.

Token totals cover usage returned in completed run records. Failed or interrupted requests and grading calls may incur additional charges. Search may be billed separately; consult the provider's bill for actual costs.

## Scoring and usage notes

The separate grading calls recorded 533,362 tokens. Search calls are counted from tool traces and do not necessarily equal billable provider requests. The judge used the same model as the systems under test, and saw both answers together in a fixed order, which may introduce bias.

Some questions do not specify a product or version, while the historical reference depends on a particular issue. This can result in a reasonable general answer being rated partially correct. In the paired results, both systems were correct on 4 cases; ToyAgent was correct while the baseline was partially correct on 14; and both were partially correct on 2.

Cases 001, 005, and 011 were spot-checked against their answers and grading explanations; the other judgments were not individually reviewed by a human. Original tool-result contents used by the judge are not published, so readers cannot independently verify every evidence-support judgment from the public files.

## Per-case results

| Case | Direct model | ToyAgent | Direct time | ToyAgent time | Route |
|---|---|---|---:|---:|---|
| case-001 | Partial | Correct | 18.2s | 82.9s | research |
| case-002 | Correct | Correct | 10.6s | 8.9s | direct |
| case-003 | Partial | Correct | 21.1s | 118.6s | research |
| case-004 | Partial | Correct | 16.9s | 119.6s | research |
| case-005 | Partial | Partial | 8.8s | 6.6s | direct |
| case-006 | Partial | Correct | 15.8s | 114.1s | research |
| case-007 | Partial | Correct | 16.8s | 184.2s | research |
| case-008 | Partial | Correct | 15.9s | 166.1s | research |
| case-009 | Correct | Correct | 9.4s | 98.1s | research |
| case-010 | Partial | Correct | 13.4s | 77.3s | research |
| case-011 | Partial | Partial | 11.2s | 9.9s | direct |
| case-012 | Partial | Correct | 12.1s | 135.3s | research |
| case-013 | Partial | Correct | 12.4s | 118.7s | research |
| case-014 | Partial | Correct | 17.6s | 76.6s | research |
| case-015 | Partial | Correct | 17.2s | 169.2s | research |
| case-016 | Correct | Correct | 9.2s | 111.5s | research |
| case-017 | Partial | Correct | 14.8s | 125.9s | research |
| case-018 | Partial | Correct | 12.0s | 114.8s | research |
| case-019 | Correct | Correct | 21.4s | 12.6s | direct |
| case-020 | Partial | Correct | 15.9s | 111.7s | research |

## Per-case grading explanations

### case-001

- **Direct model:** Offered useful steps to distinguish actual checkpoint persistence from stream events, but did not identify the known `interrupt_after` missing-event issue or recommend checking for fix #9409. No tool or source record was available to verify support.
- **ToyAgent:** Identified the known issue, fix #9409, and version checks, with steps to compare saved state and stream events. The original tool records included issue and PR content supporting the reproduction version and 1.2.18 release-note claims.

### case-002

- **Direct model:** Recommended a stable UUID or fixed-length hash and discussed session isolation and migration of old checkpoints. This addresses the problem. The system had no tools, so source support could not be checked.
- **ToyAgent:** Recommended storing and reusing a session UUID, with hashing and a warning about shared sessions. It did not mention migration, but that did not affect the main solution. No tool record was available to verify source support.

### case-003

- **Direct model:** Provided useful version checks, execution-path comparisons, and a minimal reproduction, but did not identify the known streaming-path defect or fix #9407. There was no tool record to check external support; missing citations were not penalized.
- **ToyAgent:** Located the known defect and streaming path and suggested upgrades and alternatives. The original PR and release-note records supported the conclusion that the fix entered 1.2.19.

### case-004

- **Direct model:** Offered useful sync mitigation, persistence-error checks, and recovery guidance, but did not identify the known 1.2.14 defect or its 1.2.15 fix. Its explanation focused on general asynchronous persistence risks. No tool record was available to check support.
- **ToyAgent:** Identified #9235 and the 1.2.15 fix; recommended fault-injection testing after upgrading; and covered temporary sync mitigation, error handling, and affected-state recovery. The original issue, PR, and documentation records supported the main claims and qualifications.

### case-005

- **Direct model:** Correctly identified an unawaited coroutine and suggested checking the call chain, but did not explicitly inspect custom saver's synchronous and asynchronous methods or `await adelete_thread`, and did not request code or versions. No tool record was available to verify support.
- **ToyAgent:** Offered useful asynchronous-call and task-lifecycle troubleshooting, but also omitted explicit custom saver and `await adelete_thread` checks and did not request code or versions. No tool record was available to verify support.

### case-006

- **Direct model:** Correctly explained the race and suggested serialization and prevention of stale writes, but omitted the maintainer's recommendation to assess migration from the deprecated saver to `AsyncPostgresSaver` and `durability=exit`. No tool or source record was available to verify support.
- **ToyAgent:** Presented the maintainer-recommended migration and explained serialization and persistence-semantics differences. The original issue, maintainer comments, PR, and source records supported the main conclusions. Search results conflicted about closure status, but the API record supported the final statement.

### case-007

- **Direct model:** Recommended recording package versions, distinguished old `thread_ts` from newer `checkpoint_id`, and suggested field checks, but did not explicitly check package/test compatibility or the metadata-dictionary assertion difference. No tool record was available to verify support.
- **ToyAgent:** Explicitly checked dependency and test-version compatibility, explained the change in `thread_ts` support and metadata comparisons, and gave remediation steps. The original issue, PR diff, and query source supported the core diagnosis; the answer also noted environment differences.

### case-008

- **Direct model:** Correctly noted that protocol compatibility does not imply SQL and transaction compatibility and suggested PostgreSQL alternatives, but did not mention the CockroachDB-specific checkpointer needed when CockroachDB must be retained. No tool or source record was available to verify support.
- **ToyAgent:** Recommended the dedicated CockroachDBSaver and read/write and recovery checks, consistent with the reference. Original tool records supported the known compatibility issue, version requirements, and main API. The example was not observed executing and should be tested in the target environment.

### case-009

- **Direct model:** Correctly explained that runtime configuration metadata is stored as checkpoint metadata and read back in the snapshot; version caveats were reasonable. It had no tool records, so external support could not be checked; this did not affect correctness.
- **ToyAgent:** Correctly described the save/read path and qualified version and checkpointer differences. Original search results and partial source records supported the mechanism and merge rules, although some source pages were truncated.

### case-010

- **Direct model:** Idempotency, deduplication, and testing advice was useful, but it did not identify the known defect or its 1.2.16 fix. No tool or source record was available to verify support.
- **ToyAgent:** Identified #9315 and 1.2.16 and recommended upgrading, idempotency, and regression testing. The original records supported the core conclusion; the answer cited related issue #9106 rather than reference issue #9276 without changing the diagnosis.

### case-011

- **Direct model:** Offered useful timing, event-stream, and resume-association troubleshooting, but did not identify the need to check Agent Server version or the 0.17.0 handling of resume during interruption. No tool evidence was available; the race explanation remained a hypothesis.
- **ToyAgent:** Offered useful event-chain and timing comparisons, but also omitted Agent Server version checks and the related 0.17.0 behavior. No tool evidence was recorded.

### case-012

- **Direct model:** Distinguishing loaded writes from new writes was useful, but it omitted #9114, deployment-version checks, and replay ordering. Deduplication by `task_id` may not work because duplicate writes can use different task IDs. No tool evidence was available.
- **ToyAgent:** Identified #9114, replay ordering, and an upgrade path, with a scoped temporary mitigation. The original release notes directly supported the version, which should still be checked against the deployed installation.

### case-013

- **Direct model:** Suggested upgrading, checking checkpoints, and testing recovery, but omitted PR #7498 and fix version 1.1.7. Replaying from before the interrupt does not necessarily bypass the defect. No tool record was available.
- **ToyAgent:** Identified #7498 and 1.1.7 and suggested version and regression checks. Original records supported the related issue, manual-fork limitations, and subsequent regression; the answer also distinguished the 1.1.9 and 1.1.10 claims.

### case-014

- **Direct model:** Suggested version checks, reproduction, and replay investigation, but omitted #6863 and its 1.0.9 fix. Its reproduction advice emphasized concurrency although the defect can occur with sequential asynchronous tasks. No tool record was available to verify the technical claims.
- **ToyAgent:** Identified the defect and fix version, provided a regression reproduction, and distinguished normal replay. Original issue, PR, release, and documentation records supported the main conclusions and conditional workaround.

### case-015

- **Direct model:** Suggested a useful workaround using graph-level interrupt streams and resuming execution, but did not identify the known false report in the tools stream, fix #9406, or the need to check for a version containing it. No tool record was available.
- **ToyAgent:** Distinguished a false stream event from an actual graph pause and described upgrading, reading graph-level interrupts, and resuming. The original PR and release notes supported #9406 and version 1.2.19; documentation or search results supported other principal advice.

### case-016

- **Direct model:** Correctly recommended renaming the business parameter and updating callers and the schema, and explained the purpose of the runtime object. No tool record was available to verify source support; missing citations did not affect correctness.
- **ToyAgent:** Identified the reserved-name conflict and suggested renaming. The original issue, documentation, and search records supported the main explanation. The alias alternative included version and testing caveats and was not presented as the default fix.

### case-017

- **Direct model:** Correctly noted that interrupts should not be swallowed and gave recovery advice, but changing only the user wrapper would not fix the outer ToolNode defect. It omitted the released fix and component-version checks. No tool or evidence record was available.
- **ToyAgent:** Distinguished the user wrapper from the framework-level defect, identified the fix version and component checks and upgrade, and provided temporary guidance and pause/resume validation. Original tool records supported the main conclusions; source output was truncated and the example was not observed running.

### case-018

- **Direct model:** Provided useful commit, package-version, and interrupt checks, but did not identify fix PR #9232 or `langgraph-prebuilt` 1.1.1. The answer was principally general troubleshooting advice.
- **ToyAgent:** Identified the fix PR, released package and version, and distinguished version requirements for a different invalid-resume issue. The original issue, commit, and release notes supported the key conclusions. It did not explicitly recommend separate reproduction for other issues, without affecting the core answer.

### case-019

- **Direct model:** Provided an effective way to locate unsupported objects field by field using the actual serializer, and requested code and versions. It did not name `langchain-azure-ai` or explicitly request a minimal reproduction, but adequately addressed the general problem. No tool record was available.
- **ToyAgent:** Covered message fields, other state, serializer configuration, and versions, and qualified type scanning as a clue. It also did not name `langchain-azure-ai` or explicitly request a minimal reproduction. No tool record was available.

### case-020

- **Direct model:** Gave useful version, environment, and import-path checks, but did not identify the known checkpoint-path conflict or concrete compatible version combinations. Its TypeVar advice applies to custom generics and is not a substitute for `V` here. No tool record was available to verify support.
- **ToyAgent:** Explained the path conflict, historical fix combinations, and combinations to verify when retaining older versions, with troubleshooting and security notes. Original issue reports, source, version metadata, and a security advisory supported the main claims.

## Records

- `blind-terra-20261111.jsonl`: answers, durations, tool-call metadata, and available usage. Fetched page contents and tool arguments were removed from the public copy.
- `blind-terra-20261111-judgments.jsonl`: per-case model judgments and grading usage.
- `public/cases.json`: test questions; `private/reference.json`: the independent scoring references.
