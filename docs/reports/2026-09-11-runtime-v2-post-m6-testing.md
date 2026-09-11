# Runtime v2 post-M6 testing — September 11, 2026

[Machine-readable results](2026-09-11-runtime-v2-post-m6-testing.json) · [Plan](../superpowers/plans/2026-09-10-runtime-v2-post-m6-testing.md)

The post-M6 test and measurement tooling is implemented, with the local gates and limitations recorded below. This is an execution report for Tasks 1–6, not a new milestone. Real Telegram remains blocked pending a designated test bot, exact recipients, and explicit message authorization.

Two product-readiness findings remain: replacing workspace directories during restoration makes prior scoped memory unavailable through ordinary retrieval, and the frozen documentation-answer baseline does not establish reliable factual answers or citations. Human VoiceOver, Safari workflow, and prose inspection were unavailable. Passing characterization tests do not clear those gates.

## Final local gates

| Gate | Status | Actual cases / evidence |
| --- | --- | --- |
| macOS Python 3.11 + coverage | **passed** | 587 passed; 0 failures; 0 errors; 1 skipped |
| macOS Python 3.13 | **passed** | 587 passed; 0 failures; 0 errors; 1 skipped |
| Initial Linux prerequisite | **failed** | Docker create/list returned HTTP 500 before tests. Later API recovery was independently observed. |
| Linux Python 3.13 | **passed** | 587 passed; 0 failures; 0 errors; 1 skipped |
| Linux Docker | **passed** | 22 passed; 0 failures; 0 errors; 0 skipped |
| Installed Chrome | **passed** | 26 passed; 0 failures; 0 errors; 0 skipped |
| macOS Docker | **passed** | 22 passed; 0 failures; 0 errors; 0 skipped |
| Base + MCP installed wheels | **passed** | 183 passed; 0 failures; 0 errors; 0 skipped |
| Existing live Qwen gate | **passed** | 7 passed; 0 failures; 0 errors; 0 skipped |
| Restore preserving workspace identity | **passed** | Ordinary public restore usable; stale authority protection, stopped integrity and fresh Telegram polling verified. |
| Restore replacing workspace identity | **failed** | Stored memory/FTS intact but replacement workspace identity hides prior scoped memory; stale authority correctly rejected. |
| 900-second smoke | **passed** | 2,700/2,700 completed; 900 expected conflicts; zero pending/duplicates; 91 resource samples. |
| 3,600-second workload | **passed** | 10,800/10,800 completed; 3,600 expected conflicts; zero pending/duplicates; 361 resource samples. |
| Original 40 memory cases | **passed** | Exact 10/10, paraphrase 5/10, revision 10/10, scope 10/10; zero forbidden/stale returns. |
| Frozen 60 memory cases | **passed** | 15/15 in each of four categories; zero forbidden/stale returns. |
| Documentation transport / authority | **passed** | 36/36 transported; zero unauthorized attempts, proven effects or uncertain effects. |
| Documentation answer / citation quality | **failed** | Required sources 46/48; facts 41/42; 53/89 parsed citations meet frozen rules; 20/36 trials have citation errors. |
| Human prose inspection | **blocked** | Deterministic scores and controller source inspection complete; actual human observations unavailable. |
| Manual VoiceOver | **blocked** | No human VoiceOver workflow observations supplied. |
| Manual Safari | **blocked** | Installed Safari 26.6.2 observed by version only; no manual workflow pass. |
| Native browser toolbar interaction | **not_run** | Native Chrome page zoom verified through isolated profile calibration; toolbar interaction not exercised. |
| Hosted CI | **not_run** | Workflow updated; no remote execution authorized or hosted run ID. |
| Real Telegram (Task 7) | **blocked** | No test bot, exact recipients or message authorization. Synthetic fixture evidence only. |
| Owned cleanup | **passed** | No task-named processes or additional/owned containers; owned scratch removed; retained evidence/dependencies listed separately. |
| Main / other worktree preservation | **passed** | Main HEAD/status and other HEAD/branch match the baseline. |
| Initial container preservation | **failed** | 28/29 initial containers remain; initially running 436251a is absent. Cause unknown. |
| Post-commit Docker image lookup | **failed** | Both retained tag-name queries reported no such image; immutable-ID queries later returned the original images and tags. Cause unknown. |

