<script lang="ts">
	import { goto } from '$app/navigation';
	import { Search, X, ChevronLeft, ChevronRight, AlertCircle } from 'lucide-svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import { createDebugTurnsQuery } from '$lib/features/debug-console/queries';
	import { selectedProfile } from '$lib/stores/selectedProfile.svelte';
	import { m } from '$lib/paraglide/messages';

	const PAGE_SIZE = 50;
	let offset = $state(0);
	let userFilter = $state('');
	let sessionFilter = $state('');
	let turnFilter = $state('');

	let filters = $derived({
		user_id: userFilter.trim() || undefined,
		session_id: sessionFilter.trim() || undefined,
		turn_id: turnFilter.trim() || undefined,
		profile_id: selectedProfile.id ?? undefined,
		limit: PAGE_SIZE,
		offset
	});

	const turns = $derived(createDebugTurnsQuery(filters));
	const rows = $derived($turns.data ?? []);
	const hasFilters = $derived(!!(userFilter || sessionFilter || turnFilter));

	const hasPrev = $derived(offset > 0);
	const hasNext = $derived(rows.length === PAGE_SIZE);

	function clearFilters() {
		userFilter = '';
		sessionFilter = '';
		turnFilter = '';
		offset = 0;
	}
	function prevPage() {
		offset = Math.max(0, offset - PAGE_SIZE);
	}
	function nextPage() {
		if (hasNext) offset += PAGE_SIZE;
	}

	function fmtTime(iso: string) {
		return new Date(iso).toLocaleString();
	}
	function fmtDuration(ms: number) {
		return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
	}
	function truncate(text: string | null | undefined, max = 60) {
		if (!text) return '';
		return text.length > max ? text.slice(0, max).trimEnd() + '…' : text;
	}
</script>

<svelte:head><title>{m.debug_console_title()}</title></svelte:head>

