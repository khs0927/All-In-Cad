# CAD 자동화 상태기계 명세 (run_spec 1.0)

이 문서는 `run_spec.json` 과 `vectors/golden_vectors.json` 을 설명한다.
**이 명세가 정본이다.** 원본 C# 상태기계나 이후의 어떤 호스트 구현도 이 명세보다 우선하지 않는다.
구현이 명세와 어긋나면 명세가 틀렸다고 판단하기 전에 벡터를 다시 실행해 근거를 낼 것.

## 1. 라벨링 규칙

이 문서의 모든 진술은 아래 세 라벨 중 하나를 가진다.

| 라벨 | 뜻 |
|---|---|
| `[관측]` | ZWCAD 2026 실측 또는 원본 `XicadRunner.cs` 코드에서 직접 확인된 사실 |
| `[결정]` | 사용자가 확정했거나, 관측 사실에서 직접 도출한 설계 선택. 다르게 정할 수 있지만 명세를 바꾸기 전까지는 규칙이다 |
| `[불가]` | 현재 확인하지 못한 것. 추정이 아니라 확인 불가로 적어 두었다 |

## 2. 설계 근거

### 2.1 왜 소스가 아니라 명세인가 `[결정]`

이식 분석 결론 0.98 에 따라, 원본 `XicadRunner.cs` 는 ZWCAD SDK 타입(`ZwApplication`, `Database.TransactionManager`, `SendStringToExecute`)과 포커스 의존 유휴 이벤트 펌프에 결합되어 있다. 이 결합은 그대로 옮길 수 없다. 따라서 상태·전이·가드·오류 코드를 언어 중립 JSON 으로 먼저 고정하고, 모든 후속 호스트 구현이 골든 벡터를 통과하는 것을 통과 조건으로 삼는다. 소스는 이 명세의 **참조 구현**이지 권위가 아니다.

### 2.2 4상 상태기계의 일반화 `[관측]`

원본의 실제 4상은 `launch` → `start_settle` → `gate` → `finish_settle` 이다 `[관측]`. 이를 8개 상태로 일반화했다.

| 원본 4상 | 일반화 상태 | 근거 |
|---|---|---|
| `launch` | `launching` | 사전 스냅샷 캡처, 실행 기록 개시, 실행 문자열 1회 전송이 모두 이 상태에 있다 `[관측]` |
| `start_settle` | `launching` 내부 (T03 / T04) | `launch` 와 별 상이 아니라 "명령이 기동했는지를 기다리는" 하나의 대기다 `[관측]`. 기동 관측이 없으면 `command_started=false` 인 채 실패한다 |
| `gate` | `awaiting_input` (T05 / T06 / T07) | 인자 1개 전송, 계약 프롬프트 대조, 빈 인자 건너뛰기가 모두 인자 단위 진행이다 `[관측]` |
| `finish_settle` | `executing` (T08) | 인자를 다 보낸 뒤 호스트 명령이 스스로 끝날 때까지의 대기다 `[관측]` |
| `CompleteJob` 의 델타 판정 | `verifying` (T09 / T10 / T11) | `[관측]` |
| `TryRollback` | `rolling_back` (T12) | `[관측]` |

`committed` / `failed` 는 원본의 `Status = "ok"` / `"failed"` 문자열에 대응하는 종료 상태이며, 여기에 `verifying` 과 `rolling_back` 을 명시 상태로 세운 것은 "검증 실패 뒤 되돌리기"와 "검증 중 카운터 재시도"를 서로 다른 경로로 관찰 가능하게 만들기 위해서다 `[결정]`.

`idle` 은 원본에는 상주 상수로만 있던 상태를 명시한 것이다 `[결정]`. 승인 게이트(T02)가 여기서만 작동하므로 상주 상태로 못 둔다.

### 2.3 호스트 중립 용어 `[결정]`