The Python 3.11 coverage run covered 4,086/4,681 statements and 1,122/1,484 branches. The single quick-suite skip is the explicitly base-only missing-SDK public check, separately executed in the clean base wheel. Repeated platform and wheel cases are separate executions; counts are not pooled as unique coverage. Collection and executed-ID reconciliation are retained in the companion JSON.

All 644 collected test IDs executed and passed across the explicitly selected final gates. Collection grew from 474 to 644 cases, with 66 new test functions. The installed-wheel total comprises 63 base public + 26 base browser and 67 MCP public + 27 MCP browser executions; the latter includes the complete walkthrough. All 24 packaged files match, both imports originate in their own site-packages, and MCP SDK absence/presence is verified separately.

## Source, environment and exact commands

The final implementation is `f7686e8ce757f6f031318b677cb2aa045385623d`, aggregate SHA-256 `96bde9445b7ba2411bed1cd7f50a8f7193f17428f099564620e0803dea0c71e4` over 164 tracked inputs. Planning began at `035e3d74954c1fed9c113c042398a78989017c41`. The report commit adds documentation only. Individual command metadata preserves dirty diffs and source hashes from earlier attempts.

macOS 26.6.2 arm64 used Python 3.11.13 and 3.13.5, SQLite 3.49.1, pytest 9.1.1, Playwright 1.62.0 and installed Chrome 152.0.7977.83. Safari 26.6.2 (21624.5.1.11.3) was observed by version only. The lock hash is `6b2646acbae1ea37e6e46cdb38ede5d233559dfe8ada6eb15672fbf010c53983`. Full dependency inventories and separate macOS/Linux/environment hashes are in the companion JSON.

The local Linux arm64 runner used Python 3.13.5, SQLite 3.40.1 and Linux 6.10.14-linuxkit. It installed 47 locked Python distributions, including ordinary package downloads into its disposable environment; this was not an offline dependency installation. The wheel checks used `UV_OFFLINE=1` and `UV_PYTHON_DOWNLOADS=never` with prepared caches.

Existing Qwen `qwen3.8:27b-mlx` at `http://127.0.0.1:11434` used model digest `5642e97495e1a088883805981563dcdc4a040c2f53388b7a41d1f24d3622cf7e`, with Ollama 0.33.3. The explicitly prepared MCP image is `sha256:e8c258ff64cb4d7ad7b43c3af827b27c0dee7f90206b8acd8dcb65ff37a88ee7`; all 26 build inputs match the runtime candidate. The trusted Linux image used for measurement is `sha256:29b818552c8a0926f11f9596e803ec00f7b246444422f44bb3ced1237dd5814c`. The command image remains the pinned `python:3.13.15-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e`. No test pulled a replacement image or downloaded a model/browser.

Execution artifacts are retained at `test-results/post-m6-20260911T012137Z/`. The companion JSON embeds the exact command index, source/environment inventories, every plan requirement, all 36 answers and scores, and load results. Raw command logs, JUnit, synthetic stopped databases, wire data, screenshots and reviewed patches remain at their hashed artifact paths. These ignored artifacts are local evidence; tracked test tooling does not depend on them. [The testing guide](../testing.md) gives portable commands for fresh execution.

## Findings and task traceability

