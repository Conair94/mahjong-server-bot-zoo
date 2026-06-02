// Feedback button + modal — Spec 23 (feedback-reporting.md § 23.3).
//
// A small fixed-position [feedback] link opens a <dialog> with a bug/feature
// <select> and a <textarea>.  On submit it dispatches a `feedback-submit`
// CustomEvent ({type, text}) up to <mahjong-app>, which owns the WS connection
// and sends the FEEDBACK frame.  When the server replies (FEEDBACK_ACK or
// ERROR), the app calls back into this component via `onResult(ok, message)`.
//
// The component renders nothing until `sessionToken` is set — only logged-in
// players can submit (§ 23.3 open question 3 → resolved "yes").

import { LitElement, html, css } from "lit";

const MAX_LEN = 1000;

class FeedbackButton extends LitElement {
  static properties = {
    sessionToken: { type: String },
    _open: { state: true },        // dialog visibility
    _type: { state: true },        // "bug" | "feature"
    _text: { state: true },        // textarea draft
    _phase: { state: true },       // "draft" | "submitting" | "done" | "error"
    _errorMsg: { state: true },    // inline error string
  };

  static styles = css`
    .launcher {
      position: fixed;
      bottom: 0.5rem;
      right: 0.75rem;
      z-index: 1000;
      background: transparent;
      border: none;
      color: var(--fg-dim);
      font-family: inherit;
      font-size: inherit;
      cursor: pointer;
      padding: 0.25rem 0.5rem;
    }
    .launcher:hover { color: var(--accent); }

    dialog {
      background: var(--bg);
      color: var(--fg);
      border: 1px solid var(--accent);
      font-family: inherit;
      font-size: inherit;
      padding: 1rem 1.5rem 1.25rem;
      max-width: 440px;
      width: 90vw;
    }
    dialog::backdrop { background: rgba(0, 0, 0, 0.5); }
    .title { color: var(--accent); margin-bottom: 0.75rem; }
    .row { display: flex; flex-direction: column; gap: 0.25rem; margin-bottom: 0.6rem; }
    .label { color: var(--fg-dim); font-size: 0.9em; }
    select, textarea {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--fg);
      font-family: inherit;
      font-size: inherit;
      padding: 0.3rem 0.5rem;
      width: 100%;
      box-sizing: border-box;
    }
    select:focus, textarea:focus { outline: none; border-color: var(--accent); }
    textarea { resize: vertical; }
    .charcount { color: var(--fg-dim); font-size: 0.8em; text-align: right; }
    .actions { display: flex; gap: 0.75rem; margin-top: 0.5rem; }
    button.act {
      background: transparent;
      border: 1px solid var(--accent);
      color: var(--accent);
      font-family: inherit;
      font-size: inherit;
      padding: 0.25rem 1rem;
      cursor: pointer;
    }
    button.act:hover:not(:disabled) { background: var(--accent); color: var(--bg); }
    button.act:disabled { opacity: 0.5; cursor: default; }
    button.secondary { border-color: var(--border); color: var(--fg-dim); }
    button.secondary:hover { color: var(--accent); border-color: var(--accent); background: transparent; }
    .error { color: var(--error); margin: 0.5rem 0; padding: 0.4rem 0.75rem; border: 1px solid var(--error); }
    .done { color: var(--accent); margin: 0.5rem 0; }
  `;

  constructor() {
    super();
    this.sessionToken = null;
    this._open = false;
    this._type = "bug";
    this._text = "";
    this._phase = "draft";
    this._errorMsg = null;
  }

  // Called by <mahjong-app> when the server replies to a FEEDBACK frame.
  onResult(ok, message) {
    if (ok) {
      this._phase = "done";
      this._errorMsg = null;
    } else {
      this._phase = "error";
      this._errorMsg = message || "Something went wrong. Please try again.";
    }
  }

  _openDialog() {
    this._phase = "draft";
    this._errorMsg = null;
    this._open = true;
    this.updateComplete.then(() => {
      const dlg = this.renderRoot.querySelector("dialog");
      if (dlg && !dlg.open) dlg.showModal();
    });
  }

  _closeDialog() {
    const dlg = this.renderRoot.querySelector("dialog");
    if (dlg && dlg.open) dlg.close();
    this._open = false;
    // Discard the draft so the next open starts fresh.
    this._text = "";
    this._type = "bug";
    this._phase = "draft";
    this._errorMsg = null;
  }

  _onSubmit() {
    const text = this._text.trim();
    if (text.length < 10) {
      this._phase = "error";
      this._errorMsg = "Please enter at least 10 characters.";
      return;
    }
    this._phase = "submitting";
    this._errorMsg = null;
    this.dispatchEvent(
      new CustomEvent("feedback-submit", {
        detail: { type: this._type, text },
        bubbles: true,
        composed: true,
      }),
    );
  }

  render() {
    // Only logged-in players see the button.
    if (!this.sessionToken) return html``;

    return html`
      <button class="launcher" @click=${this._openDialog} title="Report a bug or request a feature">
        [feedback]
      </button>
      ${this._open ? this._renderDialog() : ""}
    `;
  }

  _renderDialog() {
    if (this._phase === "done") {
      return html`
        <dialog @cancel=${this._closeDialog}>
          <div class="title">─ Feedback ─</div>
          <p class="done">Thank you! Your feedback was saved.</p>
          <div class="actions">
            <button class="act" @click=${this._closeDialog}>Close</button>
          </div>
        </dialog>
      `;
    }

    const submitting = this._phase === "submitting";
    return html`
      <dialog @cancel=${this._closeDialog}>
        <div class="title">─ Feedback ─</div>
        ${this._phase === "error" && this._errorMsg
          ? html`<div class="error">${this._errorMsg}</div>`
          : ""}
        <div class="row">
          <label class="label" for="fb-type">Type</label>
          <select
            id="fb-type"
            .value=${this._type}
            ?disabled=${submitting}
            @change=${(e) => (this._type = e.target.value)}
          >
            <option value="bug">Bug report</option>
            <option value="feature">Feature request</option>
          </select>
        </div>
        <div class="row">
          <label class="label" for="fb-text">Your suggestion</label>
          <textarea
            id="fb-text"
            rows="5"
            maxlength=${MAX_LEN}
            ?disabled=${submitting}
            .value=${this._text}
            @input=${(e) => (this._text = e.target.value)}
          ></textarea>
          <div class="charcount">${this._text.length} / ${MAX_LEN}</div>
        </div>
        <div class="actions">
          <button class="act" ?disabled=${submitting} @click=${this._onSubmit}>
            ${submitting ? "Sending…" : "Submit"}
          </button>
          <button class="act secondary" ?disabled=${submitting} @click=${this._closeDialog}>
            Cancel
          </button>
        </div>
      </dialog>
    `;
  }
}

customElements.define("feedback-button", FeedbackButton);