원본 코드의 `CMDACTIVE`, `LASTPROMPT`, `CMDNAMES` 는 ZWCAD 고유 시스템 변수 이름이므로 명세에 쓰지 않는다. 대신

- `host_state` — 명령 수행 중 여부 (원본 `IsInCommand()` / `CMDACTIVE` 대응) `[관측]`
- `host_prompt` — 현재 프롬프트 문자열 (원본 `LASTPROMPT` 대응) `[관측]`
- `host_command_stack` — 중첩 명령 스택 (원본 `CMDNAMES` 대응) `[관측]`

이벤트 구동 방식(`Application.Idle`, `WM_TIMER`)도 명세에 쓰지 않는다. 명세는 **전이**만 규정하고 스케줄러는 각 호스트의 책임으로 둔다 `[결정]`. 다만 `schedule_is_not_focus_dependent` 가드 하나만 예외적으로 유지한다. 함정 3 때문에 "포커스를 잃어도 멈추지 않는 것"은 명세가 보장해야 할 불변 조건이기 때문이다 `[결정]`.

### 2.4 기록 경로와 표현 규칙 `[결정]`

- 주 기록 경로는 ezdxf(Python) 다. FreeCAD 는 사람이 여는 검증용 호스트이며 실행 주체가 아니다.
- 쓰기 포맷 DXF R2018(AC1032), 읽기 하한 R2000(AC1015).
- **벽과 문은 블록 INSERT 도 해칭도 쓰지 않고 LINE 과 ARC 조합으로만 표현한다.**
  - `[관측]` 실제 CAD 에서 그려진 벽 9개 엔티티가 전부 LINE 이었다.
  - `[관측]` 노멀라이저가 블록 INSERT 를 전개하지 않고 HATCH 를 파싱하지 않는다 (`topology.py` 의 `segments_from_entities` 는 `geometry.start`/`end` 또는 `geometry.points` 만 읽는다).
  - `[결정]` 따라서 블록과 해칭은 토폴로지 세그먼트에 0개를 기여하므로, 노멀라이저 갭을 우회로 회피한다. 정규화기에 INSERT 전개나 HATCH 파싱을 새로 넣지 않는다.
  - `[결정]` 레이어 매핑은 `configs/architectural-layers.json` 을 따른다: 벽 = WAL1/WAL2/WAL3, 문 = DOOR/DOOR_ELE `[관측]`. 알 수 없는 레이어는 보존하고 표시만 한다 `[관측]`.

### 2.5 읽지 못한 자산 `[불가]`

`artifacts/w1-port.md` (이식 분석 1순순서 0→8) 는 이 저장소에 존재하지 않았다. `docs/` 하위 `*.md` 전체를 훑었으나 해당 파일이 없다. 따라서 1순순서 0→8 항목은 이 명서에 반영되지 않았다. 후속 작업에서 이 문서가 제공되면 전이 순서와 우선순위를 대조해 갱신해야 한다.

## 3. 런타임에서 확정된 6개 함정 → 가드 매핑

아래 표가 이 명세의 핵심이다. 각 함정은 관측 실측이며, 가드로 구현되며, 골든 벡터로 고정된다.

