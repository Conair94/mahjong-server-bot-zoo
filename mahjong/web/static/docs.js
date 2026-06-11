// <docs-page> — the in-client documentation reader (Spec 36 — docs-pane.md).
//
// A top-level page (sibling of <profile-page>): folder/topic menu on the
// left, the active document on the right in a pre-wrap monospace block.
// Content lives in docs_content.js; this component is pure presentation.
// Emits `docs-back` (the app decides where "back" goes — docs are reachable
// from the auth screen, the lobby, and mid-table).

import { LitElement, html, css } from "lit";
import { DOC_SECTIONS, FIRST_DOC_SLUG, docBySlug } from "/static/docs_content.js";

class DocsPage extends LitElement {
  static properties = {
    activeSlug: { type: String },
  };

  static styles = css`
    :host { display: block; }
    .wrap { border: 1px solid var(--border); padding: 1rem 1.25rem 1.25rem; }
    .head {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 1rem;
      margin-bottom: 1rem;
    }
    .who { color: var(--accent); font-size: 1.1em; }
    .back {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--fg);
      font-family: inherit;
      font-size: inherit;
      padding: 0.25rem 0.75rem;
      cursor: pointer;
    }
    .back:hover { color: var(--accent); border-color: var(--accent); }
    .columns { display: flex; gap: 1.5rem; flex-wrap: wrap; }
    nav { min-width: 16rem; flex: 0 0 auto; }
    .folder { color: var(--fg-dim); margin: 0.6rem 0 0.2rem; }
    .folder::before { content: "▾ "; }
    .topic {
      display: block;
      width: 100%;
      text-align: left;
      background: none;
      border: none;
      color: var(--fg);
      font-family: inherit;
      font-size: inherit;
      padding: 0.12rem 0 0.12rem 1.1rem;
      cursor: pointer;
    }
    .topic:hover { color: var(--accent); }
    .topic.active { color: var(--accent); }
    .topic.active::before { content: "› "; margin-left: -1.1rem; }
    article { flex: 1 1 30rem; min-width: 0; }
    pre.body {
      margin: 0;
      white-space: pre-wrap;
      overflow-wrap: break-word;
      line-height: 1.35;
      color: var(--fg);
    }
  `;

  constructor() {
    super();
    this.activeSlug = FIRST_DOC_SLUG;
  }

  _select(slug) {
    this.activeSlug = slug;
    // Long docs: snap the reader back to the top of the new topic.
    this.renderRoot.querySelector("article")?.scrollTo?.(0, 0);
    this.scrollIntoView?.({ block: "start" });
  }

  _back() {
    this.dispatchEvent(new CustomEvent("docs-back", { bubbles: true, composed: true }));
  }

  render() {
    const doc = docBySlug(this.activeSlug) ?? docBySlug(FIRST_DOC_SLUG);
    return html`
      <div class="wrap">
        <div class="head">
          <span class="who">▤ Documentation</span>
          <button class="back" @click=${this._back} title="Back (Esc)">[ back ]</button>
        </div>
        <div class="columns">
          <nav>
            ${DOC_SECTIONS.map(
              (section) => html`
                <div class="folder">${section.title}</div>
                ${section.docs.map(
                  (d) => html`
                    <button
                      class="topic ${d.slug === doc?.slug ? "active" : ""}"
                      @click=${() => this._select(d.slug)}
                    >
                      ${d.title}
                    </button>
                  `,
                )}
              `,
            )}
          </nav>
          <article>
            <pre class="body">${doc?.body ?? ""}</pre>
          </article>
        </div>
      </div>
    `;
  }
}

customElements.define("docs-page", DocsPage);
