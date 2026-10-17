<script lang="ts">
	/**
	 * Share intents for the public marketing pages.
	 *
	 * Plain links to each platform's share URL plus a copy-link fallback — no
	 * SDK, no script, no cookie, so this needs no consent gate and no CSP
	 * allowance. What the recipient sees in the preview comes from the Open Graph
	 * tags that `$lib/seo/Seo.svelte` emits.
	 */
	import { page } from '$app/state';
	import { m } from '$lib/paraglide/messages';
	import { BRAND_ICONS } from './brand-icons';
	import { Link2, Check } from 'lucide-svelte';

	let { url, title }: { url?: string; title?: string } = $props();

	const shareUrl = $derived(url ?? page.url.href);
	const shareTitle = $derived(title ?? '');

	const targets = $derived([
		{
			key: 'whatsapp' as const,
			label: m.share_on_whatsapp(),
			href: `https://wa.me/?text=${encodeURIComponent(`${shareTitle} ${shareUrl}`.trim())}`
		},
		{
			key: 'facebook' as const,
			label: m.share_on_facebook(),
			href: `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(shareUrl)}`
		},
		{
			key: 'x' as const,
			label: m.share_on_x(),
			href:
				`https://twitter.com/intent/tweet?url=${encodeURIComponent(shareUrl)}` +
				(shareTitle ? `&text=${encodeURIComponent(shareTitle)}` : '')
		}
	]);

	let copied = $state(false);
	let copyTimer: ReturnType<typeof setTimeout> | undefined;

	async function copyLink() {
		try {
			await navigator.clipboard.writeText(shareUrl);
			copied = true;
			clearTimeout(copyTimer);
			copyTimer = setTimeout(() => (copied = false), 2000);
		} catch {
			// Clipboard access can be denied; the share links above still work.
		}
	}
</script>

<div class="share">
	<span class="share-label">{m.share_label()}</span>
	<ul>
		{#each targets as target (target.key)}
			<li>
				<a
					href={target.href}
					target="_blank"
					rel="noopener noreferrer"
					aria-label={target.label}
					title={target.label}
				>
					<svg
						width="18"
						height="18"
						viewBox="0 0 24 24"
						fill="currentColor"
						aria-hidden="true"
						focusable="false"
					>
						<path d={BRAND_ICONS[target.key].path} />
					</svg>
				</a>
			</li>
		{/each}
		<li>
			<button
				type="button"
				onclick={copyLink}
				aria-label={copied ? m.share_link_copied() : m.share_copy_link()}
				title={copied ? m.share_link_copied() : m.share_copy_link()}
			>
				{#if copied}<Check size={18} />{:else}<Link2 size={18} />{/if}
			</button>
		</li>
	</ul>
</div>

<style>
	.share {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		flex-wrap: wrap;
	}
	.share-label {
		font-size: 0.8125rem;
		font-weight: 600;
		opacity: 0.7;
	}
	ul {
		display: flex;
		gap: 0.4rem;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	a,
	button {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 2.125rem;
		height: 2.125rem;
		border-radius: 0.5rem;
		border: 1px solid var(--color-border, rgba(0, 0, 0, 0.12));
		background: transparent;
		color: inherit;
		cursor: pointer;
		opacity: 0.8;
		transition:
			opacity 0.15s ease,
			background-color 0.15s ease;
	}
	a:hover,
	a:focus-visible,
	button:hover,
	button:focus-visible {
		opacity: 1;
		background: color-mix(in srgb, currentColor 10%, transparent);
	}
	@media (prefers-reduced-motion: reduce) {
		a,
		button {
			transition: none;
		}
	}
</style>
