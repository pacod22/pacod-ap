// Shared content schema for the localized legal / info documents.
//
// Each page ships a `content.ts` exporting a `Record<Locale, LegalDoc>`. The
// <LegalDocument> component renders a LegalDoc for the active locale. Prose
// blocks are trusted, static, hand-authored strings and may contain simple
// inline HTML (<a>, <strong>, <code>, <em>); they are rendered with {@html}.
// Anything interactive (the cookie-consent button) is a typed block the
// component renders as real markup — never inject scripts through `html`.
import type { Locale } from '$lib/paraglide/runtime';

export type { Locale };

/** A paragraph of prose (may contain inline HTML). */
export interface ParagraphBlock {
	k: 'p';
	html: string;
}

/** A bulleted or numbered list; each item may contain inline HTML. */
export interface ListBlock {
	k: 'ul' | 'ol';
	items: string[];
}

/** The highlighted callout box (used for important notices). */
export interface NoteBlock {
	k: 'note';
	html: string;
}

/**
 * A table. `kind: 'cols'` renders `head` as a header row over `rows`.
 * `kind: 'keyval'` renders each row as a <th>label</th><td>value</td> pair
 * (no header row) — used for the contact details table.
 */
export interface TableBlock {
	k: 'table';
	kind: 'cols' | 'keyval';
	head?: string[];
	rows: string[][];
}

/** Renders the cookie-consent "Manage preferences" button (label via Paraglide). */
export interface ManageConsentBlock {
	k: 'manage';
}

export type Block = ParagraphBlock | ListBlock | NoteBlock | TableBlock | ManageConsentBlock;

export interface Section {
	/** Optional <h2> heading for the section. */
	heading?: string;
	blocks: Block[];
}

export interface LegalDoc {
	/** Small kicker above the title, e.g. "Legal" / "About us" / "Contact". */
	eyebrow: string;
	/** Page <h1> and document <title>. */
	title: string;
	/** Intro lead paragraph (plain text). */
	lead: string;
	/** <meta name="description"> for the page head. */
	metaDescription: string;
	/** Optional "Last updated …" line under the lead. */
	updatedLine?: string;
	sections: Section[];
}

export type LocalizedDoc = Record<Locale, LegalDoc>;

/** Labels for the interactive contact form (rendered in +page.svelte). */
export interface ContactFormLabels {
	heading: string;
	nameLabel: string;
	namePlaceholder: string;
	emailLabel: string;
	emailPlaceholder: string;
	messageLabel: string;
	messagePlaceholder: string;
	submit: string;
	/** HTML — includes the mailto link. */
	hint: string;
	/** mailto subject prefix, e.g. "Website enquiry from" */
	subjectPrefix: string;
	/** fallback name when the visitor left the name field blank */
	visitorFallback: string;
}
