<script lang="ts">
	/**
	 * Follow icons for the profiles this deployment has configured.
	 *
	 * Renders nothing at all when no `SOCIAL_*_URL` is set — which is the state
	 * of a fresh deployment, and the reason an empty footer column never appears.
	 * The same URLs feed the Organization `sameAs` in the site JSON-LD, so search
	 * engines and visitors are told the same thing from one source.
	 */
	import { m } from '$lib/paraglide/messages';
	import { BRAND_ICONS, type BrandKey } from './brand-icons';
	import type { PublicSiteConfig } from '$lib/server/site-config';

	let {
		site,
		size = 20,
		showTitle = false
	}: { site: PublicSiteConfig; size?: number; showTitle?: boolean } = $props();

	const links = $derived(
		(
			[
				['facebook', site.social.facebook],
				['instagram', site.social.instagram],
				['youtube', site.social.youtube],
				['tiktok', site.social.tiktok],
				['x', site.social.x]
			] as [BrandKey, string][]
		)
			.filter(([, href]) => Boolean(href))
			.map(([key, href]) => ({ key, href, icon: BRAND_ICONS[key] }))
	);
</script>

{#if links.length}
	<div class="social-links">
		{#if showTitle}<span class="social-title">{m.social_follow_title()}</span>{/if}
		<ul>
			{#each links as link (link.key)}
				<li>
					<a
						href={link.href}
						target="_blank"
						rel="noopener noreferrer me"
						aria-label={m.social_follow_aria({ network: link.icon.label })}
						title={link.icon.label}
					>
						<svg
							width={size}
							height={size}
							viewBox="0 0 24 24"
							fill="currentColor"
							aria-hidden="true"
							focusable="false"
						>
							<path d={link.icon.path} />
						</svg>
					</a>
				</li>
			{/each}
		</ul>
	</div>
{/if}

<style>
	.social-links {
		display: flex;
		flex-direction: column;
		gap: 0.6rem;
	}
	.social-title {
		font-size: 0.8125rem;
		font-weight: 600;
		letter-spacing: 0.02em;
		opacity: 0.75;
	}
	ul {
		display: flex;
		gap: 0.5rem;
		margin: 0;
		padding: 0;
		list-style: none;
	}
	a {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 2.25rem;
		height: 2.25rem;
		border-radius: 0.625rem;
		color: inherit;
		opacity: 0.72;
		transition:
			opacity 0.15s ease,
			background-color 0.15s ease,
			transform 0.15s ease;
	}
	a:hover,
	a:focus-visible {
		opacity: 1;
		background: color-mix(in srgb, currentColor 12%, transparent);
		transform: translateY(-1px);
	}
	@media (prefers-reduced-motion: reduce) {
		a {
			transition: none;
		}
		a:hover,
		a:focus-visible {
			transform: none;
		}
	}
</style>
