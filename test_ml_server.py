import asyncio
import csv
import importlib.util
import logging
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

spec = importlib.util.spec_from_file_location('ml_server', Path(__file__).with_name('MillionLords_bot_ML_Sever.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
logging.disable(logging.CRITICAL)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'accounts.csv'
        self.patch = patch.object(m, 'CSV_FILE', self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_main_registration_is_unique(self):
        self.assertTrue(m.save_main(123, '  가나다  '))
        self.assertFalse(m.save_main(123, 'changed'))
        self.assertEqual(m.get_account(123)['main'], '가나다')
        self.assertEqual(len(m.load_rows()), 1)

    def test_alt_gap_does_not_overwrite_existing(self):
        m.save_rows([{'discord_id': '123', 'main': 'main', 'alt1': 'first', 'alt3': 'third'}])
        self.assertTrue(m.save_alt(123, 'second')[0])
        row = m.get_account(123)
        self.assertEqual([row[f'alt{i}'] for i in (1, 2, 3)], ['first', 'second', 'third'])
        self.assertFalse(m.save_alt(123, 'FIRST')[0])
        self.assertFalse(m.save_alt(123, 'main')[0])

    def test_admin_alt_creation_is_atomic(self):
        self.assertFalse(m.save_alt(123, 'new')[0])
        self.assertFalse(self.path.exists())
        self.assertTrue(m.save_alt(123, 'new', admin=True)[0])
        self.assertEqual(m.get_account(123)['main'], '관리자')
        before = self.path.read_bytes()
        with patch.object(m.os, 'replace', side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError):
                m.save_alt(456, 'other', admin=True)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIsNone(m.get_account(456))

    def test_failed_replace_preserves_original_and_cleans_temp(self):
        m.save_main(123, 'first')
        before = self.path.read_bytes()
        with patch.object(m.os, 'replace', side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError):
                m.save_main(456, 'second')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_bom_and_custom_columns_survive_empty_and_nonempty_data(self):
        self.path.write_text('discord_id,main,alt1,note\n', encoding='utf-8-sig')
        m.save_main(123, '가나다')
        self.assertIn('note', m.get_account(123))
        rows = m.load_rows()
        rows[0]['note'] = 'keep me'
        m.save_rows(rows)
        m.save_alt(123, 'ALT')
        self.assertEqual(m.get_account(123)['note'], 'keep me')
        self.assertTrue(m.delete_user_data(123))
        self.assertEqual(m.load_rows(), [])
        with self.path.open(encoding='utf-8-sig') as stream:
            self.assertIn('note', next(csv.reader(stream)))

    def test_malformed_csv_is_not_overwritten(self):
        for text in ['', 'wrong,main\n123,A\n', 'discord_id,main\n123,A,extra\n',
                     'discord_id,main\n123\n', 'discord_id,main\n123,A\n123,B\n',
                     'discord_id,main\nnot-id,A\n', 'discord_id,main,main\n123,A,B\n']:
            with self.subTest(text=text):
                self.path.write_text(text, encoding='utf-8')
                before = self.path.read_bytes()
                with self.assertRaises(m.DataError):
                    m.save_main(789, 'new')
                self.assertEqual(self.path.read_bytes(), before)

    def test_empty_main_row_is_repaired_without_duplicate(self):
        m.save_rows([{'discord_id': '123', 'main': '', 'alt1': 'existing'}])
        self.assertTrue(m.save_main(123, 'main'))
        self.assertEqual(len(m.load_rows()), 1)
        self.assertEqual(m.get_account(123)['alt1'], 'existing')

    def test_input_validation(self):
        for nick in ['', '   ', '\n', 'A\nB', 'x'*33]:
            with self.assertRaises(ValueError):
                m.validate_nickname(nick, main=True)
        for uid in ['0', '-1', '1.5', 'abc', str(2**64), '１２３']:
            with self.assertRaises(ValueError):
                m.parse_user_id(uid)
        self.assertEqual(m.parse_user_id(' 123 '), 123)

    def test_token_is_isolated_from_other_bot(self):
        with patch.object(m, 'BASE_DIR', self.path.parent), patch.dict(m.os.environ, {'DISCORD_BOT_TOKEN': 'other-bot'}, clear=True):
            with self.assertRaises(RuntimeError):
                m._load_token()
            (self.path.parent/'ml_bot_token.txt').write_text('local-test', encoding='utf-8')
            self.assertEqual(m._load_token(), 'local-test')
            with patch.dict(m.os.environ, {'ML_DISCORD_BOT_TOKEN': 'env-test'}):
                self.assertEqual(m._load_token(), 'env-test')

    def test_startup_exception_is_logged_without_connecting(self):
        previous_level = logging.getLogger().level
        logging.disable(logging.NOTSET)
        try:
            with patch.object(m, 'LOG_DIR', self.path.parent/'Log'), patch.object(m, '_load_token', side_effect=RuntimeError('test-only-startup-error')), patch.object(m.bot, 'run') as run:
                self.assertEqual(m.main(), 1)
                run.assert_not_called()
                text = (m.LOG_DIR/'runtime.log').read_text(encoding='utf-8')
                self.assertIn('test-only-startup-error', text)
                self.assertIn('Traceback', text)
        finally:
            logging.disable(logging.CRITICAL)
            logging.getLogger().setLevel(previous_level)

    def test_normal_exit_does_not_restart(self):
        previous_level = logging.getLogger().level
        try:
            with patch.object(m, 'LOG_DIR', self.path.parent/'Log'), patch.object(m, '_load_token', return_value='test-only'), patch.object(m.bot, 'run') as run:
                self.assertEqual(m.main(), 0)
                run.assert_called_once_with('test-only', reconnect=True, log_handler=None)
        finally:
            logging.getLogger().setLevel(previous_level)


def fake_interaction(uid=123, owner_id=999):
    member = Mock(spec=m.discord.Member)
    member.id = uid
    member.edit = AsyncMock()
    member.add_roles = AsyncMock()
    role = SimpleNamespace(id=m.SIGNUP_ROLE_ID, name='가입자')
    guild = SimpleNamespace(id=10, owner_id=owner_id, get_role=Mock(return_value=role), fetch_member=AsyncMock())
    response = SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock(), is_done=Mock(return_value=False))
    async def defer(**kwargs):
        response.is_done.return_value = True
    response.defer.side_effect = defer
    return SimpleNamespace(user=member, guild=guild, response=response,
                           edit_original_response=AsyncMock(), followup=SimpleNamespace(send=AsyncMock()),
                           command=SimpleNamespace(name='test'))


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'accounts.csv'
        self.patch = patch.object(m, 'CSV_FILE', self.path)
        self.patch.start()
        m._user_locks = [asyncio.Lock() for _ in range(64)]

    async def asyncTearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    async def test_concurrent_main_and_alt_updates(self):
        results = await asyncio.gather(*(asyncio.to_thread(m.save_main, 123, 'A') for _ in range(20)))
        self.assertEqual(sum(results), 1)
        await asyncio.gather(*(asyncio.to_thread(m.save_main, uid, 'B') for uid in range(1000, 1030)))
        self.assertEqual(len(m.load_rows()), 31)
        await asyncio.gather(*(asyncio.to_thread(m.save_alt, 123, f'ALT{i}') for i in range(20)))
        row = m.get_account(123)
        self.assertEqual({row[key] for key in m.get_all_alt_keys([row]) if row[key]}, {f'ALT{i}' for i in range(20)})

    async def test_signup_defers_and_saves_before_remote_changes(self):
        inter = fake_interaction()
        async def check_edit(**kwargs):
            inter.response.defer.assert_awaited_once()
            self.assertEqual(m.get_account(123)['main'], '가나다')
        inter.user.edit.side_effect = check_edit
        await m.signup.callback(inter, '가나다')
        inter.user.edit.assert_awaited_once()
        inter.guild.fetch_member.assert_not_called()
        inter.user.add_roles.assert_awaited_once()

    async def test_failed_save_never_changes_discord_profile(self):
        inter = fake_interaction()
        with patch.object(m, 'save_main', side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError):
                await m.signup.callback(inter, 'test')
        inter.user.edit.assert_not_called()
        inter.user.add_roles.assert_not_called()

    async def test_profile_failure_can_retry_same_signup_without_duplicate(self):
        inter = fake_interaction()
        inter.user.edit.side_effect = m.discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'denied')
        await m.signup.callback(inter, 'test')
        self.assertEqual(m.get_account(123)['main'], 'test')
        self.assertIn('닉네임 변경에 실패', inter.edit_original_response.call_args.kwargs['content'])
        retry = fake_interaction()
        await m.signup.callback(retry, 'test')
        retry.user.edit.assert_awaited_once()
        self.assertEqual(len(m.load_rows()), 1)
        other = fake_interaction()
        await m.signup.callback(other, 'different')
        other.user.edit.assert_not_called()
        self.assertEqual(m.get_account(123)['main'], 'test')

    async def test_owner_and_missing_role_report(self):
        inter = fake_interaction(owner_id=123)
        inter.guild.get_role.return_value = None
        await m.signup.callback(inter, 'test')
        inter.user.edit.assert_not_called()
        content = inter.edit_original_response.call_args.kwargs['content']
        self.assertIn('서버 오너', content)
        self.assertIn('가입 역할을 찾지', content)

    async def test_role_failure_keeps_registration_and_reports_partial_result(self):
        inter = fake_interaction()
        inter.user.add_roles.side_effect = m.discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'denied')
        await m.signup.callback(inter, 'test')
        self.assertEqual(m.get_account(123)['main'], 'test')
        self.assertIn('역할 부여에 실패', inter.edit_original_response.call_args.kwargs['content'])

    async def test_dm_signup_rejected(self):
        inter = fake_interaction()
        inter.guild = None
        await m.signup.callback(inter, 'test')
        inter.response.send_message.assert_awaited_once()
        self.assertFalse(self.path.exists())

    async def test_alt_admin_creation_and_duplicate_response(self):
        inter = fake_interaction(uid=m.ADMIN_ID)
        await m.alt.callback(inter, 'ALT')
        self.assertEqual(m.get_account(m.ADMIN_ID)['alt1'], 'ALT')
        second = fake_interaction(uid=m.ADMIN_ID)
        await m.alt.callback(second, 'ALT')
        self.assertIn('이미', second.edit_original_response.call_args.kwargs['content'])

    async def test_reset_and_lookup_admin_checks(self):
        m.save_main(123, 'original')
        inter = fake_interaction()
        await m.reset.callback(inter, '123')
        self.assertIsNotNone(m.get_account(123))
        inter = fake_interaction()
        await m.lookup.callback(inter, '123')
        inter.edit_original_response.assert_not_called()
        admin = fake_interaction(uid=m.ADMIN_ID)
        await m.reset.callback(admin, '123')
        self.assertIsNone(m.get_account(123))

    async def test_lookup_large_alt_list_remains_private_and_bounded(self):
        row = {'discord_id': '123', 'main': 'main'}
        row.update({f'alt{i}': f'{i}-' + 'a'*100 for i in range(1, 101)})
        m.save_rows([row])
        inter = fake_interaction(uid=m.ADMIN_ID)
        await m.lookup.callback(inter, '123')
        first = inter.edit_original_response.call_args.kwargs['content']
        self.assertLessEqual(len(first), 1900)
        pieces = [first]
        for call in inter.followup.send.call_args_list:
            self.assertTrue(call.kwargs['ephemeral'])
            self.assertLessEqual(len(call.args[0]), 1900)
            pieces.append(call.args[0])
        self.assertIn('100-' + 'a'*100, ''.join(pieces))

    async def test_file_io_does_not_block_gateway_loop(self):
        started, release = threading.Event(), threading.Event()
        original = m.save_alt
        def slow_write(*args, **kwargs):
            started.set()
            release.wait(timeout=2)
            return original(*args, **kwargs)
        inter = fake_interaction(uid=m.ADMIN_ID)
        with patch.object(m, 'save_alt', side_effect=slow_write):
            task = asyncio.create_task(m.alt.callback(inter, 'ALT'))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                self.assertFalse(task.done())
                inter.response.defer.assert_awaited_once()
            finally:
                release.set()
                await task

    async def test_reconnect_does_not_sync_or_create_tasks(self):
        before = set(asyncio.all_tasks())
        fake = SimpleNamespace(user=SimpleNamespace(id=123))
        with patch.object(m, 'bot', fake), patch.object(m.tree, 'sync', new=AsyncMock()) as sync:
            for _ in range(100):
                await m.on_ready()
            sync.assert_not_called()
        self.assertEqual(before, set(asyncio.all_tasks()))

    async def test_sync_failure_is_bounded_and_not_fatal(self):
        with patch.object(m.tree, 'sync', new=AsyncMock(side_effect=OSError('offline'))) as sync, patch.object(m.asyncio, 'sleep', new=AsyncMock()):
            await m.bot.setup_hook()
            self.assertEqual(sync.await_count, 3)

    async def test_error_handler_before_and_after_defer(self):
        inter = fake_interaction()
        await m.on_app_command_error(inter, ValueError('bad input'))
        self.assertTrue(inter.response.send_message.call_args.kwargs['ephemeral'])
        inter = fake_interaction()
        await inter.response.defer(ephemeral=True)
        await m.on_app_command_error(inter, PermissionError('locked'))
        self.assertIn('파일에 접근', inter.edit_original_response.call_args.kwargs['content'])

    async def test_both_stop_aliases_are_admin_only(self):
        with patch.object(m.bot, 'close', new=AsyncMock()) as close:
            for command in [m.stop, m.stop_en]:
                await command.callback(fake_interaction())
            close.assert_not_called()
            for command in [m.stop, m.stop_en]:
                await command.callback(fake_interaction(uid=m.ADMIN_ID))
            self.assertEqual(close.await_count, 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
