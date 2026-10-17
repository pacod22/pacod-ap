<script lang="ts">
	/**
	 * WhatsApp click-to-chat.
	 *
	 * `wa.me` is the official deep link: it opens the app on mobile and WhatsApp
	 * Web on desktop, with no SDK, no script and no cookie — so it needs neither
	 * consent nor a CSP allowance. Rendered only when `SOCIAL_WHATSAPP_NUMBER`
	 * is configured.
	 *
	 * This is the entire WhatsApp integration on the web side. Sending messages
	 * programmatically is the WhatsApp Cloud API, which is a backend concern and
	 * is not wired here.
	 */
	import { m } from '$lib/paraglide/messages';
	import { BRAND_ICONS } from './brand-icons';
	import type { PublicSiteConfig } from '$lib/server/site-config';

	let {
		site,
		variant = 'link',
		size = 18
	}: { site: PublicSiteConfig; variant?: 'link' | 'button'; size?: number } = $props();

	const href = $derived(
		site.social.whatsappNumber
			? `https://wa.me/${site.social.whatsappNumber}` +
					(site.social.whatsappText ? `?text=${encodeURIComponent(site.social.whatsappText)}` : '')
			: ''
	);
</script>

{#if href}
	<a
		{href}
		target="_blank"
		rel="noopener noreferrer"
		class="wa"
		class:wa-button={variant === 'button'}
	>
		<svg
			width={size}
			height={size}
			viewBox="0 0 24 24"
			fill="currentColor"
			aria-hidden="true"
			focusable="false"
		>
			<path d={BRAND_ICONS.whatsapp.path} />
		</svg>
		<span>{m.social_whatsapp_cta()}</span>
	</a>
{/if}

<style>
	.wa {
		display: inline-flex;
		align-items: center;
		gap: 0.5rem;
		color: inherit;
		text-decoration: none;
		font-weight: 600;
	}
	.wa:hover span,
	.wa:focus-visible span {
		text-decoration: underline;
	}
	.wa-button {
		padding: 0.625rem 1rem;
		border-radius: 0.625rem;
		/* WhatsApp brand green — a recognised affordance, so it keeps its colour
		   in both themes rather than following the app tokens. */
		background: #25d366;
		color: #082514;
	}
	.wa-button:hover,
	.wa-button:focus-visible {
		background: #1fb955;
	}
	.wa-button:hover span,
	.wa-button:focus-visible span {
		text-decoration: none;
	}
</style>
