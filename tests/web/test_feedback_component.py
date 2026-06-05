"""Feedback button + modal (Spec 23 § 23.3, client side).

Pins the <feedback-button> component behaviour:

- renders nothing without a sessionToken (logged-out users),
- the launcher opens a dialog with a type <select> and a <textarea>,
- a valid submit dispatches `feedback-submit` with {type, text},
- short text is rejected locally (no event, inline error),
- onResult(true) shows the thank-you state; onResult(false, msg) shows the error.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.async_api import Page

from .conftest import FakeWireServer

pytestmark = pytest.mark.asyncio


async def _mount(page: Page, server: FakeWireServer, props: dict[str, Any]) -> None:
    """Mount a fresh <feedback-button>, apply props, expose it as window.__fb."""
    await page.goto(server.url)
    await page.wait_for_load_state("domcontentloaded")
    await page.evaluate(
        """async (props) => {
          await import('/static/feedback.js');
          await customElements.whenDefined('feedback-button');
          const el = document.createElement('feedback-button');
          window.__fbEvents = [];
          el.addEventListener('feedback-submit', (e) => window.__fbEvents.push(e.detail));
          document.body.appendChild(el);
          Object.assign(el, props);
          await el.updateComplete;
          window.__fb = el;
        }""",
        props,
    )


async def test_no_button_without_session_token(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await _mount(page, fake_wire_server, {})
    has_launcher = await page.evaluate(
        "() => !!window.__fb.renderRoot.querySelector('.launcher')"
    )
    assert has_launcher is False


async def test_launcher_visible_when_logged_in(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await _mount(page, fake_wire_server, {"sessionToken": "s_abc"})
    text = await page.evaluate(
        "() => window.__fb.renderRoot.querySelector('.launcher')?.textContent?.trim()"
    )
    assert text == "[feedback]"


async def test_open_dialog_shows_controls(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await _mount(page, fake_wire_server, {"sessionToken": "s_abc"})
    controls = await page.evaluate(
        """async () => {
          window.__fb.renderRoot.querySelector('.launcher').click();
          await window.__fb.updateComplete;
          const r = window.__fb.renderRoot;
          return {
            hasSelect: !!r.querySelector('select'),
            hasTextarea: !!r.querySelector('textarea'),
            options: [...r.querySelectorAll('select option')].map(o => o.value),
          };
        }"""
    )
    assert controls["hasSelect"] is True
    assert controls["hasTextarea"] is True
    assert controls["options"] == ["bug", "feature"]


async def test_valid_submit_dispatches_event(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await _mount(page, fake_wire_server, {"sessionToken": "s_abc"})
    events = await page.evaluate(
        """async () => {
          const el = window.__fb;
          el.renderRoot.querySelector('.launcher').click();
          await el.updateComplete;
          el._type = 'feature';
          el._text = 'Please add a spectator chat window.';
          await el.updateComplete;
          el.renderRoot.querySelector('button.act').click();
          await el.updateComplete;
          return window.__fbEvents;
        }"""
    )
    assert len(events) == 1
    assert events[0]["type"] == "feature"
    assert events[0]["text"] == "Please add a spectator chat window."


async def test_repeat_submit_sends_only_once(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """Spec 29 Bug E: clicking Submit repeatedly must dispatch exactly one
    FEEDBACK event (the cause of the three duplicate reports)."""
    await _mount(page, fake_wire_server, {"sessionToken": "s_abc"})
    count = await page.evaluate(
        """async () => {
          const el = window.__fb;
          el.renderRoot.querySelector('.launcher').click();
          await el.updateComplete;
          el._text = 'A perfectly valid bug report here.';
          await el.updateComplete;
          // Fire the handler several times synchronously, before any re-render
          // could disable the button — the idempotency guard must hold.
          el._onSubmit();
          el._onSubmit();
          el._onSubmit();
          await el.updateComplete;
          return window.__fbEvents.length;
        }"""
    )
    assert count == 1


async def test_ack_auto_closes_and_clears(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    """Spec 29 Bug E: a successful ACK auto-closes the dialog and clears the
    draft, so the user can't sit on a lingering modal and re-submit."""
    await _mount(page, fake_wire_server, {"sessionToken": "s_abc"})
    result = await page.evaluate(
        """async () => {
          const el = window.__fb;
          el.renderRoot.querySelector('.launcher').click();
          await el.updateComplete;
          el._text = 'A perfectly valid bug report here.';
          await el.updateComplete;
          el.renderRoot.querySelector('button.act').click();
          await el.updateComplete;
          el.onResult(true);
          await el.updateComplete;
          const sawDone = !!el.renderRoot.querySelector('.done');
          await new Promise((r) => setTimeout(r, 1600)); // past the auto-close
          await el.updateComplete;
          return { sawDone, open: el._open, text: el._text, phase: el._phase };
        }"""
    )
    assert result["sawDone"] is True
    assert result["open"] is False
    assert result["text"] == ""
    assert result["phase"] == "draft"


async def test_short_text_rejected_locally(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await _mount(page, fake_wire_server, {"sessionToken": "s_abc"})
    result = await page.evaluate(
        """async () => {
          const el = window.__fb;
          el.renderRoot.querySelector('.launcher').click();
          await el.updateComplete;
          el._text = 'bad';
          await el.updateComplete;
          el.renderRoot.querySelector('button.act').click();
          await el.updateComplete;
          return {
            events: window.__fbEvents.length,
            phase: el._phase,
            hasError: !!el.renderRoot.querySelector('.error'),
          };
        }"""
    )
    assert result["events"] == 0
    assert result["phase"] == "error"
    assert result["hasError"] is True


async def test_on_result_success_shows_thanks(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await _mount(page, fake_wire_server, {"sessionToken": "s_abc"})
    done_text = await page.evaluate(
        """async () => {
          const el = window.__fb;
          el.renderRoot.querySelector('.launcher').click();
          await el.updateComplete;
          el._text = 'A perfectly valid bug report here.';
          await el.updateComplete;
          el.renderRoot.querySelector('button.act').click();
          await el.updateComplete;
          el.onResult(true);
          await el.updateComplete;
          return el.renderRoot.querySelector('.done')?.textContent?.trim();
        }"""
    )
    assert "Feedback received" in done_text


async def test_on_result_failure_shows_error(
    page: Page, fake_wire_server: FakeWireServer
) -> None:
    await _mount(page, fake_wire_server, {"sessionToken": "s_abc"})
    err_text = await page.evaluate(
        """async () => {
          const el = window.__fb;
          el.renderRoot.querySelector('.launcher').click();
          await el.updateComplete;
          el._text = 'A perfectly valid bug report here.';
          await el.updateComplete;
          el.renderRoot.querySelector('button.act').click();
          await el.updateComplete;
          el.onResult(false, 'text too short');
          await el.updateComplete;
          return el.renderRoot.querySelector('.error')?.textContent?.trim();
        }"""
    )
    assert "text too short" in err_text
