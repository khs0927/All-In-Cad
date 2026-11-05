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