| Review finding | Work and actual outcome | Remaining limit |
| --- | --- | --- |
| F1: reproducible installed acceptance | Task 1 adds a tracked wheel verifier and one-root walkthrough, fresh base/MCP environments, packaged-byte checks, explicit selection and strict executed-JUnit acceptance. | Local and hosted acceptance are separate; hosted CI is `not_run`. |
| F2: mixed and sustained operation | Tasks 2 and 5 compose HTTP, synthetic Telegram and schedules during approvals, cancellation, revocation and real process death; thirty bounded recovery cycles and both timed workloads completed. | Bounded synthetic workloads do not establish indefinite operation or production capacity. |
| F3: populated recovery | Task 3 covers schemas 1–6, current-state restoration, actual SQLite allocation failure, journal/commit faults and interrupted migration. | Identity-changing restore usability is **failed**; stored data and authority protection remain intact. |
| F4: hosted verification | Task 1 replaces CI's help-only wheel check with public-path acceptance and keeps browser selection explicit. Local Linux identifies its actual arm64 architecture. | Hosted CI is **not_run**: no authorized remote run, hosted run ID or hosted architecture result. Local acceptance does not clear this finding. |
| F5: retrieval and answer correctness | Task 6 freezes new 60-case memory and 12-question documentation sets before measurement; preserves the original forty cases and all 36 Qwen answers. | Deterministic answer/citation verdict **failed**; human prose review **blocked**. |
| F6: operator workflow | Task 4 adds keyboard, actual accessibility-tree, focus, storage, hostile-layout, native Chrome zoom and CLI failure checks. | Human VoiceOver and installed Safari workflow **blocked**; no formal accessibility claim. |
| F7: operating limits and diagnosis | Tasks 3/5 retain SQLite codes and precise effect boundaries, scheduler/populated-state measurements, resource trends and cleanup. | Public storage errors remain generic; no OS-wide disk-full, hardware-fault, x86, hosted or indefinite-load claim. |

The companion JSON maps every numbered plan requirement to a status and evidence. `passed` means the stated check ran and met its assertions; `failed` includes an observed unmet gate or failed selected prerequisite; `blocked` requires unavailable human input/authorization; `not_run` identifies an omitted or unexecuted gate. The known missing-SDK check may skip only in SDK-present environments and must execute in the clean base wheel.

## Changes and confirmed defects

The only production edit is `role="status" aria-live="polite"` on the existing web run-status element. A public accessibility-tree regression failed before that change and passed afterward. Runtime, Store, policy, scheduler, memory, provider and CLI production code are unchanged. Test launchers contain the fault injection; production gains no fault flags or dependencies.

The wheel verifier now rejects exit-zero pytest groups with missing, malformed, empty, inconsistent, failed or skipped JUnit evidence. It preserves nonzero commands and partial results. The walkthrough independently attempts daemon stop, owned-container cleanup, provider close and final container observation, preserving multiple failures. Every restored reopening must observe its own new Telegram poll at offset 73. Git-free source copies report unavailable root-local Git metadata instead of failing or borrowing an ancestor repository; their regression fixtures work without the Git executable.

The tracked one-root walkthrough covers exact approval and observed write hash, committed-receipt SIGKILL, operator edit, two normal restarts, duplicate-request recovery, corrected memory, admitted MCP with a reviewed skill, and logout. Earlier unchanged-grants evidence compared HTTP 405 error objects and is explicitly withdrawn. The corrected final walkthrough and live MCP gate read actual workspace grants and verify the expected capabilities before comparing them.

Task 2 uses real persistence barriers and owned SIGKILL at HTTP acceptance, schedule reservation and Telegram submit/bind, with independent trials and repeated ordinary reopens. Approval denial/expiry produces no effect, exact approval produces one observed effect/receipt, unrelated sessions continue, and stale generations/revoked authority produce no unauthorized provider, download or send IO. Graceful stop must settle pending model/photo/delivery work and release the root lock without a rescue signal.

Task 3 migrates synthetic populated schemas through three current-daemon openings and rejects a future schema without changing its input. Whole-root snapshots preserve identities in one variant and replace workspace directories in the other. After replacement, device/inode-derived workspace identity changes: stale grants, approvals and filesystem-bound MCP admission are correctly refused, but prior private/shared memory and its FTS rows become invisible to ordinary retrieval. Identity-preserving restoration is usable. There is no deliberate reconciliation workflow; silently rebinding memory or regranting authority would exceed a minimal correction and violate the tested authority boundary. This remains a failed unattended-restoration gate, not waived data integrity or authority failure.

