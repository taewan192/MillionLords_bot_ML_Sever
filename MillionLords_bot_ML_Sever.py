import asyncio
import csv
import logging
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
import os
from pathlib import Path
import queue
import re
import sys
import tempfile
import threading

import discord
from discord.ext import commands
from discord import app_commands


ADMIN_ID = 332469454669611019
SIGNUP_ROLE_ID = 1493170026809921626
BASE_DIR = Path(__file__).resolve().parent
PLAYER_LIST_DIR = BASE_DIR / "player list"
CSV_FILE = PLAYER_LIST_DIR / "accounts.csv"
LOG_DIR = BASE_DIR / "Log"
logger = logging.getLogger("millionlords.ml_server")

# 파일의 읽기·수정·저장 전체를 보호합니다.
_csv_lock = threading.RLock()
# 사용자마다 잠금을 늘리지 않고 고정 개수를 공유합니다.
_user_locks = [asyncio.Lock() for _ in range(64)]
ALT_KEY = re.compile(r"alt[1-9][0-9]*\Z")


class DataError(ValueError):
    """기존 계정 파일을 덮어쓰지 않고 관리자 점검을 요청할 오류."""


def get_all_alt_keys(rows: list[dict]) -> list[str]:
    return sorted({key for row in rows for key in row
                   if isinstance(key, str) and ALT_KEY.fullmatch(key)},
                  key=lambda key: int(key[3:]))


def load_rows() -> list[dict]:
    with _csv_lock:
        path = Path(CSV_FILE)
        if not path.exists():
            return []
        # Excel의 UTF-8 BOM도 지원합니다. 잘못된 파일은 빈 데이터로 취급하지 않습니다.
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            headers = reader.fieldnames
            if not headers or not {"discord_id", "main"}.issubset(headers):
                raise DataError("accounts.csv의 discord_id/main 헤더를 확인해주세요.")
            if len(headers) != len(set(headers)) or any(not key for key in headers):
                raise DataError("accounts.csv에 비어 있거나 중복된 열 이름이 있습니다.")
            rows = []
            ids = set()
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise DataError("accounts.csv의 열 개수가 맞지 않습니다.")
                uid = row["discord_id"].strip()
                if not uid.isascii() or not uid.isdecimal() or not 0 < int(uid) < 2**64:
                    raise DataError("accounts.csv에 잘못된 Discord ID가 있습니다.")
                uid = str(int(uid))
                if uid in ids:
                    raise DataError("accounts.csv에 중복된 Discord ID가 있습니다.")
                ids.add(uid)
                row["discord_id"] = uid
                rows.append(row)
            return rows


def save_rows(rows: list[dict]):
    """같은 폴더의 임시 파일에 완전히 쓴 뒤 교체해 저장 실패 시 원본을 보존합니다."""
    with _csv_lock:
        path = Path(CSV_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 관리자가 추가한 열도 버리지 않습니다.
        existing_headers = []
        if path.exists():
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                existing_headers = next(csv.reader(stream), [])
        schema_rows = [{key: "" for key in existing_headers}] + rows
        alt_keys = get_all_alt_keys(schema_rows)
        extra_keys = list(dict.fromkeys(key for row in schema_rows for key in row
                                      if key not in {"discord_id", "main"} and key not in alt_keys))
        headers = ["discord_id", "main"] + alt_keys + extra_keys
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", newline="",
                                             dir=path.parent, prefix="accounts_", suffix=".tmp",
                                             delete=False) as stream:
                temp_name = stream.name
                writer = csv.DictWriter(stream, fieldnames=headers)
                writer.writeheader()
                writer.writerows(rows)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, path)
        finally:
            if temp_name is not None:
                Path(temp_name).unlink(missing_ok=True)


def get_account(user_id: int) -> dict | None:
    return next((row for row in load_rows() if row["discord_id"] == str(user_id)), None)


def validate_nickname(nickname: str, *, main: bool = False) -> str:
    nickname = nickname.strip()
    limit = 32 if main else 256
    if not nickname or len(nickname) > limit or any(ord(char) < 32 or ord(char) == 127 for char in nickname):
        raise ValueError(f"닉네임은 줄바꿈 없이 1~{limit}자로 입력해주세요.")
    return nickname


def save_main(user_id: int, nickname: str) -> bool:
    nickname = validate_nickname(nickname, main=True)
    with _csv_lock:
        rows = load_rows()
        target = next((row for row in rows if row["discord_id"] == str(user_id)), None)
        if target is not None:
            if target.get("main", "").strip():
                return False
            target["main"] = nickname
        else:
            rows.append({"discord_id": str(user_id), "main": nickname})
        save_rows(rows)
        return True


