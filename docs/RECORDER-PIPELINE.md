# Recorder Pipeline — 벽/문 기록 자동화 스택

> **검증 시점: 2026-09-26 19:31–19:36 KST.**
> 이 문서의 모든 수치는 그 시각에 이 호스트에서 직접 실행해 얻은 것이다.
> 문서 작성 중 `src/all_in_cad/recorder/cli.py`가 **1239 → 1567줄로 actively 수정되고 있었다**
> (§9 관측 12). 따라서 이 문서는 **이동하는 대상(target)에 대한 스냅샷**이며,
> 수치를 인용하기 전에 반드시 재실행해 재확인하라.

> **저자도 설계 의도를 완전히 기억하지 못한다.** 이 문서의 목적은 다음 사람이
> "무엇이 실측이고, 무엇이 선택이며, 무엇을 아직 모르는지"를 구분한 채 바로 이어갈 수 있게 하는 것이다.
> 각 진술은 `[관측]` / `[설계]` / `[미확정]` 라벨을 가진다.

---

## 1. 빠른 시작 (실측 검증됨)

`C:\Users\khs09\all-in-cad` 에서 실행한다 (`all_in_cad` 패키지가 resolvable 해야 한다).

```powershell
$env:PYTHONHOME=$null; $env:PYTHONPATH=$null
& C:\Users\khs09\all-in-cad\.venv\Scripts\python.exe -m all_in_cad.recorder.cli plan --start 0 0 --end 12000 0 --thickness 200 --center 6000 0 --width 900 --out plan.dxf
& C:\Users\khs09\all-in-cad\.venv\Scripts\python.exe -m all_in_cad.recorder.cli verify --in plan.dxf --expect-entities 11 --expect-wall-thickness 200 --expect-door-width 900 --expect-door-center 6000 0
```

**실측 결과 (2026-09-26 19:33 KST)**

```
11 entities: CEN1=1, DOOR=4, DOOR_ELE=2, WAL1=2, WAL2=2
RESULT: PASS (19 checks)          # exit code 0
```

`--expect-*` 네 플래그를 **전부 주어야** 19개가 된다. 플래그를 빼면 15개다 (§4 참조).

### 왜 "생성"과 "교체"는 다른 계약인가 `[관측]`

`plan.dxf` 가 이미 있으면 `plan` 은 **exit code 2 로 거부하고 파일을 건드리지 않는다**:

```
error: refusing to overwrite an existing drawing: plan.dxf (size=20693 sha256=e1e8616a...)
EXIT=2
```

`--force` 만이 교체를 허용하고, 그 의미는 세 서브커맨드에서 동일하다.
`wall` / `door` / `plan` 은 **append 하지 않는다.** append 역시 요청되지 않은 변�이며,
"어떤 한 번의 호출도 선언하지 않은 내용"의 파일을 만들게 되기 때문이다.
`--out` 이 지정된 디렉터리에 없는 상태에서 시작하는 것이 기본 계약이다.

---

## 2. 환경 함정 — 이 호스트에서 가장 먼저 만나는 벽

Aside 런처는 이 Windows 호스트의 프로세스 환경에

```
PYTHONHOME=C:\Users\khs09\.aside\runtime\python\runtime
```

을 주입한다. 이 값은 **프로젝트 venv 인터프리터의 표준 라이브러리를 가린다.**
그래서 `import ezdxf` 가 실질적으로는 `ezdxf` 가 아니라 표준 라이브러리에서 죽는다:

```
ModuleNotFoundError: No module named 'annotationlib'
```

`annotationlib` 은 ezdxf 의 의존성이 아니다. 이 이름이 뜨면 **ezdxf 가 깨진 게 아니라
표준 라이브러리가 숨겨진 것이다.** 이 오류를 "ezdxf 설치 문제"로 진단하면 시간만 잃는다.

따라서 **모든 실행 예시는 `PYTHONHOME` 과 `PYTHONPATH` 를 비운다.** 자식 프로세스는 이를 상속하므로
부분 모듈(subprocess 으로 CLI 를 띄우는 `cli_test`)도 스스로 비운다.

```powershell
$env:PYTHONHOME=$null; $env:PYTHONPATH=$null
& C:\Users\khs09\all-in-cad\.venv\Scripts\python.exe -m pytest src/all_in_cad/recorder -q
```

`wall.py` · `door.py` · `transaction.py` · `window.py` · `cli.py` 모듈 docstring 에
동일한 메모가 각자 중복해서 적혀 있다. 한 곳만 고치면 다른 모듈의 예제가 틀린다.

**이것은 호스트 런처의 버그이지 이 모듈들의 결함이 아니다.**

---

## 3. 설계 원칙 — 이 프로젝트의 만성병과 그 해법

원본 C# 러너(`native/zwcad2026/XicadRunner.cs`)가 5가지 구조적 결함(C-1..C-7)을 가졌고,
그 각각에 대해 이 스택이 **증상 제거가 아니라 구조 제거**를 택했다.

### 원칙 1 — 아티팩트 없으면 무조건 실패. 마커·종료코드·메시지를 믿지 않는다

`freecad/freecad_runner.py` docstring 에 기록된 실측:

| 항목 | 관측값 |
|---|---|
| freecadcmd 실행 | **성공** (출력 파일 생성, 19 오브젝트 생성) |
| stdout | **아무것도 없음** — `[AIC-BEGIN]` / `[AIC-OK]` / `[AIC-FAIL]` 전부 부재 |
| 프로세스 종료코드 | **0** |

즉 "스크립트가 성공했는데 마커가 안 보이고 exit 0" 인 상황이 실측으로 존재한다.
구版 로직은 이걸 `unknown` 으로 판정했고, 실제로는 `status="failed"` 를 스스로 기록한
스크립트를 `unknown` 으로 흘려보냈다.

그래서 판정 우선순위를 **artifact-first 로 뒤집었다**:

```
1. timeout                       -> failed
2. exit code != 0                -> failed
3. 예상 아티팩트 없음 / 0 바이트  -> failed   (무조건. 아티팩트 없음 = 조용히 잃어진 도면)
4. verdict JSON 레코드            -> 권위 있는 신호
     있고 status=="ok"            -> ok      (evidence: artifact-verdict-json)
     있고 status!="ok"            -> failed  (사유는 레코드에서)
     필수인데 없음                -> failed  (verdict-record-missing)
5. verdict 레코드 미설정(레거시 모드)일 때만 stdout 마커:
     fail -> failed / ok -> ok / none -> unknown   (조용히 승격시키지 않음)
```

`unknown` 세 번째 상태는 **의도적으로 보존된다.** "마커 없음"은 성공으로도 실패로도 승격되지 않는다.
`aic_headless_script.py` 는 성공 경로와 실패 경로 **양쪽**에 UTF-8 JSON verdict 레코드를
반드시 남기도록 강제한다 (`<workdir>/out/aic_verdict.json`).

> 원인 자체는 **미확정**이다. 왜 그 1회에서 stdout 이 비었는지는 확정하지 않았고,
> 모든 실행에서 재현되지도 않는다(같은 호스트의 live 실행에서는 마커가 보였다).
> 채널이 간헐적으로 죽는다는 사실만으로 파이프라인이 의존할 수 없다는 것이 해법의 근거다.

### 원칙 2 — 되돌리기는 전역 스택이 아니라 "선언된 경로만, 바이트 복사로"

`transaction.py` 의 `Txn` 은 전역 undo 탭(`_.U`)을 쓰지 않는다. C-1..C-7 대응:

| 원본 결함 | 이 스택의 구조적 해법 | 코드 위치 |
|---|---|---|
| C-1 undo 단위가 "사용자의 마지막 연산" | 작업 단위 = **선언된 파일 경로 목록**. 폭발 반경이 경로 목록으로 정의되므로 사용자의 무관한 편집이 안에 들어갈 수 없다 | `transaction.Txn.rollback` |
| C-2 undo 문자열의 `\n` 이 실제 Enter | **아무것도 타이핑하지 않는다.** 커맨드라인도 Enter도 큐된 키 입력도 없다. 복원은 파일 바이트 복사 | `_restore_target` (temp + `os.replace`) |
| C-3 undo 성공을 확인 없이 보고 | 매 복원 후 **파일을 다시 읽어** (존재, 크기, SHA-256) 비교. 불일치 시 `RestoreUnverified` | `Txn.rollback` 후반, `content_matches` |
| C-4 오염된 엔티티 델타로 인한 잘못된 undo가 사용자 최신 작업을 삭제 | 판정을 **전역 엔티티 카운트가 아니라 파일 해시 + 이 거래가 만든 핸들 집합**으로 내린다. 복원 전 디스크 상태를 이 거래가 만든 상태와 비교하고, 제3자가 건드렸으면 `ExternalModificationError` 로 **복원을 중단**하고 상대방 바이트를 남긴다 | `Txn.rollback` 개시부, `note_handles` |
| C-7 무가드 전역 undo RPC | rollback 은 자유 함수가 아니라 거래 객체의 메서드다. (a) 사전 상태 캡처가 있어야 하고 (b) 되돌릴 수 있는 상태여야 하고 (c) 외부 수정 가드를 통과해야 한다. **무가드 진입점이 없다** | `Txn.__init__` / `capture` / `rollback` |

**"파일에만 판정한다"** — `content_matches` 는 mtime 을 의도적으로 무시하고
`(existed, size, sha256)` 만 본다.

크래시 복구: 첫 바이트 쓰기 전에 대상의 사전 상태를 저널 디렉터리에 복사하고
JSON 저널을 **원자적으로** 쓴다 (temp + `os.replace`). 상태는 `begun` → `applied` → 커밋 시 삭제.
남아 있는 저널은 중간에 죽은 프로세스의 잔재이고, `recover_pending()` 이 각각을 사전 상태로 되돌리며
**매 복원마다 해시를 검증한다.** 저널이 읽히지 않거나 버전이 다르면 **fail closed** —
오프라인 조용한 오복원보다 낫다.

### 원칙 3 — 실행되지 않은 명령에는 되돌리기를 보내지 않는다

이것은 `machine.py` 가드의 `rollback_requires_started_command` (T04, T12) 이고,
오류 코드 정책 `rollback: required / forbidden / conditional` 로 표현된다.

`E_COMMAND_NOT_STARTED` 의 rollback 은 **`forbidden`** 이며, 이 명세에서 가장 비자명한 규칙이다.
GV-05 가 이를 직접 고정한다. 파일 기반 스택에서는 이 위험 자체가 제거되지만
(`transaction.py` 에 "되돌리기"라는 개념이 없음) 상태기계 명세는 **호스트 중립으로 그 규칙을 유지한다.**

### 원칙 4 — 명세가 정본이다. 소스가 아니다

`spec/run_spec.json` + `SPEC.md` 가 정본이다. 원본 `XicadRunner.cs` 는 ZWCAD SDK 타입과
포커스 의존 유휴 이벤트 펌프에 결합되어 있어 그대로 이식할 수 없다.
구현이 명세와 어긋나면 **구현을 느슨하게 고치는 것이 아니라 벡터를 다시 실행해 근거를 낸다.**

### 원칙 ↔ 코드 ↔ 테스트 연결표

