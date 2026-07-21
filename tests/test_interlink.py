import inspect
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch


os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("DISCORD_CLIENT_ID", "123456789012345678")
os.environ.setdefault("DISCORD_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("JSONBIN_API_KEY", "test-jsonbin-key")
os.environ.setdefault("JSONBIN_BIN_ID", "test-jsonbin-bin")
os.environ.setdefault("FLASK_SECRET_KEY", "test-flask-secret")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

import Interlink  # noqa: E402
from interlink_core import (  # noqa: E402
    AGENT_PAGE_SIZE,
    DISABLED_SERVER_IDS_KEY,
    OWNER_IDS_KEY,
    SERVER_ORDER_KEY,
    SERVER_PAGE_SIZE,
    documented_command_names,
    paginate_items,
    partition_guilds,
    reconcile_disabled_server_ids,
    reconcile_guild_order,
    replace_page_selection,
    validate_owner_ids,
    validate_server_order,
)


class FakeGuild:
    def __init__(self, guild_id, name=None, joined_at=None):
        self.id = guild_id
        self.name = name or f"Server {guild_id}"
        self.me = SimpleNamespace(joined_at=joined_at)
        self.icon = None


class FakeBot:
    def __init__(self, guilds=(), ready=True):
        self.guilds = list(guilds)
        self.users = []
        self._ready = ready
        self.user = None

    def is_ready(self):
        return self._ready


class PaginationHelperTests(unittest.TestCase):
    def test_server_page_boundaries(self):
        expected_sizes = {
            0: [],
            1: [1],
            20: [20],
            21: [20, 1],
            40: [20, 20],
            41: [20, 20, 1],
        }
        for item_count, sizes in expected_sizes.items():
            with self.subTest(item_count=item_count):
                pages = paginate_items(list(range(item_count)), SERVER_PAGE_SIZE)
                self.assertEqual([len(page) for page in pages], sizes)
                self.assertTrue(all(len(page) <= 20 for page in pages))

    def test_page_selection_is_preserved_and_replaced_locally(self):
        page_one = set(range(1, 21))
        page_two = set(range(21, 41))

        selected = replace_page_selection(set(), page_one, {1, 2})
        selected = replace_page_selection(selected, page_two, {21, 22})
        self.assertEqual(selected, {1, 2, 21, 22})

        selected = replace_page_selection(selected, page_one, {2})
        self.assertEqual(selected, {2, 21, 22})

    def test_invalid_page_size_is_rejected(self):
        with self.assertRaises(ValueError):
            paginate_items([1], 0)


class ViewPaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_server_view_uses_twenty_item_pages(self):
        guilds = [FakeGuild(index) for index in range(1, 42)]
        agents = [{"id": str(index), "username": f"Agent {index}"} for index in range(1, 27)]
        author = SimpleNamespace(id=1)
        target = SimpleNamespace(id=2, name="Target")

        views = [
            Interlink.ServerSelectView(author, target, guilds),
            Interlink.DeployView(author, guilds, agents),
            Interlink.KickView(author, guilds, agents),
            Interlink.CreateChannelView(author, guilds),
            Interlink.GetIdPaginatedView(author, guilds),
        ]

        for view in views:
            with self.subTest(view=view.__class__.__name__):
                self.assertEqual([len(page) for page in view.guild_pages], [20, 20, 1])
                self.assertTrue(all(len(page) <= SERVER_PAGE_SIZE for page in view.guild_pages))

        self.assertEqual([len(page) for page in views[1].agent_pages], [AGENT_PAGE_SIZE, 1])
        views[0].current_page = 2
        views[0]._rebuild_select()
        self.assertEqual(sum(isinstance(item, Interlink.discord.ui.Select) for item in views[0].children), 1)
        self.assertEqual(len(views[0].guild_select.options), 1)
        self.assertEqual(views[0].guild_select.max_values, 1)

        views[0].selected_guild_ids = {1, 21, 41}
        views[0].current_page = 0
        views[0]._rebuild_select()
        self.assertTrue(next(option for option in views[0].guild_select.options if option.value == "1").default)
        views[0].current_page = 1
        views[0]._rebuild_select()
        self.assertTrue(next(option for option in views[0].guild_select.options if option.value == "21").default)

    async def test_empty_server_lists_do_not_index_an_empty_page(self):
        author = SimpleNamespace(id=1)
        target = SimpleNamespace(id=2, name="Target")
        agents = [{"id": "2", "username": "Agent"}]

        views = [
            Interlink.ServerSelectView(author, target, []),
            Interlink.DeployView(author, [], agents),
            Interlink.KickView(author, [], agents),
            Interlink.CreateChannelView(author, []),
            Interlink.GetIdPaginatedView(author, []),
        ]
        for view in views:
            self.assertEqual(view.guild_pages, [])
            self.assertGreaterEqual(len(view.children), 1)

    async def test_kick_select_all_targets_every_server_not_only_current_page(self):
        guilds = [FakeGuild(index) for index in range(1, 42)]
        author = SimpleNamespace(id=1)
        view = Interlink.KickView(author, guilds, [{"id": "2", "username": "Agent"}])

        class Response:
            async def edit_message(self, **kwargs):
                self.kwargs = kwargs

        interaction = SimpleNamespace(user=author, response=Response())
        select_all = next(item for item in view.children if getattr(item, "label", None) == "Chọn Tất Cả Server")
        await select_all.callback(interaction)
        self.assertEqual(view.selected_guild_ids, {guild.id for guild in guilds})

        clear_all = next(item for item in view.children if getattr(item, "label", None) == "Bỏ chọn Tất Cả")
        await clear_all.callback(interaction)
        self.assertEqual(view.selected_guild_ids, set())

    async def test_author_only_guard_returns_ephemeral_error(self):
        author = SimpleNamespace(id=1)
        messages = []

        class Response:
            async def send_message(self, content, **kwargs):
                messages.append((content, kwargs))

        interaction = SimpleNamespace(user=SimpleNamespace(id=99), response=Response())
        view = Interlink.ServerSelectView(author, SimpleNamespace(id=2, name="Target"), [FakeGuild(1)])
        self.assertFalse(await view.interaction_check(interaction))
        self.assertTrue(messages[0][1]["ephemeral"])


class ServerOrderingTests(unittest.TestCase):
    def setUp(self):
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        self.guilds = [
            FakeGuild(3, joined_at=base + timedelta(days=3)),
            FakeGuild(1, joined_at=base + timedelta(days=1)),
            FakeGuild(2, joined_at=base + timedelta(days=2)),
            FakeGuild(4, joined_at=None),
        ]

    def test_missing_order_uses_stable_join_fallback(self):
        ordered = reconcile_guild_order(self.guilds, None)
        self.assertEqual([guild.id for guild in ordered], [1, 2, 3, 4])

    def test_saved_order_wins_and_new_servers_append(self):
        ordered = reconcile_guild_order(self.guilds, ["3", "1"])
        self.assertEqual([guild.id for guild in ordered], [3, 1, 2, 4])

    def test_duplicate_stale_and_invalid_ids_are_ignored(self):
        ordered = reconcile_guild_order(self.guilds, ["2", "2", "999", None, "bad", 1])
        self.assertEqual([guild.id for guild in ordered], [2, 1, 3, 4])

    def test_disabled_ids_are_normalized_and_guilds_are_partitioned(self):
        disabled_ids = reconcile_disabled_server_ids([2, "2", "999", None, "bad"], [1, 2, 3])
        self.assertEqual(disabled_ids, ["2"])
        enabled, disabled = partition_guilds(self.guilds, disabled_ids)
        self.assertEqual([guild.id for guild in enabled], [3, 1, 4])
        self.assertEqual([guild.id for guild in disabled], [2])

    def test_validate_server_order_normalizes_and_rejects_bad_input(self):
        self.assertEqual(validate_server_order([2, "1"], [1, 2]), ["2", "1"])
        with self.assertRaisesRegex(ValueError, "list"):
            validate_server_order("1", [1])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_server_order(["1", 1], [1])
        with self.assertRaisesRegex(ValueError, "unknown"):
            validate_server_order(["9"], [1])
        with self.assertRaisesRegex(ValueError, "invalid"):
            validate_server_order([True], [1])

    def test_all_server_picker_commands_use_shared_ordering(self):
        command_callbacks = (
            Interlink.invite.callback,
            Interlink.deploy.callback,
            Interlink.kick.callback,
            Interlink.create.callback,
            Interlink.getid.callback,
        )
        for callback in command_callbacks:
            with self.subTest(command=callback.__name__):
                self.assertIn("get_ordered_guilds", inspect.getsource(callback))

    def test_all_server_bulk_commands_use_active_guild_filter(self):
        command_callbacks = (
            Interlink.add_me.callback,
            Interlink.force_add.callback,
            Interlink.setupadmin.callback,
            Interlink.status.callback,
        )
        for callback in command_callbacks:
            with self.subTest(command=callback.__name__):
                self.assertIn("get_ordered_guilds", inspect.getsource(callback))


class ActiveGuildTests(unittest.IsolatedAsyncioTestCase):
    async def test_ordered_guilds_excludes_disabled_servers(self):
        guilds = [FakeGuild(1), FakeGuild(2), FakeGuild(3)]
        Interlink.set_cached_server_state(["3", "2", "1"], ["2"])
        with patch.object(Interlink, "bot", FakeBot(guilds, ready=True)):
            active = await Interlink.get_ordered_guilds()
        self.assertEqual([guild.id for guild in active], [3, 1])


class OwnerIdTests(unittest.TestCase):
    def test_owner_ids_accept_multiple_values_and_normalize_integers(self):
        self.assertEqual(validate_owner_ids([123, "456"]), ["123", "456"])
        self.assertEqual(validate_owner_ids(["000123"]), ["123"])

    def test_owner_ids_reject_empty_invalid_duplicate_and_out_of_range_values(self):
        invalid_values = (
            ([], "at least one"),
            ("123", "list"),
            ([True], "invalid"),
            (["abc"], "invalid"),
            ([0], "invalid"),
            ([2**64], "invalid"),
            (["123", 123], "duplicate"),
        )
        for owner_ids, message in invalid_values:
            with self.subTest(owner_ids=owner_ids):
                with self.assertRaisesRegex(ValueError, message):
                    validate_owner_ids(owner_ids)

    def test_invalid_stored_owner_metadata_falls_back_to_legacy_owner(self):
        self.assertEqual(Interlink.normalize_stored_owner_ids(None), list(Interlink.DEFAULT_OWNER_IDS))
        self.assertEqual(Interlink.normalize_stored_owner_ids([]), list(Interlink.DEFAULT_OWNER_IDS))

    def test_metadata_refresh_applies_saved_owner_ids(self):
        fake_bot = FakeBot([FakeGuild(10), FakeGuild(20)], ready=True)
        metadata = {
            SERVER_ORDER_KEY: ["20", "10"],
            DISABLED_SERVER_IDS_KEY: ["10", "999"],
            OWNER_IDS_KEY: ["123", "456"],
        }
        with patch.object(Interlink, "bot", fake_bot), \
             patch.object(Interlink.jsonbin_storage, "read_data_with_status", return_value=(True, metadata)):
            self.assertTrue(Interlink.refresh_server_order_cache())
        self.assertEqual(fake_bot.owner_ids, {123, 456})
        self.assertEqual(Interlink.get_cached_disabled_server_ids()[0], ["10"])


class OwnerPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_configured_owner_passes_discord_owner_check(self):
        previous_owner_id = Interlink.bot.owner_id
        previous_owner_ids = Interlink.bot.owner_ids
        try:
            Interlink.set_bot_owner_ids(["123", "456"])
            self.assertTrue(await Interlink.bot.is_owner(SimpleNamespace(id=123)))
            self.assertTrue(await Interlink.bot.is_owner(SimpleNamespace(id=456)))
            self.assertFalse(await Interlink.bot.is_owner(SimpleNamespace(id=789)))
        finally:
            Interlink.bot.owner_id = previous_owner_id
            Interlink.bot.owner_ids = previous_owner_ids


class JSONBinMetadataTests(unittest.TestCase):
    def setUp(self):
        self.storage = Interlink.JSONBinStorage()
        self.storage.api_key = "key"
        self.storage.bin_id = "bin"
        self.original = {
            "123": {"access_token": "secret-token", "username": "Agent"},
            "_roster_order": ["123"],
            DISABLED_SERVER_IDS_KEY: ["789"],
            OWNER_IDS_KEY: ["456"],
            "tracked_channels": {"1": ["2"]},
            "unknown_metadata": {"keep": True},
        }

    def test_set_metadata_preserves_all_other_keys(self):
        written = []
        with patch.object(self.storage, "read_data_with_status", return_value=(True, self.original.copy())), \
             patch.object(self.storage, "write_data", side_effect=lambda data: written.append(data) or True):
            self.assertTrue(self.storage.set_metadata(SERVER_ORDER_KEY, ["9", "8"]))

        self.assertEqual(len(written), 1)
        self.assertEqual(written[0][SERVER_ORDER_KEY], ["9", "8"])
        for key, value in self.original.items():
            self.assertEqual(written[0][key], value)

    def test_read_or_write_failure_is_reported(self):
        with patch.object(self.storage, "read_data_with_status", return_value=(False, {})):
            self.assertFalse(self.storage.set_metadata(SERVER_ORDER_KEY, []))
        with patch.object(self.storage, "read_data_with_status", return_value=(True, self.original.copy())), \
             patch.object(self.storage, "write_data", return_value=False):
            self.assertFalse(self.storage.set_metadata(SERVER_ORDER_KEY, []))


class AdminRoutesTests(unittest.TestCase):
    def setUp(self):
        Interlink.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = Interlink.app.test_client()
        Interlink.set_cached_server_state([], [])

    def login(self):
        with self.client.session_transaction() as session:
            session["logged_in"] = True

    def test_api_requires_login(self):
        response = self.client.post("/admin/api/reorder-servers", json={"order": []})
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.get_json()["success"])

    def test_server_api_validates_schema_duplicates_unknown_and_readiness(self):
        self.login()
        ready_bot = FakeBot([FakeGuild(1), FakeGuild(2)], ready=True)
        with patch.object(Interlink, "bot", ready_bot), \
             patch.object(Interlink.jsonbin_storage, "set_metadata", return_value=True):
            self.assertEqual(self.client.post("/admin/api/reorder-servers", data="x").status_code, 415)
            self.assertEqual(self.client.post("/admin/api/reorder-servers", json=[]).status_code, 400)
            self.assertEqual(self.client.post("/admin/api/reorder-servers", json={"order": [], "extra": 1}).status_code, 400)
            self.assertEqual(self.client.post("/admin/api/reorder-servers", json={"order": ["1", 1]}).status_code, 400)
            self.assertEqual(self.client.post("/admin/api/reorder-servers", json={"order": ["9"]}).status_code, 400)

        with patch.object(Interlink, "bot", FakeBot([FakeGuild(1)], ready=False)):
            self.assertEqual(self.client.post("/admin/api/reorder-servers", json={"order": ["1"]}).status_code, 503)

    def test_server_api_success_updates_storage_and_cache(self):
        self.login()
        fake_bot = FakeBot([FakeGuild(1), FakeGuild(2)], ready=True)
        with patch.object(Interlink, "bot", fake_bot), \
             patch.object(Interlink.jsonbin_storage, "set_metadata", return_value=True) as save:
            response = self.client.post("/admin/api/reorder-servers", json={"order": [2, "1"]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["order"], ["2", "1"])
        save.assert_called_once_with(SERVER_ORDER_KEY, ["2", "1"])
        self.assertEqual(Interlink.get_cached_server_order()[0], ["2", "1"])

    def test_server_api_reports_storage_failure(self):
        self.login()
        with patch.object(Interlink, "bot", FakeBot([FakeGuild(1)], ready=True)), \
             patch.object(Interlink.jsonbin_storage, "set_metadata", return_value=False):
            response = self.client.post("/admin/api/reorder-servers", json={"order": ["1"]})
        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.get_json()["success"])

    def test_server_state_api_requires_login_and_validates_payload(self):
        response = self.client.post("/admin/api/server-state", json={"server_id": "1", "enabled": False})
        self.assertEqual(response.status_code, 401)

        self.login()
        ready_bot = FakeBot([FakeGuild(1)], ready=True)
        with patch.object(Interlink, "bot", ready_bot):
            self.assertEqual(self.client.post("/admin/api/server-state", data="x").status_code, 415)
            self.assertEqual(self.client.post("/admin/api/server-state", json={"server_id": "1"}).status_code, 400)
            self.assertEqual(
                self.client.post("/admin/api/server-state", json={"server_id": "1", "enabled": "false"}).status_code,
                400,
            )
            self.assertEqual(
                self.client.post("/admin/api/server-state", json={"server_id": "bad", "enabled": False}).status_code,
                400,
            )
            self.assertEqual(
                self.client.post("/admin/api/server-state", json={"server_id": "9", "enabled": False}).status_code,
                400,
            )
            self.assertEqual(
                self.client.post(
                    "/admin/api/server-state",
                    json={"server_id": "1", "enabled": False, "extra": True},
                ).status_code,
                400,
            )

        with patch.object(Interlink, "bot", FakeBot([FakeGuild(1)], ready=False)):
            response = self.client.post("/admin/api/server-state", json={"server_id": "1", "enabled": False})
        self.assertEqual(response.status_code, 503)

    def test_server_state_api_disables_and_reenables_without_losing_metadata(self):
        self.login()
        fake_bot = FakeBot([FakeGuild(1), FakeGuild(2)], ready=True)
        data = {
            "123": {"access_token": "keep-secret"},
            "_roster_order": ["123"],
            SERVER_ORDER_KEY: ["2", "1"],
            "tracked_channels": {"keep": True},
            "unknown": [1, 2, 3],
        }

        def update(updater):
            updater(data)
            return True

        with patch.object(Interlink, "bot", fake_bot), \
             patch.object(Interlink.jsonbin_storage, "update_data", side_effect=update):
            disabled = self.client.post(
                "/admin/api/server-state",
                json={"server_id": 2, "enabled": False},
            )
            self.assertEqual(disabled.status_code, 200)
            self.assertEqual(disabled.get_json()["disabled_server_ids"], ["2"])
            self.assertEqual(Interlink.get_cached_disabled_server_ids()[0], ["2"])
            self.assertEqual(data[DISABLED_SERVER_IDS_KEY], ["2"])
            self.assertEqual(data["123"]["access_token"], "keep-secret")
            self.assertEqual(data["_roster_order"], ["123"])
            self.assertEqual(data["tracked_channels"], {"keep": True})
            self.assertEqual(data["unknown"], [1, 2, 3])

            enabled = self.client.post(
                "/admin/api/server-state",
                json={"server_id": "2", "enabled": True},
            )
            self.assertEqual(enabled.status_code, 200)
            self.assertEqual(enabled.get_json()["disabled_server_ids"], [])
            self.assertEqual(Interlink.get_cached_disabled_server_ids()[0], [])

    def test_server_state_api_does_not_update_cache_when_storage_fails(self):
        self.login()
        Interlink.set_cached_disabled_server_ids([])
        with patch.object(Interlink, "bot", FakeBot([FakeGuild(1)], ready=True)), \
             patch.object(Interlink.jsonbin_storage, "update_data", return_value=False):
            response = self.client.post(
                "/admin/api/server-state",
                json={"server_id": "1", "enabled": False},
            )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(Interlink.get_cached_disabled_server_ids()[0], [])

    def test_reorder_api_rejects_disabled_server_ids(self):
        self.login()
        Interlink.set_cached_disabled_server_ids(["2"])
        with patch.object(Interlink, "bot", FakeBot([FakeGuild(1), FakeGuild(2)], ready=True)):
            response = self.client.post("/admin/api/reorder-servers", json={"order": ["2"]})
        self.assertEqual(response.status_code, 400)

    def test_owner_api_requires_login_and_validates_exact_schema(self):
        response = self.client.post("/admin/api/owner-ids", json={"owner_ids": ["1"]})
        self.assertEqual(response.status_code, 401)

        self.login()
        invalid_payloads = (
            ({"owner_ids": []}, 400),
            ({"owner_ids": "1"}, 400),
            ({"owner_ids": ["1", 1]}, 400),
            ({"owner_ids": ["bad"]}, 400),
            ({"owner_ids": ["1"], "extra": True}, 400),
        )
        with patch.object(Interlink.jsonbin_storage, "set_metadata", return_value=True):
            for payload, expected_status in invalid_payloads:
                with self.subTest(payload=payload):
                    response = self.client.post("/admin/api/owner-ids", json=payload)
                    self.assertEqual(response.status_code, expected_status)

    def test_owner_api_persists_multiple_ids_and_applies_them_immediately(self):
        self.login()
        fake_bot = FakeBot(ready=True)
        with patch.object(Interlink, "bot", fake_bot), \
             patch.object(Interlink.jsonbin_storage, "set_metadata", return_value=True) as save:
            response = self.client.post("/admin/api/owner-ids", json={"owner_ids": [123, "456"]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["owner_ids"], ["123", "456"])
        save.assert_called_once_with(OWNER_IDS_KEY, ["123", "456"])
        self.assertEqual(fake_bot.owner_ids, {123, 456})
        self.assertIsNone(fake_bot.owner_id)

    def test_owner_api_does_not_apply_ids_when_storage_write_fails(self):
        self.login()
        fake_bot = FakeBot(ready=True)
        fake_bot.owner_id = None
        fake_bot.owner_ids = {999}
        with patch.object(Interlink, "bot", fake_bot), \
             patch.object(Interlink.jsonbin_storage, "set_metadata", return_value=False):
            response = self.client.post("/admin/api/owner-ids", json={"owner_ids": [123, 456]})

        self.assertEqual(response.status_code, 502)
        self.assertEqual(fake_bot.owner_ids, {999})

    def test_account_api_still_uses_old_route_and_metadata_key(self):
        self.login()
        data = {"1": {"access_token": "a"}, "2": {"access_token": "b"}}
        with patch.object(Interlink.jsonbin_storage, "read_data_with_status", return_value=(True, data)), \
             patch.object(Interlink.jsonbin_storage, "set_metadata", return_value=True) as save:
            response = self.client.post("/admin/api/reorder", json={"order": ["2", "1"]})
        self.assertEqual(response.status_code, 200)
        save.assert_called_once_with("_roster_order", ["2", "1"])

    def test_dashboard_has_separate_lists_and_does_not_render_tokens(self):
        self.login()
        data = {
            "1": {"access_token": "must-not-appear", "username": "Agent One"},
            "_roster_order": ["1"],
            SERVER_ORDER_KEY: ["2", "1"],
            DISABLED_SERVER_IDS_KEY: ["1"],
            OWNER_IDS_KEY: ["111", "222"],
        }
        fake_bot = FakeBot([FakeGuild(1, "One"), FakeGuild(2, "Two")], ready=True)
        with patch.object(Interlink, "bot", fake_bot), \
             patch.object(Interlink.jsonbin_storage, "read_data_with_status", return_value=(True, data)):
            response = self.client.get("/admin/dashboard")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="roster-list"', html)
        self.assertIn('id="server-list"', html)
        self.assertIn('id="disabled-server-list"', html)
        self.assertIn('id="owner-list"', html)
        self.assertIn("/admin/api/reorder'", html)
        self.assertIn("/admin/api/reorder-servers'", html)
        self.assertIn("/admin/api/server-state'", html)
        self.assertIn("/admin/api/owner-ids'", html)
        self.assertIn('["111", "222"]', html)
        self.assertNotIn("must-not-appear", html)
        active_server_html, disabled_server_html = html.split('id="disabled-server-list"', 1)
        active_server_html = active_server_html.split('id="server-list"', 1)[1]
        self.assertIn(">Two<", active_server_html)
        self.assertNotIn(">One<", active_server_html)
        self.assertIn(">One<", disabled_server_html)
        self.assertIn("▶️ Bật lại", disabled_server_html)

    def test_dashboard_survives_storage_failure(self):
        self.login()
        with patch.object(Interlink, "bot", FakeBot([FakeGuild(1)], ready=True)), \
             patch.object(Interlink.jsonbin_storage, "read_data_with_status", return_value=(False, {})):
            response = self.client.get("/admin/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn("JSONBin: không khả dụng", response.get_data(as_text=True))

    def test_login_health_and_logout_smoke(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/callback").status_code, 400)

        login_response = self.client.post("/admin/login", data={"password": "test-admin-password"})
        self.assertEqual(login_response.status_code, 302)
        self.assertIn("/admin/dashboard", login_response.headers["Location"])

        with patch.object(Interlink, "bot", FakeBot([FakeGuild(1)], ready=True)), \
             patch.object(Interlink, "get_db_connection", return_value=None), \
             patch.object(Interlink.jsonbin_storage, "read_data_with_status", return_value=(True, {})):
            health_response = self.client.get("/health")
        self.assertEqual(health_response.status_code, 200)
        self.assertEqual(health_response.get_json()["status"], "ok")

        logout_response = self.client.get("/admin/logout")
        self.assertEqual(logout_response.status_code, 302)


class HelpTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def embed_character_count(embed):
        data = embed.to_dict()
        count = len(data.get("title", "")) + len(data.get("description", ""))
        count += len(data.get("footer", {}).get("text", ""))
        count += len(data.get("author", {}).get("name", ""))
        for field in data.get("fields", []):
            count += len(field.get("name", "")) + len(field.get("value", ""))
        return count

    async def test_help_documents_every_active_prefix_command(self):
        active_names = {command.name for command in Interlink.bot.commands}
        self.assertEqual(documented_command_names(), active_names)
        self.assertNotIn("track", documented_command_names())
        self.assertNotIn("untrack", documented_command_names())

    async def test_help_embeds_stay_within_discord_limits(self):
        for category in ("overview", "public", "manage", "owner"):
            embed = Interlink.build_help_embed(category, is_owner=True)
            data = embed.to_dict()
            with self.subTest(category=category):
                self.assertLessEqual(len(data.get("fields", [])), 25)
                self.assertLessEqual(self.embed_character_count(embed), 6000)
                for field in data.get("fields", []):
                    self.assertLessEqual(len(field["name"]), 256)
                    self.assertLessEqual(len(field["value"]), 1024)

    async def test_owner_category_is_hidden_from_non_owner(self):
        author = SimpleNamespace(id=1)
        public_view = Interlink.HelpView(author, is_owner=False)
        owner_view = Interlink.HelpView(author, is_owner=True)
        public_values = [option.value for option in public_view.children[0].options]
        owner_values = [option.value for option in owner_view.children[0].options]
        self.assertNotIn("owner", public_values)
        self.assertIn("owner", owner_values)

        denied_embed = Interlink.build_help_embed("owner", is_owner=False)
        self.assertIn("Hướng dẫn sử dụng", denied_embed.title)

    async def test_prefix_and_slash_help_use_shared_builder(self):
        prefix_source = inspect.getsource(Interlink.help.callback)
        slash_source = inspect.getsource(Interlink.help_slash.callback)
        self.assertIn("build_help_embed", prefix_source)
        self.assertIn("build_help_embed", slash_source)


if __name__ == "__main__":
    unittest.main()
