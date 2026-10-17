<script lang="ts">
	/**
	 * Crawler traffic — which bots fetch this site, what they get, and whether
	 * they are who they claim to be.
	 *
	 * The companion Grafana tab on /metrics shows the same traffic as rates over
	 * time. This page exists for the two things a Prometheus label cannot hold:
	 * the exact URL fetched and the client IP.
	 */
	import { m } from '$lib/paraglide/messages';
	import {
		Bot,
		AlertTriangle,
		ShieldCheck,
		ShieldAlert,
		ChevronLeft,
		ChevronRight,
		X
	} from 'lucide-svelte';
	import {
		createCrawlerListQuery,
		createCrawlerStatsQuery,
		hoursSince,
		CRAWLER_CATEGORIES,
		VERIFICATION_STATES,
		type CrawlerFilters
	} from '$lib/features/crawlers/queries';

	const PAGE_SIZE = 50;
	/** Bots worth calling out by name when they go quiet. */
	const WATCHED = ['googlebot', 'bingbot'];
	/** Hours of silence from a major engine before it reads as a problem. */
	const STALE_HOURS = 48;

	let search = $state('');
	let botFilter = $state('');
	let categoryFilter = $state('');
	let verifiedFilter = $state('');
	let siteFilter = $state('');
	let errorsOnly = $state(false);
	let windowHours = $state(24);
	let offset = $state(0);

	let filters = $derived<CrawlerFilters>({
		q: search.trim() || undefined,
		bot: botFilter || undefined,
		category: categoryFilter || undefined,
		verified: verifiedFilter || undefined,
		site: siteFilter || undefined,
		errors_only: errorsOnly || undefined,
		since_hours: windowHours,
		limit: PAGE_SIZE,
		offset
	});

	// The $derived wrapper is required: passing options as getters to createQuery
	// silently never re-evaluates, so the request would never re-fire on a filter
	// change.
	const visits = $derived(createCrawlerListQuery(filters));
	const stats = $derived(createCrawlerStatsQuery(windowHours, siteFilter || undefined));

	const rows = $derived($visits.data ?? []);
	const summary = $derived($stats.data);

	const hasFilters = $derived(
		!!(search || botFilter || categoryFilter || verifiedFilter || siteFilter || errorsOnly)
	);
	const hasPrev = $derived(offset > 0);
	const hasNext = $derived(rows.length === PAGE_SIZE);

	/** Known bots, for the filter dropdown — from what has actually been seen. */
	const seenBots = $derived((summary?.by_bot ?? []).map((b) => b.bot));

	/** Major engines that have gone quiet, worst first. */
	const stale = $derived(
		WATCHED.map((bot) => {
			const stat = summary?.by_bot.find((b) => b.bot === bot);
			return { bot, hours: hoursSince(stat?.last_seen) };
		}).filter((s) => s.hours === null || s.hours > STALE_HOURS)
	);

	function resetPage() {
		offset = 0;
	}
	function clearFilters() {
		search = '';
		botFilter = '';
		categoryFilter = '';
		verifiedFilter = '';
		siteFilter = '';
		errorsOnly = false;
		resetPage();
	}
	function prevPage() {
		offset = Math.max(0, offset - PAGE_SIZE);
	}
	function nextPage() {
		if (hasNext) offset += PAGE_SIZE;
	}

	function fmtTime(iso: string): string {
		return new Date(iso).toLocaleString();
	}
	function fmtAgo(iso: string | null | undefined): string {
		const h = hoursSince(iso);
		if (h === null) return '—';
		if (h < 1) return m.crawlers_ago_minutes({ n: Math.max(1, Math.round(h * 60)) });
		if (h < 48) return m.crawlers_ago_hours({ n: Math.round(h) });
		return m.crawlers_ago_days({ n: Math.round(h / 24) });
	}
	function statusColor(status: number): string {
		if (status >= 500) return 'var(--color-danger)';
		if (status >= 400) return 'var(--color-warning, #b45309)';
		return 'var(--color-text-muted)';
	}
	/** Red only when the count is non-zero, so a healthy zero stays neutral. */
	function countStyle(n: number, color: string): string {
		return n > 0 ? `color: ${color};` : '';
	}
</script>

<svelte:head><title>{m.crawlers_title()}</title></svelte:head>

