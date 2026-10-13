# MillionLords_bot_ML_Sever

[한국어](#한국어) · [English](#english)

## 한국어

MillionLords Discord 서버의 메인·ALT 계정 등록 봇입니다. 가입자의 닉네임과 가입 역할 적용, CSV 계정 저장, 관리자 조회·초기화·종료를 지원합니다.

### 명령어

| 명령 | 기능 |
| --- | --- |
| `/signup` | 메인 닉네임 등록 및 Discord 닉네임·가입 역할 적용 |
| `/alt` | 본인의 메인 계정에 ALT 닉네임 추가 |
| `/계정조회` | 관리자: 사용자 ID로 메인·ALT 조회 |
| `/초기화` | 관리자: 계정 등록 기록 삭제 |
| `/봇종료`, `/stop` | 관리자: 정상 종료 |

응답은 비공개이며 `/signup`은 서버에서 사용합니다. `/초기화`는 CSV 기록을 삭제하고, Discord 닉네임·역할은 자동으로 되돌리지 않습니다.

### 설치와 실행

Python 3.10 이상이 필요합니다. Python 3.14.2 / discord.py 2.6.4에서 검증했습니다.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

1. `MillionLords_bot_ML_Sever.py`의 `ADMIN_ID`, `SIGNUP_ROLE_ID`를 자신의 서버에 맞게 설정합니다. 저장소 코드는 제작자의 기존 설정을 유지합니다.
2. 봇을 `bot` 및 `applications.commands` 범위로 서버에 초대하고 닉네임 관리·역할 관리 권한을 부여합니다. 봇 역할은 변경할 사용자·부여할 역할보다 위에 있어야 합니다.
3. `ml_bot_token.example.txt`를 `ml_bot_token.txt`로 복사한 뒤 봇 토큰을 입력합니다. 또는 `ML_DISCORD_BOT_TOKEN` 환경변수를 사용합니다. 환경변수가 우선합니다.
4. 실행합니다. `player list/accounts.csv`는 최초 저장 시 생성됩니다.

```powershell
.venv\Scripts\python.exe MillionLords_bot_ML_Sever.py
```

전체 멤버·메시지 캐시와 privileged intents를 사용하지 않습니다. 서버 가입자 정보는 슬래시 명령의 Member 객체에서 얻습니다. 토큰 설정은 다른 봇의 `DISCORD_BOT_TOKEN`과 분리되어 있습니다.

실제 계정 CSV, 토큰, 로그와 원본 백업은 Git에서 제외됩니다. 코드에 들어 있던 토큰은 재발급한 값을 사용하는 것이 좋습니다. 같은 CSV를 사용하는 봇을 여러 프로세스로 중복 실행하지 마세요.

### 이번 수정 내용

- 재접속마다 명령을 동기화하던 부분을 시작 시 한 번 실행하도록 수정했습니다. 시작 동기화의 통신 실패는 최대 3회 시도 후 기록합니다.
- 느린 작업 전에 응답을 유예하고 CSV 작업을 별도 스레드에서 실행합니다.
- 동시 요청의 중복 가입·갱신 누락을 방지하고 사용자별 처리를 고정 개수의 잠금으로 보호합니다.
- CSV를 임시 파일에 완전히 쓴 뒤 교체하여 저장 실패 시 기존 파일을 보존합니다.
- ALT 번호에 빈칸이 있어도 기존 ALT를 덮어쓰지 않으며 본인 메인·ALT의 중복 닉네임을 검사합니다.
- UTF-8 BOM과 추가 열을 지원하고 손상된 CSV·중복 ID는 덮어쓰지 않고 오류를 안내합니다.
- 닉네임·ID 검증, 서버 오너·누락 역할·권한 오류, 긴 ALT 조회 결과를 처리합니다.
- 시작·종료·재접속·예외 로그를 추가하고 중복 종료 구현을 정리했습니다.

이 파일에는 이전 봇의 무제한 로그 리스트나 반복 생성되는 GC 작업은 없었습니다. 동일한 메모리 누적 원인으로 단정하지 않고 실제로 발견한 문제를 수정했습니다.

### 가입 실패와 재시도

먼저 CSV 저장에 성공한 뒤 Discord 닉네임과 역할을 적용합니다. 파일 저장이 실패하면 Discord 프로필을 변경하지 않습니다. 가입 기록 저장 후 닉네임·역할 적용만 실패했다면 기록을 유지하고 부분 실패를 안내합니다.

권한과 역할 위치를 수정한 뒤 같은 닉네임으로 `/signup`을 다시 실행하면 적용을 재시도합니다. 다른 메인 닉네임으로 변경하려면 관리자에게 문의합니다. 서버 오너의 닉네임은 직접 변경해야 합니다.

### 검증과 로그

```powershell
.venv\Scripts\python.exe -X utf8 test_ml_server.py
```

오프라인 테스트 26개와 정적 검사를 통과했습니다. 동시 등록, ALT 번호 공백, 저장 실패 시 원본 보존, 재접속 100회, 응답 유예, 권한 실패·재시도와 긴 비공개 조회를 검증했습니다. 테스트는 Discord에 접속하지 않습니다.

`Log/runtime.log`에 연결·시작·종료·예외를 기록합니다. 로그 대기열은 1,000건이며 파일은 5 MiB 단위, 백업 5개로 순환합니다.

과거 자동 종료의 원인은 당시 예외 로그가 없어 확정하지 못했습니다. 실제 서버에서 장시간 운영을 재현한 결과는 아닙니다. PC 절전·재부팅·터미널 종료를 복구하는 서비스 기능은 포함하지 않습니다.

## English

A main and ALT account registration bot for a MillionLords Discord server. It applies member nicknames and a signup role, stores account records in CSV, and provides administrator lookup, reset, and shutdown commands.

### Commands

| Command | Purpose |
| --- | --- |
| `/signup` | Register a main nickname and apply the Discord nickname/signup role |
| `/alt` | Add an ALT nickname to your own main account |
| `/계정조회` | Administrator: look up accounts by user ID |
| `/초기화` | Administrator: delete account registration records |
| `/봇종료`, `/stop` | Administrator: graceful shutdown |

Responses are private. `/signup` is used in a server. Reset deletes CSV records; it does not revert Discord nicknames or remove roles.

### Setup

Requires Python 3.10 or later. Verified with Python 3.14.2 and discord.py 2.6.4.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

1. Configure `ADMIN_ID` and `SIGNUP_ROLE_ID` in `MillionLords_bot_ML_Sever.py`. The source retains the author's existing configuration.
2. Invite the bot using the `bot` and `applications.commands` scopes. Grant Manage Nicknames and Manage Roles permissions, and place its role above the members and signup role it manages.
3. Copy `ml_bot_token.example.txt` to `ml_bot_token.txt` and enter the token, or set `ML_DISCORD_BOT_TOKEN`. The environment variable takes precedence.
4. Run the bot. `player list/accounts.csv` is created on the first successful save.

```powershell
.venv\Scripts\python.exe MillionLords_bot_ML_Sever.py
```

The bot does not request privileged intents or cache the full member/message history. It uses the Member object provided by slash command interactions. Its token setting is separate from another bot's `DISCORD_BOT_TOKEN`.

Live account data, tokens, logs, and original backups are excluded from Git. Use a rotated token if your previous token was embedded in source code. Do not run multiple bot processes against the same CSV file.

### Fixes

- Moved repeated reconnect-time command synchronization into one-time startup initialization, with at most three attempts for communication failures.
- Deferred responses before slow operations and moved CSV operations off the event loop.
- Protected concurrent registrations and updates with a file lock and a fixed number of per-user processing locks.
- Replaced direct CSV truncation with temporary-file writes and atomic replacement, preserving the original when saving fails.
- Fixed ALT index gaps overwriting existing accounts, and checked duplicate nicknames within a user's main/ALT records.
- Supported UTF-8 BOMs and custom columns; rejected malformed CSV data and duplicate IDs without overwriting it.
- Added input validation and handled server owners, missing roles, permission failures, and long ALT lookup results.
- Added lifecycle/reconnect/exception logs and shared the shutdown implementation between both aliases.

This bot did not contain the previous bot's unbounded log list or repeatedly created GC tasks. The changes address defects actually found in this source rather than assuming an identical memory issue.

### Signup recovery

Account data is saved before changing the Discord nickname or role. If saving fails, the Discord profile is left unchanged. If profile updates fail after saving succeeds, the registration remains and the response explains the partial failure.

After fixing permissions or role order, repeat `/signup` with the same nickname to retry the profile updates. Changing the main nickname requires administrator assistance. A server owner must update their nickname manually.

### Validation and logs

```powershell
.venv\Scripts\python.exe -X utf8 test_ml_server.py
```

All 26 offline tests and static checks passed. Coverage includes concurrent registration, ALT gaps, save failure recovery, 100 reconnect events, deferred responses, permission failures/retries, and long private lookups. Tests do not connect to Discord.

Lifecycle events and exceptions are saved to `Log/runtime.log`. The logging queue is limited to 1,000 entries; files rotate at 5 MiB with five backups.

The historical shutdown cause could not be confirmed without exception logs from the incident. Long-running live Discord operation has not been reproduced. This project does not include a service that recovers from PC sleep, reboot, or closing the terminal.
