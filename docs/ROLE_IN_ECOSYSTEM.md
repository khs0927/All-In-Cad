<!-- README 스니펫: 아래 '## 생태계 내 역할' 절을 README.md에 그대로 붙여 넣을 수 있습니다. -->

## 생태계 내 역할 (Role in ecosystem)

**4단계 CAD — 호스트 탐지와 읽기 전용 증거**

- `all-in-cad doctor`(호스트·추출 도구 탐지), 헤드리스 DWG/DXF 추출·의미 그래프·SQLite 색인(`all-in-cad index`), `all-in-cad-dxf-evidence`를 맡습니다. MCP 서버는 없습니다.
- 목표 경계에서는 DWG를 쓰지 않습니다. 실행은 power-cad-mcp가 맡고, 이 저장소는 실행 결과를 따로 읽어 비교합니다.
- 현재 `native/autocad2027` 플러그인의 쓰기 메서드(`host.draw_line`, `host.send_command`, `xicad.*`)와 `AGENTS.md` 규칙 4(ZWCAD 기본 writer)는 이 목표와 다릅니다. 정리 순서는 CAD 정리 계획 2·4단계를 보세요.
- 받는 것: `aec-executor-handoff/1`(증거 첨부용), `cad-drawing-grammar/1`(생산자 HS-CAD 비활성). 주는 것: `all-in-cad-dxf-evidence` → Ontology-platform `sion_cad`.

전체 지도: [ECOSYSTEM.md](https://github.com/khs0927/power-cad-mcp/blob/main/docs/ecosystem/ECOSYSTEM.md) · CAD 정리 계획: [CAD_CONSOLIDATION_PLAN.md](https://github.com/khs0927/power-cad-mcp/blob/main/docs/ecosystem/CAD_CONSOLIDATION_PLAN.md) (2026-11-07 기준, 설계안)
