# run_spec 1.0 검증 절차

이 명세의 통과 조건은 하나다: **호스트 구현이 `vectors/golden_vectors.json` 의 모든 벡터를 통과한다.**
소스 코드 리뷰는 통과 조건이 아니다.

## 0. 지금 상태 (2026-09-26 기록)

- `run_spec.json` / `golden_vectors.json` 은 **JSON 구문 검증만** 되었다.
- 이 명세를 실행하는 상태기계 구현은 **아직 없다**. 따라서 어떤 호스트도 아직 벡터를 통과했다고 말할 수 없다.
- `artifacts/w1-port.md` (이식 분석) 는 이 저장소에 존재하지 않는다. 1순순서 0→8 은 미반영 상태다.

## 1. 참조 실행기(reference runner) 를 먼저 만든다

벡터는 "호스트 없이" 상태기계 규칙 자체를 검사한다. 그래서 먼저 **가짜 호스트(fake host)** 를 붙인 참조 실행기를 이식 언어와 무관하게 하나 만들고, 이것으로 골든 벡터를 통과시켜야 한다. 이 선행 단계가 없으면 구현체가 명세의 계약을 검증 없이 그냥 재현하게 된다.

가짜 호스트가 노출해야 하는 최소 인터페이스 (명세의 `guards` 가 요구하는 것만):

```
read_host_state()        -> bool          # host_state
read_host_prompt()       -> str | None    # host_prompt
read_entity_count()      -> int | None    # None 이 아니라 0 을 돌려줄 수 있다 (GV-11)
send_input(text)         -> None          # 개행 종료자 1개를 붙여 호출
undo()                   -> None          # command_started == true 일 때만 호출되어야 한다
now()                    -> monotonic     # 전이 기한 판정
```

가짜 호스트는 `golden_vectors.json` 의 `given` 만 보고 동작한다. 명세의 `expect` 를 참조해 분기하면 그건 검증이 아니라 자기 확인이다.

## 2. 실행 절차

### 2.1 스키마 자체 검사 (언어 무관)

1. `run_spec.json` 을 파싱한다.
2. `states` 에 정확히 8개가 있고, `transitions[].from` / `.to` 가 모두 `states` 에 존재하는지 확인한다.
3. `transitions[].guard` 가 모두 `guards` 의 키인지 확인한다.
4. `on_failure.error_code` 가 모두 `error_codes` 의 코드인지 확인한다.
5. `error_codes` 의 `rollback` 이 `required` / `forbidden` / `conditional` 중 하나인지 확인한다.
6. `golden_vectors` 경로가 실제 파일로 resolve 되는지 확인한다.
7. 명세에 ZWCAD 고유 용어(`CMDACTIVE`, `LASTPROMPT`, `CMDNAMES`, `SendStringToExecute`, `Application.Idle`, `ezdxf`, `FreeCAD`)가 **비-technology 블록 밖의 규칙 본문에** 새지 않았는지 확인한다. 허용된 예외: `host_neutrality.required_terms` 와 `recording_lane` 블록, 그리고 이 문서.

### 2.2 벡터 실행

각 벡터 `V` 에 대해:

```
state = "idle"
while True:
    if now() > deadline: apply timeout policy
    pick the transition matching (state, evaluated guards) using V.given
    if V.given.host_prompt_available == False and transition requires prompt: T13
    record send_input() texts into actual_inputs_sent
    record undo() calls into actual_rollback_calls
    state = transition.to
    if state in ("committed", "failed"): break
```

종료 후 아래를 **전부** 비교한다. 하나라도 다르면 실패다.

| 항목 | 비교 방법 |
|---|---|
| `terminal_state` | 문자열 정확 일치 |
| `inputs_sent` | **순서와 문자열이 모두 정확** 일치. 길이가 달라도 실패 |
| `rollback_called` | bool 정확 일치. 참인데 되돌리기 입력이 없으면 실패, 거짓인데 되돌리기 입력이 있으면 실패 |
| `final_entities` | 정수 정확 일치 |
| `error_code` | `null` 은 `null` 과 비교. 없으면 `error_codes` 의 코드와 비교 |

추가로 벡터에 있는 필드가 있으면 그것도 검증한다.

- `forbidden_inputs` — `inputs_sent` 에 **어떤 형태로도** 없어야 한다 (GV-05).
- `skipped_inputs` — `inputs_sent` 에 없어야 한다 (GV-06).
- `entity_count_read_attempts` — 카운터 재시도 횟수가 정확해야 한다 (GV-11).
- `every_input_terminated` — 모든 `inputs_sent` 항목이 정확히 하나의 개행으로 끝나야 한다 (GV-14).

## 3. 반드시 통과해야 하는 개별 케이스 (재발 방지)

전체 통과만으로는 부족하다. 아래 12개 중 하나라도 깨지면 그 함정이 되돌아온 것으로 판단한다.

1. GV-01 정상_한단계_명령
2. GV-02 정상_다단계_명령
3. GV-03 프롬프트_불일치로_중단
4. GV-04 명령_미기동
5. GV-05 실행안된명령에_UNDO_금지
6. GV-06 유휴상태_빈Enter_무시
7. GV-07 엔티티델타_불일치_롤백
8. GV-08 엔티티델타_일치_커밋
9. GV-09 호스트_프롬프트_없음
10. GV-10 시간초과
11. GV-11 검증_카운터_일시0_안정판정
12. GV-12 파괴적_명령_승인필요

추가 2개: GV-13 호출형태_명령종류_일치, GV-14 개행_종료_매전송_1회.

## 4. 실패했을 때

- 벡터를 통과시키려고 `expect` 를 고치면 안 된다. 명세는 정본이고 벡터는 관측의 고정본이다.
- 벡터가 실제 관측과 어긋난다는 것이 의심되면, 그可疑 벡터를 **삭제하지 말고** `SPEC.md` 에 라벨을 붙여 남긴다. 관측 / 결정 / 불가를 구분하는 것이 벡터를 지우는 것보다 낫다.
- 가드를 느슨하게 만들어 통과시키는 것은 금지다. 특히 `command_started` 와 `entity_count_stable` 는 느슨하게 만들면 사용자의 작업이 사라지거나 검증이 조용히 통과한다.

## 5. 호스트 구현이 이식될 때

각 호스트(ZWCAD 네이티브, FreeCAD 검증 호스트, 향후 다른 CAD)는 다음 순서로 이식한다.

1. `run_spec.json` 만 읽고 상태 머신 뼈대를 세운다.
2. 참조 실행기와 골든 벡터를 붙여 통과시킨다. **이 시점에서 호스트 종속 코드는 아직 하나도 없어야 한다.**
3. 그 위에 호스트 어댑터(위 1절의 최소 인터페이스)만 붙인다.
4. 다시 골든 벡터를 통과시킨다. 어댑터 단계에서 깨지면 그건 명세 결함이 아니라 어댑터 결함이며, 명세를 손대지 말고 어댑터를 고친다.
5. 실제 CAD 에서 수동 확인한 뒤에만 "검증됨" 이라고 쓴다.
