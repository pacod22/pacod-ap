<script lang="ts">
	import { createQuery } from '@tanstack/svelte-query';
	import { MessageSquare, ArrowRight, Clock } from 'lucide-svelte';
	import { fetchSessions } from '$lib/features/chat/index';
	import { m } from '$lib/paraglide/messages';

	// Reuses the same caller-scoped endpoint the chat sidebar uses.
	const sessions = createQuery({
		queryKey: ['chat', 'sessions', 30],
		queryFn: () => fetchSessions(30),
		staleTime: 30_000
	});

	// Backend returns newest-first; sort defensively so we don't depend on it.
	const recent = $derived(
		[...($sessions.data ?? [])]
			.sort((a, b) => new Date(b.last_turn_at).getTime() - new Date(a.last_turn_at).getTime())
			.slice(0, 3)
	);

	function relativeTime(iso: string): string {
		const diff = Date.now() - new Date(iso).getTime();
		const mins = Math.round(diff / 60_000);
		if (mins < 1) return m.common_time_just_now();
		if (mins < 60) return m.common_time_minutes_ago({ n: mins });
		const hrs = Math.round(mins / 60);
		if (hrs < 24) return m.common_time_hours_ago({ n: hrs });
		const days = Math.round(hrs / 24);
		if (days < 7) return m.common_time_days_ago({ n: days });
		return new Date(iso).toLocaleDateString();
	}
</script>

<div class="flex items-center justify-between">
	<h2 class="text-sm font-semibold tracking-wide text-[var(--color-text-muted)] uppercase">
		{m.dashboard_recent_conversations()}
	</h2>
	<a
		href="/chat"
		class="flex items-center gap-1 text-xs font-medium text-[var(--color-brand)] hover:underline"
	>
		{m.dashboard_view_all()}
		<ArrowRight size={12} />
	</a>
</div>

<div class="mt-3">
	{#if $sessions.isPending}
		<div
			class="flex h-28 items-center justify-center rounded-xl border text-sm text-[var(--color-text-muted)]"
			style="border-color: var(--color-border); background: var(--color-surface-2);"
		>
			{m.common_loading()}
		</div>
	{:else if $sessions.isError}
		<div
			class="rounded-xl border px-5 py-4 text-sm text-[var(--color-text-muted)]"
			style="border-color: var(--color-border); background: var(--color-surface-2);"
		>
			{m.dashboard_conversations_unavailable()}
		</div>
	{:else if recent.length === 0}
		<a
			href="/chat"
			class="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed px-5 py-8 text-center transition-colors hover:bg-[var(--color-surface-2)]"
			style="border-color: var(--color-border);"
		>
			<MessageSquare size={22} class="text-[var(--color-text-muted)]" />
			<span class="text-sm font-medium">{m.dashboard_no_conversations()}</span>
			<span class="text-xs text-[var(--color-text-muted)]">{m.dashboard_start_chatting()}</span>
		</a>
	{:else}
		<div class="space-y-2">
			{#each recent as s (s.session_id)}
				<a
					href="/chat?session={encodeURIComponent(s.session_id)}"
					class="group flex items-center gap-3 rounded-xl border p-4 transition-all duration-150 hover:-translate-y-0.5 hover:shadow-md"
					style="background: var(--color-surface-2); border-color: var(--color-border);"
				>
					<div
						class="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl"
						style="background: var(--color-brand-soft);"
					>
						<MessageSquare size={16} style="color: var(--color-brand);" />
					</div>
					<div class="min-w-0 flex-1">
						<div class="truncate text-sm font-medium">{s.title}</div>
						<div class="mt-0.5 flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
							<Clock size={11} />
							<span>{relativeTime(s.last_turn_at)}</span>
							<span aria-hidden="true">·</span>
							<span>{m.dashboard_turns({ count: s.turn_count })}</span>
						</div>
					</div>
					<ArrowRight
						size={14}
						class="shrink-0 text-[var(--color-text-muted)] transition-transform duration-150 group-hover:translate-x-0.5"
					/>
				</a>
			{/each}
		</div>
	{/if}
</div>