<div class="space-y-6">
	<div>
		<h1 class="text-2xl font-semibold tracking-tight">{m.nav_debug_console()}</h1>
		<p class="mt-1 max-w-2xl text-sm text-[var(--color-text-muted)]">
			{m.debug_console_intro()}
		</p>
	</div>

	<!-- Filters -->
	<div
		class="flex flex-col flex-wrap gap-2 rounded-xl border p-3 sm:flex-row sm:items-center"
		style="background: var(--color-surface-2); border-color: var(--color-border);"
	>
		<label
			class="flex h-10 w-full items-center gap-2 rounded-lg border px-3 focus-within:border-[var(--color-brand)] sm:w-52"
			style="background: var(--color-surface-2); border-color: var(--color-border);"
		>
			<Search size={13} class="shrink-0 text-[var(--color-text-muted)]" />
			<input
				type="text"
				placeholder={m.debug_console_filter_user()}
				bind:value={userFilter}
				class="noborder min-w-0 flex-1 bg-transparent text-sm outline-none"
			/>
		</label>
		<label
			class="flex h-10 w-full items-center gap-2 rounded-lg border px-3 focus-within:border-[var(--color-brand)] sm:w-52"
			style="background: var(--color-surface-2); border-color: var(--color-border);"
		>
			<Search size={13} class="shrink-0 text-[var(--color-text-muted)]" />
			<input
				type="text"
				placeholder={m.debug_console_filter_session()}
				bind:value={sessionFilter}
				class="noborder min-w-0 flex-1 bg-transparent text-sm outline-none"
			/>
		</label>
		<label
			class="flex h-10 w-full items-center gap-2 rounded-lg border px-3 focus-within:border-[var(--color-brand)] sm:w-64"
			style="background: var(--color-surface-2); border-color: var(--color-border);"
		>
			<Search size={13} class="shrink-0 text-[var(--color-text-muted)]" />
			<input
				type="text"
				placeholder={m.debug_console_filter_turn()}
				bind:value={turnFilter}
				class="noborder min-w-0 flex-1 bg-transparent text-sm outline-none"
			/>
		</label>
		{#if hasFilters}
			<Button variant="ghost" size="sm" onclick={clearFilters}
				><X size={14} />{m.common_clear()}</Button
			>
		{/if}
	</div>

	<!-- Table -->
	<div class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
		<div class="overflow-x-auto">
			<table class="data-table data-table-stack">
				<thead>
					<tr>
						<th>{m.debug_console_col_time()}</th>
						<th>{m.debug_console_col_user()}</th>
						<th>{m.debug_console_col_agent()}</th>
						<th>{m.debug_console_col_message()}</th>
						<th class="text-right">{m.debug_console_col_steps()}</th>
						<th class="text-right">{m.debug_console_col_duration()}</th>
						<th class="text-right">{m.debug_console_col_errors()}</th>
					</tr>
				</thead>
				<tbody>
					{#if $turns.isPending}
						<tr
							><td colspan="7" class="py-12 text-center text-sm text-[var(--color-text-muted)]"
								>{m.common_loading()}</td
							></tr
						>
					{:else if $turns.isError}
						<tr
							><td colspan="7" class="py-12 text-center text-sm text-[var(--color-danger)]"
								>{m.debug_console_load_failed()}</td
							></tr
						>
					{:else if rows.length === 0}
						<tr
							><td colspan="7" class="py-12 text-center text-sm text-[var(--color-text-muted)]"
								>{m.debug_console_empty()}</td
							></tr
						>
					{:else}
						{#each rows as t (t.turn_id)}
							<tr
								class="cursor-pointer transition-colors hover:bg-[var(--color-surface-2)]"
								onclick={() => goto(`/debug-console/${encodeURIComponent(t.turn_id)}`)}
							>
								<td
									data-label={m.debug_console_col_time()}
									class="whitespace-nowrap text-[var(--color-text-muted)]"
									>{fmtTime(t.started_at)}</td
								>
								<td
									data-label={m.debug_console_col_user()}
									class="font-mono text-xs break-all md:break-normal md:whitespace-nowrap"
									>{t.user_email ?? t.user_id ?? '—'}</td
								>
								<td data-label={m.debug_console_col_agent()} class="whitespace-nowrap">
									{#if t.profile_name ?? t.profile_id}
										<span
											class="inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium"
											style="background: var(--color-surface-3); color: var(--color-text);"
											>{t.profile_name ?? t.profile_id}</span
										>
									{:else}
										<span class="text-[var(--color-text-muted)]">—</span>
									{/if}
								</td>
								<td data-label={m.debug_console_col_message()} class="stack-block max-w-md">
									{#if t.turn_id.startsWith('loop:')}
										<span
											class="mr-1.5 inline-flex items-center rounded-md px-1.5 py-0.5 text-[10px] font-semibold tracking-wide uppercase"
											style="background: var(--color-brand-soft); color: var(--color-brand);"
											>{m.nav_review()}</span
										>
									{/if}{truncate(t.user_message) || '—'}
								</td>
								<td data-label={m.debug_console_col_steps()} class="text-right tabular-nums"
									>{t.interaction_count}</td
								>
								<td data-label={m.debug_console_col_duration()} class="text-right tabular-nums"
									>{fmtDuration(t.total_duration_ms)}</td
								>
								<td data-label={m.debug_console_col_errors()} class="text-right">
									{#if t.error_count > 0}
										<span
											class="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium"
											style="background: var(--color-danger-soft); color: var(--color-danger);"
										>
											<AlertCircle size={11} />
											{t.error_count}
										</span>
									{:else}
										<span class="text-[var(--color-text-muted)]">—</span>
									{/if}
								</td>
							</tr>
						{/each}
					{/if}
				</tbody>
			</table>
		</div>
	</div>

	<!-- Pagination -->
	<div class="flex items-center justify-between text-sm text-[var(--color-text-muted)]">
		<span>
			{#if !$turns.isPending}
				{m.common_showing_range({
					from: rows.length === 0 ? 0 : offset + 1,
					to: offset + rows.length
				})}
			{/if}
		</span>
		<div class="flex items-center gap-2">
			<button
				onclick={prevPage}
				disabled={!hasPrev}
				class="flex items-center gap-1 rounded-lg px-3 py-1.5 text-sm transition-colors hover:bg-[var(--color-surface-2)] disabled:pointer-events-none disabled:opacity-40"
			>
				<ChevronLeft size={14} />
				{m.common_prev()}
			</button>
			<button
				onclick={nextPage}
				disabled={!hasNext}
				class="flex items-center gap-1 rounded-lg px-3 py-1.5 text-sm transition-colors hover:bg-[var(--color-surface-2)] disabled:pointer-events-none disabled:opacity-40"
			>
				{m.common_next()}
				<ChevronRight size={14} />
			</button>
		</div>
	</div>
</div>
