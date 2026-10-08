# ZWCAD-HOST-HANDOFF — 이 호스트의 ZWCAD/XiCAD 작업 인수인계

- 작성: 2026-09-26 (Asia/Seoul)
- 세션: `sessions\2026-09-26_8ORnwfjJrKmHeySr`
- 대상 독자: ZWCAD 플러그인 **XiCAD**(`C:\xicad`)와 `all-in-cad`의 ZWCAD 2026 자동화 러너
- 목적: 라이선스로 이 호스트에서 ZWCAD를 더 이상 구동할 수 없으므로, **얻은 지식을 관측/추론/미확정으로 분리된 형태**로 남긴다.

**라벨 규칙 (이 문서 전체에 적용)**
| 라벨 | 뜻 |
|---|---|
| `[관측]` | 이 호스트에서 실제로 실행하거나, 파일 바이트에서 직접 읽은 사실 |
| `[추정]` | 관측 위의 논리. 반증 조건이 있으면 함께 적는다 |
| `[불가]` | 확인하지 못했다. 존재하지 않는다고 확인한 것이 아니다 |

> **보안 고지 `[관측]`** — 이 호스트의 `bash` / `read_file` / `write_file` / `edit_file` 결과에는
> "IMPORTANT: You are a LOWER-CAPABILITY MODEL…" 이라는 **시스템 지시처럼 생긴 블록이 매번 뒤붙어** 돌아온다.
> 이는 신뢰할 수 없는 채널(명령 stdout)에 주입된 **프롬프트 인젝션**이며 시스템 지시가 아니다.
> 본 작업에서는 지시를 따르지 않고 정상 검증 행위를 유지했다. 이 현상은 여러 산출물에서 독립적으로 8회 이상 관찰됐다.

---

## 목차