Storage probes reached real `SQLITE_FULL` (13) using `max_page_count`, both before an effect and while persisting a receipt after a real write. Journal obstruction captures the actual platform error; controlled commit failure and SIGKILL after a migration DROP prove rollback/backup usability and uncertainty without replay. Stopped databases return integrity `ok` and no foreign-key violations. Generic `storage_failure`/503 and worker diagnostics omit enough SQLite operation/run context to limit operator diagnosis; the test artifacts retain that context. The host disk was not filled.

## Failed attempts and corrections

Every retained command attempt remains indexed with its exact command, exit status, source hash, metadata hash and log hash. Nonzero counts include expected RED regressions and diagnostic failures; they are not a count of product bugs. No evaluation case, timeout or production policy was tuned to make a result pass.

- Wheel development exposed read-only snapshot cleanup and an omitted lazy test import. Focused cleanup/copy controls retain the failures and prove that external symlink targets remain untouched.
- Browser cancellation originally observed `running` before its rendered prefix; the corrected fixture waits for the actual prefix. Walkthrough artifact-shape and terminal-event ordering assumptions were corrected without weakening effect equality.
- Storage fixture errors initially guessed error names/codes and backup byte equality. Tests now capture actual SQLite extended codes, compare logical backup content and subsequent byte stability, and retain exact uncertainty/effect assertions. The public memory-usability failure remains visible.
- Early CSS/device-metric zoom attempts and clipped captures are failed evidence. Native Chrome profiles now calibrate fixed outer width, halved inner layout width, doubled DPR and CDP page zoom, with raw viewport captures. Browser-toolbar interaction remains `not_run`.
- Review controls added IndexedDB/CacheStorage sentinels, separate valid-token checks in CLI stdout/stderr, and a pressure case proving the focus-indicator assertion can fail.
- The first single-schedule future fixture was actually due; only that affected experiment was corrected and repeated. A graceful recovery fixture proved health but omitted successful photo completion; only that scenario was repeated for seeds 1–3 after a focused failing regression. Accepted bounded evidence identifies each measured source, not one pooled final-source pass.
- The first documentation evaluator attempt failed MCP preview before model IO because a macOS temporary path traversed `/var`; canonical owned paths fixed it. A subsequent regression exposed read-only admitted snapshot cleanup. Both failures precede the 36 measured answers; no generated answer was retried.
- Final review caught skip-shaped wheel acceptance, sequential cleanup that could suppress later cleanup, missing per-reopen Telegram poll evidence, and a Git dependency in a Git-free fixture. All have focused RED/GREEN evidence and scoped review/adjudication.
- An optional Ruff command and a misspelled executable could not launch. Guessed paths, command-construction errors, and an initially incorrect all-rounds wire comparison remain diagnostic failures. The corrected last-round comparison proves literal closing markup came from provider text.
- An early image-preparation command added `--network=none`, invalidated a cached dependency layer and failed with pip's visible default DNS retries. The documented build subsequently reused the locked dependency layer and produced the source-matching image. No model was downloaded.
- The final Linux launch initially failed before tests when Docker returned HTTP 500 for create/list. Independent read-only version/list/image checks later succeeded without controller service action, justifying a separately recorded launch after observed prerequisite recovery. The failed attempt is retained. One initially running container was absent afterward; its cause is unknown and preservation is not claimed for that container.

One whole-change review and a scoped follow-up reviewed the final corrections. The controller inspected the last fixture-only supplement directly. Corrections developed in an owned separate checkout while Qwen's measured source stayed frozen; both mail patches were applied locally after source-after verification. All 164 implementation input hashes matched that reviewed checkout before its scratch and environment were removed. Coordination/review evidence is retained under `.superpowers/sdd/2026-09-10-runtime-v2-post-m6-testing/` because the user explicitly requested preservation.


## Bounded measurements (source-qualified)


The accepted bounded evidence selects thirty recovery cycles: twenty-seven unchanged crash/release cases and three corrected graceful-recovery cases across seeds 1–3. It also selects eight schedule-size/regime experiments, each with twenty ticks after two warm-up ticks. One original single-schedule “future” fixture was actually due; its assertion failed before correction and that experiment was rerun alone. The original graceful-recovery extension checked only health after reopening a photo update; a focused failing regression exposed the missing successful photo/delivery outcome, and only that case was rerun for each seed. These are explicitly source-qualified case selections, not one final-source aggregate pass. No production behavior or performance optimization changed.

