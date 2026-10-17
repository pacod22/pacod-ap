<script lang="ts">
	// Contact page. The intro + "how to reach us" table come from ./content.ts via
	// <LegalDocument>; the message form below is interactive so it stays here. The
	// form is intentionally inert — there is no contact backend (the only public
	// POST is newsletter/subscribe) — so it composes a mailto: link.
	import { getLocale } from '$lib/paraglide/runtime';
	import LegalDocument from '$lib/legal/LegalDocument.svelte';
	import Seo from '$lib/seo/Seo.svelte';
	import { COMPANY } from '$lib/legal/company';
	import { contact, contactForm } from './content';

	let doc = $derived(contact[getLocale()]);
	let f = $derived(contactForm[getLocale()]);

	let name = $state('');
	let email = $state('');
	let message = $state('');

	// No backend: compose a mailto link so the button still does something useful.
	function mailtoHref() {
		const subject = encodeURIComponent(`${f.subjectPrefix} ${name || f.visitorFallback}`);
		const body = encodeURIComponent(`${message}\n\n— ${name}\n${email}`);
		return `mailto:${COMPANY.email}?subject=${subject}&body=${body}`;
	}
</script>

<Seo title={doc.title} description={doc.metaDescription} canonicalPath="/contact" localized />

<LegalDocument {doc} />

<article class="legal">
	<h2>{f.heading}</h2>
	<form
		class="contact-form"
		onsubmit={(e) => {
			e.preventDefault();
			window.location.href = mailtoHref();
		}}
	>
		<label>
			<span>{f.nameLabel}</span>
			<input
				bind:value={name}
				type="text"
				autocomplete="name"
				required
				placeholder={f.namePlaceholder}
			/>
		</label>
		<label>
			<span>{f.emailLabel}</span>
			<input
				bind:value={email}
				type="email"
				autocomplete="email"
				required
				placeholder={f.emailPlaceholder}
			/>
		</label>
		<label>
			<span>{f.messageLabel}</span>
			<textarea bind:value={message} rows="5" required placeholder={f.messagePlaceholder}
			></textarea>
		</label>
		<button type="submit">{f.submit}</button>
		<!-- static hand-authored form copy, not user input -->
		<!-- eslint-disable-next-line svelte/no-at-html-tags -->
		<p class="form-hint">{@html f.hint}</p>
	</form>
</article>

<style>
	.contact-form {
		display: flex;
		flex-direction: column;
		gap: 16px;
		max-width: 520px;
	}
	.contact-form label {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.contact-form span {
		font-size: 0.85rem;
		font-weight: 600;
		color: var(--color-text-muted);
	}
	.contact-form input,
	.contact-form textarea {
		width: 100%;
		padding: 10px 12px;
		border: 1px solid var(--color-border);
		border-radius: var(--r-md, 10px);
		background: var(--color-surface);
		color: var(--color-text);
		font: inherit;
		font-size: 0.95rem;
	}
	.contact-form textarea {
		resize: vertical;
	}
	.contact-form input:focus,
	.contact-form textarea:focus {
		outline: 2px solid var(--color-brand);
		outline-offset: 1px;
		border-color: transparent;
	}
	.contact-form button {
		align-self: flex-start;
		background: var(--color-brand);
		color: var(--color-brand-fg);
		border: none;
		border-radius: var(--r-md, 10px);
		font-weight: 600;
		font-size: 0.95rem;
		padding: 11px 22px;
		cursor: pointer;
		transition: filter 0.15s;
	}
	.contact-form button:hover {
		filter: brightness(1.06);
	}
	.form-hint {
		font-size: 0.85rem;
		color: var(--color-text-muted);
		margin: 0;
	}
</style>