def save_alt(user_id: int, nickname: str, *, admin: bool = False) -> tuple[bool, str]:
    nickname = validate_nickname(nickname)
    with _csv_lock:
        rows = load_rows()
        target = next((row for row in rows if row["discord_id"] == str(user_id)), None)
        if target is None or not target.get("main", "").strip():
            if not admin:
                return False, "❌ 먼저 `/signup`으로 메인 계정을 등록해주세요."
            if target is None:
                target = {"discord_id": str(user_id)}
                rows.append(target)
            target["main"] = "관리자"
        alt_keys = get_all_alt_keys([target])
        existing = [target["main"]] + [target.get(key, "") for key in alt_keys]
        if any(value.strip().casefold() == nickname.casefold() for value in existing):
            return False, "❌ 이미 본인의 메인 또는 ALT 계정에 등록된 닉네임입니다."
        # alt1, alt3처럼 중간 번호가 비어도 기존 alt3을 덮어쓰지 않습니다.
        next_num = 1
        while target.get(f"alt{next_num}", "").strip():
            next_num += 1
        target[f"alt{next_num}"] = nickname
        save_rows(rows)
        return True, target["main"]


def delete_user_data(user_id: int) -> bool:
    with _csv_lock:
        rows = load_rows()
        kept = [row for row in rows if row["discord_id"] != str(user_id)]
        if len(kept) == len(rows):
            return False
        save_rows(kept)
        return True


def parse_user_id(value: str) -> int:
    value = value.strip()
    if not value.isascii() or not value.isdecimal() or not 0 < int(value) < 2**64:
        raise ValueError("올바른 유저 ID를 숫자로 입력해주세요.")
    return int(value)


def _user_lock(user_id: int):
    return _user_locks[user_id % len(_user_locks)]


def _display(value: str) -> str:
    return discord.utils.escape_markdown(discord.utils.escape_mentions(value))


class MLServerBot(commands.Bot):
    async def setup_hook(self):
        # on_ready는 재접속 때도 실행되므로 동기화는 시작 시 한 번만 합니다.
        for attempt in range(3):
            try:
                commands_synced = await self.tree.sync()
                logger.info("글로벌 슬래시 명령 %d개 동기화 완료", len(commands_synced))
                break
            except (discord.HTTPException, OSError):
                if attempt == 2:
                    logger.exception("시작 시 명령 동기화 실패: 기존 등록 명령으로 연결을 계속합니다. 새 명령 반영은 재시작 시 재시도됩니다.")
                else:
                    await asyncio.sleep(2 * (attempt + 1))


# 슬래시 명령만 사용하므로 전체 멤버/메시지 캐시와 privileged intents는 불필요합니다.
intents = discord.Intents.none()
intents.guilds = True
bot = MLServerBot(command_prefix="!", help_command=None, intents=intents,
                  max_messages=None, member_cache_flags=discord.MemberCacheFlags.none(),
                  allowed_mentions=discord.AllowedMentions.none())
tree = bot.tree


async def _update_member_profile(member, guild, nickname: str) -> list[str]:
    notes = []
    if guild.owner_id == member.id:
        notes.append("⚠️ 서버 오너의 닉네임은 직접 변경해주세요.")
    else:
        try:
            await member.edit(nick=nickname, reason="메인 계정 등록")
        except discord.HTTPException:
            logger.exception("닉네임 변경 실패: user_id=%s", member.id)
            notes.append("⚠️ 닉네임 변경에 실패했습니다. 봇 권한과 역할 위치를 확인해주세요.")
    role = guild.get_role(SIGNUP_ROLE_ID)
    if role is None:
        notes.append("⚠️ 가입 역할을 찾지 못했습니다. 관리자에게 문의하세요.")
        logger.warning("가입 역할 없음: guild_id=%s role_id=%s", guild.id, SIGNUP_ROLE_ID)
    else:
        try:
            await member.add_roles(role, reason="메인 계정 등록")
            notes.append(f"✅ **{_display(role.name)}** 역할이 부여되었습니다!")
        except discord.HTTPException:
            logger.exception("가입 역할 부여 실패: user_id=%s", member.id)
            notes.append("⚠️ 역할 부여에 실패했습니다. 봇 권한과 역할 위치를 확인해주세요.")
    return notes