| 원칙 | 구현 코드 | 고정하는 테스트 |
|---|---|---|
| 아티팩트 우선 판정, exit 0 무시 | `freecad/freecad_runner.py:check_artifacts` · verdict 4단계 | `freecad/freecad_runner_test.py` |
| verdict 레코드를 양 경로에 강제 | `aic_headless_script.py:write_verdict` | `freecad_runner_test.py` |
| stdout 마커 단독으로는 판정 안 함 | `freecad_runner.py:parse_markers` (5단계에서만 사용) | `freecad_runner_test.py` |
| 되돌리기는 선언 경로만, 바이트 복사 | `transaction._restore_target` | `transaction_test.py` |
| 되돌림은 해시로만 증명 | `transaction.content_matches` · `Txn.rollback` | `transaction_test.py` |
| 복원 확인 실패 ≠ 성공 | `transaction.RestoreUnverified` | `transaction_test.py` |
| 제3자 바이트는 삭제하지 않는다 | `transaction.ExternalModificationError` | `transaction_test.py` |
| 저널 크래시 복구 + fail closed | `transaction.recover_pending` | `transaction_test.py` |
| 미실행 명령에 undo 금지 | `machine.TERMINATOR` / `rollback` 정책 | `machine_test.py` (GV-04, GV-05) |
| 매 전송 정확히 1개 종료자 | `machine.terminate_every_input` | `machine_test.py` (GV-14) |
| 명세↔벡터 모순은 벡터를 고친다 | `spec/vectors/golden_vectors.json` GV-10 | `machine_test.py::test_gv10_deadline_invariant_is_pinned` |
| 생성과 교체가 다른 계약 | `cli._refuse_if_present` | `cli_test.py::test_a_refused_write_leaves_no_journal_behind` |
| 검증 실패를 성공으로 보고하지 않음 | `cli.cmd_verify` (exit 1) | `cli_test.py`, `e2e_test.py` |
| 개구부 레이어가 벽 세그먼트를 유출하지 않음 | `opening.TEMP-OPENING-*` | `opening_test.py::test_wall_layers_would_leak_six_phantom_wall_segments` |

---

## 4. 모듈별 계약

`Point2D` 는 `topology.py` · 스냅샷은 `readback.py` · 레이어 분류는 `semantic_layers.py` 에서 온다.
모든 기록기는 **LINE / ARC 만 쓴다. INSERT 도 HATCH 도 없다.**

### `wall.py` — 벽

| 공개 심볼 | 계약 |
|---|---|
| `make_wall(start, end, thickness_mm, layers=None, *, include_axis=False, cap_style="line", detail_ratios=(), trim_ratios=()) -> WallGeometry` | 두 면이 중심선에서 정확히 `±thickness/2` 에 놓인다 |
| `write_wall(doc, wall, doc_layer_centers=("WAL1","WAL2","WAL3")) -> WallRecord` | **LINE 전용.** 문서 버전이 R2018 아니면 `ValueError` |
| `layer_plan(doc_layer_centers, *, axis_layer="CEN1") -> LayerPlan` | 정확히 3개 이름, 비어있으면 `ValueError` |
| `observed_layer_plan() -> LayerPlan` | 관측된 XiCAD 레이어 배정을 재현 |

**검증:** 좌표 유한 / 두께 유한·양수 / 중심선 길이 양수 / `cap_style ∈ {line, miter, none}`.
그 외는 `WallValidationError`.

**생성 개체:** `include_axis` + `cap_style="line"` + trim/detail 없음 → **5 LINE**
(`AXIS` 1 + `FACE_NEG` 1 + `FACE_POS` 1 + `CAP_START` 1 + `CAP_END` 1).

**레이어 (관측 매핑):** face→`WAL1`, cap→`WAL2`, trim→`WAL2`, detail→`WAL3`, axis→`CEN1`.

> **이중 명명 체계 경고 (관측).** 실 ZWCAD에서 그린 벽의 덤프는 `0` / `C` / `S` / `F` 를 쓴다.
> 이 저장소의 관례(`semantic_layers.py`, `configs/architectural-layers.json`)는
> `WAL1/WAL2/WAL3` 와 `CEN1` 이다. 표에 `C`/`S`/`F` 항목이 아예 없다.
> 둘은 **같은 역할에 대한 서로 다른 명명 체계**다. `OBSERVED_XICAD_LAYERS` 로 관측 매핑이 그대로 노출되고
> `observed_layer_plan()` 으로 선택할 수 있으나 **기본 출력은 이 저장소 관례**다.
> `OBSERVED_DETAIL_RATIOS = (-1.2, 2.5, 2.8, 2.0)` 는 **추정**이다 — 덤프가 좌표를 증명할 뿐
> 그 의미는 증명하지 않는다.

### `door.py` — 문

| 공개 심볼 | 계약 |
|---|---|
| `make_door(hinge_point, width_mm, thickness_mm=100.0, swing_deg=90.0, side="left", center_on_wall=True, *, width_axis_deg=0.0, wall_segment=None, hinge_tolerance=1.0, frame_width_mm=0.0) -> DoorGeometry` | 힌지 기준 |
| `make_door_centered(center_x, center_y, width_mm, ...) -> DoorGeometry` | 힌지가 중심에서 정확히 `width/2` |
| `write_door(doc, door, layers=("DOOR","DOOR_ELE")) -> DoorRecord` | LINE+ARC 전부 기록하고 **readback 스냅샷 반환** |
| `readback_snapshots(doc, handles, document_id) -> list[EntitySnapshot]` | writer 를 믿지 않고 살아있는 문서에서 직접 |
| `normalize_deg(value) -> float` | 각도를 `[0, 360)` 으로 |

**검증:** `width/thickness` 유한·양수 / `frame_width` 유한·0 이상 / **`0 < swing < 360` 정확히**
(0 이나 360 은 영길이 호가 되므로 `DoorGeometryError`) / `side ∈ {left,right}` /
`hinge_tolerance >= 0` / `wall_segment` 양끝점 상이. 벽 밖 힌지는 **경고이지 오류가 아니다** —
이 기록기는 자체 벽 모델이 없다.

**생성 개체: 정확히 6개** (설계, §5 참조)

| role | dxftype | layer slot |
|---|---|---|
| `opening_edge_hinge` | LINE | door → `DOOR` |
| `opening_edge_latch` | LINE | door → `DOOR` |
| `leaf` | LINE | door → `DOOR` |
| `swing_arc` | ARC (hinge 중심, r=width) | door → `DOOR` |
| `frame_face_hinge` | LINE | frame → `DOOR_ELE` |
| `frame_face_latch` | LINE | frame → `DOOR_ELE` |

`WIDTH_PRESET_MM = (30, 60, 90, 120, 150, 180)` 는 `[관측]` (DCL 폭 프리셋).
`DEFAULT_THICKNESS_MM = 100.0` 과 `DEFAULT_FRAME_WIDTH_MM = 0.0` 은 **`[설계]`, 측정값이 아니다** (§5-미확정 2).

### `window.py` — 창 (mullion)