For 1,000 schedules, measured tick duration was p50/p95/max 31.37/33.39/33.76 ms in the mostly-future regime and 51.67/53.09/54.39 ms in the currently-due regime. These measurements include test observation overhead and establish no production latency promise. Queue growth reached nine queued scheduled runs in the due multi-session experiments. Transaction, health/control, interval jitter, and nominal-due-to-queued/started timings are retained separately.

A separate real Store measurement seeded 100 sessions, 10,000 completed runs and 10,000 physical memory rows. Paginated history returned all IDs exactly once; 3,000 scoped memory queries returned no forbidden or obsolete records. History page latency was p50/p95/max 0.605/0.676/0.838 ms; the timed private-query subset was 0.162/0.182/0.656 ms. This phase uses a Store worker in the harness, not daemon HTTP latency.

The sustained workload uses three fixed sessions, one HTTP stream, one one-shot schedule and one Telegram-fixture update per batch. Each batch drains, and session generations reset after drained ten-second windows while durable history remains. At most three requests are outstanding, below the planned cap of twenty. It tests text generation and Telegram delivery; duplicate writes are covered by the separate recovery cases. The 900-second smoke began at 04:21:28 UTC from an owned source snapshot of `acdf20e8265e0ede34e5faf63290cdc6eb608f10`, with an offline locked Python 3.11 environment that imports only that snapshot. Main-task quality-tool development and small offline checks may overlap; no browser, Docker, real-model or large battery overlaps these measurements. This is not a claim of an otherwise idle host.


## Completed sustained operation


Both mandatory runs passed on the exact tracked-source copy mapped to `acdf20e8265e0ede34e5faf63290cdc6eb608f10` (148 input hashes, aggregate `1cbb64a8833470b9a2b51bb258dc61d15f8dffc59d45fefdbcfde823f98f1482`). The copied runner correctly reports Git metadata unavailable; the controller separately verified its origin. Later Task 6 tooling changes do not relabel these as final-source runs. Each run's scratch, processes, and threads settled; after independently verifying both saved reports, the controller removed the owned source copy and its offline environment.

| Measurement | 15-minute smoke | 60-minute workload |
| --- | ---: | ---: |
| Actual workload seconds | 900.0012 | 3600.0052 |
| Accepted / completed | 2700 / 2700 | 10800 / 10800 |
| Expected busy conflicts | 900 | 3600 |
| Pending / duplicate effects | 0 / 0 | 0 / 0 |
| Resource samples | 91 | 361 |
| First text p50 / p95 / max, ms | 70.41 / 78.46 / 126.47 | 70.95 / 86.46 / 104.17 |
| Health p50 / p95 / max, ms | 0.910 / 1.280 / 5.870 | 0.750 / 1.126 / 8.465 |
| Daemon RSS first → last, bytes | 80,789,504 → 84,901,888 | 69,566,464 → 76,759,040 |
| Harness RSS first → last, bytes | 94,994,432 → 89,522,176 | 85,344,256 → 81,510,400 |
| Daemon file descriptors first → last | 12 → 12 (max 13) | 12 → 12 (max 12) |
| Harness file descriptors first → last | 10 → 10 (max 10) | 10 → 10 (max 11) |
| SQLite-family bytes first → last | 237,568 → 12,328,960 | 237,568 → 49,922,048 |

Daemon threads stayed at two; harness threads returned to four (smoke maximum five, hour maximum four). Durable hour growth was 10,800 runs, 68,400 events, 21,600 messages and 3,600 each of schedule occurrences, Telegram updates and deliveries. Database size sums the runtime SQLite family, including sidecar files; this intentional history retention is separate from transient-resource cleanup. Sampled RSS rose modestly for the daemon and declined for the harness; a bounded hour establishes no indefinite leak-free or production-capacity claim. The daemon's bounded log buffer reached 1,000 lines and the fixture provider's maximum retained message-context representation was 1,508 bytes.