@tree.command(name="signup", description="메인 계정 닉네임을 등록합니다.")
@app_commands.guild_only()
@app_commands.describe(닉네임="등록할 메인 닉네임 (1~32자)")
async def signup(interaction: discord.Interaction, 닉네임: str):
    if interaction.guild is None:
        await interaction.response.send_message("❌ 서버에서 사용해주세요.", ephemeral=True)
        return
    nickname = validate_nickname(닉네임, main=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    # 슬래시 명령의 member 객체를 사용하여 전체 멤버 캐시에 의존하지 않습니다.
    member = interaction.user
    if not isinstance(member, discord.Member):
        member = await interaction.guild.fetch_member(member.id)
    async with _user_lock(member.id):
        created = await asyncio.to_thread(save_main, member.id, nickname)
        if not created:
            account = await asyncio.to_thread(get_account, member.id)
            if account is None or account["main"] != nickname:
                await interaction.edit_original_response(content="❌ 이미 메인 계정을 등록하셨습니다. 닉네임 변경은 관리자에게 문의하세요.")
                return
        # 먼저 저장에 성공해야 Discord 닉네임/역할을 바꿉니다.
        # 부분 실패한 경우 같은 닉네임으로 /signup을 재실행하면 재시도됩니다.
        notes = await _update_member_profile(member, interaction.guild, nickname)
        status = "메인 계정이 등록되었습니다!" if created else "기존 계정의 닉네임·역할 적용을 다시 시도했습니다."
        content = f"✅ **{_display(nickname)}**: {status}\n" + "\n".join(notes)
        await interaction.edit_original_response(content=content)
        logger.info("가입 처리 완료: user_id=%s created=%s", member.id, created)


@tree.command(name="alt", description="메인 계정에 alt 계정 닉네임을 추가합니다.")
@app_commands.describe(닉네임="등록할 alt 닉네임")
async def alt(interaction: discord.Interaction, 닉네임: str):
    nickname = validate_nickname(닉네임)
    await interaction.response.defer(ephemeral=True, thinking=True)
    uid = interaction.user.id
    async with _user_lock(uid):
        success, result = await asyncio.to_thread(save_alt, uid, nickname, admin=uid == ADMIN_ID)
    if success:
        result = f"✅ ALT **{_display(nickname)}**이(가) **{_display(result)}** 계정에 등록되었습니다!"
        logger.info("ALT 등록 완료: user_id=%s", uid)
    await interaction.edit_original_response(content=result)


async def _require_admin(interaction) -> bool:
    if interaction.user.id == ADMIN_ID:
        return True
    await interaction.response.send_message("❌ 관리자 전용 명령어입니다.", ephemeral=True)
    return False


@tree.command(name="초기화", description="[관리자 전용] 특정 유저의 Signup을 초기화합니다.")
@app_commands.describe(유저id="초기화할 유저의 ID")
async def reset(interaction: discord.Interaction, 유저id: str):
    if not await _require_admin(interaction):
        return
    target_id = parse_user_id(유저id)
    await interaction.response.defer(ephemeral=True, thinking=True)
    async with _user_lock(target_id):
        removed = await asyncio.to_thread(delete_user_data, target_id)
    if removed:
        result = f"✅ 유저 ID `{target_id}`의 Signup이 초기화되었습니다."
        logger.info("가입 초기화: target_id=%s admin_id=%s", target_id, interaction.user.id)
    else:
        result = f"⚠️ 유저 ID `{target_id}`는 등록된 기록이 없습니다."
    await interaction.edit_original_response(content=result)


def _text_chunks(text: str, limit: int = 1900):
    while text:
        yield text[:limit]
        text = text[limit:]


@tree.command(name="계정조회", description="[관리자 전용] 특정 유저의 메인/alt 계정 목록을 조회합니다.")
@app_commands.describe(유저id="조회할 유저의 ID")
async def lookup(interaction: discord.Interaction, 유저id: str):
    if not await _require_admin(interaction):
        return
    target_id = parse_user_id(유저id)
    await interaction.response.defer(ephemeral=True, thinking=True)
    account = await asyncio.to_thread(get_account, target_id)
    if account is None:
        await interaction.edit_original_response(content=f"⚠️ 유저 ID `{target_id}`의 데이터가 없습니다.")
        return
    main = _display(account.get("main", "").strip() or "없음")
    alts = [_display(account[key]) for key in get_all_alt_keys([account]) if account.get(key)]
    alt_text = "\n".join(f"• {value}" for value in alts) or "없음"
    if len(main) <= 1024 and len(alt_text) <= 1024:
        embed = discord.Embed(title=f"계정 조회: {target_id}", color=discord.Color.blue())
        embed.add_field(name="🏠 메인", value=main, inline=False)
        embed.add_field(name="🔗 Alt 목록", value=alt_text, inline=False)
        await interaction.edit_original_response(embed=embed)
    else:
        # Embed 필드의 1,024자 제한을 넘으면 개인 응답을 나누어 보냅니다.
        chunks = iter(_text_chunks(f"계정 조회: {target_id}\n🏠 메인: {main}\n🔗 ALT 목록\n{alt_text}"))
        await interaction.edit_original_response(content=next(chunks))
        for chunk in chunks:
            await interaction.followup.send(chunk, ephemeral=True)


async def _stop_bot(interaction):
    if not await _require_admin(interaction):
        return
    await interaction.response.send_message("🔴 봇을 종료합니다...", ephemeral=True)
    logger.info("관리자 명령으로 봇 종료: user_id=%s", interaction.user.id)
    await bot.close()


@tree.command(name="봇종료", description="[관리자 전용] 봇을 종료합니다.")
async def stop(interaction: discord.Interaction):
    await _stop_bot(interaction)


@tree.command(name="stop", description="[관리자 전용] 봇을 종료합니다.")
async def stop_en(interaction: discord.Interaction):
    await _stop_bot(interaction)


@tree.error
async def on_app_command_error(interaction, error):
    original = getattr(error, "original", error)
    if isinstance(original, DataError):
        message = f"❌ {original} 기존 데이터는 덮어쓰지 않았습니다."
    elif isinstance(original, ValueError):
        message = f"❌ {original}"
    elif isinstance(original, app_commands.NoPrivateMessage):
        message = "❌ 서버에서 사용해주세요."
    elif isinstance(original, PermissionError):
        message = "❌ 계정 파일에 접근할 수 없습니다. 파일 권한과 엑셀에서 열려 있는지 확인해주세요."
    else:
        message = "❌ 처리 중 오류가 발생했습니다. 관리자에게 알려주세요."
    if not isinstance(original, ValueError) or isinstance(original, DataError):
        logger.error("명령 처리 오류: command=%s user_id=%s",
                     getattr(interaction.command, "name", None), interaction.user.id,
                     exc_info=(type(original), original, original.__traceback__))
    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(content=message, embed=None)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        logger.exception("오류 안내 전송 실패")


@bot.event
async def on_ready():
    logger.info("봇 연결 완료: %s (%s)", bot.user, bot.user.id)


@bot.event
async def on_disconnect():
    logger.warning("Discord 연결 끊김: 자동 재접속 대기")


@bot.event
async def on_resumed():
    logger.info("Discord 세션 재개 완료")


@bot.event
async def on_error(event, *args, **kwargs):
    logger.exception("이벤트 처리 오류: %s", event)


def _configure_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(LOG_DIR / "runtime.log", maxBytes=5 * 1024 * 1024,
                                       backupCount=5, encoding="utf-8")
    console_handler = logging.StreamHandler()
    for handler in (file_handler, console_handler):
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log_queue = queue.Queue(maxsize=1000)
    queue_handler = QueueHandler(log_queue)
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger().addHandler(queue_handler)
    listener = QueueListener(log_queue, file_handler, console_handler)
    listener.start()
    return listener, queue_handler, file_handler, console_handler


def _load_token() -> str:
    # 다른 봇의 DISCORD_BOT_TOKEN과 혼동하지 않도록 전용 이름을 사용합니다.
    token = os.environ.get("ML_DISCORD_BOT_TOKEN", "").strip()
    token_file = BASE_DIR / "ml_bot_token.txt"
    if not token and token_file.exists():
        token = token_file.read_text(encoding="utf-8-sig").strip()
    if not token:
        raise RuntimeError("ML_DISCORD_BOT_TOKEN 또는 ML 폴더의 ml_bot_token.txt에 토큰을 설정해주세요.")
    return token


def main():
    listener, queue_handler, file_handler, console_handler = _configure_logging()
    try:
        token = _load_token()
        logger.info("봇 시작: Python %s / discord.py %s", sys.version.split()[0], discord.__version__)
        bot.run(token, reconnect=True, log_handler=None)
    except Exception:
        logger.exception("봇 실행 중 치명적 오류로 종료")
        return 1
    finally:
        logger.info("봇 프로세스 종료")
        logging.getLogger().removeHandler(queue_handler)
        listener.queue.join()
        listener.stop()
        file_handler.close()
        console_handler.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
