"""Unit tests for ConsentDismisser and the consent step of ``BrowserSession.navigate``."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from myrm_agent_harness.toolkits.browser.pool.config import BrowserEngine
from myrm_agent_harness.toolkits.browser.session.browser_session_navigation_mixin import (
    BrowserSessionNavigationMixin,
)
from myrm_agent_harness.toolkits.browser.session.consent_dismisser import ConsentDismisser


@pytest.fixture
def mock_page() -> MagicMock:
    page = MagicMock()
    page.evaluate = AsyncMock()
    return page


@pytest.fixture
def dismisser() -> ConsentDismisser:
    return ConsentDismisser(enabled=True)


@pytest.fixture
def disabled_dismisser() -> ConsentDismisser:
    return ConsentDismisser(enabled=False)


class TestConsentDismisserInit:
    def test_default_enabled(self) -> None:
        d = ConsentDismisser()
        assert d.enabled is True

    def test_explicit_disabled(self) -> None:
        d = ConsentDismisser(enabled=False)
        assert d.enabled is False

    def test_toggle_enabled(self) -> None:
        d = ConsentDismisser(enabled=True)
        d.enabled = False
        assert d.enabled is False


class TestDismissDisabled:
    @pytest.mark.asyncio
    async def test_returns_none_when_disabled(self, disabled_dismisser: ConsentDismisser, mock_page: MagicMock) -> None:
        result = await disabled_dismisser.dismiss(mock_page)
        assert result is None
        mock_page.evaluate.assert_not_called()


class TestDismissSuccess:
    @pytest.mark.asyncio
    async def test_returns_message_on_cmp_selector(self, dismisser: ConsentDismisser, mock_page: MagicMock) -> None:
        mock_page.evaluate.return_value = {"dismissed": True, "method": "cmp_selector"}
        result = await dismisser.dismiss(mock_page)
        assert result is not None
        assert "cmp_selector" in result
        mock_page.evaluate.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_message_on_text_match(self, dismisser: ConsentDismisser, mock_page: MagicMock) -> None:
        mock_page.evaluate.return_value = {"dismissed": True, "method": "text_match"}
        result = await dismisser.dismiss(mock_page)
        assert result is not None
        assert "text_match" in result

    @pytest.mark.asyncio
    async def test_returns_message_on_shadow_dom(self, dismisser: ConsentDismisser, mock_page: MagicMock) -> None:
        mock_page.evaluate.return_value = {"dismissed": True, "method": "shadow_dom"}
        result = await dismisser.dismiss(mock_page)
        assert result is not None
        assert "shadow_dom" in result

    @pytest.mark.asyncio
    async def test_returns_message_on_api_call(self, dismisser: ConsentDismisser, mock_page: MagicMock) -> None:
        mock_page.evaluate.return_value = {"dismissed": True, "method": "didomi_api"}
        result = await dismisser.dismiss(mock_page)
        assert result is not None
        assert "didomi_api" in result

    @pytest.mark.asyncio
    async def test_returns_message_on_container_removal(
        self, dismisser: ConsentDismisser, mock_page: MagicMock
    ) -> None:
        mock_page.evaluate.return_value = {"dismissed": True, "method": "container_removal"}
        result = await dismisser.dismiss(mock_page)
        assert result is not None
        assert "container_removal" in result


class TestDismissNoConsent:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_consent_found(self, dismisser: ConsentDismisser, mock_page: MagicMock) -> None:
        mock_page.evaluate.return_value = {"dismissed": False, "method": None}
        result = await dismisser.dismiss(mock_page)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_result(self, dismisser: ConsentDismisser, mock_page: MagicMock) -> None:
        mock_page.evaluate.return_value = None
        result = await dismisser.dismiss(mock_page)
        assert result is None


class TestDismissErrors:
    @pytest.mark.asyncio
    async def test_returns_none_on_js_exception(self, dismisser: ConsentDismisser, mock_page: MagicMock) -> None:
        mock_page.evaluate.side_effect = Exception("Page closed")
        result = await dismisser.dismiss(mock_page)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self, dismisser: ConsentDismisser, mock_page: MagicMock) -> None:
        mock_page.evaluate.side_effect = TimeoutError("Evaluation timed out")
        result = await dismisser.dismiss(mock_page)
        assert result is None


class _NavigateProbe(BrowserSessionNavigationMixin):
    """The collaborators ``navigate`` touches for a loaded page without a challenge; the rest is stubbed."""

    def __init__(self, dismisser: ConsentDismisser, page: MagicMock) -> None:
        self._domain_blocklist = None
        self._terminal_challenges: dict[str, float] = {}
        self._allow_private_networks = True
        self._extension_bridge = None
        self._engine_preference = BrowserEngine.CHROMIUM_PATCHRIGHT
        self._pending_tab_reset = None
        self._captcha_coordinator = None
        self._consent_dismisser = dismisser
        self._tab_controller = MagicMock()
        self._tab_controller.list_tabs.return_value = ["tab"]
        self._tab_controller.get_active_page.return_value = page
        self._navigator = MagicMock()
        self._navigator.goto = AsyncMock(return_value=("Example", "https://example.com/", 200))
        self._snapshot_manager = MagicMock()
        self._ensure_not_user_takeover = AsyncMock()
        self._ensure_components = AsyncMock()
        self._append_navigate_interactive_summary = AsyncMock(side_effect=lambda result: result)
        self._publish_inspector_view = AsyncMock()
        self._get_site_experience_hint = lambda url: ""

    def _require_navigator(self) -> MagicMock:
        return self._navigator

    def _require_snapshot_manager(self) -> MagicMock:
        return self._snapshot_manager


class TestNavigateConsentStep:
    @pytest.mark.asyncio
    async def test_dismissed_banner_is_reported_before_the_snapshot_baseline(self, mock_page: MagicMock) -> None:
        order: list[str] = []

        async def dismiss_script(_script: str) -> dict[str, object]:
            order.append("dismiss")
            return {"dismissed": True, "method": "cmp-selector"}

        mock_page.evaluate = AsyncMock(side_effect=dismiss_script)
        probe = _NavigateProbe(ConsentDismisser(enabled=True), mock_page)
        probe._snapshot_manager.reset_diff_baseline.side_effect = lambda: order.append("baseline")

        result = await probe.navigate("https://example.com/")

        assert "Navigated to https://example.com/ (status=200, title=Example)" in result
        assert "[Cookie consent auto-accepted via cmp-selector]" in result
        assert order == ["dismiss", "baseline"]

    @pytest.mark.asyncio
    async def test_page_without_a_banner_adds_nothing(self, mock_page: MagicMock) -> None:
        mock_page.evaluate = AsyncMock(return_value={"dismissed": False})
        probe = _NavigateProbe(ConsentDismisser(enabled=True), mock_page)

        result = await probe.navigate("https://example.com/")

        mock_page.evaluate.assert_awaited_once()
        assert "Cookie consent" not in result

    @pytest.mark.asyncio
    async def test_disabled_dismisser_never_touches_the_page(self, mock_page: MagicMock) -> None:
        probe = _NavigateProbe(ConsentDismisser(enabled=False), mock_page)

        result = await probe.navigate("https://example.com/")

        mock_page.evaluate.assert_not_called()
        assert "Cookie consent" not in result