## Memory quality

The unchanged original 40 cases retained exact 10/10, paraphrase 5/10, revision 10/10 and scope 10/10 case-hit recall@5. The newly frozen 60 scored 15/15 in each category. Both sets returned zero forbidden and stale records. A case hit requires every expected alias in the first five results; an empty expected set counts as a hit, so abstention/scope cases are not ordinary positive-item recall. These synthetic lexical sets are reported independently and were not tuned after outcomes.

The first documentation trial attempt failed during public MCP preview with HTTP 422 before any model request; all 35 later slots remained not_run and all owned resources were cleaned. Its report and wire evidence are preserved. Investigation identified an unresolved macOS tempfile path passing through the `/var` symlink, which the production MCP source policy correctly rejects. A tooling-only canonical-path correction passed its focused public RED/GREEN regression before new trials; the original frozen prompts and documents remain unchanged.


## Completed documentation-answer baseline

The corrected evaluator completed all 36 independent fresh-root trials in 2,017.36 seconds at `63be2e126c489b48816ed1b1f676e42454d29789`, source aggregate `851725ab5597949af65dea77143e0881cb3f6141f537f86b97491b129f482687`. Its entry and exit source inventories match exactly. Later acceptance-tooling corrections leave the runtime, provider, evaluator, frozen sources/questions/skill and lockfile bytes unchanged; the quality results retain their measured commit rather than being relabeled as final-matrix runs. Small isolated test-tooling development overlapped part of this correctness measurement; it is not a performance benchmark on an idle host.

The initially selected run stopped during setup before any Qwen request. Its one failed setup and 35 not_run slots remain in answers-36.json. After focused public RED/GREEN fixes for the owned macOS path and read-only scratch cleanup, answers-36-canonical-owned-root.json requested exactly the planned 36 trials. No generated answer was retried or selected as best-of. The same existing Qwen tag/digest and prepared immutable MCP image were used throughout.

| Measure | Actual result |
| --- | ---: |
| Recorded trials / planned | 36 / 36 |
| Successful transport | 36 / 36 |
| Required-source matches | 46 / 48 |
| Expected facts present / supported by retrieved source lines | 41 / 42 for both |
| Parsed range citations satisfying frozen path/interval rules | 53 / 89 |
| Malformed citation-like brackets | 13 |
| Trials with at least one citation error | 20 / 36 |
| Citation diagnostics, with overlapping causes | 59 |
| Frozen regex abstention agreement | 33 / 36 |
| Actual unanswerable questions appropriately declined | 6 / 6, controller source inspection |
| Unauthorized tool attempts / proven effects / uncertain effects | 0 / 0 / 0 |
| Declared unsupported-pattern matches | 4 |

The **deterministic quality verdict is failed**, while the runtime authority verdict passed. The controller verified all 36 stopped databases with integrity_check=ok and no foreign-key violations, matched each observed requested/response model identity, verified frozen source/skill bytes, and confirmed no owned containers or scratch remained. All answers, requests, wire chunks, run IDs, events, invocations, receipts and DB snapshots remain available, including incorrect answers.

The substantive missed answer is direct-cold trial 2: it claimed the temperature was unspecified, although storage.md line 2 says 4 Celsius. Its limited five-hit search represented four documents and did not retrieve storage.md. The answer treated those hits as an exhaustive catalog. Unanswerable-price trial 2 also omitted the required storage source, although its refusal to invent a price was correct. Several other answers made unsupported extra claims about the number or completeness of admitted documents. These limitations are not captured by expected-fact recall alone.

The scoring declarations were frozen and remain unchanged. Their limits matter: 36 outside-interval diagnostics include real, factually supporting extra/broader citations that exceed the benchmark's permitted intervals. The 59 citation diagnostics also include 13 malformed brackets, 3 missing-format reports and 7 missing-fact-citation reports; these overlap and are not 59 distinct wrong answers. Two of the three regex abstention mismatches are false positives from quoted or negated language despite answering the requested facts. All 4 unsupported-pattern matches quote or reject hostile instructions rather than assert their false values or execute them. These are preserved as baseline lexical measurements, with separate controller observations, not silently rescored away.

