<script lang="ts">
	/**
	 * Usage analytics — requests, agent turns, LLM calls/tokens/cost/errors and
	 * consented page views, broken down by site (domain), agent, model and user.
	 *
	 * The LLM figures (calls, tokens, cost, errors) come from the LLM gateway's
	 * reports via admin-api; the gateway console has the same views (linked).
	 * The Grafana "Usage" tab on /metrics shows the aggregate Prometheus view of
	 * requests and turns (no tokens/cost: those are on the "LLM gateway" tabs).
	 */
	import { page } from '$app/state';
	import { m } from '$lib/paraglide/messages';
	import { getLocale } from '$lib/paraglide/runtime';
	import {
		ChartColumn,
		ChevronLeft,
		ChevronRight,
		ArrowUp,
		ArrowDown,
		ArrowUpDown,
		Search
	} from 'lucide-svelte';
	import { selectedProfile } from '$lib/stores/selectedProfile.svelte';
	import { profilesQuery, shortNameFor } from '$lib/features/profiles/queries';
	import {
		createAnalyticsOverviewQuery,
		createAnalyticsUsersQuery,
		type UserSortKey
	} from '$lib/features/analytics/queries';
	import {
		rangeById,
		bucketFor,
		num,
		usd,
		compact,
		fmtDuration,
		fmtDateTime
	} from '$lib/features/analytics/format';
	import FilterBar from '$lib/features/analytics/FilterBar.svelte';
	import KpiGrid from '$lib/features/analytics/KpiGrid.svelte';
	import OverviewCharts from '$lib/features/analytics/OverviewCharts.svelte';
	import BreakdownTables from '$lib/features/analytics/BreakdownTables.svelte';
	import DataNotes from '$lib/features/analytics/DataNotes.svelte';
	import GatewayConsoleLink from '$lib/components/gateway/GatewayConsoleLink.svelte';
	import GatewayErrorNotice from '$lib/components/gateway/GatewayErrorNotice.svelte';

	const PAGE_SIZE = 50;

	// ----- Filters (seeded from the URL so the user page's back link restores them)
	const initial = page.url.searchParams;
	let rangeId = $state(rangeById(initial.get('range')).id);
	let site = $state(initial.get('site') ?? '');
	let profile = $state(initial.get('profile') ?? '');

	const range = $derived(rangeById(rangeId));
	const bucket = $derived(bucketFor(range));
	// The top-bar scope wins over the in-page profile select.
	const scopedProfileId = $derived(selectedProfile.id);
	const effectiveProfileId = $derived(scopedProfileId ?? (profile || undefined));

	// ----- Profiles → readable labels
	const profilesQ = profilesQuery();
	const locale = getLocale();
	const profiles = $derived($profilesQ.data ?? []);
	const profileOptions = $derived(
		profiles.map((p) => ({ id: p.id, label: shortNameFor(p, locale) }))
	);
	function profileLabel(id: string): string {
		const p = profiles.find((x) => x.id === id);
		return p ? shortNameFor(p, locale) : id;
	}

	// ----- Overview
	const overview = $derived(
		createAnalyticsOverviewQuery({
			since_hours: range.hours,
			bucket,
			site: site || undefined,
			profile_id: effectiveProfileId
		})
	);
	const data = $derived($overview.data);

	// ----- Users table (server-side sort / search / paging)
	let search = $state('');
	let q = $state('');
	let sort = $state<UserSortKey>('last_seen');
	let order = $state<'asc' | 'desc'>('desc');
	let includeSystem = $state(false);
	let offset = $state(0);

	// Debounce the search box into `q`.
	$effect(() => {
		const next = search.trim();
		const t = setTimeout(() => {
			if (next !== q) {
				q = next;
				offset = 0;
			}
		}, 300);
		return () => clearTimeout(t);
	});

	const users = $derived(
		createAnalyticsUsersQuery({
			since_hours: range.hours,
			site: site || undefined,
			profile_id: effectiveProfileId,
			q: q || undefined,
			sort,
			order,
			include_system: includeSystem,
			limit: PAGE_SIZE,
			offset
		})
	);
	const userRows = $derived($users.data?.items ?? []);
	const userTotal = $derived($users.data?.total ?? 0);
	const hasPrev = $derived(offset > 0);
	const hasNext = $derived(offset + userRows.length < userTotal);

	function resetPage() {
		offset = 0;
	}
	// Switching the top-bar scope changes the result set: go back to page 1.
	let lastScope: string | undefined = undefined;
	$effect(() => {
		const scope = effectiveProfileId;
		if (scope !== lastScope) {
			lastScope = scope;
			offset = 0;
		}
	});
	function toggleSort(key: UserSortKey) {
		if (sort === key) order = order === 'desc' ? 'asc' : 'desc';
		else {
			sort = key;
			order = 'desc';
		}
		resetPage();
	}

	/** Filters carried into the per-user link so the drill-down opens on the same view. */
	const linkQuery = $derived.by(() => {
		const p = new URLSearchParams();
		// Always carry the range: the user page's own default differs.
		p.set('range', rangeId);
		if (site) p.set('site', site);
		if (profile && !scopedProfileId) p.set('profile', profile);
		const s = p.toString();
		return s ? `?${s}` : '';
	});

	const COLUMNS: { key: UserSortKey; label: () => string }[] = [
		{ key: 'last_seen', label: () => m.analytics_col_last_seen() },
		{ key: 'page_requests', label: () => m.analytics_col_page_requests() },
		{ key: 'api_requests', label: () => m.analytics_col_api_requests() },
		{ key: 'turns', label: () => m.analytics_col_turns() },
		{ key: 'llm_calls', label: () => m.analytics_col_llm_calls() },
		{ key: 'tokens', label: () => m.analytics_col_tokens() },
		{ key: 'cost_usd', label: () => m.analytics_col_cost() },
		{ key: 'llm_errors', label: () => m.analytics_col_errors() },
		{ key: 'page_views', label: () => m.analytics_col_page_views() },
		{ key: 'dwell_ms', label: () => m.analytics_col_dwell() }
	];
