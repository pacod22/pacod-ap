<script lang="ts">
	/** The caveats every analytics view needs next to its numbers. */
	import { m } from '$lib/paraglide/messages';
	import { Info } from 'lucide-svelte';

	let { profileActive }: { profileActive: boolean } = $props();
</script>

<div class="space-y-1 text-xs text-[var(--color-text-muted)]">
	{#if profileActive}
		<p
			class="flex items-start gap-1.5 rounded-lg border p-2"
			style="border-color: var(--color-border);"
		>
			<Info size={13} class="mt-0.5 shrink-0" />
			{m.analytics_hint_profile_scope()}
		</p>
	{/if}
	<p class="flex items-start gap-1.5">
		<Info size={13} class="mt-0.5 shrink-0" />
		{m.analytics_hint_consent()}
	</p>
</div>
