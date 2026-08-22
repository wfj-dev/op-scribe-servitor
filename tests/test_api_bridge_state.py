import asyncio

from opscribe.api_bridge import APIStateStore


def test_state_store_link_and_token_lifecycle(tmp_path):
    store = APIStateStore(path=str(tmp_path / "api_state.json"))

    async def _run() -> None:
        link = await store.create_link(
            public_base_url="https://example.invalid",
            redirect_path="/v1/link/callback",
            ttl_seconds=300,
        )
        row = await store.get_link(link["link_id"])
        assert row is not None
        assert row["status"] == "pending"

        token = await store.issue_token_for_user(12345, "Brother Test")
        assert token

        resolved = await store.resolve_token(token)
        assert resolved is not None
        assert resolved.user_id == 12345
        assert resolved.display_name == "Brother Test"

        await store.update_link(
            link["link_id"],
            {
                "status": "linked",
                "user_id": 12345,
                "display_name": "Brother Test",
                "token_value": token,
            },
        )

        first_claim = await store.consume_link_token(link["link_id"])
        assert first_claim is not None
        assert first_claim["token"] == token

        second_claim = await store.consume_link_token(link["link_id"])
        assert second_claim is None

        assert await store.revoke_token(resolved.token_id)
        assert await store.resolve_token(token) is None

    asyncio.run(_run())