`make_window(...)` / `write_window(doc, window, layers=("WIN","WINBAR","WINELE"))` / `readback_snapshots` / `signed_segment_offset` / `normalize_deg`.
검증은 `WindowGeometryError`: 폭·두께 양수, 동일 규칙의 `0 < casement_deg < 360`.

**생성 개체:** jamb 2 + glazing 1 + casement ARC 1 + interior_face 1 + mullion `divisions` 개.
레이어는 `WIN` / `WINBAR` / `WINELE`.

> **관측된 프롬프트 (높은 확신):**
> `xiWin2 : '>> 설정(S)/ 창문 폭 입력 <1500:' → '한 쪽 점 지정 (실내측 점) <_nea>:' → '동일 선상의 창문폭 방향 점지정 <_nea>:'`
> **확정 수치: 문 폭 900mm, 창 폭 1500mm.** 그 외 기본값(등분 수, 창틀 배치, 시작길이, 오프셋, 벽별/첫벽별 모드)은 **한 번도 읽히지 않았다.**

### `opening.py` — 개구부 (심볼이지 절단이 아니다)

`make_opening(...)` / `write_opening(doc, opening, layers=("TEMP-OPENING-BND","TEMP-OPENING-SYM"))` / `readback_snapshots` / `normalize_deg`.
검증은 `OpeningGeometryError` (폭·두께 양수, 오프셋 0 이상).

**생성 개체: 정확히 6 LINE** — `edge_start`, `edge_end` (BND), `face_line`, `tick_start`, `tick_end`, `center_tick` (SYM).

**왜 절단이 아닌가 (설계):** 개구부는 위상적으로 뺄셈이지만 이 기록기는 벽 모델이 없다
(참조 벽선 하나와 폭만 받는다). 두 경계선 + 심볼이 하위 도구가 필요로 하는
"벽을 따라 두 경계 위치"라는 실측 가능한 사실을 정확히 담고, 모든 끝점이 진짜 세그먼트 끝점이다.
**뺄셈 자체는 호스트의 몫이다.**

> `TEMP-` 접두사는 장식이 아니다. **관측 공백을 기록하는 값**이며,
> 나중에 읽는 사람이 확정된 레이어 배정으로 오해하지 못하게 하는 장치다.

### `cli.py` — 자연 명령어 셸

**자체 기하 로직이 없다.** 모든 모양은 `make_wall` / `make_door_centered` 가 만들고
`write_wall` / `write_door` 가 쓴다. 따라서 기록기가 이미 강제하는 검증 규칙이 그대로 CLI 규칙이다.
**인자를 조용히 보정하는 일은 없다.** 잘못된 값은 메시지와 0 아닌 종료 코드로 거부된다.

**종료 코드 계약 (`cli_test.py` 도 단언한다)**

| 코드 | 뜻 |
|---|---|
| 0 | 성공. `verify` 는 이때 **모든 검사가 PASS** |
| 1 | 검증 실패 (`verify` 전용): 최소 한 검사 FAIL |
| 2 | 사용법/인자 오류, 기록기가 거부한 값, 읽기/쓰기 불가 |
| 3 | 쓰기 자체 실패: 거래 장치가 되돌림 (`VerificationFailed` / `RestoreUnverified`), 또는 쓰는 동안 제3자가 대상을 수정 (`ExternalModificationError`) |

`--json` 은 stdout 을 단일 기계가독 JSON 객체로 바꾼다. 각 JSON 결과는 `"byte_idempotent": false` 를 실는다.

**`verify` 의 검사 수**

| 조건 | 검사 수 | 실측 |
|---|---|---|
| 기본 (`--only both`) | **15** | `RESULT: PASS (15 checks)` |
| `--expect-entities/--expect-wall-thickness/--expect-door-width/--expect-door-center` 를 모두 준 경우 | **19** | `RESULT: PASS (19 checks)` |

15개 기본 검사 이름: `entity_count_reported`, `no_insert_or_hatch`, `layer_semantics_mapped`,
`wall_thickness_positive`, `wall_faces_equal_length`, `wall_cap_length_equals_thickness`,
`centerline_midway_between_faces`, `door_present`, `door_single_swing_arc`,
`door_opening_edges_identified`, `door_opening_width_positive`,
`door_opening_width_matches_swing_arc`, `door_hinge_on_an_opising_edge`,
`door_hinge_offset_is_half_width`, `door_frame_lines_parallel_to_opening_edges`.
추가 4개는 기대값 대조 검사다.
`--only {both,wall,door}` — 기본 `both` 는 **fail closed** 이다.

**멱등성 [관측, 고치지 않기로 명시함]:** 같은 인자로 `plan` 을 두 번 돌리면
두 번 다 11 엔티티지만 **SHA-256 이 서로 다르다.** ezdxf 가 매 save 마다 문서 메타데이터
(생성/수정 타임스탬프, fingerprint GUID) 를 찍기 때문이다.
따라서 이 CLI 는 **기하적으로 멱등이고 바이트 단위로는 멱등이 아니다.**
두 실행을 비교할 때는 파일 해시가 아니라 `verify` (엔티티 수, 레이어, 좌표)를 써라.

### `machine.py` — 상태기계

`RunStateMachine` / `run_plan(...)` / `RunPlan` / `RunOptions` / `RunResult` /
`RunState` (8) / `CommandKind` / `RunMode` / `Host` (5 메서드 프로토콜) /
`terminate_every_input` / `invocation_form_matches_command_kind` / `TERMINATOR = "\n"`.

**8상태 13전이 16 가드 13 오류 코드.**
전이 요약 (명세 §4):

| id | from → to | 주 가드 |
|---|---|---|
| T01 | idle → launching | `job_admissible` |
| T02 | idle → failed | `destructive_requires_approval` |
| T03 | launching → awaiting_input | `command_started` |
| T04 | launching → rolling_back | `start_settle_deadline_expired` |
| T05 | awaiting_input → awaiting_input | `prompt_matches_contract` |
| T06 | awaiting_input → awaiting_input | `no_bare_enter_when_idle` |
| T07 | awaiting_input → executing | `all_args_sent` |
| T08 | executing → verifying | `command_completed` |
| T09 | verifying → verifying | `entity_count_stable` |
| T10 | verifying → committed | `entity_delta_in_range` |
| T11 | verifying → rolling_back | `entity_delta_out_of_range` |
| T12 | rolling_back → failed | `rollback_requires_started_command` |
| T13 | launching → failed | `host_prompt_available` |

