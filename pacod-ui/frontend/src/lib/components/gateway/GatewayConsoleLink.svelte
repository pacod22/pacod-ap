<script lang="ts">
	/**
	 * "Open in gateway console" — a link to the same view in the LLM gateway's
	 * (pacod-gw) web console. Always a new tab, never an iframe: the console
	 * sends X-Frame-Options: DENY. Renders nothing without an http(s) URL.
	 */
	import { ExternalLink } from 'lucide-svelte';
	import { m } from '$lib/paraglide/messages';
	import { safeExternalUrl } from '$lib/utils/external-url';
	import { cn } from '$lib/utils/cn';

	let {
		href,
		label,
		class: className = ''
	}: {
		href: string | null | undefined;
		/** Defaults to "Open in gateway console". */
		label?: string;
		class?: string;
	} = $props();

	const url = $derived(safeExternalUrl(href));
</script>

{#if url}
	<a
		href={url}
		target="_blank"
		rel="noopener noreferrer"
		class={cn(
			'inline-flex items-center gap-1 text-xs whitespace-nowrap text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-brand)] hover:underline',
			className
		)}
	>
		{label ?? m.gateway_console_open_view()}
		<ExternalLink size={11} class="shrink-0" />
	</a>
{/if}