</script>

<svelte:head><title>{m.analytics_title()}</title></svelte:head>

<div class="space-y-4">
	<div class="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
		<div>
			<h1 class="flex items-center gap-2 text-xl font-semibold">
				<ChartColumn size={20} />
				{m.analytics_title()}
			</h1>
			<p class="mt-1 text-sm text-[var(--color-text-muted)]">{m.analytics_subtitle()}</p>
		</div>
		<!-- Gateway usage for the same filters (new tab; the console refuses framing). -->
		<GatewayConsoleLink href={data?.console_url} class="mt-1.5" />
	</div>

	<FilterBar
		bind:rangeId
		bind:site
		bind:profile
		{scopedProfileId}
		{profileOptions}
		{profileLabel}
		onchange={resetPage}
	/>
	<DataNotes profileActive={!!effectiveProfileId} />
	<GatewayErrorNotice error={data?.gateway_error} />

	{#if $overview.isError}
		<p class="text-sm" style="color: var(--color-danger);">{m.analytics_load_failed()}</p>
	{/if}

	<KpiGrid
		totals={data?.totals}
		errorsByType={data?.errors_by_type}
		uniqueUsers={data?.totals.unique_users ?? 0}
		errorsConsoleUrl={data?.errors_console_url}
		loading={$overview.isPending}
	/>

	<OverviewCharts {data} {bucket} loading={$overview.isPending} />

	<BreakdownTables {data} loading={$overview.isPending} {profileLabel} />

	<!-- Users -->
	<section class="space-y-2">
		<h2 class="text-base font-semibold">{m.analytics_users_title()}</h2>
		<GatewayErrorNotice error={$users.data?.gateway_error} />
		<div
			class="flex flex-wrap items-center gap-2 rounded-xl border p-2"
			style="border-color: var(--color-border); background: var(--color-surface-2);"
		>
			<label class="field-input flex h-10 min-w-[220px] flex-1 items-center gap-2">
				<Search size={14} class="shrink-0 text-[var(--color-text-muted)]" />
				<input
					class="noborder w-full bg-transparent outline-none"
					placeholder={m.analytics_users_search()}
					bind:value={search}
				/>
			</label>
			<label class="flex items-center gap-1.5 px-1 text-sm">
				<input type="checkbox" bind:checked={includeSystem} onchange={resetPage} />
				{m.analytics_users_include_system()}
			</label>
		</div>

		<div class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
			<div class="overflow-x-auto">
				<table class="data-table data-table-stack">
					<thead>
						<tr>
							<th>{m.analytics_col_user()}</th>
							{#each COLUMNS as c (c.key)}
								<th style={c.key === 'last_seen' ? '' : 'text-align: right'}>
									<button
										type="button"
										class="inline-flex items-center gap-1 whitespace-nowrap hover:text-[var(--color-text)]"
										onclick={() => toggleSort(c.key)}
									>
										{c.label()}
										{#if sort === c.key}
											{#if order === 'desc'}<ArrowDown size={12} />{:else}<ArrowUp size={12} />{/if}
										{:else}
											<ArrowUpDown size={12} class="opacity-40" />
										{/if}
									</button>
								</th>
							{/each}
						</tr>
					</thead>
					<tbody>
						{#if $users.isPending}
							<tr
								><td colspan="11" class="py-12 text-center text-sm text-[var(--color-text-muted)]"
									>{m.common_loading()}</td
								></tr
							>
						{:else if $users.isError}
							<tr
								><td
									colspan="11"
									class="py-12 text-center text-sm"
									style="color: var(--color-danger);">{m.analytics_load_failed()}</td
								></tr
							>
						{:else if userRows.length === 0}
							<tr
								><td colspan="11" class="py-12 text-center text-sm text-[var(--color-text-muted)]"
									>{m.analytics_users_empty()}</td
								></tr
							>
						{:else}
							{#each userRows as u (u.user_id)}
								<tr>
									<td data-label={m.analytics_col_user()}>
										<a
											href="/analytics/users/{encodeURIComponent(u.user_id)}{linkQuery}"
											class="font-medium hover:underline"
											style="color: var(--color-brand);"
										>
											{u.email ?? u.display_name ?? u.user_id}
										</a>
										{#if u.email && u.display_name}
											<div class="text-xs text-[var(--color-text-muted)]">{u.display_name}</div>
										{/if}
									</td>
									<td
										data-label={m.analytics_col_last_seen()}
										class="text-xs whitespace-nowrap text-[var(--color-text-muted)] tabular-nums"
										>{fmtDateTime(u.last_seen)}</td
									>
									<td data-label={m.analytics_col_page_requests()} class="text-right tabular-nums"
										>{num(u.page_requests)}</td
									>
									<td data-label={m.analytics_col_api_requests()} class="text-right tabular-nums"
										>{num(u.api_requests)}</td
									>
									<td data-label={m.analytics_col_turns()} class="text-right tabular-nums"
										>{num(u.turns)}</td
									>
									<td data-label={m.analytics_col_llm_calls()} class="text-right tabular-nums"
										>{num(u.llm_calls)}</td
									>
									<td data-label={m.analytics_col_tokens()} class="text-right tabular-nums"
										>{compact(u.tokens)}</td
									>
									<td data-label={m.analytics_col_cost()} class="text-right tabular-nums"
										>{usd(u.cost_usd)}</td
									>
									<td
										data-label={m.analytics_col_errors()}
										class="text-right tabular-nums"
										style={u.llm_errors > 0 ? 'color: var(--color-danger);' : ''}
										>{num(u.llm_errors)}</td
									>
									<td data-label={m.analytics_col_page_views()} class="text-right tabular-nums"
										>{num(u.page_views)}</td
									>
									<td data-label={m.analytics_col_dwell()} class="text-right tabular-nums"
										>{u.dwell_ms ? fmtDuration(u.dwell_ms) : '—'}</td
									>
								</tr>
							{/each}
						{/if}
					</tbody>
				</table>
			</div>
		</div>

		<div class="flex items-center justify-between text-sm text-[var(--color-text-muted)]">
			<span>
				{#if !$users.isPending}
					{m.analytics_users_range({
						from: userRows.length === 0 ? 0 : offset + 1,
						to: offset + userRows.length,
						total: userTotal
					})}
				{/if}
			</span>
			<div class="flex items-center gap-2">
				<button
					class="inline-flex items-center gap-1 rounded-lg px-2 py-1 disabled:opacity-40"
					onclick={() => (offset = Math.max(0, offset - PAGE_SIZE))}
					disabled={!hasPrev}
				>
					<ChevronLeft size={14} />{m.common_prev()}
				</button>
				<button
					class="inline-flex items-center gap-1 rounded-lg px-2 py-1 disabled:opacity-40"
					onclick={() => hasNext && (offset += PAGE_SIZE)}
					disabled={!hasNext}
				>
					{m.common_next()}<ChevronRight size={14} />
				</button>
			</div>
		</div>
	</section>
</div>