<div class="space-y-4">
	<div>
		<h1 class="flex items-center gap-2 text-xl font-semibold">
			<Bot size={20} />
			{m.crawlers_title()}
		</h1>
		<p class="mt-1 text-sm text-[var(--color-text-muted)]">{m.crawlers_subtitle()}</p>
	</div>

	<!-- Crawl-health banner: a major engine going quiet is the signal that
	     indexing has stalled, and it is invisible everywhere else. -->
	{#if stale.length > 0 && !$stats.isPending}
		<div
			class="flex items-start gap-2 rounded-xl border p-3 text-sm"
			style="border-color: var(--color-warning, #b45309); background: color-mix(in srgb, var(--color-warning, #b45309) 8%, transparent);"
		>
			<AlertTriangle size={16} class="mt-0.5 shrink-0" />
			<div>
				{#each stale as s (s.bot)}
					<div>{m.crawlers_stale_warning({ bot: s.bot, hours: STALE_HOURS })}</div>
				{/each}
			</div>
		</div>
	{/if}

	<!-- Summary cards -->
	<div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
		<div class="rounded-xl border p-3" style="border-color: var(--color-border);">
			<div class="text-xs text-[var(--color-text-muted)]">{m.crawlers_stat_hits()}</div>
			<div class="mt-1 text-2xl font-semibold tabular-nums">{summary?.total_hits ?? '—'}</div>
		</div>
		<div class="rounded-xl border p-3" style="border-color: var(--color-border);">
			<div class="text-xs text-[var(--color-text-muted)]">{m.crawlers_stat_errors()}</div>
			<div
				class="mt-1 text-2xl font-semibold tabular-nums"
				style={countStyle(summary?.total_errors ?? 0, 'var(--color-danger)')}
			>
				{summary?.total_errors ?? '—'}
			</div>
		</div>
		<div class="rounded-xl border p-3" style="border-color: var(--color-border);">
			<div class="flex items-center gap-1 text-xs text-[var(--color-text-muted)]">
				<ShieldAlert size={12} />
				{m.crawlers_stat_spoofed()}
			</div>
			<div
				class="mt-1 text-2xl font-semibold tabular-nums"
				style={countStyle(summary?.unverified_hits ?? 0, 'var(--color-warning, #b45309)')}
			>
				{summary?.unverified_hits ?? '—'}
			</div>
		</div>
		<div class="rounded-xl border p-3" style="border-color: var(--color-border);">
			<div class="text-xs text-[var(--color-text-muted)]">{m.crawlers_stat_bots()}</div>
			<div class="mt-1 text-2xl font-semibold tabular-nums">{summary?.by_bot.length ?? '—'}</div>
		</div>
	</div>

	<!-- Per-bot rollup, most active first -->
	{#if summary && summary.by_bot.length > 0}
		<div class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
			<div class="overflow-x-auto">
				<table class="data-table data-table-stack">
					<thead>
						<tr>
							<th>{m.crawlers_col_bot()}</th>
							<th>{m.crawlers_col_category()}</th>
							<th>{m.crawlers_col_hits()}</th>
							<th>{m.crawlers_col_errors()}</th>
							<th>{m.crawlers_col_verification()}</th>
							<th>{m.crawlers_col_last_seen()}</th>
						</tr>
					</thead>
					<tbody>
						{#each summary.by_bot as b (b.bot)}
							<tr>
								<td data-label={m.crawlers_col_bot()} class="font-medium">{b.bot}</td>
								<td data-label={m.crawlers_col_category()}>{b.category}</td>
								<td data-label={m.crawlers_col_hits()} class="tabular-nums">{b.hits}</td>
								<td
									data-label={m.crawlers_col_errors()}
									class="tabular-nums"
									style={countStyle(b.errors, 'var(--color-danger)')}>{b.errors}</td
								>
								<td data-label={m.crawlers_col_verification()}>
									{#if b.verified_hits > 0}
										<span
											class="inline-flex items-center gap-1"
											style="color: var(--color-success, #15803d);"
										>
											<ShieldCheck size={13} />{b.verified_hits}
										</span>
									{/if}
									{#if b.unverified_hits > 0}
										<span
											class="ml-2 inline-flex items-center gap-1"
											style="color: var(--color-warning, #b45309);"
										>
											<ShieldAlert size={13} />{b.unverified_hits}
										</span>
									{/if}
									{#if b.verified_hits === 0 && b.unverified_hits === 0}
										<span class="text-[var(--color-text-muted)]">—</span>
									{/if}
								</td>
								<td
									data-label={m.crawlers_col_last_seen()}
									class="whitespace-nowrap text-[var(--color-text-muted)]">{fmtAgo(b.last_seen)}</td
								>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</div>
	{/if}

	<!-- Filters -->
	<div
		class="flex flex-wrap items-center gap-2 rounded-xl border p-2"
		style="border-color: var(--color-border); background: var(--color-surface-2);"
	>
		<label class="field-input flex h-10 min-w-[200px] flex-1 items-center gap-2">
			<input
				class="noborder w-full bg-transparent outline-none"
				placeholder={m.crawlers_search_placeholder()}
				bind:value={search}
				oninput={resetPage}
			/>
		</label>

		<select class="field-input h-10" bind:value={botFilter} onchange={resetPage}>
			<option value="">{m.crawlers_filter_all_bots()}</option>
			{#each seenBots as bot (bot)}<option value={bot}>{bot}</option>{/each}
		</select>

		<select class="field-input h-10" bind:value={categoryFilter} onchange={resetPage}>
			<option value="">{m.crawlers_filter_all_categories()}</option>
			{#each CRAWLER_CATEGORIES as c (c)}<option value={c}>{c}</option>{/each}
		</select>

		<select class="field-input h-10" bind:value={verifiedFilter} onchange={resetPage}>
			<option value="">{m.crawlers_filter_all_verification()}</option>
			{#each VERIFICATION_STATES as v (v)}<option value={v}>{v}</option>{/each}
		</select>

		<select class="field-input h-10" bind:value={siteFilter} onchange={resetPage}>
			<option value="">{m.crawlers_filter_all_sites()}</option>
			<option value="pacod">pacod</option>
		</select>

		<select class="field-input h-10" bind:value={windowHours} onchange={resetPage}>
			<option value={24}>{m.crawlers_window_24h()}</option>
			<option value={168}>{m.crawlers_window_7d()}</option>
			<option value={720}>{m.crawlers_window_30d()}</option>
		</select>

		<label class="flex items-center gap-1.5 px-1 text-sm">
			<input type="checkbox" bind:checked={errorsOnly} onchange={resetPage} />
			{m.crawlers_filter_errors_only()}
		</label>

		{#if hasFilters}
			<button
				type="button"
				class="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
				onclick={clearFilters}
			>
				<X size={14} />
				{m.common_clear()}
			</button>
		{/if}
	</div>

	<!-- Visit log -->
	<div class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
		<div class="overflow-x-auto">
			<table class="data-table data-table-stack">
				<thead>
					<tr>
						<th>{m.crawlers_col_time()}</th>
						<th>{m.crawlers_col_bot()}</th>
						<th>{m.crawlers_col_site()}</th>
						<th>{m.crawlers_col_path()}</th>
						<th>{m.crawlers_col_status()}</th>
						<th>{m.crawlers_col_ip()}</th>
						<th>{m.crawlers_col_verification()}</th>
					</tr>
				</thead>
				<tbody>
					{#if $visits.isPending}
						<tr
							><td colspan="7" class="py-12 text-center text-sm text-[var(--color-text-muted)]"
								>{m.common_loading()}</td
							></tr
						>
					{:else if $visits.isError}
						<tr
							><td colspan="7" class="py-12 text-center text-sm" style="color: var(--color-danger);"
								>{m.crawlers_load_failed()}</td
							></tr
						>
					{:else if rows.length > 0}
						{#each rows as v (v.id)}
							<tr>
								<td
									data-label={m.crawlers_col_time()}
									class="whitespace-nowrap text-[var(--color-text-muted)] tabular-nums"
									>{fmtTime(v.occurred_at)}</td
								>
								<td
									data-label={m.crawlers_col_bot()}
									class="font-medium whitespace-nowrap"
									title={v.user_agent ?? ''}>{v.bot}</td
								>
								<td data-label={m.crawlers_col_site()}>{v.site}</td>
								<td
									data-label={m.crawlers_col_path()}
									class="stack-block max-w-[280px] truncate font-mono text-xs"
									title={v.path}>{v.path}</td
								>
								<td
									data-label={m.crawlers_col_status()}
									class="tabular-nums"
									style="color: {statusColor(v.status)}">{v.status}</td
								>
								<td
									data-label={m.crawlers_col_ip()}
									class="font-mono text-xs text-[var(--color-text-muted)]">{v.ip ?? '—'}</td
								>
								<td data-label={m.crawlers_col_verification()}>
									{#if v.verified === 'verified'}
										<span
											class="inline-flex items-center gap-1"
											style="color: var(--color-success, #15803d);"
											title={m.crawlers_verified_hint()}
										>
											<ShieldCheck size={13} />{m.crawlers_verified()}
										</span>
									{:else if v.verified === 'unverified'}
										<span
											class="inline-flex items-center gap-1"
											style="color: var(--color-warning, #b45309);"
											title={m.crawlers_unverified_hint()}
										>
											<ShieldAlert size={13} />{m.crawlers_unverified()}
										</span>
									{:else}
										<span class="text-[var(--color-text-muted)]" title={m.crawlers_unknown_hint()}
											>{m.crawlers_unknown()}</span
										>
									{/if}
								</td>
							</tr>
						{/each}
					{:else}
						<tr
							><td colspan="7" class="py-12 text-center text-sm text-[var(--color-text-muted)]"
								>{hasFilters ? m.crawlers_none_match() : m.crawlers_empty_window()}</td
							></tr
						>
					{/if}
				</tbody>
			</table>
		</div>
	</div>

	<div class="flex items-center justify-between text-sm text-[var(--color-text-muted)]">
		<span>
			{#if !$visits.isPending}
				{m.common_showing_range({
					from: rows.length === 0 ? 0 : offset + 1,
					to: offset + rows.length
				})}
			{/if}
		</span>
		<div class="flex items-center gap-2">
			<button
				class="inline-flex items-center gap-1 rounded-lg px-2 py-1 disabled:opacity-40"
				onclick={prevPage}
				disabled={!hasPrev}
			>
				<ChevronLeft size={14} />{m.common_prev()}
			</button>
			<button
				class="inline-flex items-center gap-1 rounded-lg px-2 py-1 disabled:opacity-40"
				onclick={nextPage}
				disabled={!hasNext}
			>
				{m.common_next()}<ChevronRight size={14} />
			</button>
		</div>
	</div>
</div>