| # | 함정 (관측 실측) | 가드 | 전이 | 벡터 | `rollback` 정책 |
|---|---|---|---|---|---|
| 1 | 개행 종료가 없으면 입력 버퍼에 쌓여 다음 전송이 뒤에 붙는다. 실측 `"_.LINE_.U"` → 알 수 없는 명령 | `terminate_every_input` | T01, T05 (모든 `inputs_sent` 항목에 종료자 1개 강제) | GV-14 | 해당 없음 |
| 2 | 유휴 프롬프트에서 빈 Enter 는 마지막 명령을 재실행한다. 실측으로 치수 명령이 되살아나 좌표를 가로챘다 | `no_bare_enter_when_idle` | T06 | GV-06 | 수행 중 빈 인자는 `E_EMPTY_ARG_WHILE_BUSY` → required |
| 3 | `Application.Idle` 는 창이 포커스를 잃으면 멈춘다. 실측으로 40초 정지 후 타임아웃. WM_TIMER 해법은 이식 불가 | `schedule_is_not_focus_dependent` | 스케줄러 규약 (전이 아님). 전이 자체에는 없음 | GV-10 (타임아웃 경로로 관측) | 해당 없음 |
| 4 | 실행되지 않은 명령에 UNDO 를 보내면 사용자 작업이 사라진다 | `rollback_requires_started_command` | T04, T12 | GV-04, GV-05 | `E_COMMAND_NOT_STARTED` 의 rollback = **forbidden** |
| 5 | 네이티브 명령과 LISP 명령의 호출 형태가 다르다. 스크립트 접두는 LISP 명령에만 유효하고 네이티브 명령에 쓰면 실패 | `invocation_form_matches_command_kind` | T01 (실행 문자열 구성) | GV-13 (양쪽 형태) | `E_INVALID_INVOCATION` = forbidden |
| 6 | 엔티티 수 일시 읽기가 0 을 반환할 수 있다. 검증용 카운터는 안정 판정을 거쳐야 한다 | `entity_count_stable` | T09 | GV-11 | `E_COUNTER_UNSTABLE` = conditional (판정 불가이지 실패 증거 아님) |

함정 3 은 유일하게 "전이"가 아니라 "구동 방식"에 대한 규칙이다. 그래서 가드 목록에는 있지만 전이 표에는 없다. 구현 검토 시 이 점을 놓치면 포커스 의존 펌프가 그대로 이식된다 `[결정]`.

## 4. 전이 요약 (13개)

| id | from → to | 성격 | 주 가드 |
|---|---|---|---|
| T01 | idle → launching | 정상 진입 | `job_admissible` |
| T02 | idle → failed | 파괴적 승인 거부 | `destructive_requires_approval` |
| T03 | launching → awaiting_input | 기동 관측 | `command_started` |
| T04 | launching → rolling_back | 기동 실패 | `start_settle_deadline_expired` |
| T05 | awaiting_input → awaiting_input | 인자 1개 전송 | `prompt_matches_contract` |
| T06 | awaiting_input → awaiting_input | 빈 인자 건너뛰기/중단 | `no_bare_enter_when_idle` |
| T07 | awaiting_input → executing | 인자 소진 | `all_args_sent` |
| T08 | executing → verifying | 명령 종료 관측 | `command_completed` |
| T09 | verifying → verifying | 카운터 안정 재시도 | `entity_count_stable` |
| T10 | verifying → committed | 델타 통과 | `entity_delta_in_range` |
| T11 | verifying → rolling_back | 델타 위반 | `entity_delta_out_of_range` |
| T12 | rolling_back → failed | 되돌리기 또는 생략 | `rollback_requires_started_command` |
| T13 | launching → failed | 프롬프트 미제공 | `host_prompt_available` |

`on_failure` 필드는 `{ "target_state", "error_code" }` 객체이며, 해당 전이의 정상 가드가 기한 내에 만족되지 않았을 때 갈 경로를 뜻한다. 전이를 늘리기 위해 만든 것이 아니라, 원본 코드의 `FailJob(...)` 호출 지점마다 실제 존재하던 경로를 그대로 옮긴 것이다.

## 5. 오류 코드 정책

`rollback` 필드는 세 값만 쓴다.

- `required` — 명령이 시작되었으니 되돌려야 한다.
- `forbidden` — 명령이 시작되지 않았거나 아무것도 전송하지 않았다. 되돌리기를 **보내지 않는다.** 사용자 작업 보호가 우선이다 `[결정]`.
- `conditional` — `command_started` 값에 따라 T12 가 결정한다.

