<script lang="ts">
	/**
	 * Shown on a legacy config page when some of its settings belong to the
	 * agent's published Agent-editor version: the page displays the version's
	 * values, and the backend refuses (409) a change to them.
	 */
	import { Waypoints } from 'lucide-svelte';
	import { m } from '$lib/paraglide/messages';

	let { fields }: { fields: string[] } = $props();
</script>

{#if fields.length}
	<div
		class="mb-3 flex items-start gap-2 rounded-md border px-3 py-2 text-xs"
		style="border-color: var(--color-warning); background: var(--color-warning-soft); color: var(--color-text);"
	>
		<Waypoints size={14} class="mt-0.5 shrink-0" />
		<div>
			<p>{m.agent_editor_managed_notice()}</p>
			<p class="mt-1 font-mono text-[11px]" style="color: var(--color-text-muted);">
				{fields.join(', ')}
			</p>
			<a href="/agent-editor" class="mt-1 inline-block underline" style="color: var(--color-brand);"
				>{m.agent_editor_managed_link()}</a
			>
		</div>
	</div>
{/if}