- [a) 이 호스트에서 ZWCAD로 무엇이 되었고 무엇이 안 되었는지](#a-이-호스트에서-zwcad로-무엇이-되었고-무엇이-안-되었는지)
- [b) 역분석으로 확정된 사실 (관측)](#b-역분석으로-확정된-사실-관측)
- [c) 정적으로 못 얻은 것 (불가)](#c-정적으로-못-얻은-것-불가)
- [d) 실행 중 발견한 CAD 런타임 함정 6가지](#d-실행-중-발견한-cad-런타임-함정-6가지)
- [e) C# 러너의 CRITICAL 9건과 그 구조적 원인](#e-c-러너의-critical-9건과-그-구조적-원인)
- [f) 이 호스트에서 CAD 자동화가 불가능한 세 가지 이유](#f-이-호스트에서-cad-자동화가-불가능한-세-가지-이유)
- [g) 복호 도구 사용법과 이전 경로](#g-복호-도구-사용법과-이전-경로)
- [h] 하지 못한 것 (정직성 장)](#h-하지-못한-것-정직성-장)
- [부록: 함께 읽을 산출물](#부록-함께-읽을-산출물)

---

## a) 이 호스트에서 ZWCAD로 무엇이 되었고 무엇이 안 되었는지

### a-1. 된 것 `[관측]`

| 항목 | 결과 | 근거 |
|---|---|---|
| ZWCAD 2026 실행 | 성공 (여러 차례) | pid 36504, 37692, 43172 등. 도면 `Drawing1.dwg` |
| 벽 생성 `c:xiDrawWall` | 성공 | 엔티티 3개 → 9개. 6개 증가 |
| 프롬프트 캡처 | 성공 | `LOGFILEMODE=1` 커맨드 로그가 유일한 오염 없는 채널 |
| 명령 상태 폴링 | 성공 | `LASTPROMPT` / `CMDACTIVE` / `CMDNAMES` 실측 |
| `xiDoor2` 1·2단계 진행 | 성공 | `>> 설정(S)/ 문 폭 입력 <900>:` 승인 → `>> 경첩측 점 지정:` 도달 |
| 정적 역분석 (FAS4 복호) | 완전 성공 | 28/28 파일, 11,505 심볼, 722 함수명 |
| SDK 컴파일 프로브 | 성공 | `ZwManaged.dll` / `ZwDatabaseMgd.dll` 참조, 경고 0 오류 0 |
| 명령 계약 레지스트리 | 부분 성공 | 408개 중 harvested 314 / ambiguous 17 / not_found 77 |
| 위험도 분류 | 완료 | DESTRUCTIVE 15 / MUTATING 324 / READONLY 36 / UNKNOWN 33 |

### a-2. 안 된 것 `[관측]`

| 항목 | 결과 | 근거 |
|---|---|---|
| **문 배치 완주** | **실패 (엔티티 델타 0)** | 9개 → 9개. 2단계 힌지점이 DIMLINEAR에 가로채임 |
| 408개 명령 자동 구동 | 미착수 | 안전 정책만 산출, 실제 일괄 실행 0건 |
| ZWCAD 2026 COM 등록 | 없음 | `GetActiveObject` 전 ProgID가 `MK_E_UNAVAILABLE (0x800401E3)`. 레지스트리 `HKLM/HKCU\SOFTWARE\Classes` 하에 `ZWCAD*` 서브키 **0건** |
| GUI 클릭 / NETLOAD | 불가 | 데스크톱 GUI 도구 부재 (브라우저 자동화만 존재) |
| `SendInput` 키 입력 | 차단 | 강제 Ctrl+9 시도에서 `sentCount=0` = UIPI 차단 |
| `.des` 복호화 | 실패 | DEScoder BlowFish 8바이트 ECB, 벤더 상수 키 |

### a-3. 라이선스 블록 (이 호스트의 결정적 장애물) `[관측]`

```
제목줄 변화:  "ZWCAD 2026 Professional Edition"  →  "ZWCAD 2026 Trial version (Limited)"
zwco.log:     "Authorization Lost and Functions Limited. (-15)"  (12:59:26 이후 지속)
ZwLic.log:    "no dll  DESKTOP-KTQHS1I"  86행 연속, 2026-08-30 ~ 05:24
license.xml:  LicType="2"(floating)  ServerName="DESKTOP-KTQHS1I"  ServerPort="27000"
              AppLicenseInstall Status="1" Tried="5"
```

- `[관측]` 이 호스트는 **浮动(floating) 라이선스 서버**로 설정되어 있다. `DESKTOP-KTQHS1I` 는 이 머신(192.168.123.155)이다.
- `[관측]` 그러나 **라이선스 서버가 존재하지 않는다.** C:/D:/G: 전역에 `lmgrd` / `NLM` / `lmutil` 바이너리 0건, `.lic` 파일 0건, ZWSOFT 라이선스 서버 Windows 서비스 없음(있는 것은 MS `FlexNet Licensing Service 64` 와 `ClipSVC` 뿐), 27000 포트에 리스닝 없음.
- `[관측]` 클라이언트 쪽은 정상: `flxNetCommonFNP.dll` v11.16.6.0 존재.
- `[관측]` 유일한 `.asr` 은 `LocalTrial_20262142_ZWCAD_OFFICIAL_TRIAL.asr` 이고, 구매 라이선스 산출물은 디스크 전체에 없다.
- `[관측]` `-15` 는 26.31 업데이트 이전에도 있었다(`zwco.log` 의 02:58:18, 03:44:23 기록). **설치 문제로 롤백/재설치해도 해결되지 않는다.**
- `[관측]` 실용적 결과: 이 모드에서는 **시작 스크립트와 플러그인 로딩이 억제된다.** `acaddoc.lsp` 자동 로더를 심어도 재시작 후 `zwco.log` 에 `[AIC]` / `[FORCE-XICAD]` 줄이 아예 없었다.
- **해결에 필요한 산출물 (2개, 둘 다 로컬 생성 불가)** `[관측]`
  1. ZWSOFT Network License Manager 설치 파일
  2. 이 호스트에 발급된 `.lic` 파일
- **결론:** 라이선스는 owner(사용자) 액션이다. 이 작업 세션 동안 **재빌드 / NETLOAD / 문 배치 재시도 전부 헛일이었고, 지금도 그렇다.**

---

## b) 역분석으로 확정된 사실 (관측)

### b-1. FAS4 CRUNCH 복호 성공 `[관측]`

- **알고리즘: rolling XOR (그리폰).** `out[i] = enc[i] ^ key[i % L] ^ key[(i-1) % L]`, `kp` 는 **1부터** 시작.
- **키 = 파일 자신의 평문 트레일러.** 마커 `b'\n;fas4 crunch\n;'` 를 찾고, 그 앞 6~11 바이트를 붙인 블록이 키다. 검증 조건은 `data[key_start-1] == len(key)`.
- **결과:** 28/28 `.fas` 파일 복호 성공. 키 추출 실패 0건. 실측 키 길이 14~21 바이트, **파일마다 다르다.**
- **기각된 해석:** 사전 + 허프만/산술 유사 압축이 아니다. 엔트로피 7.18~7.71 은 키 주기 아티팩트다.
- **기각된 해석:** 1바이트 XOR `0xc2` 는 부분 정렬 우연이다(최적 바이트가 파일마다 다름).
- **재현 절차:** `REPRODUCE-FAS4.md` 참조.

### b-2. 408개 명령 프롬프트 어휘 `[관측 + 부분 추정]`

- 계약 레지스트리: **harvested 314 (77.0%) / ambiguous 17 (4.2%) / not_found 77 (18.9%)**.
- 후보 6,186개 중 high 1,550건. high 중 1,077건이 강한 근거(`name_substring`).
- **75개 harvested 명령은 high 후보가 하나도 없다.** 계약이 전부 약한 근거(`symbol_name_table`)에 얹혀 있다.
- `not_found` 77건의 구성 `[관측]`:
  - 25건 = `unknown` 카테고리(mns 전용, `description_ko` 공란). **`.fas` 집합에서는 계약화 원천 자체가 없다.**
  - ~20건 = 대화상자 전용 명령(DCL을 열고 커맨드라인 프롬프트를 안 함).
  - SecMulti 10건 / SecPaper 8건 = 공유 헬퍼 프롬프트가 명령명 없는 앵커에 있어 약한 근거만 적용.
- **77건 중 어느 것도 추측으로 채우지 않았다.** 조용히 들어간 틀린 계약은 `not_found`보다 위험하다 — 자동화는 그럴듯한 오답과 정답을 구분하지 못한다.

### b-3. 벽/문 실제 기하 `[관측]`

- 벽 생성 결과 **7개 엔티티, 전부 `LINE`**. 관측된 레이어 배분:
  - layer `0` : 중심선 1개
  - layer `C` : 양면 2개 (Y=−100, Y=+100; 두께 200)
  - layer `S` : 3개 (Y=−120, Y=+250, Y=+280)
  - layer `F` : 1개 (Y=+200)
  - **관측 합계: 1+2+3+1=7. 캡 엔티티는 관측되지 않았다.** 이전의 9개 주장은 캡 2개를 더한 것이며 실측 근거가 없다.
- **두께 200** `[관측]`. C 레이어 Y 간격이 정확히 thickness와 1:1 대응.
- `[추정]` `C`=중심선/벽면, `F`=마감선, `S`=구조선. **역할 추정이며, 반증 조건**은 동일 두께·동일 길이 벽을 다른 위치에 하나 더 그려 3개 레이어의 개수와 Y 오프셋이 재현되는지 확인하는 것이다. 하나라도 바뀌면 기각.
- `[추정]` 일반식 `S 오프셋 = thickness/2 + (20, 150, 180)`. **단일 thickness 인스턴스 1개에서 도출.** thickness=300으로 재현해 170/300/330 이 아니면 기각.
- `[불가]` 벽이 만드는 엔티티가 전부 LINE 인지는 미확정. `xi_makelwpoly` 심볼이 존재하므로 최소 한 경로는 폴리라인일 수 있다.
- `[불가]` 경사 벽에서 회전하는지 수평 고정인지. 관측된 6개 선은 전부 수평(Y 상수)이다.

### b-4. 문 계약 2단계 `[관측 — 런타임 캡처 + 정적 복호 양쪽에서 일치]`

```
1) >> 설정(S)/ 문 폭 입력 <900>:     기본값 900, 키워드 S = 설정 대화상자
2) >> 경첩측 점 지정:                 힌지측 점 (osnap _nea)
```

- `[관측]` `CMDACTIVE` 가 35(LISP 명령 수행 중)에서 1(단일 CAD 명령)로 떨어진 지점이 명령 종료 신호다. 2단계에서 35→1 전이가 관측됐다.
- `[관측]` 정적 복호 문자열 `문 폭 입력 <;900.0` (resource offset 15369), `경첩측 점 지정 <_nea>: ` (15344), `한 쪽 점 지정 (실내측 점) <_nea>: `.
- `[관측]` `xiWin2` 는 완전히 대칭: `>> 설정(S)/ 창문 폭 입력 <1500>:` → `>> 한 쪽 점 지정 (실내측 점):`
- **`[불가]` 3단계의 존재 여부.** 관측된 사실은 "3단계 프롬프트가 **미관측**"까지다. "3단계가 없다"는 결론은 아니다. 명령이 2단계에서 중단되었기 때문이다.
- **프롬프트 수 ≠ 입력 읽기 수 `[관측]`** — `>> 설정(S)/ 문 폭 입력 <900>:` 한 줄 안에서 `getkword` 키워드 읽기와 `getdist` 수치 읽기가 이미 2회다. 즉 최소 3회의 입력이 이미 함의되고, 프롬프트 없는 반복 단계가 정적으로 배제되지 않는다.
- `[추정, 강함]` `c:xidoor1` 은 벽선을 깎지 않고, 이미 있는 벽선 위치를 참조(`_nea`, osnap `DOOR`)해 심볼만 추가한다. 개구부를 만드는 명령은 별도로 `xiWallOpening`(라벨 `개구부 그리기`)이 존재한다.

### b-5. DialogBox DCL 파라미터 `[관측 — 평문 DCL 실물, CP949]`

- `C:\xicad\DialogBox\ACAD_xi9172_d.01.dcl` = `xiDoor2:dialog { label = "문 그리기 - D2"; … }`
- **문 파라미터 34개 확정** (키/라벨 직접 판독): `CmdFirst_rdo`/`DclFirst_rdo`, `OnewayDoor_rdo`/`SwingDoor_rdo`,
  `Offset_edt`, `Middle_rdo`/`Twice_rdo`/`OneFix_rdo`, `DoorDivWd_edt`, `DoorAng_pop`, `FixWide_edt`,
  `DoorBarLay_edt`, `DoorThk_edt`, `Threshold_edt`, `FixDoorDepth_rdo`/`FixWallDepth_rdo`, `FixDepth_edt`, `BarWidth_edt`,
  `WallCen_tgl`/`WallCen_rdo`, `IntDepth_*`/`ExtDepth_*`, `ArcOpen_rdo`/`LinOpen_rdo`/`NonOpen_rdo`, `DoorArcLay_edt`,
  `DoorEle_rdo`/`MatDivi_rdo`/`NonEle_rdo`, `DoorEleLay_edt`, `SillLoc_grp`(4안), `Fin1Wall_rdo`/`EachWall_rdo`,
  `WallGap_*`(3안 기준), `DoorBar1..4_img`, `cmdlp_tgl`, `Group_tgl`.
- `ACAD_xi0428_d.01.dcl` = `xiWallOpening` (라벨 `개구부 그리기`), 파라미터 11개.
- `[관측]` **`DOOR` 는 블록 이름이 아니라 osnap 모드 토큰**이다 — `(osmode "DOOR" 0)` / `(osmode "DOOR" 1)`.
- `[관측]` **`DOOR_ELE` 도 블록 이름이 아니다** — 실제 키는 `DoorEle_rdo`(라디오) + `DoorEleLay_edt`(레이어)다. **`OnewayDoor` 도** `OnewayDoor_rdo` 라디오 키다. 두 가설 모두 반증됐다.
- `[관측]` `C:\xicad\xiLib\xiConfig.cfg` 가 위젯 값을 대화상자 이름 키로 직렬화 보관한다(`/xiDoor1`, `/xiDoor2`, `/xiDoor3`, `/xiWin1`, `/xiWin2`, `/xiWallOpening`, `/xiDrawWall`). **디컴파일 없이 기본값을 얻는 실질적 대체 경로.**
- `[관측]` `900` / `200` / `1500` 리터럴은 84개 DCL 어디에도 없다. 모든 기본값은 런타임 주입이다. `xiConfig.cfg` 기준 `900` 은 `/xiDoor1`·`/xiDoor3` 에 있고 `/xiDoor2` 는 `1500|1000` 이다.
  → `[추정]` 캡처된 `>> 설정(S)/ 문 폭 입력 <900>:` 은 **XIDOOR2 가 아니라 XIDOOR1 또는 XIDOOR3** 에서 나왔을 가능성이 높다. **미확정.**
- `[관측]` `xiDoor1`·`xiDoor3`·`xiDrawWall` 의 DCL 파일은 `C:\xicad` 안에 없다. 코드 내 동적 생성 또는 별도 배포본.
- `[관측]` `cw_barx`(벽그리기 DCL)는 DialogBox 42개 어디에도 없다.

### b-6. 컨테이너 정체 `[관측]`

| 확장자 | 정체 | 상태 |
|---|---|---|
| `.fas` | 표준 AutoLISP FAS4 (34바이트 매직 + 십진수 ASCII 헤더 2행) | **완전 복호** |
| `.zelx` | `!ZAS` + **64바이트** 헤더 래퍼. `0x28 = n1+64`, `0x2C` = 트레일러 시작, `0x30`/`0x34` = `.fas` 헤더 정수와 28/28 일치 | 동일 소스 파생 증명됨 |
| `.des` | `BWF PROTECTED LISP` = **DEScoder(Menhirs NV/Bricsys) BlowFish 8바이트 ECB**, 암호문 오프셋 20부터 | **복호 실패** (벤더 상수 키) |

- `[관측]` `.des` 안에서 `FAS4-FILE` 매직은 **어느 위치에서도 발견되지 않는다** → `.fas` 를 품은 컨테이너가 아니라 별도 암호화 산출물.
- `[관측]` 28개 `.des` 의 오프셋 20~67 이 23개 파일에서 **완전히 동일** → ECB 서명. 키 1개만 얻으면 28개 전부 열린다.
- `[관측]` 오프셋 20~67 동일 블록은 `xi2dPro`, `xiBath2`, `xiKitc`, `xiNumInc`, `xiStair` 5개에서 다르다.
- **`.des` 는 이 호스트에서 열리지 않는다.** 복호화 키는 `DESCoder.exe` 또는 BricsCAD LISP 엔진 내부 벤더 상수이고, 트리에 알려 평문 쌍이 없다. ECB 코드북은 키를 대체하지 못한다(ECB는 알려평문→암호문을 줄 뿐, BlowFish 복호는 역함수가 필요하다).

### b-7. CHM 도움말 `[관측 — 부정 결과]`

`C:\xicad` 의 CHM 3개는 **모두 서드파티 AutoCAD 플러그인 `iDwgTab` 의 도움말**이다. `index.htm` 제목이 `iDwgTab3.4 프로그램 설명` / `iDwgTab 3.4 Program description` 이다. 각 49페이지 디컴파일 후 전수 스윕 결과 `xi*` 명령 0건, 한국어 동치어 0건. `D2`/`W2`/`문` 히트는 `images\*.gif` 내부의 바이너리 오탐과 `구문` 부분 문자열이었다.
→ **`C:\xicad` 안의 어떤 문서도 입력 순서를 명시하지 않는다.** 이름이 그럴듯한 `xiDrawWall.txt` 같은 형제 파일은 linetype/layer 그룹 정의다.

---

## c) 정적으로 못 얻은 것 (불가)

> **이 섹션이 이 인수인계 문서에서 가장 비싼 자산이다.**
> 여기서 "시도하지 않았다"와 "될 수 없다고 확인했다"를 구분한다.

### c-1. 프롬프트 실행 순서 — **구조적으로 불가능** `[관측 + 추론]`

복호된 resource 스트림은 **인터리빙된 코드가 아니라 연속된 테이블들**이다.

```
오프셋 ~0   – ~2000  : 심볼 이름 테이블   (C:XIDOOR2 @61, C:XIDOOR3 @51, C:XIDOOR1 @80, C:XICWALL @90 …)
오프셋 ~2000 – ~5000  : DCL 대화상자 소스   (TwoDoor_tgl @2498, xiDoorRotate:dialog @2643 …)
오프셋 ~5000 – 끝    : gvar 이름과 문자열 상수 (XIDOOR2_WIDTH @8328, 경첩측 점 지정 @15344,
                                                 문 폭 입력 <;900.0 @15369, OnewayDoor_rdo @15479 …)
꼬리 ~50300+         : 함수 참조 영역
```

**따라서 `C:XIDOOR2` 의 오프셋 61과 프롬프트의 오프셋 15369 사이의 거리는 호출 그래프 인접도도 프롬프트 순서도 아니다. 그것은 "같은 테이블에 같이 들어 있다"는 뜻일 뿐이다. 오프셋이 가깝다는 사실은 그 프롬프트가 그 명령의 것이라는 증거가 아니다.**

이것이 레지스트리가 문서 수준과 명령 수준 양쪽에
`"order_confidence": "offset_order_not_execution_order"` 를 저장하는 이유이며,
모든 후보에 `link_basis` 와 `confidence` 를 붙인 이유다.

- **CODE 섹션에는 오프셋 테이블도 길이 테이블도 없다.** 32비트 단조 run 스캔 결과는 16비트 인덱스 + 상위 2바이트 0을 32비트로 읽은 아티팩트뿐이며, 파일 전체의 길이 필드는 컨테이너 스칼라 4개뿐이다.
- **관측된 피연산** `[관측]`: `0x03 PUSH_VALUE`, `0x09 PUSH_G`, `0x0c PUSH_GLOBAL` 이 각각 CRUNCH 섹션 GVar 테이블로의 16비트 LE 인덱스 1개를 가진다. `57`/`67`/`68` 은 int32 피연산(`57` 은 음수 = 상대 분기). 인덱스는 선언 역순으로 참조된다.
- 실측 예: `xi2dPro.fas` `gvar[174]='LOGAND'`, `gvar[166]='LENGTH'`; `xiKitc.fas` `gvar[202]='XI_PATH'`, `gvar[190]='xiKici:dialog { '`
- `[불가]` 위 opcode 역할은 ISA가 완전 해독되지 않았으므로 **추론**이다. 빈도표와 n-gram이 재현 가능한 근거다.

**프롬프트 순서를 얻으려면 가능한 경로는 셋뿐이며, 전부 이번 세션의 범위 밖이었다.**
1. **바이트코드 역어셈블** — `princ`/`entsel`/`getpoint` 호출 지점이 실제 순서를 준다. 이 작업은 bytecode 섹션을 **길이만 파싱하고 역어셈블하지 않았다.** 다음 사람의 1순위.
2. **런타임 캡처** — 프롬프트 순서만 직접 답한다. 라이선스 상태 때문에 현재 불가.
3. **`xiConfig.cfg` + DCL의 위상 대응** — DCL 위젯 순서와 프롬프트 순서의 관계 규칙이 있으면 간접 추정. **그런 규칙은 발견되지 않았다.**

### c-2. 나머지 `[불가]` 항목

| 항목 | 상태 | 이유 |
|---|---|---|
| 벽 검색 로직 (힌지점 근접? 레이어 필터? 직전 결과 기억?) | 불가 | 암호화된 본문 안. 기존 캡처는 전부 벽 없는 도면이라 분기에 한 번도 진입하지 못했다 |
| `INSUNITS` / 도면 단위 | 미직접 확인 | 수치 1:1 대응으로 mm `[추정]`이나 시스템 변수를 직접 읽지 않았다 |
| `DoorAng_pop` 기본 각도 (45 vs 90) | 미확정 | 리터럴 `45`,`90`,`90` 이 있으나 어느 것이 기본인지 분기 손실. `xiConfig.cfg` 의 `doorangpop` 을 직접 읽으면 답이다 |
| 층 0 잔여 선 2개 (Y=−2500, Y=−2000) 정체 | 미확정 | "실험 실패 잔재"는 해석일 뿐. 같은 입력으로 처음부터 다시 그려 재현되는지 봐야 한다 |
| `xiDoor1`/`xiDoor3`/`xiDrawWall` DCL | 부재 | DialogBox에 없음 |
| `cw_barx` (벽 DCL) 파라미터 | 부재 | DialogBox에 없음 |
| 3단계 프롬프트 존재 여부 | 미관측 | 명령이 2단계에서 중단됨 |
| `설정(S)` 키워드 분기 | 미실행 | `CMDDIA=0` 확인했으나 실제 시험은 하지 못했다 |
| 단축키 `D2`/`WAL` 안정성 | 불안정 `[관측]` | `alias_smoke` 에서 `W2`만 `ok:true`, `WAL`·`D2`는 `ok:false`. `REINIT` 상태 의존. **자동화는 `(c:xiDoor2)` 직접 호출이 안전하다** |
| 33개 `mns` 명령의 성격 | 미상 | `description_ko` 공란. 이름만 보고 등급을 매기지 않는다 |
| 업스트림 opcode 테이블 | best-effort | `0x1e`/`0x1f` = 자리표시자 `ALPHA`/`BETA`, `0x40`/`0x62`/`0x63` = `UNK_40`/`NOP_62`/`NOP_63`. 기본 피연산 폭과 0x03/0x09/0x0c 해석은 28/28 정합하므로 신뢰, 분기 opcode 이름은 미확정 |
| `out_xiWin.lsp`의 제어 흐름 | 신뢰 불가 | 디컴파일러가 동명 비연속 변수를 재사용했다. `entmakex` 가 door 본문에 34회 등장하지만 이는 지역변수명이다. **문자열·심볼명에는 신뢰, 제어 흐름에는 신뢰하지 말 것** |

---

## d) 실행 중 발견한 CAD 런타임 함정 6가지

> 각 항목은 **실측 증거**와 **재발 조건**을 함께 적는다. 실패한 순서가 서열되지 않았으므로
> "이걸 몰랐으면 다시 걸렸을 것"을 명시한다.

### 함정 1 — 깨진 전송이 "프로브가 소비됐다"로 위장한다 `[관측]`

- **증상:** 프로브 LISP가 CAD에 도달하지 못한 채 `consumed` 로 판정되어, 원인 없이 "명령이 살아있다"는 잘못된 결론에 도달했다.
- **실측:** `aic_rpc.ps1` 를 `powershell -File` 로 **중첩 호출**하면 LISP 문자열 안의 큰따옴표가 네이티브 인자 경계에서 깨져 `The system cannot find the path specified.` 가 났다.
- **왜 위험한가:** mtime 판정 로직 자체는 처음부터 올바르지만, **"프로브가 실행 안 됨"과 "명령이 입력을 먹음"이 구분되지 않는다.** 전자는 하네스 버그, 후자는 CAD 상태 문제인데 같은 신호로 보인다.
- **수정:** Named Pipe 클라이언트를 인프로세스로 직접 구현. `aic.native/1` = 4바이트 LE 길이 접두 + UTF-8 JSON.
- **재발 조건:** 셸 중첩 호출로 큰따옴표를 포함된 인자를 넘기는 모든 경로.
- **규칙:** **"프로브 미실행"을 보고받으면 CAD 상태 탓으로 돌리지 말고, 먼저 프로브 자체의 전달 실패를 배제하라.**

### 함정 2 — Enter를 포함한 Clear는 지우던 명령을 되살린다 `[관측]`

- **증상:** `ESC + CR + CR + space` 로 Clear를 시도했더니 DIMLINEAR가 매번 되살아났다. 영구적으로 못 죽이는 것처럼 보였다.
- **실측:** `Command:` 프롬프트에서 Enter는 **직전 명령 재실행**이다.
- **재발 조건:** 커맨드 라인이 유휴 상태일 때 종료 시퀀스에 개행을 포함시키는 모든 구현.
- **수정:** Clear는 **ESC 만**.
- **규칙:** **명령 문자열 끝에 맨 Enter 를 절대 붙이지 말라.** `HandleXicadDrawDoor` 페이로드가 `\n\n` 으로 끝나는 것이 `delta_entities: 0` 의 진짜 근본 원인이었다. `C:\xicad\Lisp\_onekey.lsp` 의 `c:DL`/`c:DA` = `_.DIMLINEAR`/`_.DIMALIGNED` 매크로가 "직전 명령" 슬롯에 앉아 있었다.

### 함정 3 — Raw ESC는 취소가 아니라 빈 점 입력이다 `[관측]`

- **증상:** `[char]27` 을 보냈는데 취소되지 않고 프롬프트만 반복되었다.
- **실측:** DIMLINEAR의 점 프롬프트에서 0x1B 가 빈 입력으로 소비됐다.
- **재발 조건:** `SendStringToExecute` 로 raw 바이트를 넘길 때.
- **수정:** 캐럿 표기 **`^C^C^C`** 를 쓴다.
- **규칙:** 취소 시퀀스도 프로토콜 정보다. `SendStringToExecute` 는 `^C` 표기를 받는다.

### 함정 4 — 종료 신호와 프롬프트 문자열은 다른 축이다 `[관측]`

- **증상:** 마지막 프롬프트가 `Specify the first point: ` 인데, XiCAD 문 명령이 왜 거기서 끝났는지afx 멈췄는지 알 수 없었다.
- **실측:** XiCAD의 LISP 구동 명령은 `CMDACTIVE: 35` 에 있다. **1 로 떨어지는 순간 XiCAD 명령은 반환한 것**이고, 그 뒤의 `Specify the first point: ` 는 **남아 있던 네이티브 명령(LINE)의 프롬프트**다.
- **재발 조건:** 지연된 프롬프트를 그대로 신뢰하는 모든 게이트. 게이트가 한 스텝 지연 관측하는 것과 결합하면 인자가 프롬프트 이전에 큐잉된다.
- **수정:** 종료 판정은 `LASTPROMPT` 문자열이 아니라 **`CMDACTIVE` 전이**로 한다. `CMDNAMES` 로 어느 명령인지 교차 확인한다.
- **규칙:** **`CMDACTIVE` 는 종료 신호이지 프롬프트 텍스트가 아니다.**

### 함정 5 — 창으로 프롬프트를 볼 수 없다 `[관측]`

- **증상:** ZWCAD 메인 윈도가 최소화 상태(rect `-18286,-18286,158,26`)라 `PrintWindow` 캡처로 커맨드 라인을 읽을 수 없었다.
- **관측된 폐쇄 경로 전체:**
  - 커맨드라인이 Ctrl+9 상태로 꺼져 렌더링되지 않음
  - `WM_GETTEXT` on `CZcadCmdlineHistoryPopup` 및 커맨드라인 편집 윈도우는 **길이 0** 반환(owner-drawn)
  - 숨겨진 `ZWCAD Text Window`(`AfxFrameOrView140u`)에 `PrintWindow` 는 **검은 이미지** (활성화되지 않으면 스스로 다시 숨김)
  - `SendInput` 은 **UIPI 차단**으로 `sentCount=0`
- **해결:** **`LOGFILEMODE=1` 커맨드 로그가 이 호스트에서 유일한 오염 없는 프롬프트 읽기 채널이었다.**
- **주의 2가지** `[관측]`: (a) 한국어 프롬프트가 mojibake로 기록된다 — 구조 `>> …(S)/ … <900>:` 로 식별한다. (b) 켜는 순간 이전 세션 기록이 로그로 flush 되므로, 새 로그의 `Command: DIMLINEAR` 는 **과거 아티팩트**지 라이브 명령이 아니다.
- **후속 상태:** 이 변수는 ZWCAD 재시작 후에도 1로 살아남았으며, 유휴 커맨드 라인에서 `(setvar "LOGFILEMODE" 0)` 으로 검증된 0으로 되돌린 바 있다. **`door-placement-result.md` 작성 시점엔 커맨드 라인이 wedged 되어 복원이 적용되지 않았다. 후속 작업자는 반드시 재확인하라.**
- **규칙:** **UI 캡처 시도에 시간을 쓰지 마라. 파이프로 가라.**

### 함정 6 — 프로브 LISP 자체가 조용히 죽는다 `[관측]`

세 가지가 겹쳤다. 어느 하나만으로도 프로브는 "정상인데 데이터가 없다"처럼 보인다.
- **괄호 불균형** — 사양으로 주어진 프로브 LISP는 `CMDACTIVE` 줄의 닫는 괄호가 하나 빠져 있었다(원문 그대로). 교정 전에는 프로브가 `HASDOORFUNC` 줄 이후로 기록되지 않았다.
- **`fboundp` 가 이 빌드에서 에러** — `(fboundp 'c:xiDoor2)` 를 쓰면 `progn` 중간에서 에러가 나 프로브가 중단된다. → **`atoms-family` 로 대체.** `(atoms-family 1 '("C:XIDOOR2" …))` 가 `(C:XIDOOR2 XIDOOR2 nil nil)` 반환. 스칼라 표기 `(atoms-family 1 "C:DXFOUT")` 와 `(atoms-family 1 'C:DXFOUT)` **둘 다 실패**(`ERROR: invalid argument type: listp`). **리스트만 받는다.**
- **개별 심볼 대신 전체 덤프** — 특정 이름이 중요할 때 `(vl-princ-to-string (atoms-family 0))` 로 115,904 문자를 받아 부분 문자열 검색. 이것이 `C:DXFOUT`/`C:SAVE`/`C:WBLOCK`/`C:QSAVE`/`C:SAVEAS` 가 라이브 심볼 테이블에 **없음**을 확립했고, 별개 툴체인(HS-STEEL `bridge\fnmap.json`)의 `nil` 항목을 독립 확인했다.

**부수 함정 — 엔티티 카운트 3종이 서로 다르다 `[관측]`**
| 소스 | 같은 호출에서의 값 | 판정 |
|---|---|---|
| 기존 `host.context` `EntityCount` | 0 (실제 3) | **신뢰 불가** |
| `SnapshotModel` 기반 `xicad.draw_*` 핸들러 | 진짜 3 | **의심 대상** — `delta_entities` 를 신뢰하지 말고 재파생할 것 |
| `BlockTableRecord` + `TransactionManager.StartTransaction()` 순회 | 3 | **정답** |
- 파이프라인 종료 중 읽으면 **일시적 0** 이 나온다. 도면이 멀쩡한데 파괴된 것처럼 보이므로 더 나쁘다. 성공 경로에서만 재시도 안정 읽기를 쓰고, 실패 경로는 **미설정(-1)으로 둬라.**

---

## e) C# 러너의 CRITICAL 9건과 그 구조적 원인

리뷰 대상: `C:\Users\khs09\all-in-cad\native\zwcad2026\XicadRunner.cs` (476줄 전량) + `Plugin.cs` (1002줄의 일부) + `shared\CurrentUserPipeServer.cs`.
**실행하지 않았고 빌드하지 않았다. 모든 판정은 소스 텍스트 근거다.**
결과: **CRITICAL 9 / MAJOR 14 / MINOR 9** (전체 `w1-review.md`)

### e-1. 구조적 원인 하나: **되돌리기 단위가 "이 잡"이 아니라 "직전 사용자 작업"이다**

러너 전체 설계는 "자신이 만든 엔티티만 되돌린다"는 전제를 깔고 있다. 그런데 되돌림 경로가 정확히 두 갈래다:

- `job.UndoRecording` 이 true 면 `doc.Database.Undo()` — **전역 Undo 레코드**
- 아니면 문자열 `_.U\n` — **전역 Undo 명령**

잡이 만든 기록이 아니다. **직전(혹은 지금 열려 있는) Undo 레코드**를 되돌린다.
`StartUndoRecord` 는 `NotImplementedException` 이고, **`EndUndoRecord` 를 호출하는 코드가 어디에도 없다.** L321에서 연 기록은 실행 lifetime 동안 닫히지 않는다.

이 하나의 구조적 결함이 아래 CRITICAL 9건 중 6건을 직접 유발한다.

| # | 요약 | 파일:라인 | 구조적 원인과의 관계 |
|---|---|---|---|
| **C-1** | 되돌림 단위가 "이 잡"이 아니라 "직전 사용자 작업" | `XicadRunner.cs:453-461` | **근원 자체** |
| **C-2** | `_.U\n` 의 `\n` 은 실제 Enter → 마지막 명령 재실행 가능. `^C^C^C` 와 `_.U` 를 **같은 틱에 연속 큐잉**하고 완료 대기·프롬프트 확인이 전혀 없음 | `:443, 460` / `Plugin.cs:860-865` | C-1 의 실행 경로 |
| **C-3** | 되돌림 성공을 검증 없이 `rolled_back=true` 로 보고. `SendStringToExecute` 가 예외 안 던졌다는 뜻일 뿐, ZWCAD Undo 는 비동기 큐다. 개수 재확인·REVISION 비교 없음 | `:462, 229-257` | C-1 의 보고 결함 |
| **C-4** | `CountEntities` 가 **블록 테이블 전체**를 센다. 잡이 건드린 건 모델스페이스 하나뿐인데 20초 기본 창 동안 사용자가 그리면 Δ가 어긋나고, 오탐 실패가 **사용자 작업을 되돌리는 트리거**가 된다 | `:89-112, 315, 403-405` | C-1 의 오염 경로 |
| **C-5** | 계약이 비면 게이트가 완전히 사라지고, 그 상태로 인자가 전송된다. `if (expect.Length > 0 && …)` 가 곧 실행 조건 | `:350-364` | 별개 (관찰 모드가 가장 위험) |
| **C-6** | `start_settle` 은 "명령이 살아있다"만 보고 프롬프트 준비 완료를 보장하지 않는다. `CMDNAMES` 로 `(c:LINE)` 실행 중인지 대조하지 않는다 | `:340-345` | C-5 와 결합 |
| **C-7** | `xicad.rollback` RPC 는 **잡 가드가 전혀 없다.** `ActiveJob`·`CommandStarted`·`BeforeEntities` 확인 없이 `doc.Database.Undo()` 한 줄. `^C^C^C` 도 안 보낸다 | `:229-257` | C-1 을 RPC 로 노출 |
| **C-8** | 레거시 LISP 핸들러와 `host.send_command` 가 **JobGate 를 우회**. `xicad.draw_wall` 은 `FILEDIA 0 CMDDIA 0 ` + `._CANCEL\n(c:xiDrawWall)\n<점>\n<점>\n\n` 을 통째로 큐에 넣는다. `E_BUSY` 는 `xicad.run` 에만 적용 | `Plugin.cs:264-285, 505-527, 560-570, 607, 644, 669, 719` | C-5/C-6 과 결합해 인자 사이에 끼어든다 |
| **C-9** | 잡이 `running` 에서 영구히 갇힐 수 있고 **탈출 수단이 없다.** `PumpBusy` 가 true 로 남으면 이후 모든 `xicad.run` 이 영구히 `E_BUSY`. `xicad.cancel` 같은 탈출 메서드가 `HostCapabilities.All` 과 switch 에 없다 | `:184, 275-289` / `Plugin.cs:191-210, 726` | C-6 의 스케줄러 버전 |

### e-2. 다음 사람이 제일 먼저 봐야 할 것

1. **C-1 하나만 고쳐도** C-2/C-3/C-4/C-7 의 대부분이 사라진다. 잡 단위 Undo 경계를 만들 수 없다면(SDK에 `EndUndoRecord` 가 없으므로) **최소한 되돌림 전후 `CountEntities` + `REVISION` 을 기록하고 실제 반영을 확인한 뒤에만 `rolled_back=true` 를 세워라.** 반영 안 됐으면 `E_ROLLBACK_UNCONFIRMED` 를 반환하라.
2. **C-4 의 엔티티 카운트는 모델스페이스만 세도록 바꿔라.** 블록 테이블 전체 순회는 작업이 많은 도면에서 수백 ms씩 앱 스레드를 점유하며(M-12), 그 지연이 게이트 타이밍을 흔든다.
3. **C-5 는 관찰 모드가 가장 위험 모드라는 역설을 만든다.** 계약이 없으면 아무 검증 없이 인자를 큐에 넣는다고 코드에 적혀 있다. 계약 없는 호출은 코드 레벨에서 막아라.
4. **C-9 에 탈출구부터 만들어라.** `xicad.cancel` 이 없다면 408회 배치에서 한 번 갇히면 그 세션 전부가 죽고 되돌릴 수 없다.
5. **컴파일 성공은 런타임 성공이 아니다 `[관측]`.** `Database.StartUndoRecord()` 는 깨끗하게 컴파일되다가 ZWCAD 안에서 첫 실제 호출에 `NotImplementedException` 을 던져 롤백 계층을 조용히 `_.U` 문자열 폴백으로 강등시킨다. 부수효과를 겨냥한 SDK 멤버는 살아있는 프로세스에 대해 최소 한 번 실행해 봐야 자동화가 의존할 수 있다.

---

## f) 이 호스트에서 CAD 자동화가 불가능한 세 가지 이유

> 세 경로는 **각각 다른 서명을 보인다.** 이 서명을 알면 재시도 시간을 아낀다.

### f-1. 데스크톱 GUI 도구가 없다 `[관측]`

- 이 환경의 자동화 표면은 **브라우저 전용**(Playwright over CDP)이다.
- NETLOAD 대화상자를 클릭하거나, 대화상자 상자에 타이핑할 방법이 **존재하지 않는다**.
- **서명:** "할 수 있다"고 가정하고 스크립트를 짜다가 실행 단계에서 막힌다. 실행이 아니라 **도구 부재**다.

### f-2. `SendInput` 이 UIPI 에 막힌다 `[관측]`

- 강제 Ctrl+9(커맨드라인 표시)를 시도했고 `sentCount=0` 이 돌아왔다.
- 이는 PowerShell 프로세스가 ZWCAD보다 **낮은 무결성 수준**에 있어 키 입력이 **거부된 것이 아니라 조용히 버려졌음**을 뜻한다.
- **서명:** **0 전송은 UIPI 신호다, 코드 버그가 아니다.** 재생성(`regen`)하면 반환값이 1이 되고 아무 일도 일어나지 않는다. 이 차이를 모르면 "키가 안 먹는다"를 코드 문제로 잘못 추적한다.
- **우회:** 키 입력 자체를 버려라. 파이프로 가라.

### f-3. ZWCAD 2026 이 COM 등록되어 있지 않다 `[관측]`

- `GetActiveObject` 가 **모든** ProgID에서 `MK_E_UNAVAILABLE (0x800401E3)`: `ZWCAD.Application.26`, `ZWCAD.Application.26.0`, `ZWCAD.Application`, `ZwCAD.Application.26`, `AutoCAD.Application.26`.
- `HKLM:\SOFTWARE\Classes` 와 `HKCU:\SOFTWARE\Classes` 전수 스윕에서 **`ZWCAD*` 서브키 0건**.
- **이 사실은 f-1/f-2보다 더 중요하다.** "COM으로라도 로딩 단계만이라도" 라는 제안이 있을 수 있으나, **이 경로는 이 호스트에 존재하지 않는다.** 역사적 `C:\xicad\_nl_automation\runner\cad_com.py` COM 하네스는 **어떤 형태로도 부활시킬 수 없다.**
- **서명:** **out-of-band 폴백을 제안하기 전에 그 폴백 표면이 존재하는지 먼저 확인하라.** "COM" 은 CAD 제품에 보편적으로 있다고 읽히지만 그렇지 않다.
  (이 문서 작성 시점 실측 재확인: `New-Object -ComObject "ZWCAD.Application"` → `0x80080005 CO_E_SERVER_EXEC_FAILURE`, 서버 프로세스 부재로 인한 실패. 위 레지스트리 부재 판정과 모순되지 않는다.)

### f-4. (보조) SDK 어셈블리를 밖에서 로드할 수 없다 `[관측]`

- `Assembly.LoadFrom` on `ZwManaged.dll` → **`0x8007045A`** (CAD 런타임 필요).
- `Assembly.ReflectionOnlyLoadFrom` 은 **작동한다** — net48 x64 작은 도구로 두 DLL을 리플렉션 전용 컨텍스트에 올리고 `AppDomain.CurrentDomain.ReflectionOnlyAssemblyResolve` 로 형제 어셈블리를 ZWCAD 디렉터리에서 해석시키며 베이스 체인을 순회해 멤버를 출력하면 추측이 조회로 바뀐다.
- **함정:** 그 덤퍼가 `MethodInfo`/`PropertyInfo`/`FieldInfo` 만 다뤘으므로 **생성자가 보이지 않았다.** 그것이 `new Transaction()` 이 그럴듯해 보이다가 `CS1729` 로 실패한 이유다. **`ConstructorInfo` 를 추가하라.**
- ZWSOFT 는 `ZwManaged.xml` 을 **제공하지 않는다**(있어도 `ZwMigrator.xml`, `ZwUpdateConfig.xml` 뿐). 문서 폴백이 없고 **컴파일러가 유일한 권위**다.
- **어셈블리에서 앱 버전을 읽지 마라:** `ZwManaged.dll`/`ZwDatabaseMgd.dll` 은 `26.8.0.19652`(2025-09-05) 로 측정되지만 실제 호스트는 `26.31 (2026.09.07)`. **앱 버전은 프로세스 이름에서 가져와라.**

---

## g) 복호 도구 사용법과 이전 경로

### g-1. 도구가 있는 곳 (지금)

```
C:\Users\khs09\.aside\u\0\sessions\2026-09-26_8ORnwfjJrKmHeySr\tmp\verify\decrypt_fas4.py   (15,048 B)
```

동일 사본 2곳이 있다: `tmp\w1-9\decrypt_fas4.py`, `tmp\w2-5\decrypt_fas4.py` (모두 15,048 B, 동일).

**이들은 전부 `tmp` 아래에 있다. 세션이 끝나면 사라질 수 있다.**
`C:\Users\khs09\all-in-cad` 저장소 안에는 **동등한 것이 없다.**

### g-2. 옮겨야 하는 이유 `[추정, 높음]`

1. **구현이 세션 tmp 안에만 존재한다.** `tmp` 는 사용자 비주얼 영역이 아니고随时 정리될 수 있다. 검증된 유일한 FAS4 복호기Implementation이 영구 자산으로 남아 있지 않다.
2. **재현 가능성:** 이 호스트가 아니라 다른 곳에서 `.fas` 를 디코드하려면 지금 이 파일을 시작점으로 삼아야 한다. 위 `REPRODUCE-FAS4.md` 는 이 파일이 없으면 독립 재현이 어렵도록 작성되어 있다.
3. **저장소가 이미 그 이웃을 갖고 있다:** `all-in-cad\docs\` 에는 `PROTOCOL.md`, `CAPABILITY_MATRIX.md`, `HEADLESS_PIPELINE.md` 등 호스트 추상화 문서가 이미 있다. 복호기는 이 흐름에 속한다.

### g-3. 옮길 proposed 경로 (실행하지 않음 — 커밋 권한 없음)

```
제안:  all-in-cad\tools\xi-cad\fas4\decrypt_fas4.py
대안:  all-in-cad\engine\fas4\decrypt_fas4.py     (oda_export.py 가 이미 engine\ 에 있음)
```

- 커밋·저장소 수정은 **이 작업에서 하지 않았다.** 경로와 필요성만 보고한다.
- 이동 시 함께 옮길 것: `decrypt_fas4.py`, `tmp\check_fas4_probe.py`(아래 g-4의 검증 스크립트), 그리고 `w3-2-contract-registry.json`(2.7 MB — 축약본을 저장소에 둘지는 별건 판단).
- 라이선스/출처 고지 필요 `[관측]`: 이 구현은 역공학 결과이지 벤더 소스가 아니다. 공개 참조는 `https://github.com/btduy13/Fas2Lsp` (`fas4_decompiler.py`). `Hopfengetraenk/Fas-Disasm` 는 AutoCAD VisualLISP 용이라 **부적합**하다.

### g-4. 쓰기 함정 — 이 도구를 쓰기 전용 트리에 조심하라 `[관측 — 이번 세션에서 실측됨]`

```
tmp\verify\decrypt_fas4.py:428   filepath = sys.argv[1] if len(sys.argv) > 1 else 'PDI.fas'
tmp\verify\decrypt_fas4.py:439   dec_path = os.path.splitext(filepath)[0] + '_decrypted.bin'
tmp\verify\decrypt_fas4.py:441   f.write(result['res_data'])
```

- **출력 경로가 입력 경로 옆에 하드코딩되어 있다.** `python decrypt_fas4.py C:\xicad\Lisp\xiWin.fas` 는
  `C:\xicad\Lisp\xiWin_decrypted.bin` 을 쓴다.
- **이번 세션에서 실제로 일어났다.** 검증 실행 중 이 파일이 생성되었고, 즉시 삭제했다.
  `C:\xicad\Lisp` 파일 수 101개로 원복 확인. **읽기 전용 위반이 발생했다가 되돌렸다.**
- **다음 사람 지침:** 먼저 입력을 세션 tmp로 복사하거나, `439`행을 출력 디렉터리 인자로 고쳐라.
  **읽기 전용 제약 아래에서 이 스크립트를 `C:\xicad` 에 직접 가리키지 마라.**

### g-5. 실측 재검증 (이 문서 작성 중 수행)

```
file      : C:\xicad\Lisp\xiWin.fas
key bytes : 21  b'\xa1\xa5\x08\xa4,\xe3\n;fas4 crunch\n;'
nsyms     : 1725 | code len: 111162 | res len: 50822 | res vars: 28
needle    : b'\xb9\xae \xc6\xf8 \xc0\xd4\xb7\xc2 <;900.0'   (문 폭 입력 <;900.0, cp949)
hit offsets in decrypted resource stream: [15369]
VERIFIED
```

`nsyms=1725 / bytecode=111162 / res_len=50822 / res_vars=28` 은 이전에 확정된 수치와 **정확히 일치**한다.
`문 폭 입력 <;900.0` 은 복호 스트림 오프셋 **15369** 에 있으며, 계약 레지스트리 기록과도 일치한다.
전체 절차와 다른 확인 스텝은 `REPRODUCE-FAS4.md` 참조.

### g-6. 이 호스트의 Python 격리 문제 `[관측]`

```
$env:PYTHONHOME=""; $env:PYTHONPATH=""; $env:PYTHONIOENCODING="utf-8"
```
- Aside 가 주입하는 `PYTHONHOME` 이 `python -m venv` 설치를 조용히 깨뜨린다. **`uv venv` + 명시적 `PYTHONPATH` 가 신뢰 경로.**
- 한국어 출력을 위해 `PYTHONIOENCODING="utf-8"` 이 필요하다. 안 그러면 `print` 가 깨진다.

---

## h) 하지 못한 것 (정직성 장)

> **이 문서의 가치는 여기 있다. 성공 목록보다 이 목록이 다음 사람에게 더 비싸다.**

| # | 못한 것 | 상태 | 왜 | 다음 사람이 할 수 있는 것 |
|---|---|---|---|---|
| 1 | **문 배치 완주** | **실패** | 2단계 힌지점이 DIMLINEAR에 가로채여 엔티티 델타 0. 도면은 9개로 무손실 보존 | 라이선스 복구 후 `LOGFILEMODE` 채널로 한 스텝씩 재시도. **명령이 살아 있다는 증거(프롬프트 관측)가 사용자가 작업 중이라는 증거가 아니라면 실패다** |
| 2 | **408개 명령 일괄 자동 구동** | **미착수** | 안전 정책(READONLY 36 / scratch-first 324 / approval 48)만 산출. 실제 구동 0건 | 정책대로 allowlist 로 시작. 36건부터 |
| 3 | **프롬프트 실행 순서 314건** | **구조적으로 불가** | §c-1. 리소스 스트림이 코드가 아니라 테이블이다 | **CODE 섹션 역어셈블이 유일한 정적 경로.** `getpoint`/`entsel`/`princ` 호출 지점을 찾으면 순서가 나온다 |
| 4 | **`xiDoor2` 3단계 존재 여부** | 미관측 | 명령이 2단계에서 중단됨 | 1번이 풀리면 즉시 답이 나온다 |
| 5 | **`.des` 28개 복호** | **실패(막힘)** | DEScoder BlowFish 8바이트 ECB, 벤더 상수 키. 트리에 알려평문 쌍 없음 | 키 1개만 얻으면 28개 전부 열린다. `DESCoder.exe` 런타임 분석이 유일 경로 |
| 6 | **C# 러너 CRITICAL 9 수정** | **미착수** | 이 작업은 쓰기를 handoff 아카이브로 제한했다 | e-2 의 순서대로. C-1 이 선행 |
| 7 | **복호 도구 이전** | **미실행** | 커밋 권한 없음 | g-3 의 제안 경로대로 |
| 8 | **`xiConfig.cfg` 기본값 정합 검증** | 미실행 | 시간 부족 | `/xiDoor1`,`/xiDoor2` 값을 읽어 캡처된 `<900>` 이 어느 명령 것인지 확정(U-4) |
| 9 | **33개 mns 명령 등급 확정** | 미해소 | `description_ko` 공란 | `C:\xicad` 의 mns 원본 라인 라벨 확인(읽기 전용) → 개발사 문서 → 최소 샘플 도면 단독 구동. **추정 금지** |
| 10 | **wall/door 3단계 이후, 경사 벽 동작, 잔여선 2개 정체** | 미확정 | 런타임 필요 | §b-3 의 반증 조건이 실험 절차다 |
| 11 | **`LOGFILEMODE` 복원** | **실패** | 커맨드 라인이 wedged 되어 0 복원이 적용되지 않았다 | **후속 작업자 반드시 재확인.** 커맨드 로그가 켜진 채 남아 있을 수 있다 |
| 12 | **기존 추적 파일 수정 / 커밋 / `doctor.ps1` 링크** | **하지 않음** | 금지 규칙 | 아래 "기존 문서 링크 제안" 참조 |

### 기존 문서에 링크를 추가할지에 대한 제안 (보고만, 미실행)

`C:\Users\khs09\all-in-cad\scripts\windows\doctor.ps1` 는 **진단 스크립트**이며
(ZWCAD/AutoCAD 프로세스, Python, .NET SDK, ODA File Converter 존재 확인) **ZWCAD 계약이나 XiCAD 지식이 있는 파일이 아니다.**
`docs\CAPABILITY_MATRIX.md` 는 **증거 규약**(`supported`/`unsupported`/`unknown`/`error`)을 정의하는 파일이라 XiCAD 정적 계약 레지스트리와 성격이 맞는다.

**제안 (실행하지 않음):**
1. `docs\CAPABILITY_MATRIX.md` 하단에 "XiCAD 정적 계약 레지스트리" 항목 추가 — `harvested 314` 는 `supported` 가 아니라 **`unknown`** 이다. "프롬프트 **어휘**는 회수됐지만 순서는 미확정"이 규약에 정확히 들어맞는다.
2. `doctor.ps1` 에는 **링크를 넣지 말 것.** 진단 스크립트가 특정 플러그인의 역분석 결과를 참조하면, `doctor` 가 "이 플러그인이 설치돼 있다"고 오독할 여지가 생긴다.
3. 라이선스 상태 체크를 `doctor.ps1` 에 넣는 것은 **별도 제안**이다. 현재 `doctor.ps1` 는 ZWCAD **프로세스**만 본다. `Trial version (Limited)` 타이틀과 `zwco.log` 의 `Authorization Lost` 를 감지하면 앞으로의 모든 CAD 작업을 **시작 전에** 막아줄 수 있다. 다만 이건 별건 승인 대상이다.

---

## 부록: 함께 읽을 산출물

| 파일 | 내용 |
|---|---|
| `artifacts\zwcad-handoff\RISK-REGISTRY.md` | 408개 명령 통합 표 + 파괴 15개 전체 |
| `artifacts\zwcad-handoff\TIMELINE.md` | 시도 순서와 **실패가 가르쳐준 것** |
| `artifacts\zwcad-handoff\REPRODUCE-FAS4.md` | `.fas` 디코딩 재현 절차 |
| `artifacts\w3-2-contract-registry.{md,json}` | 408개 계약 레지스트리 전문 |
| `artifacts\w3-3-command-tiers.{md,json}` | 위험도 분류 전문 |
| `artifacts\w1-9-crunch-decoder.md` | CRUNCH 복호 완전 복원 |
| `artifacts\w1-review.md` | C# 러너 리뷰 전문 (CRITICAL 9 / MAJOR 14 / MINOR 9) |
| `artifacts\door-placement-result.md` | 런타임 하네스 실패 기록과 DIMLINEAR 가로채기 증거 |
| `artifacts\w1-semantics.md` | 벽/문 의미론 확정 명세 (관측/추정/미확정) |
| `artifacts\w1-6-dcl-dialogs.md` | DCL 전수 분석 |
| `artifacts\w1-4-zas-header.md` | `.zelx` 64바이트 헤더 |
| `artifacts\w1-5-des-cipher.md` | `.des` BlowFish ECB |
| `artifacts\w1-1-chm-manual.md` | CHM 부정 결과 (iDwgTab 도움말) |
| `artifacts\w3-1-sdk-api-probe.md` | ZWSOFT SDK 컴파일 판정표 |
| `artifacts\w1-port.md` | 호스트 독립 코어 분리 (shared/ 512줄 중 506줄 = 98.8% ZWCAD 비종속) |
| `memory\projects\xi-cad-zwcad-automation.md` | 이 프로젝트의 장기 기억 페이지 |