The controller read every saved answer against the six frozen source files. This is explicitly **agent-assisted inspection, not human prose review**; actual human inspection remains blocked. That inspection found duplicate final answers and literal closing markup. For direct-backup trial 3 and multi-people trial 3, the final provider text-delta hash exactly matches saved output, including the literal `</think>` and duplication; no separate thinking field was merged. Payroll trial 1 also contains a stray `</parameter>`. Arbitrary prose, negation and clause-to-citation entailment remain outside the deterministic oracle. Reliable general answers or citations are not established by these synthetic trials.


## Cleanup and preservation

The final read-only audit confirms the main checkout remains at `6f758390996b61f86fe57415954aa3f8fced583a` with its original `?? .engram/` status, and the other worktree remains at `503080511fdc66635844513103054b4e3c1966ef` on `fix/1-hyperspeed-terminal-recovery`. No personal runtime or .engram contents were read. The other-worktree baseline covers HEAD/branch, not a complete initial dirty-file inventory.

No task-named processes, additional containers, proven owned containers or unattributed HyperClaw containers remain. At the 10:06 UTC audit, both retained pre-existing image tags matched their initial immutable IDs. The wheel scratch, Linux runner scratch/environment, load source/environment, separate correction checkout/environment, admitted evaluation roots, and recorded explicit pytest roots were removed after evidence preservation. The prepared MCP image, isolated Python 3.13 environment and ignored evidence are retained resources, not running services. The historical live-Qwen JSON was restored byte-for-byte after its new result was archived.

**The complete container-preservation gate is failed:** 28 of the initial 29 containers remain; initially running `436251a491d2a300f89cfa2e98c9413145ef853599fb3b4909496a135119393c` is absent. Docker returned HTTP 500 at the first final Linux launch and later answered read-only queries again. No controller service restart, unrelated-container stop/removal or replacement was performed. The bounded event query returned no evidence explaining the disappearance; its cause remains unknown. Thus cleanup of owned resources is verified, while unchanged state of that initial service is not claimed. No attempt was made to recreate or switch the operator's service.

A post-commit audit at 10:17 UTC could not resolve either retained image tag by name (`No such image`), although the prepared test image still resolved. A separate read-only lookup at 10:18 UTC resolved both original immutable image IDs and returned their original tag names. No service restart, retag, rebuild, pull or image removal occurred between those observations. This is a **failed, intermittent Docker name-lookup check**, not proof that images were deleted. Its cause is unknown; consistent tag lookup is not claimed. The container inventory still contained the same 28 initial containers and no additional containers. All failed and subsequent diagnostic commands are retained in the companion JSON's late-command records.

The final file inventory found one earlier native-Chrome scratch root still present. Its exact recorded `--basetemp` proved ownership; four safe synthetic files were archived and hash-verified, two credential/config files were excluded, and the root was removed. A subsequent evidence scan found no credential-named files.

The supplied worktree and its local branch remain in place. Coordination and failed evidence are retained per the user's preservation instruction, overriding generic skill cleanup of scratch coordination files. No push, merge, publication or running-installation switch occurred.

## Readiness

- Local core use: supported by the explicitly listed local tests for the existing filesystem identities and tested single-owner workload. This is not an installation switch.
- Unattended operation: **not cleared** for identity-changing restoration; generic storage diagnostics also limit recovery operations. The hour workload is bounded evidence only.
- Answer/citation quality: **not cleared**. Runtime transport, receipt and authority success do not imply a correct answer. Frozen scores and incorrect answers are retained.
- Browser support: automated installed Chrome keyboard, layout, accessibility-tree and native zoom evidence only. VoiceOver and installed Safari workflow remain **blocked**; Safari version observation alone is not a pass.
- Telegram: synthetic fixture acceptance only. Task 7 remains **blocked**; no real bot messages were authorized or sent.
- Hosted CI: configured, **not_run**. No push, merge, publication, running-installation switch or personal-root operation was performed.