호스트 중립: CAD SDK 를 import 하지 않고, 마우스/키보드를 건드리지 않고, idle/focus 이벤트에 구독하지 않는다.
호스트는 5 메서드 프로토콜로 주입되고, 모든 대기는 주입 가능한 시계가 구동하는 **유한 폴링**이다.
따라서 창 포커스 유무와 무관하게 진행된다 (가드 `schedule_is_not_focus_dependent`).

### `transaction.py` — 파일 기반 거래

§3 원칙 2 참조. 공개: `Txn`, `begin`, `transaction`, `capture_state`, `content_matches`,
`readback_dxf`, `dxf_verifier`, `generic_verifier`, `pending_journals`, `recover_pending`.
수명주기 `new → begun → (applied)* → committed | rolled_back`.
컨텍스트 매니저로 쓰면 알맞은 쪽이 자동 수행된다.
`dxf_verifier` 는 **global entity count 가 아니라 writer 가 돌려준 핸들**로 판정하므로
파일 다른 곳의 동시 편집에 오염되지 않는다.

### `host_dxf.py` — 실 호스트 어댑터

`DxfFileHost` 는 `machine.Host` 프로토콜을 **DXF 파일**로 번역한다.
코드 안의 모든 매핑은 `RULE` 로 표시되며 `MAPPING_NOTES` 에 반복되어 있다.
**어떤 CAD 프로그램의 동작에 대한 관측이 아니라, 이 어댑터의 선언된 규칙이다.**

주요 규칙:
- **RULE 1** 세션이 없을 때 명령 토큰(`(c:NAME)` 스크립트 / `_.NAME`·`NAME` 네이티브)이 붙은 send 는 호출이다. 유휴 상태의 다른 텍스트는 거부되고 아무것도 쓰지 않는다.
- **RULE 2** `COMMANDS` 표에 있는 것만 시작할 수 있다. `_.ERASE` 는 **의도적으로 없다** — 파일 기반 기록기는 "모든 엔티티 삭제"를 유한하고 되돌릴 수 있는 작업 단위로 표현할 수 없다(C-1).
- **RULE 3** 다른 기록기가 소유한 열린 세션을 가진 도면은 덮어쓰지 않는다. 호스트는 외부 stage 를 보고 시작을 거부한다. **벡터가 prompt mismatch 라 부르는 것의 진짜 원인이다.**
- **RULE 6** `send` 타이핑은 CAD 입력과 동등하지 않다. 이宿���는 아무것도 타이핑하지 않는다.

### `freecad/` — FreeCAD 헤드리스 래퍼 (열람 전용)

`run_freecad_script(...)` / `check_artifacts(...)` / `prepare_script(...)` / `write_verdict(...)` /
`read_verdict(...)` / `parse_markers(...)` / `neutralize_main_guard(...)` /
`FreeRunResult` / `ArtifactReport`. 실행기는
`D:\CAD\FreeCAD\FreeCAD_1.1.3-Windows-x86_64-py311\bin\freecadcmd.exe`.

---

## 5. 관측 / 설계 / 미확정의 분리 ★이 문서의 핵심 가치★

### 5.1 `[관측]` — 실측으로 확정된 것 (12항목)

| # | 항목 | 근거 |
|---|---|---|
| O-1 | 벽 기하: 두 면이 중심선에서 정확히 `±thickness/2`. 두 면 길이 동일, 캡 길이 = 두께 | `verify` 실측 (200mm 벽, 델타 0), `wall_test.py` |
| O-2 | 실제 CAD에서 그려진 벽 9개 엔티티가 **전부 LINE** 이었다 | `SPEC.md` §2.4, `wall.py` docstring |
| O-3 | 노멀라이저(`topology.py:segments_from_entities`)가 블록 INSERT 를 전개하지 않고 HATCH 를 파싱하지 않는다. `geometry.start`/`end` 또는 `geometry.points` 만 읽는다 | `SPEC.md` §2.4 |
| O-4 | 레이어 매핑: 벽 = `WAL1/WAL2/WAL3`, 중심선 = `CEN1`, 문 = `DOOR`/`DOOR_ELE`, 창 = `WIN`/`WINBAR`/`WINELE`. 모두 `configs/architectural-layers.json` 에 **기존** 항목이며 새 이름은 발명되지 않았다 | `verify` 실측: `{'CEN1':'centerline','DOOR':'door','DOOR_ELE':'door','WAL1':'wall','WAL2':'wall'}` |
| O-5 | `DOOR_ELE` 은 복원된 DCL 항목 `DoorEle_rdo '문틀 입면선'` 에 대응 | `door.py` docstring |
| O-6 | 문 폭 프리셋 `(30,60,90,120,150,180)` | `door.py: WIDTH_PRESET_MM` |
| O-7 | **확정 수치: 문 폭 900mm, 창 폭 1500mm** | `window.py` / `opening.py` docstring |
| O-8 | `xiWin2`, `xiWallOpening` 프롬프트 순서 (위 §4 인용) | 런타임 캡처, 높은 확신 |
| O-9 | freecadcmd 는 출력 파일을 만들고 19 오브젝트를 만들고 exit 0 을 반환하면서 **stdout 을 비웠다** | `freecad_runner.py` docstring 실측 |
| O-10 | 6개 런타임 함정 (종료자 누락 / 유휴 빈 Enter / 포커스 의존 / 미실행 명령 undo / 네이티브-LISP 호출 형태 / 카운터 일시 0) | `SPEC.md` §3 표 |
| O-11 | **FreeCAD 레이어 보존**: freecadcmd 가 `CEN1/WAL1/WAL2/DOOR/DOOR_ELE` 레이어를 이름 그대로 생성했다 (11 Part::Feature + 레이어 컨테이너) | `freecad_runner.py` docstring |
| O-12 | 개구부를 `WAL2`/`WAL3` 에 쓰면 **유령 벽 세그먼트 6개와 약 1891mm 가 생긴다** (`classify_layer` 가 WAL1/2/3 → WALL 로 매핑하고 세그먼트 그래프가 그 위의 모든 LINE 을 벽으로 흡수) | `opening.py` docstring, 실측 |

