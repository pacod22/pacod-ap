<script lang="ts">
	import { Activity as ActivityIcon } from 'lucide-svelte';
	import { createActivityQuery } from '$lib/features/activity/queries';
	import { m } from '$lib/paraglide/messages';

	const activity = createActivityQuery();
</script>

{#if $activity.isPending}
	<div
		class="flex h-24 items-center justify-center rounded-xl border text-sm text-[var(--color-text-muted)]"
		style="border-color: var(--color-border); background: var(--color-surface-2);"
	>
		{m.common_loading()}
	</div>
{:else if $activity.data}
	{@const a = $activity.data}
	<div class="grid gap-4 sm:grid-cols-3">
		{#each [{ label: m.dashboard_activity_last_24h(), value: a.last_24h }, { label: m.dashboard_activity_last_7d(), value: a.last_7d }, { label: m.dashboard_activity_last_30d(), value: a.last_30d }] as stat (stat.label)}
			<div
				class="rounded-xl border p-5"
				style="background: var(--color-surface-2); border-color: var(--color-border);"
			>
				<div class="flex items-center gap-1.5">
					<ActivityIcon size={12} class="text-[var(--color-text-muted)]" />
					<span class="text-xs text-[var(--color-text-muted)]">{stat.label}</span>
				</div>
				<div class="mt-1.5 text-2xl font-semibold tabular-nums">
					{stat.value.toLocaleString()}
				</div>
				<div class="text-xs text-[var(--color-text-muted)]">{m.dashboard_activity_requests()}</div>
			</div>
		{/each}
	</div>
{:else}
	<div
		class="rounded-xl border px-5 py-4 text-sm text-[var(--color-text-muted)]"
		style="border-color: var(--color-border); background: var(--color-surface-2);"
	>
		{m.dashboard_activity_unavailable()}
	</div>
{/if}
