<script lang="ts">
	/**
	 * Non-blocking warning for a response whose `gateway_error` is set: the
	 * gateway's reports could not be read, so the LLM figures beside it read 0.
	 * Renders nothing when there is no error.
	 */
	import { AlertTriangle } from 'lucide-svelte';
	import { m } from '$lib/paraglide/messages';
	import { cn } from '$lib/utils/cn';

	let {
		error,
		class: className = ''
	}: {
		error: string | null | undefined;
		class?: string;
	} = $props();
</script>

{#if error}
	<p
		role="status"
		class={cn('flex items-start gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs', className)}
		style="border-color: var(--color-warning); background: var(--color-warning-soft); color: var(--color-text);"
	>
		<AlertTriangle size={13} class="mt-0.5 shrink-0" style="color: var(--color-warning);" />
		<span class="min-w-0 break-words">{m.gateway_error_llm_unavailable({ message: error })}</span>
	</p>
{/if}