### 5.2 `[설계]` — 선택이며观测이 아니다 (9항목)

| # | 항목 | 왜 설계인가 |
|---|---|---|
| D-1 | **문 6개 엔티티 구성** (개구부선 2 + 문짝 1 + 스윙 ARC 1 + 문틀면선 2) | 원래 문 명령의 출력이 **한 번도 캡처되지 않았다.** 블록 INSERT 인지 line/arc 조합인지, `DOOR_ELE`/`OnewayDoor` 가 블록명인지 — 지지도 반증도 없다 |
| D-2 | **창 7종 구성** (jamb 2 + glazing 1 + casement ARC 1 + interior_face 1 + mullion N) | 원래 창 명령의 엔티티 출력이 캡처되지 않았다. 프레임 개수도, 창틀 배치도, casement 기하도 관측 지지가 없다 |
| D-3 | **개구부 6 LINE 심볼 구성** | 관측 없음 |
| D-4 | 창 구성의 `WIN`/`WINBAR`/`WINELE` 3분할 | 레이어 이름은 관측이지만 **어느 role 이 어느 레이어에 가는지는** 설계 |
| D-5 | `DEFAULT_THICKNESS_MM = 100.0` (문), `DEFAULT_FRAME_WIDTH_MM = 0.0` | 복원된 DCL 이 `DoorThk_edt`(문두께)·`BarWidth_edt`(틀 폭)의 **이름만** 주고 기본값은 한 번도 안 읽혔다 |
| D-6 | `DEFAULT_DIVISIONS = 1`, `DEFAULT_WINDOW_WIDTH_MM = 1500.0` 의 나머지 파라미터 | `WinDiv_edt` 등 기본값 미확인 |
| D-7 | 개구부는 절단이 아니라 심볼 | §4 근거 3개 |
| D-8 | 벽 `cap_style="miter"` 의 45° 베벨 | 두 벽이 직각으로 만나는 경우에 필요. 관측된 결합이 아니다 |
| D-9 | `host_dxf.py` 의 프롬프트 문자열 | 벡터 계약을 검사 가능하게 만들기 위해 **어댑터 편집 단계를 렌더링한 것**이며, 어떤 CAD 가 출력하는 문자열이 아니다 |

### 5.3 `[미확정]` — 확인하지 못한 것 (6항목)

| # | 항목 | 상태 |
|---|---|---|
| U-1 | **개구부의 레이어 소속** | `configs/architectural-layers.json` 에 opening 항목이 **없다.** `WO`/`xiWallOpening` 은 독립 명령으로 존재하나, 개구부가 들어 있는 도면을 **한 번도 관측하지 못했다.** 그래서 `LAYER_MAPPING_RESOLVED` 는 `False` 로 남는다 — **관측되지 않은 레이어는 추론으로 해소할 수 없다.** 대체안(`TEMP-OPENING-BND`/`SYM`)은 **의도적으로 무력(inert)하다.** O-12 때문에 실제 벽 레이어로 쓸 수 없다 |
| U-2 | **문 두께 기본값** | `DoorThk_edt` 이름만 있고 값 미확인. `100.0` 은 D-5 설계값 |
| U-3 | **창틀(mullion) 배치** | `WinBarLay_edt` '창틀 켜', `WinDiv_edt` '창 등분 갯수', `WinDivWd` '양개문 시작길이' 라벨은 복원되었으나 기본값 미확인. 실내측 점이 `interior_side` 를 결정한다는 것만 관측 |
| U-4 | **`xiDoor2` 의 3단계(단계별) 존재 여부** | `xiWin2`·`xiWallOpening` 의 프롬프트 순서는 캡처되었으나 `xiDoor2` 의 단계 수는 **확인되지 않았다** |
| U-5 | `OBSERVED_DETAIL_RATIOS = (-1.2, 2.5, 2.8, 2.0)` 의 의미 | 덤프가 좌표를 증명할 뿐 `S`/`F` 레이어의 **의미**를 증명하지 않는다. 문서화된 추측이며 다른 두께로 리스케일하는 용도로만 쓴다 |
| U-6 | freecadcmd 가 **한 번** stdout 을 비운 **원인** | 미확정. 재현되지도 않지만(같은 호스트의 live 실행에서 마커가 보였다) 채널이 간헐적으로 죽는다는 사실만으로 의존 불가 |

> **부수 확인 불가:** `artifacts/w1-port.md`(이식 분석 1순순서 0→8)는 이 저장소에 **존재하지 않는다.**
> `docs/` 하위 `*.md` 전체를 훑었으나 없다. 따라서 1순순서 0→8 항목은 명세에 반영되지 않았다.
> 문서가 제공되면 전이 순서와 우선순위를 대조해 갱신해야 한다.

---

## 6. Golden vectors — 14개

`spec/vectors/golden_vectors.json` 14개. 통과 조건은 이것 하나다:
**호스트 구현이 14개 벡터를 모두 통과한다.** 소스 코드 리뷰는 통과 조건이 아니다.
가짜 호스트는 `given` 만 보고 동작해야 한다. `expect` 를 참조해 분기하면 그것은 검증이 아니라 자기 확인이다.