`E_COMMAND_NOT_STARTED` 가 `forbidden` 인 것이 이 명세에서 가장 비자명한 규칙이며, GV-05 가 이를 직접 고정한다.

## 6. 검증 상태에 대한 정직한 고지 `[불가]`

이 명세는 **JSON 구문 검증만** 되었다. 골든 벡터를 실행하는 상태기계 구현은 아직 없다. 따라서:

- "벡터를 통과했다" 는 주장을 이 문서에서 하지 않는다.
- `inputs_sent` 의 종료자 표기는 관측된 C# 코드의 `arg + "\n"` 형태를 그대로 옮긴 것이며, 원본 코드 주석과 실측 로그에 근거한다 `[관측]`.
- GV-06 / GV-10 / GV-13 의 종료 상태 조합은 원본 코드의 분기에서 도출한 것이며, 실제 호스트에서 재현 확인이 필요하다.
- 실행 절차는 `README.md` 참조.

## 7. GV-10 벡터 결함과 정정 기록 (2026-09-26) `[관측]`

`machine.py` 구현 과정에서 GV-10 이 명세 규칙과 자기 자신의 기대가 충돌하는 것을 발견했다. **벡터가 잘못된 것이지 명세가 잘못된 것이 아니므로, 구현을 느슨하게 고치지 않고 벡터를 고쳤다.**

- **결함** `[관측]`: GV-10 은 `deadline_expires_after_args=2` 인데 `prompt_sequence` 를 1항목(`"Specify first point: "`)만 제공하고 있었다. `golden_vectors.json` 의 규칙("배열이 부족하면 마지막 값을 재사용")에 따라 2~4단계가 `"Specify first point: "` 를 재사용했고, 이는 2단계 계약 `"Specify next point or [Undo]: "` 와 불일치했다. 그 결과 `E_TIMEOUT` 에 도달하기 **전에** `E_PROMPT_MISMATCH` 로 먼저 죽었고, 인자는 1개만 전송됐다. 벡터의 `expect` 는 인자 2개 전송 후 `E_TIMEOUT` 이었다.
- **의도된 시나리오** `[결정]`: "호스트가 인자 2개를 받고 더 이상 새 프롬프트를 내지 않아 기한이 만료"다. 기한 만료 시나리오는 각 단계의 계약을 만족하는 프롬프트가 계속 제공된다는 전제 위에서만 도달 가능하다.
- **정정** `[결정]`: GV-10 의 `given.prompt_sequence` 를 4항목으로 늘렸다. `["Specify first point: ", "Specify next point or [Undo]: ", "Specify next point or [Undo]: ", "Specify next point or [Undo]: "]`. 그 외 어떤 필드도 바꾸지 않았다. `machine.py` 는 한 줄도 수정하지 않았다.
- **고정된 불변식** `[관측]`: `machine_test.py::test_gv10_deadline_invariant_is_pinned` 이 (1) 정정된 벡터가 `E_TIMEOUT` 으로 끝나고 인자가 정확히 2개 전송되는지, (2) `len(prompt_sequence) == len(args) == len(contract)` 이고 각 계약이 대응 프롬프트에 포함되는지, (3) 정정 전 1항목 데이터를 다시 넣으면 `E_PROMPT_MISMATCH` 로 되돌아가며 절대 `E_TIMEOUT` 이 되지 않는지, 를 함께 단언한다. 즉 "프롬프트가 바뀌면 불일치 판정이 기한 만료보다 우선한다"는 규칙이 회귀 테스트로 남는다 `[결정]`.
- **위 6절의 "상태기계 구현은 아직 없다" 는 이 시점에 더 이상 사실이 아니다** `[관측]`: `recorder/machine.py` 가 구현되었고 골든 벡터 14개가 주입된 가짜 호스트에서 모두 통과한다. 그 통과는 이 명세의 판정 로직에 대한 증거일 뿐이며, 실 CAD 호스트에서의 동작 증거는 아니다 `[불가]`.
