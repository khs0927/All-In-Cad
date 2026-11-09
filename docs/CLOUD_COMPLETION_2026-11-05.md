# Cloud completion — 2026-11-05

브랜치: `claude/cloud-work-completion-upgrade-beuecn` (기준: `main` @ `47461ef`, #17 병합 직후)

이 문서는 사용자가 자리를 비운 동안 클라우드(Linux 컨테이너, CAD 호스트 없음)에서 수행한 작업 기록이다.
Windows CAD 호스트·.NET SDK·자격증명이 필요한 항목은 실행하지 않았고, 실행한 것처럼 기록하지도 않았다.

## Inventory (미병합 브랜치 / PR)

열린 PR: 없음. `git branch -r --no-merged origin/main` 결과 4개 브랜치.

| 브랜치 | 판정 | 근거 |
|---|---|---|
| `agent/autocad-host-2027` | 건너뜀 (이미 병합) | khs0927/All-In-Cad#17 로 squash 병합됨. `main` 과의 차이는 `main` 쪽에만 있는 이후 커밋뿐. |
| `agent/detection-evidence-20261003` | 통합 (merge) | `tool_probes` 의 installation-detection 의미론과 테스트는 이미 khs0927/All-In-Cad#16 으로 `main` 에 있음. 새로 들어온 것은 `docs/audits/2026-10-03-upstream-pin-evidence.json` 뿐이며, 모든 pinned SHA 가 `upstream/upstream.lock.json` 과 일치함을 확인. |
| `codex/autocad2027-readonly-capabilities-20260918` | 부분 통합 | `scripts/windows/doctor.ps1` 의 .NET SDK 탐지(DOTNET_ROOT → 저장소 `.dotnet` → PATH, `--list-sdks` 성공 필요)와 `.gitignore` 의 `.dotnet/` 만 이식. 같은 커밋의 ODA 경로 hunk 는 경로 구분자(`\`)가 사라진 회귀라서 제외. 네이티브 C# 변경은 아래 행과 동일 내용. |
| `feat/read-only-host-adapters-current-main-20261003` | 보류 (호스트 필요) | 위 codex 브랜치의 네이티브 부분을 rebase 한 것(파일 내용 동일). `native/autocad2027/Plugin.cs`, `.csproj` 가 #17 의 1,078줄 AutoCAD 2027 워커와 정면 충돌하며, 214줄 read-only 어댑터는 #17 워커에 의해 사실상 대체됨. ZWCAD 2026 read-only 어댑터(247줄)는 충돌 없이 병합되지만, ZWCAD 작업은 `docs/ZWCAD-HOST-HANDOFF.md` 기준으로 동결 상태이고 이 컨테이너에서는 .NET SDK·ZRX.NET 어셈블리가 없어 컴파일조차 검증할 수 없어 병합하지 않음. |

## Changes on this branch

1. `merge:` detection-evidence 브랜치의 upstream pin/루트 라이선스 감사 JSON.
2. `feat(doctor):` .NET SDK 탐지 강화 (codex 브랜치 f664ee9 의 일부 이식).
3. `fix:` 모든 CLI 실행마다 출력되던 pydantic `Field name "schema" ... shadows an attribute` UserWarning 5건을
   `all_in_cad` 패키지 범위로만 정확히 필터링 (`src/all_in_cad/_warnings.py`). 필드명 `schema` 는 저장 포맷의 일부라 유지.
   → **대체됨:** main 에 병합된 `khs0927/All-In-Cad#19` 가 필드 alias 로 근본 원인을 고쳐, 필터는 제거함
   (`python -W error::UserWarning -c "import all_in_cad"` 통과).
4. `ci:` `core-ci`, `dxf-recorder-ci` 가 `claude/**` push 에서도 실행되도록 트리거 추가.

## Upgrades

- `pip list --outdated`: `pydantic_core 2.50.2 → 2.50.3` (pydantic 이 고정하는 전이 의존성, 하한만 있는 범위라 변경 불필요), `pip` 자체.
- `pip-audit`: **No known vulnerabilities found** (설치된 `[dev,headless]` 전체).
- 의존성 범위(`pydantic>=2.9,<3`, `ezdxf>=1.4`, `pytest>=8.3`, `ruff>=0.8`, `mypy>=1.10`)는 최신 버전을 이미 허용하므로 수정하지 않음.
- GitHub Actions 는 이미 SHA 고정(checkout v7.0.1, setup-python v7.0.0, setup-dotnet v6.0.0)이라 변경 없음.

## Open-source integration

추가 없음. `ezdxf` 는 이미 `dev`/`headless` extra 에 있음(#18). ROADMAP 의 남은 항목(공간 인덱스 가속, PDF 증거 수집)은
"실제 도면 벤치마크/정규화 검증 이후"로 명시돼 있어, 지금 shapely/rtree 등을 넣는 것은 문서와 어긋난다고 판단.

## Verification (Linux, CPython 3.13.18, `.venv`)

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev,headless]"
.venv/bin/ruff check src tests scripts ci          # All checks passed!
.venv/bin/pytest -o addopts="" -q                  # 112 passed, 1 skipped
.venv/bin/python -m pytest src/all_in_cad/recorder -o addopts="" -q --junitxml=ci-reports/recorder-junit.xml
                                                   # 844 passed, 4 skipped (FreeCAD 미설치 allowlist)
.venv/bin/python -m pytest ci/recorder_ci_gate_test.py -o addopts="" -q   # 20 passed
.venv/bin/python ci/recorder_ci_gate.py ci-reports/recorder-junit.xml
                                                   # collected=848 passed=844 failed=0 skipped=4 floor=848
.venv/bin/python scripts/upstream/check_pins.py    # OK (offline 구조 검증)
.venv/bin/pip-audit                                # No known vulnerabilities found
```

- `mypy src`: 117 errors / 29 files — 기존부터 있던 것(주로 recorder 테스트 모듈의 타입 표기, pydantic `schema` 필드).
  CI 에서 mypy 를 돌리지 않으며 이번 변경으로 늘어난 것은 없음. 별도 정리 과제로 남김.
- `scripts/upstream/check_pins.py --online`: 이 컨테이너의 egress proxy 가 GitHub REST API 를 HTTP 403 으로 거부해 실행 불가.
  주간 `upstream-audit` workflow 로 확인 필요.
- `.NET`: `dotnet` 미설치, `builds.dotnet.microsoft.com` 접속이 proxy 에서 거부되어 `native/shared`, `native/headless` 빌드 미검증 (CI `native-protocol` job 이 담당).
  `native-scaffold-policy` job 의 grep 단언 4개는 로컬에서 모두 통과.

### Real run

ezdxf 로 2실 평면 DXF(외벽/내벽 이중선, 문 호, 창, 텍스트)를 만들어 실제 CLI 로 색인:

```bash
all-in-cad index --root <tmp>/drawings --db <tmp>/index.sqlite --workdir <tmp>/work --compute-hash
# processed 1: lane=ezdxf entities=7 rooms=4 opening_hosts=2 semantic_nodes=15 semantic_edges=20 warnings=[] (exit 0)
# 두 번째 실행: processed 0, skipped 1, failures 0  (증분 색인 동작)
# SQLite 테이블: drawings, entities, refs, semantic_nodes, semantic_edges, rooms, opening_hosts,
#                room_adjacencies, annotation_bindings, extraction_runs
all-in-cad benchmark --workdir <tmp>/bench --entities 2000
# extracted_entities=2000 semantic_nodes=6000 semantic_edges=4000; generate 211ms / extract 276ms / graph 43ms
all-in-cad doctor   # ezdxf DETECTED, acadsharp/libredwg/oda NOT_DETECTED, 모두 execution_allowed=false
```

## Remaining (사용자 Windows CAD 호스트 / 자격증명 필요)

- `feat/read-only-host-adapters-current-main-20261003` 의 ZWCAD 2026 read-only 어댑터: ZWCAD 라이선스·ZRX.NET 어셈블리가 있는 호스트에서
  컴파일·NETLOAD 후 병합 여부 결정. AutoCAD 쪽은 #17 워커로 대체되었으므로 해당 브랜치를 그대로 병합하지 말 것.
- `docs/ROADMAP.md` Phase 1/2 의 라이브 호스트 항목 전부 (AutoCAD 2027 플러그인 로드, Named Pipe ping, PyRx 매트릭스, 네이티브 트랜잭션/readback).
- 실제 `Z:\` 도면 트리 인벤토리, 실도면 기반 정밀도/재현율 측정.
- `doctor.ps1` 변경은 PowerShell 이 없어 이 컨테이너에서 실행하지 못함 → Windows 에서 `scripts/windows/doctor.ps1` 1회 실행 확인 필요.
- upstream pin online 감사 (GitHub API 접근 가능한 환경/CI).

## Local Windows run (2026-11-05)

호스트: `DESKTOP-CMFD3O8`, Windows 10, AutoCAD 2027(26.0.0.0, PID 82684, 빈 `Drawing1.dwg`), .NET SDK 10.0.403(프로세스 환경변수 `DOTNET_ROOT`만 사용).

### 결과

| 항목 | 명령 | 결과 |
|---|---|---|
| doctor | `scripts/windows/doctor.ps1` | exit 0. `DOTNET_ROOT` 없으면 .NET SDK `False`, 있으면 `True`(10.0.403). 탐지 순서 동작 확인. `-Strict` 는 수정 전 3건 실패로 종료(ZWCAD, ODA, ACadSharp probe) |
| doctor ODA 오탐 | 같은 스크립트 | 이 PC는 `ProgramFiles=E:\Program files` 인데 ODA는 `C:\Program Files\ODA` 에 있어 못 찾음. `doctor.ps1`, `tool_probes.odafc_search_roots` 에 `SystemDrive` 루트 추가 후 둘 다 `ODAFileConverter 27.1.0` 탐지 |
| upstream pin 온라인 감사 | `python scripts/upstream/check_pins.py --online --json-output ...` | exit 0. 14개 pin 모두 upstream 에 존재. current 3, behind 11. 결과: `docs/audits/2026-11-05-upstream-pin-online.json`. pin 은 올리지 않음 |
| 플러그인 빌드 | `dotnet build native/autocad2027/AllInCad.AutoCAD2027.csproj -c Release -p:ACAD2027_DIR="C:\Program Files\Autodesk\AutoCAD 2027"` | 수정 전 실패, 수정 후 오류 0, 경고 3(WindowsBase 버전 충돌 MSB3277, 로드에는 영향 없었음) |
| 플러그인 로드 | 빈 세션에서 `(command "_.NETLOAD" "<scratch>/AllInCad.AutoCAD2027.dll")` | `Initialize ok`, `\\.\pipe\all-in-cad-acad-82684` 생성. `SECURELOAD=0` 이라 보안 프롬프트 없음 |
| Named Pipe | framing 스모크 클라이언트(`aic.native/1`) | `system.ping` pong, `host.context`(`host=autocad2027`, `sdk_bound=true`, 문서 revision/개수), `host.capabilities` 통과. `plan.execute` → `E_METHOD_NOT_FOUND`, `aic.native/2` → `E_PROTOCOL` |
| 네이티브 트랜잭션 | `host.draw_line` (0,0)→(1234.5,678.9) | handle `1F75`, 개체 0→1, revision 8053→8054 |
| 독립 readback | power-cad `cad_get`, `cad_snapshot` | `LINE`, 시작/끝점 일치, 길이 1408.86318. 화면 확인 |
| 정리 | power-cad `cad_delete` dry_run → 실행 | 개체 0개 복귀(power-cad, pipe 양쪽 확인) |
| Python | `ruff check src tests scripts ci`, `pytest tests`, `pytest src/all_in_cad/recorder` (Windows, CPython 3.12) | ruff 통과, core 117 passed, recorder 844 passed / 4 skipped (12분 15초) |

### 코드 수정

- `native/autocad2027` 은 #17 이후 한 번도 빌드될 수 없는 상태였다. `Plugin.cs` 가 쓰는 `PipeNames`, `WorkerDescriptor`, `WorkerDescriptorStore`, Newtonsoft 기반 요청 타입이 저장소에 없고 Newtonsoft 참조도 없었다.
  `HostProtocol.cs` 를 추가해 핸들러 코드를 그대로 두고 커밋된 `native/shared` 서버 위에 올렸다.
- 호스트 식별값이 `zwcad2026` / `ZWCAD` 로 나가던 것을 `autocad2027` / `AutoCAD` 로 수정. `document_id` 접두사도 `autocad:`.
- `host.context` 와 `SnapshotModel` 이 pipe 스레드에서 DB 를 읽던 것을 애플리케이션 스레드로 옮김.
- `AIC_AUTOCAD_SESSION_TOKEN` 이 설정되어 있으면 세션 토큰을 요구.

### 남은 항목

- `plan.execute`, `entity.read`, `document.snapshot`, `verification.capture` 는 미구현. 승인 토큰, 쓰기 lease, 멱등 키 없이 `host.draw_line`, `host.send_command` 가 열려 있어 `AGENTS.md` 규칙 2, 3 을 아직 충족하지 못한다. 그래서 ROADMAP 의 "Execute v1 primitives", "Capture native post-commit readback" 은 체크하지 않았다(이번 증거는 LINE 1종).
- revision 이 Handseed 값이라 삭제 후에도 8054 그대로였다. 생성만 반영하므로 `expected_revision` 충돌 검사로 쓸 수 없다. `document_id` 도 이름 기반이다.
- `host.send_command`, `xicad.*` 는 실행하지 않음. XiCAD 는 이 AutoCAD 에 로드되어 있지 않다.
- Autodesk Assistant MCP, `bimwright/dwg-mcp` 2027 shell, PyRx 26.0 매트릭스: 설치가 필요해 실행하지 않음.
- ZWCAD 2026 전체: ZWCAD 미설치, 프로세스 없음.
- `Z:\` 인벤토리와 실도면 정밀도/재현율: 이 PC에 `Z:` 드라이브 없음(C, D, E, G 만 존재).
- `native/autocad2027` 빌드는 CI 에 없다(AutoCAD SDK 필요). `acaddoc.autoload.lsp` 의 경로는 다른 PC 기준이고 실행하지 않음.
- 시험 빌드는 언로드할 수 없어 AutoCAD 를 닫을 때까지 PID 82684 에 로드된 채 pipe 를 연다. 영구 설치는 없음. 로그와 descriptor 는 `%LOCALAPPDATA%\All-In-Cad` 에 남고 descriptor 는 종료 시 삭제된다.

## Round 2 (2026-11-07)

목표: Windows CAD 호스트 검증을 소유자 self-hosted runner(`self-hosted, Windows, X64`)에서 반복 가능하게 만들기.

### 추가한 것

- `scripts/windows-live-smoke.ps1` — AutoCAD 2027 라이브 스모크. 실행마다 `artifacts/live-smoke/<runId>/live-smoke.json`
  (`all-in-cad/live-smoke/v1`: 버전, 단계별 passed/failed/skipped, 원본 응답 파일별 sha256)을 남긴다.
  - `-DryRun`: 탐지만(OS, `doctor.ps1 -Json`, AutoCAD 2027 설치/버전, 플러그인 빌드 sha256, acad.exe, worker descriptor). CAD 접속 없음.
  - 기본: 읽기 전용. Named Pipe `system.ping` / `host.capabilities` / `host.context`. 사용자 도면 경로는 sha256 으로만 기록.
  - `-Write`: COM 으로 `%TEMP%` 에 **새 도면**을 만들고, worker 의 활성 문서가 그 temp 도면임을 `host.context` 로 확인한 뒤에만
    `host.draw_line` 1회 → COM `HandleToObject` 로 독립 readback → temp 도면 닫기/삭제, 이전 활성 문서 복귀. 사용자 도면은 저장·닫기·수정하지 않는다.
- `scripts/windows/doctor.ps1 -Json <path>` — 기존 표 출력은 그대로, 같은 결과를 JSON 으로도 기록(추가 옵션).
- `src/all_in_cad/live_smoke.py` + `all-in-cad live-smoke ingest <evidence> [--apply]` — 증거 스키마 검증 →
  `docs/verification-ledger.json`(`all-in-cad/verification-ledger/v1`) 항목 평가 → ROADMAP 승격.
  readback 항목은 PS 단계 통과만으로는 부족하고, 요청(파이프)/실제(COM) 두 채널을 기존 `DrawingEvidence` + `cross_check_evidence`
  로 다시 비교해 `VerificationReport.passed` 여야 통과. 승격은 각 항목의 **가장 최근** ledger 기록이 passed 일 때만, 체크만 하고 해제는 하지 않는다(회귀는 보고).
  `--apply` 없으면 아무것도 쓰지 않는다.
- `docs/ROADMAP.md` Phase 2 에 live smoke 로만 체크되는 AutoCAD 항목 2개 추가(LINE 네이티브 트랜잭션, COM readback cross-check).
  "both hosts" 항목은 ZWCAD 생산자가 없으므로 자동 승격 대상에서 제외.
- `.github/workflows/windows-live-smoke.yml` — `workflow_dispatch` 전용, `runs-on: [self-hosted, Windows, X64]`, `contents: read`,
  Windows PowerShell 5.1(COM 용), 입력은 env 로만 전달, 증거를 artifact 로 업로드하고 ingest 결과는 미리보기(`ingest-preview.json`)만 만든다. 커밋하지 않음.

### 검증 (Linux 컨테이너)

```text
ruff check src tests scripts ci     # All checks passed!
pytest -o addopts="" -q             # 140 passed, 1 skipped (신규 tests/test_live_smoke.py 24건 포함)
mypy src/all_in_cad/live_smoke.py   # Success
python scripts/upstream/check_pins.py  # OK
```

PowerShell 이 이 컨테이너에 없어 `.ps1` 은 실행하지 못했다. 대신 테스트가 스크립트의 정적 계약(ledger 가 요구하는 step id 를 모두 내보내는지,
Save/SaveAs/Close 가 temp 문서 객체에만 걸리는지, `host.draw_line` 이 temp 가드 뒤에만 있는지, `host.send_command` 미사용)과
workflow(수동 트리거만, self-hosted 라벨, 입력이 run 스크립트에 직접 보간되지 않음)를 검사한다. 실제 실행은 아래 LOCAL STEPS.

### LOCAL STEPS (Windows PC)

1. Runner 등록: GitHub `khs0927/All-In-Cad` → Settings → Actions → Runners → New self-hosted runner → Windows x64 의 명령으로
   `C:\actions-runner\all-in-cad` 에 설치, `config.cmd --url https://github.com/khs0927/All-In-Cad --token <표시된 토큰>`.
   기본 라벨이 `self-hosted, Windows, X64`. **서비스로 설치하지 말 것**(질문에 N) — 파이프가 CurrentUserOnly 이고 COM ROT 가 세션별이라
   AutoCAD 를 띄운 사용자 세션에서 `run.cmd` 로 실행해야 한다.
2. AutoCAD 2027 실행 → `dotnet build native/autocad2027/AllInCad.AutoCAD2027.csproj -c Release -p:ACAD2027_DIR="C:\Program Files\Autodesk\AutoCAD 2027"` →
   빌드 산출물 복사본을 `NETLOAD`.
3. 로컬 1회 확인: `powershell -ExecutionPolicy Bypass -File scripts\windows-live-smoke.ps1 -DryRun` → 기본(읽기 전용) → `-Write`.
4. Workflow: `gh workflow run windows-live-smoke.yml -R khs0927/All-In-Cad -f mode=write` (또는 Actions 탭) →
   `gh run download <run-id> -R khs0927/All-In-Cad -D artifacts\downloaded`.
5. 승격: `all-in-cad live-smoke ingest artifacts\downloaded\...\live-smoke.json` 로 미리보기 → 맞으면 `--apply` →
   `docs/verification-ledger.json`, `docs/ROADMAP.md` 커밋.

## Round 3 (2026-11-09)

배경: power-cad-mcp `docs/ecosystem/CAD_CONSOLIDATION_PLAN.md` 가 같은 AutoCAD 2027 프로세스에 두 writer 가 함께 로드될 수 있는데
All-In-Cad 플러그인의 `host.draw_line` / `host.send_command` 등은 승인 토큰 없이 실행된다고 지적. power-cad 쪽 writer 는 승인을 요구한다.

### 변경 — 쓰기 승인 게이트 (기본 ON)

- `native/shared/HostWriteGuard.cs` (신규): `HostWriteGuard` + `SendCommandPolicy` + `CanonicalJson`.
  흐름은 power-cad `cad_plan_create` → `cad_plan_execute` 와 같은 개념: **plan → token → execute**.
  - `host.plan_write {method, params}` → 검증 후 pending plan 저장, `plan_id`, `approval_token`, `operation_digest`, `expires_at` 반환(쓰기 없음).
  - 게이트 대상 메서드는 같은 params + `params.approval_token` 으로 호출. 토큰 = `b64url(body).b64url(HMAC-SHA256)`, 프로세스별 랜덤 비밀키,
    method + canonical params digest 에 묶임, 기본 120 s 만료(`AIC_APPROVAL_TTL_SECONDS`, 최대 600), **1회용**, pending 최대 64.
  - 거부 코드: `E_APPROVAL_REQUIRED` / `E_APPROVAL_INVALID` / `E_APPROVAL_MISMATCH` / `E_APPROVAL_EXPIRED` / `E_APPROVAL_REUSED` / `E_APPROVAL_BUSY` / `E_COMMAND_DENIED`.
  - 대상: `host.draw_line`, `host.send_command`, `xicad.ensure_loaded`, `xicad.draw_wall|door|window`, `xicad.smoke_wall_door_window`,
    `xicad.run`, `xicad.rollback`, 그리고 `force_load` 가 켜진 `xicad.probe`. 읽기(`system.ping`, `host.context`, `host.capabilities`,
    `xicad.state`, `xicad.run_status`)는 게이트 없음.
  - `host.send_command` / `xicad.run` 명령 정책(항상 적용, opt-out 과 무관): 공백으로 나눈 모든 토큰을 denylist 와 대조(SHELL, SCRIPT, NETLOAD,
    APPLOAD, VBA*, CUI*, SAVE*/QSAVE/WBLOCK/EXPORT/PLOT/PUBLISH, OPEN/NEW/CLOSE*/QUIT/EXIT, PURGE, SECURELOAD/TRUSTEDPATHS, U/UNDO 등),
    LISP `(` 는 `AIC_SEND_COMMAND_ALLOW_LISP=1` 없이는 거부, `AIC_SEND_COMMAND_DENY` 로 추가, `AIC_SEND_COMMAND_ALLOW` 로 첫 명령 allowlist.
  - **opt-out**: acad.exe 환경에 `AIC_REQUIRE_APPROVAL=0` (false/off/no). 그 외 값/미설정은 ON. opt-out 시 부트 로그 `approval SKIPPED (opt-out)`
    + 명령줄 경고. 토큰 발급은 계속 되므로 같은 클라이언트 코드가 두 모드에서 동작.
- `native/autocad2027/Plugin.cs`: `DispatchAsync` 가 switch **이전에** `AuthorizeWrite` 실행, `host.plan_write` 처리, Initialize 에서 env 로 게이트 재구성,
  `system.ping.approval_required` / `host.capabilities.approval` 노출, capability 목록에 `host.plan_write` 추가.
  `HostProtocol.cs`: 원본 System.Text.Json params(`RawParams`) 보존 — Newtonsoft 재파싱이 날짜형 문자열·숫자 표기를 바꿔 digest 가 흔들리지 않게.
- `src/all_in_cad/host_approval.py` (신규): 같은 계약의 Python 레퍼런스 구현. `tests/fixtures/host_approval_vectors.json` 을 Python/C# 양쪽이 재생해
  digest·숫자 정규화·명령 정책이 어긋나지 않음을 확인. (All-In-Cad 에는 Python MCP 서버가 없다 — 파이프 쓰기 경로는 C# 플러그인뿐이라 강제는 C# 에서 한다.)
- `native/shared-tests/` (신규): NuGet 의존 없는 콘솔 테스트(CI `native-protocol` 잡에 추가).
- `scripts/windows-live-smoke.ps1 -Write`: temp 도면 가드 뒤에 `pipe.approval_gate`(토큰 없는 draw_line → `E_APPROVAL_REQUIRED`, 엔티티 0 확인) →
  `pipe.approval_request`(`host.plan_write`) → `pipe.draw_line`(토큰 포함) → `pipe.approval_reuse`(같은 토큰 재전송 → `E_APPROVAL_REUSED`, 개수 불변).
  opt-out 워커에서는 gate/reuse 단계가 skipped. 증거 detail 에는 토큰 sha256 만 기록.
- `live_smoke.py`: ledger 항목 `autocad2027.native.approval_gate` + ROADMAP 승격 줄 1개 추가. 기존 항목 요구 단계는 그대로.
- 문서: `docs/PROTOCOL.md`(메서드 표, 흐름, 정책, opt-out), `docs/SAFETY_CONTRACT.md`, `README.md`, `native/autocad2027/README.md`.

### 검증 (Linux 컨테이너)

```text
ruff check src tests scripts ci                      # All checks passed!
pytest -o addopts="" -q                              # 175 passed, 1 skipped (tests/test_host_approval.py 33건 신규, test_live_smoke 2건 신규)
dotnet build native/shared -c Release                # 0 warnings (TreatWarningsAsErrors)
dotnet run --project native/shared-tests -c Release -- tests/fixtures/host_approval_vectors.json   # passed=84 failed=0
pwsh 7.6 Parser.ParseFile(scripts/windows-live-smoke.ps1)   # parse errors: 0
```

AutoCAD SDK 가 없어 `AllInCad.AutoCAD2027.csproj` 전체 빌드는 불가(의도된 fail-closed). 대신 `HostProtocol.cs` + Plugin.cs 의 신규
`DispatchAsync` 앞부분/`HandlePlanWrite`/`AuthorizeWrite` 를 그대로 잘라 net10 스크래치 프로젝트에서 컴파일(성공, CA1416 플랫폼 경고만 제외).
`tests/test_host_approval.py` 는 Plugin.cs 의 dispatch `case` 가 모두 게이트 대상이거나 명시된 읽기 전용인지, 게이트가 switch 앞에서 도는지도 검사한다.

### LOCAL STEPS (Windows PC)

1. `git pull` 후 AutoCAD 2027 을 닫고 플러그인 재빌드:
   `dotnet build native\autocad2027\AllInCad.AutoCAD2027.csproj -c Release -p:ACAD2027_DIR="C:\Program Files\Autodesk\AutoCAD 2027"`
   (D: 설치면 기본값 그대로). `bin\Release` 를 별도 폴더로 복사.
2. (선택) C# 게이트 단독 확인: `dotnet run --project native\shared-tests\AllInCad.NativeProtocol.Tests.csproj -c Release -- tests\fixtures\host_approval_vectors.json` → `failed=0`.
3. AutoCAD 2027 실행(환경변수 `AIC_REQUIRE_APPROVAL` **설정하지 않음**) → 복사본 `NETLOAD` → `AIC_STATUS`.
   `%LOCALAPPDATA%\All-In-Cad\logs\autocad2027.log` 에 `write approval gate: required=True` 확인.
4. `powershell -ExecutionPolicy Bypass -File scripts\windows-live-smoke.ps1` (읽기 전용) → `pipe.capabilities` detail 에 `approval_required: true`.
5. `powershell -ExecutionPolicy Bypass -File scripts\windows-live-smoke.ps1 -Write` → `pipe.approval_gate`, `pipe.approval_request`,
   `pipe.draw_line`, `pipe.approval_reuse`, `com.readback` 모두 passed. 로그에 `approval refused ... E_APPROVAL_REQUIRED`, `approval consumed`,
   `approval refused ... E_APPROVAL_REUSED` 가 순서대로 있어야 함.
6. `all-in-cad live-smoke ingest artifacts\live-smoke\<runId>\live-smoke.json` 미리보기 → `autocad2027.native.approval_gate` passed 확인 → `--apply` →
   `docs/verification-ledger.json`, `docs/ROADMAP.md` 커밋.
7. (선택) opt-out 확인: AutoCAD 종료 → `set AIC_REQUIRE_APPROVAL=0` 한 cmd 창에서 `acad.exe` 실행 → NETLOAD → 명령줄 WARNING 확인 →
   `-Write` 실행 시 gate/reuse 가 skipped, draw_line/readback 은 passed. 끝나면 변수 없이 AutoCAD 재시작(기본 ON 복귀).
8. power-cad-mcp 와 함께 로드한 상태에서도 4–5 를 한 번 더 돌려, 두 플러그인 모두 토큰 없는 쓰기를 거부하는지 확인.