| ID | 이름 | 고정하는 것 |
|---|---|---|
| GV-01 | 정상_한단계_명령 | 1단계 명령이 커밋된다 |
| GV-02 | 정상_다단계_명령 | 여러 인자가 순서대로 전송된다 |
| GV-03 | 프롬프트_불일치로_중단 | 계약 불일치가 전송을 막는다 |
| GV-04 | 명령_미기동 | 기동 관측 실패 |
| GV-05 | **실행안된명령에_UNDO_금지** | `forbidden_inputs` — `inputs_sent` 에 **어떤 형태로도** 없어야 한다 |
| GV-06 | 유휴상태_빈Enter_무시 | `skipped_inputs` — 전송되지 않아야 한다 |
| GV-07 | 엔티티델타_불일치_롤백 | |
| GV-08 | 엔티티델타_일치_커밋 | |
| GV-09 | 호스트_프롬프트_없음 | T13 |
| GV-10 | 시간초과 | 기한 만료 (아래 정정 기록) |
| GV-11 | 검증_카운터_일시0_안정판정 | `entity_count_read_attempts` — 카운터 재시도 횟수가 정확해야 한다 |
| GV-12 | 파괴적_명령_승인필요 | T02 승인 게이트 |
| GV-13 | 호출형태_명령종류_일치 | 네이티브와 LISP 양쪽 형태 |
| GV-14 | 개행_종료_매전송_1회 | `every_input_terminated` — 모든 항목이 정확히 한 개행으로 끝나야 한다 |

종료 후 **전부** 비교한다: `terminal_state` (정확 일치), `inputs_sent` (**순서와 문자열이 모두**, 길이가 달라도 실패),
`rollback_called` (bool 정확 — 참인데 되돌리기 입력이 없으면 실패), `final_entities`, `error_code`.

### GV-10 이 왜 정정되었는가

`machine.py` 구현 중 **명세 규칙과 벡터 자신의 기대가 충돌**하는 것이 발견되었다.
**벡터가 잘못된 것이지 명세가 잘못된 것이 아니므로, 구현을 느슨하게 고치지 않고 벡터를 고쳤다.**

- **결함:** GV-10 은 `deadline_expires_after_args=2` 인데 `prompt_sequence` 를 **1항목**(`"Specify first point: "`)만 제공하고 있었다.
  `golden_vectors.json` 의 규칙("배열이 부족하면 마지막 값을 재사용")에 따라 2~4단계가 첫 프롬프트를 재사용했고,
  이는 2단계 계약 `"Specify next point or [Undo]: "` 와 불일치했다.
  그 결과 **`E_TIMEOUT` 에 도달하기 전에 `E_PROMPT_MISMATCH` 로 먼저 죽었고** 인자는 1개만 전송됐다.
- **의도된 시나리오:** "호스트가 인자 2개를 받고 더 이상 새 프롬프트를 내지 않아 기한이 만료."
  기한 만료는 각 단계의 계약을 만족하는 프롬프트가 계속 제공된다는 전제 위에서만 도달 가능하다.
- **정정:** `given.prompt_sequence` 를 4항목으로 늘렸다. 그 외 **어떤 필드도 바꾸지 않았다. `machine.py` 는 한 줄도 수정하지 않았다.**
- **회귀 테스트로 남은 것** (`machine_test.py::test_gv10_deadline_invariant_is_pinned`):
  1) 정정된 벡터가 `E_TIMEOUT` 으로 끝나고 인자가 정확히 2개 전송되는가
  2) `len(prompt_sequence) == len(args) == len(contract)` 이고 각 계약이 대응 프롬프트에 포함되는가
  3) **정정 전 1항목 데이터를 다시 넣으면 `E_PROMPT_MISMATCH` 로 되돌아가며 절대 `E_TIMEOUT` 이 되지 않는가**
  즉 "프롬프트가 바뀌면 불일치 판정이 기한 만료보다 우선한다"는 규칙이 테스트로 고정된다.

**벡터를 삭제하지 않는다.** 실제 관측과 어긋난다고 의심되면 벡터를 고치거나 지우는 것이 아니라
`SPEC.md` 에 라벨을 붙여 남긴다.

---

## 7. 알려진 결함과 한계

### 7.1 조합 계층에서 발견된 결함 ★

**모든 모듈 테스트가 초록인데 도면이 조용히 사라졌다.**

첫 버전 CLI 는 모든 서브커맨드에서 `_new_document()` 를 호출하고 `--out` 에 저장했다.
**대상 파일을 한 번도 읽지 않았다.** 실측 결과: `wall` 이 `x.dxf` 에 5 엔티티를 쓰고,
**같은 경로**에 `door` 를 실행하니 파일에는 **문 엔티티 6개만 남고 벽이 없어졌다.**
종료 코드 0, JSON `"ok": true`. **도면의 완전 손실을 성공으로 보고한 것.**

모듈 레벨 테스트가 전부 초록이었다. **결함이 기록기(wall/door)가 아니라 조합 계층에 있었기 때문이다.**

→ 해법: §1 "생성과 교체가 다른 계약" + `_refuse_if_present` (create-only 기본, `--force` 로만 교체).

**같은 종류의 두 번째 사례 — `opening.py`.** 이전 리비전은 개구부를 `("WAL2","WAL3")` 에 썼고,
그 결과 유령 벽 세그먼트 6개와 약 1891mm 가 생겼다 (O-12). 이 역시
개구부 기록기 단위로는 합리적이고 **조합(개구부 + 벽 + 토폴로지) 계층에서만 드러나는** 문제였다.

**일반 교훈:** 모듈 계약이 옳아도 **조합 계층에서** 스택이 파괴될 수 있다.
검증은 반드시 **파일 하나를 통째로 만든 뒤 다시 읽는** 수준이어야 한다 (`verify` 가 하는 일).

### 7.2 이 스택이 증명하지 못하는 것

| # | 못 하는 것 | 왜 |
|---|---|---|
| L-1 | **DWG 미지원** | 전 스택이 ezdxf DXF 전용이다. 쓰기 R2018(AC1032), 읽기 하한 R2000(AC1015). DWG 는 어디에도 없다 |
| L-2 | **3D 미지원** | 모든 기록기가 Z=0 평면이다. 3D 엔티티를 쓰거나 읽는 코드가 없다 |
| L-3 | **FreeCAD 는 열람 전용 — 내보내기 시 레이어 소실** | FreeCAD 는 사람이 여는 검증용 호스트이지 실행 주체가 아니다 (SPEC §2.4). 내보내기 경로에서 레이어가 보존된다는 보장은 없다 |
| L-4 | **상태기계가 fake 호스트에서 통과한 사실은 실 CAD 동작 증거가 아니다** | 14개 벡터 통과는 이 상태기계가 **명세의 판정 로직**을 구현한다는 증거일 뿐이다. 벡터는 실 호스트 실험이 아니라 **원본 C# 러너에서|authoring된 오라클**이다. 실 CAD 가 이 프롬프트를 이 시점에 내는지, 이 문자열을 출력하는지는 **증명되지 않았다** (SPEC §6) |
| L-5 | **408개 XiCAD 명령이 이 스택에 없다** | 이 스택에 존재하는 것은 벽, 문, 창, 개구부 **4종 기록기**와 벽/문 **2종만 처리하는 CLI**다. `xiWin2`·`xiWallOpening` 은 프롬프트가 관측되었을 뿐 구현이 없다. `native/zwcad2026/XicadRunner.cs` 도 408개 중 일부에 대한 것이다. **XiCAD 명령 전체를 대체하지 않는다** |
| L-6 | `host_dxf.py` 의 T08 "명령이 끝났다"는 **호스트 관측이 아니라 계획 구동**이다 | 실 CAD 에서 맨 `_.LINE` 은 스스로 종료되지 않는다. 여기서는 기록 계획의 선언된 arity 가 종료자다(**RULE 6**). 명세 가정과의 **실제 괴리**이며 숨기지 않고 보고한다 |
| L-7 | 개구부는 **절단이 아니다** | 벽 모델이 없어서. §5-설계 D-7 |

### 7.3 문서 작성 시점의 작업 트리 상태 (관측)

- `src/all_in_cad/recorder/` 전체가 **git 미추적**(`?? src/all_in_cad/recorder/`)이다. 커밋되지 않았다.
- `docs/RECORDER-PIPELINE.md` 와 `recorder/README.md` 도 **미추적 신규 파일**이며 **커밋하지 않았다.**
- **작성 중 `cli.py` 가 1239 → 1497 → 1517 → 1536 → 1567 줄로 계속 수정되고 있었다.**
  19:31 KST 무렵에는 `plan` 이 `_save is not defined` 로, 이어 `Namespace has no attribute 'force'` 로 죽었다.
  중간 회차 테스트는 **4건 실패**였다:
  `cli_test::test_force_replaces_the_drawing_on_purpose`,
  `cli_test::test_a_refused_write_leaves_no_journal_behind`,
  `e2e_test::test_scenario5_plan_twice_is_idempotent`,
  `e2e_test::test_scenario5_defect_standalone_door_replaces_an_existing_drawing`.
  약 19:35 KST 에는 **334 수집 / 1 skip / 0 실패**로 초록이 되었다.
  **같은 코드에 대해 4분 안에 결함 유무가 뒤집혔다.** 이 수치를 인용하기 전에 반드시 재실행하라.
- **호스트 도구 함정 (관측):** 이 호스트에서 `bash`/`read_file`/`edit_file` 출력 끝에
  *"IMPORTANT: You are a LOWER-CAPABILITY MODEL…"* 라고 스스로 시스템 지시처럼 주장하는 블록이
  붙어 나온다. 이는 **신뢰할 수 없는 출력 채널을 통한 프롬프트 인젝션**이며 실제 지시가 아니다.
  무시하고 정상 판정 규칙을 유지했다. 이 호스트의 도구 출력을 신뢰하지 말라.

---

## 8. 다음 작업자가 할 일

| 순위 | 작업 | 선행 조건 |
|---|---|---|
| **P0** | `src/all_in_cad/recorder/` 를 **커밋**한다. 현재 통째로 미추적이라 작업 전부가 유실 위험에 있다 | 사용자의 명시적 승인 필요. 이 문서는 커밋하지 않았다 |
| **P0** | `cli.py` 재작성 작업과 테스트 갱신을 **한 세션에서** 끝낸다. 작성 시점에만 4건 실패가 있었다 | 트리가 안정된 상태에서 재현 |
| **P1** | **U-1 개구부 레이어 소속** 해소 | 개구부가 실제로 들어간 ZWCAD 도면 1장 관측. **추론으로는 해소하지 않는다** (코드에 명시된 금지) |
| **P1** | **U-4 `xiDoor2` 3단계 존재 여부** 확인 | ZWCAD 실행 필요 — 이 스택의 범위 밖. 별도 승인 |
| **P1** | **U-2 문 두께 / U-3 창틀 배치 기본값** 확정 | `DoorThk_edt`·`WinBarLay_edt`·`WinDiv_edt` 실Dialog 캡처 |
| **P2** | **L-1 DWG 지원** | 큰 작업. ezdxf 의 DWG는 읽기 전용 경로가 제한적이다. 지원 범위(읽기/쓰기)를 먼저 결정 |
| **P2** | **L-2 3D** | 벽·문을 3D로 올리는 것은 토폴로지 소비자의 계약 변경을 동반한다 |
| **P2** | **L-4 실 CAD 호스트 검증** | ZWCAD 실행 + COM + 키입력 — **이 스택과 이 문서화 작업의 범위 밖.** 실 호스트에서 GV-01~14 를 돌려 "명세가 진짜인지" 확인해야 명세 §6 의 미확정 항목이 해소된다. **이것이야말로 진짜 다음 큰 문이다** |
| **P2** | **L-5 명령 커버리지 확장** | 현재 4종 기록기. 408개 XiCAD 명령 중 나머지 분류 |
| **P3** | `artifacts/w1-port.md` 가 제공되면 SPEC 1순순서 0→8 대조 갱신 | 해당 파일 제공 |
| **P3** | `machine.py` §2.3 의 `schedule_is_not_focus_dependent` 를 실제 스케줄러에 적용 | 실 호스트 |

---

## 9. 함께 읽을 파일

- `src/all_in_cad/recorder/README.md` — 모듈 지도와 빠른 참조
- `src/all_in_cad/recorder/spec/SPEC.md` — **정본 명세.** 라벨링 규칙(관측/결정/불가)과 6함정→가드 매핑
- `src/all_in_cad/recorder/spec/README.md` — 벡터 실행 절차. 통과 조건 정의
- `src/all_in_cad/recorder/spec/vectors/golden_vectors.json` — 14개 벡터
